import unittest
from datetime import date, datetime

from cti import touches
from cti.db import connect
from cti.periods import Period

NOW = datetime(2026, 10, 31, 12, 0, 0)
OCT = Period(date(2026, 10, 1), date(2026, 10, 31))


class TouchesTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.conn.execute("INSERT INTO agents(id, name) VALUES (1, '김'), (2, '숨김')")
        self.conn.execute("UPDATE agents SET active = 0 WHERE id = 2")
        self.n = 0

    def call(self, cust, at, contracted=0, agent=1):
        self.n += 1
        self.conn.execute(
            "INSERT INTO calls(agent_id, customer_key, started_at, source_key, contracted) VALUES (?, ?, ?, ?, ?)",
            (agent, cust, at, f"s{self.n}", contracted))

    def test_counts_gaps_and_insights(self):
        # 계약 6명: 2번째 통화에서 계약, 간격 1일
        for i in range(6):
            c = f"c{i}"
            self.call(c, "2026-10-01 10:00:00")
            self.call(c, "2026-10-02 10:00:00", contracted=1)
            self.call(c, "2026-10-02 10:00:00", contracted=0)  # 다른 드라이브의 같은 녹취 → 한 번으로
            self.call(c, "2026-10-05 10:00:00")               # 계약 뒤 통화는 세지 않음
        # 실패 6명: 3번, 간격 5일, 마지막 통화가 14일 넘게 전
        for i in range(6):
            for d in ("01", "06", "11"):
                self.call(f"f{i}", f"2026-10-{d} 09:00:00")
        self.call("open", "2026-10-30 09:00:00")              # 진행 중
        self.call("hidden", "2026-10-01 09:00:00", contracted=1, agent=2)  # 숨긴 에이전트 → 뺌
        self.call("old", "2026-09-01 09:00:00", contracted=1)  # 기간 밖에만 통화 → 뺌

        r = touches.analyze(self.conn, OCT, None, now=NOW)
        sm = r["summary"]
        self.assertEqual((sm["contract"], sm["fail"], sm["open"]), (6, 6, 1))
        self.assertEqual(r["by_touch"]["contract"], [0, 6, 0, 0, 0, 0])
        self.assertEqual(r["by_touch"]["fail"], [0, 0, 6, 0, 0, 0])
        self.assertEqual(r["contract_cum_pct"][:2], [0.0, 100.0])
        self.assertEqual((sm["contract_touches"], sm["contract_days"], sm["contract_gap"], sm["fail_gap"]), (2, 1, 1, 5))
        self.assertEqual(r["gap_customers"], [0, 6, 6, 0, 0])
        self.assertEqual(r["gap_contract_rate"], [None, 100.0, 0.0, None, None])
        text = " ".join(r["insights"])
        self.assertIn("2번째 통화 안에 계약", text)
        self.assertIn("1.0일, 실패 고객은 5.0일", text)
        self.assertIn("'1~2일'일 때 계약률이 100%", text)

    def test_few_contracts_says_so(self):
        self.call("c", "2026-10-01 10:00:00", contracted=1)
        r = touches.analyze(self.conn, OCT, None, now=NOW)
        self.assertEqual(len(r["insights"]), 1)
        self.assertIn("결론을 내기엔 일러요", r["insights"][0])
        self.assertEqual(r["summary"]["contract_touches"], 1)

    def test_agent_filter(self):
        self.call("x", "2026-10-01 10:00:00", contracted=1, agent=2)
        self.assertEqual(touches.analyze(self.conn, OCT, 2, now=NOW)["summary"]["contract"], 1)


if __name__ == "__main__":
    unittest.main()
