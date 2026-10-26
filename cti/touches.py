"""고객(번호)별 터치 분석: 계약·실패까지 몇 번 통화했는지, 통화 간격이 얼마였는지 + 인사이트 문장.

- 터치 = 같은 번호로 건 통화 1번(두 드라이브에 같은 녹취가 있어도 같은 시각이면 1번)
- 계약 = 그 번호의 통화 중 하나라도 계약완료. 첫 계약 통화까지를 센다
- 실패 = 계약 없이 마지막 통화 뒤 FAIL_DAYS 일 넘게 연락 없음. 그 사이면 진행 중(그래프에서 뺌)
- 대상 = 선택한 기간에 통화가 있었던 번호. 그 번호의 기간 밖 통화까지 모두 본다
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean

from .periods import Period

FAIL_DAYS = 14
TOUCH_LABELS = ["1회", "2회", "3회", "4회", "5회", "6회+"]
# (이상, 미만, 이름) — 고객별 평균 통화 간격(일)
GAP_BUCKETS = [(0, 1, "하루 안"), (1, 3, "1~2일"), (3, 7, "3~6일"), (7, 14, "1~2주"), (14, None, "2주 넘게")]
MIN_N = 5  # 이보다 적은 묶음은 인사이트 문장에 쓰지 않음(우연일 수 있어서)


def _gap_bucket(days: float) -> int:
    return next(i for i, (lo, hi, _) in enumerate(GAP_BUCKETS) if days >= lo and (hi is None or days < hi))


def analyze(conn, p: Period, agent_id: int | None, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    s, e = p.start.isoformat(), (p.end + timedelta(days=1)).isoformat()
    scope, scope_args = ("agent_id = ?", [agent_id]) if agent_id is not None \
        else ("agent_id IN (SELECT id FROM agents WHERE active = 1)", [])
    rows = conn.execute(
        f"""
        SELECT customer_key, started_at, contracted FROM calls
        WHERE {scope} AND customer_key IN (
            SELECT customer_key FROM calls
            WHERE {scope} AND customer_key IS NOT NULL AND started_at >= ? AND started_at < ?)
        ORDER BY customer_key, started_at
        """,
        [*scope_args, *scope_args, s, e],
    ).fetchall()

    # 번호별 [(시각, 계약?)] — 같은 시각은 한 번으로
    by_key: dict[str, dict[str, bool]] = {}
    for r in rows:
        t = by_key.setdefault(r["customer_key"], {})
        t[r["started_at"]] = t.get(r["started_at"], False) or bool(r["contracted"])

    fail_before = now - timedelta(days=FAIL_DAYS)
    out = {"contract": [], "fail": []}  # 고객마다 (터치 수, 처음~끝 일수, 평균 간격 일수 또는 None)
    open_n = 0
    for touches in by_key.values():
        ts = sorted(touches.items())
        idx = next((i for i, (_, c) in enumerate(ts) if c), None)
        if idx is not None:
            kind, ts = "contract", ts[:idx + 1]
        elif datetime.fromisoformat(ts[-1][0]) < fail_before:
            kind = "fail"
        else:
            open_n += 1
            continue
        times = [datetime.fromisoformat(t) for t, _ in ts]
        gaps = [(b - a).total_seconds() / 86400 for a, b in zip(times, times[1:])]
        out[kind].append((len(times), (times[-1] - times[0]).total_seconds() / 86400, mean(gaps) if gaps else None))

    by_touch = {k: [0] * len(TOUCH_LABELS) for k in out}
    for k, cs in out.items():
        for n, _, _ in cs:
            by_touch[k][min(n, len(TOUCH_LABELS)) - 1] += 1
    nc = len(out["contract"])
    cum, run = [], 0
    for v in by_touch["contract"]:
        run += v
        cum.append(round(run / nc * 100, 1) if nc else None)

    # 통화 간격별 계약률(2번 이상 통화한 고객만 — 1번이면 간격이 없음)
    gap_n = {k: [0] * len(GAP_BUCKETS) for k in out}
    for k, cs in out.items():
        for _, _, g in cs:
            if g is not None:
                gap_n[k][_gap_bucket(g)] += 1
    gap_rate = [round(c / (c + f) * 100, 1) if c + f else None for c, f in zip(gap_n["contract"], gap_n["fail"])]

    def avg(kind, i):
        vals = [c[i] for c in out[kind] if c[i] is not None]
        return round(mean(vals), 1) if vals else None

    summary = {
        "contract": nc, "fail": len(out["fail"]), "open": open_n,
        "contract_touches": avg("contract", 0), "contract_days": avg("contract", 1), "contract_gap": avg("contract", 2),
        "fail_touches": avg("fail", 0), "fail_gap": avg("fail", 2),
    }
    return {
        "fail_days": FAIL_DAYS,
        "summary": summary,
        "touch_labels": TOUCH_LABELS,
        "by_touch": by_touch,
        "contract_cum_pct": cum,
        "gap_labels": [b[2] for b in GAP_BUCKETS],
        "gap_customers": [c + f for c, f in zip(gap_n["contract"], gap_n["fail"])],
        "gap_contract_rate": gap_rate,
        "insights": _insights(summary, cum, gap_rate, gap_n),
    }


def _insights(sm: dict, cum: list, gap_rate: list, gap_n: dict) -> list[str]:
    """숫자에서 바로 행동으로 옮길 만한 결론 2~4개."""
    if sm["contract"] < MIN_N:
        return [f"아직 계약 고객이 {sm['contract']}명이라 결론을 내기엔 일러요. 계약완료 녹취가 쌓이면 더 정확해져요."]
    out = []
    k = next(i for i, v in enumerate(cum) if v is not None and v >= 80)
    head = f"{k + 1}번째" if k < len(TOUCH_LABELS) - 1 else f"{len(TOUCH_LABELS)}번 넘게"
    out.append(f"계약 고객의 {cum[k]:.0f}%가 {head} 통화 안에 계약했어요."
               + (f" {k + 1}번 넘게 걸어도 계약이 없으면 우선순위를 낮춰도 돼요." if k < len(TOUCH_LABELS) - 1 else ""))
    if sm["contract_days"] is not None:
        out.append(f"계약까지 평균 {sm['contract_touches']:.1f}번, 첫 통화부터 {sm['contract_days']:.1f}일 걸렸어요.")
    if sm["contract_gap"] is not None and sm["fail_gap"] is not None:
        c, f = sm["contract_gap"], sm["fail_gap"]
        if abs(c - f) >= 0.5:
            out.append(f"계약 고객은 평균 {c:.1f}일, 실패 고객은 {f:.1f}일 간격으로 통화했어요."
                       + (" 간격이 벌어지면 계약이 줄어드니 빨리 다시 거는 게 좋아요." if c < f else " 너무 자주 거는 것보다 텀을 두는 게 나았어요."))
    ok = [(r, i) for i, r in enumerate(gap_rate) if r is not None and gap_n["contract"][i] + gap_n["fail"][i] >= MIN_N]
    if len(ok) >= 2:
        best, worst = max(ok), min(ok)
        if best[0] - worst[0] >= 5:
            out.append(f"통화 간격이 '{GAP_BUCKETS[best[1]][2]}'일 때 계약률이 {best[0]:.0f}%로 가장 높고, "
                       f"'{GAP_BUCKETS[worst[1]][2]}'일 때 {worst[0]:.0f}%로 가장 낮아요.")
    return out
