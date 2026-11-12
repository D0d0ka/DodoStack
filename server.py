"""DodoStack main server: shared identity plus host-routed child services."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlencode, urlsplit
from http.cookies import SimpleCookie
import argparse
import hashlib
import hmac
import importlib
from ipaddress import IPv4Address
import os
import re
import secrets
import sqlite3
import sys

from dodo.auth import AccountStore, Identity
from dodo import pages
from dodo.framework import Application, Response, redirect, request_from_handler
from dodo.web import WebApplication
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
PORT = int(os.environ.get("DODO_PORT", "8000"))
PRIVATE = os.environ.get("DODO_PRIVATE", "false").strip().lower() in {"true", "1", "yes", "on"}
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

accounts = AccountStore(
    INSTANCE / "accounts.sqlite3", secret,
    min_password_length=int(os.environ.get("DODO_MIN_PASSWORD_LENGTH", "8")),
)
SERVICE_MODULES = {
    "todo": "subsites.todo.app",
    "doodle": "subsites.doodle.app",
    "dodobook": "subsites.dodobook.server",
    "dodotv": "subsites.DodoTV.run",
    "dodokey": "subsites.dodokey.app",
}
workspace = Application("workspace", static_dir=BASE_DIR / "static")


BRAND_FILES = {
    "stack": BASE_DIR / "assets/server-icon.png",
    "todo": BASE_DIR / "subsites/todo/static/images/dodo_sign.png",
    "doodle": BASE_DIR / "subsites/doodle/templates/dodo.png",
    "dodobook": BASE_DIR / "subsites/dodobook/static/dodobook.png",
    "dodotv": BASE_DIR / "subsites/DodoTV/dodotv.png",
    "dodokey": BASE_DIR / "subsites/dodokey/static/dodokey.png",
}


def workspace_static(request):
    name = request.path.removeprefix("/static/branding/").removesuffix(".png")
    logo = BRAND_FILES.get(name) if request.path.startswith("/static/branding/") else None
    if logo:
        return Response(logo.read_bytes(), headers={"Content-Type": "image/png"})
    return workspace.dispatch(request)


sys.path.insert(0, str(BASE_DIR))
service_apps = {}
for service_name, module_name in SERVICE_MODULES.items():
    module = importlib.import_module(module_name)
    service_apps[service_name] = module.app


admin_tabs = {
    key: (app.admin.label, f"/admin/{key}{app.admin.entry}")
    for key, app in service_apps.items() if isinstance(app, WebApplication) and app.admin
}


def valid_hostname(host):
    return len(host) <= 253 and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    )


def host_routes(config):
    local_hosts = ("localhost", "127.0.0.1", "0.0.0.0")
    workspace_domains = {"localhost": "localhost"}
    for entry in config.lower().split(","):
        if not entry.strip():
            continue
        host, _, domain = (part.strip().rstrip(".") for part in entry.partition("="))
        domain = domain or host
        if not valid_hostname(host) or not valid_hostname(domain):
            raise ValueError("DODO_WORKSPACE_HOSTS expects hostnames or IPv4 addresses with a service domain")
        try:
            IPv4Address(host)
        except ValueError:
            if host.split(".")[-1].isdigit():
                raise ValueError(f"Invalid DODO_WORKSPACE_HOSTS address: {host}")
        if domain.split(".")[-1].isdigit():
            raise ValueError(f"Set a DNS service domain for {host} using {host}=example.home.arpa")
        if host in local_hosts or host in workspace_domains:
            raise ValueError(f"Duplicate or reserved DodoStack workspace hostname: {host}")
        workspace_domains[host] = domain

    routes = {host: None for host in (*local_hosts, *workspace_domains)}
    for host, domain in workspace_domains.items():
        services = {name: f"{name}.{domain}" for name in SERVICE_MODULES}
        identity = Identity(accounts, services, PORT, private=PRIVATE, workspace_host=host)
        identity.admin_tabs = admin_tabs
        routes[host] = (identity, None)
        for name, address in services.items():
            if address in routes or not valid_hostname(address):
                raise ValueError(f"Duplicate or invalid DodoStack service hostname: {address}")
            routes[address] = (identity, name)
    for host in local_hosts[1:]:
        routes[host] = routes["localhost"]
    return routes


HOST_ROUTES = host_routes(os.environ.get("DODO_WORKSPACE_HOSTS", ""))


def control_panel(request, identity):
    user = identity.main_user(request)
    path = request.path
    admin = path.startswith("/admin")
    if not user:
        return redirect("/login")
    destination = identity.account_destination(user)
    if destination and path != destination:
        return redirect(destination)
    if admin and not user["is_admin"]:
        return Response.text("Administrator access required.", 403)
    token = hmac.new(accounts.secret, request.cookies[identity.main_session].encode(), hashlib.sha256).hexdigest()
    message = ""
    if request.method == "POST":
        if not hmac.compare_digest(token, request.value("csrf")):
            return Response.text("Invalid security token.", 403)
        try:
            if path == "/account/email":
                accounts.set_email(user["id"], request.value("email"))
                return redirect("/")
            if path == "/admin/approve":
                accounts.approve(request.value("username"))
                return redirect("/admin")
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
        except sqlite3.IntegrityError:
            message = "That email is already in use."
    if admin:
        response = Response.html(pages.admin_accounts(accounts.list_accounts(), identity.admin_tabs, token, message,
                                                      min_password_length=accounts.min_password_length))
    elif path == "/account/email":
        response = Response.html(pages.email_page(token, user["email"], message))
    else:
        response = Response.html(pages.password_page(token, message, min_password_length=accounts.min_password_length))
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
        route = HOST_ROUTES.get(hostname)
        if route is None:
            return self.send_result(Response.text("Unknown DodoStack service", 404))
        identity, service = route
        main = service is None
        app = service_apps.get(service)
        parts = target.path.split("/", 3)
        if main and len(parts) >= 3 and parts[1] == "admin" and parts[2] in identity.admin_tabs:
            service = parts[2]
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get(identity.main_session)
            user = identity.session_user(session.value if session else None, "server")
            if not user:
                response = redirect("/login")
            elif destination := identity.account_destination(user):
                response = redirect(destination)
            elif not user["is_admin"]:
                response = Response.text("Administrator access required.", 403)
            elif len(parts) == 3 or not parts[3]:
                response = redirect(identity.admin_tabs[service][1])
            else:
                return self.serve_web(service_apps[service], service, "/" + parts[3], target, user, identity, admin=True)
            return self.send_result(response)
        if service and not target.path.startswith("/_auth/"):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get("dodo_session")
            user = identity.get_service_user(service, session.value if session else None)
            auth_pages = {"/login", "/signup", "/register", "/forgot-password"}
            if not user and not (self.command in {"GET", "HEAD"} and target.path.rstrip("/") in auth_pages):
                if self.command not in {"GET", "HEAD"} or target.path.startswith("/api/"):
                    return self.send_result(Response.text("Sign in with an approved account.", 401))
                query = urlencode({"service": service, "path": target.path + ("?" + target.query if target.query else "")})
                return self.send_result(redirect(f"{identity.workspace_url}/auth/authorize?{query}"))
            if isinstance(app, WebApplication):
                return self.serve_web(app, service, target.path, target, user, identity)
        request = request_from_handler(self)
        if main:
            if request.path.startswith("/static/"):
                response = workspace_static(request)
            elif request.path in {"/admin", "/admin/role", "/admin/password", "/admin/approve", "/account/password", "/account/email"}:
                response = control_panel(request, identity)
            else:
                response = identity.handle_main(request)
        elif service:
            response = identity.handle_service(service, request)
            if response is None:
                response = app.dispatch(request, auth=identity) if isinstance(app, Application) else Response.text("Not found", 404)
        self.send_result(response, request.response_cookies)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = dispatch

    def serve_web(self, app, service, path, target, user, identity, *, admin=False):
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
    server = ThreadingHTTPServer((os.environ.get("DODO_HOST", "127.0.0.1"), PORT), DodoHandler)
    for host, (identity, service) in HOST_ROUTES.items():
        if service is None and host == identity.workspace_host:
            print(f"DodoStack is running at {identity.workspace_url}")
            for name in identity.services:
                print(f"  {name.title()}: {identity.service_url(name)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DodoStack...")
        server.server_close()


if __name__ == "__main__":
    main()
