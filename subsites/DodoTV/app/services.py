"""Catalog presentation helpers shared by public and admin blueprints."""
from __future__ import annotations

from datetime import datetime, timezone

from dodo.web import url_for
from sqlalchemy import func

from .extensions import db
from .models import Episode, Genre, Movie, Rating, Setting, TVShow, WatchProgress
from .utils.media import format_duration, media_url


DEFAULT_SETTINGS = {
    "site_name": "Dodo TV",
    "tagline": "Stories worth staying in for.",
    "footer_note": "Dodo TV — original streaming, built with Flask.",
    "allow_registration": "true",
}


def get_setting(key: str) -> str:
    try:
        return Setting.get(key, DEFAULT_SETTINGS.get(key, ""))
    except Exception:  # pragma: no cover - first-run before tables exist
        return DEFAULT_SETTINGS.get(key, "")


def allow_registration() -> bool:
    return get_setting("allow_registration").lower() == "true"


def site_settings() -> dict[str, str]:
    return {key: get_setting(key) for key in DEFAULT_SETTINGS}


def rating_summary(
    *, movie_id: int | None = None, show_id: int | None = None, user_id: int | None = None
) -> dict:
    """Average, count and optional personal score for one catalog title."""
    column = Rating.movie_id if movie_id is not None else Rating.show_id
    content_id = movie_id if movie_id is not None else show_id
    average, count = (
        db.session.query(func.avg(Rating.score), func.count(Rating.id))
        .filter(column == content_id)
        .one()
    )
    user_score = None
    if user_id:
        user_score = (
            db.session.query(Rating.score)
            .filter(column == content_id, Rating.user_id == user_id)
            .scalar()
        )
    return {
        "average": round(float(average), 1) if average is not None else None,
        "count": int(count or 0),
        "user_score": user_score,
    }


def poster_for(show: TVShow | Movie) -> str | None:
    if isinstance(show, Movie):
        return show.poster_path
    if show.poster_path:
        return show.poster_path
    for episode in show.episodes:
        if episode.thumbnail_path:
            return episode.thumbnail_path
    return None


def movie_watch_url(movie: Movie) -> str:
    if movie.video_path:
        return url_for("main.watch_movie", movie_id=movie.id)
    return url_for("main.movie_detail", movie_id=movie.id)


def show_watch_url(show: TVShow) -> str:
    episode = show.first_episode()
    if episode:
        return url_for("main.watch_episode", episode_id=episode.id)
    return url_for("main.show_detail", show_id=show.id)


def genres_text(genres) -> str:
    return ", ".join(g.name for g in genres)


def movie_card(movie: Movie, progress: WatchProgress | None = None) -> dict:
    percent = progress.percent if progress else 0
    return {
        "kind": "movie",
        "id": movie.id,
        "title": movie.title,
        "image": media_url(poster_for(movie)),
        "href": url_for("main.movie_detail", movie_id=movie.id),
        "watch_href": movie_watch_url(movie),
        "badge": "MOVIE",
        "meta": " · ".join(
            part
            for part in [
                str(movie.release_year) if movie.release_year else "",
                movie.runtime_display,
                movie.age_rating,
            ]
            if part
        ),
        "progress": percent,
        "resume": f"Resume · {percent}%" if percent else "",
        "description": movie.description,
        "is_new": movie.created_at,
    }


def episode_card(episode: Episode, progress: WatchProgress | None = None) -> dict:
    percent = progress.percent if progress else 0
    image = episode.thumbnail_path or (episode.show.poster_path if episode.show else None)
    return {
        "kind": "episode",
        "id": episode.id,
        "title": episode.title,
        "image": media_url(image),
        "href": url_for("main.watch_episode", episode_id=episode.id),
        "watch_href": url_for("main.watch_episode", episode_id=episode.id),
        "badge": episode.display_code if episode.show is None else f"{episode.show.title}",
        "meta": " · ".join(
            part
            for part in [
                episode.show.title if episode.show else "",
                episode.display_code,
                episode.runtime_display,
            ]
            if part
        ),
        "progress": percent,
        "resume": f"Resume · {percent}%" if percent else "",
        "description": episode.description,
        "is_new": episode.created_at,
    }


def show_card(show: TVShow, progress: WatchProgress | None = None) -> dict:
    percent = progress.percent if progress else 0
    subtitle = f"{show.season_count} season{'s' if show.season_count != 1 else ''}"
    return {
        "kind": "show",
        "id": show.id,
        "title": show.title,
        "image": media_url(poster_for(show)),
        "href": url_for("main.show_detail", show_id=show.id),
        "watch_href": show_watch_url(show),
        "badge": "SERIES",
        "meta": " · ".join(
            part
            for part in [
                str(show.release_year) if show.release_year else "",
                subtitle,
                show.age_rating,
            ]
            if part
        ),
        "progress": percent,
        "resume": f"Resume · {percent}%" if percent else "",
        "description": show.description,
        "is_new": show.created_at,
    }


def progress_card(progress: WatchProgress) -> dict | None:
    if progress.movie:
        card = movie_card(progress.movie, progress)
        card["resume"] = f"Resume · {format_duration(progress.position_seconds)} left off"
        return card
    if progress.episode and progress.episode.show:
        card = episode_card(progress.episode, progress)
        card["badge"] = progress.episode.show.title
        card["resume"] = (
            f"{progress.episode.display_code} · {format_duration(progress.position_seconds)}"
        )
        return card
    return None


def hero_item(item: Movie | TVShow) -> dict:
    if isinstance(item, Movie):
        return {
            "kind": "movie",
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "image": media_url(poster_for(item)),
            "detail_url": url_for("main.movie_detail", movie_id=item.id),
            "watch_url": movie_watch_url(item),
            "trailer_url": url_for("media.stream_trailer", kind="movie", content_id=item.id)
            if item.trailer_path
            else None,
            "badge": "Featured movie",
            "meta": " · ".join(
                part
                for part in [
                    str(item.release_year) if item.release_year else "",
                    item.runtime_display,
                    genres_text(item.genres),
                    item.age_rating,
                ]
                if part
            ),
        }
    return {
        "kind": "show",
        "id": item.id,
        "title": item.title,
        "description": item.description,
        "image": media_url(poster_for(item)),
        "detail_url": url_for("main.show_detail", show_id=item.id),
        "watch_url": show_watch_url(item),
        "trailer_url": url_for("media.stream_trailer", kind="show", content_id=item.id)
        if item.trailer_path
        else None,
        "badge": "Featured series",
        "meta": " · ".join(
            part
            for part in [
                str(item.release_year) if item.release_year else "",
                f"{item.season_count} season{'s' if item.season_count != 1 else ''}",
                genres_text(item.genres),
                item.age_rating,
            ]
            if part
        ),
    }


def get_genres() -> list[Genre]:
    return Genre.query.order_by(Genre.name).all()


def time_ago(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def popular_movies(limit: int = 12) -> list[Movie]:
    counts = (
        db.session.query(
            WatchProgress.movie_id,
            db.func.count(WatchProgress.id).label("watch_count"),
        )
        .filter(WatchProgress.movie_id.isnot(None))
        .group_by(WatchProgress.movie_id)
        .subquery()
    )
    query = (
        db.session.query(Movie)
        .outerjoin(counts, counts.c.movie_id == Movie.id)
        .filter(Movie.published.is_(True))
        .order_by(db.func.coalesce(counts.c.watch_count, 0).desc(), Movie.created_at.desc())
    )
    return query.limit(limit).all()


def popular_shows(limit: int = 12) -> list[TVShow]:
    counts = (
        db.session.query(Episode.show_id, db.func.count(WatchProgress.id).label("watch_count"))
        .join(WatchProgress, WatchProgress.episode_id == Episode.id)
        .group_by(Episode.show_id)
        .subquery()
    )
    query = (
        db.session.query(TVShow)
        .outerjoin(counts, counts.c.show_id == TVShow.id)
        .filter(TVShow.published.is_(True))
        .order_by(db.func.coalesce(counts.c.watch_count, 0).desc(), TVShow.created_at.desc())
    )
    return query.limit(limit).all()
