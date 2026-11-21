"""Validation shared by the crawler and search results."""
import re
from urllib.parse import parse_qs, urlparse


def youtube_video_id(url: str | None) -> str | None:
    try:
        parsed = urlparse(url or '')
        if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or '').lower().rstrip('.')
        path = parsed.path.strip('/')
        video_id = None
        if host == 'youtu.be':
            video_id = path.split('/')[0]
        elif any(host == domain or host.endswith('.' + domain)
                 for domain in ('youtube.com', 'youtube-nocookie.com')):
            if path == 'watch':
                video_id = parse_qs(parsed.query).get('v', [''])[0]
            elif path.startswith(('embed/', 'shorts/', 'live/')):
                video_id = path.split('/')[1]
        if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            return video_id
    except ValueError:
        pass
    return None


def youtube_embed_url(url: str | None) -> str | None:
    video_id = youtube_video_id(url)
    return f'https://www.youtube-nocookie.com/embed/{video_id}' if video_id else None
