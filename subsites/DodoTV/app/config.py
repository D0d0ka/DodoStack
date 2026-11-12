"""Application configuration loaded from environment variables."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # Security -------------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = int(os.getenv("SESSION_LIFETIME_DAYS", "14")) * 86400
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "4096")) * 1024 * 1024

    # Database -------------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'instance' / 'dodotv.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Storage --------------------------------------------------------------
    UPLOAD_FOLDER = Path(os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))).resolve()
    FRAME_COUNT = int(os.getenv("FRAME_COUNT", "8"))
    MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "20"))
    MAX_SUBTITLE_MB = int(os.getenv("MAX_SUBTITLE_MB", "5"))
    AUTO_INIT_DB = _bool("AUTO_INIT_DB", True)

    # FFmpeg ---------------------------------------------------------------
    FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "ffmpeg")
    FFPROBE_BINARY = os.getenv("FFPROBE_BINARY", "ffprobe")
    FFMPEG_TIMEOUT = int(os.getenv("FFMPEG_TIMEOUT", "180"))

    # Presentation ---------------------------------------------------------
    SITE_NAME = os.getenv("SITE_NAME", "Dodo TV")
    ITEMS_PER_PAGE = int(os.getenv("ITEMS_PER_PAGE", "24"))
    PREFERRED_URL_SCHEME = "https" if SESSION_COOKIE_SECURE else "http"


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    AUTO_INIT_DB = False
    SECRET_KEY = "test-secret-key"
