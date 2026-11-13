"""Dodo's routed, template and streaming runtime for larger services."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import hmac
import logging
import secrets
import sys

from itsdangerous import BadSignature, URLSafeTimedSerializer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from wtforms import Form
from werkzeug.datastructures import CombinedMultiDict
from werkzeug.exceptions import HTTPException, NotFound, abort
from werkzeug.local import LocalProxy
from werkzeug.routing import Map, Rule
from werkzeug.utils import redirect, send_file as _file, send_from_directory as _directory
from werkzeug.wrappers import Request, Response

from .diagnostics import request_id
from .framework import cookie_name
from .pages import error_response

_app = ContextVar("dodo_app")
_request = ContextVar("dodo_request")
current_app = LocalProxy(_app)
request = LocalProxy(_request)
current_user = LocalProxy(lambda: request.profile)


class Guest:
    is_authenticated = False
    is_active = False
    is_admin = False
    role = "user"
    id = None


class UserMixin:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def get_id(self):
        return str(self.id)


@dataclass(frozen=True)
class SharedProfile(UserMixin):
    id: int
    username: str
    is_admin: bool


class Config(dict):
    def from_object(self, source):
        self.update({key: getattr(source, key) for key in dir(source) if key.isupper()})


class Blueprint:
    def __init__(self, name, import_name, url_prefix=""):
        self.name = name
        self.url_prefix = url_prefix
        self.routes = []

    def route(self, path, methods=("GET",), endpoint=None):
        def add(view):
            self.routes.append((path, methods, endpoint or view.__name__, view))
            return view
        return add

    def get(self, path):
        return self.route(path)

    def post(self, path):
        return self.route(path, ("POST",))

    def delete(self, path):
        return self.route(path, ("DELETE",))


@dataclass(frozen=True)
class AdminTab:
    label: str
    entry: str
    endpoints: frozenset[str]
    prefixes: tuple[str, ...]
    assets: frozenset[str]
    on_service: bool


class WebApplication(Blueprint):
    def __init__(self, import_name, *, static_folder="static", template_folder="templates"):
        super().__init__(import_name, import_name)
        self.root_path = str(Path(sys.modules[import_name].__file__).resolve().parent)
        self.instance_path = str(Path(self.root_path) / "instance")
        self.static_folder = Path(self.root_path) / static_folder
        self.config = Config()
        self.state = {}

        self.url_map = Map(strict_slashes=False)
        self.view_functions = {}
        self.before = []
        self.after = []
        self.teardown = []
        self.processors = []
        self.errors = {}
        self.admin = None
        self.profile_loader = None
        self.logger = logging.getLogger(import_name)
        self.templates = Environment(
            loader=FileSystemLoader(Path(self.root_path) / template_folder),
            autoescape=select_autoescape(),
        )
        self.add_rule("/static/<path:filename>", ("GET",), "static", self.send_static_file)

    def send_static_file(self, filename):
        return send_from_directory(self.static_folder, filename)

    def add_rule(self, path, methods, endpoint, view):
        self.url_map.add(Rule(path, methods=methods, endpoint=endpoint))
        self.view_functions[endpoint] = view

    def route(self, path, methods=("GET",), endpoint=None):
        def add(view):
            self.add_rule(path, methods, endpoint or view.__name__, view)
            return view
        return add

    def register_blueprint(self, blueprint):
        for path, methods, endpoint, view in blueprint.routes:
            self.add_rule(blueprint.url_prefix + path, methods, f"{blueprint.name}.{endpoint}", view)

    def admin_tab(self, label, entry, *, endpoints=(), prefixes=(), assets=(), on_service=False):
        """Register a tab and route its endpoints through the central admin gate."""
        self.admin = AdminTab(label, entry, frozenset(endpoints), tuple(prefixes), frozenset(assets), on_service)

    def is_admin_endpoint(self, endpoint):
        return bool(self.admin and (endpoint in self.admin.endpoints or endpoint.startswith(self.admin.prefixes)))

    def is_admin_asset(self, endpoint):
        return bool(self.admin and endpoint in self.admin.assets)

    def before_request(self, callback):
        self.before.append(callback)
        return callback

    def after_request(self, callback):
        self.after.append(callback)
        return callback

    def context_processor(self, callback):
        self.processors.append(callback)
        return callback

    def template_filter(self, name):
        def add(callback):
            self.templates.filters[name] = callback
            return callback
        return add

    def errorhandler(self, key):
        def add(callback):
            self.errors[key] = callback
            return callback
        return add

    @contextmanager
    def app_context(self):
        token = _app.set(self)
        try:
            yield
        finally:
            try:
                for callback in self.teardown:
                    callback()
            finally:
                _app.reset(token)

    def serve(self, environ, start_response, *, path, identity, user, service, admin=False, api=False):
        incoming = Request(environ)
        incoming.max_content_length = self.config.get("MAX_CONTENT_LENGTH", 2 * 1024 * 1024)
        incoming.identity = identity
        incoming.shared_user = user
        incoming.service = service
        incoming.admin_mount = admin
        incoming.profile = Guest()
        incoming.endpoint = ""
        incoming.adapter = self.url_map.bind(incoming.host, url_scheme=incoming.scheme)
        secret = self.config.get("SECRET_KEY") or identity.accounts.secret
        session_cookie = identity.main_session if admin else identity.service_session
        session = incoming.cookies.get(session_cookie, "")
        serializer = URLSafeTimedSerializer(secret, salt=f"dodo-web:{service}:{admin}:{session}")
        name = cookie_name(f"dodo_ui_{service}" + ("_admin" if admin else ""), identity.secure)
        raw = incoming.cookies.get(name)
        try:
            incoming.ui = serializer.loads(raw, max_age=86400) if raw else {}
        except BadSignature:
            incoming.ui = {}
        original = incoming.ui.copy()
        token = _request.set(incoming)
        try:
            with self.app_context():
                try:
                    endpoint, values = incoming.adapter.match(path, method=incoming.method)
                    incoming.endpoint = endpoint
                    self.logger.debug("Matched endpoint=%s service=%s admin_mount=%s", endpoint, service, admin)
                    admin_endpoint = self.is_admin_endpoint(endpoint)
                    admin_asset = self.is_admin_asset(endpoint) and incoming.method in {"GET", "HEAD"}
                    if admin and not (admin_endpoint or admin_asset):
                        self.logger.debug("Endpoint is outside the workspace admin mount")
                        raise NotFound()
                    if not admin and admin_endpoint and not self.admin.on_service:
                        self.logger.debug("Endpoint is available only on the workspace admin mount")
                        raise NotFound()
                    if user and self.profile_loader:
                        incoming.profile = self.profile_loader(user) or Guest()
                    elif user:
                        incoming.profile = SharedProfile(user["id"], user["username"], bool(user["is_admin"]))
                    result = None
                    for callback in self.before:
                        result = callback()
                        if result is not None:
                            self.logger.debug("Request handled by before_request=%s", callback.__name__)
                            break
                    if result is None:
                        result = self.view_functions[endpoint](**values)
                except HTTPException as error:
                    self.logger.debug("HTTP error status=%s type=%s endpoint=%s", error.code,
                                      type(error).__name__, incoming.endpoint or "<unmatched>")
                    handler = self.errors.get(type(error))
                    if not handler and (not api or error.code not in {403, 404}):
                        handler = self.errors.get(error.code)
                    if handler:
                        result = handler(error)
                    elif error.code and error.code >= 400:
                        detail = error.description if isinstance(error, CSRFError) else None
                        result = self.failure(incoming, error.code, api=api, detail=detail)
                        for key, value in error.get_headers(environ):
                            if key.lower() not in {"content-type", "content-length"}:
                                result.headers.add(key, value)
                    else:
                        result = error
                except Exception as error:
                    self.logger.exception("Service request failed endpoint=%s", incoming.endpoint or "<unmatched>")
                    handler = self.errors.get(500)
                    result = handler(error) if handler else self.failure(incoming, 500, api=api)
                response = as_response(result)
                for callback in self.after:
                    response = callback(response)
                if incoming.ui != original:
                    response.set_cookie(name, serializer.dumps(incoming.ui), httponly=True,
                                        samesite="Lax", secure=identity.secure)
                if admin:
                    response.headers["Cache-Control"] = "no-store"
            return response(environ, start_response)
        finally:
            _request.reset(token)

    @staticmethod
    def failure(incoming, status, *, api, detail=None):
        error = error_response(status, workspace_url=incoming.identity.workspace_url,
                               service=incoming.service, api=api, detail=detail)
        return Response(error.body, status=status, headers=error.headers)


def as_response(value):
    if isinstance(value, tuple):
        body, status, *headers = value
        response = as_response(body)
        response.status_code = status
        if headers:
            response.headers.update(headers[0])
        return response
    if isinstance(value, (dict, list)):
        return jsonify(value)
    if isinstance(value, HTTPException):
        return value.get_response(request.environ)
    return value if isinstance(value, Response) else Response(value, mimetype="text/html")


def url_for(endpoint, **values):
    external = values.pop("_external", False)
    anchor = values.pop("_anchor", None)
    if endpoint.startswith("."):
        endpoint = request.endpoint.split(".")[0] + endpoint
    path = request.adapter.build(endpoint, values)
    origin = request.identity.workspace_url
    admin_endpoint = current_app.is_admin_endpoint(endpoint)
    if ((admin_endpoint and (request.admin_mount or not current_app.admin.on_service))
            or (request.admin_mount and current_app.is_admin_asset(endpoint))):
        result = f"/admin/{request.service}{path}"
        result = origin + result if external or not request.admin_mount else result
    else:
        result = request.identity.service_url(request.service, path) if external or request.admin_mount else path
    return result + ("#" + anchor if anchor else "")


def render_template(name, **values):
    from .pages import admin_navigation
    navigation = Markup(admin_navigation(request.identity.admin_tabs, request.service)) if request.admin_mount else ""
    context = {"admin_navigation": navigation, "url_for": url_for, "request": request, "current_user": current_user,
               "get_flashed_messages": get_flashed_messages, "csrf_token": csrf_token,
               "request_id": request_id.get(),
               "admin_base": f"/admin/{request.service}", "admin_preview": request.admin_mount and shared_admin(),
               "workspace_url": request.identity.workspace_url}
    for callback in current_app.processors:
        context.update(callback())
    return current_app.templates.get_template(name).render(**(context | values))


def jsonify(value=None, **kwargs):
    import json
    return Response(json.dumps(kwargs if kwargs else value), mimetype="application/json")


def flash(message, category="message"):
    request.ui["_flashes"] = [*request.ui.get("_flashes", []), (category, message)][-12:]


def get_flashed_messages(with_categories=False, category_filter=()):
    if not hasattr(request, "_flashes"):
        request._flashes = request.ui.pop("_flashes", [])
    return [item if with_categories else item[1] for item in request._flashes
            if not category_filter or item[0] in category_filter]


def send_file(path, **options):
    return _file(path, request.environ, response_class=Response, **options)


def send_from_directory(directory, path, **options):
    return _directory(directory, path, request.environ, response_class=Response, **options)


def require_login(view):
    from functools import wraps

    @wraps(view)
    def protected(*args, **kwargs):
        if current_user.is_authenticated and current_user.is_active:
            return view(*args, **kwargs)
        if request.shared_user:
            return Response("This service profile is unavailable. Contact an administrator.", status=403)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Sign in to continue."}), 401
        return shared_signin(request.full_path.rstrip("?"))
    return protected


def shared_signin(target="/", *, signup=False):
    from urllib.parse import urlencode
    endpoint = "/signup" if signup else "/auth/authorize"
    return redirect(f"{request.identity.workspace_url}{endpoint}?" +
                    urlencode({"service": request.service, "path": target}), 303)


def shared_signout():
    identity = request.identity
    result = identity.sign_out_service(request.shared_user)
    response = Response(result.body, status=result.status, headers=result.headers)
    response.delete_cookie(identity.service_session, secure=identity.secure, httponly=True, samesite="Lax")
    return response


def csrf_token():
    if "csrf" not in request.ui:
        request.ui["csrf"] = secrets.token_urlsafe(32)
    return request.ui["csrf"]


def verify_form_token():
    if not current_app.config.get("WTF_CSRF_ENABLED", True):
        return
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
        expected = request.ui.get("csrf")
        if not expected or not token.isascii() or not hmac.compare_digest(token, expected):
            current_app.logger.debug("Rejected invalid CSRF token")
            raise CSRFError()


class CSRFError(HTTPException):
    code = 400
    description = "Invalid or expired security token."


class WebForm(Form):
    def __init__(self, *args, **kwargs):
        formdata = CombinedMultiDict((request.files, request.form)) if request.method == "POST" else None
        super().__init__(formdata, *args, **kwargs)

    def validate_on_submit(self):
        return request.method == "POST" and self.validate()

    def hidden_tag(self):
        return Markup('<input type="hidden" name="csrf_token" value="{}">').format(csrf_token())


def shared_admin():
    return bool(request.shared_user and request.shared_user["is_admin"])


def admin_only(view):
    from functools import wraps

    @wraps(view)
    def protected(*args, **kwargs):
        if not request.admin_mount or not shared_admin():
            abort(403)
        return view(*args, **kwargs)
    return protected
