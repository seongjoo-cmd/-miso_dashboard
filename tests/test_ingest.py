import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cti import ingest
from cti.db import connect
from tests.helpers import write_m4a, write_wav


class ParseTest(unittest.TestCase):
    def check(self, name, started, phone, masked):
        p = ingest.parse_filename(name)
        self.assertIsNotNone(p, name)
        self.assertEqual((p.started_at, p.phone, p.masked), (started, phone, masked), name)

    def test_formats(self):
        t = datetime(2026, 10, 12, 10, 30, 15)
        self.check("통화 녹음 010-1234-5678_261012_103015.m4a", t, "01012345678", "010-****-5678")
        self.check("01012345678_20261012_103015.wav", t, "01012345678", "010-****-5678")
        self.check("20261012103015_01012345678.mp3", t, "01012345678", "010-****-5678")
        self.check("2026-10-12 10-30-15 +82 10 1234 5678.m4a", t, "01012345678", "010-****-5678")
        self.check("0311234567_20261012_103015.amr", t, "0311234567", "031-****-4567")
        # 실제 녹취 파일 이름 형식(삼성 통화 녹음, 대표번호 8자리)
        self.check("통화 녹음 16004112_260724_140102.mp4", datetime(2026, 7, 24, 14, 1, 2), "16004112", "1600-****")
        self.check("통화 녹음 1588-1234_261012_103015.m4a", t, "15881234", "1588-****")

    def test_no_phone_still_parses(self):
        p = ingest.parse_filename("녹음_20261012_051230.m4a")
        self.assertEqual((p.started_at, p.phone), (datetime(2026, 10, 12, 5, 12, 30), None))

    def test_unparseable(self):
        self.assertIsNone(ingest.parse_filename("memo.m4a"))
        self.assertIsNone(ingest.parse_filename("01012345678_20261340_103015.wav"))  # 13월

    def test_mask_name(self):
        self.assertEqual(ingest.mask_name("가/01012345678_x.wav"), "가/010-****-5678_x.wav")
        self.assertEqual(ingest.mask_name("가/통화 녹음 16004112_x.mp4"), "가/통화 녹음 1600-****_x.mp4")


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sync"
        self.conn = connect(":memory:")

    def tearDown(self):
        self.tmp.cleanup()

    def calls(self):
        return self.conn.execute(
            "SELECT a.name, c.* FROM calls c JOIN agents a ON a.id = c.agent_id ORDER BY started_at").fetchall()

    def test_ingest_all(self):
        write_wav(self.root / "김민지" / "통화 녹음 010-1111-2222_261012_090000.wav", 42)
        write_m4a(self.root / "김민지" / "2026" / "01033334444_20261012_091500.m4a", 125)
        write_wav(self.root / "이서준" / "통화 녹음 010-1111-2222_261012_100000.wav", 5)
        write_wav(self.root / "이서준" / "memo.wav", 1)                     # 해석 실패
        notes = self.root / "이서준" / "notes.txt"                          # 녹취 아님
        notes.write_text("x")
        os.utime(notes, (0, 0))
        write_wav(self.root / "이서준" / "01055556666_20261012_110000.wav", 1, old=False)  # 동기화 중

        s = ingest.ingest_all(self.conn, self.root)
        self.assertEqual((s["agents"], s["added"], s["recent"], s["not_audio"]), (2, 3, 1, 1))
        self.assertEqual(s["failed"], ["이서준/memo.wav"])

        rows = self.calls()
        self.assertEqual([(r["name"], r["started_at"], r["duration_sec"]) for r in rows], [
            ("김민지", "2026-10-12 09:00:00", 42),
            ("김민지", "2026-10-12 09:15:00", 125),
            ("이서준", "2026-10-12 10:00:00", 5),
        ])
        # 같은 번호 → 같은 고객 키, 원문 번호는 어디에도 없음
        self.assertEqual(rows[0]["customer_key"], rows[2]["customer_key"])
        self.assertNotEqual(rows[0]["customer_key"], rows[1]["customer_key"])
        self.assertEqual(rows[1]["customer_masked"], "010-****-4444")
        dump = "\n".join(self.conn.iterdump())
        for raw in ("01011112222", "010-1111-2222", "01033334444"):
            self.assertNotIn(raw, dump)

        # 다시 돌려도 중복 적재되지 않는다
        s2 = ingest.ingest_all(self.conn, self.root)
        self.assertEqual((s2["added"], s2["already"]), (0, 3))
        self.assertEqual(ingest.last_result(self.conn)["already"], 3)

    def test_agent_auto_created_and_reused(self):
        self.conn.execute("INSERT INTO agents(name, sync_dir) VALUES ('박지우', '박지우')")
        write_wav(self.root / "박지우" / "01012345678_20261012_090000.wav", 1)
        write_wav(self.root / "신규" / "01012345678_20261012_090000.wav", 1)
        ingest.ingest_all(self.conn, self.root)
        names = [r[0] for r in self.conn.execute("SELECT name FROM agents ORDER BY id")]
        self.assertEqual(names, ["박지우", "신규"])

    def test_contract_folder(self):
        f = self.root / "김민지" / "01011112222_20261012_090000.wav"
        write_wav(f, 3)
        write_wav(self.root / "김민지" / "계약완료" / "01033334444_20261012_100000.wav", 3)
        ingest.ingest_all(self.conn, self.root)
        self.assertEqual([r["contracted"] for r in self.calls()], [0, 1])
        # 이미 들어온 녹취를 계약완료 폴더로 옮기면 새로 넣지 않고 표시만
        dest = self.root / "김민지" / "계약완료" / f.name
        f.rename(dest)
        s = ingest.ingest_all(self.conn, self.root)
        self.assertEqual(s["added"], 0)
        self.assertEqual([r["contracted"] for r in self.calls()], [1, 1])

    def test_drive_contract(self):
        rec = {"id": "f1", "agent": "김민지", "name": "01011112222_20261012_090000.m4a", "size": 1, "durationMs": 1000}
        ingest.ingest_records(self.conn, [rec, {**rec, "id": "f2", "contract": True}])
        self.assertEqual([r["contracted"] for r in self.calls()], [0, 1])
        ingest.mark_contracts(self.conn, ["f1", 3, None])
        self.assertEqual([r["contracted"] for r in self.calls()], [1, 1])


if __name__ == "__main__":
    unittest.main()
