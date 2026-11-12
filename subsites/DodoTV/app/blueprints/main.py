"""Public catalog, detail and playback pages."""
from __future__ import annotations

import math

from dodo.web import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from dodo.web import current_user, require_login, shared_admin
from sqlalchemy import or_

from ..extensions import db
from ..models import (
    Episode,
    Genre,
    Movie,
    Rating,
    TVShow,
    WatchHistory,
    WatchProgress,
    utcnow,
)
from ..services import (
    episode_card,
    get_genres,
    hero_item,
    movie_card,
    movie_watch_url,
    popular_movies,
    popular_shows,
    poster_for,
    progress_card,
    rating_summary,
    show_card,
    show_watch_url,
)
from ..utils.media import format_duration, format_runtime, media_url
from .media import can_view_episode, can_view_movie, can_view_show

main_bp = Blueprint("main", __name__)


def _is_admin() -> bool:
    return request.admin_mount and shared_admin()


def _progress_map(user_id: int, episode_ids: list[int]) -> dict[int, int]:
    if not user_id or not episode_ids:
        return {}
    rows = WatchProgress.query.filter(
        WatchProgress.user_id == user_id, WatchProgress.episode_id.in_(episode_ids)
    ).all()
    return {row.episode_id: row.percent for row in rows}


@main_bp.route("/")
def index():
    featured_movies = (
        Movie.query.filter_by(published=True, featured=True)
        .order_by(Movie.updated_at.desc())
        .limit(6)
        .all()
    )
    featured_shows = (
        TVShow.query.filter_by(published=True, featured=True)
        .order_by(TVShow.updated_at.desc())
        .limit(6)
        .all()
    )
    hero_candidates = sorted(
        featured_movies + featured_shows,
        key=lambda item: item.updated_at or item.created_at,
        reverse=True,
    )
    if not hero_candidates:
        latest_movies = (
            Movie.query.filter_by(published=True).order_by(Movie.created_at.desc()).limit(4).all()
        )
        latest_shows = (
            TVShow.query.filter_by(published=True)
            .order_by(TVShow.created_at.desc())
            .limit(4)
            .all()
        )
        hero_candidates = sorted(
            latest_movies + latest_shows,
            key=lambda item: item.created_at,
            reverse=True,
        )
    heroes = [hero_item(item) for item in hero_candidates[:5]]

    continue_cards = []
    if current_user.is_authenticated:
        recent = (
            WatchProgress.query.filter_by(user_id=current_user.id)
            .order_by(WatchProgress.updated_at.desc())
            .limit(30)
            .all()
        )
        for progress in recent:
            if progress.completed or not progress.position:
                continue
            if progress.duration and progress.position / progress.duration >= 0.96:
                continue
            if progress.movie and not progress.movie.published:
                continue
            if progress.episode and not (
                progress.episode.published
                and progress.episode.show
                and progress.episode.show.published
            ):
                continue
            card = progress_card(progress)
            if card:
                continue_cards.append(card)
            if len(continue_cards) >= 12:
                break

    recent_movies = (
        Movie.query.filter_by(published=True).order_by(Movie.created_at.desc()).limit(12).all()
    )
    recent_shows = (
        TVShow.query.filter_by(published=True).order_by(TVShow.created_at.desc()).limit(12).all()
    )
    recent_entries = sorted(
        [(item.created_at, movie_card(item)) for item in recent_movies]
        + [(item.created_at, show_card(item)) for item in recent_shows],
        key=lambda row: row[0],
        reverse=True,
    )
    recent_cards = [card for _, card in recent_entries][:14]

    genres = get_genres()
    counts = {
        "movies": Movie.query.filter_by(published=True).count(),
        "shows": TVShow.query.filter_by(published=True).count(),
    }

    return render_template(
        "index.html",
        heroes=heroes,
        continue_cards=continue_cards,
        featured_movies=[movie_card(m) for m in featured_movies],
        featured_shows=[show_card(s) for s in featured_shows],
        recent_cards=recent_cards,
        popular_movie_cards=[movie_card(m) for m in popular_movies()],
        popular_show_cards=[show_card(s) for s in popular_shows()],
        genres=genres,
        counts=counts,
    )


@main_bp.route("/browse")
def browse():
    q = (request.args.get("q") or "").strip()[:100]
    content_type = request.args.get("type", "all")
    if content_type not in {"all", "movie", "show"}:
        content_type = "all"
    genre_slug = (request.args.get("genre") or "").strip()
    sort = request.args.get("sort", "recent")
    if sort not in {"recent", "oldest", "title", "year"}:
        sort = "recent"
    year = request.args.get("year", type=int)

    genre = Genre.query.filter_by(slug=genre_slug).first() if genre_slug else None
    if genre_slug and not genre:
        flash("That genre could not be found.", "info")

    entries = []
    if content_type in {"all", "movie"}:
        query = Movie.query.filter(Movie.published.is_(True))
        if q:
            query = query.filter(Movie.title.ilike(f"%{q}%"))
        if genre:
            query = query.filter(Movie.genres.any(Genre.id == genre.id))
        if year:
            query = query.filter(Movie.release_year == year)
        entries.extend(query.order_by(Movie.id))

    if content_type in {"all", "show"}:
        query = TVShow.query.filter(TVShow.published.is_(True))
        if q:
            query = query.filter(
                or_(
                    TVShow.title.ilike(f"%{q}%"),
                    TVShow.description.ilike(f"%{q}%"),
                )
            )
        if genre:
            query = query.filter(TVShow.genres.any(Genre.id == genre.id))
        if year:
            query = query.filter(TVShow.release_year == year)
        entries.extend(query.order_by(TVShow.id))

    sort_keys = {
        "recent": lambda item: item.created_at,
        "oldest": lambda item: item.created_at,
        "title": lambda item: item.title.lower(),
        "year": lambda item: item.release_year or 0,
    }
    entries.sort(key=sort_keys[sort], reverse=sort in {"recent", "year"})

    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, current_app.config["ITEMS_PER_PAGE"])
    total = len(entries)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    start = (page - 1) * per_page
    cards = [
        movie_card(item) if isinstance(item, Movie) else show_card(item)
        for item in entries[start : start + per_page]
    ]

    years = sorted(
        {
            year_value
            for (year_value,) in db.session.query(Movie.release_year)
            .filter(Movie.published.is_(True), Movie.release_year.isnot(None))
            .distinct()
            .all()
        }
        | {
            year_value
            for (year_value,) in db.session.query(TVShow.release_year)
            .filter(TVShow.published.is_(True), TVShow.release_year.isnot(None))
            .distinct()
            .all()
        },
        reverse=True,
    )

    def page_url(target_page: int) -> str:
        args = request.args.to_dict(flat=True)
        args["page"] = target_page
        return url_for("main.browse", **args)

    return render_template(
        "browse.html",
        cards=cards,
        total=total,
        page=page,
        pages=pages,
        prev_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < pages else None,
        genres=get_genres(),
        years=years,
        q=q,
        content_type=content_type,
        genre=genre,
        year=year,
        sort=sort,
    )


@main_bp.get("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return redirect(url_for("main.browse"))
    return redirect(url_for("main.browse", q=query))


def _trailer_subtitle_data(owner: Movie | TVShow) -> list[dict]:
    return [
        {
            "url": url_for("media.serve_trailer_subtitle", subtitle_id=subtitle.id),
            "label": subtitle.label,
            "language": subtitle.language,
            "default": bool(subtitle.is_default),
        }
        for subtitle in sorted(
            owner.trailer_subtitles, key=lambda item: (not item.is_default, item.label)
        )
    ]


def _frame_caption(label: str, timestamp: int | float | None) -> str:
    duration = format_duration(timestamp) if timestamp else ""
    return " · ".join(part for part in (label, duration) if part)


@main_bp.get("/movie/<int:movie_id>")
def movie_detail(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None or not can_view_movie(movie):
        abort(404)
    progress = None
    if current_user.is_authenticated:
        progress = WatchProgress.query.filter_by(
            user_id=current_user.id, movie_id=movie.id
        ).first()

    gallery = [
        {
            "src": media_url(asset.path),
            "label": "Still",
            "caption": _frame_caption(movie.title, asset.timestamp),
        }
        for asset in movie.media_assets
        if asset.kind == "frame" and asset.path
    ]

    genre_ids = [genre.id for genre in movie.genres]
    related = (
        Movie.query.filter(Movie.published.is_(True), Movie.id != movie.id)
        .filter(Movie.genres.any(Genre.id.in_(genre_ids)))
        .order_by(Movie.created_at.desc())
        .limit(10)
        .all()
        if genre_ids
        else Movie.query.filter(Movie.published.is_(True), Movie.id != movie.id)
        .order_by(Movie.created_at.desc())
        .limit(10)
        .all()
    )
    if not related:
        related = popular_movies(10)

    return render_template(
        "movie_detail.html",
        movie=movie,
        progress=progress,
        related=[movie_card(item) for item in related if item.id != movie.id][:10],
        watch_url=movie_watch_url(movie),
        trailer_url=url_for("media.stream_trailer", kind="movie", content_id=movie.id)
        if movie.trailer_path
        else None,
        trailer_subtitles=_trailer_subtitle_data(movie),
        poster=media_url(poster_for(movie)),
        gallery=gallery,
        rating=rating_summary(
            movie_id=movie.id,
            user_id=current_user.id if current_user.is_authenticated else None,
        ),
    )


@main_bp.get("/show/<int:show_id>")
def show_detail(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None or not can_view_show(show):
        abort(404)

    visible_episodes = [ep for ep in show.episodes if ep.published or _is_admin()]
    visible_seasons = []
    for season in show.seasons:
        episodes = [ep for ep in season.episodes if ep.published or _is_admin()]
        if episodes:
            visible_seasons.append((season, episodes))
    episode_ids = [episode.id for episode in visible_episodes]
    progress = (
        _progress_map(current_user.id, episode_ids)
        if current_user.is_authenticated
        else {}
    )

    gallery = []
    for episode in _sort_episodes(visible_episodes):
        for asset in episode.media_assets:
            if asset.kind != "frame" or not asset.path:
                continue
            gallery.append(
                {
                    "src": media_url(asset.path),
                    "label": episode.display_code,
                    "caption": _frame_caption(
                        f"{episode.display_code} · {episode.title}", asset.timestamp
                    ),
                }
            )

    total_runtime = sum(int(episode.runtime or 0) for episode in visible_episodes)

    genre_ids = [genre.id for genre in show.genres]
    related = (
        TVShow.query.filter(TVShow.published.is_(True), TVShow.id != show.id)
        .filter(TVShow.genres.any(Genre.id.in_(genre_ids)))
        .order_by(TVShow.created_at.desc())
        .limit(10)
        .all()
        if genre_ids
        else TVShow.query.filter(TVShow.published.is_(True), TVShow.id != show.id)
        .order_by(TVShow.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "show_detail.html",
        show=show,
        seasons=visible_seasons,
        progress=progress,
        episode_count=len(visible_episodes),
        total_runtime_display=format_runtime(total_runtime),
        related=[show_card(item) for item in related][:10],
        watch_url=show_watch_url(show),
        trailer_url=url_for("media.stream_trailer", kind="show", content_id=show.id)
        if show.trailer_path
        else None,
        trailer_subtitles=_trailer_subtitle_data(show),
        poster=media_url(poster_for(show)),
        gallery=gallery,
        rating=rating_summary(
            show_id=show.id,
            user_id=current_user.id if current_user.is_authenticated else None,
        ),
    )


def _save_rating(field: str, content_id: int, dest: str):
    score = request.form.get("score", type=int)
    if score not in range(1, 6):
        flash("Choose a rating between 1 and 5 stars.", "error")
        return redirect(f"{dest}#rate")
    rating = Rating.query.filter_by(user_id=current_user.id, **{field: content_id}).first()
    if rating is None:
        rating = Rating(user_id=current_user.id, score=score, **{field: content_id})
        db.session.add(rating)
    else:
        rating.score = score
    db.session.commit()
    flash(f"Your {score}-star rating was saved.", "success")
    return redirect(f"{dest}#rate")


@main_bp.post("/movie/<int:movie_id>/rate")
@require_login
def rate_movie(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None or not can_view_movie(movie):
        abort(404)
    return _save_rating(
        "movie_id", movie.id, url_for("main.movie_detail", movie_id=movie.id)
    )


@main_bp.post("/show/<int:show_id>/rate")
@require_login
def rate_show(show_id: int):
    show = db.session.get(TVShow, show_id)
    if show is None or not can_view_show(show):
        abort(404)
    return _save_rating("show_id", show.id, url_for("main.show_detail", show_id=show.id))


def _record_history(movie: Movie | None = None, episode: Episode | None = None) -> None:
    if not current_user.is_authenticated:
        return
    query = WatchHistory.query.filter_by(user_id=current_user.id)
    if movie is not None:
        entry = query.filter_by(movie_id=movie.id).first()
    else:
        entry = query.filter_by(episode_id=episode.id).first()
    if entry is None:
        entry = WatchHistory(user_id=current_user.id, movie_id=movie.id if movie else None, episode_id=episode.id if episode else None)
        db.session.add(entry)
    else:
        entry.watched_at = utcnow()
    db.session.commit()


def _sort_episodes(episodes):
    return sorted(episodes, key=lambda e: (e.season_number, e.number))


@main_bp.get("/watch/movie/<int:movie_id>")
def watch_movie(movie_id: int):
    movie = db.session.get(Movie, movie_id)
    if movie is None or not can_view_movie(movie):
        abort(404)
    if not movie.video_path:
        flash("This movie does not have a video file yet.", "info")
        return redirect(url_for("main.movie_detail", movie_id=movie.id))

    progress = None
    if current_user.is_authenticated:
        progress = WatchProgress.query.filter_by(
            user_id=current_user.id, movie_id=movie.id
        ).first()
    _record_history(movie=movie)

    config = {
        "type": "movie",
        "id": movie.id,
        "title": movie.title,
        "videoUrl": url_for("media.stream_movie", content_id=movie.id),
        "poster": media_url(movie.poster_path),
        "progress": progress.position_seconds if progress else 0,
        "duration": progress.duration_seconds if progress else (movie.runtime or 0),
        "nextUrl": None,
        "nextTitle": None,
        "autoNext": False,
        "saveUrl": url_for("api.save_progress"),
        "backUrl": url_for("main.movie_detail", movie_id=movie.id),
        "subtitles": [
            {
                "id": sub.id,
                "label": sub.label,
                "language": sub.language,
                "url": url_for("media.serve_subtitle", subtitle_id=sub.id),
                "default": sub.is_default,
            }
            for sub in sorted(movie.subtitles, key=lambda s: (not s.is_default, s.label))
        ],
    }

    return render_template(
        "watch.html",
        kind="movie",
        movie=movie,
        episode=None,
        show=None,
        season=None,
        title=movie.title,
        description=movie.description,
        meta=" · ".join(
            part
            for part in [
                str(movie.release_year) if movie.release_year else "",
                movie.runtime_display,
                movie.age_rating,
            ]
            if part
        ),
        back_url=url_for("main.movie_detail", movie_id=movie.id),
        trailer_url=url_for("media.stream_trailer", kind="movie", content_id=movie.id)
        if movie.trailer_path
        else None,
        poster=media_url(poster_for(movie)),
        config=config,
        progress=progress,
        related=[movie_card(m) for m in popular_movies(8) if m.id != movie.id][:8],
    )


@main_bp.get("/watch/episode/<int:episode_id>")
def watch_episode(episode_id: int):
    episode = db.session.get(Episode, episode_id)
    if episode is None or not can_view_episode(episode):
        abort(404)
    if not episode.video_path:
        flash("This episode does not have a video file yet.", "info")
        return redirect(url_for("main.show_detail", show_id=episode.show_id))

    show = episode.show
    ordered = _sort_episodes([ep for ep in show.episodes if ep.published or _is_admin()])
    index = next((i for i, item in enumerate(ordered) if item.id == episode.id), None)
    previous_episode = ordered[index - 1] if index is not None and index > 0 else None
    next_episode = (
        ordered[index + 1] if index is not None and index < len(ordered) - 1 else None
    )

    progress = None
    if current_user.is_authenticated:
        progress = WatchProgress.query.filter_by(
            user_id=current_user.id, episode_id=episode.id
        ).first()
    _record_history(episode=episode)

    progress_map = (
        _progress_map(current_user.id, [ep.id for ep in ordered])
        if current_user.is_authenticated
        else {}
    )

    config = {
        "type": "episode",
        "id": episode.id,
        "title": episode.title,
        "videoUrl": url_for("media.stream_episode", content_id=episode.id),
        "poster": media_url(episode.thumbnail_path or poster_for(show)),
        "progress": progress.position_seconds if progress else 0,
        "duration": progress.duration_seconds if progress else (episode.runtime or 0),
        "nextUrl": url_for("main.watch_episode", episode_id=next_episode.id)
        if next_episode
        else None,
        "nextTitle": next_episode.title if next_episode else None,
        "nextCode": next_episode.display_code if next_episode else None,
        "previousUrl": url_for("main.watch_episode", episode_id=previous_episode.id)
        if previous_episode
        else None,
        "autoNext": True,
        "saveUrl": url_for("api.save_progress"),
        "backUrl": url_for("main.show_detail", show_id=show.id),
        "subtitles": [
            {
                "id": sub.id,
                "label": sub.label,
                "language": sub.language,
                "url": url_for("media.serve_subtitle", subtitle_id=sub.id),
                "default": sub.is_default,
            }
            for sub in sorted(
                episode.subtitles, key=lambda s: (not s.is_default, s.label)
            )
        ],
    }

    return render_template(
        "watch.html",
        kind="episode",
        movie=None,
        episode=episode,
        show=show,
        season=episode.season,
        title=episode.title,
        description=episode.description or show.description,
        meta=" · ".join(
            part
            for part in [
                show.title,
                episode.display_code,
                episode.runtime_display,
                show.age_rating,
            ]
            if part
        ),
        back_url=url_for("main.show_detail", show_id=show.id),
        trailer_url=url_for("media.stream_trailer", kind="show", content_id=show.id)
        if show.trailer_path
        else None,
        poster=media_url(episode.thumbnail_path or poster_for(show)),
        config=config,
        progress=progress,
        episodes=ordered,
        progress_map=progress_map,
        previous_episode=previous_episode,
        next_episode=next_episode,
        related=[],
    )
