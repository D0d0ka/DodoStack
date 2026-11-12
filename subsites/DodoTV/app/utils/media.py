"""Media upload, validation, probing and thumbnail generation helpers."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Iterable

from dodo.web import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from ..extensions import db
from ..models import Episode, MediaAsset, Movie

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".ogv", ".avi"}
SUBTITLE_EXTENSIONS = {".vtt"}
UPLOAD_CATEGORIES = (
    "movies",
    "shows",
    "episodes",
    "trailers",
    "thumbnails",
    "generated_frames",
    "subtitles",
    "temp",
)
FRAME_PERCENTAGES = (0.08, 0.20, 0.32, 0.44, 0.56, 0.68, 0.80, 0.90)


class MediaError(Exception):
    """Raised for user-facing media problems."""


# ---------------------------------------------------------------------------
# Paths and formatting
# ---------------------------------------------------------------------------
def upload_root() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"]).resolve()


def ensure_upload_dirs() -> None:
    root = upload_root()
    root.mkdir(parents=True, exist_ok=True)
    for category in UPLOAD_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)


def resolve_path(relative_path: str | None) -> Path | None:
    """Resolve a database path safely inside the upload directory."""
    if not relative_path:
        return None
    root = upload_root()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        log.warning("Blocked unsafe media path: %s", relative_path)
        return None
    return candidate


def media_url(relative_path: str | None) -> str | None:
    """Turn ``thumbnails/abc.jpg`` into the public media route URL."""
    if not relative_path:
        return None
    from dodo.web import url_for

    clean = str(relative_path).lstrip("/")
    return url_for("media.serve_media", filename=clean)


def unique_name(extension: str) -> str:
    return f"{uuid.uuid4().hex}{extension.lower()}"


def human_size(num_bytes: int | float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "0:00"
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_runtime(seconds: int | float | None) -> str:
    """Human-friendly runtime such as ``2h 05m`` or ``18m``."""
    if not seconds:
        return ""
    total = max(1, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes = round(total / 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------
def ffmpeg_available() -> bool:
    return _binary_available(current_app.config.get("FFMPEG_BINARY", "ffmpeg"))


def ffprobe_available() -> bool:
    return _binary_available(current_app.config.get("FFPROBE_BINARY", "ffprobe"))


def _binary_available(binary: str) -> bool:
    try:
        result = subprocess.run(
            [binary, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe_video(path: Path) -> dict:
    """Inspect a video with FFprobe. Raises MediaError with useful context."""
    if not ffprobe_available():
        raise MediaError(
            "FFprobe is not available. Install FFmpeg and make sure ffprobe is on "
            "your PATH, or configure FFPROBE_BINARY."
        )
    try:
        result = subprocess.run(
            [
                current_app.config.get("FFPROBE_BINARY", "ffprobe"),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=current_app.config.get("FFMPEG_TIMEOUT", 180),
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Video analysis timed out. The file may be corrupted.") from exc
    except OSError as exc:
        raise MediaError(f"Could not run FFprobe: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        message = detail[-1] if detail else "unknown error"
        raise MediaError(f"FFprobe could not read this video: {message}")

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError("FFprobe returned unreadable metadata for this video.") from exc

    streams = data.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise MediaError("The uploaded file does not contain a video stream.")
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for raw in (data.get("format", {}).get("duration"), video_stream.get("duration")):
        try:
            duration = float(raw)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    if duration <= 0:
        raise MediaError("The video has no readable duration and cannot be processed.")

    return {
        "duration": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "codec": video_stream.get("codec_name") or "",
        "audio_codec": (audio_stream or {}).get("codec_name") or "",
        "size": int(data.get("format", {}).get("size") or path.stat().st_size),
        "format": data.get("format", {}).get("format_name") or "",
    }


def generate_frames(video_relative_path: str, prefix: str, count: int | None = None) -> list[dict]:
    """Extract preview frames at several timestamps.

    Returns a list of ``{"path": ..., "timestamp": ...}`` dictionaries. Raises
    MediaError when FFmpeg is missing or no frame could be produced.
    """
    video = resolve_path(video_relative_path)
    if video is None or not video.exists():
        raise MediaError("The video file is missing on disk. Upload it again.")
    if not ffmpeg_available():
        raise MediaError(
            "FFmpeg is not available, so preview frames cannot be generated. "
            "Install FFmpeg and restart Dodo TV."
        )

    info = probe_video(video)
    duration = info["duration"]
    wanted = max(1, min(12, count or current_app.config.get("FRAME_COUNT", 8)))
    percentages = FRAME_PERCENTAGES[:wanted]
    if wanted > len(FRAME_PERCENTAGES):
        step = 1 / (wanted + 1)
        percentages = tuple(step * (i + 1) for i in range(wanted))

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)[:40] or "video"
    folder_rel = f"generated_frames/{safe_prefix}_{uuid.uuid4().hex[:12]}"
    folder = resolve_path(folder_rel)
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)

    produced: list[dict] = []
    for index, pct in enumerate(percentages, start=1):
        timestamp = max(0.15, min(duration - 0.25, duration * pct))
        output = folder / f"frame_{index:02d}.jpg"
        command = [
            current_app.config.get("FFMPEG_BINARY", "ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(960,iw)':-2",
            "-q:v",
            "3",
            "-y",
            str(output),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=current_app.config.get("FFMPEG_TIMEOUT", 180),
            )
        except subprocess.TimeoutExpired:
            log.warning("Frame extraction timed out at %.2fs", timestamp)
            continue
        except OSError as exc:
            raise MediaError(f"Could not run FFmpeg: {exc}") from exc

        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            produced.append(
                {
                    "path": f"{folder_rel}/{output.name}",
                    "timestamp": round(timestamp, 2),
                    "position": index,
                }
            )
        else:
            log.warning(
                "Frame extraction failed at %.2fs: %s", timestamp, (result.stderr or "").strip()
            )

    if not produced:
        shutil.rmtree(folder, ignore_errors=True)
        raise MediaError(
            "No preview frames could be extracted from this video. Check that the "
            "file is a valid video and that FFmpeg has permission to write to the "
            "upload folder."
        )
    return produced


def probe_remote_duration(relative_path: str | None) -> int | None:
    path = resolve_path(relative_path)
    if not path or not path.exists():
        return None
    try:
        return int(round(probe_video(path)["duration"]))
    except MediaError:
        return None


# ---------------------------------------------------------------------------
# Validation and saving
# ---------------------------------------------------------------------------
def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def _check_size(file: FileStorage, max_mb: int, label: str) -> None:
    stream = file.stream
    pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(pos)
    if size == 0:
        raise MediaError(f"The uploaded {label} is empty.")
    if size > max_mb * 1024 * 1024:
        raise MediaError(f"The uploaded {label} is larger than {max_mb} MB.")


def _save_to_category(file: FileStorage, category: str, extension: str) -> tuple[Path, str]:
    ensure_upload_dirs()
    filename = unique_name(extension)
    relative = f"{category}/{filename}"
    absolute = resolve_path(relative)
    assert absolute is not None
    file.save(absolute)
    return absolute, relative


def save_video(file: FileStorage, category: str, label: str = "video") -> tuple[str, dict]:
    """Save and validate a video. Returns (relative_path, probe_info)."""
    if not file or not file.filename:
        raise MediaError(f"No {label} file was provided.")
    extension = _extension(file.filename)
    if extension not in VIDEO_EXTENSIONS:
        raise MediaError(
            f"Unsupported {label} format '{extension or 'unknown'}'. "
            f"Allowed formats: {', '.join(sorted(VIDEO_EXTENSIONS))}."
        )
    _check_size(file, current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024), label)
    absolute, relative = _save_to_category(file, category, extension)
    try:
        info = probe_video(absolute)
    except MediaError:
        absolute.unlink(missing_ok=True)
        raise
    return relative, info


def save_image(file: FileStorage, category: str = "thumbnails", label: str = "image") -> str:
    if not file or not file.filename:
        raise MediaError(f"No {label} file was provided.")
    extension = _extension(file.filename)
    if extension not in IMAGE_EXTENSIONS:
        raise MediaError(
            f"Unsupported {label} format. Use JPG, PNG or WebP."
        )
    _check_size(file, current_app.config["MAX_IMAGE_MB"], label)
    absolute, relative = _save_to_category(file, category, extension)
    try:
        with Image.open(absolute) as image:
            image.verify()
        with Image.open(absolute) as image:
            width, height = image.size
            if width < 100 or height < 100:
                raise MediaError("The image is too small. Use an image at least 100×100 pixels.")
            if width * height > 40_000_000:
                raise MediaError("The image dimensions are too large.")
    except (UnidentifiedImageError, OSError) as exc:
        absolute.unlink(missing_ok=True)
        raise MediaError("The image file is not a valid JPG, PNG or WebP image.") from exc
    except MediaError:
        absolute.unlink(missing_ok=True)
        raise
    return relative


def save_subtitle(file: FileStorage, label: str = "subtitle") -> str:
    if not file or not file.filename:
        raise MediaError(f"No {label} file was provided.")
    if _extension(file.filename) != ".vtt":
        raise MediaError("Subtitles must be uploaded as .vtt (WebVTT) files.")
    _check_size(file, current_app.config["MAX_SUBTITLE_MB"], "subtitle file")
    raw = file.read()
    file.stream.seek(0)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MediaError("Subtitle files must be UTF-8 encoded WebVTT files.") from exc
    if not text.lstrip().startswith("WEBVTT"):
        raise MediaError("This file is not a valid WebVTT file: it must start with 'WEBVTT'.")
    if "-->" not in text:
        raise MediaError("This WebVTT file has no subtitle cues (no '-->' timestamps found).")
    ensure_upload_dirs()
    relative = f"subtitles/{unique_name('.vtt')}"
    absolute = resolve_path(relative)
    assert absolute is not None
    absolute.write_text(text, encoding="utf-8")
    return relative


def copy_to_thumbnails(relative_path: str, prefix: str = "poster") -> str:
    source = resolve_path(relative_path)
    if source is None or not source.exists():
        raise MediaError("The selected frame is no longer available.")
    ensure_upload_dirs()
    extension = source.suffix.lower() if source.suffix.lower() in IMAGE_EXTENSIONS else ".jpg"
    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", prefix)[:32] or "poster"
    relative = f"thumbnails/{safe_prefix}_{unique_name(extension)}"
    destination = resolve_path(relative)
    assert destination is not None
    shutil.copy2(source, destination)
    return relative


def delete_relative(relative_path: str | None) -> None:
    """Delete one uploaded file, refusing paths outside the upload root."""
    if not relative_path:
        return
    path = resolve_path(relative_path)
    if path is None or not path.exists() or path.is_dir():
        return
    try:
        path.unlink()
    except OSError as exc:  # pragma: no cover - filesystem edge case
        log.warning("Could not delete %s: %s", path, exc)


def delete_frame_folder(relative_path: str | None) -> None:
    if not relative_path:
        return
    path = resolve_path(relative_path)
    if path is None:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


def add_frames_to_db(asset_owner, frames: Iterable[dict], kind: str = "frame") -> int:
    """Persist generated frames for a movie or episode."""
    if not isinstance(asset_owner, (Movie, Episode)):
        raise TypeError("Frames can only be attached to a movie or an episode.")
    is_movie = isinstance(asset_owner, Movie)
    count = 0
    for frame in frames:
        db.session.add(
            MediaAsset(
                movie_id=asset_owner.id if is_movie else None,
                episode_id=None if is_movie else asset_owner.id,
                kind=kind,
                path=frame["path"],
                position=frame.get("position", count),
                timestamp=frame.get("timestamp"),
            )
        )
        count += 1
    return count


def purge_frames(owner) -> None:
    """Remove generated frames and their files for a movie/episode."""
    for asset in list(owner.media_assets):
        if asset.kind == "frame":
            delete_relative(asset.path)
            db.session.delete(asset)


def collect_owner_files(movie=None, show=None, episode=None) -> set[str]:
    """Collect every uploaded file belonging to a piece of content."""
    files: set[str] = set()
    if movie:
        for value in (movie.video_path, movie.trailer_path, movie.poster_path):
            if value:
                files.add(value)
        files.update(s.file_path for s in movie.subtitles)
        files.update(s.file_path for s in movie.trailer_subtitles)
        files.update(a.path for a in movie.media_assets)
    if episode:
        for value in (episode.video_path, episode.thumbnail_path):
            if value:
                files.add(value)
        files.update(s.file_path for s in episode.subtitles)
        files.update(a.path for a in episode.media_assets)
    if show:
        for value in (show.poster_path, show.trailer_path):
            if value:
                files.add(value)
        files.update(s.file_path for s in show.trailer_subtitles)
        for ep in show.episodes:
            files.update(collect_owner_files(episode=ep))
    return files


def storage_usage() -> dict:
    root = upload_root()
    categories = {}
    total = 0
    for category in UPLOAD_CATEGORIES:
        folder = root / category
        size = 0
        if folder.exists():
            for path in folder.rglob("*"):
                if path.is_file():
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
        categories[category] = size
        total += size
    return {"total": total, "categories": categories}
