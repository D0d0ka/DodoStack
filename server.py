"""DodoStack main server: shared identity plus host-routed child services."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit
from http.cookies import SimpleCookie
import argparse
import importlib
from ipaddress import IPv4Address
import logging
import os
import re
import secrets
import sqlite3
import ssl
import sys
from threading import BoundedSemaphore
from time import perf_counter

from dodo.auth import AccountStore, AuthRateLimit, Identity
from dodo import pages
from dodo.diagnostics import configure_logging, request_id
from dodo.framework import Application, Response, redirect, request_from_handler, same_origin
from dodo.web import WebApplication
from dotenv import load_dotenv
from werkzeug.exceptions import BadRequest, HTTPException

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEBUG = os.environ.get("DODO_DEBUG", "false").strip().lower() in {"true", "1", "yes", "on"}
log = logging.getLogger("dodo.server")
if __name__ == "__main__":
    configure_logging(DEBUG)
    log.info("Starting DodoStack debug=%s", DEBUG)
PORT = int(os.environ.get("DODO_PORT", "8000"))
HTTPS = os.environ.get("DODO_HTTPS", "false").strip().lower()
if HTTPS not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
    raise SystemExit("DODO_HTTPS expects true or false.")
HTTPS = HTTPS in {"true", "1", "yes", "on"}
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
auth_rate_limit = AuthRateLimit()
SERVICE_MODULES = {
    "todo": "subsites.todo.app",
    "doodle": "subsites.doodle.app",
    "dodobook": "subsites.dodobook.server",
    "dodotv": "subsites.DodoTV.run",
    "dodokey": "subsites.dodokey.app",
    "dodocalender": "subsites.dodocalender.app",
}
workspace = Application("workspace", static_dir=BASE_DIR / "static")


BRAND_FILES = {
    "stack": BASE_DIR / "assets/server-icon.png",
    "todo": BASE_DIR / "subsites/todo/static/images/dodo_sign.png",
    "doodle": BASE_DIR / "subsites/doodle/templates/dodo.png",
    "dodobook": BASE_DIR / "subsites/dodobook/static/dodobook.png",
    "dodotv": BASE_DIR / "subsites/DodoTV/dodotv.png",
    "dodokey": BASE_DIR / "subsites/dodokey/static/dodokey.png",
    "dodocalender": BASE_DIR / "subsites/dodocalender/static/dodocalender.png",
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
    started = perf_counter()
    module = importlib.import_module(module_name)
    service_apps[service_name] = module.app
    log.debug("Loaded service=%s module=%s duration_ms=%.2f", service_name, module_name,
              (perf_counter() - started) * 1000)


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
    workspace_addresses = {"localhost": ("localhost", PORT)}
    for entry in config.lower().split(","):
        if not entry.strip():
            continue
        address, _, domain = (part.strip() for part in entry.partition("="))
        host, separator, port = address.partition(":")
        host = host.rstrip(".")
        domain = domain.rstrip(".")
        domain = domain or host
        if separator and (not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535):
            raise ValueError(f"Invalid HTTP port in DODO_WORKSPACE_HOSTS: {address}")
        if not valid_hostname(host) or not valid_hostname(domain):
            raise ValueError("DODO_WORKSPACE_HOSTS expects host[:port][=service-domain]")
        try:
            IPv4Address(host)
        except ValueError:
            if host.split(".")[-1].isdigit():
                raise ValueError(f"Invalid DODO_WORKSPACE_HOSTS address: {host}")
        if domain.split(".")[-1].isdigit():
            raise ValueError(f"Set a DNS service domain for {host} using {host}=example.home.arpa")
        if host in local_hosts or host in workspace_addresses:
            raise ValueError(f"Duplicate or reserved DodoStack workspace hostname: {host}")
        workspace_addresses[host] = (domain, int(port) if separator else PORT)

    # Register each service domain as a workspace with the same browser-facing port.
    for domain, port in tuple(workspace_addresses.values()):
        if workspace_addresses.setdefault(domain, (domain, port)) != (domain, port):
            raise ValueError(f"Conflicting DodoStack workspace settings for service domain: {domain}")

    routes = {host: None for host in (*local_hosts, *workspace_addresses)}
    for host, (domain, port) in workspace_addresses.items():
        services = {name: f"{name}.{domain}" for name in SERVICE_MODULES}
        identity = Identity(accounts, services, port, private=PRIVATE, workspace_host=host, secure=HTTPS)
        identity.admin_tabs = admin_tabs
        routes[host] = (identity, None)
        if host != domain:
            continue
        for name, address in identity.services.items():
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
    token = identity.form_token(request)
    message = ""
    if request.method == "POST":
        if not identity.valid_form(request, token):
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
    server_version = "DodoStack"
    sys_version = ""

    def dispatch(self):
        self.run_request(self.route_request)

    def run_request(self, handler):
        token = request_id.set(secrets.token_hex(8))
        self.status = None
        self.headers_sent = False
        self.workspace_url = HOST_ROUTES["localhost"][0].workspace_url
        self.service = None
        self.api = False
        self.route_name = "http"
        started = perf_counter()
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as error:
            log.debug("Client connection interrupted type=%s", type(error).__name__)
            self.close_connection = True
        except HTTPException as error:
            if self.headers_sent:
                self.close_connection = True
                log.exception("HTTP error after response started")
            else:
                log.debug("HTTP error status=%s type=%s", error.code, type(error).__name__)
                self.close_connection = True
                try:
                    self.send_failure(error.code)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    log.debug("Client disconnected before the error response")
        except Exception:
            log.exception("Request failed route=%s", self.route_name)
            self.close_connection = True
            if not self.headers_sent:
                try:
                    self.send_failure(500)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    log.debug("Client disconnected before the error response")
        finally:
            level = logging.WARNING if self.status and self.status >= 500 else logging.INFO
            log.log(level, "Response route=%s status=%s duration_ms=%.2f", self.route_name,
                    self.status, (perf_counter() - started) * 1000)
            request_id.reset(token)

    def route_request(self):
        try:
            target = urlsplit(self.path)
        except ValueError as error:
            raise BadRequest() from error
        if not target.path.startswith("/") and target.path != "*":
            raise BadRequest()
        host = self.headers.get("Host", "localhost").lower()
        hostname = host.partition(":")[0].rstrip(".")
        self.api = (target.path.startswith("/api/") or target.path == "/api"
                    or "application/json" in self.headers.get("Accept", "").lower()
                    or self.headers.get("X-Requested-With", "").lower() == "xmlhttprequest")
        route = HOST_ROUTES.get(hostname)
        log.debug("Request method=%r host=%s client=%s query_present=%s",
                  self.command, hostname if route else "<unregistered>",
                  self.client_address[0], bool(target.query))
        if route is None:
            self.route_name = "unknown_host"
            parent_route = HOST_ROUTES.get(hostname.partition(".")[2])
            if parent_route:
                self.workspace_url = parent_route[0].workspace_url
            return self.send_failure(404, title="Unknown DodoStack service",
                                     detail="This hostname is not registered as a DodoStack service.")
        identity, service = route
        self.workspace_url = identity.workspace_url
        self.service = service
        self.route_name = service or "workspace"
        length = self.headers.get("Content-Length", "0").strip()
        if (self.headers.get("Transfer-Encoding") is not None
                or len(self.headers.get_all("Content-Length", [])) > 1
                or len(self.headers.get_all("Host", [])) > 1
                or not re.fullmatch(r"[0-9]{1,20}", length)):
            raise BadRequest()
        if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", host):
            raise BadRequest()
        host_port = host.partition(":")[2]
        default_port = "443" if identity.secure else "80"
        origin_host = hostname if not host_port or host_port == default_port else f"{hostname}:{host_port}"
        if not same_origin(self.command, self.headers, f"{identity.scheme}://{origin_host}"):
            return self.send_failure(403)
        log.debug("Routed request service=%s content_length=%s", self.route_name, length)
        main = service is None
        if main and self.command == "POST" and target.path in {"/login", "/signup", "/forgot-password", "/account/password"}:
            delay = auth_rate_limit.retry_after(self.client_address[0])
            if delay:
                log.warning("Authentication rate limit reached")
                response = pages.error_response(429, detail="Wait a minute and try again.",
                                                workspace_url=self.workspace_url, api=self.api)
                response.headers["Retry-After"] = str(delay)
                return self.send_result(response)
        app = service_apps.get(service)
        parts = target.path.split("/", 3)
        if main and len(parts) >= 3 and parts[1] == "admin" and parts[2] in identity.admin_tabs:
            service = parts[2]
            self.route_name = f"admin/{service}"
            self.api = self.api or (len(parts) == 4 and parts[3].startswith("api/"))
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get(identity.main_session)
            user = identity.session_user(session.value if session else None, "server")
            if not user:
                log.debug("Admin gate requires a session")
                response = redirect("/login")
            elif destination := identity.account_destination(user):
                log.debug("Admin gate requires account approval")
                response = redirect(destination)
            elif not user["is_admin"]:
                log.debug("Admin gate denied a non-admin account")
                response = Response.text("Administrator access required.", 403)
            elif len(parts) == 3 or not parts[3]:
                response = redirect(identity.admin_tabs[service][1])
            else:
                return self.serve_web(service_apps[service], service, "/" + parts[3], target, user, identity, admin=True)
            return self.send_result(response)
        if service and not target.path.startswith("/_auth/"):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get(identity.service_session)
            user = identity.get_service_user(service, session.value if session else None)
            log.debug("Service gate service=%s authenticated=%s", service, bool(user))
            auth_pages = {"/login", "/signup", "/register", "/forgot-password"}
            if not user and not (self.command in {"GET", "HEAD"} and target.path.rstrip("/") in auth_pages):
                if self.command not in {"GET", "HEAD"} or self.api:
                    return self.send_result(Response.text("Sign in with an approved account.", 401))
                request = request_from_handler(self)
                request.secure = identity.secure
                response = identity.authorize_redirect(service, request)
                return self.send_result(response, request.response_cookies)
            if isinstance(app, WebApplication):
                return self.serve_web(app, service, target.path, target, user, identity)
        request = request_from_handler(self)
        request.secure = identity.secure
        if main:
            known_path = request.path in {"/", "/login", "/signup", "/logout", "/forgot-password",
                                          "/auth/authorize", "/account/pending", "/account/password",
                                          "/account/email", "/admin", "/admin/role", "/admin/password",
                                          "/admin/approve"}
            endpoint = "static" if request.path.startswith("/static/") else "<unmatched>"
            log.debug("Workspace endpoint=%s", request.path if known_path else endpoint)
            if request.path.startswith("/static/"):
                response = workspace_static(request)
            elif request.path in {"/admin", "/admin/role", "/admin/password", "/admin/approve", "/account/password", "/account/email"}:
                response = control_panel(request, identity)
            else:
                response = identity.handle_main(request, api=self.api)
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
            "wsgi.version": (1, 0), "wsgi.url_scheme": identity.scheme,
            "wsgi.input": self.rfile, "wsgi.errors": sys.stderr,
            "wsgi.multithread": True, "wsgi.multiprocess": False, "wsgi.run_once": False,
        }
        for key, value in self.headers.items():
            # WSGI normalizes dashes to underscores. Ignore aliases that could override trusted headers.
            if "_" not in key:
                key = key.upper().replace("-", "_")
                environ[key if key in {"CONTENT_TYPE", "CONTENT_LENGTH"} else "HTTP_" + key] = value

        def start_response(status, headers):
            self.send_response(int(status.split()[0]))
            for key, value in headers:
                if key.lower() not in {"x-request-id", "x-content-type-options"}:
                    self.send_header(key, value)
            self.end_headers()
            return self.wfile.write

        chunks = app.serve(environ, start_response, path=unquote_to_bytes(path).decode("utf-8", "replace"),
                           identity=identity, user=user, service=service, admin=admin, api=self.api)
        try:
            for chunk in chunks:
                if self.command != "HEAD":
                    self.wfile.write(chunk)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()

    def send_failure(self, status, **kwargs):
        self._headers_buffer = []
        self.send_result(pages.error_response(status, workspace_url=self.workspace_url,
                                              service=self.service, api=self.api, **kwargs))

    def send_result(self, response, cookies=()):
        if response.status >= 400 and response.headers.get("Content-Type", "").startswith("text/plain"):
            detail = response.body.decode("utf-8") if response.status < 500 else None
            if detail == "Not found":
                detail = None
            error = pages.error_response(response.status, detail=detail, workspace_url=self.workspace_url,
                                         service=self.service, api=self.api)
            response.headers.update(error.headers)
            response.body = error.body
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
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def end_headers(self):
        self.send_header("X-Request-ID", request_id.get())
        self.send_header("X-Content-Type-Options", "nosniff")
        if not any(line.lower().startswith(b"referrer-policy:") for line in getattr(self, "_headers_buffer", ())):
            self.send_header("Referrer-Policy", "strict-origin")
        if not any(line.lower().startswith(b"x-frame-options:") for line in getattr(self, "_headers_buffer", ())):
            self.send_header("X-Frame-Options", "DENY")
        if HTTPS:
            self.send_header("Strict-Transport-Security", "max-age=86400")
        self.headers_sent = True
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        # A malformed request can fail before a protocol version is recognized.
        if self.request_version == self.default_request_version:
            self.request_version = "HTTP/1.0"
        self.run_request(lambda: self.send_failure(code))

    def log_request(self, code="-", size="-"):
        self.status = int(code)

    def log_error(self, fmt, *args):
        # The base handler includes raw protocol input in some messages.
        log.debug("HTTP connection error client=%s", self.client_address[0])


class DodoServer(ThreadingHTTPServer):
    request_queue_size = 64

    def __init__(self, address, tls):
        self.tls = tls
        self.slots = BoundedSemaphore(64)
        super().__init__(address, DodoHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            request.settimeout(15)
            if self.tls:
                request = self.tls.wrap_socket(request, server_side=True)
            super().process_request_thread(request, address)
        except OSError:
            self.shutdown_request(request)
            log.debug("Connection closed before request handling")
        finally:
            self.slots.release()


def tls_context():
    if not HTTPS:
        return None
    certificate = os.environ.get("DODO_TLS_CERT_FILE", "").strip()
    private_key = os.environ.get("DODO_TLS_KEY_FILE", "").strip()
    if not certificate or not private_key:
        raise SystemExit("DODO_HTTPS=true requires DODO_TLS_CERT_FILE and DODO_TLS_KEY_FILE.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    try:
        context.load_cert_chain(BASE_DIR / certificate, BASE_DIR / private_key, password="")
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not load the configured DodoStack TLS certificate and key ({type(error).__name__}).") from None
    return context


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
    server = DodoServer((os.environ.get("DODO_HOST", "127.0.0.1"), PORT), tls_context())
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
