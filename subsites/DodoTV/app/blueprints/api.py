"""JSON API for playback progress."""
from __future__ import annotations

from math import isfinite

from dodo.web import Blueprint, jsonify, request
from dodo.web import current_user, require_login

from ..extensions import db
from ..models import Episode, Movie, WatchProgress
from .media import can_view_episode, can_view_movie

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.post("/progress")
@require_login
def save_progress():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid playback data."}), 400

    kind = data.get("kind")
    if kind not in ("movie", "episode"):
        return jsonify({"ok": False, "error": "Unknown content type."}), 400

    try:
        raw_id = data.get("id")
        if type(raw_id) not in (int, str):
            raise ValueError
        content_id = int(raw_id)
        position = float(data.get("position", 0))
        duration = float(data.get("duration", 0))
        if not 0 < content_id <= 2**63 - 1 or not (isfinite(position) and isfinite(duration)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return jsonify({"ok": False, "error": "Invalid playback data."}), 400

    movie = episode = None
    if kind == "movie":
        movie = db.session.get(Movie, content_id)
        if movie is None or not can_view_movie(movie):
            return jsonify({"ok": False, "error": "Content not found."}), 404
    else:
        episode = db.session.get(Episode, content_id)
        if episode is None or not can_view_episode(episode):
            return jsonify({"ok": False, "error": "Content not found."}), 404

    progress = WatchProgress.query.filter_by(
        user_id=current_user.id,
        movie_id=movie.id if movie else None,
        episode_id=episode.id if episode else None,
    ).first()
    if progress is None:
        progress = WatchProgress(
            user_id=current_user.id,
            movie_id=movie.id if movie else None,
            episode_id=episode.id if episode else None,
        )
        db.session.add(progress)

    if duration <= 0:
        duration = progress.duration_seconds
    position = max(0.0, position)
    progress.position = min(position, duration) if duration else position
    progress.duration = duration
    progress.completed = bool(
        progress.duration and progress.position / progress.duration >= 0.95
    )
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "position": round(progress.position, 2),
            "percent": progress.percent,
            "completed": progress.completed,
        }
    )


@api_bp.get("/progress/<kind>/<int:content_id>")
@require_login
def get_progress(kind: str, content_id: int):
    if kind not in {"movie", "episode"}:
        return jsonify({"ok": False, "error": "Unknown content type."}), 400
    progress = WatchProgress.query.filter_by(
        user_id=current_user.id,
        movie_id=content_id if kind == "movie" else None,
        episode_id=content_id if kind == "episode" else None,
    ).first()
    if progress is None:
        return jsonify({"ok": True, "position": 0, "duration": 0, "percent": 0})
    return jsonify(
        {
            "ok": True,
            "position": progress.position_seconds,
            "duration": progress.duration_seconds,
            "percent": progress.percent,
            "completed": progress.completed,
        }
    )
