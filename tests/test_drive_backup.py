import unittest

from cti import calls, drive_backup, ingest
from cti.db import connect


def rec(fid, agent, name):
    return {"id": fid, "agent": agent, "name": name, "size": 1000, "durationMs": 60000}


class DriveBackupTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        ingest.ingest_records(self.conn, [
            rec("a1", "김민지", "01011112222_20261012_090000.mp4"),
            rec("a2", "김민지", "01011112222_20261012_100000.mp4"),
            rec("b1", "이서준", "01033334444_20261012_110000.mp4"),
        ])
        self.kim = self.conn.execute("SELECT id FROM agents WHERE name = '김민지'").fetchone()[0]

    def tearDown(self):
        self.conn.close()

    def waiting(self):
        return {a["name"]: a["waiting"] for a in drive_backup.overview(self.conn)["agents"]}

    def test_request_is_idempotent_while_pending(self):
        t1 = drive_backup.request(self.conn, self.kim, "admin")
        t2 = drive_backup.request(self.conn, self.kim, "admin")
        self.assertEqual(t1["id"], t2["id"])
        self.assertEqual(drive_backup.pending(self.conn), [{"id": t1["id"], "agent": "김민지"}])

    def test_progress_then_done(self):
        t = drive_backup.request(self.conn, self.kim, "admin")
        self.assertEqual(self.waiting(), {"김민지": 2, "이서준": 1})
        r = drive_backup.report(self.conn, t["id"], "progress", ["a1"])
        self.assertEqual((r["status"], r["moved"]), ("pending", 1))
        self.assertEqual(self.waiting()["김민지"], 1)
        r = drive_backup.report(self.conn, t["id"], "done", ["a2"])
        self.assertEqual((r["status"], r["moved"]), ("done", 2))
        self.assertEqual(self.waiting(), {"김민지": 0, "이서준": 1})
        self.assertEqual(drive_backup.pending(self.conn), [])
        # 백업 후에도 조회·재생 가능(파일 ID 그대로)
        found = calls.search(self.conn, None, q="01011112222")
        self.assertEqual(found["total"], 2)
        self.assertEqual(calls.detail(self.conn, found["items"][0]["id"])["play"]["type"], "drive")

    def test_other_agents_files_untouched(self):
        t = drive_backup.request(self.conn, self.kim, "admin")
        drive_backup.report(self.conn, t["id"], "done", ["a1", "b1"])  # b1 은 다른 에이전트 → 무시
        self.assertEqual(self.waiting()["이서준"], 1)

    def test_request_all(self):
        lee = self.conn.execute("SELECT id FROM agents WHERE name = '이서준'").fetchone()[0]
        drive_backup.request(self.conn, lee, "admin")  # 이미 대기 중인 에이전트는 그대로
        self.assertEqual(drive_backup.request_all(self.conn, "admin"), 1)
        self.assertEqual(sorted(t["agent"] for t in drive_backup.pending(self.conn)), ["김민지", "이서준"])
        self.assertEqual(drive_backup.request_all(self.conn, "admin"), 0)  # 다시 눌러도 중복 요청 없음

    def test_request_many(self):
        lee = self.conn.execute("SELECT id FROM agents WHERE name = '이서준'").fetchone()[0]
        self.assertEqual(drive_backup.request_many(self.conn, [self.kim, lee, self.kim, 999], "admin"), 2)
        self.assertEqual(drive_backup.request_many(self.conn, [self.kim], "admin"), 0)  # 이미 대기 중
        self.assertEqual(len(drive_backup.pending(self.conn)), 2)

    def test_bad_report(self):
        t = drive_backup.request(self.conn, self.kim, "admin")
        with self.assertRaises(ValueError):
            drive_backup.report(self.conn, t["id"], "weird", [])
        with self.assertRaises(LookupError):
            drive_backup.report(self.conn, 999, "done", [])


if __name__ == "__main__":
    unittest.main()
