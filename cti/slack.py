"""슬랙 봇으로 '녹취 안 올린 상담원' 알림 보내기.

봇 토큰(xoxb-…)은 환경 변수 SLACK_BOT_TOKEN 이 있으면 그것을, 없으면 설정 화면에서 넣은 값을 쓴다.
받는 곳(slack_target):
- 이메일(name@company.com) → users.lookupByEmail 로 찾아서 봇과의 DM 으로 보냄
- 사용자 ID(U…/W…) → DM, 채널 ID(C…/G…) 또는 #채널이름 → 채널
봇 권한(OAuth scope): chat:write, users:read, users:read.email
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from .db import get_setting, set_setting

API = "https://slack.com/api/"
TOKEN_RE = re.compile(r"xoxb-[A-Za-z0-9-]{10,}")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
TARGET_RE = re.compile(r"(#[a-z0-9_\-가-힣]{1,80}|[UWCG][A-Z0-9]{6,})")
DOW = "월화수목금토일"


class SlackError(Exception):
    pass


# 슬랙 오류 코드 → 사람이 읽는 말
_ERRORS = {
    "invalid_auth": "봇 토큰이 맞지 않아요. xoxb- 로 시작하는 Bot User OAuth Token 을 다시 넣어 주세요.",
    "not_authed": "봇 토큰이 없어요.",
    "account_inactive": "봇 토큰이 더 이상 쓰이지 않아요(앱이 지워졌거나 다시 설치됨).",
    "users_not_found": "그 이메일의 슬랙 사용자를 찾지 못했어요.",
    "missing_scope": "봇 권한이 부족해요. chat:write, users:read, users:read.email 을 추가하고 앱을 다시 설치해 주세요.",
    "channel_not_found": "받는 곳(채널·사용자)을 찾지 못했어요.",
    "not_in_channel": "봇이 그 채널에 없어요. 채널에서 /invite 로 봇을 먼저 초대해 주세요.",
}


def token(conn) -> tuple[str | None, str | None]:
    """(토큰, 출처 'env'|'db')"""
    env = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if env:
        return env, "env"
    t = get_setting(conn, "slack_bot_token")
    return (t, "db") if t else (None, None)


def config(conn) -> dict:
    """화면용 — 토큰 값은 절대 내보내지 않는다."""
    _, src = token(conn)
    return {"token_set": bool(src), "token_source": src, "target": get_setting(conn, "slack_target") or ""}


def save_config(conn, body: dict) -> dict:
    if "token" in body:
        t = str(body.get("token") or "").strip()
        if t and not TOKEN_RE.fullmatch(t):
            raise ValueError("봇 토큰은 xoxb- 로 시작해요(Bot User OAuth Token).")
        if t:
            set_setting(conn, "slack_bot_token", t)
        elif body.get("clear_token"):
            conn.execute("DELETE FROM settings WHERE key = 'slack_bot_token'")
    if "target" in body:
        target = str(body.get("target") or "").strip()
        if target and not (EMAIL_RE.fullmatch(target) or TARGET_RE.fullmatch(target)):
            raise ValueError("받는 곳은 슬랙 이메일, 사용자 ID(U…), 채널 ID(C…) 또는 #채널이름으로 넣어 주세요.")
        set_setting(conn, "slack_target", target)
    conn.commit()
    return config(conn)


def _call(tok: str, method: str, params: dict, post: bool = False) -> dict:
    headers = {"Authorization": f"Bearer {tok}"}
    if post:
        req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                     headers={**headers, "Content-Type": "application/json; charset=utf-8"})
    else:
        req = urllib.request.Request(f"{API}{method}?{urllib.parse.urlencode(params)}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SlackError(f"슬랙에 연결하지 못했어요: {e}")
    if not data.get("ok"):
        code = data.get("error", "unknown")
        raise SlackError(_ERRORS.get(code, f"슬랙 오류: {code}"))
    return data


def resolve_target(tok: str, target: str) -> str:
    if EMAIL_RE.fullmatch(target):
        return _call(tok, "users.lookupByEmail", {"email": target})["user"]["id"]
    return target


def _when(iso: str | None, today: date) -> str:
    if not iso:
        return "올린 적 없음"
    d = datetime.fromisoformat(iso)
    days = (today - d.date()).days
    label = "어제" if days == 1 else f"{d.month}월 {d.day}일({DOW[d.weekday()]})"
    return f"마지막 업로드 {label} {d:%H:%M}"


def missing_message(agents: list[dict], today: date, now: datetime, url: str | None) -> tuple[str, list]:
    """(알림 미리보기 텍스트, Block Kit 블록)"""
    missing = [a for a in agents if a["missing"]]
    head = f"{today.month}월 {today.day}일({DOW[today.weekday()]}) {now:%H:%M} 기준"
    if missing:
        title = f":warning: 오늘 녹취를 아직 안 올린 상담원 {len(missing)}명"
        lines = "\n".join(f"• *{a['name']}* — {_when(a['last_upload'], today)}" for a in missing)
    else:
        title = ":white_check_mark: 오늘 모든 상담원이 녹취를 올렸어요"
        lines = "\n".join(f"• {a['name']} — 오늘 {a['today']}개" for a in agents) or "상담원이 없어요"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{head}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": lines}},
    ]
    if url:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{url}|통화 대시보드에서 보기>"}]})
    return f"{title} ({head})", blocks


def send(conn, text: str, blocks: list) -> dict:
    tok, _ = token(conn)
    if not tok:
        raise SlackError("설정에서 슬랙 봇 토큰을 먼저 넣어 주세요.")
    target = get_setting(conn, "slack_target")
    if not target:
        raise SlackError("설정에서 받는 곳(슬랙 이메일이나 채널)을 먼저 넣어 주세요.")
    channel = resolve_target(tok, target)
    _call(tok, "chat.postMessage", {"channel": channel, "text": text, "blocks": blocks, "unfurl_links": False}, post=True)
    return {"sent_to": target}
