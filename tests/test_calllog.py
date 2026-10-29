import unittest
from datetime import datetime

from cti import calllog, ingest
from cti.db import connect


def ms(*a):
    return int(datetime(*a).timestamp() * 1000)


REC = {"id": "f1", "agent": "가", "name": "통화 녹음 01012345678_261026_152409.m4a", "size": 16000, "durationMs": None}
LOG = [["+821012345678", ms(2026, 10, 26, 15, 23, 55), 67, 2],   # 녹취 14초 전에 건 통화 → 짝
       ["01012345678", ms(2026, 10, 26, 15, 0, 0), 0, 2],         # 안 받은 통화
       ["", ms(2026, 10, 26, 15, 1, 0), 0, 3]]                     # 번호 없음


class CallLogTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def dur(self):
        return tuple(self.conn.execute("SELECT duration_sec, duration_source FROM calls").fetchone())

    def test_log_after_recording_fills_duration(self):
        ingest.ingest_records(self.conn, [REC])
        self.assertEqual(self.dur()[1], "size")
        r = calllog.ingest_file(self.conn, "x1", "가", LOG)
        self.assertEqual(r, {"added": 3, "filled": 1})
        self.assertEqual(self.dur(), (67, "calllog"))
        # 같은 기록을 또 받아도 중복 없음
        self.assertEqual(calllog.ingest_file(self.conn, "x2", "가", LOG)["added"], 0)
        self.assertEqual(calllog.known_files(self.conn, ["x1", "x9"]), ["x1"])

    def test_recording_after_log_fills_duration(self):
        calllog.ingest_file(self.conn, "x1", "가", LOG)
        ingest.ingest_records(self.conn, [REC])
        self.assertEqual(self.dur(), (67, "calllog"))

    def test_no_match_outside_window(self):
        calllog.ingest_file(self.conn, "x1", "가", [["01012345678", ms(2026, 10, 26, 15, 20, 0), 67, 2]])
        ingest.ingest_records(self.conn, [REC])
        self.assertEqual(self.dur()[1], "size")


class MessagesTest(unittest.TestCase):
    def test_sent_sms_counted_per_agent(self):
        from cti import dashboard
        from cti.periods import Period
        from datetime import date
        conn = connect(":memory:")
        sms = [["01012345678", ms(2026, 10, 26, 10, 0, 0), 2, "sms"],
               ["+821012345678", ms(2026, 10, 26, 11, 0, 0), 2, "mms"],
               ["01099998888", ms(2026, 10, 26, 12, 0, 0), 1, "sms"],   # 받은 문자
               ["01099998888", ms(2026, 10, 26, 12, 0, 0), 3, "sms"]]   # 임시저장 등은 뺌
        self.assertEqual(calllog.ingest_messages(conn, "s1", "가", sms), {"added": 3})
        self.assertEqual(calllog.ingest_messages(conn, "s2", "가", sms), {"added": 0})
        rows = dashboard.agent_performance(conn, Period(date(2026, 10, 26), date(2026, 10, 26)), None)
        self.assertEqual((rows[0]["sms_sent"], rows[0]["sms_customers"]), (2, 1))


    def test_sms_summary_follow_up_and_contract(self):
        from cti import sms
        from cti.periods import Period
        from datetime import date
        conn = connect(":memory:")
        ingest.ingest_records(conn, [REC, {**REC, "id": "f2", "contract": True,
                                           "name": "통화 녹음 01077776666_261026_100000.m4a"}])
        calllog.ingest_messages(conn, "s1", "가", [
            ["01012345678", ms(2026, 10, 26, 16, 0, 0), 2, "sms"],   # 통화(15:24) 뒤 문자 → 후속
            ["01055554444", ms(2026, 10, 26, 9, 0, 0), 2, "sms"],
            ["01055554444", ms(2026, 10, 26, 9, 5, 0), 1, "sms"]])
        s = sms.summary(conn, Period(date(2026, 10, 26), date(2026, 10, 26)), None)
        self.assertEqual((s["sent"], s["received"], s["customers"]), (2, 1, 2))
        self.assertEqual(s["follow_pct"], 50.0)  # 통화한 고객 2명 중 1명
        self.assertEqual((s["contract_pct_texted"], s["contract_pct_not_texted"]), (0.0, 100.0))
        self.assertEqual(s["daily"]["sent"], [2])
        self.assertEqual(s["agents"][0]["followed"], 1)


if __name__ == "__main__":
    unittest.main()
