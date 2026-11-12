"""DodoStack main server: shared identity plus host-routed child services."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib
import os
import secrets
import sqlite3
import sys

from dodo.auth import AccountStore, Identity
from dodo.framework import Application, Response, request_from_handler

BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.environ.get("DODO_PORT", "8000"))
INSTANCE = BASE_DIR / "instance"
INSTANCE.mkdir(exist_ok=True)
secret_path = INSTANCE / "server.key"
if os.environ.get("DODO_SECRET_KEY"):
    secret = os.environ["DODO_SECRET_KEY"]
else:
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
        secret_path.chmod(0o600)
    secret = secret_path.read_text(encoding="utf-8").strip()

accounts = AccountStore(INSTANCE / "accounts.sqlite3", secret)
SERVICE_MODULES = {"todo": "subsites.todo.app"}
services = {name: f"{name}.localhost" for name in SERVICE_MODULES}
identity = Identity(accounts, services, PORT)
workspace = Application("workspace", static_dir=BASE_DIR / "static")


def migrate_todo_accounts():
    """Move existing Todo credentials into the shared account database."""
    legacy = BASE_DIR / "subsites" / "todo" / "instance" / "todos.sqlite3"
    if not legacy.exists():
        return
    source = sqlite3.connect(legacy)
    source.row_factory = sqlite3.Row
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        source.close()
        return
    target = accounts.connect()
    target.execute("CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    if target.execute("SELECT 1 FROM schema_migrations WHERE name='legacy_todo_accounts'").fetchone():
        target.close()
        source.close()
        return
    mapping = {}
    for old in source.execute("SELECT id,username,password_hash,created_at FROM users ORDER BY id"):
        existing = target.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (old["username"],)).fetchone()
        if existing:
            new_id = existing["id"]
        else:
            cursor = target.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)", (old["username"], old["password_hash"], old["created_at"]))
            new_id = cursor.lastrowid
        mapping[old["id"]] = new_id
    target.commit()
    if "todos" in tables:
        todo_columns = {row[1] for row in source.execute("PRAGMA table_info(todos)")}
        if "user_id" in todo_columns:
            source.execute("CREATE TEMP TABLE IF NOT EXISTS user_id_migration(old_id INTEGER PRIMARY KEY,new_id INTEGER NOT NULL)")
            source.execute("DELETE FROM user_id_migration")
            source.executemany("INSERT INTO user_id_migration(old_id,new_id) VALUES (?,?)", mapping.items())
            source.execute("""UPDATE todos SET user_id=(SELECT new_id FROM user_id_migration WHERE old_id=todos.user_id)
                WHERE user_id IN (SELECT old_id FROM user_id_migration)""")
            source.commit()
    target.execute("INSERT OR IGNORE INTO schema_migrations(name) VALUES ('legacy_todo_accounts')")
    target.commit()
    target.close()
    source.close()


migrate_todo_accounts()
sys.path.insert(0, str(BASE_DIR))
service_apps = {}
for service_name, module_name in SERVICE_MODULES.items():
    module = importlib.import_module(module_name)
    module.app.auth = identity
    service_apps[service_name] = module.app


class DodoHandler(BaseHTTPRequestHandler):
    server_version = "DodoStack/1.0"

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        request = request_from_handler(self)
        # Callback URLs contain short-lived bearer tickets; never write them to access logs.
        request_line = self.requestline.split(" ", 2)
        if len(request_line) == 3:
            request_line[1] = request_line[1].split("?", 1)[0]
            self.requestline = " ".join(request_line)
        host = request.headers.get("host", "localhost").split(":", 1)[0].lower().rstrip(".")
        if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
            response = (workspace.dispatch(request) if request.path.startswith("/static/")
                        else identity.handle_main(request))
        else:
            service = next((key for key, service_host in services.items() if host == service_host), None)
            if service is None:
                response = Response.text("Unknown DodoStack service", 404)
            else:
                response = identity.handle_service(service, request)
                if response is None:
                    response = service_apps[service].dispatch(request)
        self.send_response(response.status)
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                for cookie in value.split("\n"):
                    self.send_header("Set-Cookie", cookie)
            else:
                self.send_header(key, value)
        for cookie in request.response_cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((os.environ.get("DODO_HOST", "127.0.0.1"), PORT), DodoHandler)
    print(f"DodoStack is running at http://localhost:{PORT}")
    for name, host in services.items():
        print(f"  {name.title()}: http://{host}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DodoStack...")
        server.server_close()
