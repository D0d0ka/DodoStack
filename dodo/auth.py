"""Shared account store and short-lived, audience-scoped service sessions."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

from .framework import Request, Response, redirect
from . import pages


def local_path(path: str) -> str:
    if (not path.startswith("/") or path.startswith(("//", "/_auth/"))
            or "\\" in path or any(ord(char) < 32 for char in path)):
        return "/"
    return quote(path, safe="/%?&=;:+,~@!$'()*")


class AccountStore:
    def __init__(self, database: str | Path, secret: str):
        self.path = Path(database)
        self.secret = secret.encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                session_version INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                password_reset_requested INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
            if "is_admin" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "password_reset_requested" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN password_reset_requested INTEGER NOT NULL DEFAULT 0")
            if "session_version" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")

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
        if encoded.startswith("pbkdf2_sha256$"):
            try:
                _, rounds, salt, expected = encoded.split("$", 3)
                decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
                actual = hashlib.pbkdf2_hmac("sha256", password.encode(), decode(salt), int(rounds))
                return hmac.compare_digest(actual, decode(expected))
            except (ValueError, TypeError):
                return False
        # Preserve existing Flask/Werkzeug password hashes during account migration.
        try:
            from werkzeug.security import check_password_hash
            return check_password_hash(encoded, password)
        except ImportError:
            return False

    def authenticate(self, username: str, password: str):
        with self.connect() as db:
            user = db.execute("SELECT id, username, password_hash FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if user and self.verify_password(password, user["password_hash"]):
            return {"id": user["id"], "username": user["username"]}
        return None

    def account(self, user_id):
        with self.connect() as db:
            row = db.execute("SELECT id, username, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
        return {**row, "is_admin": bool(row["is_admin"])} if row else None

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
            db.execute("UPDATE users SET is_admin=? WHERE id=?", (enabled, user["id"]))

    def list_accounts(self):
        with self.connect() as db:
            return db.execute("SELECT username, is_admin, password_reset_requested FROM users ORDER BY username").fetchall()

    def request_password_reset(self, username):
        with self.connect() as db:
            db.execute("UPDATE users SET password_reset_requested=1 WHERE username=?", (username,))

    def reset_password(self, username, password):
        if not 8 <= len(password) <= 255:
            raise ValueError("Use a password between 8 and 255 characters.")
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

    def register(self, username: str, password: str):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)", (username, self.hash_password(password)))
            return {"id": cursor.lastrowid, "username": username}

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
        if not token or "." not in token:
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
    def __init__(self, accounts: AccountStore, services: dict[str, str], port: int):
        self.accounts, self.services, self.port = accounts, services, port
        self.main_session = "dodo_main_session"

    def main_user(self, request: Request):
        return self.session_user(request.cookies.get(self.main_session), "server")

    def get_service_user(self, service: str, token: str | None):
        return self.session_user(token, service)

    def session_user(self, token: str | None, audience: str):
        payload = self.accounts.read_claims(token, audience)
        if not payload or not self.accounts.is_current(payload):
            return None
        return self.accounts.account(int(payload["sub"]))

    def issue_assertion(self, source_service: str, target_service: str, user: dict, claims: dict | None = None, ttl: int = 60):
        """Create a brief signed user assertion for a trusted service call."""
        if source_service not in self.services or target_service not in self.services:
            raise ValueError("Assertions can only be issued between registered services")
        return self.accounts.issue({"sub": user["id"], "name": user["username"], "ver": self.accounts.session_version(user["id"]), "aud": target_service,
                                   "iss": source_service, "claims": claims or {},
                                   "exp": int(time.time()) + max(1, min(ttl, 300))})

    def verify_assertion(self, source_service: str, target_service: str, token: str):
        payload = self.accounts.read_claims(token, target_service)
        if not payload or payload.get("iss") != source_service or not self.accounts.is_current(payload):
            return None
        return {"user": {"id": int(payload["sub"]), "username": payload["name"]},
                "claims": payload.get("claims", {})}

    def service_url(self, service: str, path: str = "/"):
        host = self.services[service]
        return f"http://{host}:{self.port}{path}"

    def authorize_redirect(self, service: str, request: Request):
        path = request.path
        if request.query:
            path += "?" + urlencode([(key, value) for key, values in request.query.items() for value in values])
        query = urlencode({"service": service, "path": path})
        return redirect(f"http://localhost:{self.port}/auth/authorize?{query}")

    def handle_service(self, service: str, request: Request):
        if request.path == "/_auth/logout":
            return Response(b"", 200, {"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "dodo_session=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly", "Refresh": "0; url=/"})
        if request.path == "/_auth/complete":
            if not self.get_service_user(service, request.cookies.get("dodo_session")):
                return self.sign_in_problem(
                    service, "Allow cookies for this service and try signing in again.", 403,
                )
            response = redirect(local_path(request.value("path", "/")))
        elif request.path == "/_auth/callback":
            ticket = self.accounts.read_claims(request.value("ticket"), "ticket:" + service)
            if not ticket or not self.accounts.is_current(ticket):
                return self.sign_in_problem(
                    service, "This sign-in link has expired. Start again from the service.", 400,
                )
            lifetime = 12 * 3600
            session = self.accounts.issue({
                "sub": ticket["sub"], "name": ticket["name"], "aud": service,
                "ver": ticket["ver"], "exp": int(time.time()) + lifetime,
            })
            request.set_cookie("dodo_session", session, max_age=lifetime)
            response = redirect("/_auth/complete?" + urlencode({"path": ticket["path"]}))
        else:
            return None
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def sign_in_problem(self, service: str, message: str, status: int):
        response = Response.html(pages.sign_in_problem(
            service, message, self.service_url(service), f"http://localhost:{self.port}",
        ), status)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    def handle_main(self, request: Request):
        user = self.main_user(request)
        path = request.path
        if path == "/auth/authorize":
            service, target = request.value("service"), request.value("path", "/")
            if service not in self.services:
                return Response.text("Unknown service", 404)
            if not target.startswith("/") or target.startswith("//"):
                target = "/"
            if user:
                return self._issue_ticket(service, user, target)
            return Response.html(pages.auth_page("login", service=service, target=target))
        if path == "/forgot-password":
            submitted = request.method == "POST"
            if submitted:
                self.accounts.request_password_reset(request.value("username").strip())
            return Response.html(pages.reset_request_page(submitted))
        if path in {"/login", "/signup"}:
            mode = "signup" if path == "/signup" else "login"
            service, target = request.value("service"), request.value("path", "/")
            if request.method == "POST":
                username, password = request.value("username").strip(), request.value("password")
                if mode == "signup":
                    confirmation = request.value("password_confirmation")
                    if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
                        error = "Choose a username with 3–24 letters, numbers, or underscores."
                    elif len(password) < 8:
                        error = "Your password must be at least 8 characters long."
                    elif password != confirmation:
                        error = "Your passwords do not match."
                    else:
                        try:
                            user = self.accounts.register(username, password)
                            error = None
                        except sqlite3.IntegrityError:
                            error = "That username is already taken. Try another one."
                else:
                    user = self.accounts.authenticate(username, password)
                    error = None if user else "The username or password is incorrect."
                if not error:
                    version = self.accounts.session_version(user["id"])
                    request.set_cookie(self.main_session, self.accounts.issue({"sub": user["id"], "name": user["username"], "aud": "server", "ver": version, "exp": int(time.time()) + 30 * 86400}), max_age=30 * 86400)
                    if service in self.services:
                        return self._issue_ticket(service, user, target)
                    return redirect("/")
                return Response.html(pages.auth_page(mode, error=error, service=service, target=target))
            if user:
                return self._issue_ticket(service, user, target) if service in self.services else redirect("/")
            return Response.html(pages.auth_page(mode, service=service, target=target))
        if path == "/logout":
            if user:
                self.accounts.revoke_sessions(user["id"])
            request.delete_cookie(self.main_session)
            service = request.value("service")
            if service in self.services:
                ticket = self.accounts.issue({"sub": 0, "name": "", "aud": "logout:" + service, "exp": int(time.time()) + 90})
                response = redirect(self.service_url(service, "/_auth/logout?ticket=" + quote(ticket)))
            else:
                response = redirect("/")
            return response
        if path == "/":
            return Response.html(pages.dashboard(user, {key: self.service_url(key) for key in self.services}, request.value("service")))
        return Response.text("Not found", 404)

    def _issue_ticket(self, service, user, target):
        ticket = self.accounts.issue({
            "sub": user["id"], "name": user["username"],
            "ver": self.accounts.session_version(user["id"]),
            "aud": "ticket:" + service, "exp": int(time.time()) + 120,
            "path": local_path(target),
        })
        return redirect(self.service_url(service, "/_auth/callback?ticket=" + quote(ticket)))
