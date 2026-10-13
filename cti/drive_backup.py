"""구글 드라이브 녹취 백업(정리).

녹취 파일은 드라이브에 있으므로 서버가 직접 옮기지 못한다.
1. 관리자가 에이전트별 백업을 요청하면 drive_tasks 에 'pending' 으로 쌓는다.
2. Apps Script 가 5분마다 GET /api/drive/tasks 로 가져가서, 그 에이전트 폴더의 파일을
   먼저 대시보드에 적재(누락 방지)한 뒤 같은 드라이브의 '_백업/<에이전트>' 폴더로 옮긴다.
3. 옮긴 파일 ID 를 POST /api/drive/tasks/<id> 로 알려 주면 calls.archived_at 을 기록한다.
드라이브 안에서 옮기는 것이라 파일 ID 가 그대로여서 조회·재생은 계속 된다.
"""
from __future__ import annotations

from datetime import datetime

from .db import insert_returning_id

MAX_IDS = 2000


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def request(conn, agent_id: int, requested_by: str) -> dict:
    """백업 요청. 이미 대기 중인 요청이 있으면 그것을 돌려준다."""
    row = conn.execute("SELECT * FROM drive_tasks WHERE agent_id = ? AND status = 'pending'", (agent_id,)).fetchone()
    if row:
        return dict(row)
    tid = insert_returning_id(
        conn, "INSERT INTO drive_tasks(agent_id, requested_by, requested_at) VALUES (?, ?, ?)",
        (agent_id, requested_by, _now()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM drive_tasks WHERE id = ?", (tid,)).fetchone())


def request_many(conn, agent_ids: list[int], requested_by: str) -> int:
    """고른 에이전트들 백업 요청. 이미 대기 중이거나 없는 에이전트는 건너뛴다. 새로 요청한 수."""
    n = 0
    for aid in dict.fromkeys(int(a) for a in agent_ids):
        if not conn.execute("SELECT 1 FROM agents WHERE id = ?", (aid,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM drive_tasks WHERE agent_id = ? AND status = 'pending'", (aid,)).fetchone():
            continue
        request(conn, aid, requested_by)
        n += 1
    return n


def request_all(conn, requested_by: str) -> int:
    """백업 안 한 녹취가 있는 모든 에이전트를 한 번에 요청. 새로 요청한 수를 돌려준다."""
    rows = conn.execute(
        """
        SELECT DISTINCT c.agent_id FROM calls c JOIN agents a ON a.id = c.agent_id
        WHERE a.active = 1 AND c.drive_file_id IS NOT NULL AND c.drive_file_id <> 'demo' AND c.archived_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM drive_tasks t WHERE t.agent_id = c.agent_id AND t.status = 'pending')
        """).fetchall()
    for r in rows:
        request(conn, r[0], requested_by)
    return len(rows)


def pending(conn) -> list[dict]:
    """Apps Script 가 처리할 요청: 에이전트 폴더 이름과 함께."""
    rows = conn.execute(
        """
        SELECT t.id, COALESCE(a.sync_dir, a.name) AS agent
        FROM drive_tasks t JOIN agents a ON a.id = t.agent_id
        WHERE t.status = 'pending' ORDER BY t.id
        """).fetchall()
    return [dict(r) for r in rows]


def report(conn, task_id: int, status: str, moved_ids: list, failed: int = 0, message: str | None = None) -> dict:
    """status: progress(일부 옮김, 계속 대기) | done | failed"""
    if status not in ("progress", "done", "failed"):
        raise ValueError("status 는 progress, done, failed 중 하나여야 해요.")
    task = conn.execute("SELECT * FROM drive_tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise LookupError("없는 백업 요청이에요.")
    ids = [str(i) for i in moved_ids[:MAX_IDS] if i]
    now = _now()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(
            f"UPDATE calls SET archived_at = ? WHERE agent_id = ? AND drive_file_id IN ({','.join('?' * len(chunk))})",
            [now, task["agent_id"], *chunk])
    conn.execute(
        """
        UPDATE drive_tasks SET moved = moved + ?, failed = failed + ?, message = COALESCE(?, message),
               status = ?, done_at = CASE WHEN ? = 'progress' THEN done_at ELSE ? END
        WHERE id = ?
        """,
        (len(ids), int(failed or 0), message, "pending" if status == "progress" else status, status, now, task_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM drive_tasks WHERE id = ?", (task_id,)).fetchone())


def overview(conn) -> dict:
    """설정 화면용: 에이전트별 아직 백업 안 한 녹취 수 + 대기 중 요청 + 최근 기록."""
    agents = conn.execute(
        """
        SELECT a.id AS agent_id, a.name,
               SUM(CASE WHEN c.drive_file_id IS NOT NULL AND c.drive_file_id <> 'demo'
                         AND c.archived_at IS NULL THEN 1 ELSE 0 END) AS waiting,
               MAX(c.archived_at) AS last_archived
        FROM agents a LEFT JOIN calls c ON c.agent_id = a.id
        WHERE a.active = 1
        GROUP BY a.id ORDER BY a.name
        """).fetchall()
    pend = {r["agent_id"]: dict(r) for r in conn.execute("SELECT * FROM drive_tasks WHERE status = 'pending'")}
    history = conn.execute(
        """
        SELECT t.id, a.name AS agent_name, t.requested_by, t.requested_at, t.status, t.done_at, t.moved, t.failed, t.message
        FROM drive_tasks t JOIN agents a ON a.id = t.agent_id
        ORDER BY t.id DESC LIMIT 20
        """).fetchall()
    return {
        "agents": [{**dict(r), "waiting": r["waiting"] or 0, "pending": pend.get(r["agent_id"])} for r in agents],
        "history": [dict(r) for r in history],
    }
