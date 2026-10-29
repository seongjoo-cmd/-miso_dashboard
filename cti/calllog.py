"""휴대폰 통화 기록·문자 기록(SMS Backup & Restore 의 calls-*.xml, sms-*.xml)을 적재하고,
녹취의 통화시간을 통화 기록의 정확한 값으로 채운다. 문자는 내용 없이 상대 번호·시각·방향만 저장한다.

Apps Script 가 XML 을 읽어 통화마다 [번호, 시작(ms), 통화초, 유형] 으로 보낸다.
녹취 파일 이름의 시각은 통화가 연결된 때라 기록의 시작 시각보다 신호 가는 시간만큼 늦다.
→ 같은 에이전트·같은 번호에서 녹취 시각 MATCH_BEFORE_SEC 전 ~ MATCH_AFTER_SEC 후 사이의 가장 가까운 기록을 짝으로 본다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import ingest

MAX_ENTRIES = 20000
MATCH_BEFORE_SEC = 120
MATCH_AFTER_SEC = 10


def _ts(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="seconds")


def known_files(conn, file_ids: list) -> list[str]:
    ids = [i for i in file_ids if isinstance(i, str) and i][:ingest.MAX_RECORDS]
    if not ids:
        return []
    return [r[0] for r in conn.execute(
        f"SELECT file_id FROM call_log_files WHERE file_id IN ({','.join('?' * len(ids))})", ids)]


def _local(ms) -> datetime:
    ms = int(ms)
    return datetime.fromtimestamp(ms if ms < 10**11 else ms / 1000)  # 초 단위로 온 값도 받기


def _phone(hk: bytes, number: str) -> tuple[str, str | None, str | None]:
    ph = ingest.parse_phone(number)
    return (ingest.customer_key(hk, ph[0]), ph[1], ph[2]) if ph else ("", None, number[:40] or None)


def ingest_messages(conn, file_id: str, agent: str, entries: list) -> dict:
    """entries: [[상대 번호, 시각 epoch ms, 1 받음·2 보냄, 'sms'|'mms'], ...] → 새로 넣은 문자 수."""
    with ingest._lock:
        hk = ingest.hash_key(conn)
        aid = ingest._agent_for_folder(conn, agent)
        rows = []
        for e in entries:
            try:
                number, at, box, kind = str(e[0] or ""), _local(e[1]), int(e[2] or 0), str(e[3])
            except (TypeError, ValueError, IndexError, OverflowError, OSError):
                continue
            if box in (1, 2) and kind in ("sms", "mms"):  # 임시저장·보내는 중 등은 뺌
                rows.append((aid, *_phone(hk, number), _ts(at), box, kind))
        before = conn.execute("SELECT COUNT(*) FROM messages WHERE agent_id = ?", (aid,)).fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO messages(agent_id, customer_key, customer_masked, customer_phone, sent_at, box, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        added = conn.execute("SELECT COUNT(*) FROM messages WHERE agent_id = ?", (aid,)).fetchone()[0] - before
        conn.execute("INSERT OR IGNORE INTO call_log_files(file_id, agent_id, read_at, entries) VALUES (?, ?, ?, ?)",
                     (file_id, aid, _ts(datetime.now()), len(rows)))
        conn.commit()
    return {"added": added}


def ingest_file(conn, file_id: str, agent: str, entries: list) -> dict:
    """entries: [[번호, 시작 epoch ms, 통화초, 유형], ...] → 새로 넣은 기록 수·통화시간을 채운 녹취 수."""
    with ingest._lock:
        hk = ingest.hash_key(conn)
        aid = ingest._agent_for_folder(conn, agent)
        rows = []
        for e in entries:
            try:
                number, started, sec, typ = str(e[0] or ""), _local(e[1]), int(e[2] or 0), int(e[3] or 0)
            except (TypeError, ValueError, IndexError, OverflowError, OSError):
                continue
            rows.append((aid, *_phone(hk, number), _ts(started), max(sec, 0), typ))
        before = _count(conn, aid)
        conn.executemany(
            "INSERT OR IGNORE INTO call_log(agent_id, customer_key, customer_masked, customer_phone, started_at, "
            "duration_sec, call_type) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        added = _count(conn, aid) - before
        conn.execute("INSERT OR IGNORE INTO call_log_files(file_id, agent_id, read_at, entries) VALUES (?, ?, ?, ?)",
                     (file_id, aid, _ts(datetime.now()), len(rows)))
        times = sorted(r[4] for r in rows)
        filled = fill_durations(conn, aid, times[0], times[-1]) if times else 0
        conn.commit()
    return {"added": added, "filled": filled}


def _count(conn, aid: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM call_log WHERE agent_id = ?", (aid,)).fetchone()[0]


def fill_durations(conn, agent_id: int, lo: str, hi: str) -> int:
    """lo~hi 사이 녹취 중 아직 기록 값이 아닌 것 → 짝이 되는 통화 기록의 통화시간으로. 바꾼 녹취 수."""
    pad = timedelta(seconds=MATCH_BEFORE_SEC + MATCH_AFTER_SEC)
    lo_dt, hi_dt = datetime.fromisoformat(lo) - pad, datetime.fromisoformat(hi) + pad
    log: dict[str, list[tuple[datetime, int]]] = {}
    for r in conn.execute(
            "SELECT customer_key, started_at, duration_sec FROM call_log WHERE agent_id = ? AND customer_key <> '' "
            "AND duration_sec > 0 AND started_at BETWEEN ? AND ?", (agent_id, _ts(lo_dt), _ts(hi_dt))):
        log.setdefault(r[0], []).append((datetime.fromisoformat(r[1]), r[2]))
    if not log:
        return 0
    updates = []
    for r in conn.execute(
            "SELECT id, customer_key, started_at FROM calls WHERE agent_id = ? AND customer_key IS NOT NULL "
            "AND (duration_source IS NULL OR duration_source <> 'calllog') AND started_at BETWEEN ? AND ?",
            (agent_id, _ts(lo_dt), _ts(hi_dt))):
        t = datetime.fromisoformat(r[2])
        near = [(abs((t - d).total_seconds()), sec) for d, sec in log.get(r[1], ())
                if -MATCH_AFTER_SEC <= (t - d).total_seconds() <= MATCH_BEFORE_SEC]
        if near:
            updates.append((min(near)[1], r[0]))
    conn.executemany("UPDATE calls SET duration_sec = ?, duration_source = 'calllog' WHERE id = ?", updates)
    return len(updates)
