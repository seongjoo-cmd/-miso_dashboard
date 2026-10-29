"""드라이브 연결 상태와 에이전트별 녹취 업로드 현황.

- 연결 상태: Apps Script 가 대시보드에 들를 때마다(새 녹취 보내기·백업 요청 확인) 시각을 남기고,
  새 스크립트는 회차가 끝날 때 결과(성공·오류)도 알려 준다. 자동 실행은 10분마다라서
  STALE_MIN 분 넘게 소식이 없으면 멈춘 것으로 본다.
- 업로드 현황: 녹취 파일이 폴더에 올라온 시각(calls.uploaded_at, 없으면 통화 시각)의 최댓값.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .db import get_setting, set_setting

STALE_MIN = 25


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def touch(conn) -> None:
    """Apps Script 가 연동 키로 들렀다."""
    set_setting(conn, "drive_last_seen", _now().isoformat(sep=" "))
    conn.commit()


def record_run(conn, body: dict) -> dict:
    """Apps Script 한 회차의 결과: {ok, added, found, more, error}."""
    run = {
        "at": _now().isoformat(sep=" "),
        "ok": bool(body.get("ok")),
        "added": int(body.get("added") or 0),
        "more": bool(body.get("more")),
        "error": str(body.get("error") or "")[:300] or None,
    }
    set_setting(conn, "drive_last_run", json.dumps(run, ensure_ascii=False))
    if run["ok"]:
        set_setting(conn, "drive_last_ok", run["at"])
    set_setting(conn, "drive_last_seen", run["at"])
    conn.commit()
    return run


def drive_status(conn, now: datetime | None = None) -> dict:
    """state: never(연결 전) | ok | stale(자동 실행이 멈춘 듯) | error(마지막 회차 실패)"""
    now = now or _now()
    seen = get_setting(conn, "drive_last_seen")
    raw = get_setting(conn, "drive_last_run")
    run = json.loads(raw) if raw else None
    if not seen:
        state = "never"
    elif run and not run["ok"]:
        state = "error"
    elif now - datetime.fromisoformat(seen) > timedelta(minutes=STALE_MIN):
        state = "stale"
    else:
        state = "ok"
    return {"state": state, "last_seen": seen, "last_ok": get_setting(conn, "drive_last_ok"),
            "last_run": run, "stale_min": STALE_MIN}


def agent_uploads(conn, today: date) -> list[dict]:
    """활성 에이전트별 마지막 업로드 시각과 오늘 올린 수. 오늘 하나도 없으면 missing."""
    start = today.isoformat() + " 00:00:00"
    rows = conn.execute(
        """
        SELECT a.id AS agent_id, a.name,
               MAX(COALESCE(c.uploaded_at, c.started_at)) AS last_upload,
               SUM(CASE WHEN COALESCE(c.uploaded_at, c.started_at) >= ? THEN 1 ELSE 0 END) AS today
        FROM agents a LEFT JOIN calls c ON c.agent_id = a.id
        WHERE a.active = 1
        GROUP BY a.id, a.name
        ORDER BY a.name
        """, (start,)).fetchall()
    out = []
    for r in rows:
        n = int(r["today"] or 0)
        out.append({"agent_id": r["agent_id"], "name": r["name"], "last_upload": r["last_upload"],
                    "today": n, "missing": n == 0})
    # 안 올린 사람이 위로, 그 안에서는 오래된 순
    out.sort(key=lambda a: (not a["missing"], a["last_upload"] or "", a["name"]))
    return out
