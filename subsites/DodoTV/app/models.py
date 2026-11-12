"""SQLAlchemy models for Dodo TV.

Media binaries are never stored in the database. Only relative paths below the
upload folder are persisted.
"""
from datetime import datetime, timezone
from math import isfinite

from dodo.web import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


movie_genres = db.Table(
    "movie_genres",
    db.Column(
        "movie_id",
        db.Integer,
        db.ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "genre_id",
        db.Integer,
        db.ForeignKey("genres.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

show_genres = db.Table(
    "show_genres",
    db.Column(
        "show_id",
        db.Integer,
        db.ForeignKey("tv_shows.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    db.Column(
        "genre_id",
        db.Integer,
        db.ForeignKey("genres.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(48), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    last_login_at = db.Column(db.DateTime(timezone=True))

    watch_progress = db.relationship(
        "WatchProgress", back_populates="user", cascade="all, delete-orphan"
    )
    watch_history = db.relationship(
        "WatchHistory", back_populates="user", cascade="all, delete-orphan"
    )
    ratings = db.relationship(
        "Rating", back_populates="user", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def initial(self) -> str:
        return (self.username or "?")[:1].upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<User {self.username}>"


class Genre(db.Model):
    __tablename__ = "genres"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)

    movies = db.relationship("Movie", secondary=movie_genres, back_populates="genres")
    shows = db.relationship("TVShow", secondary=show_genres, back_populates="genres")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Genre {self.name}>"


class Movie(db.Model):
    __tablename__ = "movies"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    creator = db.Column(db.String(255), default="", nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    release_year = db.Column(db.Integer, nullable=True, index=True)
    age_rating = db.Column(db.String(16), default="PG-13", nullable=False)
    runtime = db.Column(db.Integer, nullable=True)  # seconds
    video_path = db.Column(db.String(512), nullable=True)
    trailer_path = db.Column(db.String(512), nullable=True)
    poster_path = db.Column(db.String(512), nullable=True)
    featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    published = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    genres = db.relationship(
        "Genre", secondary=movie_genres, back_populates="movies", lazy="selectin"
    )
    subtitles = db.relationship(
        "Subtitle", back_populates="movie", cascade="all, delete-orphan"
    )
    media_assets = db.relationship(
        "MediaAsset",
        back_populates="movie",
        cascade="all, delete-orphan",
        order_by="MediaAsset.position",
    )
    watch_progress = db.relationship(
        "WatchProgress", back_populates="movie", cascade="all, delete-orphan"
    )
    watch_history = db.relationship(
        "WatchHistory", back_populates="movie", cascade="all, delete-orphan"
    )
    ratings = db.relationship(
        "Rating", back_populates="movie", cascade="all, delete-orphan"
    )
    trailer_subtitles = db.relationship(
        "TrailerSubtitle",
        back_populates="movie",
        cascade="all, delete-orphan",
        order_by="TrailerSubtitle.label",
    )

    @property
    def runtime_display(self) -> str:
        if not self.runtime:
            return ""
        seconds = max(1, int(self.runtime))
        if seconds < 60:
            return f"{seconds}s"
        minutes = round(seconds / 60)
        hours, mins = divmod(minutes, 60)
        return f"{hours}h {mins:02d}m" if hours else f"{mins}m"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Movie {self.title}>"


class TVShow(db.Model):
    __tablename__ = "tv_shows"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    creator = db.Column(db.String(255), default="", nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    release_year = db.Column(db.Integer, nullable=True, index=True)
    age_rating = db.Column(db.String(16), default="PG-13", nullable=False)
    poster_path = db.Column(db.String(512), nullable=True)
    trailer_path = db.Column(db.String(512), nullable=True)
    featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    published = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    genres = db.relationship(
        "Genre", secondary=show_genres, back_populates="shows", lazy="selectin"
    )
    seasons = db.relationship(
        "Season",
        back_populates="show",
        cascade="all, delete-orphan",
        order_by="Season.number",
    )
    episodes = db.relationship(
        "Episode",
        back_populates="show",
        cascade="all, delete-orphan",
        order_by="Episode.season_number, Episode.number",
    )
    ratings = db.relationship(
        "Rating", back_populates="show", cascade="all, delete-orphan"
    )
    trailer_subtitles = db.relationship(
        "TrailerSubtitle",
        back_populates="show",
        cascade="all, delete-orphan",
        order_by="TrailerSubtitle.label",
    )

    @property
    def episode_count(self) -> int:
        return len(self.episodes)

    @property
    def season_count(self) -> int:
        return len(self.seasons)

    @property
    def total_runtime(self) -> int:
        """Combined runtime of every episode, in seconds."""
        return sum(int(episode.runtime or 0) for episode in self.episodes)

    @property
    def total_runtime_display(self) -> str:
        from .utils.media import format_runtime

        return format_runtime(self.total_runtime)

    def first_episode(self):
        return (
            Episode.query.filter_by(show_id=self.id, published=True)
            .order_by(Episode.season_number, Episode.number)
            .first()
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TVShow {self.title}>"


class Season(db.Model):
    __tablename__ = "seasons"
    __table_args__ = (db.UniqueConstraint("show_id", "number", name="uq_season_show_number"),)

    id = db.Column(db.Integer, primary_key=True)
    show_id = db.Column(
        db.Integer, db.ForeignKey("tv_shows.id", ondelete="CASCADE"), nullable=False
    )
    number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(255), default="", nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    show = db.relationship("TVShow", back_populates="seasons")
    episodes = db.relationship(
        "Episode",
        back_populates="season",
        cascade="all, delete-orphan",
        order_by="Episode.number",
    )

    @property
    def display_title(self) -> str:
        if self.number == 0:
            return self.title or "Specials"
        return self.title or f"Season {self.number}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Season {self.number}>"


class Episode(db.Model):
    __tablename__ = "episodes"
    __table_args__ = (
        db.UniqueConstraint("season_id", "number", name="uq_episode_season_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    show_id = db.Column(
        db.Integer, db.ForeignKey("tv_shows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_id = db.Column(
        db.Integer, db.ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    season_number = db.Column(db.Integer, nullable=False, default=1)
    number = db.Column(db.Integer, nullable=False, default=1)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    runtime = db.Column(db.Integer, nullable=True)
    video_path = db.Column(db.String(512), nullable=True)
    thumbnail_path = db.Column(db.String(512), nullable=True)
    published = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    show = db.relationship("TVShow", back_populates="episodes")
    season = db.relationship("Season", back_populates="episodes")
    subtitles = db.relationship(
        "Subtitle", back_populates="episode", cascade="all, delete-orphan"
    )
    media_assets = db.relationship(
        "MediaAsset",
        back_populates="episode",
        cascade="all, delete-orphan",
        order_by="MediaAsset.position",
    )
    watch_progress = db.relationship(
        "WatchProgress", back_populates="episode", cascade="all, delete-orphan"
    )
    watch_history = db.relationship(
        "WatchHistory", back_populates="episode", cascade="all, delete-orphan"
    )

    @property
    def display_code(self) -> str:
        if self.season_number == 0:
            return f"SP{self.number:02d}"
        return f"S{self.season_number:02d}E{self.number:02d}"

    @property
    def runtime_display(self) -> str:
        if not self.runtime:
            return ""
        seconds = max(1, int(self.runtime))
        if seconds < 60:
            return f"{seconds}s"
        return f"{round(seconds / 60)}m"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Episode {self.display_code} {self.title}>"


class Subtitle(db.Model):
    __tablename__ = "subtitles"

    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    episode_id = db.Column(
        db.Integer, db.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    language = db.Column(db.String(16), nullable=False, default="en")
    label = db.Column(db.String(96), nullable=False, default="English")
    file_path = db.Column(db.String(512), nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    movie = db.relationship("Movie", back_populates="subtitles")
    episode = db.relationship("Episode", back_populates="subtitles")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Subtitle {self.label}>"


class TrailerSubtitle(db.Model):
    """WebVTT tracks that belong to a movie or series trailer."""

    __tablename__ = "trailer_subtitles"

    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    show_id = db.Column(
        db.Integer, db.ForeignKey("tv_shows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    language = db.Column(db.String(16), nullable=False, default="en")
    label = db.Column(db.String(96), nullable=False, default="English")
    file_path = db.Column(db.String(512), nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    movie = db.relationship("Movie", back_populates="trailer_subtitles")
    show = db.relationship("TVShow", back_populates="trailer_subtitles")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TrailerSubtitle {self.label}>"


class Rating(db.Model):
    """A single 1–5 star score from one user for one movie or series."""

    __tablename__ = "ratings"
    __table_args__ = (
        db.UniqueConstraint("user_id", "movie_id", name="uq_rating_user_movie"),
        db.UniqueConstraint("user_id", "show_id", name="uq_rating_user_show"),
        db.CheckConstraint("score >= 1 AND score <= 5", name="ck_rating_score_range"),
        db.CheckConstraint(
            "(movie_id IS NOT NULL AND show_id IS NULL) OR "
            "(movie_id IS NULL AND show_id IS NOT NULL)",
            name="ck_rating_single_target",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    show_id = db.Column(
        db.Integer, db.ForeignKey("tv_shows.id", ondelete="CASCADE"), nullable=True, index=True
    )
    score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user = db.relationship("User", back_populates="ratings")
    movie = db.relationship("Movie", back_populates="ratings")
    show = db.relationship("TVShow", back_populates="ratings")

    def __repr__(self) -> str:  # pragma: no cover
        target = f"movie:{self.movie_id}" if self.movie_id else f"show:{self.show_id}"
        return f"<Rating {target} {self.score}/5>"


class MediaAsset(db.Model):
    __tablename__ = "media_assets"

    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    episode_id = db.Column(
        db.Integer, db.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind = db.Column(db.String(32), nullable=False, default="frame", index=True)
    path = db.Column(db.String(512), nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)
    timestamp = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    movie = db.relationship("Movie", back_populates="media_assets")
    episode = db.relationship("Episode", back_populates="media_assets")


class WatchProgress(db.Model):
    __tablename__ = "watch_progress"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    episode_id = db.Column(
        db.Integer, db.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    position = db.Column(db.Float, default=0, nullable=False)
    duration = db.Column(db.Float, default=0, nullable=False)
    completed = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user = db.relationship("User", back_populates="watch_progress")
    movie = db.relationship("Movie", back_populates="watch_progress")
    episode = db.relationship("Episode", back_populates="watch_progress")

    @property
    def position_seconds(self) -> float:
        return max(0.0, self.position) if self.position is not None and isfinite(self.position) else 0.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.duration) if self.duration is not None and isfinite(self.duration) else 0.0

    @property
    def percent(self) -> int:
        duration = self.duration_seconds
        if not duration:
            return 0
        return round(min(self.position_seconds / duration, 1.0) * 100)


class WatchHistory(db.Model):
    __tablename__ = "watch_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    movie_id = db.Column(
        db.Integer, db.ForeignKey("movies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    episode_id = db.Column(
        db.Integer, db.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    watched_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True
    )

    user = db.relationship("User", back_populates="watch_history")
    movie = db.relationship("Movie", back_populates="watch_history")
    episode = db.relationship("Episode", back_populates="watch_history")


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, default="", nullable=False)

    @staticmethod
    def get(key: str, default: str = "") -> str:
        row = db.session.get(Setting, key)
        return row.value if row else default

    @staticmethod
    def set(key: str, value: str) -> None:
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = value
