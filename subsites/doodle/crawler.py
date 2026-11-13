import ipaddress
import socket
import threading
import logging
from collections import deque
from urllib.parse import parse_qs, urljoin, urlparse

from urllib3 import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import HTTPError, TimeoutError as FetchTimeout
from urllib3.util import Timeout
from urllib3.util import parse_url
from bs4 import BeautifulSoup

from .database import get_page_urls, replace_media_assets, upsert_page, url_exists

logger = logging.getLogger(__name__)

class BlockedDestination(ValueError):
    pass


GLOBAL_IPV6 = ipaddress.ip_network("2000::/3")


def public_destination(url):
    # Use the same parser and normalized path as the HTTP client.
    target = parse_url(url)
    if (target.scheme not in {"http", "https"} or not target.host or target.auth is not None
            or target.port == 0):
        raise BlockedDestination()
    hostname = target.host.strip("[]")
    port = target.port or (443 if target.scheme == "https" else 80)
    records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    if not addresses or any(not address.is_global or address.is_multicast or address.is_reserved
                            or address.is_loopback or address.is_link_local
                            or (address.version == 6 and
                                (address not in GLOBAL_IPV6 or address.sixtofour or address.teredo))
                            for address in addresses):
        raise BlockedDestination()
    return target, str(addresses[0]), hostname, port


def fetch_page(url):
    for _ in range(6):
        target, address, hostname, port = public_destination(url)
        if target.scheme == "https":
            pool = HTTPSConnectionPool(address, port, server_hostname=hostname,
                                       assert_hostname=hostname, cert_reqs="CERT_REQUIRED")
        else:
            pool = HTTPConnectionPool(address, port)
        response = None
        try:
            response = pool.urlopen("GET", target.request_uri, headers={**HEADERS, "Host": target.netloc},
                                    timeout=Timeout(connect=REQUEST_TIMEOUT, read=REQUEST_TIMEOUT),
                                    redirect=False, retries=False, preload_content=False,
                                    decode_content=False)
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            if not 200 <= response.status < 300:
                return url, None, f"HTTP {response.status}"
            if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/html":
                return url, None, "Skipped — not HTML"
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                return url, None, "Skipped — unsupported content encoding"
            content = response.read(MAX_RESPONSE_BYTES + 1, decode_content=False)
            if len(content) > MAX_RESPONSE_BYTES:
                return url, None, "Skipped — response is too large"
            return url, content, None
        finally:
            if response is not None:
                response.close()
            pool.close()
    return url, None, "Too many redirects"


HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; DoodleBot/1.0)', 'Accept-Encoding': 'identity'}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 10          # seconds
MAX_LINKS_PER_PAGE = 15       # links to enqueue per crawled page
MAX_CONTENT_CHARS = 50_000    # chars stored in DB
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.avif')
VIDEO_EXTENSIONS = ('.mp4', '.webm', '.mov', '.m4v', '.ogv')
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.ogg', '.oga', '.flac', '.m4a', '.aac')


class CrawlerManager:
    def __init__(self):
        self._queue: list[tuple[str, int]] = []   # (url, depth)
        self._status = 'idle'   # idle | crawling | done
        self._current_url: str | None = None
        self._crawled_count = 0
        self._results: deque[dict] = deque(maxlen=100)  # recent crawl log only
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ queue

    def add_url(self, url: str) -> tuple[bool, str]:
        url = url.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            _, _, hostname, _ = public_destination(url)
        except BlockedDestination:
            logger.warning('SSRF blocked (queue)')
            return False, 'URL must point to a public HTTP or HTTPS address'
        except (HTTPError, OSError, ValueError):
            return False, 'Invalid or unreachable URL'

        with self._lock:
            queued_urls = {u for u, _ in self._queue}
            if url in queued_urls:
                return False, 'URL is already in the queue'
            if url_exists(url):
                return False, 'URL has already been crawled'
            self._queue.append((url, 0))

        logger.debug('Queued URL host=%s', hostname)
        return True, 'URL added to queue'

    def get_queue(self) -> list[dict]:
        with self._lock:
            return [{'url': u, 'depth': d} for u, d in self._queue]

    def remove_url(self, url: str) -> tuple[bool, str]:
        target = (url or '').strip()
        if not target:
            return False, 'URL is required'

        with self._lock:
            before = len(self._queue)
            self._queue = [(u, d) for (u, d) in self._queue if u != target]
            removed = before - len(self._queue)

        if removed > 0:
            logger.debug('Removed %d queue entries', removed)
            return True, 'URL removed from queue'
        return False, 'URL not found in queue'

    def add_urls_for_recrawl(self, urls: list[str]) -> int:
        """Queue URLs bypassing the url_exists check so already-crawled pages are re-fetched."""
        with self._lock:
            queued_urls = {u for u, _ in self._queue}
            added = 0
            for url in urls:
                if url not in queued_urls:
                    self._queue.append((url, 0))
                    queued_urls.add(url)
                    added += 1
        if added:
            logger.info('Queued %d URL(s) for recrawl', added)
        return added

    # ----------------------------------------------------------------- status

    def get_status(self) -> dict:
        with self._lock:
            return {
                'status':        self._status,
                'current_url':   self._current_url,
                'queue_length':  len(self._queue),
                'crawled_count': self._crawled_count,
                'results':       list(self._results)[-30:],
            }

    # ------------------------------------------------------------------ crawl

    def start_crawl(self, max_depth: int = 1) -> tuple[bool, str]:
        with self._lock:
            if self._status == 'crawling':
                return False, 'Already crawling'
            if not self._queue:
                return False, 'Queue is empty — add at least one URL first'
            self._status = 'crawling'
            self._crawled_count = 0

        t = threading.Thread(target=self._worker, args=(max_depth,), daemon=True)
        t.start()
        logger.info('Crawl started (max_depth=%d)', max_depth)
        return True, 'Crawl started'

    def backfill_media(self, limit: int | None = None) -> tuple[bool, str]:
        with self._lock:
            if self._status == 'crawling':
                return False, 'Already crawling'

        urls = get_page_urls(limit=limit)
        if not urls:
            return False, 'No indexed pages available to backfill'

        with self._lock:
            if self._status == 'crawling':
                return False, 'Already crawling'
            queued_urls = {u for u, _ in self._queue}
            added = 0
            for url in urls:
                if url not in queued_urls:
                    self._queue.append((url, 0))
                    queued_urls.add(url)
                    added += 1

            if added == 0:
                return False, 'All indexed pages are already queued'

            self._status = 'crawling'
            self._crawled_count = 0

        t = threading.Thread(target=self._worker, args=(0,), daemon=True)
        t.start()
        logger.info('Media backfill started for %d indexed pages', added)
        return True, f'Media backfill started for {added} pages'

    def _worker(self, max_depth: int):
        visited: set[str] = set()

        while True:
            with self._lock:
                if not self._queue:
                    self._status = 'done'
                    self._current_url = None
                    logger.info('Crawl complete. Recent results kept: %d', len(self._results))
                    break
                url, depth = self._queue.pop(0)
                if url in visited:
                    continue
                self._current_url = url

            visited.add(url)

            ok, msg = self._crawl_one(url, depth, max_depth, visited)

            with self._lock:
                self._crawled_count += 1
                self._results.append({'url': url, 'success': ok, 'message': msg})

    def _crawl_one(self, url: str, depth: int, max_depth: int, visited: set[str]) -> tuple[bool, str]:
        logger.debug('Crawling URL depth=%d', depth)
        try:
            final_url, content, error = fetch_page(url)
            if error:
                return False, error
            soup = BeautifulSoup(content, 'html.parser')

            # title
            tag = soup.find('title')
            title = tag.get_text(strip=True) if tag else url

            # clean body text
            for unwanted in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                unwanted.decompose()
            text = ' '.join(soup.get_text(separator=' ').split())

            page_id = upsert_page(url, title, text[:MAX_CONTENT_CHARS])
            if page_id is None:
                return False, 'Failed to save page'

            assets = self._extract_media_assets(soup, final_url, title)
            replace_media_assets(page_id, assets)
            logger.debug('Saved crawled page id=%s', page_id)

            # recurse
            if depth < max_depth:
                links = self._extract_links(soup, final_url, urlparse(url).netloc)
                with self._lock:
                    queued = {u for u, _ in self._queue}
                    added = 0
                    for link in links:
                        if link not in visited and link not in queued and not url_exists(link):
                            self._queue.append((link, depth + 1))
                            queued.add(link)
                            added += 1
                            if added >= MAX_LINKS_PER_PAGE:
                                break

            return True, f'Saved — {title[:60]}'

        except BlockedDestination:
            logger.warning('SSRF blocked (crawl)')
            return False, 'Blocked — URL must point to a public HTTP or HTTPS address'
        except FetchTimeout:
            return False, 'Request timed out'
        except (HTTPError, OSError):
            return False, 'Connection error'
        except Exception:
            logger.exception('Unexpected crawl error')
            return False, 'Could not crawl page'

    @staticmethod
    def _extract_links(soup: BeautifulSoup, base_url: str, allowed_domain: str) -> list[str]:
        seen: set[str] = set()
        links: list[str] = []

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            try:
                parsed = urlparse(urljoin(base_url, href))
            except ValueError:
                continue

            if parsed.scheme not in ('http', 'https'):
                continue
            if parsed.netloc != allowed_domain:
                continue

            clean = parsed._replace(fragment='').geturl()
            if clean not in seen:
                seen.add(clean)
                links.append(clean)

        return links

    @classmethod
    def _extract_media_assets(cls, soup: BeautifulSoup, base_url: str, page_title: str) -> list[dict]:
        assets: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def add_asset(kind: str, raw_url: str | None, **extra):
            normalized = cls._normalize_asset_url(raw_url, base_url)
            if not normalized:
                return
            key = (kind, normalized)
            if key in seen:
                return
            seen.add(key)
            asset = {
                'kind': kind,
                'url': normalized,
                'title': extra.get('title') or page_title,
                'thumbnail_url': extra.get('thumbnail_url'),
                'embed_url': extra.get('embed_url'),
                'source_url': extra.get('source_url') or base_url,
                'duration': extra.get('duration'),
            }
            assets.append(asset)

        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or cls._first_srcset_url(img.get('srcset'))
            add_asset(
                'pictures',
                src,
                title=(img.get('alt') or img.get('title') or page_title),
                source_url=base_url,
            )

        for video in soup.find_all('video'):
            video_src = video.get('src')
            poster = cls._normalize_asset_url(video.get('poster'), base_url)
            add_asset('videos', video_src, title=page_title, thumbnail_url=poster, source_url=base_url)
            for source in video.find_all('source'):
                add_asset('videos', source.get('src'), title=page_title, thumbnail_url=poster, source_url=base_url)

        for audio in soup.find_all('audio'):
            audio_src = audio.get('src')
            add_asset('sound', audio_src, title=page_title, source_url=base_url)
            for source in audio.find_all('source'):
                add_asset('sound', source.get('src'), title=page_title, source_url=base_url)

        for iframe in soup.find_all('iframe', src=True):
            src = iframe.get('src')
            if cls._extract_youtube_video_id(src):
                embed_url = cls._normalize_asset_url(src, base_url)
                add_asset(
                    'youtube',
                    src,
                    title=(iframe.get('title') or page_title),
                    embed_url=embed_url,
                    thumbnail_url=cls._youtube_thumbnail(src),
                    source_url=base_url,
                )

        for a in soup.find_all('a', href=True):
            href = a.get('href')
            text = a.get_text(' ', strip=True) or page_title
            normalized = cls._normalize_asset_url(href, base_url)
            if not normalized:
                continue

            lower = normalized.lower()
            if cls._extract_youtube_video_id(normalized):
                add_asset(
                    'youtube',
                    normalized,
                    title=text,
                    embed_url=cls._youtube_embed_url(normalized),
                    thumbnail_url=cls._youtube_thumbnail(normalized),
                    source_url=base_url,
                )
            elif lower.endswith(IMAGE_EXTENSIONS):
                add_asset('pictures', normalized, title=text, source_url=base_url)
            elif lower.endswith(VIDEO_EXTENSIONS):
                add_asset('videos', normalized, title=text, source_url=base_url)
            elif lower.endswith(AUDIO_EXTENSIONS):
                add_asset('sound', normalized, title=text, source_url=base_url)

        return assets

    @staticmethod
    def _first_srcset_url(srcset: str | None) -> str | None:
        if not srcset:
            return None
        first = srcset.split(',')[0].strip()
        return first.split()[0] if first else None

    @staticmethod
    def _normalize_asset_url(raw_url: str | None, base_url: str) -> str | None:
        if not raw_url:
            return None
        try:
            parsed = urlparse(urljoin(base_url, raw_url.strip()))
        except ValueError:
            return None
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return None
        return parsed._replace(fragment='').geturl()

    @staticmethod
    def _extract_youtube_video_id(url: str | None) -> str | None:
        if not url:
            return None
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            path = parsed.path.strip('/')
            if 'youtu.be' in host and path:
                return path.split('/')[0]
            if 'youtube.com' in host:
                if path == 'watch':
                    return parse_qs(parsed.query).get('v', [''])[0] or None
                if path.startswith('embed/'):
                    return path.split('/', 1)[1] or None
            return None
        except Exception:
            return None

    @classmethod
    def _youtube_thumbnail(cls, url: str | None) -> str | None:
        video_id = cls._extract_youtube_video_id(url)
        if not video_id:
            return None
        return f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'

    @classmethod
    def _youtube_embed_url(cls, url: str | None) -> str | None:
        video_id = cls._extract_youtube_video_id(url)
        if not video_id:
            return None
        return f'https://www.youtube-nocookie.com/embed/{video_id}'


# module-level singleton
crawler_manager = CrawlerManager()
