#!/usr/bin/env python3
"""Тендерный калькулятор: сервер.

Только стандартная библиотека Python 3.10+. Данные: SQLite + файлы документов в TENDER_DATA.

Запуск:                 python3 server.py serve
Список рабочих мест:    python3 server.py users
Новая личная ссылка:    python3 server.py relink <id>

Доступ: человек открывает ссылку-приглашение (в ней код TENDER_INVITE), вводит имя
и получает своё рабочее место: свои тендеры, свой прайс, свои настройки. Ключ рабочего
места хранится в cookie браузера; личная ссылка с этим ключом открывает то же место
на другом устройстве.
"""

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
DB_PATH = os.path.join(DATA, "app.db")
INVITE = os.environ.get("TENDER_INVITE", "").strip()
FILES = os.path.join(DATA, "files")
COOKIE_DAYS = 400
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
              id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', token_hash TEXT UNIQUE NOT NULL,
              settings TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL, seen INTEGER NOT NULL);
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
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', cost REAL NOT NULL DEFAULT 0,
              note TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS prices_user ON prices(user_id, name COLLATE NOCASE);
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


def get_settings(user):
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        saved = json.loads(user.get("settings") or "{}")
    except ValueError:
        saved = {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k].update(v)
        else:
            s[k] = v
    return s


# ---------------------------------------------------------------- ключи рабочих мест

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(24)


def user_by_token(token):
    if not token or len(token) > 100:
        return None
    return q("SELECT * FROM users WHERE token_hash=?", (token_hash(token),), one=True)


_fails = {}
_fails_lock = threading.Lock()


def too_many(ip, bucket, limit, window=3600):
    with _fails_lock:
        key = (bucket, ip)
        recent = [t for t in _fails.get(key, []) if t > time.time() - window]
        _fails[key] = recent
        return len(recent) >= limit


def hit(ip, bucket):
    with _fails_lock:
        _fails.setdefault((bucket, ip), []).append(time.time())


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


def route(method, pattern, auth=True):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "Tender/1.0"
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # клиент закрыл соединение

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
        u = user_by_token(self.cookie_token())
        if u and now() - u["seen"] > 3600:
            ex("UPDATE users SET seen=? WHERE id=?", (now(), u["id"]))
        return u

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
            for m, rx, fn, auth in ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if not mt:
                    continue
                user = self.current_user()
                if auth and not user:
                    raise ApiError(401, "Откройте ссылку-приглашение или свою личную ссылку")
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
    return {"id": u["id"], "name": u["name"]}


def set_cookie(h, token):
    return {"Set-Cookie": h.session_cookie(token, COOKIE_DAYS * 86400)}


@route("POST", r"/api/join", auth=False)
def api_join(h, _user):
    ip = client_ip(h)
    if too_many(ip, "bad", 20):
        raise ApiError(429, "Слишком много попыток. Подождите час")
    b = h.json_body()
    code = str(b.get("code") or "")
    if INVITE and not hmac.compare_digest(code.encode(), INVITE.encode()):
        hit(ip, "bad")
        raise ApiError(403, "Ссылка-приглашение устарела. Попросите новую")
    if too_many(ip, "join", 30, 86400):
        raise ApiError(429, "С этого адреса создано слишком много рабочих мест. Попробуйте завтра")
    hit(ip, "join")
    name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:80]
    token = new_token()
    uid = ex("INSERT INTO users(name, token_hash, settings, created, seen) VALUES (?,?,?,?,?)",
             (name, token_hash(token), "{}", now(), now()))
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True)
    h.send_json({"user": public_user(u), "key": token}, extra=set_cookie(h, token))


@route("POST", r"/api/claim", auth=False)
def api_claim(h, _user):
    ip = client_ip(h)
    if too_many(ip, "bad", 20):
        raise ApiError(429, "Слишком много попыток. Подождите час")
    token = str(h.json_body().get("key") or "")
    u = user_by_token(token)
    if not u:
        hit(ip, "bad")
        raise ApiError(404, "Личная ссылка не подошла: её сменили или скопировали не полностью")
    h.send_json({"user": public_user(u)}, extra=set_cookie(h, token))


@route("POST", r"/api/logout", auth=False)
def api_logout(h, _user):
    h.send_json({"ok": True}, extra={"Set-Cookie": h.session_cookie("", 0)})


@route("GET", r"/api/me", auth=False)
def api_me(h, user):
    if not user:
        return h.send_json({"user": None, "invite": bool(INVITE)})
    h.send_json({"user": public_user(user), "settings": get_settings(user), "key": h.cookie_token()})


@route("POST", r"/api/relink")
def api_relink(h, user):
    token = new_token()
    ex("UPDATE users SET token_hash=? WHERE id=?", (token_hash(token), user["id"]))
    h.send_json({"key": token}, extra=set_cookie(h, token))


@route("PUT", r"/api/profile")
def api_profile(h, user):
    b = h.json_body()
    if "name" in b:
        ex("UPDATE users SET name=? WHERE id=?", (re.sub(r"\s+", " ", str(b["name"])).strip()[:80], user["id"]))
    s = get_settings(user)
    st = b.get("settings") if isinstance(b.get("settings"), dict) else None
    if st:
        tax = st.get("tax") if isinstance(st.get("tax"), dict) else {}
        if tax.get("regime") in ("osno", "usn6", "usn15"):
            s["tax"]["regime"] = tax["regime"]
        for k in ("usnVat", "usnRate", "profitRate"):
            v = num_or_none(tax.get(k))
            if v is not None and 0 <= v <= 50:
                s["tax"][k] = v
        for k in ("overheadPct", "targetPct", "moneyRatePct", "bgRatePct"):
            v = num_or_none(st.get(k))
            if v is not None and 0 <= v <= 100:
                s[k] = v
        if "supplierVat" in st:
            s["supplierVat"] = bool(st["supplierVat"])
        ex("UPDATE users SET settings=? WHERE id=?", (json.dumps(s, ensure_ascii=False), user["id"]))
    u = q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    h.send_json({"user": public_user(u), "settings": get_settings(u)})


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
    h.send_json({"prices": q("SELECT id, name, unit, cost, note, updated FROM prices WHERE user_id=? ORDER BY name COLLATE NOCASE",
                             (user["id"],))})


def price_fields(b):
    name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:240]
    if not name:
        raise ApiError(400, "Укажите название")
    return name, str(b.get("unit") or "")[:30], num_or_none(b.get("cost")) or 0.0, str(b.get("note") or "")[:300]


@route("POST", r"/api/prices")
def api_price_add(h, user):
    b = h.json_body()
    rows = b.get("rows") if isinstance(b.get("rows"), list) else [b]
    if q("SELECT COUNT(*) AS n FROM prices WHERE user_id=?", (user["id"],), one=True)["n"] + len(rows) > 20000:
        raise ApiError(400, "В прайсе слишком много строк")
    added = 0
    for r in rows[:5000]:
        if not isinstance(r, dict):
            continue
        try:
            name, unit, cost, note = price_fields(r)
        except ApiError:
            continue
        exist = q("SELECT id FROM prices WHERE user_id=? AND name=? COLLATE NOCASE", (user["id"], name), one=True)
        if exist:
            ex("UPDATE prices SET unit=?, cost=?, note=?, updated=? WHERE id=?", (unit, cost, note, now(), exist["id"]))
        else:
            ex("INSERT INTO prices(user_id, name, unit, cost, note, updated) VALUES (?,?,?,?,?,?)",
               (user["id"], name, unit, cost, note, now()))
        added += 1
    h.send_json({"ok": True, "count": added})


@route("PUT", r"/api/prices/(\d+)")
def api_price_put(h, user, pid):
    name, unit, cost, note = price_fields(h.json_body())
    ex("UPDATE prices SET name=?, unit=?, cost=?, note=?, updated=? WHERE id=? AND user_id=?",
       (name, unit, cost, note, now(), int(pid), user["id"]))
    h.send_json({"ok": True})


@route("DELETE", r"/api/prices/(\d+)")
def api_price_delete(h, user, pid):
    ex("DELETE FROM prices WHERE id=? AND user_id=?", (int(pid), user["id"]))
    h.send_json({"ok": True})


# ---------------------------------------------------------------- запуск

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "users":
        rows = q("""SELECT u.id, u.name, u.created, u.seen, (SELECT COUNT(*) FROM tenders t WHERE t.user_id=u.id) AS n
                    FROM users u ORDER BY u.id""")
        if not rows:
            print("Рабочих мест пока нет")
        for r in rows:
            print(f"{r['id']:>4}  {r['name'] or 'без имени':<30} тендеров: {r['n']:<4} был: {time.strftime('%d.%m.%Y', time.localtime(r['seen']))}")
        return
    if cmd == "relink":
        if len(sys.argv) < 3 or not sys.argv[2].isdigit():
            print("Использование: server.py relink <id>", file=sys.stderr)
            sys.exit(2)
        token = new_token()
        if not q("SELECT id FROM users WHERE id=?", (int(sys.argv[2]),), one=True):
            print("Нет рабочего места с таким номером", file=sys.stderr)
            sys.exit(1)
        ex("UPDATE users SET token_hash=? WHERE id=?", (token_hash(token), int(sys.argv[2])))
        print(token)
        return
    db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"Тендерный калькулятор: http://{HOST}:{PORT}" + ("" if INVITE else "  (приглашение не задано: войти может любой)"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
