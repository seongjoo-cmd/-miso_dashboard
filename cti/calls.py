"""녹취(통화) 목록 조회·검색.

검색어 규칙
- 숫자 8자리 이상: 전체 번호 → 같은 방식으로 HMAC 해서 customer_key 와 비교(원문 번호는 DB에 없음)
- 숫자 4자리: 뒤 4자리 → 마스킹 번호 끝자리와 비교
- 그 외 글자: 에이전트 이름
조회 조건(날짜·시간대·에이전트·번호)은 모두 함께 적용한다(주문 조회처럼).
'이 고객 통화 모두 보기'(same_as)만 날짜와 관계없이 전체에서 찾는다.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .ingest import customer_key, hash_key
from .periods import Period

PAGE = 50
DEMO_AUDIO = "/static/demo-call.wav"


class SearchError(ValueError):
    pass


def _phone_digits(q: str) -> str:
    d = re.sub(r"\D", "", q)
    if d.startswith("82") and len(d) >= 11:  # +82 10 … → 010…
        d = "0" + d[2:]
    return d


def shown_phone(r, full_phone: bool) -> str | None:
    """관리자(full_phone)에게는 전체 번호, 그 외에는 가운데를 가린 번호."""
    return (full_phone and r["customer_phone"]) or r["customer_masked"]


def search(conn, period: Period | None, agent_id: int | None = None, q: str | None = None,
           same_as: int | None = None, min_sec: int | None = None, cursor: str | None = None,
           limit: int = PAGE, hour_from: int | None = None, hour_to: int | None = None,
           contract_only: bool = False, full_phone: bool = False) -> dict:
    """hour_from 이상 hour_to 미만 시각(0~24시)에 시작한 통화."""
    where, args = [], []
    by_customer = False
    q = (q or "").strip()

    if same_as is not None:
        row = conn.execute("SELECT customer_key, customer_masked FROM calls WHERE id = ?", (same_as,)).fetchone()
        if row is None or not row["customer_key"]:
            raise SearchError("이 통화는 고객번호가 없어서 모아 볼 수 없어요.")
        where.append("c.customer_key = ?"); args.append(row["customer_key"])
        by_customer = True
    elif q:
        digits = _phone_digits(q)
        if digits and not re.search(r"[^\d\s\-+().]", q):
            if len(digits) >= 8:
                where.append("c.customer_key = ?"); args.append(customer_key(hash_key(conn), digits))
                by_customer = True
            elif len(digits) == 4:
                where.append("c.customer_masked LIKE ?"); args.append(f"%-{digits}")
                by_customer = True
            else:
                raise SearchError("전체 번호나 뒤 4자리로 찾아 주세요.")
        else:
            where.append("a.name LIKE ?"); args.append(f"%{q}%")

    if period is not None and same_as is None:
        where.append("c.started_at >= ? AND c.started_at < ?")
        args += [period.start.isoformat(), (period.end + timedelta(days=1)).isoformat()]
    if agent_id is not None:
        where.append("c.agent_id = ?"); args.append(agent_id)
    if hour_from is not None or hour_to is not None:
        lo, hi = hour_from or 0, 24 if hour_to is None else hour_to
        if not (0 <= lo < hi <= 24):
            raise SearchError("시간대를 다시 골라 주세요.")
        where.append("substr(c.started_at, 12, 2) >= ? AND substr(c.started_at, 12, 2) < ?")
        args += [f"{lo:02d}", f"{hi:02d}"]
    if contract_only:
        where.append("c.contracted = 1")
    if min_sec:
        where.append("c.duration_sec >= ?"); args.append(min_sec)

    base_where = " AND ".join(where) or "1=1"
    total = conn.execute(
        f"SELECT COUNT(*) FROM calls c JOIN agents a ON a.id = c.agent_id WHERE {base_where}", args
    ).fetchone()[0]

    page_where, page_args = base_where, list(args)
    if cursor:
        try:
            cur_at, cur_id = cursor.rsplit("|", 1)
            cur_id = int(cur_id)
        except ValueError:
            raise SearchError("잘못된 페이지 정보예요.")
        page_where += " AND (c.started_at < ? OR (c.started_at = ? AND c.id < ?))"
        page_args += [cur_at, cur_at, cur_id]

    rows = conn.execute(
        f"""
        SELECT c.id, c.started_at, c.agent_id, a.name AS agent_name, c.customer_masked, c.customer_phone,
               c.duration_sec, c.drive_file_id, c.contracted
        FROM calls c JOIN agents a ON a.id = c.agent_id
        WHERE {page_where}
        ORDER BY c.started_at DESC, c.id DESC
        LIMIT ?
        """,
        [*page_args, limit + 1],
    ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "id": r["id"], "started_at": r["started_at"], "agent_id": r["agent_id"], "agent_name": r["agent_name"],
        "customer_masked": shown_phone(r, full_phone), "duration_sec": r["duration_sec"],
        "playable": bool(r["drive_file_id"]), "contracted": bool(r["contracted"]),
    } for r in rows]
    return {
        "items": items,
        "total": total,
        "by_customer": by_customer,
        "next_cursor": f"{rows[-1]['started_at']}|{rows[-1]['id']}" if more else None,
    }


def detail(conn, call_id: int, full_phone: bool = False) -> dict | None:
    r = conn.execute(
        """
        SELECT c.id, c.started_at, c.agent_id, a.name AS agent_name, a.extension, c.customer_masked,
               c.customer_phone, c.customer_key, c.duration_sec, c.duration_source, c.drive_file_id, c.contracted
        FROM calls c JOIN agents a ON a.id = c.agent_id WHERE c.id = ?
        """,
        (call_id,),
    ).fetchone()
    if r is None:
        return None
    same = 0
    if r["customer_key"]:
        same = conn.execute("SELECT COUNT(*) FROM calls WHERE customer_key = ?", (r["customer_key"],)).fetchone()[0]
    fid = r["drive_file_id"]
    if fid == "demo":
        play = {"type": "demo", "url": DEMO_AUDIO}
    elif fid:
        play = {"type": "drive",
                "preview_url": f"https://drive.google.com/file/d/{fid}/preview",
                "open_url": f"https://drive.google.com/file/d/{fid}/view"}
    else:
        play = None
    return {
        "id": r["id"], "started_at": r["started_at"], "agent_id": r["agent_id"], "agent_name": r["agent_name"],
        "extension": r["extension"], "customer_masked": shown_phone(r, full_phone), "duration_sec": r["duration_sec"],
        "duration_estimated": r["duration_source"] == "size", "same_customer_calls": same, "play": play,
        "contracted": bool(r["contracted"]),
    }
