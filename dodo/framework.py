"""A deliberately small HTTP application framework for Dodo services.

Services register handlers with ``@app.route`` and can use ``request``,
``Response``, ``redirect`` and ``require_user``. The host server mounts each
service on its own localhost subdomain while keeping them in one process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlsplit
import html
import re


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    form: dict[str, list[str]]
    headers: dict[str, str]
    cookies: dict[str, str]
    user: dict | None = None
    flash: str | None = None
    response_cookies: list[str] = field(default_factory=list)

    def value(self, name: str, default: str = "") -> str:
        return self.form.get(name, self.query.get(name, [default]))[0]

    def set_cookie(self, name: str, value: str, *, max_age: int, httponly: bool = True) -> None:
        cookie = f"{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax"
        if httponly:
            cookie += "; HttpOnly"
        self.response_cookies.append(cookie)

    def delete_cookie(self, name: str) -> None:
        self.response_cookies.append(f"{name}=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly")


@dataclass
class Response:
    body: bytes
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def html(cls, body: str, status: int = 200) -> "Response":
        return cls(body.encode("utf-8"), status, {"Content-Type": "text/html; charset=utf-8"})

    @classmethod
    def text(cls, body: str, status: int = 200) -> "Response":
        return cls(body.encode("utf-8"), status, {"Content-Type": "text/plain; charset=utf-8"})


def redirect(location: str, status: int = 303) -> Response:
    return Response(b"", status, {"Location": location})


def safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def require_user(handler: Callable) -> Callable:
    handler.requires_user = True
    return handler


class Application:
    def __init__(self, name: str, *, static_dir: str | Path | None = None):
        self.name = name
        self.static_dir = Path(static_dir).resolve() if static_dir else None
        self.routes: list[tuple[str, re.Pattern, Callable]] = []
        self.auth = None

    def route(self, path: str, methods: tuple[str, ...] = ("GET",)):
        def register(handler: Callable) -> Callable:
            chunks = re.split(r"(<int:[a-zA-Z_][a-zA-Z0-9_]*>)", path)
            expression = "^" + "".join(
                f"(?P<{chunk[5:-1]}>[0-9]+)" if chunk.startswith("<int:") else re.escape(chunk)
                for chunk in chunks
            ) + "$"
            for method in methods:
                self.routes.append((method.upper(), re.compile(expression), handler))
            return handler
        return register

    def static(self, relative: str) -> Response:
        if self.static_dir is None:
            return Response.text("Not found", 404)
        target = (self.static_dir / relative).resolve()
        if self.static_dir not in target.parents or not target.is_file():
            return Response.text("Not found", 404)
        kind = "text/css; charset=utf-8" if target.suffix == ".css" else "image/png" if target.suffix == ".png" else "application/octet-stream"
        return Response(target.read_bytes(), 200, {"Content-Type": kind})

    def dispatch(self, request: Request) -> Response:
        if self.auth:
            request.user = self.auth.get_service_user(self.name, request.cookies.get("dodo_session"))
        if request.path.startswith("/static/"):
            return self.static(request.path.removeprefix("/static/"))
        for method, pattern, handler in self.routes:
            match = pattern.match(request.path)
            if match and method == request.method:
                if getattr(handler, "requires_user", False) and request.user is None:
                    return self.auth.authorize_redirect(self.name, request)
                response = handler(request, **{key: int(value) for key, value in match.groupdict().items()})
                if isinstance(response, str):
                    response = Response.html(response)
                return response
        return Response.text("Not found", 404)


def request_from_handler(handler: BaseHTTPRequestHandler) -> Request:
    parsed = urlsplit(handler.path)
    length = int(handler.headers.get("Content-Length", "0") or 0)
    payload = handler.rfile.read(min(length, 1_000_000)) if length else b""
    form = parse_qs(payload.decode("utf-8", "replace")) if handler.command == "POST" else {}
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except Exception:
        pass
    return Request(handler.command, parsed.path, parse_qs(parsed.query), form,
                   {key.lower(): value for key, value in handler.headers.items()},
                   {key: morsel.value for key, morsel in cookie.items()})


def query_string(**values: str) -> str:
    return urlencode(values)
