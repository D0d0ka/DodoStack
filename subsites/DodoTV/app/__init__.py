"""Dodo TV application factory."""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dodo.web import WebApplication, jsonify, render_template, request, verify_form_token
from dodo.web import CSRFError
from dodo.orm import Profiles
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import BASE_DIR, Config
from .extensions import db
from .models import Genre, User, utcnow
from .services import DEFAULT_SETTINGS, allow_registration, site_settings, time_ago
from .utils.media import (
    ensure_upload_dirs,
    format_duration,
    human_size,
    media_url,
)

log = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


DEFAULT_GENRES = [
    ("Action", "action"),
    ("Adventure", "adventure"),
    ("Animation", "animation"),
    ("Comedy", "comedy"),
    ("Crime", "crime"),
    ("Documentary", "documentary"),
    ("Drama", "drama"),
    ("Family", "family"),
    ("Fantasy", "fantasy"),
    ("History", "history"),
    ("Horror", "horror"),
    ("Music", "music"),
    ("Mystery", "mystery"),
    ("Romance", "romance"),
    ("Science Fiction", "science-fiction"),
    ("Thriller", "thriller"),
    ("War", "war"),
    ("Western", "western"),
]


def _resolve_secret_key(app: WebApplication) -> None:
    if app.config.get("SECRET_KEY"):
        return
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    key_file = instance / ".secret_key"
    if key_file.exists():
        app.config["SECRET_KEY"] = key_file.read_text(encoding="utf-8").strip()
        return
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError:  # pragma: no cover
        pass
    app.config["SECRET_KEY"] = key
    log.warning(
        "SECRET_KEY was not set. A development key was generated at %s. "
        "Set SECRET_KEY in .env before deploying.",
        key_file,
    )


def create_profile(account):
    if not account["is_admin"] and not allow_registration():
        return None
    return User(
        username=profiles.username(account),
        email=f'{account["id"]}@accounts.dodostack.invalid',
        password_hash="",
    )


profiles = Profiles(db, User, create_profile)


def load_profile(account):
    profile = profiles(account)
    if profile and profile.is_active and not request.admin_mount:
        now = utcnow()
        previous = profile.last_login_at
        if previous is None or (now - previous.replace(tzinfo=timezone.utc)).total_seconds() >= 3600:
            profile.last_login_at = now
            db.session.commit()
    return profile if profile and profile.is_active else None


def create_app(config_object=Config) -> WebApplication:
    app = WebApplication(
        __name__,
        static_folder="static",
        template_folder="templates",
    )
    app.instance_path = str(BASE_DIR / "instance")
    app.config.from_object(config_object)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _resolve_secret_key(app)

    # FFmpeg may live in Homebrew paths that GUI-launched processes miss.
    if app.config.get("FFMPEG_BINARY") == "ffmpeg":
        for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if Path(candidate).exists():
                app.config["FFMPEG_BINARY"] = candidate
                break
    if app.config.get("FFPROBE_BINARY") == "ffprobe":
        for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"):
            if Path(candidate).exists():
                app.config["FFPROBE_BINARY"] = candidate
                break

    db.init_app(app)
    app.before_request(verify_form_token)
    app.profile_loader = load_profile

    with app.app_context():
        ensure_upload_dirs()

    from .blueprints.admin import admin_bp
    from .blueprints.api import api_bp
    from .blueprints.auth import auth_bp
    from .blueprints.main import main_bp
    from .blueprints.media import media_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)

    app.admin_tab(
        "Dodo TV", "/admin/", prefixes=("admin.",),
        assets=("static", "media.logo", "media.serve_media", "media.stream_movie",
                "media.stream_episode", "media.stream_trailer", "media.serve_subtitle",
                "media.serve_trailer_subtitle", "main.movie_detail", "main.show_detail",
                "main.watch_movie", "main.watch_episode"),
    )

    @app.context_processor
    def inject_globals():
        try:
            settings = site_settings()
        except Exception:  # pragma: no cover - database not initialized yet
            settings = DEFAULT_SETTINGS.copy()
        try:
            reg = allow_registration()
        except Exception:  # pragma: no cover
            reg = True
        return {
            "site": settings,
            "now_year": datetime.now(timezone.utc).year,
            "ffmpeg_ok": _ffmpeg_is_available(app),
            "allow_registration": reg,
        }

    @app.template_filter("duration")
    def duration_filter(value):
        return format_duration(value)

    @app.template_filter("filesize")
    def filesize_filter(value):
        return human_size(value or 0)

    @app.template_filter("timeago")
    def timeago_filter(value):
        return time_ago(value)

    @app.template_filter("media")
    def media_filter(value):
        return media_url(value)

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(error):
        wants_json = (
            request.accept_mimetypes.best == "application/json"
            or request.path.startswith("/api/")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({"ok": False, "error": "The upload is too large."}), 413
        return render_template("errors/413.html"), 413

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if (
            request.path.startswith("/api/")
            or request.is_json
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        ):
            return jsonify({"ok": False, "error": "Invalid or expired security token."}), 400
        return app.failure(request, 400, api=False, detail=error.description)

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        if (
            request.path.startswith("/api/")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        ):
            return jsonify({"ok": False, "error": "The server could not complete that request."}), 500
        return render_template("errors/500.html"), 500

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "app": "Dodo TV"})

    if app.config.get("AUTO_INIT_DB"):
        with app.app_context():
            db.create_all()
            seed_genres()

    return app


def _ffmpeg_is_available(app: WebApplication) -> bool:
    from .utils.media import _binary_available

    cached = app.state.get("dodotv_ffmpeg_available")
    if cached is None:
        cached = _binary_available(
            app.config.get("FFMPEG_BINARY", "ffmpeg")
        ) and _binary_available(app.config.get("FFPROBE_BINARY", "ffprobe"))
        app.state["dodotv_ffmpeg_available"] = cached
    return bool(cached)


def seed_genres() -> None:
    existing = {genre.slug for genre in Genre.query.all()}
    added = 0
    for name, slug in DEFAULT_GENRES:
        if slug not in existing:
            db.session.add(Genre(name=name, slug=slug))
            added += 1
    if added:
        db.session.commit()
        log.info("Seeded %s genres", added)


