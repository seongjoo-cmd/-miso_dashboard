"""문자 화면 집계(휴대폰 문자 백업 기준, 내용 없이 번호·시각·방향만).

- 통화 후 문자율: 기간 안에 통화한 고객(에이전트·번호) 중, 그 에이전트가 통화 뒤 FOLLOW_HOURS 안에 문자를 보낸 비율
- 계약률 비교: 기간 안에 통화한 고객을 문자를 받은 쪽·안 받은 쪽으로 나눠, 계약완료 녹취가 있는 고객 비율
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .dashboard import _agents, _range_args, _where
from .periods import Period

FOLLOW_HOURS = 24


def summary(conn, p: Period, agent_id: int | None) -> dict:
    where, args = _where(p, agent_id)
    s, e = _range_args(p)
    # 기간 끝에 한 통화의 후속 문자도 보이게 FOLLOW_HOURS 만큼 더 읽음
    e2 = (datetime.fromisoformat(e) + timedelta(hours=FOLLOW_HOURS)).isoformat(sep=" ")
    mwhere, margs = _where(p, agent_id)
    mwhere, margs = mwhere.replace("started_at", "sent_at"), [s, e2, *margs[2:]]
    all_msgs = _tuples(conn.execute(f"SELECT agent_id, customer_key, sent_at, box FROM messages WHERE {mwhere}", margs))
    msgs = [m for m in all_msgs if m[2] < e]
    later = [(a, k, at) for a, k, at, box in all_msgs if at >= e and box == 2 and k]
    calls = _tuples(conn.execute(
        f"SELECT agent_id, customer_key, started_at FROM calls WHERE customer_key IS NOT NULL AND {where}", args))
    contracted = {r[0] for r in conn.execute("SELECT DISTINCT customer_key FROM calls WHERE contracted = 1")}

    sent_times: dict[tuple, list[str]] = {}
    for a, k, at, box in msgs:
        if box == 2 and k:
            sent_times.setdefault((a, k), []).append(at)
    for a, k, at in later:
        sent_times.setdefault((a, k), []).append(at)

    first_call: dict[tuple, str] = {}
    for a, k, at in calls:
        if (a, k) not in first_call or at < first_call[(a, k)]:
            first_call[(a, k)] = at
    followed = {ak for ak, at in first_call.items()
                if any(at <= t <= _plus(at) for t in sent_times.get(ak, ()))}

    per = {r["id"]: {"agent_id": r["id"], "name": r["name"], "sent": 0, "received": 0, "customers": set(),
                     "called": 0, "followed": 0} for r in _agents(conn, agent_id)}
    days = {d.isoformat(): 0 for d in p.dates()}
    for a, k, at, box in msgs:
        if a not in per:
            continue
        if box == 2:
            per[a]["sent"] += 1
            if k:
                per[a]["customers"].add(k)
            days[at[:10]] = days.get(at[:10], 0) + 1
        else:
            per[a]["received"] += 1
    for a, k in first_call:
        if a in per:
            per[a]["called"] += 1
            per[a]["followed"] += (a, k) in followed

    texted = {k for (a, k) in sent_times}
    called_keys = {k for (a, k) in first_call}
    rate = lambda keys: round(len(keys & contracted) / len(keys) * 100, 1) if keys else None  # noqa: E731
    rows = sorted(({**v, "customers": len(v["customers"]),
                    "follow_pct": round(v["followed"] / v["called"] * 100, 1) if v["called"] else None}
                   for v in per.values()), key=lambda r: (-r["sent"], r["name"]))
    total_called = len(first_call)
    return {
        "follow_hours": FOLLOW_HOURS,
        "sent": sum(r["sent"] for r in rows),
        "received": sum(r["received"] for r in rows),
        "customers": len({k for a, k, at, box in msgs if box == 2 and k}),
        "follow_pct": round(len(followed) / total_called * 100, 1) if total_called else None,
        "contract_pct_texted": rate(called_keys & texted),
        "contract_pct_not_texted": rate(called_keys - texted),
        "daily": {"dates": list(days), "sent": list(days.values())},
        "agents": rows,
        "has_data": conn.execute("SELECT 1 FROM messages LIMIT 1").fetchone() is not None,
    }


def _tuples(cur) -> list[tuple]:
    # Postgres 행(Row)은 dict 라 풀어 쓰면 열 이름이 나옴 → 값 튜플로
    return [tuple(r[i] for i in range(len(r))) for r in cur.fetchall()]


def _plus(at: str) -> str:
    return (datetime.fromisoformat(at) + timedelta(hours=FOLLOW_HOURS)).isoformat(sep=" ")
