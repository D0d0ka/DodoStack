"""Shared account store and short-lived, audience-scoped service sessions."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
from threading import Lock
import time

from email_validator import EmailNotValidError, validate_email

from .framework import Request, Response, cookie_name, redirect
from . import pages

log = logging.getLogger(__name__)


class AuthRateLimit:
    """Limit password work per peer and across the process."""
    window = 60

    def __init__(self):
        self.lock = Lock()
        self.deadline = 0
        self.total = 0
        self.peers = {}

    def retry_after(self, peer):
        with self.lock:
            now = time.monotonic()
            if now >= self.deadline:
                self.deadline = now + self.window
                self.total = 0
                self.peers.clear()
            count = self.peers.get(peer, 0)
            if count >= 10 or self.total >= 120:
                return int(self.deadline - now) + 1
            self.peers[peer] = count + 1
            self.total += 1
            return 0


def local_path(path: str) -> str:
    if (not path.startswith("/") or path.startswith(("//", "/_auth/"))
            or "\\" in path or any(ord(char) < 32 for char in path)):
        return "/"
    return quote(path, safe="/%?&=;:+,~@!$'()*")


def normalized_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized
    except EmailNotValidError as error:
        raise ValueError("Enter a valid email address.") from error


class AccountStore:
    def __init__(self, database: str | Path, secret: str, *, min_password_length=8):
        self.path = Path(database)
        self.secret = secret.encode()
        if not 1 <= min_password_length <= 255:
            raise ValueError("DODO_MIN_PASSWORD_LENGTH must be between 1 and 255.")
        self.min_password_length = min_password_length
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                session_version INTEGER NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                email TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 1,
                password_reset_requested INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(lower(email))")
            db.execute("""CREATE TABLE IF NOT EXISTS redeemed_tickets (
                nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL
            )""")

    def redeem_ticket(self, nonce, expires):
        with self.connect() as db:
            db.execute("DELETE FROM redeemed_tickets WHERE expires < ?", (int(time.time()),))
            return db.execute("INSERT OR IGNORE INTO redeemed_tickets VALUES (?, ?)",
                              (nonce, expires)).rowcount == 1

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return "pbkdf2_sha256$310000$%s$%s" % (
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="))

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        if not encoded.startswith("pbkdf2_sha256$"):
            return False
        try:
            _, rounds, salt, expected = encoded.split("$", 3)

            def decode(value):
                return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), decode(salt), int(rounds))
            return hmac.compare_digest(actual, decode(expected))
        except (ValueError, TypeError):
            return False

    def authenticate(self, identifier: str, password: str):
        if len(identifier) > 254 or not 1 <= len(password) <= 255:
            return None
        if "@" in identifier:
            try:
                identifier = normalized_email(identifier)
            except ValueError:
                return None
            lookup = "lower(email)=lower(?)"
        else:
            lookup = "username=?"
        with self.connect() as db:
            user = db.execute(
                "SELECT id, username, password_hash FROM users WHERE " + lookup, (identifier,),
            ).fetchone()
        if user and self.verify_password(password, user["password_hash"]):
            return {"id": user["id"], "username": user["username"]}
        return None

    def account_id_by_email(self, email: str):
        try:
            email = normalized_email(email)
        except ValueError:
            return None
        with self.connect() as db:
            row = db.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
        return row["id"] if row else None

    def account(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT id, username, email, is_admin, approved FROM users WHERE id=?", (user_id,)).fetchone()
        return {**row, "is_admin": bool(row["is_admin"]), "approved": bool(row["approved"])} if row else None

    def set_admin(self, username, enabled):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT id, is_admin FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("Unknown DodoStack account.")
            if not enabled and user["is_admin"] and db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin=1"
            ).fetchone()[0] == 1:
                raise ValueError("Keep at least one administrator.")
            if enabled:
                db.execute("UPDATE users SET is_admin=1, approved=1 WHERE id=?", (user["id"],))
            else:
                db.execute("UPDATE users SET is_admin=0 WHERE id=?", (user["id"],))

    def list_accounts(self):
        with self.connect() as db:
            return db.execute("SELECT username, email, is_admin, approved, password_reset_requested FROM users ORDER BY approved, username").fetchall()

    def request_password_reset(self, username):
        with self.connect() as db:
            db.execute("UPDATE users SET password_reset_requested=1 WHERE username=?", (username,))

    def validate_password(self, password):
        if not self.min_password_length <= len(password) <= 255:
            raise ValueError(f"Use a password between {self.min_password_length} and 255 characters.")

    def reset_password(self, username, password):
        self.validate_password(password)
        with self.connect() as db:
            db.execute(
                "UPDATE users SET password_hash=?, session_version=session_version+1, password_reset_requested=0 WHERE username=?",
                (self.hash_password(password), username),
            )

    def session_version(self, user_id: int) -> int:
        with self.connect() as db:
            row = db.execute("SELECT session_version FROM users WHERE id=?", (user_id,)).fetchone()
        return int(row[0]) if row else -1

    def is_current(self, payload: dict) -> bool:
        try:
            version = self.session_version(int(payload["sub"]))
            return version >= 0 and payload.get("ver") == version
        except (ValueError, KeyError, TypeError):
            return False

    def revoke_sessions(self, user_id: int) -> None:
        with self.connect() as db:
            db.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))

    def set_email(self, user_id: int, email: str):
        with self.connect() as db:
            db.execute("UPDATE users SET email=? WHERE id=?", (normalized_email(email), user_id))

    def approve(self, username: str):
        with self.connect() as db:
            db.execute("UPDATE users SET approved=1 WHERE username=?", (username,))

    def register(self, username: str, password: str, email: str, *, approved: bool):
        self.validate_password(password)
        email = normalized_email(email)
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO users(username,password_hash,email,approved,session_version) VALUES (?,?,?,?,?)",
                (username, self.hash_password(password), email, approved, secrets.randbits(52)),
            )
            return {"id": cursor.lastrowid, "username": username, "email": email, "approved": approved}

    def issue(self, payload: dict) -> str:
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    def read(self, token: str | None, audience: str):
        payload = self.read_claims(token, audience)
        if payload is None:
            return None
        try:
            return {"id": int(payload["sub"]), "username": payload["name"]}
        except (ValueError, KeyError, TypeError):
            return None

    def read_claims(self, token: str | None, audience: str):
        if not token or "." not in token or not token.isascii():
            return None
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["aud"] != audience or payload["exp"] < int(time.time()):
                return None
            return payload
        except (ValueError, KeyError, TypeError):
            return None


class Identity:
    def __init__(self, accounts: AccountStore, services: dict[str, str], port: int, *,
                 private=False, workspace_host="localhost", secure=False):
        self.accounts, self.services, self.port = accounts, services, port
        self.workspace_host = workspace_host
        self.secure = secure
        self.scheme = "https" if secure else "http"
        self.port_suffix = "" if port == (443 if secure else 80) else f":{port}"
        self.workspace_url = f"{self.scheme}://{workspace_host}{self.port_suffix}"
        self.private = private
        self.main_session = cookie_name("dodo_main_session", secure)
        self.service_session = cookie_name("dodo_session", secure)
        self.service_flow = cookie_name("dodo_signin_flow", secure)
        self.form_session = cookie_name("dodo_form", secure)

    def form_token(self, request):
        session = request.cookies.get(self.main_session)
        if session:
            return self.csrf_token(session, "workspace")
        session = request.cookies.get(self.form_session, "")
        if not re.fullmatch(r"[a-f0-9]{64}", session):
            session = secrets.token_hex(32)
            request.set_cookie(self.form_session, session, max_age=3600)
        return self.csrf_token(session, "anonymous")

    def csrf_token(self, session, purpose):
        return hmac.new(self.accounts.secret, f"csrf:{purpose}:{session}".encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def valid_form(request, expected):
        supplied = request.form.get("csrf", [""])[0]
        return supplied.isascii() and hmac.compare_digest(expected, supplied)

    def sign_out_service(self, user):
        self.accounts.revoke_sessions(user["id"])
        return redirect(self.workspace_url + "/")

    def main_user(self, request: Request):
        return self.session_user(request.cookies.get(self.main_session), "server")

    def get_service_user(self, service: str, token: str | None):
        return self.session_user(token, service)

    @staticmethod
    def account_destination(user):
        if not user["approved"]:
            return "/account/pending"
        return None

    def session_user(self, token: str | None, audience: str):
        payload = self.accounts.read_claims(token, audience)
        if not payload or not self.accounts.is_current(payload):
            log.debug("Session rejected audience=%s reason=%s", audience,
                      "missing" if not token else "invalid_or_expired" if not payload else "revoked")
            return None
        user = self.accounts.account(int(payload["sub"]))
        if user and (audience == "server" or not self.account_destination(user)):
            log.debug("Session accepted audience=%s", audience)
            return user
        log.debug("Session rejected audience=%s reason=account_unavailable", audience)
        return None

    def issue_assertion(self, source_service: str, target_service: str, user: dict, claims: dict | None = None, ttl: int = 60):
        """Create a brief signed user assertion for a trusted service call."""
        if source_service not in self.services or target_service not in self.services:
            raise ValueError("Assertions can only be issued between registered services")
        return self.accounts.issue({"sub": user["id"], "name": user["username"], "ver": self.accounts.session_version(user["id"]), "aud": "assertion:" + target_service,
                                   "iss": source_service, "claims": claims or {},
                                   "exp": int(time.time()) + max(1, min(ttl, 300))})

    def verify_assertion(self, source_service: str, target_service: str, token: str):
        payload = self.accounts.read_claims(token, "assertion:" + target_service)
        if not payload or payload.get("iss") != source_service or not self.accounts.is_current(payload):
            return None
        user = self.accounts.account(int(payload["sub"]))
        if not user or self.account_destination(user):
            return None
        return {"user": {"id": user["id"], "username": user["username"]},
                "claims": payload.get("claims", {})}

    def service_url(self, service: str, path: str = "/"):
        host = self.services[service]
        return f"{self.scheme}://{host}{self.port_suffix}{path}"

    def authorize_redirect(self, service: str, request: Request):
        path = request.path
        if request.query:
            path += "?" + urlencode([(key, value) for key, values in request.query.items() for value in values])
        state = request.cookies.get(self.service_flow, "")
        if not re.fullmatch(r"[a-f0-9]{64}", state):
            state = secrets.token_hex(32)
            request.set_cookie(self.service_flow, state, max_age=900)
        challenge = self.csrf_token(state, "signin:" + self.services[service])
        query = urlencode({"service": service, "path": path, "state": challenge})
        response = redirect(f"{self.workspace_url}/auth/authorize?{query}")
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def handle_service(self, service: str, request: Request):
        if request.path in {"/_auth/complete", "/_auth/callback"}:
            log.debug("Shared service authentication endpoint=%s service=%s", request.path, service)
            if request.method != "GET":
                response = Response.text("This address accepts GET.", 405)
                response.headers["Allow"] = "GET"
                return response
        if request.path == "/_auth/complete":
            if not self.get_service_user(service, request.cookies.get(self.service_session)):
                return self.sign_in_problem(
                    service, "Allow cookies for this service and try signing in again.", 403,
                )
            response = redirect(local_path(request.value("path", "/")))
        elif request.path == "/_auth/callback":
            ticket = self.accounts.read_claims(request.value("ticket"), "ticket:" + self.services[service])
            state = request.cookies.get(self.service_flow, "")
            challenge = self.csrf_token(state, "signin:" + self.services[service])
            if (not ticket or not self.accounts.is_current(ticket)
                    or not state or ticket.get("state") != challenge
                    or not ticket.get("nonce")
                    or not self.accounts.redeem_ticket(ticket["nonce"], ticket["exp"])):
                log.debug("Service sign-in ticket rejected service=%s", service)
                return self.sign_in_problem(
                    service, "This sign-in link has expired or was already used. Start again from the service.", 400,
                )
            user = self.accounts.account(int(ticket["sub"]))
            if not user or self.account_destination(user):
                return redirect(f"{self.workspace_url}/")
            lifetime = 12 * 3600
            session = self.accounts.issue({
                "sub": ticket["sub"], "name": ticket["name"], "aud": service,
                "ver": ticket["ver"], "exp": int(time.time()) + lifetime,
            })
            request.set_cookie(self.service_session, session, max_age=lifetime)
            response = redirect("/_auth/complete?" + urlencode({"path": ticket["path"]}))
        else:
            return None
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def sign_in_problem(self, service: str, message: str, status: int):
        response = Response.html(pages.sign_in_problem(
            service, message, self.service_url(service), self.workspace_url,
        ), status)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def handle_main(self, request: Request, *, api=False):
        user = self.main_user(request)
        path = request.path
        if path == "/logout" and request.method != "POST":
            response = Response.text("Sign out using the workspace form.", 405)
            response.headers["Allow"] = "POST"
            return response
        token = ""
        if path in {"/login", "/signup", "/forgot-password", "/logout", "/auth/authorize"}:
            token = self.form_token(request)
            if request.method == "POST" and not self.valid_form(request, token):
                return Response.text("Invalid security token.", 403)
        if user and path != "/logout":
            destination = self.account_destination(user)
            if destination and path != destination:
                return redirect(destination)
        if path == "/account/pending":
            if not user:
                return redirect("/login")
            if user["approved"]:
                return redirect("/")
            response = Response.html(pages.pending_page(self.form_token(request)))
            response.headers["Cache-Control"] = "no-store"
            return response
        if path == "/auth/authorize":
            service, target = request.value("service"), request.value("path", "/")
            state = request.value("state")
            if service not in self.services:
                return pages.error_response(404, title="Unknown DodoStack service",
                                            detail="The requested service is not registered in this workspace.",
                                            workspace_url=self.workspace_url, api=api)
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            if user:
                return self._issue_ticket(service, user, target, state)
            return self.auth_form("login", service, target, token=token, state=state)
        if path == "/forgot-password":
            submitted = request.method == "POST"
            if submitted:
                self.accounts.request_password_reset(request.value("username").strip())
            response = Response.html(pages.reset_request_page(submitted, token))
            response.headers["Cache-Control"] = "no-store"
            return response
        if path in {"/login", "/signup"}:
            mode = "signup" if path == "/signup" else "login"
            service, target = request.value("service"), request.value("path", "/")
            state = request.value("state")
            if request.method == "POST":
                fields = {key: request.form.get(key, [""])[0] for key in
                          ("username", "email", "password", "password_confirmation")}
                username, password = fields["username"].strip(), fields["password"]
                if mode == "signup":
                    confirmation = fields["password_confirmation"]
                    if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
                        error = "Choose a username with 3–24 letters, numbers, or underscores."
                    elif password != confirmation:
                        error = "Your passwords do not match."
                    else:
                        try:
                            user = self.accounts.register(
                                username, password, fields["email"], approved=not self.private,
                            )
                            error = None
                        except ValueError as exc:
                            error = str(exc)
                        except sqlite3.IntegrityError:
                            error = "That username or email is already in use."
                else:
                    authenticated = self.accounts.authenticate(username, password)
                    user = self.accounts.account(authenticated["id"]) if authenticated else None
                    error = None if user else "The username, email or password is incorrect."
                if not error:
                    log.debug("Shared account %s accepted", mode)
                    version = self.accounts.session_version(user["id"])
                    request.set_cookie(self.main_session, self.accounts.issue({"sub": user["id"], "name": user["username"], "aud": "server", "ver": version, "exp": int(time.time()) + 30 * 86400}), max_age=30 * 86400)
                    request.delete_cookie(self.form_session)
                    destination = self.account_destination(user)
                    if destination:
                        return redirect(destination)
                    if service in self.services:
                        return self._issue_ticket(service, user, target, state)
                    return redirect("/")
                log.debug("Shared account %s rejected", mode)
                return self.auth_form(mode, service, target, error=error, fields=fields, token=token, state=state)
            if user:
                return self._issue_ticket(service, user, target, state) if service in self.services else redirect("/")
            return self.auth_form(mode, service, target, token=token, state=state)
        if path == "/logout":
            if user:
                self.accounts.revoke_sessions(user["id"])
            request.delete_cookie(self.main_session)
            request.delete_cookie(self.form_session)
            return redirect("/")
        if path == "/":
            selected = request.value("service")
            if selected in self.services:
                return redirect(self.service_url(selected))
            response = Response.html(pages.dashboard(
                user, {key: self.service_url(key) for key in self.services}, private=self.private,
                token=self.form_token(request) if user else "",
            ))
            response.headers["Cache-Control"] = "no-store"
            return response
        return Response.text("Not found", 404)

    def auth_form(self, mode, service, target, **values):
        response = Response.html(pages.auth_page(
            mode, service=service, target=target, private=self.private,
            min_password_length=self.accounts.min_password_length, **values,
        ))
        response.headers["Cache-Control"] = "no-store"
        return response

    def _issue_ticket(self, service, user, target, state):
        target = local_path(target)
        if not re.fullmatch(r"[a-f0-9]{64}", state):
            return redirect(self.service_url(service, target), 303)
        ticket = self.accounts.issue({
            "sub": user["id"], "name": user["username"],
            "ver": self.accounts.session_version(user["id"]),
            "aud": "ticket:" + self.services[service], "exp": int(time.time()) + 120,
            "nonce": secrets.token_hex(24),
            "path": target,
            "state": state,
        })
        response = redirect(self.service_url(service, "/_auth/callback?ticket=" + quote(ticket)))
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response
