"""HTTP Range aware file streaming for video playback."""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from dodo.web import Response, request

from .media import resolve_path

CHUNK_SIZE = 64 * 1024
RANGE_RE = re.compile(r"^bytes=([0-9]*)-([0-9]*)$")


def _mimetype(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("video/"):
        return guessed
    return {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".ogv": "video/ogg",
    }.get(path.suffix.lower(), "application/octet-stream")


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def send_video(relative_path: str) -> Response:
    """Stream a video with byte-range support. Never loads the whole file."""
    path = resolve_path(relative_path)
    if path is None or not path.is_file():
        response = Response("Video file not found.", status=404, mimetype="text/plain")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    file_size = path.stat().st_size
    if file_size == 0:
        return Response("Video file is empty.", status=404, mimetype="text/plain")

    range_header = request.headers.get("Range", "").strip()
    content_type = _mimetype(path)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{path.name}"',
    }

    if not range_header:
        headers["Content-Length"] = str(file_size)
        return Response(
            _iter_file(path, 0, file_size),
            status=200,
            headers=headers,
        )

    match = RANGE_RE.match(range_header)
    if not match:
        return _range_not_satisfiable(file_size)

    start_raw, end_raw = match.groups()
    if start_raw == "" and end_raw == "":
        return _range_not_satisfiable(file_size)
    try:
        first = int(start_raw) if start_raw else None
        last = int(end_raw) if end_raw else None
    except ValueError:
        return _range_not_satisfiable(file_size)

    if first is None:
        # suffix range: last N bytes
        if not last:
            return _range_not_satisfiable(file_size)
        start = max(0, file_size - last)
        end = file_size - 1
    else:
        start = first
        end = last if last is not None else file_size - 1
        if start >= file_size:
            return _range_not_satisfiable(file_size)
        end = min(end, file_size - 1)

    if end < start:
        return _range_not_satisfiable(file_size)

    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    headers["Content-Length"] = str(length)
    response = Response(
        _iter_file(path, start, length),
        status=206,
        headers=headers,
    )
    return response


def _range_not_satisfiable(file_size: int) -> Response:
    response = Response("Requested range not satisfiable.", status=416, mimetype="text/plain")
    response.headers["Content-Range"] = f"bytes */{file_size}"
    response.headers["Accept-Ranges"] = "bytes"
    return response
