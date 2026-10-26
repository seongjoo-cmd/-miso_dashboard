"""대시보드 집계 쿼리.

용어
- 처리 건수: 짧은 통화를 포함한 전체 통화 수
- ATT(Average Talk Time): 전체 통화의 duration_sec 평균
- 짧은 통화 비율: duration_sec < min_call_sec 인 통화 / 전체 통화
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta

from .calls import shown_phone
from .db import get_setting_int
from .periods import Period


def _range_args(p: Period) -> tuple[str, str]:
    # started_at 은 'YYYY-MM-DD HH:MM:SS' 문자열이므로 반열린 구간으로 비교한다.
    return p.start.isoformat(), (p.end + timedelta(days=1)).isoformat()


def _where(p: Period, agent_id: int | None, col: str = "") -> tuple[str, list]:
    s, e = _range_args(p)
    sql = f"{col}started_at >= ? AND {col}started_at < ?"
    args: list = [s, e]
    if agent_id is not None:
        sql += f" AND {col}agent_id = ?"
        args.append(agent_id)
    else:  # 관리에서 숨긴 에이전트(테스트·퇴사자 등)는 전체 통계에서 뺀다. 녹취 목록에서는 계속 찾을 수 있음
        sql += f" AND {col}agent_id IN (SELECT id FROM agents WHERE active = 1)"
    return sql, args


def kpis(conn: sqlite3.Connection, p: Period, agent_id: int | None, min_sec: int) -> dict:
    where, args = _where(p, agent_id)
    r = conn.execute(
        f"""
        SELECT COUNT(*)                                             AS total_calls,
               COALESCE(SUM(duration_sec), 0)                       AS total_sec,
               AVG(duration_sec)                                    AS att_sec,
               COUNT(DISTINCT customer_key)                         AS customers,
               SUM(CASE WHEN duration_sec < ? THEN 1 ELSE 0 END)    AS short_calls
        FROM calls WHERE {where}
        """,
        [min_sec, *args],
    ).fetchone()
    total = r["total_calls"]
    return {
        "total_calls": total,
        "total_sec": r["total_sec"],
        "att_sec": round(r["att_sec"], 1) if r["att_sec"] is not None else None,
        "customers": r["customers"],
        "short_ratio": round((r["short_calls"] or 0) / total, 4) if total else None,
    }


def _delta(cur, prev, kind: str):
    if cur is None or prev is None:
        return None
    if kind == "pp":  # 비율 지표는 %p 차이
        return {"abs": round(cur - prev, 4)}
    diff = cur - prev
    return {"abs": round(diff, 1), "pct": round(diff / prev * 100, 1) if prev else None}


def kpis_with_delta(conn, cur: Period, prev: Period, agent_id, min_sec) -> dict:
    c = kpis(conn, cur, agent_id, min_sec)
    pv = kpis(conn, prev, agent_id, min_sec)
    out = {}
    for key in c:
        out[key] = {
            "value": c[key],
            "prev": pv[key],
            "delta": _delta(c[key], pv[key], "pp" if key == "short_ratio" else "num"),
        }
    return out


def _agents(conn, agent_id: int | None) -> list[sqlite3.Row]:
    if agent_id is not None:
        return conn.execute("SELECT id, name FROM agents WHERE id = ?", (agent_id,)).fetchall()
    return conn.execute("SELECT id, name FROM agents WHERE active = 1 ORDER BY name").fetchall()


def daily_matrix(conn, p: Period, agent_id: int | None) -> dict:
    """행=에이전트, 열=날짜, 셀=처리 건수(짧은 통화 포함 전체). 행·열 합계 포함."""
    where, args = _where(p, agent_id)
    rows = conn.execute(
        f"""
        SELECT agent_id, substr(started_at, 1, 10) AS d, COUNT(*) AS n
        FROM calls WHERE {where}
        GROUP BY agent_id, d
        """,
        args,
    ).fetchall()
    dates = [d.isoformat() for d in p.dates()]
    idx = {d: i for i, d in enumerate(dates)}
    agents = _agents(conn, agent_id)
    by_agent = {a["id"]: [0] * len(dates) for a in agents}
    names = {a["id"]: a["name"] for a in agents}
    for r in rows:
        if r["agent_id"] not in by_agent:  # 비활성 에이전트의 과거 통화
            by_agent[r["agent_id"]] = [0] * len(dates)
            names[r["agent_id"]] = conn.execute(
                "SELECT name FROM agents WHERE id = ?", (r["agent_id"],)).fetchone()["name"]
        by_agent[r["agent_id"]][idx[r["d"]]] = r["n"]
    out_rows = [
        {"agent_id": aid, "name": names[aid], "counts": counts, "total": sum(counts)}
        for aid, counts in by_agent.items()
    ]
    col_totals = [sum(r["counts"][i] for r in out_rows) for i in range(len(dates))]
    return {
        "dates": dates,
        "rows": out_rows,
        "col_totals": col_totals,
        "grand_total": sum(col_totals),
        "max_cell": max((c for r in out_rows for c in r["counts"]), default=0),
    }


def agent_performance(conn, p: Period, agent_id: int | None) -> list[dict]:
    where, args = _where(p, agent_id, col="c.")
    rows = conn.execute(
        f"""
        SELECT a.id AS agent_id, a.name,
               COUNT(c.id)                                       AS handled,
               COUNT(c.id)                                       AS total_calls,
               COALESCE(SUM(c.duration_sec), 0)                  AS total_sec,
               AVG(c.duration_sec)                               AS att_sec,
               COALESCE(MAX(c.duration_sec), 0)                  AS longest_sec
        FROM agents a
        LEFT JOIN calls c ON c.agent_id = a.id AND {where}
        WHERE (a.active = 1 OR c.id IS NOT NULL) {'AND a.id = ?' if agent_id is not None else ''}
        GROUP BY a.id
        ORDER BY handled DESC, a.name
        """,
        [*args, *([agent_id] if agent_id is not None else [])],
    ).fetchall()
    return [
        {**dict(r), "att_sec": round(r["att_sec"], 1) if r["att_sec"] is not None else None}
        for r in rows
    ]


def daily_trend(conn, p: Period, agent_id: int | None) -> dict:
    """일별 통화량(전체 통화 기준) — 에이전트별 시리즈 + 합계."""
    where, args = _where(p, agent_id)
    rows = conn.execute(
        f"""
        SELECT agent_id, substr(started_at, 1, 10) AS d, COUNT(*) AS n
        FROM calls WHERE {where} GROUP BY agent_id, d
        """,
        args,
    ).fetchall()
    dates = [d.isoformat() for d in p.dates()]
    idx = {d: i for i, d in enumerate(dates)}
    agents = {a["id"]: a["name"] for a in _agents(conn, agent_id)}
    series: dict[int, list[int]] = {aid: [0] * len(dates) for aid in agents}
    for r in rows:
        if r["agent_id"] not in series:
            series[r["agent_id"]] = [0] * len(dates)
            agents[r["agent_id"]] = conn.execute(
                "SELECT name FROM agents WHERE id = ?", (r["agent_id"],)).fetchone()["name"]
        series[r["agent_id"]][idx[r["d"]]] = r["n"]
    total = [sum(s[i] for s in series.values()) for i in range(len(dates))]
    return {
        "dates": dates,
        "total": total,
        "series": [{"agent_id": aid, "name": agents[aid], "counts": s} for aid, s in series.items()],
    }


def hourly_distribution(conn, p: Period, agent_id: int | None) -> list[int]:
    where, args = _where(p, agent_id)
    rows = conn.execute(
        f"""
        SELECT CAST(substr(started_at, 12, 2) AS INTEGER) AS h, COUNT(*) AS n
        FROM calls WHERE {where} GROUP BY h
        """,
        args,
    ).fetchall()
    out = [0] * 24
    for r in rows:
        out[r["h"]] = r["n"]
    return out


def agent_detail(conn, agent_id: int, p: Period, recent_limit: int = 20, full_phone: bool = False) -> dict | None:
    agent = conn.execute("SELECT id, name, extension FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if agent is None:
        return None
    where, args = _where(p, agent_id)
    rows = conn.execute(
        f"""
        SELECT substr(started_at, 1, 10) AS d,
               COUNT(*) AS handled,
               AVG(duration_sec) AS att_sec
        FROM calls WHERE {where} GROUP BY d
        """,
        args,
    ).fetchall()
    dates = [d.isoformat() for d in p.dates()]
    by_d = {r["d"]: r for r in rows}
    recent = conn.execute(
        """
        SELECT id, started_at, customer_masked, customer_phone, duration_sec
        FROM calls WHERE agent_id = ? ORDER BY started_at DESC LIMIT ?
        """,
        (agent_id, recent_limit),
    ).fetchall()
    return {
        "agent": dict(agent),
        "trend": {
            "dates": dates,
            "handled": [by_d[d]["handled"] if d in by_d else 0 for d in dates],
            "att_sec": [round(by_d[d]["att_sec"], 1) if d in by_d and by_d[d]["att_sec"] is not None
                        else None for d in dates],
        },
        "recent_calls": [{"id": r["id"], "started_at": r["started_at"], "duration_sec": r["duration_sec"],
                          "customer_masked": shown_phone(r, full_phone)} for r in recent],
    }


def build(conn, cur: Period, prev: Period, agent_id: int | None) -> dict:
    min_sec = get_setting_int(conn, "min_call_sec")
    return {
        "period": {"start": cur.start.isoformat(), "end": cur.end.isoformat()},
        "prev_period": {"start": prev.start.isoformat(), "end": prev.end.isoformat()},
        "min_call_sec": min_sec,
        "kpis": kpis_with_delta(conn, cur, prev, agent_id, min_sec),
        "daily_matrix": daily_matrix(conn, cur, agent_id),
        "agent_performance": agent_performance(conn, cur, agent_id),
        "daily_trend": daily_trend(conn, cur, agent_id),
        "hourly": hourly_distribution(conn, cur, agent_id),
    }
