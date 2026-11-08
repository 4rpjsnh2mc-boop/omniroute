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
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import market
import pricein
import tparse as tparser

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
DATA = os.environ.get("TENDER_DATA", os.path.join(BASE, "..", "data"))
HOST = os.environ.get("TENDER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TENDER_PORT", "8090"))
DB_PATH = os.path.join(DATA, "app.db")
INVITE = os.environ.get("TENDER_INVITE", "").strip()
TG_TOKEN = os.environ.get("TENDER_TG_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("TENDER_PUBLIC_URL", "").strip().rstrip("/")
RENDER_PY = os.environ.get("TENDER_RENDER", "").strip()  # python из venv с Playwright: открывает сайты на JavaScript
JS_HOSTS = ("bidzaar.com",)  # площадки, которые без JavaScript показывают пустую страницу
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
    "minMarginPct": 3,
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
            CREATE TABLE IF NOT EXISTS reminders (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
              at INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '', sent INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS reminders_due ON reminders(sent, at);
            CREATE TABLE IF NOT EXISTS notified (tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
              kind TEXT NOT NULL, PRIMARY KEY (tender_id, kind));
            CREATE TABLE IF NOT EXISTS market (tender_id INTEGER PRIMARY KEY REFERENCES tenders(id) ON DELETE CASCADE,
              data TEXT NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS docflags (tender_id INTEGER PRIMARY KEY REFERENCES tenders(id) ON DELETE CASCADE,
              data TEXT NOT NULL);
            """
        )
        for table, col, decl in (("users", "tg_chat", "INTEGER"), ("users", "tg_code", "TEXT"),
                                 ("tenders", "deadline_ts", "INTEGER"), ("tenders", "stage", "TEXT NOT NULL DEFAULT ''"),
                                 ("tenders", "important", "INTEGER NOT NULL DEFAULT 0"), ("tenders", "cost", "REAL")):
            cols = [r[1] for r in _db.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                _db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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


_render_lock = threading.Semaphore(1)  # браузер тяжёлый: по одной странице за раз


def render_html(url):
    """HTML страницы после выполнения JavaScript или None, если браузер не установлен или не справился."""
    if not RENDER_PY or not os.path.exists(RENDER_PY):
        return None
    _check_url(url)
    with _render_lock:
        try:
            r = subprocess.run([RENDER_PY, os.path.join(BASE, "render.py"), url], capture_output=True, timeout=90,
                               env={**os.environ, "HOME": DATA})
        except (subprocess.TimeoutExpired, OSError) as e:
            sys.stderr.write(f"render {url}: {e!r}\n")
            return None
    if r.returncode != 0 or not r.stdout:
        sys.stderr.write(f"render {url}: code {r.returncode} {r.stderr[-500:]!r}\n")
        return None
    return r.stdout[:MAX_PAGE].decode("utf-8", "replace")


# ---------------------------------------------------------------- документы (фоновая загрузка)

_jobs = {}
_jobs_lock = threading.Lock()


def doc_dir(tender_id):
    d = os.path.join(FILES, str(int(tender_id)))
    os.makedirs(d, exist_ok=True)
    return d


def update_docflags(tender_id):
    """Ищем в документах тендера «или эквивалент», СМП, нацрежим и т.п."""
    found = {}
    for d in q("SELECT name, path FROM docs WHERE tender_id=? AND status='ok'", (tender_id,)):
        try:
            with open(os.path.join(FILES, str(int(tender_id)), d["path"]), "rb") as f:
                text = tparser.doc_text(d["name"], f.read(MAX_DOC))
        except OSError:
            continue
        for k, v in tparser.flags(text).items():
            if k not in found or (k == "analogs" and v["value"] == "forbidden"):
                v["doc"] = d["name"]
                found[k] = v
    ex("INSERT INTO docflags(tender_id, data) VALUES (?,?) ON CONFLICT(tender_id) DO UPDATE SET data=excluded.data",
       (tender_id, json.dumps(found, ensure_ascii=False)))


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
        try:
            update_docflags(tender_id)
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
        for k in ("overheadPct", "targetPct", "moneyRatePct", "bgRatePct", "minMarginPct"):
            v = num_or_none(st.get(k))
            if v is not None and 0 <= v <= 100:
                s[k] = v
        if "supplierVat" in st:
            s["supplierVat"] = bool(st["supplierVat"])
        if isinstance(st.get("myPlatforms"), list):
            s["myPlatforms"] = [str(x)[:40] for x in st["myPlatforms"][:200] if re.fullmatch(r"[a-z0-9-]{1,40}", str(x))]
        ex("UPDATE users SET settings=? WHERE id=?", (json.dumps(s, ensure_ascii=False), user["id"]))
    u = q("SELECT * FROM users WHERE id=?", (user["id"],), one=True)
    h.send_json({"user": public_user(u), "settings": get_settings(u)})


# ---------------------------------------------------------------- API: тендеры

SUMMARY = ("title", "number", "url", "customer", "status", "nmck", "price", "profit", "margin", "deadline",
           "deadline_ts", "stage", "important", "cost")
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
    dts = num_or_none(s.get("deadlineTs"))
    old = q("SELECT deadline_ts FROM tenders WHERE id=?", (int(tid),), one=True)
    if old and (old["deadline_ts"] or None) != (int(dts) if dts else None):
        ex("DELETE FROM notified WHERE tender_id=?", (int(tid),))  # срок сменился: напомним заново
    ex(f"""UPDATE tenders SET data=?, title=?, number=?, url=?, customer=?, status=?, nmck=?, price=?, profit=?, margin=?,
           deadline=?, deadline_ts=?, stage=?, important=?, cost=?, updated=? WHERE id=? AND user_id=?""",
       (raw, str(s.get("title") or "")[:300], str(s.get("number") or "")[:100], str(s.get("url") or "")[:1000],
        str(s.get("customer") or "")[:300], status, num_or_none(s.get("nmck")), num_or_none(s.get("price")),
        num_or_none(s.get("profit")), num_or_none(s.get("margin")), str(s.get("deadline") or "")[:80],
        int(dts) if dts else None, str(s.get("stage") or "")[:40], 1 if s.get("important") else 0,
        num_or_none(s.get("cost")), now(), int(tid), user["id"]))
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
        final, headers, data = fetch(tparser.normalize_url(url))
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
    host = (urllib.parse.urlsplit(final).hostname or "").lower()
    if info["textLength"] < 400 or not info["items"] and any(host == d or host.endswith("." + d) for d in JS_HOSTS):
        html = render_html(final)
        if html:
            rendered = tparser.extract([(final, html)] + pages[1:], final)
            if rendered["textLength"] > info["textLength"]:
                info = rendered
    if info["textLength"] < 200:
        if any(host == d or host.endswith("." + d) for d in JS_HOSTS) and not RENDER_PY:
            raise ApiError(400, "Эта площадка работает на JavaScript. Включите на сервере встроенный браузер: запустите установку ещё раз и ответьте «да» на вопрос про сайты на JavaScript")
        raise ApiError(400, "Страница почти пустая: площадка показывает закупку только после входа. Сделайте скриншоты или загрузите документы")
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
    fl = q("SELECT data FROM docflags WHERE tender_id=?", (int(tid),), one=True)
    h.send_json({"docs": rows, "job": job, "flags": json.loads(fl["data"]) if fl else {}})


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
    update_docflags(int(tid))
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

# ---------------------------------------------------------------- API: цены конкурентов (ЕИС)

_market_jobs = {}


def run_market(tid, payload):
    def progress(done, total, text):
        with _jobs_lock:
            _market_jobs[tid] = {"running": True, "done": done, "total": total, "text": text}
    try:
        data = market.analyze(payload, lambda u: fetch(u, timeout=40), decode_html, progress)
        ex("INSERT INTO market(tender_id, data, updated) VALUES (?,?,?) ON CONFLICT(tender_id) DO UPDATE SET data=excluded.data, updated=excluded.updated",
           (tid, json.dumps(data, ensure_ascii=False), now()))
        with _jobs_lock:
            _market_jobs[tid] = {"running": False, "text": "Готово"}
    except Exception as e:  # сообщаем пользователю коротко, без внутренностей
        sys.stderr.write(f"market {tid}: {e!r}\n")
        with _jobs_lock:
            _market_jobs[tid] = {"running": False, "error": str(e) if isinstance(e, FetchError) else "Поиск не удался. Попробуйте позже"}


@route("POST", r"/api/tenders/(\d+)/market")
def api_market_start(h, user, tid):
    own_tender(user, tid)
    tid = int(tid)
    b = h.json_body()
    items = [{"id": str(i.get("id") or ""), "name": str(i.get("name") or "")[:300], "unit": str(i.get("unit") or "")[:30],
              "qty": num_or_none(i.get("qty")) or 0, "nmckUnit": num_or_none(i.get("nmckUnit")) or 0, "cost": num_or_none(i.get("cost")) or 0}
             for i in (b.get("items") or [])[:100] if isinstance(i, dict)]
    payload = {"items": items, "place": str(b.get("place") or "")[:300], "customer": str(b.get("customer") or "")[:300],
               "inn": re.sub(r"\D", "", str(b.get("inn") or ""))[:12]}
    with _jobs_lock:
        if _market_jobs.get(tid, {}).get("running"):
            raise ApiError(409, "Поиск уже идёт")
        if sum(1 for j in _market_jobs.values() if j.get("running")) >= 3:
            raise ApiError(429, "Сервер занят другими поисками. Попробуйте через минуту")
        _market_jobs[tid] = {"running": True, "done": 0, "total": 1, "text": "Начинаю"}
    threading.Thread(target=run_market, args=(tid, payload), daemon=True).start()
    h.send_json({"ok": True})


@route("GET", r"/api/tenders/(\d+)/market")
def api_market_get(h, user, tid):
    own_tender(user, tid)
    row = q("SELECT data, updated FROM market WHERE tender_id=?", (int(tid),), one=True)
    with _jobs_lock:
        job = dict(_market_jobs.get(int(tid), {})) or None
    h.send_json({"job": job, "data": json.loads(row["data"]) if row else None})


# ---------------------------------------------------------------- API: напоминания

@route("GET", r"/api/reminders")
def api_reminders_all(h, user):
    rows = q("""SELECT r.id, r.tender_id, r.at, r.text, r.sent, t.title FROM reminders r JOIN tenders t ON t.id=r.tender_id
                WHERE r.user_id=? AND r.at > ? ORDER BY r.at LIMIT 100""", (user["id"], now() - 7 * 86400))
    h.send_json({"reminders": rows})


@route("GET", r"/api/tenders/(\d+)/reminders")
def api_reminders(h, user, tid):
    own_tender(user, tid)
    h.send_json({"reminders": q("SELECT id, at, text, sent FROM reminders WHERE tender_id=? ORDER BY at", (int(tid),))})


@route("POST", r"/api/tenders/(\d+)/reminders")
def api_reminder_add(h, user, tid):
    own_tender(user, tid)
    b = h.json_body()
    at = num_or_none(b.get("at"))
    if not at or at < now() - 60 or at > now() + 400 * 86400:
        raise ApiError(400, "Укажите дату и время в будущем")
    if q("SELECT COUNT(*) AS n FROM reminders WHERE tender_id=?", (int(tid),), one=True)["n"] >= 30:
        raise ApiError(400, "Слишком много напоминаний для одного тендера")
    rid = ex("INSERT INTO reminders(user_id, tender_id, at, text, created) VALUES (?,?,?,?,?)",
             (user["id"], int(tid), int(at), str(b.get("text") or "")[:300], now()))
    h.send_json({"id": rid})


@route("DELETE", r"/api/tenders/(\d+)/reminders/(\d+)")
def api_reminder_delete(h, user, tid, rid):
    own_tender(user, tid)
    ex("DELETE FROM reminders WHERE id=? AND tender_id=?", (int(rid), int(tid)))
    h.send_json({"ok": True})


# ---------------------------------------------------------------- API: Telegram

@route("GET", r"/api/telegram")
def api_tg(h, user):
    h.send_json({"enabled": bool(TG_TOKEN and TG["username"]), "linked": bool(user.get("tg_chat"))})


@route("POST", r"/api/telegram/link")
def api_tg_link(h, user):
    if not (TG_TOKEN and TG["username"]):
        raise ApiError(400, "Telegram-бот не подключён на сервере")
    code = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    ex("UPDATE users SET tg_code=? WHERE id=?", (code, user["id"]))
    h.send_json({"url": f"https://t.me/{TG['username']}?start={code}"})


@route("DELETE", r"/api/telegram/link")
def api_tg_unlink(h, user):
    ex("UPDATE users SET tg_chat=NULL, tg_code=NULL WHERE id=?", (user["id"],))
    h.send_json({"ok": True})


@route("POST", r"/api/telegram/test")
def api_tg_test(h, user):
    if not user.get("tg_chat"):
        raise ApiError(400, "Сначала подключите Telegram")
    if not tg_send(user["tg_chat"], "Проверка: напоминания тендерного калькулятора будут приходить сюда."):
        raise ApiError(502, "Telegram не ответил. Попробуйте позже")
    h.send_json({"ok": True})


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
    known = price_names(user)
    added = updated = 0
    for r in rows[:5000]:
        if not isinstance(r, dict):
            continue
        try:
            name, unit, cost, note = price_fields(r)
        except ApiError:
            continue
        pid = known.get(name.lower())
        if pid:
            ex("UPDATE prices SET unit=?, cost=?, note=?, updated=? WHERE id=?", (unit, cost, note, now(), pid))
            updated += 1
        else:
            known[name.lower()] = ex("INSERT INTO prices(user_id, name, unit, cost, note, updated) VALUES (?,?,?,?,?,?)",
                                     (user["id"], name, unit, cost, note, now()))
            added += 1
    h.send_json({"ok": True, "count": added + updated, "added": added, "updated": updated})


def price_names(user):
    """Название в нижнем регистре → id. Python понимает регистр кириллицы, SQLite нет."""
    return {r["name"].lower(): r["id"] for r in q("SELECT id, name FROM prices WHERE user_id=?", (user["id"],))}


# ---------------------------------------------------------------- загрузка прайса из файла, текста, ссылки

MAX_PRICE_FILE = 25 * 1024 * 1024
_parse_lock = threading.Semaphore(2)  # распознавание картинок нагружает процессор


def download_price(url):
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    try:
        if pricein.is_yandex_disk(url):
            try:
                _, _, meta = fetch(pricein.YANDEX_API + urllib.parse.quote(url, safe=""), limit=200000)
                href = json.loads(meta)["href"]
            except (FetchError, ValueError, KeyError, TypeError):
                raise ApiError(400, "Яндекс.Диск не отдал файл. Проверьте, что ссылка публичная (Поделиться → Скопировать ссылку)")
            final, headers, data = fetch(href, limit=MAX_PRICE_FILE, timeout=90)
        else:
            final, headers, data = fetch(pricein.cloud_download_url(url), limit=MAX_PRICE_FILE, timeout=90)
    except FetchError as e:
        msg = str(e)
        if "не пустила" in msg:
            msg = "Нет доступа к файлу. Откройте доступ «всем, у кого есть ссылка»"
        elif "большой" in msg:
            msg = "Файл больше 25 МБ"
        raise ApiError(400, msg)
    name = filename_from_headers(headers) or urllib.parse.unquote(urllib.parse.urlsplit(final).path.rsplit("/", 1)[-1])
    return name or "файл", data


@route("POST", r"/api/prices/parse")
def api_price_parse(h, user):
    """Распознаёт прайс и возвращает строки для проверки. В прайс ничего не записывает."""
    if too_many(str(user["id"]), "parse", 150):
        raise ApiError(429, "Слишком много загрузок за час. Попробуйте позже")
    hit(str(user["id"]), "parse")
    text = None
    if (h.headers.get("Content-Type") or "").startswith("application/json"):
        b = h.json_body()
        if str(b.get("url") or "").strip():
            name, data = download_price(str(b["url"]).strip())
        elif str(b.get("text") or "").strip():
            name, text = "текст", str(b["text"])[:3000000]
        else:
            raise ApiError(400, "Нет ни файла, ни ссылки, ни текста")
    else:
        if int(h.headers.get("Content-Length") or 0) > MAX_PRICE_FILE:
            raise ApiError(413, "Файл больше 25 МБ")
        name = urllib.parse.unquote(h.headers.get("X-File-Name") or "файл")[:200]
        data = h.body_bytes(MAX_PRICE_FILE)
        if not data:
            raise ApiError(400, "Пустой файл")
    with _parse_lock:
        try:
            if text is not None:
                items, source, warns = pricein.parse_text(text), "текст", []
            else:
                items, source, warns = pricein.parse_bytes(name, data)
        except Exception:
            import traceback
            traceback.print_exc()
            items, source, warns = [], "файл", ["Не получилось прочитать файл. Попробуйте другой формат"]
    known = price_names(user)
    rows = [dict(it, exists=it["name"].lower() in known) for it in items[:3000]]
    if not rows and not warns:
        warns.append("Не нашли строк с ценами. Нужны название и цена в одной строке")
    h.send_json({"rows": rows, "source": source, "warnings": warns, "file": name})


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

# ---------------------------------------------------------------- Telegram и напоминания

TG = {"username": ""}


def tg_call(method, params, timeout=20):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def tg_send(chat_id, text):
    try:
        return tg_call("sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}).get("ok", False)
    except Exception as e:
        sys.stderr.write(f"telegram send: {e!r}\n")
        return False


def tg_poll():
    offset = 0
    while True:
        try:
            if not TG["username"]:
                TG["username"] = tg_call("getMe", {})["result"]["username"]
            res = tg_call("getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message"]}, timeout=70)
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = (msg.get("chat") or {}).get("id")
                m = re.match(r"^/start\s+([A-Za-z0-9]{8,40})$", text)
                if m and chat:
                    user = q("SELECT id, name FROM users WHERE tg_code=?", (m.group(1),), one=True)
                    if user:
                        ex("UPDATE users SET tg_chat=?, tg_code=NULL WHERE id=?", (chat, user["id"]))
                        tg_send(chat, "Готово. Напоминания о тендерах будут приходить сюда.")
                    else:
                        tg_send(chat, "Ссылка устарела. Откройте калькулятор: Настройки, «Подключить Telegram».")
                elif chat and text.startswith("/start"):
                    tg_send(chat, "Чтобы получать напоминания, откройте калькулятор: Настройки, «Подключить Telegram».")
        except Exception as e:
            sys.stderr.write(f"telegram poll: {e!r}\n")
            time.sleep(15)


def fmt_rub(v):
    return f"{v:,.0f} ₽".replace(",", " ") if isinstance(v, (int, float)) else "—"


def tender_text(t, head):
    left = ""
    if t.get("deadline_ts"):
        sec = t["deadline_ts"] - now()
        left = f" (осталось {sec // 86400} дн. {sec % 86400 // 3600} ч.)" if sec > 0 else " (срок прошёл)"
    lines = [head, t.get("title") or "Без названия"]
    if t.get("number"):
        lines.append(f"№ {t['number']}")
    if t.get("deadline"):
        lines.append(f"Подача до: {t['deadline']}{left}")
    if t.get("cost"):
        lines.append(f"Закупка: {fmt_rub(t['cost'])}")
    if t.get("price"):
        lines.append(f"Наша цена: {fmt_rub(t['price'])}")
    if t.get("profit") is not None and t.get("margin") is not None:
        lines.append(f"Чистая прибыль: {fmt_rub(t['profit'])}, маржа {t['margin']:.1f}%".replace(".", ","))
    if t.get("stage"):
        lines.append(f"Следующий шаг: {t['stage']}")
    if PUBLIC_URL:
        lines.append(f"{PUBLIC_URL}/#/t/{t['id']}")
    elif t.get("url"):
        lines.append(t["url"])
    return "\n".join(lines)


def reminder_loop():
    while True:
        try:
            for r in q("""SELECT r.*, u.tg_chat FROM reminders r JOIN users u ON u.id=r.user_id
                          WHERE r.sent=0 AND r.at<=? LIMIT 50""", (now(),)):
                t = q("SELECT * FROM tenders WHERE id=?", (r["tender_id"],), one=True)
                if r["tg_chat"] and t:
                    tg_send(r["tg_chat"], tender_text(t, "⏰ " + (r["text"] or "Напоминание")))
                ex("UPDATE reminders SET sent=1 WHERE id=?", (r["id"],))
            for kind, window, head in (("d1", 86400, "⏰ Завтра окончание подачи заявок"), ("h3", 3 * 3600, "⏰ Через 3 часа окончание подачи заявок")):
                for t in q(f"""SELECT t.*, u.tg_chat FROM tenders t JOIN users u ON u.id=t.user_id
                               WHERE u.tg_chat IS NOT NULL AND t.deadline_ts IS NOT NULL AND t.deadline_ts > ? AND t.deadline_ts <= ?
                               AND t.status IN ('draft', 'submitted')
                               AND NOT EXISTS (SELECT 1 FROM notified n WHERE n.tender_id=t.id AND n.kind=?) LIMIT 50""",
                           (now(), now() + window, kind)):
                    tg_send(t["tg_chat"], tender_text(t, head))
                    ex("INSERT OR IGNORE INTO notified(tender_id, kind) VALUES (?,?)", (t["id"], kind))
        except Exception as e:
            sys.stderr.write(f"reminders: {e!r}\n")
        time.sleep(60)


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
    threading.Thread(target=reminder_loop, daemon=True).start()
    if TG_TOKEN:
        threading.Thread(target=tg_poll, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print(f"Тендерный калькулятор: http://{HOST}:{PORT}" + ("" if INVITE else "  (приглашение не задано: войти может любой)"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
