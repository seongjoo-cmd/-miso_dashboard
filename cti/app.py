"""miso internet sales 통화 대시보드 HTTP 서버 (표준 라이브러리만 사용).

환경 변수
- CTI_DB           SQLite 파일 경로        (기본: ./data/cti.db)
- CTI_SYNC_ROOT    상담원 동기화 폴더 루트  (기본: ./data/sync)
- CTI_BACKUP_ROOT  백업 zip 저장 위치      (기본: ./data/backup)
- CTI_HOST / CTI_PORT                      (기본: 127.0.0.1 / 8080)
- CTI_INGEST_INTERVAL 녹취 폴더 자동 적재 주기(초, 0이면 끔) (기본: 300)
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import auth, backup, calls, dashboard, demo, drive_backup, ingest
from .db import connect, get_setting, get_setting_int, set_setting
from .periods import PRESETS, resolve

ROOT = Path(__file__).resolve().parent.parent
mimetypes.add_type("application/manifest+json", ".webmanifest")
STATIC = ROOT / "static"
COOKIE = "cti_session"


ON_VERCEL = bool(os.environ.get("VERCEL"))
# 녹취를 어디서 가져오나: folder(사무실 PC의 동기화 폴더) | drive(Apps Script 가 드라이브 목록을 보내 줌)
SOURCE = os.environ.get("CTI_SOURCE") or ("drive" if ON_VERCEL else "folder")


class Config:
    def __init__(self, db_path: Path, sync_root: Path, backup_root: Path, today=date.today,
                 demo: bool = False, db_url: str | None = None):
        self.db_path = db_path
        self.db_url = db_url  # 있으면 Postgres 사용(Vercel 등)
        self.sync_root = sync_root
        self.backup_root = backup_root
        self.today = today
        self.demo = demo  # 가짜 데이터로 채운 화면 확인용 모드

    @property
    def db_target(self):
        return self.db_url or self.db_path

    @classmethod
    def from_env(cls) -> "Config":
        # Vercel 은 /tmp 만 쓸 수 있고 인스턴스가 바뀌면 지워진다 → 영구 DB(DATABASE_URL) 연결 전까지는 데모 모드
        data = Path("/tmp/cti") if ON_VERCEL else ROOT / "data"
        # Vercel 의 Neon 연결 시 붙는 이름(접두어에 따라 DATABASE_URL / STORAGE_URL / POSTGRES_URL)
        db_url = next((os.environ[k] for k in ("DATABASE_URL", "STORAGE_URL", "POSTGRES_URL")
                       if os.environ.get(k, "").startswith(("postgres://", "postgresql://"))), None)
        demo_default = "1" if ON_VERCEL and not db_url else "0"
        return cls(
            Path(os.environ.get("CTI_DB", data / "cti.db")),
            Path(os.environ.get("CTI_SYNC_ROOT", data / "sync")),
            Path(os.environ.get("CTI_BACKUP_ROOT", data / "backup")),
            demo=os.environ.get("CTI_DEMO", demo_default) == "1",
            db_url=db_url,
        )


def bootstrap(cfg: Config) -> None:
    """서버 시작 시 준비: 스키마, (데모 모드면) 가짜 데이터, 관리자 계정."""
    if not cfg.db_url:
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(cfg.db_target)
    try:
        if cfg.demo and conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0:
            demo.seed(conn)
        # 콘솔이 없는 환경(Vercel)용: 환경 변수의 관리자 계정을 만들거나 그 비밀번호로 맞춘다
        # (환경 변수만 바꾸면 비밀번호 변경·분실 복구가 된다)
        pw = os.environ.get("CTI_ADMIN_PASSWORD")
        if pw:
            name = os.environ.get("CTI_ADMIN_USER", "admin")
            row = conn.execute("SELECT password_hash, role FROM users WHERE username = ?", (name,)).fetchone()
            if row is None or row["role"] != "admin" or not auth.verify_password(pw, row["password_hash"]):
                auth.create_user(conn, name, pw, "admin")
        elif cfg.demo and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            demo.create_demo_users(conn)
    finally:
        conn.close()


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"잘못된 날짜: {s}")


def _parse_period(cfg: Config, q: dict):
    preset = q.get("preset", "today")
    if preset not in PRESETS:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"알 수 없는 기간: {preset}")
    try:
        return resolve(preset, cfg.today(), _parse_date(q.get("start")), _parse_date(q.get("end")))
    except ValueError as e:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(e))


def _parse_int(q: dict, key: str) -> int | None:
    v = q.get(key)
    if not v:
        return None
    if not v.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"잘못된 값: {key}")
    return int(v)


def _parse_agent(q: dict) -> int | None:
    v = q.get("agent_id")
    if not v:
        return None
    if not v.isdigit():
        raise ApiError(HTTPStatus.BAD_REQUEST, "잘못된 상담원 ID")
    return int(v)


# 화면에서 바꿀 수 있는 설정과 허용 범위
EDITABLE_SETTINGS = {"min_call_sec": (0, 600), "fallback_kbps": (8, 512)}


def _save_settings(conn: sqlite3.Connection, body: dict) -> None:
    changes = {k: body[k] for k in EDITABLE_SETTINGS if k in body}
    if not changes:
        raise ApiError(HTTPStatus.BAD_REQUEST, "변경할 설정이 없습니다.")
    for k, v in changes.items():
        lo, hi = EDITABLE_SETTINGS[k]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{k} 는 {lo}~{hi} 사이의 정수여야 합니다.")
    for k, v in changes.items():
        set_setting(conn, k, str(v))
    conn.commit()


def _user_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def drive_token(conn) -> str:
    """Apps Script 가 /api/drive/push 에 보낼 연동 키(DB마다 한 번 생성)."""
    tok = get_setting(conn, "drive_push_token")
    if not tok:
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('drive_push_token', ?)",
                     (secrets.token_urlsafe(24),))
        conn.commit()
        tok = get_setting(conn, "drive_push_token")
    return tok


def drive_script(conn, base_url: str) -> str:
    tpl = (Path(__file__).with_name("drive_sync.gs")).read_text(encoding="utf-8")
    return tpl.replace("{{CTI_URL}}", base_url).replace("{{CTI_TOKEN}}", drive_token(conn))


WEBAPP_RE = re.compile(r"https://script\.google\.com/(a/macros/[^/]+|macros)/s/[A-Za-z0-9_-]+/exec")


def drive_sync_now(conn) -> dict:
    """[동기화] 버튼: Apps Script 웹 앱을 불러 바로 동기화(최대 약 25초)."""
    url = get_setting(conn, "drive_webapp_url")
    if not url:
        raise ApiError(HTTPStatus.CONFLICT, "설정 > 구글 드라이브 연결에서 동기화 주소(웹 앱 URL)를 먼저 넣어 주세요.")
    full = f"{url}?{urllib.parse.urlencode({'token': drive_token(conn), 'action': 'sync'})}"
    try:
        with urllib.request.urlopen(full, timeout=27) as r:  # 구글이 결과 주소로 넘겨줌(자동으로 따라감)
            data = json.loads(r.read().decode("utf-8"))
    except TimeoutError:
        return {"ok": True, "pending": True}
    except (urllib.error.URLError, ValueError) as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            return {"ok": True, "pending": True}
        raise ApiError(HTTPStatus.BAD_GATEWAY,
                       "서버에서 구글 스크립트를 바로 부를 수 없어요(회사 계정 전용 배포). 새 탭에서 동기화할게요.")
    if not data.get("ok"):
        raise ApiError(HTTPStatus.CONFLICT if data.get("busy") else HTTPStatus.BAD_GATEWAY,
                       data.get("error") or "동기화에 실패했어요.")
    return data


def _settings_payload(conn: sqlite3.Connection) -> dict:
    return {**{k: get_setting_int(conn, k) for k in EDITABLE_SETTINGS},
            "last_ingest": ingest.last_result(conn), "source": SOURCE,
            "drive_webapp_url": get_setting(conn, "drive_webapp_url") or ""}


def make_handler(cfg: Config):
    if not cfg.db_url:
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    connect(cfg.db_target).close()  # 스키마 초기화

    class Handler(BaseHTTPRequestHandler):
        server_version = "CTIDashboard/1.0"

        # ---- 공통 ----
        def log_message(self, fmt, *args):  # noqa: D401 - 조용한 로그
            if os.environ.get("CTI_QUIET"):
                return
            super().log_message(fmt, *args)

        def _send_json(self, status: int, payload, headers: dict | None = None):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _token(self) -> str | None:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c[COOKIE].value if COOKIE in c else None

        def _read_json(self) -> dict:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON 요청만 허용됩니다.")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "잘못된 JSON")

        def _dispatch(self, method: str):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            if not url.path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "허용되지 않는 메서드")
                return self._static(url.path)
            conn = connect(cfg.db_target)
            try:
                return self._api(method, url.path, q, conn)
            finally:
                conn.close()

        def _handle(self, method: str):
            try:
                self._dispatch(method)
            except ApiError as e:
                self._send_json(e.status, {"error": str(e)})
            except backup.BackupError as e:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(e)})
            except Exception as e:  # pragma: no cover - 예기치 못한 오류
                self.log_error("unhandled: %r", e)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "서버 오류"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        # ---- 정적 파일 ----
        def _static(self, path: str):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            if rel.startswith("static/"):
                rel = rel[len("static/"):]
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                raise ApiError(HTTPStatus.NOT_FOUND, "없는 경로")
            body = f.read_bytes()
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype == "application/javascript":
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ---- 구글 드라이브(Apps Script) 연동 ----
        def _base_url(self) -> str:
            proto = self.headers.get("X-Forwarded-Proto") or ("https" if ON_VERCEL else "http")
            return f"{proto}://{self.headers.get('Host', '127.0.0.1')}"

        def _check_drive_token(self, conn):
            sent = self.headers.get("X-CTI-Token", "")
            if not sent or not hmac.compare_digest(sent, drive_token(conn)):
                raise ApiError(HTTPStatus.UNAUTHORIZED, "연동 키가 맞지 않아요. 설정 화면의 스크립트를 다시 복사해 주세요.")

        def _drive_push(self, conn):
            self._check_drive_token(conn)
            body = self._read_json()
            files = body.get("files")
            if not isinstance(files, list) or len(files) > ingest.MAX_RECORDS:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"files 는 {ingest.MAX_RECORDS}개 이하 목록이어야 해요.")
            s = ingest.ingest_records(conn, [f for f in files if isinstance(f, dict)], bool(body.get("first", True)))
            return self._send_json(200, {"added": s["added"], "already": s["already"]})

        def _login_response(self, conn, token: str):
            user = {**auth.user_for_token(conn, token), "demo": cfg.demo}
            cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
            if self.headers.get("X-Forwarded-Proto") == "https":
                cookie += "; Secure"
            return self._send_json(200, user, {"Set-Cookie": cookie})

        # ---- API ----
        def _api(self, method: str, path: str, q: dict, conn: sqlite3.Connection):
            if method == "POST" and path == "/api/login":
                body = self._read_json()
                try:
                    token = auth.login(conn, str(body.get("username", "")), str(body.get("password", "")))
                except auth.LoginLocked:
                    raise ApiError(HTTPStatus.TOO_MANY_REQUESTS,
                                   "비밀번호를 여러 번 틀려 5분 동안 로그인할 수 없습니다.")
                if not token:
                    raise ApiError(HTTPStatus.UNAUTHORIZED, "아이디 또는 비밀번호가 올바르지 않습니다.")
                return self._login_response(conn, token)

            if method == "GET" and path == "/api/status":
                return self._send_json(200, {
                    "storage": "permanent" if cfg.db_url else ("temporary" if ON_VERCEL else "local"),
                    "demo": cfg.demo,
                    "needs_setup": _user_count(conn) == 0,
                })
            if method == "POST" and path == "/api/setup":
                # 계정이 하나도 없을 때만: 첫 관리자 계정을 화면에서 만든다
                body = self._read_json()
                name, pw = str(body.get("username", "")).strip(), str(body.get("password", ""))
                if _user_count(conn) > 0:
                    raise ApiError(HTTPStatus.CONFLICT, "이미 관리자 계정이 있어요. 로그인해 주세요.")
                if not 2 <= len(name) <= 32 or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "아이디는 영문·숫자 2~32자로 정해 주세요.")
                if len(pw) < 8:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "비밀번호는 8자 이상으로 정해 주세요.")
                auth.create_user(conn, name, pw, "admin")
                return self._login_response(conn, auth.login(conn, name, pw))

            if method == "POST" and path == "/api/drive/push":
                return self._drive_push(conn)
            if path == "/api/drive/tasks" and method == "GET":
                self._check_drive_token(conn)
                return self._send_json(200, {"tasks": drive_backup.pending(conn)})
            m = re.fullmatch(r"/api/drive/tasks/(\d+)", path)
            if m and method == "POST":
                self._check_drive_token(conn)
                body = self._read_json()
                try:
                    t = drive_backup.report(conn, int(m.group(1)), str(body.get("status")),
                                            body.get("moved_ids") or [], body.get("failed") or 0, body.get("message"))
                except ValueError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
                except LookupError as e:
                    raise ApiError(HTTPStatus.NOT_FOUND, str(e))
                return self._send_json(200, {"status": t["status"], "moved": t["moved"]})

            user = auth.user_for_token(conn, self._token())
            if user is None:
                raise ApiError(HTTPStatus.UNAUTHORIZED, "로그인이 필요합니다.")

            if method == "POST" and path == "/api/logout":
                auth.logout(conn, self._token())
                return self._send_json(200, {"ok": True},
                                       {"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            if method == "GET" and path == "/api/me":
                return self._send_json(200, {**user, "demo": cfg.demo})
            if method == "GET" and path == "/api/agents":
                rows = conn.execute(
                    "SELECT id, name, extension FROM agents WHERE active = 1 ORDER BY name").fetchall()
                return self._send_json(200, [dict(r) for r in rows])
            if path == "/api/settings":
                if method == "POST":
                    if user["role"] != "admin":
                        raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 변경할 수 있습니다.")
                    _save_settings(conn, self._read_json())
                return self._send_json(200, _settings_payload(conn))
            if path in ("/api/drive/backup", "/api/drive/backup/all"):
                if user["role"] != "admin":
                    raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 할 수 있어요.")
                if method == "POST" and path.endswith("/all"):
                    self._read_json()
                    n = drive_backup.request_all(conn, user["username"])
                    return self._send_json(200, {**drive_backup.overview(conn), "requested": n})
                if method == "POST":
                    ids = self._read_json().get("agent_ids")
                    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
                        raise ApiError(HTTPStatus.BAD_REQUEST, "백업할 상담원을 골라 주세요.")
                    n = drive_backup.request_many(conn, ids, user["username"])
                    return self._send_json(200, {**drive_backup.overview(conn), "requested": n})
                if method == "GET" and not path.endswith("/all"):
                    return self._send_json(200, drive_backup.overview(conn))
            if method == "POST" and path == "/api/drive/webapp":
                if user["role"] != "admin":
                    raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 바꿀 수 있어요.")
                url = str(self._read_json().get("url", "")).strip()
                if url and not WEBAPP_RE.fullmatch(url):
                    raise ApiError(HTTPStatus.BAD_REQUEST,
                                   "웹 앱 URL 은 https://script.google.com/macros/s/…/exec 모양이에요.")
                set_setting(conn, "drive_webapp_url", url)
                conn.commit()
                return self._send_json(200, _settings_payload(conn))
            if method == "POST" and path == "/api/drive/sync-now":
                self._read_json()
                return self._send_json(200, drive_sync_now(conn))
            if method == "GET" and path == "/api/drive/script":
                if user["role"] != "admin":
                    raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 볼 수 있어요.")
                return self._send_json(200, {"script": drive_script(conn, self._base_url())})
            if method == "POST" and path == "/api/ingest":
                if user["role"] != "admin":
                    raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 실행할 수 있습니다.")
                self._read_json()
                ingest.ingest_all(conn, cfg.sync_root)
                return self._send_json(200, _settings_payload(conn))
            if method == "GET" and path == "/api/dashboard":
                cur, prev = _parse_period(cfg, q)
                return self._send_json(200, dashboard.build(conn, cur, prev, _parse_agent(q)))

            if method == "GET" and path == "/api/calls":
                period = None if q.get("preset") == "all" else _parse_period(cfg, q)[0]
                try:
                    return self._send_json(200, calls.search(
                        conn, period, _parse_agent(q), q.get("q"),
                        _parse_int(q, "same_as"), _parse_int(q, "min_sec"), q.get("cursor"),
                        hour_from=_parse_int(q, "hour_from"), hour_to=_parse_int(q, "hour_to")))
                except calls.SearchError as e:
                    raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
            m = re.fullmatch(r"/api/calls/(\d+)", path)
            if m and method == "GET":
                d = calls.detail(conn, int(m.group(1)))
                if d is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "녹취를 찾을 수 없어요.")
                return self._send_json(200, d)

            m = re.fullmatch(r"/api/agents/(\d+)(/backup)?", path)
            if m:
                agent_id = int(m.group(1))
                if method == "GET" and not m.group(2):
                    cur, _ = _parse_period(cfg, q)
                    detail = dashboard.agent_detail(conn, agent_id, cur)
                    if detail is None:
                        raise ApiError(HTTPStatus.NOT_FOUND, "상담원을 찾을 수 없습니다.")
                    if user["role"] == "admin" and SOURCE == "folder":
                        detail["sync_folder"] = backup.folder_status(conn, agent_id, cfg.sync_root)
                        detail["backups"] = backup.history(conn, agent_id)
                    return self._send_json(200, detail)
                if method == "POST" and m.group(2):
                    if user["role"] != "admin":
                        raise ApiError(HTTPStatus.FORBIDDEN, "관리자만 실행할 수 있습니다.")
                    self._read_json()  # Content-Type 강제(CSRF 완화)
                    if SOURCE == "drive":
                        if not conn.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone():
                            raise ApiError(HTTPStatus.NOT_FOUND, "상담원을 찾을 수 없어요.")
                        drive_backup.request(conn, agent_id, user["username"])
                        return self._send_json(200, drive_backup.overview(conn))
                    result = backup.backup_and_clear(
                        conn, agent_id, cfg.sync_root, cfg.backup_root, user["username"])
                    return self._send_json(200, {
                        "backup": result,
                        "sync_folder": backup.folder_status(conn, agent_id, cfg.sync_root),
                        "backups": backup.history(conn, agent_id),
                    })
            raise ApiError(HTTPStatus.NOT_FOUND, "없는 API")

    return Handler


def _ingest_loop(cfg: Config, interval: int) -> None:
    """동기화 폴더를 주기적으로 읽어 새 녹취 파일을 적재한다."""
    while True:
        try:
            conn = connect(cfg.db_target)
            try:
                s = ingest.ingest_all(conn, cfg.sync_root)
            finally:
                conn.close()
            if s["added"] or s["failed"]:
                print(f"[적재] 추가 {s['added']}건, 해석 실패 {len(s['failed'])}건")
        except Exception as e:  # 적재 실패가 서버를 멈추지 않도록
            print(f"[적재] 오류: {e!r}")
        time.sleep(interval)


def _local_ips() -> list[str]:
    """이 컴퓨터의 IPv4 주소(같은 와이파이, Tailscale 100.x 포함)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # 기본 경로의 주소(패킷은 보내지 않음)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main() -> None:
    cfg = Config.from_env()
    host = os.environ.get("CTI_HOST", "127.0.0.1")
    port = int(os.environ.get("CTI_PORT", "8080"))
    interval = int(os.environ.get("CTI_INGEST_INTERVAL", "300"))
    bootstrap(cfg)
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg))
    if interval > 0:
        threading.Thread(target=_ingest_loop, args=(cfg, interval), daemon=True).start()
    print(f"miso internet sales 통화 대시보드: http://127.0.0.1:{port}  (DB: {cfg.db_path}, 녹취 폴더: {cfg.sync_root})")
    if host in ("0.0.0.0", ""):
        for ip in _local_ips():
            print(f"  휴대폰·다른 컴퓨터에서: http://{ip}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
