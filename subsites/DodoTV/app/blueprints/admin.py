"""Administrator dashboard and content management routes."""
from __future__ import annotations

import mimetypes
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dodo.web import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from dodo.web import current_user, admin_only, request
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from ..extensions import db
from ..forms import (
    BulkApplyForm,
    BulkUploadForm,
    EpisodeForm,
    MovieForm,
    SeasonForm,
    SettingsForm,
    ShowForm,
    SubtitleForm,
)
from ..models import (
    Episode,
    Genre,
    MediaAsset,
    Movie,
    Season,
    Setting,
    Subtitle,
    TrailerSubtitle,
    TVShow,
    User,
    WatchHistory,
    WatchProgress,
    utcnow,
)
from ..services import get_setting, movie_card, poster_for, show_card, site_settings, time_ago
from ..utils.media import (
    MediaError,
    add_frames_to_db,
    collect_owner_files,
    copy_to_thumbnails,
    delete_relative,
    ensure_upload_dirs,
    ffmpeg_available,
    format_duration,
    generate_frames,
    human_size,
    probe_video,
    resolve_path,
    save_image,
    save_subtitle,
    save_video,
    storage_usage,
    upload_root,
)
from .media import can_view_movie, can_view_show

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _set_genre_choices(form) -> None:
    form.genre_ids.choices = [
        (genre.id, genre.name) for genre in Genre.query.order_by(Genre.name).all()
    ]


def _runtime_minutes(seconds: int | None) -> int | None:
    """Convert stored seconds to a minute value for the admin form.

    Demo clips and trailers can be shorter than a minute. Leaving the field
    blank in that case preserves the exact duration instead of rounding it
    down to zero.
    """
    if not seconds or seconds < 60:
        return None
    return max(1, round(seconds / 60))


def _genres_from_ids(ids: list[int]) -> list[Genre]:
    if not ids:
        return []
    return Genre.query.filter(Genre.id.in_(ids)).all()


def _populate_movie(movie: Movie, form: MovieForm) -> None:
    movie.title = form.title.data.strip()
    movie.creator = (form.creator.data or "").strip()
    movie.description = (form.description.data or "").strip()
    movie.release_year = form.release_year.data
    movie.age_rating = form.age_rating.data
    movie.featured = bool(form.featured.data)
    # A blank runtime means "read it from the video" (or keep the current
    # value when editing). Only overwrite when the administrator typed one.
    if form.runtime.data:
        movie.runtime = int(form.runtime.data) * 60
    movie.genres = _genres_from_ids(form.genre_ids.data)


def _populate_show(show: TVShow, form: ShowForm) -> None:
    show.title = form.title.data.strip()
    show.creator = (form.creator.data or "").strip()
    show.description = (form.description.data or "").strip()
    show.release_year = form.release_year.data
    show.age_rating = form.age_rating.data
    show.featured = bool(form.featured.data)
    show.genres = _genres_from_ids(form.genre_ids.data)


def _movie_publish_problems(movie: Movie) -> list[str]:
    problems = []
    if not movie.video_path:
        problems.append("a movie video file")
    if not movie.poster_path:
        problems.append("a poster (upload one or choose a generated frame)")
    return problems


def _show_publish_problems(show: TVShow) -> list[str]:
    problems = []
    if not show.poster_path:
        problems.append("a poster image")
    if not any(episode.published for episode in show.episodes):
        problems.append("at least one published episode")
    return problems


def _episode_publish_problems(episode: Episode) -> list[str]:
    if not episode.video_path:
        return ["an episode video file"]
    return []


def _flash_problems(problems: list[str], subject: str) -> None:
    if problems:
        flash(
            f"{subject} was saved as a draft. Add {', '.join(problems)} before publishing.",
            "warning",
        )


def _remove_frames(owner) -> list[str]:
    paths = []
    for asset in list(owner.media_assets):
        paths.append(asset.path)
        db.session.delete(asset)
    return paths


def _delete_paths_after_commit(paths: list[str]) -> None:
    for path in paths:
        delete_relative(path)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@admin_bp.get("/")
@admin_only
def dashboard():
    usage = storage_usage()
    stats = {
        "users": User.query.count(),
        "admins": User.query.filter_by(is_admin=True).count(),
        "movies": Movie.query.count(),
        "published_movies": Movie.query.filter_by(published=True).count(),
        "shows": TVShow.query.count(),
        "published_shows": TVShow.query.filter_by(published=True).count(),
        "episodes": Episode.query.count(),
        "published_episodes": Episode.query.filter_by(published=True).count(),
        "watch_sessions": WatchHistory.query.count(),
    }
    watch_seconds = db.session.query(db.func.coalesce(db.func.sum(WatchProgress.position), 0)).scalar() or 0
    stats["watch_minutes"] = int(watch_seconds // 60)

    recent_activity = (
        WatchHistory.query.order_by(WatchHistory.watched_at.desc()).limit(8).all()
    )
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    drafts = (
        Movie.query.filter_by(published=False).order_by(Movie.updated_at.desc()).limit(5).all()
        + TVShow.query.filter_by(published=False).order_by(TVShow.updated_at.desc()).limit(5).all()
    )

    today = datetime.now(timezone.utc).date()
    day_counts = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        count = WatchHistory.query.filter(
            WatchHistory.watched_at >= start, WatchHistory.watched_at < end
        ).count()
        day_counts.append({"label": day.strftime("%a"), "count": count, "date": day})
    max_day = max((row["count"] for row in day_counts), default=0) or 1
    for row in day_counts:
        row["height"] = max(6, round(row["count"] / max_day * 100))

    recent_movies = Movie.query.order_by(Movie.created_at.desc()).limit(4).all()
    recent_shows = TVShow.query.order_by(TVShow.created_at.desc()).limit(4).all()

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        usage=usage,
        recent_activity=recent_activity,
        recent_users=recent_users,
        drafts=drafts,
        day_counts=day_counts,
        recent_content=sorted(
            recent_movies + recent_shows,
            key=lambda item: item.created_at,
            reverse=True,
        )[:6],
        ffmpeg=ffmpeg_available(),
    )


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------
@admin_bp.get("/movies")
@admin_only
def movies():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "all")
    query = Movie.query
    if q:
        query = query.filter(Movie.title.ilike(f"%{q}%"))
    if status == "published":
        query = query.filter_by(published=True)
    elif status == "draft":
        query = query.filter_by(published=False)
    items = query.order_by(Movie.created_at.desc()).all()
    return render_template("admin/movies.html", movies=items, q=q, status=status)


@admin_bp.route("/movies/new", methods=["GET", "POST"])
@admin_only
def movie_new():
    form = MovieForm()
    _set_genre_choices(form)
    if form.validate_on_submit():
        new_files: list[str] = []
        try:
            movie = Movie()
            _populate_movie(movie, form)
            video_uploaded = False
            if form.video.data:
                relative, info = save_video(form.video.data, "movies", "movie video")
                new_files.append(relative)
                movie.video_path = relative
                video_uploaded = True
                if not movie.runtime:
                    movie.runtime = max(1, int(round(info["duration"])))
            if form.trailer.data:
                relative, _ = save_video(form.trailer.data, "trailers", "trailer")
                new_files.append(relative)
                movie.trailer_path = relative
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "poster")
                new_files.append(relative)
                movie.poster_path = relative

            movie.published = bool(form.published.data)
            db.session.add(movie)
            db.session.flush()

            if video_uploaded:
                try:
                    frames = generate_frames(movie.video_path, f"movie_{movie.id}")
                    add_frames_to_db(movie, frames)
                except MediaError as exc:
                    flash(f"Movie saved, but preview frames failed: {exc}", "warning")

            if movie.published:
                problems = _movie_publish_problems(movie)
                if problems:
                    movie.published = False
                    _flash_problems(problems, "Movie")
            db.session.commit()
            flash(f"“{movie.title}” was created.", "success")
            if not movie.poster_path:
                flash("Choose one of the generated frames below to set the poster.", "info")
            return redirect(url_for("admin.movie_edit", movie_id=movie.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")
        except Exception:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            raise
    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template("admin/movie_form.html", form=form, movie=None)


@admin_bp.route("/movies/<int:movie_id>/edit", methods=["GET", "POST"])
@admin_only
def movie_edit(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None:
        abort(404)
    form = MovieForm(obj=movie)
    _set_genre_choices(form)
    if request.method == "GET":
        form.genre_ids.data = [genre.id for genre in movie.genres]
        form.runtime.data = _runtime_minutes(movie.runtime)

    if form.validate_on_submit():
        old_files: list[str] = []
        new_files: list[str] = []
        try:
            video_replaced = False
            if form.video.data:
                relative, info = save_video(form.video.data, "movies", "movie video")
                new_files.append(relative)
                old_files.append(movie.video_path) if movie.video_path else None
                old_files.extend(_remove_frames(movie))
                movie.video_path = relative
                video_replaced = True
                if not form.runtime.data:
                    movie.runtime = max(1, int(round(info["duration"])))
            if form.trailer.data:
                relative, _ = save_video(form.trailer.data, "trailers", "trailer")
                new_files.append(relative)
                if movie.trailer_path:
                    old_files.append(movie.trailer_path)
                movie.trailer_path = relative
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "poster")
                new_files.append(relative)
                if movie.poster_path:
                    old_files.append(movie.poster_path)
                movie.poster_path = relative

            _populate_movie(movie, form)
            movie.published = bool(form.published.data)

            if video_replaced:
                try:
                    frames = generate_frames(movie.video_path, f"movie_{movie.id}")
                    add_frames_to_db(movie, frames)
                except MediaError as exc:
                    flash(f"Video replaced, but preview frames failed: {exc}", "warning")

            if movie.published:
                problems = _movie_publish_problems(movie)
                if problems:
                    movie.published = False
                    _flash_problems(problems, "Movie")
            db.session.commit()
            _delete_paths_after_commit([p for p in old_files if p])
            flash("Movie updated.", "success")
            return redirect(url_for("admin.movie_edit", movie_id=movie.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash("That change conflicts with an existing record.", "error")

    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")

    subtitle_form = SubtitleForm()
    return render_template(
        "admin/movie_form.html",
        form=form,
        movie=movie,
        subtitle_form=subtitle_form,
        trailer_subtitle_form=SubtitleForm(),
        frames=[asset for asset in movie.media_assets if asset.kind == "frame"],
    )


@admin_bp.post("/movies/<int:movie_id>/delete")
@admin_only
def movie_delete(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None:
        abort(404)
    files = collect_owner_files(movie=movie)
    title = movie.title
    db.session.delete(movie)
    db.session.commit()
    _delete_paths_after_commit(list(files))
    flash(f"“{title}” and its media were deleted.", "success")
    return redirect(url_for("admin.movies"))


@admin_bp.post("/movies/<int:movie_id>/toggle-publish")
@admin_only
def movie_toggle_publish(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None:
        abort(404)
    if not movie.published:
        problems = _movie_publish_problems(movie)
        if problems:
            _flash_problems(problems, "Movie")
            return redirect(request.referrer or url_for("admin.movies"))
        movie.published = True
        flash("Movie published.", "success")
    else:
        movie.published = False
        flash("Movie unpublished and hidden from viewers.", "info")
    db.session.commit()
    return redirect(request.referrer or url_for("admin.movies"))


@admin_bp.post("/movies/<int:movie_id>/toggle-feature")
@admin_only
def movie_toggle_feature(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None:
        abort(404)
    movie.featured = not movie.featured
    db.session.commit()
    flash("Homepage feature updated.", "success")
    return redirect(request.referrer or url_for("admin.movies"))


@admin_bp.post("/movies/<int:movie_id>/trailer/remove")
@admin_only
def movie_remove_trailer(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None:
        abort(404)
    old_files = [movie.trailer_path] if movie.trailer_path else []
    old_files.extend(subtitle.file_path for subtitle in movie.trailer_subtitles)
    for subtitle in list(movie.trailer_subtitles):
        db.session.delete(subtitle)
    movie.trailer_path = None
    db.session.commit()
    _delete_paths_after_commit(old_files)
    flash("Trailer and its subtitles removed.", "success")
    return redirect(url_for("admin.movie_edit", movie_id=movie.id))


@admin_bp.post("/movies/<int:movie_id>/frames/generate")
@admin_only
def movie_generate_frames(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None or not movie.video_path:
        abort(404)
    try:
        frames = generate_frames(movie.video_path, f"movie_{movie.id}")
        old_paths = _remove_frames(movie)
        add_frames_to_db(movie, frames)
        db.session.commit()
        _delete_paths_after_commit(old_paths)
        flash(f"Generated {len(frames)} new preview frames.", "success")
    except MediaError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.movie_edit", movie_id=movie.id))


@admin_bp.post("/movies/<int:movie_id>/frames/<int:asset_id>/select")
@admin_only
def movie_select_frame(movie_id: int, asset_id: int):
    movie = db.session.get(Movie, movie_id)
    asset = MediaAsset.query.filter_by(
        id=asset_id, movie_id=movie_id, kind="frame"
    ).first()
    if movie is None or asset is None:
        abort(404)
    try:
        new_poster = copy_to_thumbnails(asset.path, f"movie_{movie.id}")
    except MediaError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.movie_edit", movie_id=movie.id))
    old = movie.poster_path
    movie.poster_path = new_poster
    db.session.commit()
    if old:
        delete_relative(old)
    flash("Poster updated from the selected frame.", "success")
    return redirect(url_for("admin.movie_edit", movie_id=movie.id))


@admin_bp.post("/movies/<int:movie_id>/frames/<int:asset_id>/delete")
@admin_only
def movie_delete_frame(movie_id: int, asset_id: int):
    asset = MediaAsset.query.filter_by(
        id=asset_id, movie_id=movie_id, kind="frame"
    ).first()
    if asset is None:
        abort(404)
    path = asset.path
    db.session.delete(asset)
    db.session.commit()
    delete_relative(path)
    flash("Preview frame deleted.", "success")
    return redirect(url_for("admin.movie_edit", movie_id=movie_id))


# ---------------------------------------------------------------------------
# Subtitles
# ---------------------------------------------------------------------------
def _owner_for_subtitles(kind: str, content_id: int):
    if kind == "movie":
        return db.session.get(Movie, content_id), "movie_id"
    return db.session.get(Episode, content_id), "episode_id"


@admin_bp.post("/<kind>/<int:content_id>/subtitles/add")
@admin_only
def subtitle_add(kind: str, content_id: int):
    if kind not in {"movie", "episode"}:
        abort(404)
    owner, field = _owner_for_subtitles(kind, content_id)
    if owner is None:
        abort(404)
    form = SubtitleForm()
    redirect_endpoint = "admin.movie_edit" if kind == "movie" else "admin.episode_edit"
    redirect_kwargs = {"movie_id": content_id} if kind == "movie" else {"episode_id": content_id}
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))
    if not form.file.data or not form.file.data.filename:
        flash("Choose a .vtt subtitle file to upload.", "error")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))
    try:
        relative = save_subtitle(form.file.data)
    except MediaError as exc:
        flash(str(exc), "error")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))

    if form.is_default.data:
        Subtitle.query.filter_by(**{field: content_id}).update(
            {Subtitle.is_default: False}, synchronize_session=False
        )
    subtitle = Subtitle(
        language=form.language.data.strip().lower(),
        label=form.label.data.strip(),
        file_path=relative,
        is_default=bool(form.is_default.data),
        **{field: content_id},
    )
    db.session.add(subtitle)
    db.session.commit()
    flash(f"Subtitle “{subtitle.label}” added.", "success")
    return redirect(url_for(redirect_endpoint, **redirect_kwargs))


@admin_bp.post("/<kind>/<int:content_id>/subtitles/<int:subtitle_id>/delete")
@admin_only
def subtitle_delete(kind: str, content_id: int, subtitle_id: int):
    if kind not in {"movie", "episode"}:
        abort(404)
    field = "movie_id" if kind == "movie" else "episode_id"
    subtitle = Subtitle.query.filter_by(id=subtitle_id, **{field: content_id}).first()
    if subtitle is None:
        abort(404)
    path = subtitle.file_path
    db.session.delete(subtitle)
    db.session.commit()
    delete_relative(path)
    flash("Subtitle deleted.", "success")
    endpoint = "admin.movie_edit" if kind == "movie" else "admin.episode_edit"
    kwargs = {"movie_id": content_id} if kind == "movie" else {"episode_id": content_id}
    return redirect(url_for(endpoint, **kwargs))


@admin_bp.post("/<kind>/<int:content_id>/subtitles/<int:subtitle_id>/default")
@admin_only
def subtitle_make_default(kind: str, content_id: int, subtitle_id: int):
    if kind not in {"movie", "episode"}:
        abort(404)
    field = "movie_id" if kind == "movie" else "episode_id"
    subtitle = Subtitle.query.filter_by(id=subtitle_id, **{field: content_id}).first()
    if subtitle is None:
        abort(404)
    Subtitle.query.filter_by(**{field: content_id}).update(
        {Subtitle.is_default: False}, synchronize_session=False
    )
    subtitle.is_default = True
    db.session.commit()
    flash("Default subtitle updated.", "success")
    endpoint = "admin.movie_edit" if kind == "movie" else "admin.episode_edit"
    kwargs = {"movie_id": content_id} if kind == "movie" else {"episode_id": content_id}
    return redirect(url_for(endpoint, **kwargs))


# ---------------------------------------------------------------------------
# Trailer subtitles
# ---------------------------------------------------------------------------
def _owner_for_trailer_subtitles(kind: str, content_id: int):
    if kind == "movie":
        return (
            db.session.get(Movie, content_id),
            "movie_id",
            "admin.movie_edit",
            {"movie_id": content_id},
        )
    if kind == "show":
        return (
            db.session.get(TVShow, content_id),
            "show_id",
            "admin.show_edit",
            {"show_id": content_id},
        )
    abort(404)


@admin_bp.post("/<kind>/<int:content_id>/trailer-subtitles/add")
@admin_only
def trailer_subtitle_add(kind: str, content_id: int):
    owner, field, endpoint, kwargs = _owner_for_trailer_subtitles(kind, content_id)
    if owner is None:
        abort(404)
    form = SubtitleForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
        return redirect(url_for(endpoint, **kwargs))
    if not form.file.data or not form.file.data.filename:
        flash("Choose a .vtt subtitle file to upload.", "error")
        return redirect(url_for(endpoint, **kwargs))
    try:
        relative = save_subtitle(form.file.data)
    except MediaError as exc:
        flash(str(exc), "error")
        return redirect(url_for(endpoint, **kwargs))

    if form.is_default.data:
        TrailerSubtitle.query.filter_by(**{field: content_id}).update(
            {TrailerSubtitle.is_default: False}, synchronize_session=False
        )
    subtitle = TrailerSubtitle(
        language=form.language.data.strip().lower(),
        label=form.label.data.strip(),
        file_path=relative,
        is_default=bool(form.is_default.data),
        **{field: content_id},
    )
    db.session.add(subtitle)
    db.session.commit()
    flash(f"Trailer subtitle “{subtitle.label}” added.", "success")
    return redirect(url_for(endpoint, **kwargs))


@admin_bp.post("/<kind>/<int:content_id>/trailer-subtitles/<int:subtitle_id>/delete")
@admin_only
def trailer_subtitle_delete(kind: str, content_id: int, subtitle_id: int):
    _, field, endpoint, kwargs = _owner_for_trailer_subtitles(kind, content_id)
    subtitle = TrailerSubtitle.query.filter_by(
        id=subtitle_id, **{field: content_id}
    ).first()
    if subtitle is None:
        abort(404)
    path = subtitle.file_path
    db.session.delete(subtitle)
    db.session.commit()
    delete_relative(path)
    flash("Trailer subtitle deleted.", "success")
    return redirect(url_for(endpoint, **kwargs))


@admin_bp.post(
    "/<kind>/<int:content_id>/trailer-subtitles/<int:subtitle_id>/default"
)
@admin_only
def trailer_subtitle_make_default(kind: str, content_id: int, subtitle_id: int):
    _, field, endpoint, kwargs = _owner_for_trailer_subtitles(kind, content_id)
    subtitle = TrailerSubtitle.query.filter_by(
        id=subtitle_id, **{field: content_id}
    ).first()
    if subtitle is None:
        abort(404)
    TrailerSubtitle.query.filter_by(**{field: content_id}).update(
        {TrailerSubtitle.is_default: False}, synchronize_session=False
    )
    subtitle.is_default = True
    db.session.commit()
    flash("Default trailer subtitle updated.", "success")
    return redirect(url_for(endpoint, **kwargs))


# ---------------------------------------------------------------------------
# TV shows
# ---------------------------------------------------------------------------
@admin_bp.get("/shows")
@admin_only
def shows():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "all")
    query = TVShow.query
    if q:
        query = query.filter(TVShow.title.ilike(f"%{q}%"))
    if status == "published":
        query = query.filter_by(published=True)
    elif status == "draft":
        query = query.filter_by(published=False)
    items = query.order_by(TVShow.created_at.desc()).all()
    return render_template("admin/shows.html", shows=items, q=q, status=status)


@admin_bp.route("/shows/new", methods=["GET", "POST"])
@admin_only
def show_new():
    form = ShowForm()
    _set_genre_choices(form)
    if form.validate_on_submit():
        new_files: list[str] = []
        try:
            show = TVShow()
            _populate_show(show, form)
            if form.trailer.data:
                relative, _ = save_video(form.trailer.data, "trailers", "trailer")
                new_files.append(relative)
                show.trailer_path = relative
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "poster")
                new_files.append(relative)
                show.poster_path = relative
            show.published = bool(form.published.data)
            db.session.add(show)
            if show.published:
                problems = _show_publish_problems(show)
                if problems:
                    show.published = False
                    _flash_problems(problems, "Show")
            db.session.commit()
            flash(f"“{show.title}” was created. Add seasons and episodes next.", "success")
            return redirect(url_for("admin.show_edit", show_id=show.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")
    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template("admin/show_form.html", form=form, show=None)


@admin_bp.route("/shows/<int:show_id>/edit", methods=["GET", "POST"])
@admin_only
def show_edit(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    form = ShowForm(obj=show)
    _set_genre_choices(form)
    if request.method == "GET":
        form.genre_ids.data = [genre.id for genre in show.genres]

    if form.validate_on_submit():
        old_files: list[str] = []
        new_files: list[str] = []
        try:
            if form.trailer.data:
                relative, _ = save_video(form.trailer.data, "trailers", "trailer")
                new_files.append(relative)
                if show.trailer_path:
                    old_files.append(show.trailer_path)
                show.trailer_path = relative
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "poster")
                new_files.append(relative)
                if show.poster_path:
                    old_files.append(show.poster_path)
                show.poster_path = relative
            _populate_show(show, form)
            show.published = bool(form.published.data)
            if show.published:
                problems = _show_publish_problems(show)
                if problems:
                    show.published = False
                    _flash_problems(problems, "Show")
            db.session.commit()
            _delete_paths_after_commit(old_files)
            flash("Show updated.", "success")
            return redirect(url_for("admin.show_edit", show_id=show.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")

    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")

    season_form = SeasonForm()
    if request.method == "GET" and show.seasons:
        season_form.number.data = max(s.number for s in show.seasons) + 1

    bulk_form = BulkApplyForm()
    if request.method == "GET":
        bulk_form.description.data = show.description
        bulk_form.age_rating.data = show.age_rating
        bulk_form.published.data = show.published

    return render_template(
        "admin/show_edit.html",
        show=show,
        form=form,
        season_form=season_form,
        bulk_form=bulk_form,
        trailer_subtitle_form=SubtitleForm(),
        episodes=sorted(show.episodes, key=lambda e: (e.season_number, e.number)),
    )


@admin_bp.post("/shows/<int:show_id>/delete")
@admin_only
def show_delete(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    files = collect_owner_files(show=show)
    title = show.title
    db.session.delete(show)
    db.session.commit()
    _delete_paths_after_commit(list(files))
    flash(f"“{title}” and all of its episodes were deleted.", "success")
    return redirect(url_for("admin.shows"))


@admin_bp.post("/shows/<int:show_id>/toggle-publish")
@admin_only
def show_toggle_publish(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    if not show.published:
        problems = _show_publish_problems(show)
        if problems:
            _flash_problems(problems, "Show")
            return redirect(request.referrer or url_for("admin.shows"))
        show.published = True
        flash("Show published.", "success")
    else:
        show.published = False
        flash("Show unpublished and hidden from viewers.", "info")
    db.session.commit()
    return redirect(request.referrer or url_for("admin.shows"))


@admin_bp.post("/shows/<int:show_id>/toggle-feature")
@admin_only
def show_toggle_feature(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    show.featured = not show.featured
    db.session.commit()
    flash("Homepage feature updated.", "success")
    return redirect(request.referrer or url_for("admin.shows"))


@admin_bp.post("/shows/<int:show_id>/trailer/remove")
@admin_only
def show_remove_trailer(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    old_files = [show.trailer_path] if show.trailer_path else []
    old_files.extend(subtitle.file_path for subtitle in show.trailer_subtitles)
    for subtitle in list(show.trailer_subtitles):
        db.session.delete(subtitle)
    show.trailer_path = None
    db.session.commit()
    _delete_paths_after_commit(old_files)
    flash("Trailer and its subtitles removed.", "success")
    return redirect(url_for("admin.show_edit", show_id=show.id))


# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------
@admin_bp.post("/shows/<int:show_id>/seasons/new")
@admin_only
def season_new(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    form = SeasonForm()
    if form.validate_on_submit():
        if Season.query.filter_by(show_id=show.id, number=form.number.data).first():
            flash("A season with that number already exists.", "error")
        else:
            season = Season(
                show_id=show.id,
                number=form.number.data,
                title=(form.title.data or "").strip(),
                description=(form.description.data or "").strip(),
            )
            db.session.add(season)
            db.session.commit()
            flash(f"Season {season.number} added.", "success")
    else:
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return redirect(url_for("admin.show_edit", show_id=show.id))


@admin_bp.route("/shows/<int:show_id>/seasons/<int:season_id>/edit", methods=["GET", "POST"])
@admin_only
def season_edit(show_id: int, season_id: int):
    show = db.session.get(TVShow, show_id)
    season = Season.query.filter_by(id=season_id, show_id=show_id).first()
    if show is None or season is None:
        abort(404)
    form = SeasonForm(obj=season)
    if form.validate_on_submit():
        duplicate = Season.query.filter(
            Season.show_id == show.id, Season.number == form.number.data, Season.id != season.id
        ).first()
        if duplicate:
            flash("A season with that number already exists.", "error")
        else:
            season.number = form.number.data
            season.title = (form.title.data or "").strip()
            season.description = (form.description.data or "").strip()
            for episode in season.episodes:
                episode.season_number = season.number
            db.session.commit()
            flash("Season updated.", "success")
            return redirect(url_for("admin.show_edit", show_id=show.id))
    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template(
        "admin/season_edit.html",
        show=show,
        season=season,
        form=form,
        episodes=sorted(season.episodes, key=lambda e: e.number),
    )


@admin_bp.post("/shows/<int:show_id>/seasons/<int:season_id>/delete")
@admin_only
def season_delete(show_id: int, season_id: int):
    season = Season.query.filter_by(id=season_id, show_id=show_id).first()
    if season is None:
        abort(404)
    files = set()
    for episode in season.episodes:
        files.update(collect_owner_files(episode=episode))
    number = season.number
    db.session.delete(season)
    db.session.commit()
    _delete_paths_after_commit(list(files))
    flash(f"Season {number} deleted.", "success")
    return redirect(url_for("admin.show_edit", show_id=show_id))


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------
def _episode_form_for_show(form: EpisodeForm, show: TVShow, selected: int | None = None) -> None:
    form.season_id.choices = [
        (season.id, season.display_title if season.number == 0 else f"Season {season.number} — {season.display_title}")
        for season in show.seasons
    ]
    if selected and not form.season_id.data:
        form.season_id.data = selected


@admin_bp.route("/shows/<int:show_id>/episodes/new", methods=["GET", "POST"])
@admin_only
def episode_new(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    if not show.seasons:
        flash("Create a season before adding episodes.", "info")
        return redirect(url_for("admin.show_edit", show_id=show.id))
    selected = request.args.get("season_id", type=int)
    form = EpisodeForm()
    _episode_form_for_show(form, show, selected)
    if request.method == "GET":
        if not form.season_id.data:
            form.season_id.data = show.seasons[0].id
        season = db.session.get(Season, form.season_id.data)
        if season:
            existing = [ep.number for ep in season.episodes]
            form.number.data = (max(existing) + 1) if existing else 1

    if form.validate_on_submit():
        season = Season.query.filter_by(id=form.season_id.data, show_id=show.id).first()
        if season is None:
            flash("Choose a valid season for this show.", "error")
            return render_template(
                "admin/episode_form.html", form=form, show=show, episode=None
            )
        if Episode.query.filter_by(season_id=season.id, number=form.number.data).first():
            flash("An episode with that number already exists in this season.", "error")
            return render_template(
                "admin/episode_form.html", form=form, show=show, episode=None
            )
        new_files: list[str] = []
        try:
            episode = Episode(
                show_id=show.id,
                season_id=season.id,
                season_number=season.number,
                number=form.number.data,
                title=form.title.data.strip(),
                description=(form.description.data or "").strip(),
                published=bool(form.published.data),
            )
            if form.runtime.data:
                episode.runtime = int(form.runtime.data) * 60
            video_uploaded = False
            if form.video.data:
                relative, info = save_video(form.video.data, "episodes", "episode video")
                new_files.append(relative)
                episode.video_path = relative
                video_uploaded = True
                if not episode.runtime:
                    episode.runtime = max(1, int(round(info["duration"])))
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "episode poster")
                new_files.append(relative)
                episode.thumbnail_path = relative
            db.session.add(episode)
            db.session.flush()
            if video_uploaded:
                try:
                    frames = generate_frames(episode.video_path, f"episode_{episode.id}")
                    add_frames_to_db(episode, frames)
                except MediaError as exc:
                    flash(f"Episode saved, but preview frames failed: {exc}", "warning")
            if episode.published:
                problems = _episode_publish_problems(episode)
                if problems:
                    episode.published = False
                    _flash_problems(problems, "Episode")
            db.session.commit()
            flash(f"Episode {episode.display_code} was created.", "success")
            return redirect(url_for("admin.episode_edit", episode_id=episode.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")
        except Exception:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            raise
    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template("admin/episode_form.html", form=form, show=show, episode=None)


@admin_bp.route("/episodes/<int:episode_id>/edit", methods=["GET", "POST"])
@admin_only
def episode_edit(episode_id: int):
    episode = db.session.get(Episode, episode_id)
    if episode is None:
        abort(404)
    show = episode.show
    form = EpisodeForm(obj=episode)
    _episode_form_for_show(form, show)
    if request.method == "GET":
        form.season_id.data = episode.season_id
        form.runtime.data = _runtime_minutes(episode.runtime)

    if form.validate_on_submit():
        season = Season.query.filter_by(id=form.season_id.data, show_id=show.id).first()
        if season is None:
            flash("Choose a valid season.", "error")
            return redirect(url_for("admin.episode_edit", episode_id=episode.id))
        duplicate = Episode.query.filter(
            Episode.season_id == season.id,
            Episode.number == form.number.data,
            Episode.id != episode.id,
        ).first()
        if duplicate:
            flash("Another episode already uses that number in the selected season.", "error")
            return redirect(url_for("admin.episode_edit", episode_id=episode.id))

        old_files: list[str] = []
        new_files: list[str] = []
        try:
            if form.video.data:
                relative, info = save_video(form.video.data, "episodes", "episode video")
                new_files.append(relative)
                if episode.video_path:
                    old_files.append(episode.video_path)
                old_files.extend(_remove_frames(episode))
                episode.video_path = relative
                if not form.runtime.data:
                    episode.runtime = max(1, int(round(info["duration"])))
            if form.thumbnail.data:
                relative = save_image(form.thumbnail.data, "thumbnails", "episode poster")
                new_files.append(relative)
                if episode.thumbnail_path:
                    old_files.append(episode.thumbnail_path)
                episode.thumbnail_path = relative

            episode.title = form.title.data.strip()
            episode.description = (form.description.data or "").strip()
            episode.number = form.number.data
            episode.season_id = season.id
            episode.season_number = season.number
            if form.runtime.data:
                episode.runtime = int(form.runtime.data) * 60
            episode.published = bool(form.published.data)
            if episode.published:
                problems = _episode_publish_problems(episode)
                if problems:
                    episode.published = False
                    _flash_problems(problems, "Episode")

            if form.video.data:
                try:
                    frames = generate_frames(episode.video_path, f"episode_{episode.id}")
                    add_frames_to_db(episode, frames)
                except MediaError as exc:
                    flash(f"Video replaced, but preview frames failed: {exc}", "warning")

            db.session.commit()
            _delete_paths_after_commit(old_files)
            flash("Episode updated.", "success")
            return redirect(url_for("admin.episode_edit", episode_id=episode.id))
        except MediaError as exc:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            for path in new_files:
                delete_relative(path)
            flash("That episode number conflicts with another episode in the season.", "error")

    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")

    subtitle_form = SubtitleForm()
    return render_template(
        "admin/episode_form.html",
        form=form,
        show=show,
        episode=episode,
        subtitle_form=subtitle_form,
        frames=[asset for asset in episode.media_assets if asset.kind == "frame"],
    )


@admin_bp.post("/episodes/<int:episode_id>/delete")
@admin_only
def episode_delete(episode_id: int):
    episode = db.session.get(Episode, episode_id)
    if episode is None:
        abort(404)
    show_id = episode.show_id
    files = collect_owner_files(episode=episode)
    label = episode.display_code
    db.session.delete(episode)
    db.session.commit()
    _delete_paths_after_commit(list(files))
    flash(f"Episode {label} deleted.", "success")
    return redirect(url_for("admin.show_edit", show_id=show_id))


@admin_bp.post("/episodes/<int:episode_id>/toggle-publish")
@admin_only
def episode_toggle_publish(episode_id: int):
    episode = db.session.get(Episode, episode_id)
    if episode is None:
        abort(404)
    if not episode.published:
        problems = _episode_publish_problems(episode)
        if problems:
            _flash_problems(problems, "Episode")
        else:
            episode.published = True
            flash("Episode published.", "success")
    else:
        episode.published = False
        flash("Episode unpublished.", "info")
    db.session.commit()
    return redirect(request.referrer or url_for("admin.show_edit", show_id=episode.show_id))


@admin_bp.post("/episodes/<int:episode_id>/frames/generate")
@admin_only
def episode_generate_frames(episode_id: int):
    episode = db.session.get(Episode, episode_id)
    if episode is None or not episode.video_path:
        abort(404)
    try:
        frames = generate_frames(episode.video_path, f"episode_{episode.id}")
        old_paths = _remove_frames(episode)
        add_frames_to_db(episode, frames)
        db.session.commit()
        _delete_paths_after_commit(old_paths)
        flash(f"Generated {len(frames)} new preview frames.", "success")
    except MediaError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.episode_edit", episode_id=episode.id))


@admin_bp.post("/episodes/<int:episode_id>/frames/<int:asset_id>/select")
@admin_only
def episode_select_frame(episode_id: int, asset_id: int):
    episode = db.session.get(Episode, episode_id)
    asset = MediaAsset.query.filter_by(
        id=asset_id, episode_id=episode_id, kind="frame"
    ).first()
    if episode is None or asset is None:
        abort(404)
    try:
        new_poster = copy_to_thumbnails(asset.path, f"episode_{episode.id}")
    except MediaError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.episode_edit", episode_id=episode.id))
    old = episode.thumbnail_path
    episode.thumbnail_path = new_poster
    db.session.commit()
    if old:
        delete_relative(old)
    flash("Episode thumbnail updated from the selected frame.", "success")
    return redirect(url_for("admin.episode_edit", episode_id=episode.id))


@admin_bp.post("/episodes/<int:episode_id>/frames/<int:asset_id>/delete")
@admin_only
def episode_delete_frame(episode_id: int, asset_id: int):
    asset = MediaAsset.query.filter_by(
        id=asset_id, episode_id=episode_id, kind="frame"
    ).first()
    if asset is None:
        abort(404)
    path = asset.path
    db.session.delete(asset)
    db.session.commit()
    delete_relative(path)
    flash("Preview frame deleted.", "success")
    return redirect(url_for("admin.episode_edit", episode_id=episode_id))


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------
@admin_bp.route("/shows/<int:show_id>/bulk-upload", methods=["GET", "POST"])
@admin_only
def bulk_upload(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    if not show.seasons:
        flash("Create a season before uploading episodes.", "info")
        return redirect(url_for("admin.show_edit", show_id=show.id))
    form = BulkUploadForm()
    form.season_id.choices = [
        (season.id, f"Season {season.number} — {season.display_title}")
        for season in show.seasons
    ]
    if request.method == "GET":
        form.season_id.data = show.seasons[0].id
        existing = [ep.number for ep in show.seasons[0].episodes]
        form.number_start.data = (max(existing) + 1) if existing else 1

    if form.validate_on_submit():
        season = Season.query.filter_by(id=form.season_id.data, show_id=show.id).first()
        if season is None:
            flash("Choose a valid season.", "error")
            return redirect(url_for("admin.bulk_upload", show_id=show.id))
        files = [file for file in (form.videos.data or []) if file and file.filename]
        if not files:
            flash("Select at least one video file to upload.", "error")
            return render_template("admin/bulk_upload.html", show=show, form=form)
        if len(files) > 20:
            flash("Upload at most 20 episodes at a time.", "error")
            return render_template("admin/bulk_upload.html", show=show, form=form)

        existing = {ep.number for ep in season.episodes}
        next_number = form.number_start.data
        created = 0
        failures: list[str] = []
        for upload in files:
            while next_number in existing:
                next_number += 1
            try:
                relative, info = save_video(upload, "episodes", "episode video")
            except MediaError as exc:
                failures.append(f"{upload.filename}: {exc}")
                continue
            title = re.sub(r"[_\-\.]+", " ", Path(upload.filename).stem)
            title = re.sub(r"\s+", " ", title).strip().title() or f"Episode {next_number}"
            episode = Episode(
                show_id=show.id,
                season_id=season.id,
                season_number=season.number,
                number=next_number,
                title=title,
                description="",
                runtime=max(1, int(round(info["duration"]))),
                video_path=relative,
                published=bool(form.publish.data),
            )
            db.session.add(episode)
            db.session.flush()
            try:
                frames = generate_frames(episode.video_path, f"episode_{episode.id}")
                add_frames_to_db(episode, frames)
            except MediaError as exc:
                failures.append(f"{upload.filename}: {exc}")
            db.session.commit()
            existing.add(next_number)
            next_number += 1
            created += 1
        if created:
            flash(f"{created} episode(s) uploaded to {season.display_title}.", "success")
        for failure in failures:
            flash(failure, "warning")
        return redirect(url_for("admin.show_edit", show_id=show.id))

    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template("admin/bulk_upload.html", show=show, form=form)


@admin_bp.post("/shows/<int:show_id>/bulk-apply")
@admin_only
def bulk_apply(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None:
        abort(404)
    form = BulkApplyForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
        return redirect(url_for("admin.show_edit", show_id=show.id))

    target = form.target.data
    if target == "show":
        show.description = (form.description.data or "").strip()
        show.age_rating = form.age_rating.data
        show.published = bool(form.published.data)
        db.session.commit()
        flash("Show metadata updated.", "success")
        return redirect(url_for("admin.show_edit", show_id=show.id))

    if target == "all":
        episodes = list(show.episodes)
    else:
        try:
            ids = {
                int(value.strip())
                for value in (form.episode_ids.data or "").split(",")
                if value.strip()
            }
        except ValueError:
            ids = set()
        episodes = [ep for ep in show.episodes if ep.id in ids]
        if not episodes:
            flash("Select at least one episode to update.", "error")
            return redirect(url_for("admin.show_edit", show_id=show.id))

    changed = 0
    skipped_publish = 0
    for episode in episodes:
        if form.copy_description.data:
            episode.description = (form.description.data or "").strip()
        if form.copy_age_rating.data:
            episode.show.age_rating = form.age_rating.data
        if form.copy_published.data:
            if form.published.data and not episode.video_path:
                episode.published = False
                skipped_publish += 1
            else:
                episode.published = bool(form.published.data)
        changed += 1
    db.session.commit()
    flash(f"Updated {changed} episode(s).", "success")
    if skipped_publish:
        flash(
            f"{skipped_publish} episode(s) stayed unpublished because they have no video file.",
            "warning",
        )
    return redirect(url_for("admin.show_edit", show_id=show.id))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@admin_bp.get("/users")
@admin_only
def users():
    q = (request.args.get("q") or "").strip()
    query = User.query
    if q:
        query = query.filter(
            db.or_(User.username.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"))
        )
    return render_template("admin/users.html", users=query.order_by(User.created_at.desc()).all(), q=q)


@admin_bp.post("/users/<int:user_id>/toggle-active")
@admin_only
def user_toggle_active(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin.users"))
    user.is_active = not user.is_active
    db.session.commit()
    flash(
        f"{user.username} was {'reactivated' if user.is_active else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/delete")
@admin_only
def user_delete(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("admin.users"))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"User {username} deleted.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Media library / uploads
# ---------------------------------------------------------------------------
def referenced_media_paths() -> set[str]:
    paths: set[str] = set()
    for movie in Movie.query.all():
        paths.update(collect_owner_files(movie=movie))
    for show in TVShow.query.all():
        paths.update(collect_owner_files(show=show))
    for subtitle in Subtitle.query.all():
        paths.add(subtitle.file_path)
    for asset in MediaAsset.query.all():
        paths.add(asset.path)
    return paths


def orphan_files() -> list[dict]:
    root = upload_root()
    referenced = referenced_media_paths()
    orphans = []
    if not root.exists():
        return orphans
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.name in {".DS_Store", ".gitkeep"}:
            continue
        if relative.startswith("temp/") or relative not in referenced:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            orphans.append({"path": relative, "size": size})
    return sorted(orphans, key=lambda row: row["path"])


@admin_bp.get("/media")
@admin_only
def media():
    usage = storage_usage()
    assets = (
        MediaAsset.query.order_by(MediaAsset.created_at.desc()).limit(60).all()
    )
    orphans = orphan_files()
    counts = {
        "movies": Movie.query.count(),
        "episodes": Episode.query.count(),
        "subtitles": Subtitle.query.count(),
        "frames": MediaAsset.query.filter_by(kind="frame").count(),
    }
    return render_template(
        "admin/media.html",
        usage=usage,
        assets=assets,
        orphans=orphans,
        counts=counts,
        ffmpeg=ffmpeg_available(),
    )


@admin_bp.post("/media/orphans/cleanup")
@admin_only
def cleanup_orphans():
    orphans = orphan_files()
    for row in orphans:
        delete_relative(row["path"])
    flash(f"Removed {len(orphans)} orphaned file(s).", "success")
    return redirect(url_for("admin.media"))


@admin_bp.post("/media/orphans/delete")
@admin_only
def delete_orphan():
    path = (request.form.get("path") or "").strip()
    allowed = {row["path"] for row in orphan_files()}
    if path not in allowed:
        flash("That file is not an orphaned upload.", "error")
        return redirect(url_for("admin.media"))
    delete_relative(path)
    flash("Orphaned file deleted.", "success")
    return redirect(url_for("admin.media"))


@admin_bp.post("/media/assets/<int:asset_id>/delete")
@admin_only
def delete_asset(asset_id: int):
    asset = db.session.get(MediaAsset, asset_id)
    if asset is None:
        abort(404)
    path = asset.path
    db.session.delete(asset)
    db.session.commit()
    delete_relative(path)
    flash("Media asset deleted.", "success")
    return redirect(request.referrer or url_for("admin.media"))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_only
def settings():
    form = SettingsForm()
    if request.method == "GET":
        form.site_name.data = get_setting("site_name")
        form.tagline.data = get_setting("tagline")
        form.footer_note.data = get_setting("footer_note")
        form.allow_registration.data = get_setting("allow_registration") == "true"
    if form.validate_on_submit():
        Setting.set("site_name", form.site_name.data.strip())
        Setting.set("tagline", (form.tagline.data or "").strip())
        Setting.set("footer_note", (form.footer_note.data or "").strip())
        Setting.set("allow_registration", "true" if form.allow_registration.data else "false")
        db.session.commit()
        flash("Platform settings saved.", "success")
        return redirect(url_for("admin.settings"))
    elif request.method == "POST":
        for errors in form.errors.values():
            for error in errors:
                flash(error, "error")
    return render_template("admin/settings.html", form=form)
