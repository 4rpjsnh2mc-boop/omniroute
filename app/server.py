#!/usr/bin/env python3
"""Тендерный калькулятор: сервер.

Только стандартная библиотека Python 3.10+. Данные: SQLite + файлы документов в TENDER_DATA.

Запуск:            python3 server.py serve
Создать админа:    python3 server.py create-admin <логин>   (пароль спросит)
"""

import getpass
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tparse as tparser

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
DATA = os.environ.get("TENDER_DATA", os.path.join(BASE, "..", "data"))
HOST = os.environ.get("TENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TENDER_PORT", "8090"))
DB_PATH = os.path.join(DATA, "tender.db")
FILES = os.path.join(DATA, "files")
SESSION_DAYS = 30
COOKIE = "tsid"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MAX_PAGE = 15 * 1024 * 1024
MAX_DOC = 40 * 1024 * 1024
MAX_UPLOAD = 60 * 1024 * 1024
MAX_DOCS_PER_TENDER = 40

DEFAULT_SETTINGS = {
    "company": "",
    "tax": {"regime": "osno", "usnVat": 0, "usnRate": 6, "profitRate": 25},
    "supplierVat": True,
    "overheadPct": 5,
    "targetPct": 10,
    "moneyRatePct": 18,
    "bgRatePct": 3,
}

# ---------------------------------------------------------------- база

_db_lock = threading.RLock()
_db = None


def db():
    global _db
    if _db is None:
        os.makedirs(DATA, exist_ok=True)
        os.makedirs(FILES, exist_ok=True)
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA foreign_keys=ON")
        _db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '',
              pass_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
              created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tenders (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL DEFAULT '', number TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
              customer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
              nmck REAL, price REAL, profit REAL, margin REAL, deadline TEXT NOT NULL DEFAULT '',
              data TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL, updated INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS tenders_user ON tenders(user_id, updated);
            CREATE TABLE IF NOT EXISTS docs (
              id INTEGER PRIMARY KEY, tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
              name TEXT NOT NULL, path TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0,
              src_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok', error TEXT NOT NULL DEFAULT '',
              created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS prices (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', cost REAL NOT NULL DEFAULT 0,
              note TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL, updated_by TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        _db.commit()
    return _db


def q(sql, args=(), one=False):
    with _db_lock:
        cur = db().execute(sql, args)
        rows = cur.fetchall()
        db().commit()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]


def ex(sql, args=()):
    with _db_lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur.lastrowid


def now():
    return int(time.time())


def get_settings():
    row = q("SELECT value FROM settings WHERE key='company'", one=True)
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row["value"])
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(s.get(k), dict):
                    s[k].update(v)
                else:
                    s[k] = v
        except ValueError:
            pass
    return s


# ---------------------------------------------------------------- пароли и сессии

def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + dk.hex()


def check_password(pw, stored):
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


def new_session(user_id):
    token = secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode()).hexdigest()
    ex("INSERT INTO sessions(token_hash, user_id, expires) VALUES (?,?,?)", (th, user_id, now() + SESSION_DAYS * 86400))
    ex("DELETE FROM sessions WHERE expires < ?", (now(),))
    return token


_fails = {}
_fails_lock = threading.Lock()


def login_blocked(ip):
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if t > time.time() - 600]
        _fails[ip] = recent
        return len(recent) >= 8


def login_failed(ip):
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------- загрузка страниц (с защитой от обращений во внутреннюю сеть)

class FetchError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Ссылка должна начинаться с http:// или https://")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in (80, 443):
        raise FetchError("Нестандартный порт в ссылке не поддерживается")
    try:
        infos = socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError("Сайт не найден. Проверьте ссылку")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise FetchError("Эта ссылка ведёт во внутреннюю сеть")


def fetch(url, limit=MAX_PAGE, timeout=30):
    """Возвращает (итоговый url, заголовки, байты)."""
    for _ in range(6):
        _check_url(url)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        })
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers["Location"])
                continue
            if e.code in (401, 403):
                raise FetchError("Площадка не пустила: нужен вход или доступ закрыт")
            if e.code == 404:
                raise FetchError("Страница не найдена (404)")
            raise FetchError(f"Площадка ответила ошибкой {e.code}")
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            raise FetchError("Площадка не отвечает. Попробуйте позже")
        with resp:
            data = resp.read(limit + 1)
            if len(data) > limit:
                raise FetchError("Файл слишком большой")
            return resp.geturl() or url, resp.headers, data
    raise FetchError("Слишком много перенаправлений")


def decode_html(headers, data):
    ctype = headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4000], re.I)
    enc = m.group(1) if m else "utf-8"
    if isinstance(enc, bytes):
        enc = enc.decode("ascii", "ignore")
    for e in (enc, "utf-8", "cp1251"):
        try:
            return data.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def safe_filename(name, fallback="document"):
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", " ", name or "").strip(" .") or fallback
    return name[:150]


def filename_from_headers(headers):
    cd = headers.get("Content-Disposition", "")
    m = re.search(r"filename\*\s*=\s*([\w-]+)''([^;]+)", cd, re.I)
    if m:
        try:
            return urllib.parse.unquote(m.group(2), encoding=m.group(1))
        except LookupError:
            pass
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        raw = m.group(1).strip()
        try:  # многие площадки отдают имя в UTF-8 или cp1251 без кодирования
            b = raw.encode("latin-1")
            for enc in ("utf-8", "cp1251"):
                try:
                    return b.decode(enc)
                except UnicodeDecodeError:
                    continue
        except UnicodeEncodeError:
            return raw
    return ""


# ---------------------------------------------------------------- документы (фоновая загрузка)

_jobs = {}
_jobs_lock = threading.Lock()


def doc_dir(tender_id):
    d = os.path.join(FILES, str(int(tender_id)))
    os.makedirs(d, exist_ok=True)
    return d


def store_doc(tender_id, name, data, src_url=""):
    ext = os.path.splitext(name)[1].lower()[:10]
    fname = secrets.token_hex(8) + re.sub(r"[^.a-z0-9]", "", ext)
    path = os.path.join(doc_dir(tender_id), fname)
    with open(path, "wb") as f:
        f.write(data)
    return ex("INSERT INTO docs(tender_id, name, path, size, src_url, status, created) VALUES (?,?,?,?,?,?,?)",
              (tender_id, safe_filename(name), fname, len(data), src_url, "ok", now()))


def download_docs(tender_id, docs):
    with _jobs_lock:
        _jobs[tender_id] = {"total": len(docs), "done": 0, "running": True}
    try:
        have = {r["src_url"] for r in q("SELECT src_url FROM docs WHERE tender_id=?", (tender_id,))}
        for d in docs:
            url = d["url"]
            if url in have:
                pass
            elif q("SELECT COUNT(*) AS n FROM docs WHERE tender_id=?", (tender_id,), one=True)["n"] >= MAX_DOCS_PER_TENDER:
                break
            else:
                try:
                    final, headers, data = fetch(url, limit=MAX_DOC, timeout=60)
                    name = filename_from_headers(headers) or d.get("name") or "document"
                    if not os.path.splitext(name)[1]:
                        guess = mimetypes.guess_extension((headers.get("Content-Type") or "").split(";")[0].strip()) or ""
                        name += guess
                    ctype = (headers.get("Content-Type") or "").lower()
                    if "text/html" in ctype and not name.lower().endswith((".html", ".htm")):
                        raise FetchError("Вместо файла пришла страница (нужен вход на площадку)")
                    store_doc(tender_id, name, data, src_url=url)
                except FetchError as e:
                    ex("INSERT INTO docs(tender_id, name, src_url, status, error, created) VALUES (?,?,?,?,?,?)",
                       (tender_id, safe_filename(d.get("name") or url), url, "error", str(e), now()))
            with _jobs_lock:
                _jobs[tender_id]["done"] += 1
    finally:
        with _jobs_lock:
            _jobs[tender_id]["running"] = False


# ---------------------------------------------------------------- HTTP

def client_ip(h):
    fwd = h.headers.get("X-Real-IP") or (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    peer = h.client_address[0]
    return fwd if fwd and peer in ("127.0.0.1", "::1") else peer


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


ROUTES = []


def route(method, pattern, auth=True, admin=False):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth, admin))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "Tender/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    # -- ответы
    def _headers(self, status, ctype, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def send_json(self, obj, status=200, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self._headers(status, "application/json; charset=utf-8", len(body), dict({"Cache-Control": "no-store"}, **(extra or {})))
        self.wfile.write(body)

    def send_file(self, path, ctype, download_name=None, cache="no-cache"):
        size = os.path.getsize(path)
        extra = {"Cache-Control": cache}
        if download_name:
            quoted = urllib.parse.quote(download_name)
            extra["Content-Disposition"] = f"attachment; filename=\"document{os.path.splitext(download_name)[1][:10]}\"; filename*=UTF-8''{quoted}"
        self._headers(200, ctype, size, extra)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # -- запрос
    def body_bytes(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, "Слишком большой запрос")
        return self.rfile.read(n) if n else b""

    def json_body(self):
        raw = self.body_bytes(4 * 1024 * 1024)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError(400, "Неверный формат запроса")
        if not isinstance(data, dict):
            raise ApiError(400, "Неверный формат запроса")
        return data

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def current_user(self):
        token = self.cookie_token()
        if not token:
            return None
        th = hashlib.sha256(token.encode()).hexdigest()
        return q("""SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash=? AND s.expires>? AND u.active=1""", (th, now()), one=True)

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def session_cookie(self, token, max_age):
        secure = "; Secure" if self.is_https() else ""
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"

    def dispatch(self, method):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(404, "Не найдено")
                return self.serve_static(path)
            if method != "GET" and self.headers.get("X-Tender") != "1":
                raise ApiError(403, "Запрос отклонён")
            for m, rx, fn, auth, admin in ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if not mt:
                    continue
                user = self.current_user() if auth or path == "/api/me" else None
                if auth and not user:
                    raise ApiError(401, "Войдите в систему")
                if admin and not user["is_admin"]:
                    raise ApiError(403, "Только для администратора")
                return fn(self, user, *mt.groups())
            raise ApiError(404, "Не найдено")
        except ApiError as e:
            self.close_connection = True
            self.send_json({"error": e.message}, e.status, extra={"Connection": "close"})
        except Exception:  # не показываем внутренности ошибки пользователю
            import traceback
            traceback.print_exc()
            try:
                self.send_json({"error": "Внутренняя ошибка сервера"}, 500)
            except Exception:
                pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_file(full, ctype, cache="no-cache")


# ---------------------------------------------------------------- API: вход

def public_user(u):
    return {"id": u["id"], "login": u["login"], "name": u["name"], "isAdmin": bool(u["is_admin"])}


@route("POST", r"/api/login", auth=False)
def api_login(h, _user):
    ip = client_ip(h)
    if login_blocked(ip):
        raise ApiError(429, "Слишком много попыток. Подождите 10 минут")
    b = h.json_body()
    u = q("SELECT * FROM users WHERE login=? AND active=1", (str(b.get("login", "")).strip().lower(),), one=True)
    if not u or not check_password(str(b.get("password", "")), u["pass_hash"]):
        login_failed(ip)
        raise ApiError(401, "Неверный логин или пароль")
    token = new_session(u["id"])
    h.send_json({"user": public_user(u)}, extra={"Set-Cookie": h.session_cookie(token, SESSION_DAYS * 86400)})


@route("POST", r"/api/logout", auth=False)
def api_logout(h, _user):
    token = h.cookie_token()
    if token:
        ex("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    h.send_json({"ok": True}, extra={"Set-Cookie": h.session_cookie("", 0)})


@route("GET", r"/api/me", auth=False)
def api_me(h, user):
    if not user:
        return h.send_json({"user": None, "settings": None})
    h.send_json({"user": public_user(user), "settings": get_settings()})


@route("POST", r"/api/password")
def api_password(h, user):
    b = h.json_body()
    if not check_password(str(b.get("old", "")), user["pass_hash"]):
        raise ApiError(400, "Текущий пароль указан неверно")
    new = str(b.get("new", ""))
    if len(new) < 8:
        raise ApiError(400, "Новый пароль: не меньше 8 символов")
    ex("UPDATE users SET pass_hash=? WHERE id=?", (hash_password(new), user["id"]))
    h.send_json({"ok": True})


# ---------------------------------------------------------------- API: тендеры

SUMMARY = ("title", "number", "url", "customer", "status", "nmck", "price", "profit", "margin", "deadline")
STATUSES = {"draft", "submitted", "won", "lost", "skipped"}


def own_tender(user, tid):
    t = q("SELECT * FROM tenders WHERE id=? AND user_id=?", (int(tid), user["id"]), one=True)
    if not t:
        raise ApiError(404, "Тендер не найден")
    return t


def num_or_none(v):
    try:
        f = float(v)
        return f if f == f and abs(f) < 1e15 else None
    except (TypeError, ValueError):
        return None


@route("GET", r"/api/tenders")
def api_tenders(h, user):
    rows = q(f"SELECT id, {', '.join(SUMMARY)}, updated FROM tenders WHERE user_id=? ORDER BY updated DESC", (user["id"],))
    h.send_json({"tenders": rows})


@route("POST", r"/api/tenders")
def api_tender_create(h, user):
    b = h.json_body()
    data = b.get("data") if isinstance(b.get("data"), dict) else {}
    tid = ex("INSERT INTO tenders(user_id, title, data, created, updated) VALUES (?,?,?,?,?)",
             (user["id"], str(b.get("title") or "Новый тендер")[:300], json.dumps(data, ensure_ascii=False), now(), now()))
    h.send_json({"id": tid})


@route("GET", r"/api/tenders/(\d+)")
def api_tender_get(h, user, tid):
    t = own_tender(user, tid)
    t["data"] = json.loads(t["data"] or "{}")
    h.send_json({"tender": t})


@route("PUT", r"/api/tenders/(\d+)")
def api_tender_put(h, user, tid):
    own_tender(user, tid)
    b = h.json_body()
    data = b.get("data")
    if not isinstance(data, dict):
        raise ApiError(400, "Нет данных тендера")
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw) > 3 * 1024 * 1024:
        raise ApiError(413, "Слишком большой тендер")
    s = b.get("summary") if isinstance(b.get("summary"), dict) else {}
    status = s.get("status") if s.get("status") in STATUSES else "draft"
    ex(f"""UPDATE tenders SET data=?, title=?, number=?, url=?, customer=?, status=?, nmck=?, price=?, profit=?, margin=?,
           deadline=?, updated=? WHERE id=? AND user_id=?""",
       (raw, str(s.get("title") or "")[:300], str(s.get("number") or "")[:100], str(s.get("url") or "")[:1000],
        str(s.get("customer") or "")[:300], status, num_or_none(s.get("nmck")), num_or_none(s.get("price")),
        num_or_none(s.get("profit")), num_or_none(s.get("margin")), str(s.get("deadline") or "")[:80], now(),
        int(tid), user["id"]))
    h.send_json({"ok": True, "updated": now()})


@route("DELETE", r"/api/tenders/(\d+)")
def api_tender_delete(h, user, tid):
    own_tender(user, tid)
    for d in q("SELECT path FROM docs WHERE tender_id=?", (int(tid),)):
        if d["path"]:
            try:
                os.remove(os.path.join(FILES, str(int(tid)), d["path"]))
            except OSError:
                pass
    ex("DELETE FROM tenders WHERE id=? AND user_id=?", (int(tid), user["id"]))
    h.send_json({"ok": True})


@route("POST", r"/api/tenders/(\d+)/import")
def api_tender_import(h, user, tid):
    own_tender(user, tid)
    url = str(h.json_body().get("url") or "").strip()
    try:
        final, headers, data = fetch(url)
        pages = [(final, decode_html(headers, data))]
        for extra in tparser.extra_pages(final):
            try:
                f2, h2, d2 = fetch(extra)
                pages.append((f2, decode_html(h2, d2)))
            except FetchError:
                pass
    except FetchError as e:
        raise ApiError(400, str(e))
    info = tparser.extract(pages, final)
    if info["textLength"] < 200:
        raise ApiError(400, "Страница почти пустая: площадка показывает закупку только после входа")
    if info["docs"]:
        threading.Thread(target=download_docs, args=(int(tid), info["docs"]), daemon=True).start()
    info["url"] = url
    h.send_json({"info": info})


# ---------------------------------------------------------------- API: документы

@route("GET", r"/api/tenders/(\d+)/docs")
def api_docs(h, user, tid):
    own_tender(user, tid)
    rows = q("SELECT id, name, size, src_url, status, error, created FROM docs WHERE tender_id=? ORDER BY id", (int(tid),))
    with _jobs_lock:
        job = dict(_jobs.get(int(tid), {})) or None
    h.send_json({"docs": rows, "job": job})


@route("POST", r"/api/tenders/(\d+)/docs")
def api_doc_upload(h, user, tid):
    own_tender(user, tid)
    name = urllib.parse.unquote(h.headers.get("X-File-Name") or "document")
    data = h.body_bytes(MAX_UPLOAD)
    if not data:
        raise ApiError(400, "Пустой файл")
    if q("SELECT COUNT(*) AS n FROM docs WHERE tender_id=?", (int(tid),), one=True)["n"] >= MAX_DOCS_PER_TENDER:
        raise ApiError(400, "В тендере уже слишком много документов")
    did = store_doc(int(tid), name, data)
    h.send_json({"id": did})


@route("GET", r"/api/tenders/(\d+)/docs/(\d+)")
def api_doc_get(h, user, tid, did):
    own_tender(user, tid)
    d = q("SELECT * FROM docs WHERE id=? AND tender_id=? AND status='ok'", (int(did), int(tid)), one=True)
    if not d:
        raise ApiError(404, "Документ не найден")
    path = os.path.join(FILES, str(int(tid)), d["path"])
    if not os.path.isfile(path):
        raise ApiError(404, "Файл не найден на сервере")
    ctype = mimetypes.guess_type(d["name"])[0] or "application/octet-stream"
    if ctype in ("text/html", "image/svg+xml", "application/javascript", "text/javascript", "application/xhtml+xml"):
        ctype = "application/octet-stream"
    h.send_file(path, ctype, download_name=d["name"])


@route("DELETE", r"/api/tenders/(\d+)/docs/(\d+)")
def api_doc_delete(h, user, tid, did):
    own_tender(user, tid)
    d = q("SELECT * FROM docs WHERE id=? AND tender_id=?", (int(did), int(tid)), one=True)
    if d:
        if d["path"]:
            try:
                os.remove(os.path.join(FILES, str(int(tid)), d["path"]))
            except OSError:
                pass
        ex("DELETE FROM docs WHERE id=?", (int(did),))
    h.send_json({"ok": True})


# ---------------------------------------------------------------- API: прайс (общий для компании)

@route("GET", r"/api/prices")
def api_prices(h, user):
    h.send_json({"prices": q("SELECT * FROM prices ORDER BY name COLLATE NOCASE")})


def price_fields(b):
    name = str(b.get("name") or "").strip()[:240]
    if not name:
        raise ApiError(400, "Укажите название")
    return name, str(b.get("unit") or "")[:30], num_or_none(b.get("cost")) or 0.0, str(b.get("note") or "")[:300]


@route("POST", r"/api/prices")
def api_price_add(h, user):
    b = h.json_body()
    rows = b.get("rows") if isinstance(b.get("rows"), list) else [b]
    added = 0
    for r in rows[:2000]:
        if not isinstance(r, dict):
            continue
        try:
            name, unit, cost, note = price_fields(r)
        except ApiError:
            continue
        exist = q("SELECT id FROM prices WHERE lower(name)=lower(?)", (name,), one=True)
        if exist:
            ex("UPDATE prices SET unit=?, cost=?, note=?, updated=?, updated_by=? WHERE id=?",
               (unit, cost, note, now(), user["login"], exist["id"]))
        else:
            ex("INSERT INTO prices(name, unit, cost, note, updated, updated_by) VALUES (?,?,?,?,?,?)",
               (name, unit, cost, note, now(), user["login"]))
        added += 1
    h.send_json({"ok": True, "count": added})


@route("PUT", r"/api/prices/(\d+)")
def api_price_put(h, user, pid):
    name, unit, cost, note = price_fields(h.json_body())
    ex("UPDATE prices SET name=?, unit=?, cost=?, note=?, updated=?, updated_by=? WHERE id=?",
       (name, unit, cost, note, now(), user["login"], int(pid)))
    h.send_json({"ok": True})


@route("DELETE", r"/api/prices/(\d+)")
def api_price_delete(h, user, pid):
    ex("DELETE FROM prices WHERE id=?", (int(pid),))
    h.send_json({"ok": True})


# ---------------------------------------------------------------- API: пользователи и настройки (админ)

@route("GET", r"/api/users", admin=True)
def api_users(h, user):
    rows = q("""SELECT u.id, u.login, u.name, u.is_admin, u.active, u.created,
                (SELECT COUNT(*) FROM tenders t WHERE t.user_id=u.id) AS tenders FROM users u ORDER BY u.id""")
    h.send_json({"users": rows})


def valid_login(s):
    s = str(s or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._@-]{3,40}", s):
        raise ApiError(400, "Логин: 3–40 латинских букв, цифр или . _ - @")
    return s


@route("POST", r"/api/users", admin=True)
def api_user_add(h, user):
    b = h.json_body()
    login = valid_login(b.get("login"))
    pw = str(b.get("password") or "")
    if len(pw) < 8:
        raise ApiError(400, "Пароль: не меньше 8 символов")
    if q("SELECT id FROM users WHERE login=?", (login,), one=True):
        raise ApiError(400, "Такой логин уже есть")
    uid = ex("INSERT INTO users(login, name, pass_hash, is_admin, active, created) VALUES (?,?,?,?,1,?)",
             (login, str(b.get("name") or "")[:100], hash_password(pw), 1 if b.get("isAdmin") else 0, now()))
    h.send_json({"id": uid})


@route("PUT", r"/api/users/(\d+)", admin=True)
def api_user_put(h, user, uid):
    uid = int(uid)
    b = h.json_body()
    target = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not target:
        raise ApiError(404, "Пользователь не найден")
    if "name" in b:
        ex("UPDATE users SET name=? WHERE id=?", (str(b["name"])[:100], uid))
    if "password" in b:
        pw = str(b["password"])
        if len(pw) < 8:
            raise ApiError(400, "Пароль: не меньше 8 символов")
        ex("UPDATE users SET pass_hash=? WHERE id=?", (hash_password(pw), uid))
        ex("DELETE FROM sessions WHERE user_id=?", (uid,))
    if "active" in b or "isAdmin" in b:
        if uid == user["id"]:
            raise ApiError(400, "Нельзя отключить себя или снять с себя права администратора")
        if "active" in b:
            ex("UPDATE users SET active=? WHERE id=?", (1 if b["active"] else 0, uid))
            if not b["active"]:
                ex("DELETE FROM sessions WHERE user_id=?", (uid,))
        if "isAdmin" in b:
            ex("UPDATE users SET is_admin=? WHERE id=?", (1 if b["isAdmin"] else 0, uid))
    h.send_json({"ok": True})


@route("PUT", r"/api/settings", admin=True)
def api_settings_put(h, user):
    b = h.json_body()
    s = get_settings()
    tax = b.get("tax") if isinstance(b.get("tax"), dict) else {}
    if tax.get("regime") in ("osno", "usn6", "usn15"):
        s["tax"]["regime"] = tax["regime"]
    for k in ("usnVat", "usnRate", "profitRate"):
        v = num_or_none(tax.get(k))
        if v is not None and 0 <= v <= 50:
            s["tax"][k] = v
    for k in ("overheadPct", "targetPct", "moneyRatePct", "bgRatePct"):
        v = num_or_none(b.get(k))
        if v is not None and 0 <= v <= 100:
            s[k] = v
    if "supplierVat" in b:
        s["supplierVat"] = bool(b["supplierVat"])
    if "company" in b:
        s["company"] = str(b["company"])[:200]
    ex("INSERT INTO settings(key, value) VALUES ('company', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (json.dumps(s, ensure_ascii=False),))
    h.send_json({"settings": s})


# ---------------------------------------------------------------- запуск

def create_admin(login):
    login = valid_login(login)
    pw = os.environ.get("TENDER_ADMIN_PASSWORD") or getpass.getpass("Пароль администратора (не меньше 8 символов): ")
    if len(pw) < 8:
        print("Пароль слишком короткий", file=sys.stderr)
        sys.exit(1)
    exist = q("SELECT id FROM users WHERE login=?", (login,), one=True)
    if exist:
        ex("UPDATE users SET pass_hash=?, is_admin=1, active=1 WHERE id=?", (hash_password(pw), exist["id"]))
        print(f"Пароль пользователя {login} обновлён, права администратора выданы")
    else:
        ex("INSERT INTO users(login, name, pass_hash, is_admin, active, created) VALUES (?,?,?,1,1,?)",
           (login, "Администратор", hash_password(pw), now()))
        print(f"Администратор {login} создан")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "create-admin":
        if len(sys.argv) < 3:
            print("Использование: server.py create-admin <логин>", file=sys.stderr)
            sys.exit(2)
        create_admin(sys.argv[2])
        return
    if cmd == "count-users":
        print(q("SELECT COUNT(*) AS n FROM users", one=True)["n"])
        return
    db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"Тендерный калькулятор: http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
