"""Shared account store and short-lived, audience-scoped service sessions."""
from __future__ import annotations

from datetime import date
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
import unicodedata

from email_validator import EmailNotValidError, validate_email

from subsites.dodomail.delivery import SUPPORT_ADDRESS, initialize as initialize_mail, welcome

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


DMAIL_LOCAL = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"


def clean_name(value):
    name = " ".join(value.split())
    if len(name) > 60 or not any(char.isalpha() for char in name):
        raise ValueError("Enter both your first name and last name (up to 60 characters each).")
    return name


def name_local_part(first_name, last_name):
    parts = []
    for name in (first_name, last_name):
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        part = re.sub(r"[^a-z0-9]+", ".", ascii_name.lower()).strip(".")[:24].rstrip(".")
        parts.append(part or "user")
    return ".".join(parts)


class DmailAddressTaken(ValueError):
    pass


ADMIN_PERMISSIONS = {
    "accounts": "Accounts",
    **{key: pages.SERVICE_NAMES[key] for key in ("doodle", "dodobook", "dodotv", "dodocalender")},
    "dodomail": "DodoMail Support",
}


def can_admin(user, permission):
    return bool(user and user.get("approved") and (user.get("is_owner") or (
        user.get("is_admin") and permission in user.get("admin_permissions", []))))


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
                birth_date TEXT NOT NULL,
                real_name TEXT NOT NULL,
                dmail_address TEXT NOT NULL COLLATE NOCASE UNIQUE,
                approved INTEGER NOT NULL DEFAULT 1,
                password_reset_requested INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(lower(email))")
            db.execute("""CREATE TABLE IF NOT EXISTS redeemed_tickets (
                nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL
            )""")

            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "is_owner" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE users ADD COLUMN admin_permissions TEXT NOT NULL DEFAULT '[]'")
                db.execute("UPDATE users SET admin_permissions=? WHERE is_admin=1",
                           (json.dumps(list(ADMIN_PERMISSIONS)),))
                db.execute("UPDATE users SET is_owner=1 WHERE id=(SELECT id FROM users "
                           "WHERE is_admin=1 AND approved=1 ORDER BY id LIMIT 1)")
            initialize_mail(db)

    def redeem_ticket(self, nonce, expires):
        with self.connect() as db:
            db.execute("DELETE FROM redeemed_tickets WHERE expires < ?", (int(time.time()),))
            return db.execute("INSERT OR IGNORE INTO redeemed_tickets VALUES (?, ?)",
                              (nonce, expires)).rowcount == 1

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
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
        if identifier.lower().endswith("@dmail"):
            lookup = "dmail_address=?"
        elif "@" in identifier:
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

    @staticmethod
    def account_roles(row):
        return {**row, "is_admin": bool(row["is_admin"]), "is_owner": bool(row["is_owner"]),
                "approved": bool(row["approved"]), "admin_permissions": json.loads(row["admin_permissions"])}

    def account(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT id, username, email, is_admin, is_owner, admin_permissions, approved FROM users WHERE id=?", (user_id,)).fetchone()
        return self.account_roles(row) if row else None

    def set_admin(self, username, enabled):
        """Trusted local CLI/bootstrap operation; browser changes use set_role."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("Unknown DodoStack account.")
            if not enabled:
                self.require_other_owner(db, user)
            owner = enabled and not db.execute("SELECT 1 FROM users WHERE is_owner=1 AND approved=1").fetchone()
            db.execute("UPDATE users SET is_admin=?, is_owner=?, admin_permissions=?, approved=CASE WHEN ? THEN 1 ELSE approved END WHERE id=?",
                       (enabled, bool(owner or (enabled and user["is_owner"])),
                        json.dumps(list(ADMIN_PERMISSIONS) if enabled else []), enabled, user["id"]))

    def set_owner(self, username):
        """Trusted local CLI operation for assigning or recovering ownership."""
        with self.connect() as db:
            if not db.execute(
                "UPDATE users SET is_owner=1, is_admin=1, approved=1, admin_permissions='[]' WHERE username=?",
                (username,),
            ).rowcount:
                raise ValueError("Unknown DodoStack account.")

    def set_role(self, actor_id, username, role, permissions):
        if role not in {"member", "admin", "owner"} or not set(permissions) <= ADMIN_PERMISSIONS.keys():
            raise ValueError("Choose a valid role and permissions.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM users WHERE id=? AND is_owner=1 AND approved=1", (actor_id,)).fetchone():
                raise PermissionError
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("Unknown DodoStack account.")
            if role != "owner":
                self.require_other_owner(db, user)
            db.execute("UPDATE users SET is_admin=?, is_owner=?, admin_permissions=?, approved=CASE WHEN ? THEN 1 ELSE approved END WHERE id=?",
                       (role != "member", role == "owner", json.dumps(sorted(set(permissions)) if role == "admin" else []),
                        role != "member", user["id"]))

    def list_accounts(self):
        with self.connect() as db:
            return [self.account_roles(row) for row in db.execute(
                "SELECT id, username, is_admin, is_owner, admin_permissions, approved, password_reset_requested FROM users ORDER BY approved, username")]

    @staticmethod
    def require_other_owner(db, user):
        if user["is_owner"] and user["approved"] and not db.execute(
            "SELECT 1 FROM users WHERE is_owner=1 AND approved=1 AND id<>?", (user["id"],)
        ).fetchone():
            raise ValueError("Keep at least one approved owner.")

    @staticmethod
    def account_manager(db, actor_id, user):
        actor = db.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or not can_admin(AccountStore.account_roles(actor), "accounts"):
            raise PermissionError
        if (user["is_admin"] or user["is_owner"]) and not actor["is_owner"]:
            raise PermissionError

    def restrict_account(self, actor_id, account_id, *, delete):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE id=?", (account_id,)).fetchone()
            if not user:
                raise ValueError("Unknown DodoStack account.")
            self.account_manager(db, actor_id, user)
            self.require_other_owner(db, user)
            if delete:
                db.execute("DELETE FROM mail_messages WHERE recipient_id=?", (account_id,))
                db.execute("DELETE FROM user_mail WHERE sender_id=? OR recipient_id=?", (account_id, account_id))
                db.execute("DELETE FROM users WHERE id=?", (account_id,))
            elif user["approved"]:
                db.execute("UPDATE users SET approved=0, session_version=session_version+1 WHERE id=?", (account_id,))

    def manage_account(self, actor_id, username, *, password=None):
        if password is not None:
            self.validate_password(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError("Unknown DodoStack account.")
            self.account_manager(db, actor_id, user)
            if password is None:
                db.execute("UPDATE users SET approved=1 WHERE id=?", (user["id"],))
            else:
                db.execute("UPDATE users SET password_hash=?, session_version=session_version+1, password_reset_requested=0 WHERE id=?",
                           (self.hash_password(password), user["id"]))

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

    def suggest_dmail(self, first_name, last_name):
        base = name_local_part(clean_name(first_name), clean_name(last_name))
        with self.connect() as db:
            candidate, number = base, 2
            while candidate + "@dmail" == SUPPORT_ADDRESS or db.execute(
                "SELECT 1 FROM users WHERE dmail_address=?", (candidate + "@dmail",),
            ).fetchone():
                candidate = f"{base}{number}"
                number += 1
        return candidate

    def register(self, username, password, email, *, birth_date, first_name, last_name, dmail_local, approved):
        self.validate_password(password)
        email = normalized_email(email)
        real_name = f"{clean_name(first_name)} {clean_name(last_name)}"
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", birth_date) or date.fromisoformat(birth_date) > date.today():
                raise ValueError
        except ValueError:
            raise ValueError("Enter a valid date of birth.") from None
        local = dmail_local.strip().lower()
        if not 1 <= len(local) <= 64 or not re.fullmatch(DMAIL_LOCAL, local):
            raise ValueError("Use up to 64 letters (a–z) or digits, separated by dots, underscores or hyphens.")
        address = local + "@dmail"
        password_hash = self.hash_password(password)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if address == SUPPORT_ADDRESS or db.execute(
                "SELECT 1 FROM users WHERE dmail_address=?", (address,),
            ).fetchone():
                raise DmailAddressTaken
            cursor = db.execute(
                """INSERT INTO users(username,password_hash,email,birth_date,real_name,dmail_address,
                    approved,session_version) VALUES (?,?,?,?,?,?,?,?)""",
                (username, password_hash, email, birth_date, real_name, address, approved, secrets.randbits(52)),
            )
            welcome(db, cursor.lastrowid)
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

    def allowed_admin_tabs(self, user):
        return {key: value for key, value in self.admin_tabs.items() if can_admin(user, key)}

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

    def service_account_menu(self, request, user, *, placement=""):
        token = self.csrf_token(request.cookies[self.service_session], "service-logout")
        return pages.account_menu(user["username"], token, workspace_url=self.workspace_url,
                                  logout="/_auth/logout", placement=placement)

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
        if request.path == "/_auth/logout":
            if request.method != "POST":
                return Response(b"", 405, {"Allow": "POST"})
            session = request.cookies.get(self.service_session, "")
            user = self.get_service_user(service, session)
            if not user:
                return Response.text("Sign in with an approved account.", 401)
            if not self.valid_form(request, self.csrf_token(session, "service-logout")):
                return Response.text("Invalid security token.", 403)
            request.delete_cookie(self.service_session)
            return self.sign_out_service(user)
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
        if path in {"/signup", "/login"} and request.method not in {"GET", "HEAD", "POST"}:
            return Response(b"", 405, {"Allow": "GET, HEAD, POST"})
        if path == "/logout" and request.method != "POST":
            response = Response.text("Sign out using the workspace form.", 405)
            response.headers["Allow"] = "POST"
            return response
        token = ""
        if path in {"/login", "/signup", "/forgot-password", "/logout", "/auth/authorize", "/signup/address"}:
            token = self.form_token(request)
            if request.method == "POST" and not self.valid_form(request, token):
                log.debug("Rejected workspace form with an invalid security token")
                return Response.text("Invalid security token. Reload the form and try again.", 403)
        if user and path != "/logout":
            destination = self.account_destination(user)
            if destination and path != destination:
                return redirect(destination)
        if path == "/account/pending":
            if not user:
                return redirect("/login")
            if user["approved"]:
                return redirect("/")
            response = Response.html(pages.pending_page(self.form_token(request), user["username"]))
            response.headers["Cache-Control"] = "no-store"
            return response
        if path == "/auth/authorize":
            service, target = request.value("service"), request.value("path", "/")
            state = request.value("state")
            if service not in self.services:
                return pages.error_response(404, title="Unknown DodoStack service",
                                            detail="The requested service is not registered in this workspace.",
                                            api=api)
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            if user:
                return self._issue_ticket(service, user, target, state)
            return self.auth_form("login", service, target, token=token, state=state)
        if path == "/signup/address":
            if request.method != "POST":
                return Response(b"", 405, {"Allow": "POST"})
            try:
                suggestion = self.accounts.suggest_dmail(
                    request.form.get("first_name", [""])[0], request.form.get("last_name", [""])[0],
                )
            except ValueError as error:
                return Response.text(str(error), 400)
            response = Response.text(suggestion)
            response.headers["Cache-Control"] = "no-store"
            return response
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
                          ("username", "email", "password", "password_confirmation", "birth_date", "first_name", "last_name", "dmail_local")}
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
                                birth_date=fields["birth_date"], first_name=fields["first_name"],
                                last_name=fields["last_name"],
                                dmail_local=fields["dmail_local"],
                            )
                            error = None
                        except DmailAddressTaken:
                            fields["dmail_local"] = self.accounts.suggest_dmail(fields["first_name"], fields["last_name"])
                            error = "That DodoMail address is in use. Review the available address below and submit again."
                        except ValueError as exc:
                            error = str(exc)
                        except sqlite3.IntegrityError:
                            error = "That username or email is already in use."
                else:
                    authenticated = self.accounts.authenticate(username, password)
                    user = self.accounts.account(authenticated["id"]) if authenticated else None
                    error = None if user else "The sign-in details are incorrect."
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
                user, {key: self.service_url(key) if key in self.services else None
                       for key in pages.SERVICE_NAMES}, private=self.private,
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
