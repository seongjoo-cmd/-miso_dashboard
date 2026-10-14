"""회원가입 인증번호: 회사 이메일의 슬랙 계정으로 6자리 번호를 DM 으로 보낸다.

번호는 DB 에 해시로만 두고(서버가 여러 대여도 공유), 10분 동안 MAX_TRIES 번까지 맞춰 볼 수 있다.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta

from . import auth, slack

CODE_TTL = timedelta(minutes=10)
RESEND_SEC = 60
MAX_TRIES = 5


class SignupError(ValueError):
    pass


def _hash(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _check_email(conn: sqlite3.Connection, email: str) -> str:
    email = auth.normalize(email)
    if not auth.valid_signup_email(email):
        raise SignupError(f"@{auth.SIGNUP_DOMAIN} 이메일로만 가입할 수 있어요.")
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (email,)).fetchone():
        raise SignupError("이미 가입한 이메일이에요. 로그인해 주세요.")
    return email


def send_code(conn: sqlite3.Connection, email: str) -> str:
    """인증번호를 만들어 슬랙 DM 으로 보낸다. 정리된 이메일을 돌려준다."""
    email = _check_email(conn, email)
    row = conn.execute("SELECT sent_at FROM signup_codes WHERE email = ?", (email,)).fetchone()
    if row and _now() - datetime.fromisoformat(row["sent_at"]) < timedelta(seconds=RESEND_SEC):
        raise SignupError("인증번호를 방금 보냈어요. 1분 뒤에 다시 받아 주세요.")
    code = f"{secrets.randbelow(10**6):06d}"
    try:
        slack.dm(conn, email, f"miso 통화 대시보드 가입 인증번호는 *{code}* 예요. 10분 안에 입력해 주세요.")
    except slack.SlackError as e:
        raise SignupError(str(e))
    now = _now()
    conn.execute(
        "INSERT INTO signup_codes(email, code_hash, expires_at, tries, sent_at) VALUES (?, ?, ?, 0, ?) "
        "ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at, "
        "tries = 0, sent_at = excluded.sent_at",
        (email, _hash(email, code), (now + CODE_TTL).isoformat(sep=" "), now.isoformat(sep=" ")))
    conn.commit()
    return email


def signup(conn: sqlite3.Connection, email: str, password: str, code: str) -> str:
    """인증번호를 확인하고 가입시킨다. 정리된 이메일을 돌려준다."""
    email = _check_email(conn, email)
    if len(password) < 8:
        raise SignupError("비밀번호는 8자 이상으로 정해 주세요.")
    row = conn.execute("SELECT code_hash, expires_at, tries FROM signup_codes WHERE email = ?", (email,)).fetchone()
    if row is None:
        raise SignupError("인증번호를 먼저 받아 주세요.")
    if row["tries"] >= MAX_TRIES or _now() > datetime.fromisoformat(row["expires_at"]):
        raise SignupError("인증번호가 만료됐어요. 다시 받아 주세요.")
    if not hmac.compare_digest(row["code_hash"], _hash(email, code.strip())):
        conn.execute("UPDATE signup_codes SET tries = tries + 1 WHERE email = ?", (email,))
        conn.commit()
        left = MAX_TRIES - row["tries"] - 1
        raise SignupError(f"인증번호가 맞지 않아요. {left}번 더 시도할 수 있어요." if left else "인증번호가 만료됐어요. 다시 받아 주세요.")
    try:
        auth.signup(conn, email, password)
    except auth.SignupError as e:
        raise SignupError(str(e))
    conn.execute("DELETE FROM signup_codes WHERE email = ?", (email,))
    conn.commit()
    return email
