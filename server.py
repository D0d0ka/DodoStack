"""DodoStack main server: shared identity plus host-routed child services."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit
from http.cookies import SimpleCookie
import argparse
import hashlib
import hmac
import importlib
import os
import secrets
import sqlite3
import sys

from dodo.auth import AccountStore, Identity
from dodo import pages
from dodo.framework import Application, Response, redirect, request_from_handler
from dodo.web import WebApplication
from sqlalchemy.exc import IntegrityError

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
SERVICE_MODULES = {
    "todo": "subsites.todo.app",
    "doodle": "subsites.doodle.app",
    "dodobook": "subsites.dodobook.server",
    "dodotv": "subsites.DodoTV.run",
}
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
    if isinstance(module.app, Application):
        module.app.auth = identity
    service_apps[service_name] = module.app


identity.admin_tabs = {
    key: (app.admin.label, f"/admin/{key}{app.admin.entry}")
    for key, app in service_apps.items() if isinstance(app, WebApplication) and app.admin
}


def control_panel(request):
    user = identity.main_user(request)
    path = request.path
    admin = path.startswith("/admin")
    if not user:
        return redirect("/login")
    if admin and not user["is_admin"]:
        return Response.text("Administrator access required.", 403)
    token = hmac.new(accounts.secret, request.cookies[identity.main_session].encode(), hashlib.sha256).hexdigest()
    message = ""
    if request.method == "POST":
        if not hmac.compare_digest(token, request.value("csrf")):
            return Response.text("Invalid security token.", 403)
        try:
            if path == "/admin/role":
                enabled = request.value("enabled")
                if enabled not in {"0", "1"}:
                    raise ValueError("Choose a valid role.")
                accounts.set_admin(request.value("username"), enabled == "1")
                return redirect("/admin")
            if path == "/admin/password":
                accounts.reset_password(request.value("username"), request.value("password"))
                return redirect("/admin")
            if path == "/account/password":
                if not accounts.authenticate(user["username"], request.value("current")):
                    raise ValueError("The current password is incorrect.")
                accounts.reset_password(user["username"], request.value("password"))
                return redirect("/login")
        except ValueError as error:
            message = str(error)
    if admin:
        response = Response.html(pages.admin_accounts(accounts.list_accounts(), identity.admin_tabs, token, message))
    else:
        response = Response.html(pages.password_page(token, message))
    response.headers["Cache-Control"] = "no-store"
    return response


class DodoHandler(BaseHTTPRequestHandler):
    server_version = "DodoStack/1.0"

    def dispatch(self):
        target = urlsplit(self.path)
        host = self.headers.get("Host", "localhost").lower()
        hostname = host.partition(":")[0].rstrip(".")
        # Redact query strings, which can contain sign-in tickets.
        self.requestline = f"{self.command} {target.path} {self.request_version}"
        main = hostname in {"localhost", "127.0.0.1", "0.0.0.0"}
        service = next((name for name, address in services.items() if hostname == address), None)
        app = service_apps.get(service)
        parts = target.path.split("/", 3)
        if main and len(parts) >= 3 and parts[1] == "admin" and parts[2] in identity.admin_tabs:
            service = parts[2]
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get(identity.main_session)
            user = identity.session_user(session.value if session else None, "server")
            if not user:
                response = redirect("/login")
            elif not user["is_admin"]:
                response = Response.text("Administrator access required.", 403)
            elif len(parts) == 3 or not parts[3]:
                response = redirect(identity.admin_tabs[service][1])
            else:
                return self.serve_web(service_apps[service], service, "/" + parts[3], target, user, admin=True)
            return self.send_result(response)
        if isinstance(app, WebApplication) and not target.path.startswith("/_auth/"):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get("dodo_session")
            user = identity.get_service_user(service, session.value if session else None)
            return self.serve_web(app, service, target.path, target, user)
        request = request_from_handler(self)
        if main:
            if request.path.startswith("/static/"):
                response = workspace.dispatch(request)
            elif request.path in {"/admin", "/admin/role", "/admin/password", "/account/password"}:
                response = control_panel(request)
            else:
                response = identity.handle_main(request)
        elif service:
            response = identity.handle_service(service, request)
            if response is None:
                response = app.dispatch(request) if isinstance(app, Application) else Response.text("Not found", 404)
        else:
            response = Response.text("Unknown DodoStack service", 404)
        self.send_result(response, request.response_cookies)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = dispatch

    def serve_web(self, app, service, path, target, user, *, admin=False):
        def decode(value):
            return unquote_to_bytes(value).decode("latin1")

        environ = {
            "REQUEST_METHOD": self.command, "SCRIPT_NAME": "",
            "PATH_INFO": decode(target.path), "QUERY_STRING": target.query,
            "SERVER_NAME": self.server.server_name, "SERVER_PORT": str(self.server.server_port),
            "SERVER_PROTOCOL": self.request_version, "REMOTE_ADDR": self.client_address[0],
            "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
            "wsgi.input": self.rfile, "wsgi.errors": sys.stderr,
            "wsgi.multithread": True, "wsgi.multiprocess": False, "wsgi.run_once": False,
        }
        for key, value in self.headers.items():
            key = key.upper().replace("-", "_")
            environ[key if key in {"CONTENT_TYPE", "CONTENT_LENGTH"} else "HTTP_" + key] = value

        def start_response(status, headers):
            self.send_response(int(status.split()[0]))
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return self.wfile.write

        chunks = app.serve(environ, start_response, path=unquote_to_bytes(path).decode("utf-8", "replace"),
                           identity=identity, user=user, service=service, admin=admin)
        try:
            for chunk in chunks:
                if self.command != "HEAD":
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if hasattr(chunks, "close"):
                chunks.close()

    def send_result(self, response, cookies=()):
        self.send_response(response.status)
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                for cookie in value.split("\n"):
                    self.send_header("Set-Cookie", cookie)
            else:
                self.send_header(key, value)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="Run or configure DodoStack")
    commands = parser.add_subparsers(dest="command")
    role = commands.add_parser("set-admin", help="Set a shared account's admin role")
    role.add_argument("username")
    role.add_argument("--remove", action="store_true")
    link = commands.add_parser("link-profile", help="Link a legacy profile to a shared account")
    link.add_argument("service", choices=("dodobook", "dodotv"))
    link.add_argument("username")
    link.add_argument("profile_id", type=int)
    commands.add_parser("seed-tv-demo", help="Generate the Dodo TV demo catalog")
    args = parser.parse_args()
    if args.command == "seed-tv-demo":
        from subsites.DodoTV.app.demo import seed_demo
        with service_apps["dodotv"].app_context():
            summary = seed_demo()
        print(f"Demo catalog has {summary['total_movies']} movies and {summary['total_shows']} shows.")
        return
    if args.command == "set-admin":
        try:
            accounts.set_admin(args.username, not args.remove)
            print(f"{args.username}: {'member' if args.remove else 'administrator'}")
        except ValueError as error:
            parser.error(str(error))
        return
    if args.command == "link-profile":
        with accounts.connect() as db:
            account = db.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
        if not account:
            parser.error("Unknown DodoStack account.")
        app = service_apps[args.service]
        with app.app_context():
            try:
                getattr(app, "profile_links", app.profile_loader).link(account["id"], args.profile_id)
            except (ValueError, IntegrityError):
                parser.error("Profile missing or already linked. Use an unlinked account and profile.")
        return
    server = ThreadingHTTPServer((os.environ.get("DODO_HOST", "127.0.0.1"), PORT), DodoHandler)
    print(f"DodoStack is running at http://localhost:{PORT}")
    for name, host in services.items():
        print(f"  {name.title()}: http://{host}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DodoStack...")
        server.server_close()


if __name__ == "__main__":
    main()
