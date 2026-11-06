import os
import unittest
from datetime import date
from unittest import mock

from cti import calls, hubspot, ingest
from cti.db import connect
from cti.periods import Period


def rec(fid, agent, name):
    return {"id": fid, "agent": agent, "name": name, "size": 1000, "durationMs": 60000}


class FakeHubspot:
    """딜 2건(연락처 1·2), 연락처 3(전화번호 없음). 검색은 100건씩 — 여기선 페이지 크기를 1로 줄여 이어 읽기를 시험."""

    def __init__(self, page=100):
        self.page, self.calls = page, []
        self.deals = [{"id": "d1", "properties": {"hs_lastmodifieddate": "2026-10-30T01:00:00.000Z"}},
                      {"id": "d2", "properties": {"hs_lastmodifieddate": "2026-10-30T02:00:00.000Z"}}]

    def __call__(self, method, path, body=None):
        self.calls.append((path, body))
        if path == "/crm/v3/objects/deals/search":
            start = int(body.get("after") or 0)
            chunk = self.deals[start:start + self.page]
            nxt = start + self.page
            out = {"results": chunk}
            if nxt < len(self.deals):
                out["paging"] = {"next": {"after": str(nxt)}}
            return out
        if path == "/crm/v4/associations/deals/contacts/batch/read":
            links = {"d1": [1, 3], "d2": [2]}
            return {"results": [{"from": {"id": i["id"]}, "to": [{"toObjectId": c} for c in links[i["id"]]]}
                                for i in body["inputs"]]}
        if path == "/crm/v3/objects/contacts/batch/read":
            data = {"1": {"phone": "+82 10-1111-2222"}, "2": {"phone": None, "mobilephone": "01033332222"}, "3": {}}
            return {"results": [{"id": i["id"], "properties": data[i["id"]]} for i in body["inputs"]]}
        raise AssertionError(path)


class HubspotTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        ingest.ingest_records(self.conn, [
            rec("f1", "김민지", "통화 녹음 010-1111-2222_261012_090000.mp4"),
            rec("f2", "김민지", "통화 녹음 010-1111-2222_261005_100000.mp4"),  # 같은 고객의 다른 날 통화
            rec("f3", "이서준", "통화 녹음 010-3333-2222_261012_110000.mp4"),
            rec("f4", "이서준", "통화 녹음 010-9999-0000_261012_120000.mp4"),  # 허브스팟에 없는 고객
        ])
        self.day = Period(date(2026, 10, 1), date(2026, 10, 31))
        self.env = mock.patch.dict(os.environ, {"HUBSPOT_TOKEN": "test-token"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.close()

    def ids(self, res):
        return sorted(self.conn.execute("SELECT drive_file_id FROM calls WHERE id = ?", (i["id"],)).fetchone()[0]
                      for i in res["items"])

    def test_customer_level_contract(self):
        with mock.patch.object(hubspot, "_request", FakeHubspot()):
            r = hubspot.sync(self.conn)
        self.assertTrue(r["done"])
        self.assertEqual(hubspot.status(self.conn)["customers"], 2)  # 전화번호 없는 연락처는 건너뜀
        res = calls.search(self.conn, self.day)
        self.assertEqual({i["id"]: i["contracted"] for i in res["items"]}.values().__len__(), 4)
        only = calls.search(self.conn, self.day, contract_only=True)
        self.assertEqual(self.ids(only), ["f1", "f2", "f3"])  # 계약한 고객의 통화는 모두(날짜 상관없이)
        rest = calls.search(self.conn, self.day, no_contract=True)
        self.assertEqual(self.ids(rest), ["f4"])
        self.assertTrue(all(not i["contracted"] for i in rest["items"]))
        self.assertTrue(calls.detail(self.conn, only["items"][0]["id"])["contracted"])

    def test_drive_contract_folder_still_counts(self):
        self.conn.execute("UPDATE calls SET contracted = 1 WHERE drive_file_id = 'f4'")
        self.assertEqual(self.ids(calls.search(self.conn, self.day, contract_only=True)), ["f4"])
        self.assertEqual(self.ids(calls.search(self.conn, self.day, no_contract=True)), ["f1", "f2", "f3"])

    def test_resume_when_time_runs_out_and_no_phone_stored(self):
        fake = FakeHubspot(page=1)
        with mock.patch.object(hubspot, "_request", fake):
            first = hubspot.sync(self.conn, budget=-1)  # 한 쪽만 읽고 멈춤
            self.assertFalse(first["done"])
            self.assertEqual(hubspot.status(self.conn)["customers"], 1)
            second = hubspot.sync(self.conn)
        self.assertTrue(second["done"])
        self.assertEqual(hubspot.status(self.conn)["customers"], 2)
        stored = " ".join(str(v) for r in self.conn.execute("SELECT * FROM hubspot_contracts") for v in tuple(r))
        self.assertNotIn("1111", stored)  # 번호 원문은 저장하지 않음

    def test_incremental_after_first_full_run(self):
        fake = FakeHubspot()
        with mock.patch.object(hubspot, "_request", fake):
            hubspot.sync(self.conn)
            fake.calls.clear()
            hubspot.sync(self.conn)
        flt = fake.calls[0][1]["filterGroups"][0]["filters"]
        since = next(f for f in flt if f["propertyName"] == "hs_lastmodifieddate")
        self.assertGreater(int(since["value"]), 0)  # 두 번째부터는 지난 회차 이후 바뀐 딜만

    def test_error_is_recorded_and_token_not_exposed(self):
        def boom(*a, **k):
            raise hubspot.HubspotError("허브스팟 토큰이 맞지 않거나 권한이 부족해요.")
        with mock.patch.object(hubspot, "_request", boom):
            r = hubspot.sync(self.conn)
        self.assertIn("error", r)
        st = hubspot.status(self.conn)
        self.assertTrue(st["error"])
        self.assertNotIn("test-token", repr(st))

    def test_maybe_sync_needs_token_and_waits(self):
        fake = FakeHubspot()
        with mock.patch.object(hubspot, "_request", fake):
            self.assertIsNotNone(hubspot.maybe_sync(self.conn))
            self.assertIsNone(hubspot.maybe_sync(self.conn))  # 방금 했으니 건너뜀
        with mock.patch.dict(os.environ, {"HUBSPOT_TOKEN": ""}):
            self.assertIsNone(hubspot.maybe_sync(self.conn, min_interval=0))


if __name__ == "__main__":
    unittest.main()
