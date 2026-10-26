import json
import tempfile
import threading
import unittest
from unittest import mock
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from cti import auth
from cti.app import Config, make_handler
from cti.db import connect
from tests.helpers import write_wav


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cfg = Config(root / "t.db", root / "sync", root / "backup", today=lambda: date(2026, 10, 12))
        conn = connect(cfg.db_path)
        conn.execute("INSERT INTO agents(id, name) VALUES (1, '가')")
        conn.execute("INSERT INTO calls(agent_id, customer_key, started_at, duration_sec) "
                     "VALUES (1, 'A', '2026-10-12 10:00:00', 60)")
        conn.commit()
        auth.create_user(conn, "admin", "pw-admin", "admin")
        auth.create_user(conn, "viewer", "pw-viewer", "viewer")
        conn.close()
        write_wav(cfg.sync_root / "가" / "01012345678_20261011_110000.wav", 30)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, token=None):
        c = HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Cookie"] = f"cti_session={token}"
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data = r.read()
        cookie = r.getheader("Set-Cookie")
        c.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        return r.status, data, cookie

    def login(self, user, pw):
        status, _, cookie = self.req("POST", "/api/login", {"username": user, "password": pw})
        self.assertEqual(status, 200)
        return cookie.split(";")[0].split("=", 1)[1]

    def test_requires_login(self):
        self.assertEqual(self.req("GET", "/api/dashboard")[0], 401)

    def test_bad_login(self):
        self.assertEqual(self.req("POST", "/api/login", {"username": "admin", "password": "x"})[0], 401)
        auth._fails.clear()

    def test_login_lockout(self):
        try:
            for _ in range(auth.MAX_FAILS):
                self.assertEqual(self.req("POST", "/api/login", {"username": "viewer", "password": "x"})[0], 401)
            # 잠긴 뒤에는 맞는 비밀번호도 거부
            self.assertEqual(self.req("POST", "/api/login", {"username": "viewer", "password": "pw-viewer"})[0], 429)
        finally:
            auth._fails.clear()

    def test_dashboard(self):
        t = self.login("viewer", "pw-viewer")
        status, d, _ = self.req("GET", "/api/dashboard?preset=today", token=t)
        self.assertEqual(status, 200)
        self.assertEqual(d["kpis"]["total_calls"]["value"], 1)
        self.assertEqual(d["prev_period"]["start"], "2026-10-11")

    def test_bad_params(self):
        t = self.login("viewer", "pw-viewer")
        self.assertEqual(self.req("GET", "/api/dashboard?preset=nope", token=t)[0], 400)
        self.assertEqual(self.req("GET", "/api/dashboard?preset=custom&start=x&end=y", token=t)[0], 400)
        self.assertEqual(self.req("GET", "/api/dashboard?agent_id=abc", token=t)[0], 400)

    def test_analysis(self):
        t = self.login("viewer", "pw-viewer")
        status, d, _ = self.req("GET", "/api/analysis?preset=this_month", token=t)
        self.assertEqual(status, 200, d)
        self.assertIn("insights", d)
        self.assertNotIn("touch", self.req("GET", "/api/dashboard?preset=today", token=t)[1])

    def test_viewer_cannot_see_or_run_backup(self):
        t = self.login("viewer", "pw-viewer")
        status, d, _ = self.req("GET", "/api/agents/1?preset=today", token=t)
        self.assertEqual(status, 200)
        self.assertNotIn("sync_folder", d)
        self.assertNotIn("backups", d)
        self.assertEqual(self.req("POST", "/api/agents/1/backup", {}, token=t)[0], 403)

    def test_admin_backup_flow(self):
        t = self.login("admin", "pw-admin")
        status, d, _ = self.req("GET", "/api/agents/1?preset=today", token=t)
        self.assertEqual(d["sync_folder"]["file_count"], 1)
        status, r, _ = self.req("POST", "/api/agents/1/backup", {}, token=t)
        self.assertEqual(status, 200, r)
        self.assertEqual(r["backup"]["ingested"], 1)
        _, d, _ = self.req("GET", "/api/agents/1?preset=today", token=t)
        self.assertIn("010-1234-5678", [c["customer_masked"] for c in d["recent_calls"]])  # 관리자는 전체 번호
        _, d, _ = self.req("GET", "/api/agents/1?preset=today", token=self.login("viewer", "pw-viewer"))
        self.assertIn("010-****-5678", [c["customer_masked"] for c in d["recent_calls"]])  # 뷰어는 가린 번호
        self.assertEqual(r["sync_folder"]["file_count"], 0)
        self.assertEqual(len(r["backups"]), 1)
        # 다시 실행하면 빈 폴더라 409
        self.assertEqual(self.req("POST", "/api/agents/1/backup", {}, token=t)[0], 409)

    def test_settings(self):
        v = self.login("viewer", "pw-viewer")
        status, s, _ = self.req("GET", "/api/settings", token=v)
        self.assertEqual((status, s["min_call_sec"]), (200, 10))
        self.assertEqual(self.req("POST", "/api/settings", {"min_call_sec": 30}, token=v)[0], 403)

        a = self.login("admin", "pw-admin")
        for bad in (-1, 601, "30", 1.5, True, None):
            self.assertEqual(self.req("POST", "/api/settings", {"min_call_sec": bad}, token=a)[0], 400, bad)
        self.assertEqual(self.req("POST", "/api/settings", {}, token=a)[0], 400)
        self.assertEqual(self.req("POST", "/api/settings", {"fallback_kbps": 4}, token=a)[0], 400)
        self.assertNotIn("customer_hash_key", s)
        try:
            status, s, _ = self.req("POST", "/api/settings", {"min_call_sec": 120}, token=a)
            self.assertEqual((status, s["min_call_sec"]), (200, 120))
            # 기준 변경이 대시보드에 반영된다: 60초 통화가 짧은 통화가 됨
            _, d, _ = self.req("GET", "/api/dashboard?preset=today", token=a)
            self.assertEqual(d["min_call_sec"], 120)
            self.assertEqual(d["kpis"]["short_ratio"]["value"], 1.0)
            self.assertIsNotNone(d["kpis"]["att_sec"]["value"])  # 짧은 통화도 평균에 포함
        finally:
            self.req("POST", "/api/settings", {"min_call_sec": 10}, token=a)

    def test_ingest_endpoint(self):
        v = self.login("viewer", "pw-viewer")
        self.assertEqual(self.req("POST", "/api/ingest", {}, token=v)[0], 403)
        a = self.login("admin", "pw-admin")
        status, s, _ = self.req("POST", "/api/ingest", {}, token=a)
        self.assertEqual(status, 200)
        self.assertIsNotNone(s["last_ingest"]["at"])

    def test_drive_push_and_calls(self):
        a = self.login("admin", "pw-admin")
        status, s, _ = self.req("GET", "/api/drive/script", token=a)
        self.assertEqual(status, 200)
        token = s["script"].split("const CTI_TOKEN = '")[1].split("'")[0]
        self.assertIn("http://", s["script"])
        v = self.login("viewer", "pw-viewer")
        self.assertEqual(self.req("GET", "/api/drive/script", token=v)[0], 403)

        body = {"first": True, "files": [{"id": "drvA", "agent": "드라이브에이전트", "size": 1000, "durationMs": 61000,
                                          "name": "통화 녹음 010-5555-6666_261012_150000.mp4"}]}
        c = HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/drive/push", json.dumps(body), {"Content-Type": "application/json", "X-CTI-Token": "nope"})
        self.assertEqual(c.getresponse().status, 401)
        c.close()
        c = HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/drive/push", json.dumps(body), {"Content-Type": "application/json", "X-CTI-Token": token})
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())["added"]), (200, 1))
        c.close()

        status, res, _ = self.req("GET", "/api/calls?preset=all&q=010-5555-6666", token=v)
        self.assertEqual((status, res["total"]), (200, 1))
        call_id = res["items"][0]["id"]
        status, d, _ = self.req(f"GET", f"/api/calls/{call_id}", token=v)
        self.assertEqual(d["play"]["type"], "drive")
        self.assertEqual(d["duration_sec"], 61)
        self.assertEqual(self.req("GET", "/api/calls?preset=all&q=123", token=v)[0], 400)

    def test_drive_status_uploads_and_slack(self):
        a = self.login("admin", "pw-admin")
        v = self.login("viewer", "pw-viewer")
        token = self.req("GET", "/api/drive/script", token=a)[1]["script"].split("const CTI_TOKEN = '")[1].split("'")[0]

        def script(path, body):
            c = HTTPConnection("127.0.0.1", self.port)
            c.request("POST", path, json.dumps(body), {"Content-Type": "application/json", "X-CTI-Token": token})
            r = c.getresponse(); data = json.loads(r.read()); c.close()
            return r.status, data

        # 스크립트가 회차 결과를 알리면 연결 상태가 바뀐다
        self.assertEqual(script("/api/drive/run", {"ok": False, "error": "권한 없음"})[0], 200)
        st = self.req("GET", "/api/drive/status", token=v)[1]
        self.assertEqual((st["state"], st["last_run"]["error"]), ("error", "권한 없음"))
        script("/api/drive/run", {"ok": True, "added": 2})
        self.assertEqual(self.req("GET", "/api/drive/status", token=v)[1]["state"], "ok")

        # 녹취 없는 에이전트 폴더도 목록에 들어오고, 오늘 안 올린 사람으로 보인다
        script("/api/drive/push", {"first": True, "files": [], "folders": ["빈폴더에이전트"]})
        u = self.req("GET", "/api/uploads", token=a)[1]
        rows = {r["name"]: r for r in u["agents"]}
        self.assertTrue(rows["빈폴더에이전트"]["missing"])
        self.assertFalse(rows["가"]["missing"])  # today=10-12 에 통화가 있음
        self.assertEqual(u["slack"], {"token_set": False, "token_source": None, "target": ""})
        self.assertNotIn("slack", self.req("GET", "/api/uploads", token=v)[1])

        # 슬랙: 관리자만, 설정 없으면 알려 줌, 토큰은 응답에 안 나옴
        self.assertEqual(self.req("POST", "/api/slack/config", {"target": "#x"}, token=v)[0], 403)
        self.assertEqual(self.req("POST", "/api/slack/notify-missing", {}, token=v)[0], 403)
        self.assertEqual(self.req("POST", "/api/slack/notify-missing", {}, token=a)[0], 502)
        self.assertEqual(self.req("POST", "/api/slack/config", {"token": "bad"}, token=a)[0], 400)
        status, c, _ = self.req("POST", "/api/slack/config",
                                {"token": "xoxb-1-2-abcdefghijk", "target": "seongjoo@getmiso.com"}, token=a)
        self.assertEqual((status, c["token_set"]), (200, True))
        self.assertNotIn("xoxb", json.dumps(self.req("GET", "/api/uploads", token=a)[1]))
        from cti import slack
        with mock.patch.object(slack, "_call", side_effect=lambda t, m, p, post=False:
                               {"ok": True, "user": {"id": "U1"}}):
            status, r, _ = self.req("POST", "/api/slack/notify-missing", {}, token=a)
        self.assertEqual((status, r["sent_to"]), (200, "seongjoo@getmiso.com"))
        self.assertGreaterEqual(r["missing"], 1)

    def test_static_and_traversal(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.req("GET", "/static/../cti/app.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
