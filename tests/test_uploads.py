import json
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from cti import ingest, slack, uploads
from cti.db import connect, set_setting

TODAY = date(2026, 10, 13)


def make_db():
    conn = connect(":memory:")
    conn.executemany("INSERT INTO agents(id, name) VALUES (?, ?)", [(1, "가"), (2, "나"), (3, "다")])
    conn.executemany(
        "INSERT INTO calls(agent_id, started_at, duration_sec, uploaded_at) VALUES (?, ?, ?, ?)",
        [
            (1, "2026-10-13 09:00:00", 60, "2026-10-13 09:05:00"),
            (1, "2026-10-13 10:00:00", 60, "2026-10-13 10:02:00"),
            (2, "2026-10-12 17:00:00", 60, "2026-10-12 18:30:00"),
            (2, "2026-10-12 16:00:00", 60, None),  # 이 기능 전에 들어온 녹취: 통화 시각으로 대신
        ])
    conn.commit()
    return conn


class AgentUploadsTest(unittest.TestCase):
    def test_last_upload_and_missing(self):
        rows = {r["name"]: r for r in uploads.agent_uploads(make_db(), TODAY)}
        self.assertEqual((rows["가"]["today"], rows["가"]["missing"]), (2, False))
        self.assertEqual(rows["가"]["last_upload"], "2026-10-13 10:02:00")
        self.assertEqual((rows["나"]["last_upload"], rows["나"]["missing"]), ("2026-10-12 18:30:00", True))
        self.assertEqual((rows["다"]["last_upload"], rows["다"]["missing"]), (None, True))  # 폴더만 있고 녹취 없음

    def test_missing_first(self):
        names = [r["name"] for r in uploads.agent_uploads(make_db(), TODAY)]
        self.assertEqual(names, ["다", "나", "가"])  # 올린 적 없음 → 오래된 순 → 올린 사람

    def test_inactive_agents_hidden(self):
        conn = make_db()
        conn.execute("UPDATE agents SET active = 0 WHERE id = 3")
        self.assertNotIn("다", [r["name"] for r in uploads.agent_uploads(conn, TODAY)])


class DriveStatusTest(unittest.TestCase):
    def test_states(self):
        conn = connect(":memory:")
        self.assertEqual(uploads.drive_status(conn)["state"], "never")
        uploads.touch(conn)
        self.assertEqual(uploads.drive_status(conn)["state"], "ok")
        later = datetime.now() + timedelta(minutes=uploads.STALE_MIN + 1)
        self.assertEqual(uploads.drive_status(conn, later)["state"], "stale")
        uploads.record_run(conn, {"ok": False, "error": "Exception: 권한 없음"})
        st = uploads.drive_status(conn)
        self.assertEqual((st["state"], st["last_run"]["error"]), ("error", "Exception: 권한 없음"))
        self.assertIsNone(st["last_ok"])
        uploads.record_run(conn, {"ok": True, "added": 3})
        st = uploads.drive_status(conn)
        self.assertEqual((st["state"], st["last_run"]["added"]), ("ok", 3))
        self.assertIsNotNone(st["last_ok"])


class IngestUploadedAtTest(unittest.TestCase):
    def test_local_time(self):
        # cti 가 TZ 를 Asia/Seoul 로 맞춘다(UTC+9)
        self.assertEqual(ingest.local_time("2026-10-13T00:12:33.000Z"), "2026-10-13 09:12:33")
        self.assertIsNone(ingest.local_time("nope"))
        self.assertIsNone(ingest.local_time(None))

    def test_records_store_and_backfill(self):
        conn = connect(":memory:")
        rec = {"id": "f1", "agent": "가", "name": "01012345678_20261013_090000.m4a", "size": 1000, "durationMs": 5000}
        ingest.ingest_records(conn, [rec])
        self.assertIsNone(conn.execute("SELECT uploaded_at FROM calls").fetchone()[0])
        ingest.ingest_records(conn, [{**rec, "created": "2026-10-13T00:12:33Z"}])  # 다시 받으면 채움
        self.assertEqual(conn.execute("SELECT uploaded_at FROM calls").fetchone()[0], "2026-10-13 09:12:33")
        rec2 = {**rec, "id": "f2", "name": "01012345678_20261013_100000.m4a", "created": "2026-10-13T01:00:00Z"}
        ingest.ingest_records(conn, [rec2])
        self.assertEqual(conn.execute("SELECT uploaded_at FROM calls WHERE drive_file_id = 'f2'").fetchone()[0],
                         "2026-10-13 10:00:00")

    def test_register_folders(self):
        conn = connect(":memory:")
        ingest.register_folders(conn, ["가", "_백업", " ", "나"])
        self.assertEqual([r[0] for r in conn.execute("SELECT name FROM agents ORDER BY name")], ["가", "나"])


class SlackTest(unittest.TestCase):
    def test_config_validation_hides_token(self):
        conn = connect(":memory:")
        with self.assertRaises(ValueError):
            slack.save_config(conn, {"token": "xoxp-user-token"})
        with self.assertRaises(ValueError):
            slack.save_config(conn, {"target": "아무개"})
        c = slack.save_config(conn, {"token": "xoxb-123-456-abcdefghij", "target": "seongjoo@getmiso.com"})
        self.assertEqual(c, {"token_set": True, "token_source": "db", "target": "seongjoo@getmiso.com"})
        self.assertNotIn("xoxb", json.dumps(slack.config(conn)))
        # 토큰 칸을 비워 저장해도 기존 토큰은 남는다
        self.assertTrue(slack.save_config(conn, {"token": "", "target": "#cs-team"})["token_set"])
        for ok in ("U012ABCDEF", "C0123456789"):
            slack.save_config(conn, {"target": ok})

    def test_env_token_wins(self):
        conn = connect(":memory:")
        with mock.patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-env-token-123456"}):
            self.assertEqual(slack.token(conn), ("xoxb-env-token-123456", "env"))
            self.assertEqual(slack.config(conn)["token_source"], "env")

    def test_message(self):
        agents = uploads.agent_uploads(make_db(), TODAY)
        text, blocks = slack.missing_message(agents, TODAY, datetime(2026, 10, 13, 14, 5), "https://x.example")
        self.assertIn("2명", text)
        self.assertIn("10월 13일(화) 14:05", text)
        body = blocks[1]["text"]["text"]
        self.assertIn("*나* — 마지막 업로드 어제 18:30", body)
        self.assertIn("*다* — 올린 적 없음", body)
        self.assertNotIn("가", body)
        self.assertIn("https://x.example", json.dumps(blocks))

    def test_all_uploaded_message(self):
        agents = [{"name": "가", "missing": False, "today": 4, "last_upload": "2026-10-13 09:00:00"}]
        text, _ = slack.missing_message(agents, TODAY, datetime(2026, 10, 13, 18, 0), None)
        self.assertIn("모든 상담원", text)

    def test_send_dm_by_email(self):
        conn = connect(":memory:")
        slack.save_config(conn, {"token": "xoxb-123-456-abcdefghij", "target": "seongjoo@getmiso.com"})
        calls = []

        def fake(tok, method, params, post=False):
            calls.append((method, params))
            return {"ok": True, "user": {"id": "U999"}} if method == "users.lookupByEmail" else {"ok": True}
        with mock.patch.object(slack, "_call", side_effect=fake):
            self.assertEqual(slack.send(conn, "t", []), {"sent_to": "seongjoo@getmiso.com"})
        self.assertEqual(calls[0], ("users.lookupByEmail", {"email": "seongjoo@getmiso.com"}))
        self.assertEqual((calls[1][0], calls[1][1]["channel"]), ("chat.postMessage", "U999"))

    def test_send_needs_config(self):
        conn = connect(":memory:")
        with self.assertRaises(slack.SlackError):
            slack.send(conn, "t", [])
        set_setting(conn, "slack_bot_token", "xoxb-123-456-abcdefghij")
        with self.assertRaisesRegex(slack.SlackError, "받는 곳"):
            slack.send(conn, "t", [])

    def test_slack_error_is_readable(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b'{"ok": false, "error": "missing_scope"}'
        with mock.patch("urllib.request.urlopen", return_value=resp):
            with self.assertRaisesRegex(slack.SlackError, "권한"):
                slack._call("xoxb-1", "chat.postMessage", {}, post=True)


if __name__ == "__main__":
    unittest.main()
