"""Generate a complete demo catalog with real, playable media.

``python server.py seed-tv-demo`` uses this module to create short but genuine MP4 files with
FFmpeg, real posters, generated preview frames and WebVTT subtitle tracks. It is
intentionally self-contained so the demo can be recreated from a clean database.
"""
from __future__ import annotations

import math
import os
import random
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dodo.web import current_app
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .extensions import db
from .models import (
    Episode,
    Genre,
    Movie,
    Season,
    Setting,
    Subtitle,
    TVShow,
    User,
    WatchHistory,
    WatchProgress,
)
from .utils.media import (
    MediaError,
    add_frames_to_db,
    ensure_upload_dirs,
    ffmpeg_available,
    ffprobe_available,
    generate_frames,
    probe_video,
    resolve_path,
)

# ---------------------------------------------------------------------------
# Demo catalog
# ---------------------------------------------------------------------------
MOVIES = [
    {
        "slug": "neon-horizon",
        "title": "Neon Horizon",
        "year": 2026,
        "rating": "PG-13",
        "genres": ["Science Fiction", "Thriller"],
        "featured": True,
        "trailer": True,
        "languages": ["en", "es"],
        "palette": ("#07112b", "#14b8a6"),
        "description": (
            "A courier carrying a stolen memory chip races across a rain-soaked "
            "megacity before the corporation that built her can reboot the past."
        ),
        "scenes": [
            "The city never sleeps. It only changes the channel.",
            "You are not supposed to remember this.",
            "Every signal leaves a shadow.",
            "Run faster than the truth.",
            "Some memories are worth burning for.",
        ],
    },
    {
        "slug": "the-last-lighthouse",
        "title": "The Last Lighthouse",
        "year": 2023,
        "rating": "PG-13",
        "genres": ["Drama", "Mystery"],
        "featured": True,
        "trailer": True,
        "languages": ["en"],
        "palette": ("#0b1622", "#d4a94e"),
        "description": (
            "A reclusive lighthouse keeper begins receiving letters from a ship "
            "that vanished thirty years ago, each one dated tomorrow."
        ),
        "scenes": [
            "The sea keeps everything it is given.",
            "Who writes the letters at the edge of the world?",
            "Every light needs someone to tend it.",
            "The fog is lifting. Not everything else is.",
            "Come home before the tide turns.",
        ],
    },
    {
        "slug": "midnight-circuit",
        "title": "Midnight Circuit",
        "year": 2025,
        "rating": "R",
        "genres": ["Action", "Thriller"],
        "featured": False,
        "trailer": False,
        "languages": ["en"],
        "palette": ("#170a1f", "#e0436b"),
        "description": (
            "An underground street racer takes one last job: deliver a passenger "
            "nobody is supposed to see before sunrise."
        ),
        "scenes": [
            "One lap. One passenger. No second chances.",
            "The city looks different at two hundred.",
            "Do not look in the rear-view mirror.",
            "There is no finish line after midnight.",
            "Drive.",
        ],
    },
    {
        "slug": "paper-moons",
        "title": "Paper Moons",
        "year": 2022,
        "rating": "PG",
        "genres": ["Romance", "Fantasy"],
        "featured": False,
        "trailer": False,
        "languages": ["en"],
        "palette": ("#1b102b", "#f0a6ca"),
        "description": (
            "Two strangers meet every night in a dream of the same impossible city, "
            "but neither can remember the other when morning comes."
        ),
        "scenes": [
            "We have met before. I am almost certain.",
            "Fold a wish tightly enough and it becomes a moon.",
            "The city is different when we are together.",
            "Do not wake up yet.",
            "I will find you at the next dream.",
        ],
    },
    {
        "slug": "deep-field",
        "title": "Deep Field",
        "year": 2024,
        "rating": "G",
        "genres": ["Documentary"],
        "featured": False,
        "trailer": False,
        "languages": ["en"],
        "palette": ("#04111f", "#5aa9ff"),
        "description": (
            "A quiet look at the scientists who pointed the world's most powerful "
            "telescope at nothing, and found everything."
        ),
        "scenes": [
            "To see the beginning, look at the darkest part of the sky.",
            "Every point of light is a story older than the Earth.",
            "We built a mirror to catch the past.",
            "The universe is not empty. It is patient.",
            "Some questions take light-years to answer.",
        ],
    },
    {
        "slug": "echoes-of-tomorrow",
        "title": "Echoes of Tomorrow",
        "year": 2026,
        "rating": "PG-13",
        "genres": ["Science Fiction", "Drama"],
        "featured": False,
        "trailer": True,
        "languages": ["en"],
        "palette": ("#0d1a33", "#9b8cff"),
        "description": (
            "A broken time loop gives two strangers one hour to remember the life "
            "they never lived."
        ),
        "scenes": [
            "Tomorrow called. It did not leave a message.",
            "We have done this before. That is the problem.",
            "Time is a room with too many doors.",
            "If this is the last hour, make it count.",
            "Meet me where the future begins.",
        ],
    },
]

SHOWS = [
    {
        "slug": "signal-lost",
        "title": "Signal Lost",
        "year": 2025,
        "rating": "TV-14",
        "genres": ["Science Fiction", "Mystery"],
        "featured": True,
        "trailer": True,
        "palette": ("#06132b", "#38bdf8"),
        "description": (
            "When every satellite above a remote research station goes silent, a "
            "small team learns the transmissions are not coming from space."
        ),
        "seasons": [
            {
                "number": 1,
                "title": "The Quiet Sky",
                "description": "The first signals arrive, and the station goes dark.",
                "episodes": [
                    {
                        "title": "Static",
                        "description": "The team wakes to a sky full of dead channels.",
                        "languages": ["en"],
                    },
                    {
                        "title": "Carrier Wave",
                        "description": "A second signal points back toward the station.",
                        "languages": ["en"],
                    },
                ],
            },
            {
                "number": 2,
                "title": "Dead Air",
                "description": "The signal has an answer, and it knows their names.",
                "episodes": [
                    {
                        "title": "Dead Air",
                        "description": "A broadcast from tomorrow forces a terrible choice.",
                        "languages": ["en"],
                    },
                ],
            },
        ],
    },
    {
        "slug": "kitchen-at-midnight",
        "title": "Kitchen at Midnight",
        "year": 2024,
        "rating": "TV-PG",
        "genres": ["Comedy", "Drama"],
        "featured": False,
        "trailer": False,
        "palette": ("#20130a", "#f59e0b"),
        "description": (
            "A burned-out chef inherits a tiny restaurant that only opens after the "
            "last train, where every customer has a story and no one orders dessert."
        ),
        "seasons": [
            {
                "number": 1,
                "title": "Service",
                "description": "A new kitchen opens its doors at the strangest hour.",
                "episodes": [
                    {
                        "title": "Mise en Place",
                        "description": "The first night brings a critic, a storm and no onions.",
                        "languages": ["en"],
                    },
                    {
                        "title": "Last Order",
                        "description": "One table refuses to leave until the secret is served.",
                        "languages": ["en"],
                    },
                ],
            },
        ],
    },
    {
        "slug": "the-wild-line",
        "title": "The Wild Line",
        "year": 2023,
        "rating": "TV-G",
        "genres": ["Documentary", "Adventure"],
        "featured": False,
        "trailer": False,
        "palette": ("#0a1f16", "#4ade80"),
        "description": (
            "A field team follows a river from its first spring to the sea, meeting "
            "the people and creatures that depend on every mile of it."
        ),
        "seasons": [
            {
                "number": 1,
                "title": "Source to Sea",
                "description": "One river. One season. A thousand small lives.",
                "episodes": [
                    {
                        "title": "Headwaters",
                        "description": "The journey begins at a spring no map agrees on.",
                        "languages": ["en"],
                    },
                    {
                        "title": "The Crossing",
                        "description": "A flooded valley tests the team and the wildlife alike.",
                        "languages": ["en"],
                    },
                ],
            },
        ],
    },
]

SUBTITLE_LINES = {
    "en": [
        "The signal is still moving.",
        "We should not be here after dark.",
        "Tell me what you remember.",
        "There is more than one way out.",
        "Then we do it together.",
    ],
    "es": [
        "La señal sigue moviéndose.",
        "No deberíamos estar aquí después del anochecer.",
        "Dime qué recuerdas.",
        "Hay más de una salida.",
        "Entonces lo haremos juntos.",
    ],
}

LANGUAGE_LABELS = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "it": "Italiano",
    "nl": "Nederlands",
    "pl": "Polski",
    "sv": "Svenska",
    "tr": "Türkçe",
    "ru": "Русский",
    "ar": "العربية",
    "hi": "हिन्दी",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
}

# Video lengths in seconds. They are deliberately short so the demo starts fast.
MOVIE_DURATION = 30
EPISODE_DURATION = 20
TRAILER_DURATION = 7
FPS = 24

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


# ---------------------------------------------------------------------------
# Colour and artwork helpers
# ---------------------------------------------------------------------------
def _rgb(value: str) -> tuple[int, int, int]:
    clean = value.lstrip("#")
    return tuple(int(clean[index : index + 2], 16) for index in (0, 2, 4))


def _mix(first: tuple[int, int, int], second: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(first, second))


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_title(
    draw: ImageDraw.ImageDraw,
    text: str,
    initial_size: int,
    max_width: int,
    max_lines: int,
    minimum_size: int = 26,
):
    size = initial_size
    while size > minimum_size:
        font = _font(size)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 4
    font = _font(minimum_size)
    return font, _wrap(draw, text, font, max_width)


def _background(width: int, height: int, palette: tuple[str, str], seed: int) -> Image.Image:
    random.seed(seed)
    first, second = (_rgb(palette[0]), _rgb(palette[1]))
    base = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / max(1, height - 1)
        colour = _mix(first, second, t)
        draw.line([(0, y), (width, y)], fill=colour)

    # Soft coloured glows.
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    spots = [
        (width * 0.82, height * 0.12, width * 0.44, 110),
        (width * 0.18, height * 0.76, width * 0.36, 75),
        (width * 0.65, height * 0.58, width * 0.24, 48),
    ]
    for cx, cy, radius, alpha in spots:
        glow_draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=second + (alpha,),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(max(18, width // 18)))
    image = Image.alpha_composite(base.convert("RGBA"), glow)

    # Fine geometric decoration.
    decor = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    decor_draw = ImageDraw.Draw(decor)
    for ring in range(4):
        radius = width * (0.32 + ring * 0.09)
        centre_x = width * 0.78
        centre_y = height * 0.30
        decor_draw.ellipse(
            [centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius],
            outline=second + (34 - ring * 6,),
            width=max(1, width // 640),
        )
    for index in range(5):
        y = height * (0.18 + index * 0.13)
        decor_draw.line(
            [(0, y), (width * 0.42, y - height * 0.06)],
            fill=second + (16,),
            width=1,
        )
    # Subtle vignette and bottom text scrim.
    scrim = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for y in range(int(height * 0.45), height):
        t = (y - height * 0.45) / (height * 0.55)
        scrim_draw.line([(0, y), (width, y)], fill=(3, 7, 16, int(190 * t)))
    image = Image.alpha_composite(image, decor)
    image = Image.alpha_composite(image, scrim)
    return image


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    centre_x: int,
    start_y: int,
    fill,
    line_gap: int,
) -> int:
    y = start_y
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text((centre_x - width / 2, y), line, font=font, fill=fill)
        y += font.size + line_gap
    return y


def _make_poster(
    title: str,
    year: int,
    rating: str,
    genres: list[str],
    palette: tuple[str, str],
    destination: Path,
    seed: int,
) -> None:
    width, height = 600, 900
    image = _background(width, height, palette, seed)
    draw = ImageDraw.Draw(image)
    accent = _rgb(palette[1])

    label_font = _font(17)
    label = "D O D O   T V   O R I G I N A L"
    label_width = draw.textlength(label, font=label_font)
    draw.text(((width - label_width) / 2, 48), label, font=label_font, fill=accent)

    # Accent rule and small indexed mark.
    draw.line([(48, 84), (width - 48, 84)], fill=accent + (80,), width=1)
    draw.text((48, 106), "A DODO TV PRESENTATION", font=_font(13), fill=(255, 255, 255, 150))

    title_font, lines = _fit_title(draw, title, 82, width - 96, 3)
    line_gap = 8
    block_height = len(lines) * (title_font.size + line_gap)
    y = max(420, height - 230 - block_height)
    y = _draw_centered_lines(
        draw, lines, title_font, width // 2, y, (247, 250, 255), line_gap
    )

    draw.line([(width // 2 - 34, y + 20), (width // 2 + 34, y + 20)], fill=accent, width=2)
    meta = f"{year}  ·  {rating}  ·  {' / '.join(genres[:2]).upper()}"
    meta_font = _font(16)
    meta_width = draw.textlength(meta, font=meta_font)
    draw.text(((width - meta_width) / 2, y + 38), meta, font=meta_font, fill=(210, 220, 235))
    draw.text(
        (width // 2 - draw.textlength("STREAMING NOW", font=_font(12)) / 2, height - 52),
        "STREAMING NOW",
        font=_font(12),
        fill=accent,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination, "JPEG", quality=91, optimize=True)


def _make_landscape(
    title: str,
    kicker: str,
    footer: str,
    palette: tuple[str, str],
    destination: Path,
    seed: int,
) -> None:
    width, height = 1600, 900
    image = _background(width, height, palette, seed)
    draw = ImageDraw.Draw(image)
    accent = _rgb(palette[1])

    draw.text((64, 58), "DODO TV", font=_font(24), fill=(255, 255, 255))
    draw.rectangle([64, 96, 64 + 94, 99], fill=accent)
    draw.text((64, 118), kicker.upper(), font=_font(14), fill=accent)

    title_font, lines = _fit_title(draw, title, 86, width - 128, 2)
    y = height - 260 - len(lines) * (title_font.size + 6)
    for line in lines:
        draw.text((64, y), line, font=title_font, fill=(248, 250, 255))
        y += title_font.size + 6
    draw.text((64, y + 18), footer, font=_font(19), fill=(212, 222, 236))
    draw.text(
        (width - 64 - draw.textlength("ORIGINAL", font=_font(13)), 58),
        "ORIGINAL",
        font=_font(13),
        fill=accent,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination, "JPEG", quality=92, optimize=True)


# ---------------------------------------------------------------------------
# Video, subtitle and media helpers
# ---------------------------------------------------------------------------
def _run_ffmpeg(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"FFmpeg timed out while creating {label}.") from exc
    except OSError as exc:
        raise MediaError(f"Could not run FFmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        message = detail[-1] if detail else "unknown error"
        raise MediaError(f"FFmpeg could not create {label}: {message}")


def _make_video(
    scene: Path,
    destination: Path,
    duration: int,
    frequency: int,
    label: str,
    fade_in: float = 0.7,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fade_out_start = max(0.0, duration - fade_in)
    video_filter = (
        "scale=1600:900,"
        "zoompan=z='min(1+0.0009*on,1.18)':"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s=1280x720:fps={FPS},"
        "format=yuv420p,"
        f"fade=t=in:st=0:d={fade_in},fade=t=out:st={fade_out_start}:d={fade_in}"
    )
    audio_filter = (
        "volume=0.045,"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start}:d={fade_in}"
    )
    command = [
        current_app.config.get("FFMPEG_BINARY", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(FPS),
        "-i",
        str(scene),
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
        "-t",
        str(duration),
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(destination),
    ]
    _run_ffmpeg(command, label)
    if not destination.exists() or destination.stat().st_size == 0:
        raise MediaError(f"FFmpeg reported success but {label} was not written.")


def _timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _write_vtt(relative_path: str, language: str, duration: int) -> str:
    lines = SUBTITLE_LINES.get(language, SUBTITLE_LINES["en"])
    cues = []
    start = 0.8
    for index, text in enumerate(lines):
        end = min(start + 2.9, max(1.2, duration - 0.6))
        if end <= start:
            break
        cues.append(f"{index + 1}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n")
        start = end + 0.55
    content = "WEBVTT\n\n" + "\n".join(cues)
    absolute = resolve_path(relative_path)
    if absolute is None:
        raise MediaError("Could not resolve the subtitle destination.")
    absolute.parent.mkdir(parents=True, exist_ok=True)
    absolute.write_text(content, encoding="utf-8")
    return relative_path


def _genre_list(names: list[str]) -> list[Genre]:
    genres = []
    for name in names:
        genre = Genre.query.filter_by(name=name).first()
        if genre is None:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            genre = Genre(name=name, slug=slug)
            db.session.add(genre)
            db.session.flush()
        genres.append(genre)
    return genres


def _subtitle_for(owner, relative_path: str, language: str, default: bool, field: str) -> None:
    existing = Subtitle.query.filter_by(**{field: owner.id}, language=language).first()
    if existing:
        return
    if default:
        Subtitle.query.filter_by(**{field: owner.id}).update(
            {Subtitle.is_default: False}, synchronize_session=False
        )
    db.session.add(
        Subtitle(
            language=language,
            label=LANGUAGE_LABELS.get(language, language.upper()),
            file_path=relative_path,
            is_default=default,
            **{field: owner.id},
        )
    )


def _create_subtitles(owner, slug: str, languages: list[str], duration: int, field: str) -> None:
    for index, language in enumerate(languages):
        relative = f"subtitles/demo_{slug}_{language}.vtt"
        _write_vtt(relative, language, duration)
        _subtitle_for(owner, relative, language, default=index == 0, field=field)


def _ensure_dirs() -> None:
    ensure_upload_dirs()
    for category in ("thumbnails", "generated_frames", "movies", "episodes", "trailers", "subtitles", "temp"):
        path = resolve_path(category)
        if path is not None:
            path.mkdir(parents=True, exist_ok=True)


def _scene_path(slug: str) -> Path:
    path = resolve_path(f"temp/demo_scene_{slug}.jpg")
    assert path is not None
    return path


def _poster_for(slug: str, title: str, year: int, rating: str, genres: list[str], palette, seed: int) -> str:
    relative = f"thumbnails/demo_poster_{slug}.jpg"
    absolute = resolve_path(relative)
    assert absolute is not None
    _make_poster(title, year, rating, genres, palette, absolute, seed)
    return relative


def _landscape_for(relative: str, title: str, kicker: str, footer: str, palette, seed: int) -> str:
    absolute = resolve_path(relative)
    assert absolute is not None
    _make_landscape(title, kicker, footer, palette, absolute, seed)
    return relative


def _existing_movie(title: str) -> Movie | None:
    return Movie.query.filter(Movie.title == title).first()


def _existing_show(title: str) -> TVShow | None:
    return TVShow.query.filter(TVShow.title == title).first()


# ---------------------------------------------------------------------------
# Catalog builders
# ---------------------------------------------------------------------------
def _create_movie(spec: dict) -> Movie:
    existing = _existing_movie(spec["title"])
    if existing is not None:
        return existing

    slug = spec["slug"]
    scene = _scene_path(slug)
    _make_landscape(
        title=spec["title"],
        kicker="A Dodo TV original film",
        footer=f"{spec['year']}  ·  {spec['rating']}  ·  {' / '.join(spec['genres'])}",
        palette=spec["palette"],
        destination=scene,
        seed=sum(map(ord, slug)),
    )
    video_relative = f"movies/demo_{slug}.mp4"
    video_absolute = resolve_path(video_relative)
    assert video_absolute is not None
    _make_video(scene, video_absolute, MOVIE_DURATION, 220, f"the {spec['title']} video")
    scene.unlink(missing_ok=True)
    info = probe_video(video_absolute)

    poster_relative = _poster_for(
        slug,
        spec["title"],
        spec["year"],
        spec["rating"],
        spec["genres"],
        spec["palette"],
        sum(map(ord, slug)) + 7,
    )

    trailer_relative = None
    if spec.get("trailer"):
        trailer_scene = _scene_path(f"{slug}_trailer")
        _make_landscape(
            title=spec["title"],
            kicker="Official trailer",
            footer="Coming soon to Dodo TV",
            palette=spec["palette"],
            destination=trailer_scene,
            seed=sum(map(ord, slug)) + 19,
        )
        trailer_relative = f"trailers/demo_{slug}_trailer.mp4"
        trailer_absolute = resolve_path(trailer_relative)
        assert trailer_absolute is not None
        _make_video(
            trailer_scene,
            trailer_absolute,
            TRAILER_DURATION,
            330,
            f"the {spec['title']} trailer",
        )
        trailer_scene.unlink(missing_ok=True)

    movie = Movie(
        title=spec["title"],
        description=spec["description"],
        release_year=spec["year"],
        age_rating=spec["rating"],
        runtime=max(1, int(round(info["duration"]))),
        video_path=video_relative,
        trailer_path=trailer_relative,
        poster_path=poster_relative,
        featured=bool(spec.get("featured")),
        published=True,
        genres=_genre_list(spec["genres"]),
    )
    db.session.add(movie)
    db.session.flush()

    frames = generate_frames(movie.video_path, f"movie_{movie.id}", count=6)
    add_frames_to_db(movie, frames)
    _create_subtitles(movie, slug, spec["languages"], MOVIE_DURATION, "movie_id")
    db.session.commit()
    return movie


def _create_episode(show: TVShow, season: Season, spec: dict, slug: str, number: int, palette) -> Episode:
    existing = Episode.query.filter_by(
        show_id=show.id, season_id=season.id, number=number
    ).first()
    if existing is not None:
        return existing

    scene = _scene_path(f"{slug}_s{season.number}_e{number}")
    _make_landscape(
        title=spec["title"],
        kicker=f"{show.title}  ·  S{season.number:02d}E{number:02d}",
        footer=f"{spec['description']}",
        palette=palette,
        destination=scene,
        seed=sum(map(ord, f"{slug}{season.number}{number}")),
    )
    video_relative = f"episodes/demo_{slug}_s{season.number}e{number}.mp4"
    video_absolute = resolve_path(video_relative)
    assert video_absolute is not None
    _make_video(
        scene,
        video_absolute,
        EPISODE_DURATION,
        180 + number * 25,
        f"the {spec['title']} episode",
    )
    scene.unlink(missing_ok=True)
    info = probe_video(video_absolute)

    thumb_relative = f"thumbnails/demo_episode_{slug}_s{season.number}e{number}.jpg"
    _landscape_for(
        thumb_relative,
        spec["title"],
        f"{show.title}  ·  S{season.number:02d}E{number:02d}",
        spec["description"],
        palette,
        sum(map(ord, f"{slug}{season.number}{number}")) + 11,
    )

    episode = Episode(
        show_id=show.id,
        season_id=season.id,
        season_number=season.number,
        number=number,
        title=spec["title"],
        description=spec["description"],
        runtime=max(1, int(round(info["duration"]))),
        video_path=video_relative,
        thumbnail_path=thumb_relative,
        published=True,
    )
    db.session.add(episode)
    db.session.flush()

    frames = generate_frames(episode.video_path, f"episode_{episode.id}", count=5)
    add_frames_to_db(episode, frames)
    _create_subtitles(
        episode,
        f"{slug}_s{season.number}e{number}",
        spec.get("languages", ["en"]),
        EPISODE_DURATION,
        "episode_id",
    )
    db.session.commit()
    return episode


def _create_show(spec: dict) -> TVShow:
    existing = _existing_show(spec["title"])
    if existing is not None:
        return existing

    slug = spec["slug"]
    poster_relative = _poster_for(
        slug,
        spec["title"],
        spec["year"],
        spec["rating"],
        spec["genres"],
        spec["palette"],
        sum(map(ord, slug)) + 3,
    )

    trailer_relative = None
    if spec.get("trailer"):
        trailer_scene = _scene_path(f"{slug}_trailer")
        _make_landscape(
            title=spec["title"],
            kicker="Official series trailer",
            footer="A Dodo TV original series",
            palette=spec["palette"],
            destination=trailer_scene,
            seed=sum(map(ord, slug)) + 23,
        )
        trailer_relative = f"trailers/demo_{slug}_trailer.mp4"
        trailer_absolute = resolve_path(trailer_relative)
        assert trailer_absolute is not None
        _make_video(
            trailer_scene,
            trailer_absolute,
            TRAILER_DURATION,
            280,
            f"the {spec['title']} trailer",
        )
        trailer_scene.unlink(missing_ok=True)

    show = TVShow(
        title=spec["title"],
        description=spec["description"],
        release_year=spec["year"],
        age_rating=spec["rating"],
        poster_path=poster_relative,
        trailer_path=trailer_relative,
        featured=bool(spec.get("featured")),
        published=False,
        genres=_genre_list(spec["genres"]),
    )
    db.session.add(show)
    db.session.flush()

    for season_spec in spec["seasons"]:
        season = Season.query.filter_by(show_id=show.id, number=season_spec["number"]).first()
        if season is None:
            season = Season(
                show_id=show.id,
                number=season_spec["number"],
                title=season_spec.get("title", ""),
                description=season_spec.get("description", ""),
            )
            db.session.add(season)
            db.session.flush()
        for number, episode_spec in enumerate(season_spec["episodes"], start=1):
            _create_episode(show, season, episode_spec, slug, number, spec["palette"])

    show.published = True
    db.session.commit()
    return show


# ---------------------------------------------------------------------------
# Admin and progress
# ---------------------------------------------------------------------------
def _demo_admin_credentials() -> tuple[str, str, str]:
    return (
        os.getenv("DEMO_ADMIN_USERNAME", "dodo_admin").strip() or "dodo_admin",
        os.getenv("DEMO_ADMIN_EMAIL", "admin@dodotv.local").strip().lower()
        or "admin@dodotv.local",
        os.getenv("DEMO_ADMIN_PASSWORD", "DodoTV!Demo2026"),
    )


def _ensure_demo_admin() -> tuple[User, str]:
    username, email, password = _demo_admin_credentials()
    user = User.query.filter(
        (db.func.lower(User.username) == username.lower())
        | (db.func.lower(User.email) == email)
    ).first()
    if user is None:
        user = User(username=username, email=email, is_admin=True, is_active=True)
        user.set_password(password)
        db.session.add(user)
    else:
        user.is_admin = True
        user.is_active = True
        user.set_password(password)
    db.session.commit()
    return user, password


def _seed_progress(user: User, movie: Movie, show: TVShow) -> None:
    first_episode = show.first_episode()
    entries: list[tuple[Movie | None, Episode | None, float, float]] = [
        (movie, None, MOVIE_DURATION, 0.42),
    ]
    if first_episode is not None:
        entries.append((None, first_episode, EPISODE_DURATION, 0.58))

    for movie_item, episode_item, duration, fraction in entries:
        if movie_item is not None:
            existing = WatchProgress.query.filter_by(
                user_id=user.id, movie_id=movie_item.id
            ).first()
        else:
            existing = WatchProgress.query.filter_by(
                user_id=user.id, episode_id=episode_item.id
            ).first()
        if existing is None:
            existing = WatchProgress(
                user_id=user.id,
                movie_id=movie_item.id if movie_item else None,
                episode_id=episode_item.id if episode_item else None,
            )
            db.session.add(existing)
        duration = max(float(duration), 1.0)
        existing.duration = duration
        existing.position = round(duration * fraction, 2)
        existing.completed = False
        existing.updated_at = datetime.now(timezone.utc)

        history = WatchHistory.query.filter_by(
            user_id=user.id,
            movie_id=movie_item.id if movie_item else None,
            episode_id=episode_item.id if episode_item else None,
        ).first()
        if history is None:
            history = WatchHistory(
                user_id=user.id,
                movie_id=movie_item.id if movie_item else None,
                episode_id=episode_item.id if episode_item else None,
            )
            db.session.add(history)
        history.watched_at = datetime.now(timezone.utc)
    db.session.commit()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def seed_demo(with_admin: bool = False) -> dict:
    """Create the demo catalog. Returns a summary suitable for the CLI."""
    if not ffmpeg_available() or not ffprobe_available():
        raise MediaError(
            "FFmpeg and FFprobe are required to generate the demo catalog. "
            "Install FFmpeg, make sure both binaries are on your PATH, or set "
            "FFMPEG_BINARY and FFPROBE_BINARY in .env, then try again."
        )

    _ensure_dirs()

    created_movies = 0
    created_shows = 0
    for spec in MOVIES:
        before = _existing_movie(spec["title"])
        _create_movie(spec)
        if before is None:
            created_movies += 1

    for spec in SHOWS:
        before = _existing_show(spec["title"])
        _create_show(spec)
        if before is None:
            created_shows += 1

    admin_user = None
    admin_password = None
    if with_admin:
        admin_user, admin_password = _ensure_demo_admin()

    first_movie = _existing_movie(MOVIES[0]["title"])
    first_show = _existing_show(SHOWS[0]["title"])
    if admin_user is not None and first_movie is not None and first_show is not None:
        _seed_progress(admin_user, first_movie, first_show)

    Setting.set("demo_seeded", "1")
    db.session.commit()

    return {
        "movies": created_movies,
        "shows": created_shows,
        "admin": admin_user,
        "admin_password": admin_password,
        "total_movies": Movie.query.count(),
        "total_shows": TVShow.query.count(),
        "total_episodes": Episode.query.count(),
    }
