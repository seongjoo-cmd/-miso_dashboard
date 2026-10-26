"""동기화 폴더의 녹취 파일을 읽어 통화 기록으로 적재한다.

폴더 구조:  SYNC_ROOT/<에이전트 이름>/.../<녹취 파일>
- 최상위 폴더 이름 = 에이전트 (처음 보는 이름이면 에이전트를 자동 등록)
- 파일 이름 = 고객 전화번호 + 통화 시작 일시
- 통화시간 = 오디오 헤더의 재생 길이(없으면 파일 크기로 추정)

개인정보: 중복 제외 집계·검색용 HMAC 키, 표시용 마스킹 값 '010-****-5678',
관리자 화면용 전체 번호 '010-1234-5678'을 저장한다. 관리자가 아니면 API 가 마스킹 값만 준다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .audio import AUDIO_EXTS, duration_sec
from .db import get_setting, get_setting_int, insert_returning_id, set_setting

# 에이전트 폴더 안 이 이름의 폴더에 있는 녹취 = 계약 완료 건
CONTRACT_DIR = "계약완료"


def is_contract_dir(name: str) -> bool:
    return unicodedata.normalize("NFC", name.strip()) == CONTRACT_DIR


# 동기화 중(아직 쓰는 중)인 파일을 건드리지 않도록, 최근 수정된 파일은 다음 회차로 미룬다.
MIN_FILE_AGE_SEC = 60

# 일시: 20261012_103015 / 2026-10-12 10-30-15 / 20261012103015 / 261012_103015
_DT_PATTERNS = [
    re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T.]?(\d{2})[-_:.h]?(\d{2})[-_:.m]?(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})[-_ ](\d{2})(\d{2})(\d{2})(?!\d)"),
]
# 전화번호: 010-1234-5678, 01012345678, 02-123-4567, +82 10 1234 5678
_PHONE = re.compile(r"(?<![\d+])(?:\+?82[-. ]?)?(0?1[016789]|0\d{1,2})[-. ]?(\d{3,4})[-. ]?(\d{4})(?!\d)")
# 전국 대표번호 8자리: 1588-1234, 16004112
_REP_PHONE = re.compile(r"(?<![\d+])(1[5-9]\d{2})[-. ]?(\d{4})(?!\d)")


@dataclass
class Parsed:
    started_at: datetime
    phone: str | None  # 숫자만, 국내 형식(0으로 시작)
    masked: str | None  # 표시용 '010-****-5678'
    full: str | None = None  # 관리자용 '010-1234-5678'


def parse_filename(name: str) -> Parsed | None:
    stem = Path(name).stem
    started = None
    for pat in _DT_PATTERNS:
        for m in pat.finditer(stem):
            y, mo, d, h, mi, s = (int(g) for g in m.groups())
            if y < 100:
                y += 2000
            try:
                started = datetime(y, mo, d, h, mi, s)
            except ValueError:
                continue
            stem = stem[:m.start()] + " " + stem[m.end():]  # 일시 숫자가 전화번호로 잡히지 않게 제거
            break
        if started:
            break
    if started is None:
        return None
    m = _PHONE.search(stem)
    if not m:
        r = _REP_PHONE.search(stem)
        if r:
            return Parsed(started, r.group(1) + r.group(2), f"{r.group(1)}-****", f"{r.group(1)}-{r.group(2)}")
        return Parsed(started, None, None)
    area, mid, last = m.groups()
    if not area.startswith("0"):  # +82 10 ... → 010
        area = "0" + area
    return Parsed(started, area + mid + last, f"{area}-****-{last}", f"{area}-{mid}-{last}")


def hash_key(conn: sqlite3.Connection) -> bytes:
    """DB별 비밀키(전화번호·파일 경로 HMAC 용). 한 번 읽어서 재사용한다."""
    return get_setting(conn, "customer_hash_key").encode()


def _hmac(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]


def customer_key(key: bytes, digits: str) -> str:
    return _hmac(key, "phone:" + digits)


def source_key(key: bytes, source: str) -> str:
    """중복 적재 방지 키. 폴더 파일은 '<에이전트>/<경로>', 드라이브 파일은 'drive:<파일 ID>'."""
    return _hmac(key, "file:" + source)


def _insert_call(conn, hk: bytes, agent_id: int, p: Parsed, sec: float, how: str,
                 src_key: str, size: int | None, drive_file_id: str | None = None,
                 uploaded_at: str | None = None, contracted: bool = False) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO calls(agent_id, customer_key, customer_masked, customer_phone, started_at,
                                    duration_sec, duration_source, source_key, file_bytes, drive_file_id,
                                    uploaded_at, contracted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (agent_id, customer_key(hk, p.phone) if p.phone else None, p.masked, p.full,
         p.started_at.isoformat(sep=" "), round(sec), how, src_key, size, drive_file_id, uploaded_at,
         int(contracted)),
    )


def local_time(iso: str | None) -> str | None:
    """드라이브 createdTime('2026-10-13T00:12:33.000Z') → 로컬 시각 'YYYY-MM-DD HH:MM:SS'."""
    if not iso or not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat(sep=" ", timespec="seconds")


def _mtime_local(f: Path) -> str:
    return datetime.fromtimestamp(f.stat().st_mtime).isoformat(sep=" ", timespec="seconds")


def mask_name(name: str) -> str:
    """로그·화면에 보이는 파일 이름에서 전화번호 가운데를 가린다."""
    name = _PHONE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", name)
    return _REP_PHONE.sub(lambda m: f"{m.group(1)}-****", name)


def _agent_for_folder(conn: sqlite3.Connection, folder: str) -> int:
    row = conn.execute("SELECT id FROM agents WHERE sync_dir = ? OR (sync_dir IS NULL AND name = ?)",
                       (folder, folder)).fetchone()
    if row:
        return row["id"]
    return insert_returning_id(conn, "INSERT INTO agents(name, sync_dir) VALUES (?, ?)", (folder, folder))


def stable_files(folder: Path, now: float | None = None) -> tuple[list[Path], int]:
    """(최근 MIN_FILE_AGE_SEC 이내에 수정되지 않은 파일, 미룬 파일 수)."""
    now = time.time() if now is None else now
    ready, recent = [], 0
    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        if now - f.stat().st_mtime < MIN_FILE_AGE_SEC:
            recent += 1
        else:
            ready.append(f)
    return ready, recent


_lock = threading.Lock()  # 자동 적재·수동 적재·백업 전 적재가 겹치지 않게


def ingest_folder(conn: sqlite3.Connection, folder: Path, now: float | None = None) -> dict:
    """에이전트 폴더 하나를 적재. 이미 적재된 파일(source_key)은 건너뛴다."""
    with _lock:
        return _ingest_folder(conn, folder, now)


def _ingest_folder(conn: sqlite3.Connection, folder: Path, now: float | None) -> dict:
    agent_id = _agent_for_folder(conn, folder.name)
    kbps = get_setting_int(conn, "fallback_kbps")
    files, recent = stable_files(folder, now)
    hk = hash_key(conn)
    known = {r[0] for r in conn.execute("SELECT source_key FROM calls WHERE agent_id = ?", (agent_id,))}
    stats = {"added": 0, "already": 0, "recent": recent, "not_audio": 0, "failed": []}
    for f in files:
        if f.suffix.lower() not in AUDIO_EXTS:
            stats["not_audio"] += 1
            continue
        rel = f"{folder.name}/{f.relative_to(folder).as_posix()}"
        key = source_key(hk, rel)
        if key in known:
            stats["already"] += 1
            continue
        p = parse_filename(f.name)
        if p is None:
            stats["failed"].append(mask_name(rel))
            continue
        contracted = any(is_contract_dir(d) for d in f.relative_to(folder).parts[:-1])
        if contracted and _move_into_contract(conn, hk, agent_id, p, f.stat().st_size, key):
            stats["already"] += 1
            continue
        sec, how = duration_sec(f, kbps)
        _insert_call(conn, hk, agent_id, p, sec, how, key, f.stat().st_size, uploaded_at=_mtime_local(f),
                     contracted=contracted)
        stats["added"] += 1
    conn.commit()
    return stats


def _move_into_contract(conn, hk: bytes, agent_id: int, p: Parsed, size: int, key: str) -> bool:
    """폴더 방식: 이미 적재된 녹취를 '계약완료' 폴더로 옮긴 것 → 새로 넣지 않고 그 통화에 표시만."""
    row = conn.execute(
        "SELECT id FROM calls WHERE agent_id = ? AND started_at = ? AND file_bytes = ? "
        "AND customer_key IS NOT DISTINCT FROM ? AND contracted = 0 LIMIT 1",
        (agent_id, p.started_at.isoformat(sep=" "), size, customer_key(hk, p.phone) if p.phone else None),
    ).fetchone()
    if row:
        conn.execute("UPDATE calls SET contracted = 1, source_key = ? WHERE id = ?", (key, row["id"]))
    return bool(row)


def ingest_all(conn: sqlite3.Connection, sync_root: Path, now: float | None = None) -> dict:
    total = {"added": 0, "already": 0, "recent": 0, "not_audio": 0, "failed": [], "agents": 0}
    if sync_root.is_dir():
        for folder in sorted(p for p in sync_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            s = ingest_folder(conn, folder, now)
            total["agents"] += 1
            for k in ("added", "already", "recent", "not_audio"):
                total[k] += s[k]
            total["failed"] += s["failed"]
    record_result(conn, total)
    return total


# ---------- 구글 드라이브(Apps Script 가 보내 주는 파일 목록) ----------
MAX_RECORDS = 500


def register_folders(conn: sqlite3.Connection, names: list) -> None:
    """드라이브의 에이전트 폴더 목록 — 녹취가 하나도 없는 폴더의 에이전트도 '안 올림'으로 보이게 등록."""
    with _lock:
        for n in names[:200]:
            n = str(n or "").strip()
            if n and not n.startswith("_"):
                _agent_for_folder(conn, n)
        conn.commit()


def ingest_records(conn: sqlite3.Connection, records: list[dict], first: bool = True) -> dict:
    """드라이브 파일 메타데이터로 통화 기록 적재.

    record: {"id": 드라이브 파일 ID, "agent": 최상위 폴더(에이전트) 이름, "name": 파일 이름,
             "size": 바이트, "durationMs": 재생 길이(ms, 모르면 null), "created": 드라이브에 올라온 시각}
    first=False 면 이번 동기화의 이어지는 묶음이라 마지막 결과에 합산한다.
    """
    with _lock:
        hk = hash_key(conn)
        kbps = get_setting_int(conn, "fallback_kbps")
        stats = {"added": 0, "already": 0, "recent": 0, "not_audio": 0, "failed": []}
        agents: dict[str, tuple[int, set[str]]] = {}
        for r in records:
            name = str(r.get("name") or "")
            agent = str(r.get("agent") or "").strip()
            if not agent or not r.get("id"):
                continue
            if Path(name).suffix.lower() not in AUDIO_EXTS:
                stats["not_audio"] += 1
                continue
            if agent not in agents:
                aid = _agent_for_folder(conn, agent)
                known = {x[0] for x in conn.execute("SELECT source_key FROM calls WHERE agent_id = ?", (aid,))}
                agents[agent] = (aid, known)
            aid, known = agents[agent]
            key = source_key(hk, f"drive:{r['id']}")
            uploaded = local_time(r.get("created"))
            if key in known:
                stats["already"] += 1
                if r.get("contract"):
                    conn.execute("UPDATE calls SET contracted = 1 WHERE source_key = ?", (key,))
                p = parse_filename(name)
                if p and p.full:  # 전체 번호 저장 전에 들어온 녹취: 다시 받을 때 채운다
                    conn.execute("UPDATE calls SET customer_phone = ? WHERE source_key = ? AND customer_phone IS NULL",
                                 (p.full, key))
                if uploaded:  # 이 기능 전에 들어온 녹취: 다시 받을 때 올라온 시각을 채운다
                    conn.execute("UPDATE calls SET uploaded_at = ? WHERE source_key = ? AND uploaded_at IS NULL",
                                 (uploaded, key))
                continue
            p = parse_filename(name)
            if p is None:
                stats["failed"].append(mask_name(f"{agent}/{name}"))
                continue
            size = int(r.get("size") or 0) or None
            ms = r.get("durationMs")
            if isinstance(ms, (int, float)) and ms >= 0:
                sec, how = ms / 1000, "header"
            else:
                sec, how = (size or 0) * 8 / (max(kbps, 1) * 1000), "size"
            _insert_call(conn, hk, aid, p, sec, how, key, size, str(r["id"]), uploaded, bool(r.get("contract")))
            known.add(key)
            stats["added"] += 1
        stats["agents"] = len(agents)
        conn.commit()
    if not first:
        prev = last_result(conn) or {}
        for k in ("added", "already", "recent", "not_audio"):
            stats[k] += prev.get(k, 0)
        stats["agents"] = max(stats["agents"], prev.get("agents", 0))
        stats["failed_total"] = prev.get("failed_count", 0) + len(stats["failed"])
        stats["failed"] = prev.get("failed", []) + stats["failed"]  # 화면에는 앞의 20개만
    record_result(conn, stats)
    return stats


MAX_CONTRACTS = 5000


def mark_contracts(conn: sqlite3.Connection, file_ids: list) -> int:
    """드라이브 '계약완료' 폴더에 있는 파일 ID 목록 → 이미 적재된 통화에 계약 완료 표시.

    드라이브는 파일을 옮겨도 생성 시각이 그대로라, 옮긴 녹취는 스크립트가 따로 이 목록으로 알려 준다.
    목록에서 빠졌다고 표시를 지우지는 않는다(드라이브가 여러 개면 나눠 오고, 백업으로 옮겨도 계약은 그대로).
    """
    ids = [i for i in file_ids if isinstance(i, str) and i]
    n = 0
    with _lock:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            cur = conn.execute(
                "UPDATE calls SET contracted = 1 WHERE contracted = 0 AND drive_file_id IN "
                f"({','.join('?' * len(chunk))})", chunk)
            n += max(cur.rowcount, 0)
        conn.commit()
    return n


def record_result(conn: sqlite3.Connection, stats: dict) -> None:
    failed_count = stats.pop("failed_total", None)
    summary = {**stats, "failed_count": failed_count if failed_count is not None else len(stats["failed"]),
               "failed": stats["failed"][:20],
               "at": datetime.now().isoformat(sep=" ", timespec="seconds")}
    set_setting(conn, "last_ingest", json.dumps(summary, ensure_ascii=False))
    conn.commit()


def last_result(conn: sqlite3.Connection) -> dict | None:
    raw = get_setting(conn, "last_ingest")
    return json.loads(raw) if raw else None
