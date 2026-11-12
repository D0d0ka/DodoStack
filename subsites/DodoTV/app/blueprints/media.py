"""Safe media delivery, video streaming and subtitle routes."""
from __future__ import annotations

from pathlib import Path

from dodo.web import Blueprint, Response, abort, send_file
from dodo.web import request, shared_admin

from ..extensions import db
from ..models import Episode, Movie, Subtitle, TrailerSubtitle, TVShow
from ..utils.media import IMAGE_EXTENSIONS, resolve_path
from ..utils.streaming import send_video

media_bp = Blueprint("media", __name__)


def is_admin() -> bool:
    return request.admin_mount and shared_admin()


def can_view_movie(movie: Movie) -> bool:
    return movie.published or is_admin()


def can_view_show(show: TVShow) -> bool:
    return show.published or is_admin()


def can_view_episode(episode: Episode) -> bool:
    return can_view_show(episode.show) and (episode.published or is_admin())


@media_bp.get("/media/<path:filename>")
def serve_media(filename: str):
    """Serve public image assets (posters and generated frames) only."""
    clean = filename.lstrip("/")
    suffix = Path(clean).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        abort(404)
    path = resolve_path(clean)
    if path is None or not path.is_file():
        abort(404)
    response = send_file(path, conditional=True, max_age=86400)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@media_bp.get("/brand/logo.png")
def logo():
    from ..config import BASE_DIR

    path = BASE_DIR / "dodotv.png"
    if not path.exists():
        abort(404)
    response = send_file(path, mimetype="image/png", conditional=True, max_age=604800)
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@media_bp.get("/stream/movie/<int:content_id>")
def stream_movie(content_id: int):
    movie = db.session.get(Movie, content_id)
    if movie is None or not movie.video_path:
        abort(404)
    if not can_view_movie(movie):
        abort(404)
    return send_video(movie.video_path)


@media_bp.get("/stream/episode/<int:content_id>")
def stream_episode(content_id: int):
    episode = db.session.get(Episode, content_id)
    if episode is None or not episode.video_path:
        abort(404)
    if not can_view_episode(episode):
        abort(404)
    return send_video(episode.video_path)


@media_bp.get("/stream/trailer/<kind>/<int:content_id>")
def stream_trailer(kind: str, content_id: int):
    if kind == "movie":
        movie = db.session.get(Movie, content_id)
        if movie is None or not movie.trailer_path or not can_view_movie(movie):
            abort(404)
        return send_video(movie.trailer_path)
    if kind == "show":
        show = db.session.get(TVShow, content_id)
        if show is None or not show.trailer_path or not can_view_show(show):
            abort(404)
        return send_video(show.trailer_path)
    abort(404)


def _vtt_response(file_path: str) -> Response:
    path = resolve_path(file_path)
    if path is None or not path.is_file():
        abort(404)
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        abort(404)
    response = Response(content, mimetype="text/vtt")
    response.headers["Content-Type"] = "text/vtt; charset=utf-8"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"
    return response


@media_bp.get("/subtitles/<int:subtitle_id>")
def serve_subtitle(subtitle_id: int):
    subtitle = db.session.get(Subtitle, subtitle_id)
    if subtitle is None or (subtitle.movie is None and subtitle.episode is None):
        abort(404)
    if subtitle.movie and not can_view_movie(subtitle.movie):
        abort(404)
    if subtitle.episode and not can_view_episode(subtitle.episode):
        abort(404)
    return _vtt_response(subtitle.file_path)


@media_bp.get("/trailer-subtitles/<int:subtitle_id>")
def serve_trailer_subtitle(subtitle_id: int):
    subtitle = db.session.get(TrailerSubtitle, subtitle_id)
    if subtitle is None:
        abort(404)
    if subtitle.movie and not can_view_movie(subtitle.movie):
        abort(404)
    if subtitle.show and not can_view_show(subtitle.show):
        abort(404)
    if subtitle.movie is None and subtitle.show is None:
        abort(404)
    return _vtt_response(subtitle.file_path)
