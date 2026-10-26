import unittest
from datetime import date

from cti import dashboard
from cti.db import connect
from cti.periods import Period, resolve


def make_db():
    conn = connect(":memory:")
    conn.executemany("INSERT INTO agents(id, name) VALUES (?, ?)", [(1, "가"), (2, "나"), (3, "다")])
    calls = [
        # agent, customer, started_at, duration
        (1, "A", "2026-10-12 09:10:00", 100),
        (1, "B", "2026-10-12 09:50:00", 5),      # 짧은 통화
        (1, "A", "2026-10-12 14:00:00", 300),
        (2, "C", "2026-10-12 23:59:59", 10),     # 경계값: 10초는 처리 건수
        (2, "", "2026-10-11 10:00:00", 50),      # 전일
        (2, "D", "2026-10-13 00:00:00", 70),     # 다음 날 → 제외
    ]
    conn.executemany(
        "INSERT INTO calls(agent_id, customer_key, started_at, duration_sec) VALUES (?, NULLIF(?, ''), ?, ?)", calls)
    conn.commit()
    return conn


DAY = Period(date(2026, 10, 12), date(2026, 10, 12))
PREV = Period(date(2026, 10, 11), date(2026, 10, 11))


class KpiTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_kpis(self):
        k = dashboard.kpis(self.conn, DAY, None, 10)
        self.assertEqual(k["total_calls"], 4)
        self.assertEqual(k["total_sec"], 415)
        self.assertEqual(k["att_sec"], round((100 + 5 + 300 + 10) / 4, 1))  # 5초 짧은 통화도 포함
        self.assertEqual(k["customers"], 3)  # A, B, C
        self.assertEqual(k["short_ratio"], 0.25)

    def test_kpis_agent_filter(self):
        k = dashboard.kpis(self.conn, DAY, 2, 10)
        self.assertEqual(k["total_calls"], 1)
        self.assertEqual(k["att_sec"], 10)

    def test_empty_period(self):
        k = dashboard.kpis(self.conn, Period(date(2020, 1, 1), date(2020, 1, 1)), None, 10)
        self.assertEqual(k["total_calls"], 0)
        self.assertIsNone(k["att_sec"])
        self.assertIsNone(k["short_ratio"])

    def test_delta(self):
        k = dashboard.kpis_with_delta(self.conn, DAY, PREV, None, 10)
        self.assertEqual(k["total_calls"]["prev"], 1)
        self.assertEqual(k["total_calls"]["delta"], {"abs": 3, "pct": 300.0})
        self.assertEqual(k["short_ratio"]["delta"], {"abs": 0.25})  # %p 차이
        self.assertEqual(k["customers"]["prev"], 0)  # 빈 번호는 고객으로 세지 않음
        self.assertIsNone(k["customers"]["delta"]["pct"])

    def test_min_call_sec_changes_short_ratio_only(self):
        k = dashboard.kpis(self.conn, DAY, None, 200)
        self.assertEqual(k["short_ratio"], 0.75)
        self.assertEqual(k["att_sec"], round(415 / 4, 1))


class TablesTest(unittest.TestCase):
    def setUp(self):
        self.conn = make_db()

    def test_matrix_totals(self):
        cur, _ = resolve("custom", date(2026, 10, 12), date(2026, 10, 11), date(2026, 10, 12))
        m = dashboard.daily_matrix(self.conn, cur, None)
        self.assertEqual(m["dates"], ["2026-10-11", "2026-10-12"])
        rows = {r["agent_id"]: r for r in m["rows"]}
        self.assertEqual(rows[1]["counts"], [0, 3])  # 5초 짧은 통화도 포함
        self.assertEqual(rows[2]["counts"], [1, 1])
        self.assertEqual(rows[3]["counts"], [0, 0])
        self.assertEqual(m["col_totals"], [1, 4])
        self.assertEqual(m["grand_total"], 5)
        self.assertEqual(m["max_cell"], 3)

    def test_performance(self):
        rows = {r["agent_id"]: r for r in dashboard.agent_performance(self.conn, DAY, None)}
        self.assertEqual(rows[1]["handled"], 3)  # 5초 짧은 통화도 포함
        self.assertEqual(rows[1]["total_sec"], 405)
        self.assertEqual(rows[1]["att_sec"], 135)
        self.assertEqual(rows[1]["longest_sec"], 300)
        self.assertEqual(rows[3]["handled"], 0)
        self.assertIsNone(rows[3]["att_sec"])

    def test_performance_agent_filter(self):
        rows = dashboard.agent_performance(self.conn, DAY, 2)
        self.assertEqual([r["agent_id"] for r in rows], [2])

    def test_hourly(self):
        h = dashboard.hourly_distribution(self.conn, DAY, None)
        self.assertEqual(len(h), 24)
        self.assertEqual(h[9], 2)
        self.assertEqual(h[14], 1)
        self.assertEqual(h[23], 1)

    def test_trend(self):
        t = dashboard.daily_trend(self.conn, DAY, None)
        self.assertEqual(t["total"], [4])
        self.assertEqual({s["agent_id"]: s["counts"] for s in t["series"]}, {1: [3], 2: [1], 3: [0]})

    def test_hidden_agent_left_out_of_stats(self):
        m0 = dashboard.daily_matrix(self.conn, DAY, None)
        before, n2 = m0["grand_total"], next(r["total"] for r in m0["rows"] if r["agent_id"] == 2)
        self.conn.execute("UPDATE agents SET active = 0 WHERE id = 2")  # 관리에서 숨김
        m = dashboard.daily_matrix(self.conn, DAY, None)
        self.assertNotIn(2, [r["agent_id"] for r in m["rows"]])
        self.assertEqual(m["grand_total"], before - n2)
        self.assertNotIn(2, [r["agent_id"] for r in dashboard.agent_performance(self.conn, DAY, None)])
        self.assertNotIn(2, [s["agent_id"] for s in dashboard.daily_trend(self.conn, DAY, None)["series"]])
        self.assertIsNotNone(dashboard.agent_detail(self.conn, 2, DAY))  # 직접 고르면 볼 수는 있음

    def test_agent_detail(self):
        d = dashboard.agent_detail(self.conn, 1, DAY)
        self.assertEqual(d["trend"]["handled"], [3])
        self.assertEqual(d["recent_calls"][0]["started_at"], "2026-10-12 14:00:00")
        self.assertIsNone(dashboard.agent_detail(self.conn, 99, DAY))


if __name__ == "__main__":
    unittest.main()
