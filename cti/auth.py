"""간단한 사용자/세션 관리 (역할: admin, viewer)."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta

SESSION_TTL = timedelta(hours=12)
# 회원가입은 회사 이메일만. 등급은 관리자(admin)·회원(viewer) 두 가지.
# 관리자는 회원 관리(등급·비밀번호 초기화·탈퇴)를 할 수 있고, OWNER 계정은 아무도 바꾸거나 지울 수 없다.
SIGNUP_DOMAIN = "getmiso.com"
OWNER = "seongjoo@getmiso.com"
_EMAIL = re.compile(r"[a-z0-9._%+-]+@" + re.escape(SIGNUP_DOMAIN))
_ITER = 200_000

# 비밀번호 대입 방지: 같은 아이디로 연속 MAX_FAILS 회 틀리면 LOCK_SEC 동안 로그인 차단
MAX_FAILS = 5
LOCK_SEC = 300
_fails: dict[str, tuple[int, float]] = {}  # username -> (연속 실패 수, 마지막 실패 시각)
_fails_lock = threading.Lock()


class LoginLocked(Exception):
    pass


def _check_lock(username: str) -> None:
    with _fails_lock:
        n, at = _fails.get(username, (0, 0.0))
        if n >= MAX_FAILS and time.time() - at < LOCK_SEC:
            raise LoginLocked
        if n >= MAX_FAILS:  # 잠금 시간이 지나면 초기화
            _fails.pop(username, None)


def _record(username: str, ok: bool) -> None:
    with _fails_lock:
        if ok:
            _fails.pop(username, None)
        else:
            n, _ = _fails.get(username, (0, 0.0))
            _fails[username] = (n + 1, time.time())


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITER)
    return f"pbkdf2_sha256${_ITER}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
    return hmac.compare_digest(dk.hex(), digest)


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str) -> None:
    conn.execute(
        "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?) "
        "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, role = excluded.role",
        (username, hash_password(password), role),
    )
    conn.commit()


def normalize(username: str) -> str:
    """이메일 아이디는 대소문자를 가리지 않는다."""
    username = username.strip()
    return username.lower() if "@" in username else username


def valid_signup_email(email: str) -> bool:
    return bool(_EMAIL.fullmatch(email))


def is_owner(user: dict | None) -> bool:
    return bool(user) and user["username"] == OWNER


class SignupError(ValueError):
    pass


def signup(conn: sqlite3.Connection, email: str, password: str) -> str:
    """회사 이메일로 가입. OWNER 는 관리자, 나머지는 조회 권한. 정리된 이메일을 돌려준다."""
    email = normalize(email)
    if not valid_signup_email(email):
        raise SignupError(f"@{SIGNUP_DOMAIN} 이메일로만 가입할 수 있어요.")
    if len(password) < 8:
        raise SignupError("비밀번호는 8자 이상으로 정해 주세요.")
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (email,)).fetchone():
        raise SignupError("이미 가입한 이메일이에요. 로그인해 주세요.")
    conn.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
                 (email, hash_password(password), "admin" if email == OWNER else "viewer"))
    conn.commit()
    return email


def change_password(conn: sqlite3.Connection, user_id: int, current: str, new: str) -> None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current, row["password_hash"]):
        raise ValueError("지금 비밀번호가 맞지 않아요.")
    if len(new) < 8:
        raise ValueError("새 비밀번호는 8자 이상으로 정해 주세요.")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
    conn.commit()


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
    return [{**dict(r), "owner": r["username"] == OWNER} for r in rows]


def _target(conn: sqlite3.Connection, actor: dict, user_id: int) -> dict:
    row = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise LookupError("회원을 찾을 수 없어요.")
    if row["username"] == OWNER:
        raise ValueError(f"{OWNER} 계정은 바꿀 수 없어요.")
    if row["id"] == actor["id"]:
        raise ValueError("내 계정은 여기서 바꿀 수 없어요.")
    return dict(row)


def set_role(conn: sqlite3.Connection, actor: dict, user_id: int, role: str) -> None:
    if role not in ("admin", "viewer"):
        raise ValueError("등급은 관리자 또는 회원이에요.")
    _target(conn, actor, user_id)
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()


def reset_password(conn: sqlite3.Connection, actor: dict, user_id: int) -> str:
    """임시 비밀번호로 바꾸고 로그인된 세션을 끊는다. 임시 비밀번호를 돌려준다."""
    _target(conn, actor, user_id)
    temp = secrets.token_urlsafe(9)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(temp), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    return temp


def delete_user(conn: sqlite3.Connection, actor: dict, user_id: int) -> str:
    u = _target(conn, actor, user_id)
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return u["username"]


def login(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    """성공하면 세션 토큰, 틀리면 None. 잠긴 아이디면 LoginLocked."""
    username = normalize(username)
    _check_lock(username)
    row = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    ok = row is not None and verify_password(password, row["password_hash"])
    _record(username, ok)
    if not ok:
        return None
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + SESSION_TTL).isoformat(timespec="seconds")
    conn.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)",
                 (token, row["id"], expires))
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(timespec="seconds"),))
    conn.commit()
    return token


def logout(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def user_for_token(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.username, u.role FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at >= ?
        """,
        (token, datetime.now().isoformat(timespec="seconds")),
    ).fetchone()
    return dict(row) if row else None
