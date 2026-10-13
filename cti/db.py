"""SQLite 스키마와 연결."""
from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    extension TEXT,
    sync_dir  TEXT UNIQUE,      -- SYNC_ROOT 아래 최상위 폴더 이름(비어 있으면 name)
    active    INTEGER NOT NULL DEFAULT 1
);

-- 아웃바운드 전용: 모든 통화는 상담원이 고객에게 건 발신 통화다.
-- 녹취 파일 1개 = 통화 1건. 고객 전화번호 원문은 저장하지 않는다(개인정보 최소화).
CREATE TABLE IF NOT EXISTS calls (
    id               INTEGER PRIMARY KEY,
    agent_id         INTEGER NOT NULL REFERENCES agents(id),
    customer_key     TEXT,      -- 전화번호 HMAC(중복 제외 집계용, 복원 불가)
    customer_masked  TEXT,      -- 표시용 '010-****-5678'
    started_at       TEXT NOT NULL,  -- 로컬 시각 'YYYY-MM-DD HH:MM:SS'
    duration_sec     INTEGER NOT NULL DEFAULT 0,
    duration_source  TEXT,      -- 'header' | 'size'
    source_key       TEXT UNIQUE,    -- '<상담원 폴더>/<파일 경로>'의 HMAC (중복 적재 방지, 파일명에 번호가 있어 원문 미저장)
    file_bytes       INTEGER,
    drive_file_id    TEXT,            -- 구글 드라이브 파일 ID(재생용). 데모 녹취는 'demo'
    archived_at      TEXT,            -- 드라이브 '_백업' 폴더로 옮긴 시각(파일 ID는 그대로라 재생 가능)
    uploaded_at      TEXT             -- 녹취 파일이 폴더에 올라온 시각(드라이브 생성 시각·폴더 파일 수정 시각, 로컬 시각)
);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_agent_started ON calls(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_calls_customer ON calls(customer_key);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'viewer'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL
);

-- 드라이브 백업 요청: 대시보드가 쌓고, Apps Script 가 가져가 처리한 뒤 결과를 알려 준다
CREATE TABLE IF NOT EXISTS drive_tasks (
    id           INTEGER PRIMARY KEY,
    agent_id     INTEGER NOT NULL REFERENCES agents(id),
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    done_at      TEXT,
    moved        INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    message      TEXT
);

CREATE TABLE IF NOT EXISTS backups (
    id           INTEGER PRIMARY KEY,
    agent_id     INTEGER NOT NULL REFERENCES agents(id),
    created_at   TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    file_count   INTEGER NOT NULL,
    total_bytes  INTEGER NOT NULL,
    performed_by TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "min_call_sec": "10",
    # 헤더에서 길이를 못 읽는 형식은 '파일 크기 / 비트레이트'로 통화시간을 추정한다.
    "fallback_kbps": "128",
}


class Row(dict):
    """이름(row["id"])과 순서(row[0]) 둘 다로 꺼낼 수 있는 행 — sqlite3.Row 와 같은 사용법."""
    __slots__ = ("_values",)

    def __init__(self, keys, values):
        super().__init__(zip(keys, values))
        self._values = values

    def __getitem__(self, k):
        return self._values[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _pg_sql(sql: str) -> str:
    """SQLite 문법을 Postgres 로: ? → %s, INSERT OR IGNORE → ON CONFLICT DO NOTHING."""
    sql = sql.replace("%", "%%").replace("?", "%s")
    if re.match(r"\s*INSERT OR IGNORE", sql, re.I):
        sql = re.sub(r"INSERT OR IGNORE", "INSERT", sql, count=1, flags=re.I).rstrip().rstrip(";")
        sql += " ON CONFLICT DO NOTHING"
    return sql


class PgConnection:
    """psycopg 연결을 sqlite3.Connection 처럼 쓰게 하는 얇은 래퍼."""
    is_pg = True

    def __init__(self, url: str):
        import psycopg
        from psycopg.types.numeric import FloatLoader

        def row_factory(cursor):
            keys = [d.name for d in cursor.description] if cursor.description else []
            return lambda values: Row(keys, values)

        self._c = psycopg.connect(url, row_factory=row_factory, prepare_threshold=None)
        self._c.adapters.register_loader("numeric", FloatLoader)  # AVG() 결과를 float 로

    def execute(self, sql: str, params=()):
        return self._c.execute(_pg_sql(sql), tuple(params) or None)

    def executemany(self, sql: str, seq):
        with self._c.cursor() as cur:
            cur.executemany(_pg_sql(sql), list(seq))

    def executescript(self, script: str):
        self._c.execute(script)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()


def _pg_schema() -> str:
    return re.sub(r"(\bid\s+)INTEGER PRIMARY KEY", r"\1BIGSERIAL PRIMARY KEY", SCHEMA)


_pg_ready: set[str] = set()
_pg_ready_lock = threading.Lock()


def is_url(target) -> bool:
    return isinstance(target, str) and target.startswith(("postgres://", "postgresql://"))


def connect(target):
    """target 이 postgres:// URL 이면 Postgres(Vercel 등), 아니면 SQLite 파일 경로."""
    if is_url(target):
        conn = PgConnection(target)
        with _pg_ready_lock:
            if target not in _pg_ready:
                _init(conn, _pg_schema())
                _pg_ready.add(target)
        return conn
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init(conn, SCHEMA)
    return conn


# 이미 만들어진 DB에 나중에 추가된 열: (테이블, 열, 타입)
_ADDED_COLUMNS = [("calls", "drive_file_id", "TEXT"), ("calls", "archived_at", "TEXT"),
                  ("calls", "uploaded_at", "TEXT")]


def _migrate(conn) -> None:
    for table, col, typ in _ADDED_COLUMNS:
        if getattr(conn, "is_pg", False):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
        elif col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def _init(conn, schema: str) -> None:
    # 인덱스가 새 열을 참조할 수 있으므로 테이블 생성 → 열 추가 → 나머지 순서로
    tables, rest = _split_schema(schema)
    conn.executescript(tables)
    _migrate(conn)
    conn.executescript(rest)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    # 전화번호 해시용 비밀키 — DB마다 한 번 생성. API로 노출하지 않는다.
    conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('customer_hash_key', ?)",
                 (secrets.token_hex(32),))
    conn.commit()


def _split_schema(schema: str) -> tuple[str, str]:
    stmts = [s.strip() for s in schema.split(";") if s.strip()]
    tables = [s for s in stmts if s.upper().lstrip().startswith(("CREATE TABLE", "--"))
              and "CREATE TABLE" in s.upper()]
    rest = [s for s in stmts if s not in tables]
    return ";\n".join(tables) + ";", (";\n".join(rest) + ";") if rest else ""


def insert_returning_id(conn, sql: str, params=()) -> int:
    if getattr(conn, "is_pg", False):
        return conn.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()[0]
    return conn.execute(sql, params).lastrowid


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def get_setting_int(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return int(row["value"] if row else DEFAULT_SETTINGS[key])
