"""Import one YouTube video through the normal validated media pipeline."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import FileStorage
from dodo.web import current_app

from .media import (
    MediaError, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ensure_upload_dirs,
    ffmpeg_available, save_image, save_subtitle, save_video, upload_root,
)


def youtube_url(value: str) -> str:
    """Accept only individual YouTube videos and discard all other parameters."""
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443}):
            raise ValueError()
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        video_id = ''
        if host == 'youtu.be' and len(parts) == 1:
            video_id = parts[0]
        elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            if parsed.path == '/watch':
                video_id = parse_qs(parsed.query).get('v', [''])[0]
            elif len(parts) == 2 and parts[0] in {'shorts', 'embed', 'live'}:
                video_id = parts[1]
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            raise ValueError()
    except ValueError:
        raise MediaError('Enter a YouTube video URL, such as https://www.youtube.com/watch?v=VIDEO_ID.') from None
    return f'https://www.youtube.com/watch?v={video_id}'


class _QuietLogger:
    # yt-dlp diagnostics can contain signed URLs and query values.
    def debug(self, message):
        pass

    info = warning = error = debug


def download_youtube(form, category: str, new_files: list[str]) -> dict:
    """Append every saved path immediately so the caller can roll back on failure."""
    try:
        import yt_dlp
    except ImportError:
        raise MediaError('YouTube downloads require yt-dlp. Install the workspace requirements and restart.') from None
    url = youtube_url(form.youtube_url.data)
    if not ffmpeg_available():
        raise MediaError('YouTube downloads require FFmpeg. Install FFmpeg and restart DodoStack.')
    ensure_upload_dirs()
    mode = form.youtube_subtitles.data
    source = form.youtube_subtitle_source.data
    languages = ['all'] if mode == 'all' else [code.strip() for code in (form.youtube_languages.data or '').split(',') if code.strip()]
    maximum = current_app.config['MAX_CONTENT_LENGTH']
    thumbnail = bool(form.youtube_thumbnail.data) and not form.thumbnail.data

    def check_size(progress):
        size = max(progress.get('downloaded_bytes') or 0, progress.get('total_bytes') or 0)
        if size > maximum:
            raise MediaError('The YouTube video exceeds the configured video size limit.')

    def reject_live(info, *, incomplete=False):
        if info.get('is_live') or info.get('is_upcoming'):
            return 'Live and upcoming videos cannot be imported.'
        return None

    with TemporaryDirectory(prefix='youtube-', dir=upload_root() / 'temp') as directory:
        options = {
            'outtmpl': str(Path(directory) / 'video.%(ext)s'),
            'format': 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4][vcodec^=avc1]',
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'allowed_extractors': ['youtube'],
            'writesubtitles': mode != 'none' and source in {'manual', 'both'},
            'writeautomaticsub': mode != 'none' and source in {'auto', 'both'},
            'subtitleslangs': languages if mode != 'none' else [],
            'subtitlesformat': 'vtt',
            'writethumbnail': thumbnail,
            'max_filesize': maximum,
            'socket_timeout': 30,
            'retries': 2,
            'fragment_retries': 2,
            'quiet': True,
            'noprogress': True,
            'logger': _QuietLogger(),
            'progress_hooks': [check_size],
            'match_filter': reject_live,
            'ffmpeg_location': shutil.which(current_app.config.get('FFMPEG_BINARY', 'ffmpeg')),
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
        except MediaError:
            raise
        except Exception:
            raise MediaError('YouTube download failed. Check that the video is available and try again. Some videos require sign-in or cannot be downloaded.') from None
        if not info or info.get('_type') in {'playlist', 'multi_video'}:
            raise MediaError('Choose an available individual video, not a playlist or live stream.')
        folder = Path(directory)
        videos = [path for path in folder.iterdir() if path.suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise MediaError('YouTube did not provide a complete video within the configured size limit.')
        with videos[0].open('rb') as stream:
            video, probe = save_video(FileStorage(stream, filename=videos[0].name), category)
        new_files.append(video)
        result = {'video': video, 'duration': probe['duration'], 'thumbnail': None, 'subtitles': [], 'warnings': []}
        if thumbnail:
            images = [path for path in folder.iterdir() if path.suffix.lower() in IMAGE_EXTENSIONS]
            if images:
                with images[0].open('rb') as stream:
                    result['thumbnail'] = save_image(FileStorage(stream, filename=images[0].name))
                new_files.append(result['thumbnail'])
            else:
                result['warnings'].append('No YouTube thumbnail was available. Upload one or choose a generated frame.')
        for language, track in (info.get('requested_subtitles') or {}).items():
            if not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', language) or len(language) > 16:
                continue
            path = folder / f'video.{language}.vtt'
            if not path.is_file():
                continue
            with path.open('rb') as stream:
                relative = save_subtitle(FileStorage(stream, filename=path.name))
            new_files.append(relative)
            automatic = language not in (info.get('subtitles') or {}) or source == 'auto'
            label = (track.get('name') or language) + (' (automatic)' if automatic else '')
            result['subtitles'].append({'language': language, 'label': label[:96], 'file_path': relative})
        if mode != 'none' and not result['subtitles']:
            result['warnings'].append('No matching subtitles were available for the selected languages and source.')
        return result
