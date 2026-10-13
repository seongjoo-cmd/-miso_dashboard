"""데모 데이터(가짜 에이전트·통화) 생성 — 화면 확인용."""
from __future__ import annotations

import os
import random
import sqlite3
import struct
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import auth, ingest

AGENTS = [("김민지", "1001"), ("이서준", "1002"), ("박지우", "1003"),
          ("최하은", "1004"), ("정도윤", "1005"), ("강수아", "1006")]
HOUR_W = [0, 0, 0, 0, 0, 0, 0, 0.2, 0.6, 1.0, 1.1, 0.9, 0.4, 0.8, 1.0, 1.0, 0.9, 0.6, 0.2, 0.1, 0, 0, 0, 0]
FILES_PER_AGENT = 4


def write_wav(path: Path, seconds: int, rnd: random.Random, rate: int = 4000) -> None:
    """8비트 모노 무음 WAV (데모용 — 작은 용량으로 실제 길이를 갖도록 낮은 샘플레이트)."""
    data = bytes([128]) * (seconds * rate)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8)
    header += b"data" + struct.pack("<I", len(data))
    path.write_bytes(header + data)


def call_duration(rnd: random.Random) -> int:
    return rnd.randint(1, 9) if rnd.random() < 0.12 else int(rnd.lognormvariate(5.0, 0.6))


def seed(conn: sqlite3.Connection, sync_root: Path | None = None, days: int = 70) -> dict:
    """가짜 통화를 만든다. sync_root 를 주면 오늘 통화 일부를 실제 녹취 파일(WAV)로 만들어 적재 과정을 거친다."""
    rnd = random.Random(42)

    for name, ext in AGENTS:
        conn.execute("INSERT OR IGNORE INTO agents(name, extension, sync_dir) VALUES (?, ?, ?)", (name, ext, name))
    agents = {r["sync_dir"]: r["id"] for r in conn.execute("SELECT id, sync_dir FROM agents ORDER BY id")}
    skill = {a: rnd.uniform(0.7, 1.3) for a in agents}
    phones = [f"010{rnd.randint(10000000, 99999999)}" for _ in range(900)]
    hk = ingest.hash_key(conn)

    # 1) 과거 통화(오늘 파일로 만들 몫은 제외)
    conn.execute("DELETE FROM calls")
    rows = []
    now = datetime.now()
    today = date.today()
    file_calls: dict[str, list[tuple[datetime, str, int]]] = {a: [] for a in agents}
    for back in range(days, -1, -1):
        d = today - timedelta(days=back)
        weekend = d.weekday() >= 5
        for folder, aid in agents.items():
            if weekend and rnd.random() < 0.8:
                continue
            n = int(rnd.gauss(38, 8) * skill[folder] * (0.3 if weekend else 1))
            day_calls = []
            for _ in range(max(n, 0)):
                h = rnd.choices(range(24), weights=HOUR_W)[0]
                ts = datetime(d.year, d.month, d.day, h, rnd.randint(0, 59), rnd.randint(0, 59))
                if ts > now - timedelta(minutes=5):
                    continue
                day_calls.append((ts, rnd.choice(phones), call_duration(rnd)))
            day_calls.sort()
            if d == today and sync_root is not None:  # 오늘의 마지막 몇 건은 녹취 파일로 → 적재 과정을 거침
                file_calls[folder] = day_calls[-FILES_PER_AGENT:]
                day_calls = day_calls[:-FILES_PER_AGENT]
            for ts, phone, dur in day_calls:
                rows.append((aid, ingest.customer_key(hk, phone), f"{phone[:3]}-****-{phone[-4:]}",
                             ts.isoformat(sep=" "), dur))
    conn.executemany(
        "INSERT INTO calls(agent_id, customer_key, customer_masked, started_at, duration_sec, duration_source, "
        "drive_file_id) VALUES (?, ?, ?, ?, ?, 'header', 'demo')", rows)
    conn.commit()

    if sync_root is None:
        return {"agents": len(agents), "calls": len(rows), "files": 0}

    # 2) 오늘 녹취 파일 생성: '통화 녹음 010-1234-5678_261012_103015.wav'
    old = time.time() - 3600  # 동기화 완료된 파일처럼 수정 시각을 과거로
    n_files = 0
    for folder, calls in file_calls.items():
        dirpath = sync_root / folder
        dirpath.mkdir(parents=True, exist_ok=True)
        for ts, phone, dur in calls:
            f = dirpath / f"통화 녹음 {phone[:3]}-{phone[3:7]}-{phone[7:]}_{ts:%y%m%d_%H%M%S}.wav"
            write_wav(f, dur, rnd)
            os.utime(f, (old, old))
            n_files += 1
    stats = ingest.ingest_all(conn, sync_root)
    conn.execute("UPDATE calls SET drive_file_id = 'demo' WHERE drive_file_id IS NULL")  # 데모는 모두 견본 소리로 재생
    conn.commit()
    return {"agents": len(agents), "calls": len(rows) + stats["added"], "files": n_files}



def create_demo_users(conn: sqlite3.Connection) -> None:
    auth.create_user(conn, "admin", "admin1234", "admin")
    auth.create_user(conn, "viewer", "viewer1234", "viewer")
