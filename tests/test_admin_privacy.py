"""HTTP regressions using a temporary workspace and fresh service databases.

Run with .venv/bin/python -m unittest discover -s tests.
"""
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


class AdminPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="dodo-privacy-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name) / "workspace"
        shutil.copytree(Path(__file__).resolve().parents[1], cls.root,
                        ignore=shutil.ignore_patterns(
                            ".git", ".venv", "__pycache__", "instance", ".env",
                            "*.db*", "*.sqlite*", "*.key", "*.pem", "settings.json"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        env = dict(os.environ, DODO_PORT=str(cls.port), DODO_HTTPS="false",
                   DODO_PRIVATE="false", DODO_WORKSPACE_HOSTS="",
                   DODO_SECRET_KEY="isolated-privacy-test-secret")
        seed = '''
import json, time
from dodo.auth import AccountStore
from subsites.doodle import database as db
accounts = AccountStore("instance/accounts.sqlite3", "isolated-privacy-test-secret")
tokens = {}
for name in ("admin", "member", "other"):
    user = accounts.register(name, "test-password", name + "@example.com",
        birth_date="2000-01-01", first_name="Test", last_name="Account",
        dmail_local=name, approved=True)
    if name == "admin":
        accounts.set_admin(name, True)
    tokens[name] = {aud: accounts.issue(dict(sub=user["id"], name=name, aud=aud,
        ver=accounts.session_version(user["id"]), exp=int(time.time()) + 3600))
        for aud in ("server", "doodle")}
assert "email" not in accounts.list_accounts()[0].keys()
db.init_db()
for host in ("hidden.example", "www.hidden.example", "hidden.example.evil", "public.example"):
    url = "https://" + host + "/"
    page = db.upsert_page(url, "privacyneedle pictures " + host, "privacyneedle pictures content " + host)
    db.add_recommended_entry("privacyneedle", url, "privacyneedle")
    db.replace_media_assets(page, [dict(kind="pictures", url=url + "image.png", title="privacyneedle")])
page = db.upsert_page("https://public.example/assets", "privacyneedle", "privacyneedle")
db.replace_media_assets(page, [dict(kind="pictures", url="https://cdn.hidden.example/secret.png", title="privacyneedle")])
assert db.add_hidden_domain("https://HIDDEN.example./path")
assert db.get_hidden_domains() == ["hidden.example"]
assert db.remove_hidden_domain("https://hidden.example/path")
db.add_hidden_domain("hidden.example")
print(json.dumps(tokens))
'''
        cls.tokens = json.loads(subprocess.check_output(
            [sys.executable, "-c", seed], cwd=cls.root, env=env, text=True))
        cls.log = open(Path(cls.temp.name) / "server.log", "w+")
        cls.addClassCleanup(cls.log.close)
        cls.process = subprocess.Popen([sys.executable, "server.py"], cwd=cls.root,
                                       env=env, stdout=cls.log, stderr=cls.log)
        cls.addClassCleanup(cls.stop_server)
        for _ in range(100):
            try:
                if cls.get("/")[0] == 200:
                    break
            except OSError:
                pass
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError(cls.log.read())
            time.sleep(0.1)
        else:
            raise RuntimeError("Isolated workspace did not start")

    @classmethod
    def stop_server(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)

    @classmethod
    def get(cls, path, user=None, service=False):
        headers = {"Host": ("doodle.localhost" if service else "localhost") + f":{cls.port}"}
        if user:
            audience = "doodle" if service else "server"
            cookie = "dodo_session" if service else "dodo_main_session"
            headers["Cookie"] = cookie + "=" + cls.tokens[user][audience]
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def test_hidden_results_require_admin_on_both_mounts(self):
        for endpoint in ("search", "recommended", "search/media?kind=pictures"):
            path = "/api/" + endpoint + ("&" if "?" in endpoint else "?") + "q=privacyneedle"
            for user in ("member", "other", "admin"):
                with self.subTest(endpoint=endpoint, user=user):
                    status, body = self.get(path, user, service=True)
                    self.assertEqual(status, 200)
                    data = json.loads(body)
                    urls = [item["url"] for item in data["results"]]
                    self.assertIn("https://public.example/" + ("image.png" if "media" in endpoint else ""), urls)
                    self.assertEqual(any(url.startswith("https://hidden.example/") for url in urls), user == "admin")
                    self.assertEqual(any(url.startswith("https://www.hidden.example/") for url in urls), user == "admin")
                    self.assertEqual(any("cdn.hidden.example" in url for url in urls), user == "admin" and "media" in endpoint)
                    if user != "admin":
                        self.assertNotIn("hidden_domains", body)
                    if endpoint == "search":
                        self.assertEqual(data["total"], 5 if user == "admin" else 3)
            self.assertEqual(self.get(path, service=True)[0], 401)
            self.assertEqual(self.get("/admin/doodle" + path, "member")[0], 403)
            status, body = self.get("/admin/doodle" + path, "admin")
            self.assertEqual(status, 200)
            self.assertIn("https://hidden.example/", body)

    def test_admin_accounts_exclude_real_emails(self):
        status, body = self.get("/admin", "admin")
        self.assertEqual(status, 200)
        for user in self.tokens:
            self.assertNotIn(user + "@example.com", body)
        self.assertEqual(self.get("/admin", "member")[0], 403)
        self.assertEqual(self.get("/admin")[0], 303)
        # Each account can still manage only its own email settings.
        for user in self.tokens:
            status, body = self.get("/account/email", user)
            self.assertEqual(status, 200)
            self.assertIn(user + "@example.com", body)
            for other in self.tokens.keys() - {user}:
                self.assertNotIn(other + "@example.com", body)


if __name__ == "__main__":
    unittest.main()
