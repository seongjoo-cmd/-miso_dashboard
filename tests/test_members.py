"""회원가입(@getmiso.com 만, 슬랙 인증번호)·비밀번호 바꾸기·관리자 회원 관리."""
import json
import re
import tempfile
import threading
import unittest
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cti import auth, signup, slack
from cti.app import Config, make_handler
from cti.db import connect


class MembersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        cfg = Config(root / "t.db", root / "sync", root / "backup", today=lambda: date(2026, 10, 13))
        conn = connect(cfg.db_path)
        auth.create_user(conn, "admin", "pw-admin", "admin")
        conn.close()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cfg))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        auth._fails.clear()
        self.sent = {}  # 이메일 -> 슬랙으로 보낸 인증번호
        patcher = mock.patch.object(slack, "dm", side_effect=lambda conn, email, text:
                                    self.sent.__setitem__(email, re.search(r"\d{6}", text).group()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, token=None):
        c = HTTPConnection("127.0.0.1", self.port)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            headers["Cookie"] = f"cti_session={token}"
        c.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = c.getresponse()
        data, cookie = r.read(), r.getheader("Set-Cookie")
        c.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        token = cookie.split(";")[0].split("=", 1)[1] if cookie else None
        return r.status, data, token

    def send_code(self, email):
        return self.req("POST", "/api/signup/code", {"username": email})

    def signup(self, email, pw="password1"):
        status, data, _ = self.send_code(email)
        if status != 200:
            return status, data, None
        return self.req("POST", "/api/signup", {"username": email, "password": pw, "code": self.sent[data["sent_to"]]})

    def users(self, token):
        return {u["username"]: u for u in self.req("GET", "/api/users", token=token)[1]}

    def test_signup_only_company_email(self):
        for bad in ("a@gmail.com", "a@getmiso.com.evil.com", "a@xgetmiso.com", "admin"):
            self.assertEqual(self.signup(bad)[0], 400, bad)
        self.assertEqual(self.send_code("Kim@GetMiso.com")[0], 200)
        code = self.sent["kim@getmiso.com"]
        self.assertEqual(self.req("POST", "/api/signup", {"username": "kim@getmiso.com", "password": "short", "code": code})[0], 400)
        status, user, token = self.req("POST", "/api/signup", {"username": "Kim@GetMiso.com", "password": "password1", "code": code})
        self.assertEqual(status, 200)
        self.assertEqual((user["username"], user["role"]), ("kim@getmiso.com", "viewer"))
        self.assertTrue(token)
        self.assertEqual(self.signup("kim@getmiso.com")[0], 400)  # 중복
        self.assertEqual(self.req("POST", "/api/login", {"username": "KIM@getmiso.com", "password": "password1"})[0], 200)

    def test_code_required_and_checked(self):
        email = "kim@getmiso.com"
        self.assertEqual(self.req("POST", "/api/signup", {"username": email, "password": "password1", "code": "123456"})[0], 400)
        self.assertEqual(self.send_code(email)[0], 200)
        self.assertEqual(self.send_code(email)[0], 400)  # 1분 안에 다시 받기 막음
        wrong = "000000" if self.sent[email] != "000000" else "111111"
        for _ in range(signup.MAX_TRIES):
            self.assertEqual(self.req("POST", "/api/signup", {"username": email, "password": "password1", "code": wrong})[0], 400)
        # 다 틀리면 맞는 번호도 안 받는다
        status, data, _ = self.req("POST", "/api/signup", {"username": email, "password": "password1", "code": self.sent[email]})
        self.assertEqual(status, 400)
        self.assertIn("만료", data["error"])

    def test_slack_error_shown(self):
        with mock.patch.object(slack, "dm", side_effect=slack.SlackError("슬랙 계정을 찾지 못했어요")):
            status, data, _ = self.send_code("kim@getmiso.com")
        self.assertEqual((status, data["error"]), (400, "슬랙 계정을 찾지 못했어요"))

    def test_owner_signs_up_as_admin(self):
        _, user, _ = self.signup(auth.OWNER)
        self.assertEqual(user["role"], "admin")

    def test_member_cannot_manage(self):
        _, _, token = self.signup("kim@getmiso.com")
        self.assertEqual(self.req("GET", "/api/users", token=token)[0], 403)

    def test_admin_manages_members(self):
        _, _, owner = self.signup(auth.OWNER)
        _, kim, _ = self.signup("kim@getmiso.com")
        _, lee, lee_token = self.signup("lee@getmiso.com")
        # 관리자로 올리면 그 사람도 회원 관리 가능
        self.assertEqual(self.req("POST", f"/api/users/{kim['id']}/role", {"role": "admin"}, owner)[0], 200)
        _, _, kim_token = self.req("POST", "/api/login", {"username": "kim@getmiso.com", "password": "password1"})
        self.assertEqual(self.users(kim_token)["kim@getmiso.com"]["role"], "admin")
        # OWNER 와 자기 자신은 바꿀 수 없다
        owner_id = self.users(owner)[auth.OWNER]["id"]
        for action in ("role", "reset", "delete"):
            self.assertEqual(self.req("POST", f"/api/users/{owner_id}/{action}", {"role": "viewer"}, kim_token)[0], 400)
        self.assertEqual(self.req("POST", f"/api/users/{kim['id']}/delete", {}, kim_token)[0], 400)
        # 비밀번호 초기화: 세션이 끊기고 임시 비밀번호로 로그인
        status, r, _ = self.req("POST", f"/api/users/{lee['id']}/reset", {}, kim_token)
        self.assertEqual(status, 200)
        self.assertEqual(self.req("GET", "/api/me", token=lee_token)[0], 401)
        self.assertEqual(self.req("POST", "/api/login", {"username": "lee@getmiso.com", "password": r["temp_password"]})[0], 200)
        # 탈퇴
        self.assertEqual(self.req("POST", f"/api/users/{lee['id']}/delete", {}, kim_token)[0], 200)
        self.assertNotIn("lee@getmiso.com", self.users(owner))

    def test_change_own_password(self):
        _, _, token = self.signup("kim@getmiso.com")
        self.assertEqual(self.req("POST", "/api/me/password", {"current": "wrong", "new": "newpass99"}, token)[0], 400)
        self.assertEqual(self.req("POST", "/api/me/password", {"current": "password1", "new": "newpass99"}, token)[0], 200)
        self.assertEqual(self.req("POST", "/api/login", {"username": "kim@getmiso.com", "password": "newpass99"})[0], 200)


if __name__ == "__main__":
    unittest.main()
