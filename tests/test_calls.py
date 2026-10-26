import unittest
from datetime import date

from cti import calls, ingest
from cti.db import connect
from cti.periods import Period


def rec(fid, agent, name, ms=60000, size=1000):
    return {"id": fid, "agent": agent, "name": name, "size": size, "durationMs": ms}


class CallsTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        ingest.ingest_records(self.conn, [
            rec("f1", "김민지", "통화 녹음 010-1111-2222_261012_090000.mp4"),
            rec("f2", "김민지", "통화 녹음 010-1111-2222_261005_100000.mp4"),
            rec("f3", "이서준", "통화 녹음 010-3333-2222_261012_110000.mp4", ms=5000),
            rec("f4", "이서준", "통화 녹음 16004112_261012_120000.mp4"),
            rec("f5", "이서준", "녹음_261012_130000.mp4"),  # 번호 없음
        ])
        self.day = Period(date(2026, 10, 12), date(2026, 10, 12))

    def tearDown(self):
        self.conn.close()

    def ids(self, res):
        return [self.conn.execute("SELECT drive_file_id FROM calls WHERE id = ?", (i["id"],)).fetchone()[0]
                for i in res["items"]]

    def test_period_list_newest_first(self):
        r = calls.search(self.conn, self.day)
        self.assertEqual(self.ids(r), ["f5", "f4", "f3", "f1"])
        self.assertEqual(r["total"], 4)
        self.assertTrue(all(i["playable"] for i in r["items"]))
        self.assertEqual(r["items"][0]["agent_name"], "이서준")

    def test_full_phone_only_when_asked(self):
        r = calls.search(self.conn, self.day)
        self.assertEqual(r["items"][-1]["customer_masked"], "010-****-2222")
        r = calls.search(self.conn, self.day, full_phone=True)
        self.assertEqual([i["customer_masked"] for i in r["items"]], [None, "1600-4112", "010-3333-2222", "010-1111-2222"])
        self.assertEqual(calls.detail(self.conn, r["items"][-1]["id"], full_phone=True)["customer_masked"], "010-1111-2222")
        # 전체 번호 저장 전에 들어온 녹취는 다시 받을 때 채운다
        self.conn.execute("UPDATE calls SET customer_phone = NULL")
        ingest.ingest_records(self.conn, [rec("f1", "김민지", "통화 녹음 010-1111-2222_261012_090000.mp4")])
        self.assertEqual(calls.search(self.conn, self.day, full_phone=True)["items"][-1]["customer_masked"], "010-1111-2222")

    def test_contract_only(self):
        ingest.mark_contracts(self.conn, ["f3"])
        r = calls.search(self.conn, self.day, contract_only=True)
        self.assertEqual(self.ids(r), ["f3"])
        self.assertTrue(r["items"][0]["contracted"])
        self.assertTrue(calls.detail(self.conn, r["items"][0]["id"])["contracted"])

    def test_full_number_search(self):
        for q in ("010-1111-2222", "01011112222", "+82 10 1111 2222"):
            r = calls.search(self.conn, None, q=q)
            self.assertEqual(self.ids(r), ["f1", "f2"], q)
            self.assertTrue(r["by_customer"])
        # 날짜 조건도 함께 적용(주문 조회처럼)
        self.assertEqual(self.ids(calls.search(self.conn, self.day, q="010-1111-2222")), ["f1"])
        self.assertEqual(self.ids(calls.search(self.conn, None, q="1600-4112")), ["f4"])

    def test_hour_range(self):
        self.assertEqual(self.ids(calls.search(self.conn, self.day, hour_from=10, hour_to=12)), ["f3"])
        self.assertEqual(self.ids(calls.search(self.conn, self.day, hour_from=12)), ["f5", "f4"])
        self.assertEqual(self.ids(calls.search(self.conn, None, hour_from=9, hour_to=10, agent_id=1)), ["f1"])
        with self.assertRaises(calls.SearchError):
            calls.search(self.conn, None, hour_from=15, hour_to=10)

    def test_last4_and_agent_name(self):
        self.assertEqual(self.ids(calls.search(self.conn, None, q="2222")), ["f3", "f1", "f2"])
        self.assertEqual(self.ids(calls.search(self.conn, self.day, q="2222")), ["f3", "f1"])
        self.assertEqual(self.ids(calls.search(self.conn, self.day, q="서준")), ["f5", "f4", "f3"])

    def test_bad_query(self):
        with self.assertRaises(calls.SearchError):
            calls.search(self.conn, None, q="12345")

    def test_same_customer_and_min_sec(self):
        f1 = self.conn.execute("SELECT id FROM calls WHERE drive_file_id = 'f1'").fetchone()[0]
        self.assertEqual(self.ids(calls.search(self.conn, self.day, same_as=f1)), ["f1", "f2"])
        f5 = self.conn.execute("SELECT id FROM calls WHERE drive_file_id = 'f5'").fetchone()[0]
        with self.assertRaises(calls.SearchError):
            calls.search(self.conn, None, same_as=f5)
        self.assertNotIn("f3", self.ids(calls.search(self.conn, self.day, min_sec=10)))

    def test_pagination(self):
        r1 = calls.search(self.conn, None, limit=2)
        self.assertEqual(len(r1["items"]), 2)
        r2 = calls.search(self.conn, None, limit=2, cursor=r1["next_cursor"])
        r3 = calls.search(self.conn, None, limit=2, cursor=r2["next_cursor"])
        self.assertIsNone(r3["next_cursor"])
        seen = self.ids(r1) + self.ids(r2) + self.ids(r3)
        self.assertEqual(seen, ["f5", "f4", "f3", "f1", "f2"])

    def test_detail(self):
        f1 = self.conn.execute("SELECT id FROM calls WHERE drive_file_id = 'f1'").fetchone()[0]
        d = calls.detail(self.conn, f1)
        self.assertEqual(d["play"]["preview_url"], "https://drive.google.com/file/d/f1/preview")
        self.assertEqual(d["same_customer_calls"], 2)
        self.assertEqual(d["customer_masked"], "010-****-2222")
        self.assertIsNone(calls.detail(self.conn, 9999))


if __name__ == "__main__":
    unittest.main()
