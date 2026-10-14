import json
import re
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cti import auth, slack
from cti.app import Config, make_handler


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Config(root / "t.db", root / "s", root / "b")))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.tmp.cleanup()

    def req(self, method, path, body=None):
        c = HTTPConnection("127.0.0.1", self.httpd.server_address[1])
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse(); data = json.loads(r.read() or b"{}"); cookie = r.getheader("Set-Cookie"); c.close()
        return r.status, data, cookie

    def test_first_run_needs_slack_signup(self):
        status, s, _ = self.req("GET", "/api/status")
        self.assertEqual((status, s["needs_setup"], s["storage"]), (200, True, "local"))
        # 인증 없이 관리자를 만드는 길은 없다
        self.assertEqual(self.req("POST", "/api/setup", {"username": "hacker", "password": "12345678"})[0], 401)
        sent = {}
        with mock.patch.object(slack, "dm", side_effect=lambda conn, email, text: sent.update(code=re.search(r"\d{6}", text).group())):
            self.assertEqual(self.req("POST", "/api/signup/code", {"username": auth.OWNER})[0], 200)
        status, u, cookie = self.req("POST", "/api/signup", {"username": auth.OWNER, "password": "miso1234", "code": sent["code"]})
        self.assertEqual((status, u["username"], u["role"]), (200, auth.OWNER, "admin"))
        self.assertIn("cti_session=", cookie)
        self.assertFalse(self.req("GET", "/api/status")[1]["needs_setup"])


if __name__ == "__main__":
    unittest.main()
