import sqlite3
import os
import re
import hashlib
import json
from functools import lru_cache
from urllib.parse import parse_qs, urlparse, parse_qsl, urlencode

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'doodle.db')
SEARCH_CACHE_SIZE = 1024  # Suurendatud cache 256-lt 1024-le
MIN_SEARCH_CANDIDATES = 100
MAX_SEARCH_CANDIDATES = 600
SEARCH_CANDIDATE_MULTIPLIER = 15

TRACKING_QUERY_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'utm_id', 'utm_name', 'utm_reader', 'utm_referrer', 'utm_viz_id',
    'gclid', 'fbclid', 'mc_cid', 'mc_eid', 'igshid',
}

LANG_STOPWORDS = {
    'et': {'ja', 'on', 'ei', 'kui', 'see', 'ole', 'mis', 'kas', 'ning', 'või', 'aga', 'et', 'kõik'},
    'en': {'the', 'and', 'for', 'with', 'this', 'that', 'from', 'you', 'are', 'not', 'have', 'your', 'will'},
    'fi': {'ja', 'on', 'ei', 'se', 'että', 'tai', 'mutta', 'oli', 'ole', 'kun', 'myös', 'kanssa'},
    'de': {'und', 'die', 'der', 'das', 'ist', 'nicht', 'mit', 'für', 'ein', 'eine', 'den', 'von'},
    'ru': {'и', 'в', 'не', 'на', 'что', 'это', 'как', 'для', 'с', 'по', 'из', 'к'},
}

KEYWORD_STOPWORDS = {
    'the', 'and', 'for', 'with', 'this', 'that', 'from', 'your', 'have', 'will', 'you', 'not',
    'are', 'all', 'can', 'our', 'one', 'two', 'three', 'www', 'http', 'https',
    'ja', 'ning', 'aga', 'või', 'see', 'ole', 'kas', 'mis', 'kui',
}

MEDIA_HINTS = {
    'pictures': ['image', 'images', 'photo', 'photos', 'picture', 'pictures', 'gallery', 'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'wallpaper'],
    'videos': ['video', 'videos', 'watch', 'stream', 'trailer', 'clip', 'movie', 'episode', 'mp4', 'webm', 'mov'],
    'sound': ['audio', 'sound', 'music', 'song', 'songs', 'podcast', 'podcasts', 'mp3', 'wav', 'ogg', 'flac'],
    'youtube': ['youtube', 'youtu.be', 'yt'],
}


def _enable_sqlite_extensions(conn: sqlite3.Connection) -> None:
    if not hasattr(conn, 'enable_load_extension'):
        return

    try:
        conn.enable_load_extension(True)
    except (AttributeError, sqlite3.OperationalError):
        return

    try:
        conn.load_extension('fts5')  # Load full-text search module if available
    except (AttributeError, sqlite3.OperationalError, ImportError, ModuleNotFoundError):
        pass
    finally:
        try:
            conn.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError):
            pass


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    _enable_sqlite_extensions(conn)
    return conn


def get_page_count() -> int:
    conn = get_connection()
    try:
        row = conn.execute('SELECT COUNT(*) AS count FROM pages').fetchone()
        return int(row['count'] if row and row['count'] is not None else 0)
    finally:
        conn.close()


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            url     TEXT UNIQUE NOT NULL,
            title   TEXT,
            content TEXT,
            canonical_url TEXT,
            content_hash TEXT,
            domain TEXT,
            language TEXT,
            content_type TEXT,
            crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    _ensure_pages_columns(conn)
    
    # FTS5 virtual table for fast full-text search
    try:
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                id UNINDEXED,
                url,
                title,
                content,
                content=pages,
                content_rowid=id
            )
        ''')
    except Exception:
        pass  # FTS5 might not be available
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT,
            thumbnail_url TEXT,
            embed_url TEXT,
            source_url TEXT,
            duration TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(page_id, kind, url),
            FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            query TEXT PRIMARY KEY,
            search_count INTEGER NOT NULL DEFAULT 0,
            last_searched TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS recommended_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            title TEXT,
            url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(query, url)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keyword_stats (
            keyword TEXT PRIMARY KEY,
            score INTEGER NOT NULL DEFAULT 0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hidden_domains (
            domain TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Better indexes for common queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_kind ON media_assets(kind)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_page_id ON media_assets(page_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_url ON pages(url)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_canonical_url ON pages(canonical_url)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_content_hash ON pages(content_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_language ON pages(language)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_content_type ON pages(content_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_crawled_at ON pages(crawled_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_source_url ON media_assets(source_url)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_media_url ON media_assets(url)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_history_count ON search_history(search_count DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_recommended_query ON recommended_results(query)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_keyword_stats_score ON keyword_stats(score DESC)')
    # Combined index for common searches
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pages_title_content ON pages(title, content)')
    conn.commit()
    conn.close()


def _ensure_pages_columns(conn: sqlite3.Connection) -> None:
    existing = {row['name'] for row in conn.execute('PRAGMA table_info(pages)').fetchall()}
    required = {
        'canonical_url': 'TEXT',
        'content_hash': 'TEXT',
        'domain': 'TEXT',
        'language': 'TEXT',
        'content_type': 'TEXT',
    }
    for col, col_type in required.items():
        if col not in existing:
            conn.execute(f'ALTER TABLE pages ADD COLUMN {col} {col_type}')


def upsert_page(url: str, title: str, content: str) -> int | None:
    conn = get_connection()
    try:
        canonical_url = _canonicalize_url(url)
        content_hash = _content_hash(content)
        domain = _extract_domain(canonical_url)
        language = _detect_language(content, title)
        content_type = _infer_content_type(url, title, content)

        duplicate = conn.execute(
            '''
            SELECT id, url
            FROM pages
            WHERE (canonical_url = ? AND canonical_url IS NOT NULL)
               OR (content_hash = ? AND content_hash IS NOT NULL)
            ORDER BY id DESC
            LIMIT 1
            ''',
            (canonical_url, content_hash),
        ).fetchone()

        if duplicate and duplicate['url'] != url:
            conn.execute(
                '''
                UPDATE pages
                SET
                    title = ?,
                    content = ?,
                    canonical_url = ?,
                    content_hash = ?,
                    domain = ?,
                    language = ?,
                    content_type = ?,
                    crawled_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (title, content, canonical_url, content_hash, domain, language, content_type, duplicate['id']),
            )
            page_id = int(duplicate['id'])
        else:
            conn.execute(
                '''
                INSERT INTO pages (url, title, content, canonical_url, content_hash, domain, language, content_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    canonical_url = excluded.canonical_url,
                    content_hash = excluded.content_hash,
                    domain = excluded.domain,
                    language = excluded.language,
                    content_type = excluded.content_type,
                    crawled_at = CURRENT_TIMESTAMP
                ''',
                (url, title, content, canonical_url, content_hash, domain, language, content_type),
            )
            row = conn.execute('SELECT id FROM pages WHERE url = ?', (url,)).fetchone()
            page_id = row['id'] if row else None

        _update_keyword_stats_for_page(conn, title, content)
        
        # Update FTS5 index if available
        if page_id:
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO pages_fts(id, url, title, content) VALUES (?, ?, ?, ?)',
                    (page_id, url, title, content[:50000] if content else '')
                )
            except Exception:
                pass  # FTS5 not available
        
        conn.commit()
        _invalidate_search_cache()
        return page_id
    except Exception as exc:
        print(f'[DB] Error inserting {url}: {exc}')
        return None
    finally:
        conn.close()


def insert_page(url: str, title: str, content: str) -> bool:
    return upsert_page(url, title, content) is not None


def replace_media_assets(page_id: int, assets: list[dict]) -> int:
    conn = get_connection()
    try:
        conn.execute('DELETE FROM media_assets WHERE page_id = ?', (page_id,))
        rows = [
            (
                page_id,
                asset['kind'],
                asset['url'],
                asset.get('title'),
                asset.get('thumbnail_url'),
                asset.get('embed_url'),
                asset.get('source_url'),
                asset.get('duration'),
            )
            for asset in assets
            if asset.get('kind') and asset.get('url')
        ]
        if rows:
            conn.executemany(
                '''
                INSERT OR IGNORE INTO media_assets
                (page_id, kind, url, title, thumbnail_url, embed_url, source_url, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                rows,
            )
        conn.commit()
        _invalidate_search_cache()
        return len(rows)
    except Exception as exc:
        print(f'[DB] Error replacing media assets for page {page_id}: {exc}')
        return 0
    finally:
        conn.close()


def url_exists(url: str) -> bool:
    conn = get_connection()
    row = conn.execute('SELECT 1 FROM pages WHERE url = ?', (url,)).fetchone()
    conn.close()
    return row is not None


def get_page_urls(limit: int | None = None) -> list[str]:
    conn = get_connection()
    try:
        query = 'SELECT url FROM pages ORDER BY id ASC'
        params: tuple = ()
        if limit is not None:
            query += ' LIMIT ?'
            params = (limit,)
        rows = conn.execute(query, params).fetchall()
        return [row['url'] for row in rows]
    finally:
        conn.close()


def get_all_distinct_domains() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT domain FROM pages WHERE domain IS NOT NULL AND domain != '' ORDER BY domain"
        ).fetchall()
        return [row['domain'] for row in rows]
    finally:
        conn.close()


def get_page_count_for_domain(domain: str) -> int:
    domain = domain.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pages WHERE LOWER(COALESCE(domain, '')) = ?",
            (domain,),
        ).fetchone()
        return int(row['cnt'] if row else 0)
    finally:
        conn.close()


def get_urls_for_domain(domain: str) -> list[str]:
    domain = domain.strip().lower()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT url FROM pages WHERE LOWER(COALESCE(domain, '')) = ? ORDER BY id ASC",
            (domain,),
        ).fetchall()
        return [row['url'] for row in rows]
    finally:
        conn.close()


def delete_domain_pages(domain: str) -> int:
    domain = domain.strip().lower()
    if not domain:
        return 0
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM pages WHERE LOWER(COALESCE(domain, '')) = ?", (domain,)
        )
        conn.commit()
        _invalidate_search_cache()
        return cursor.rowcount
    finally:
        conn.close()


def get_hidden_domains() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute('SELECT domain FROM hidden_domains ORDER BY domain').fetchall()
        return [row['domain'] for row in rows]
    finally:
        conn.close()


def add_hidden_domain(domain: str) -> bool:
    domain = (_extract_domain(domain) or domain.strip().lower()).strip()
    if not domain:
        return False
    conn = get_connection()
    try:
        conn.execute('INSERT OR IGNORE INTO hidden_domains (domain) VALUES (?)', (domain,))
        conn.commit()
        return True
    finally:
        conn.close()


def remove_hidden_domain(domain: str) -> bool:
    domain = domain.strip().lower()
    if not domain:
        return False
    conn = get_connection()
    try:
        cursor = conn.execute('DELETE FROM hidden_domains WHERE domain = ?', (domain,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def record_search_query(query: str) -> None:
    normalized = _normalize_query_for_cache(query)
    if not normalized:
        return

    conn = get_connection()
    try:
        conn.execute(
            '''
            INSERT INTO search_history (query, search_count, last_searched)
            VALUES (?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(query) DO UPDATE SET
                search_count = search_count + 1,
                last_searched = CURRENT_TIMESTAMP
            ''',
            (normalized,),
        )

        terms, phrases, _ = _parse_search_query(normalized)
        for phrase in phrases:
            if len(phrase) < 3:
                continue
            conn.execute(
                '''
                INSERT INTO keyword_stats (keyword, score, last_seen)
                VALUES (?, 5, CURRENT_TIMESTAMP)
                ON CONFLICT(keyword) DO UPDATE SET
                    score = score + 5,
                    last_seen = CURRENT_TIMESTAMP
                ''',
                (phrase,),
            )
        for term in terms:
            if len(term) < 3 or term in KEYWORD_STOPWORDS:
                continue
            conn.execute(
                '''
                INSERT INTO keyword_stats (keyword, score, last_seen)
                VALUES (?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(keyword) DO UPDATE SET
                    score = score + 1,
                    last_seen = CURRENT_TIMESTAMP
                ''',
                (term,),
            )
        conn.commit()
    finally:
        conn.close()


def get_search_suggestions(prefix: str, limit: int = 8) -> list[dict]:
    normalized_prefix = _normalize_query_for_cache(prefix)
    if not normalized_prefix:
        return []

    safe_limit = max(1, min(int(limit), 20))
    conn = get_connection()
    try:
        query_rows = conn.execute(
            '''
            SELECT query, search_count, last_searched
            FROM search_history
            WHERE query LIKE ?
            ORDER BY search_count DESC, last_searched DESC
            LIMIT ?
            ''',
            (f'{normalized_prefix}%', safe_limit),
        ).fetchall()

        keyword_rows = conn.execute(
            '''
            SELECT keyword, score
            FROM keyword_stats
            WHERE keyword LIKE ?
            ORDER BY score DESC, last_seen DESC
            LIMIT ?
            ''',
            (f'{normalized_prefix}%', safe_limit),
        ).fetchall()

        recommended_rows = conn.execute(
            '''
            SELECT query, COUNT(*) AS ref_count, MAX(created_at) AS last_added
            FROM recommended_results
            WHERE query LIKE ?
            GROUP BY query
            ORDER BY ref_count DESC, last_added DESC
            LIMIT ?
            ''',
            (f'{normalized_prefix}%', safe_limit),
        ).fetchall()
    finally:
        conn.close()

    seen: set[str] = set()
    suggestions: list[dict] = []

    for row in query_rows:
        text = (row['query'] or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        suggestions.append({'text': text, 'source': 'history', 'weight': int(row['search_count'] or 0)})

    for row in recommended_rows:
        text = (row['query'] or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        suggestions.append({'text': text, 'source': 'recommended', 'weight': int(row['ref_count'] or 0)})

    for row in keyword_rows:
        text = (row['keyword'] or '').strip()
        if not text or text in seen:
            continue
        seen.add(text)
        suggestions.append({'text': text, 'source': 'popular', 'weight': int(row['score'] or 0)})

    source_order = {'history': 0, 'recommended': 1, 'popular': 2}
    suggestions.sort(key=lambda x: (source_order.get(x['source'], 99), -x['weight'], x['text']))
    return suggestions[:safe_limit]


def get_search_history(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    conn = get_connection()
    try:
        rows = conn.execute(
            '''
            SELECT query, search_count, last_searched
            FROM search_history
            ORDER BY last_searched DESC, search_count DESC, query ASC
            LIMIT ?
            ''',
            (safe_limit,),
        ).fetchall()
        return [
            {
                'query': (row['query'] or '').strip(),
                'search_count': int(row['search_count'] or 0),
                'last_searched': row['last_searched'],
            }
            for row in rows
            if (row['query'] or '').strip()
        ]
    finally:
        conn.close()


def add_search_history_entry(query: str, count: int = 1) -> bool:
    normalized = _normalize_query_for_cache(query)
    if not normalized:
        return False

    safe_count = max(1, int(count or 1))
    conn = get_connection()
    try:
        conn.execute(
            '''
            INSERT INTO search_history (query, search_count, last_searched)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(query) DO UPDATE SET
                search_count = search_count + excluded.search_count,
                last_searched = CURRENT_TIMESTAMP
            ''',
            (normalized, safe_count),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_search_history_entry(query: str) -> bool:
    normalized = _normalize_query_for_cache(query)
    if not normalized:
        return False

    conn = get_connection()
    try:
        result = conn.execute('DELETE FROM search_history WHERE query = ?', (normalized,))
        conn.commit()
        return (result.rowcount or 0) > 0
    finally:
        conn.close()


def clear_search_history() -> int:
    conn = get_connection()
    try:
        result = conn.execute('DELETE FROM search_history')
        conn.commit()
        return int(result.rowcount or 0)
    finally:
        conn.close()


def get_recommended_for_query(query: str, limit: int = 12, hidden_domains: list[str] | None = None) -> list[dict]:
    normalized = _normalize_query_for_cache(query)
    if not normalized:
        return []

    safe_limit = max(1, min(int(limit), 50))
    hidden = set(d.strip().lower() for d in (hidden_domains or []) if d)
    conn = get_connection()
    try:
        rows = conn.execute(
            '''
            SELECT id, query, title, url, created_at
            FROM recommended_results
            WHERE query = ?
            ORDER BY id DESC
            LIMIT ?
            ''',
            (normalized, safe_limit),
        ).fetchall()

        return [
            {
                'id': int(row['id']),
                'query': (row['query'] or '').strip(),
                'title': (row['title'] or '').strip(),
                'url': (row['url'] or '').strip(),
                'created_at': row['created_at'],
            }
            for row in rows
            if (row['url'] or '').strip()
            and _extract_domain((row['url'] or '').strip()).lower() not in hidden
        ]
    finally:
        conn.close()


def get_recommended_entries(limit: int = 300) -> list[dict]:
    safe_limit = max(1, min(int(limit), 1000))
    conn = get_connection()
    try:
        rows = conn.execute(
            '''
            SELECT id, query, title, url, created_at
            FROM recommended_results
            ORDER BY query ASC, id DESC
            LIMIT ?
            ''',
            (safe_limit,),
        ).fetchall()

        return [
            {
                'id': int(row['id']),
                'query': (row['query'] or '').strip(),
                'title': (row['title'] or '').strip(),
                'url': (row['url'] or '').strip(),
                'created_at': row['created_at'],
            }
            for row in rows
            if (row['query'] or '').strip() and (row['url'] or '').strip()
        ]
    finally:
        conn.close()


def add_recommended_entry(query: str, url: str, title: str = '') -> bool:
    normalized_query = _normalize_query_for_cache(query)
    normalized_url = (url or '').strip()
    normalized_title = (title or '').strip()
    if not normalized_query or not normalized_url:
        return False

    conn = get_connection()
    try:
        conn.execute(
            '''
            INSERT INTO recommended_results (query, title, url, created_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(query, url) DO UPDATE SET
                title = excluded.title,
                created_at = CURRENT_TIMESTAMP
            ''',
            (normalized_query, normalized_title, normalized_url),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_recommended_entry(entry_id: int) -> bool:
    conn = get_connection()
    try:
        result = conn.execute('DELETE FROM recommended_results WHERE id = ?', (int(entry_id),))
        conn.commit()
        return (result.rowcount or 0) > 0
    finally:
        conn.close()


def clear_recommended_entries() -> int:
    conn = get_connection()
    try:
        result = conn.execute('DELETE FROM recommended_results')
        conn.commit()
        return int(result.rowcount or 0)
    finally:
        conn.close()


def search_pages(
    query: str,
    limit: int = 20,
    sort: str = 'relevance',
    filters: dict | None = None,
) -> list[dict]:
    page_data = search_pages_page(query, page=1, page_size=limit, sort=sort, filters=filters)
    return [dict(item) for item in page_data['results']]


def search_pages_page(
    query: str,
    page: int = 1,
    page_size: int = 20,
    sort: str = 'relevance',
    filters: dict | None = None,
) -> dict:
    normalized_query = _normalize_query_for_cache(query)
    safe_page_size = max(1, int(page_size))
    safe_page = max(1, int(page))
    sort_key = _normalize_sort(sort)
    normalized_filters = _normalize_filters(filters or {})
    filters_key = json.dumps(normalized_filters, sort_keys=True)
    results = _search_pages_cached(normalized_query, sort_key, filters_key)
    total = len(results)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size) if total else 0
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size

    return {
        'results': [dict(item) for item in results[start:end]],
        'query': query,
        'page': safe_page,
        'page_size': safe_page_size,
        'total': total,
        'total_pages': total_pages,
        'has_more': safe_page < total_pages,
    }


@lru_cache(maxsize=SEARCH_CACHE_SIZE)
def _search_pages_cached(normalized_query: str, sort: str, filters_key: str) -> tuple[dict, ...]:
    filters = json.loads(filters_key) if filters_key else {}
    query_terms, query_phrases, query_excluded_terms = _parse_search_query(normalized_query)
    excluded_terms = _dedupe_preserve_order(query_excluded_terms + _tokenize_terms(filters.get('exclude', '')))

    if not query_terms and not query_phrases:
        return tuple()

    conn = get_connection()
    terms = query_phrases + query_terms
    where_sql, params = _build_multi_column_like_filter(['url', 'title', 'content'], terms, require_all=True)
    filter_sql, filter_params = _build_search_filter_sql(filters, normalized_query, excluded_terms)
    select_columns = (
        'id, url, title, content, canonical_url, content_hash, domain, language, content_type, crawled_at'
    )

    rows = conn.execute(
        f'SELECT {select_columns} FROM pages WHERE 1=1 {where_sql} {filter_sql} ORDER BY id DESC LIMIT ?',
        (*params, *filter_params, MAX_SEARCH_CANDIDATES),
    ).fetchall()

    if not rows and len(terms) > 1:
        relaxed_where_sql, relaxed_params = _build_multi_column_like_filter(['url', 'title', 'content'], terms, require_all=False)
        rows = conn.execute(
            f'SELECT {select_columns} FROM pages WHERE 1=1 {relaxed_where_sql} {filter_sql} ORDER BY id DESC LIMIT ?',
            (*relaxed_params, *filter_params, MAX_SEARCH_CANDIDATES),
        ).fetchall()

    conn.close()

    results = []
    for row in rows:
        url = row['url'] or ''
        title = row['title'] or url
        content = row['content'] or ''

        score = _search_score(
            url=url,
            title=title,
            content=content,
            query_terms=query_terms,
            query_phrases=query_phrases,
        )
        if score <= 0:
            continue

        match_count = _match_count(url, title, content, query_terms, query_phrases)
        if match_count <= 0:
            continue

        if excluded_terms and any(_count_word_matches((f'{url} {title} {content}').lower(), term) > 0 for term in excluded_terms):
            continue

        results.append({
            'id': row['id'],
            'url': url,
            'title': title,
            'snippet': _make_snippet(content, query_phrases + query_terms, length=260),
            'domain': row['domain'] or _extract_domain(url),
            'language': row['language'] or 'unknown',
            'content_type': row['content_type'] or 'general',
            'crawled_at': row['crawled_at'],
            'match_count': match_count,
            'canonical_url': row['canonical_url'] or _canonicalize_url(url),
            'content_hash': row['content_hash'] or _content_hash(content),
            'score': score,
        })

    _sort_results(results, sort)
    return tuple(_dedupe_ranked_results(results))


def search_media_pages(query: str, kind: str, limit: int = 20, hidden_domains: list[str] | None = None) -> list[dict]:
    normalized_query = _normalize_query_for_cache(query)
    safe_limit = max(1, int(limit))
    safe_kind = (kind or '').lower()
    hidden = set(d.strip().lower() for d in (hidden_domains or []) if d)
    results = [dict(item) for item in _search_media_pages_cached(normalized_query, safe_kind, safe_limit)]
    if hidden:
        results = [r for r in results if _extract_domain(r.get('url') or '').lower() not in hidden]
    return results


@lru_cache(maxsize=SEARCH_CACHE_SIZE)
def _search_media_pages_cached(normalized_query: str, kind: str, limit: int) -> tuple[dict, ...]:
    kind = (kind or '').lower()
    query_terms, query_phrases, _ = _parse_search_query(normalized_query)
    if (not query_terms and not query_phrases) or kind not in MEDIA_HINTS:
        return tuple()

    conn = get_connection()
    candidate_limit = _candidate_limit(limit)
    terms = query_phrases + query_terms
    where_sql, params = _build_multi_column_like_filter(['url', 'title', 'content'], terms, require_all=True)

    rows = conn.execute(
        f'SELECT id, url, title, content FROM pages WHERE 1=1 {where_sql} ORDER BY id DESC LIMIT ?',
        (*params, candidate_limit),
    ).fetchall()

    if not rows and len(terms) > 1:
        relaxed_where_sql, relaxed_params = _build_multi_column_like_filter(['url', 'title', 'content'], terms, require_all=False)
        rows = conn.execute(
            f'SELECT id, url, title, content FROM pages WHERE 1=1 {relaxed_where_sql} ORDER BY id DESC LIMIT ?',
            (*relaxed_params, candidate_limit),
        ).fetchall()

    conn.close()

    results = []
    for row in rows:
        url = row['url'] or ''
        title = row['title'] or url
        content = row['content'] or ''

        query_score = _search_score(url, title, content, query_terms, query_phrases)
        if query_score <= 0:
            continue

        media_score = _media_score(kind, url, title, content)
        if media_score <= 0 and kind == 'youtube':
            continue

        total_score = query_score * 4 + media_score
        results.append({
            'id': row['id'],
            'url': url,
            'title': title,
            'snippet': _make_snippet(content, query_phrases + query_terms),
            'score': total_score,
            'kind': kind,
            'youtube_id': _extract_youtube_video_id(url) if kind == 'youtube' else None,
            'media_match': media_score > 0,
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    if results:
        return tuple(results[:limit])

    # Fallback: if nothing looks media-specific, still show the best crawled matches.
    fallback = search_pages(normalized_query, limit=limit)
    for item in fallback:
        item['kind'] = kind
        item['youtube_id'] = _extract_youtube_video_id(item['url']) if kind == 'youtube' else None
        item['media_match'] = False
    return tuple(fallback)


def search_media_assets(query: str, kind: str, limit: int = 24, hidden_domains: list[str] | None = None) -> list[dict]:
    normalized_query = _normalize_query_for_cache(query)
    safe_limit = max(1, int(limit))
    safe_kind = (kind or '').lower()
    hidden = set(d.strip().lower() for d in (hidden_domains or []) if d)
    results = [dict(item) for item in _search_media_assets_cached(normalized_query, safe_kind, safe_limit)]
    if hidden:
        results = [
            r for r in results
            if _extract_domain(r.get('page_url') or r.get('source_url') or r.get('url') or '').lower() not in hidden
        ]
    return results


@lru_cache(maxsize=SEARCH_CACHE_SIZE)
def _search_media_assets_cached(normalized_query: str, kind: str, limit: int) -> tuple[dict, ...]:
    kind = (kind or '').lower()
    query_terms, query_phrases, _ = _parse_search_query(normalized_query)
    if (not query_terms and not query_phrases) or kind not in MEDIA_HINTS:
        return tuple()

    conn = get_connection()
    candidate_limit = _candidate_limit(limit)
    terms = query_phrases + query_terms

    # Primary: search only in media asset columns + page title/url (NOT page content)
    # This avoids false positives like Lewis Carroll pages that mention "dodo" in text
    media_cols = ['m.url', 'm.title', 'm.source_url', 'p.url', 'p.title']
    where_sql, params = _build_multi_column_like_filter(media_cols, terms, require_all=True)

    rows = conn.execute(
        f'''
        SELECT
            m.id,
            m.kind,
            m.url,
            m.title,
            m.thumbnail_url,
            m.embed_url,
            m.source_url,
            m.duration,
            p.url AS page_url,
            p.title AS page_title,
            p.content AS page_content
        FROM media_assets m
        JOIN pages p ON p.id = m.page_id
        WHERE m.kind = ? {where_sql}
        ORDER BY m.id DESC
        LIMIT ?
        ''',
        (kind, *params, candidate_limit),
    ).fetchall()

    if not rows and len(terms) > 1:
        relaxed_where_sql, relaxed_params = _build_multi_column_like_filter(
            media_cols, terms, require_all=False,
        )
        rows = conn.execute(
            f'''
            SELECT
                m.id,
                m.kind,
                m.url,
                m.title,
                m.thumbnail_url,
                m.embed_url,
                m.source_url,
                m.duration,
                p.url AS page_url,
                p.title AS page_title,
                p.content AS page_content
            FROM media_assets m
            JOIN pages p ON p.id = m.page_id
            WHERE m.kind = ? {relaxed_where_sql}
            ORDER BY m.id DESC
            LIMIT ?
            ''',
            (kind, *relaxed_params, candidate_limit),
        ).fetchall()

    # Last resort: if nothing found in media columns, also check page content
    if not rows:
        content_cols = ['m.url', 'm.title', 'm.source_url', 'p.url', 'p.title', 'p.content']
        content_where_sql, content_params = _build_multi_column_like_filter(
            content_cols, terms, require_all=True,
        )
        rows = conn.execute(
            f'''
            SELECT
                m.id,
                m.kind,
                m.url,
                m.title,
                m.thumbnail_url,
                m.embed_url,
                m.source_url,
                m.duration,
                p.url AS page_url,
                p.title AS page_title,
                p.content AS page_content
            FROM media_assets m
            JOIN pages p ON p.id = m.page_id
            WHERE m.kind = ? {content_where_sql}
            ORDER BY m.id DESC
            LIMIT ?
            ''',
            (kind, *content_params, candidate_limit),
        ).fetchall()

    conn.close()

    results = []
    for row in rows:
        title = row['title'] or row['page_title'] or row['url']
        
        # Prioritize media asset title/URL over page content for relevance scoring
        media_searchable = ' '.join([
            row['url'] or '',
            title or '',
        ])
        
        # Separately check if query terms match media asset (not just page content)
        media_query_score = _search_score(
            url=row['url'] or '',
            title=title,
            content=media_searchable,
            query_terms=query_terms,
            query_phrases=query_phrases,
        )
        
        # Media score bonus for matching hints (images, videos, etc.)
        media_hint_score = _media_score(kind, row['url'] or '', title, '')
        
        # Page-level relevance as secondary signal (very low weight)
        page_query_score = _search_score(
            url=row['page_url'] or '',
            title=row['page_title'] or '',
            content=row['page_content'] or '',
            query_terms=query_terms,
            query_phrases=query_phrases,
        )
        
        # Combine: media asset match is primary, page context is secondary
        if media_query_score > 0:
            combined_score = media_query_score * 3 + media_hint_score + (page_query_score // 4)
        else:
            # Page content matched but media asset itself didn't — skip entirely
            # This prevents false positives like random images from pages that
            # just happen to mention the search term in their text
            continue
        
        if combined_score <= 0:
            continue

        if kind == 'youtube' and not (row['embed_url'] or _extract_youtube_video_id(row['url'] or '')):
            continue

        results.append({
            'id': row['id'],
            'kind': row['kind'],
            'url': row['url'],
            'title': title,
            'thumbnail_url': row['thumbnail_url'],
            'embed_url': row['embed_url'],
            'source_url': row['source_url'] or row['page_url'],
            'duration': row['duration'],
            'page_url': row['page_url'],
            'snippet': _make_snippet(row['page_content'] or '', query_phrases + query_terms),
            'youtube_id': _extract_youtube_video_id(row['url'] or '') or _extract_youtube_video_id(row['embed_url'] or ''),
            'media_match': True,
            'score': combined_score,
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    if results:
        return tuple(results[:limit])

    # For sound tab, avoid falling back to generic web pages that are not directly playable audio.
    if kind == 'sound':
        return tuple()

    return tuple(search_media_pages(normalized_query, kind, limit=limit))


def _normalize_query_for_cache(query: str) -> str:
    return ' '.join((query or '').strip().lower().split())


def _normalize_sort(sort: str) -> str:
    allowed = {'relevance', 'newest', 'oldest', 'most_matches'}
    candidate = (sort or '').strip().lower()
    return candidate if candidate in allowed else 'relevance'


def _normalize_filters(filters: dict) -> dict:
    exact_raw = filters.get('exact_phrase_only')
    if isinstance(exact_raw, str):
        exact_phrase_only = exact_raw.strip().lower() in {'1', 'true', 'yes', 'on'}
    else:
        exact_phrase_only = bool(exact_raw)

    out = {
        'from_date': (filters.get('from_date') or '').strip(),
        'to_date': (filters.get('to_date') or '').strip(),
        'domain': extract_domain(filters.get('domain') or ''),
        'language': (filters.get('language') or '').strip().lower(),
        'content_type': (filters.get('content_type') or '').strip().lower(),
        'exact_phrase_only': exact_phrase_only,
        'exclude': (filters.get('exclude') or '').strip().lower(),
        'hidden_domains': tuple(sorted(
            str(d).strip().lower() for d in (filters.get('hidden_domains') or []) if d
        )),
    }
    if out['language'] in {'', 'all'}:
        out['language'] = ''
    if out['content_type'] in {'', 'all'}:
        out['content_type'] = ''
    return out


def _build_search_filter_sql(filters: dict, normalized_query: str, excluded_terms: list[str]) -> tuple[str, list[str]]:
    sql_parts: list[str] = []
    params: list[str] = []

    if filters.get('from_date'):
        sql_parts.append('date(crawled_at) >= date(?)')
        params.append(filters['from_date'])

    if filters.get('to_date'):
        sql_parts.append('date(crawled_at) <= date(?)')
        params.append(filters['to_date'])

    if filters.get('domain'):
        sql_parts.append('LOWER(COALESCE(domain, "")) = ?')
        params.append(filters['domain'])

    if filters.get('language'):
        sql_parts.append('LOWER(COALESCE(language, "")) = ?')
        params.append(filters['language'])

    if filters.get('content_type'):
        sql_parts.append('LOWER(COALESCE(content_type, "")) = ?')
        params.append(filters['content_type'])

    for hidden in (filters.get('hidden_domains') or []):
        if hidden:
            sql_parts.append('LOWER(COALESCE(domain, "")) != ?')
            params.append(hidden)

    if filters.get('exact_phrase_only') and normalized_query:
        sql_parts.append('(LOWER(COALESCE(title, "") || " " || COALESCE(content, "")) LIKE ?)')
        params.append(f'%{normalized_query}%')

    for term in excluded_terms:
        if term:
            sql_parts.append('LOWER(COALESCE(title, "") || " " || COALESCE(content, "") || " " || COALESCE(url, "")) NOT LIKE ?')
            params.append(f'%{term}%')

    if not sql_parts:
        return '', []

    return ' AND ' + ' AND '.join(sql_parts), params


def _sort_results(results: list[dict], sort: str) -> None:
    sort_key = _normalize_sort(sort)
    if sort_key == 'newest':
        results.sort(key=lambda x: ((x.get('crawled_at') or ''), x.get('score', 0)), reverse=True)
    elif sort_key == 'oldest':
        results.sort(key=lambda x: ((x.get('crawled_at') or ''), -x.get('score', 0)))
    elif sort_key == 'most_matches':
        results.sort(key=lambda x: (x.get('match_count', 0), x.get('score', 0), x.get('crawled_at') or ''), reverse=True)
    else:
        results.sort(key=lambda x: (x.get('score', 0), x.get('match_count', 0), x.get('crawled_at') or ''), reverse=True)


def _dedupe_ranked_results(results: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()

    for item in results:
        canonical = (item.get('canonical_url') or '').strip()
        content_hash = (item.get('content_hash') or '').strip()
        key = canonical or (f'hash:{content_hash}' if content_hash else f'url:{item.get("url") or ""}')
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def _canonicalize_url(url: str) -> str:
    try:
        parsed = urlparse((url or '').strip())
        scheme = (parsed.scheme or 'https').lower()
        host = (parsed.hostname or '').lower()
        if not host:
            return (url or '').strip().lower()

        port = parsed.port
        if port and not ((scheme == 'http' and port == 80) or (scheme == 'https' and port == 443)):
            netloc = f'{host}:{port}'
        else:
            netloc = host

        path = parsed.path or '/'
        if path != '/':
            path = path.rstrip('/')

        filtered_query = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in TRACKING_QUERY_PARAMS
        ]
        filtered_query.sort()
        query = urlencode(filtered_query)

        canonical = f'{scheme}://{netloc}{path}'
        if query:
            canonical += f'?{query}'
        return canonical
    except Exception:
        return (url or '').strip().lower()


def _extract_domain(url: str) -> str:
    try:
        return (urlparse((url or '').strip()).hostname or '').lower()
    except Exception:
        return ''


def extract_domain(url: str) -> str:
    """Accept a full URL or a bare domain entered into a filter."""
    value = (url or '').strip()
    if not value:
        return ''
    if '://' not in value and not value.startswith('//'):
        value = f'//{value}'
    return _extract_domain(value)


def _content_hash(content: str) -> str:
    normalized = ' '.join((content or '').lower().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest() if normalized else ''


def _detect_language(content: str, title: str = '') -> str:
    text = f'{title or ""} {content or ""}'.lower()
    words = _tokenize_terms(text)
    if not words:
        return 'unknown'

    best_lang = 'unknown'
    best_score = 0
    for lang, stopwords in LANG_STOPWORDS.items():
        score = sum(1 for w in words if w in stopwords)
        if score > best_score:
            best_score = score
            best_lang = lang

    return best_lang if best_score > 1 else 'unknown'


def _infer_content_type(url: str, title: str, content: str) -> str:
    source = f'{(url or "").lower()} {(title or "").lower()} {(content or "")[:1200].lower()}'

    rules = [
        ('documentation', ['docs', 'documentation', 'api reference', 'guide', 'manual']),
        ('forum', ['forum', 'thread', 'discussion', 'comment', 'community']),
        ('blog', ['blog', 'post', 'author', 'published']),
        ('news', ['news', 'breaking', 'report', 'headline']),
        ('ecommerce', ['cart', 'checkout', 'price', 'buy now', 'product']),
        ('media', ['video', 'podcast', 'gallery', 'watch', 'listen']),
        ('article', ['article', 'read more', 'editorial']),
    ]

    for content_type, hints in rules:
        if any(hint in source for hint in hints):
            return content_type
    return 'general'


def _update_keyword_stats_for_page(conn: sqlite3.Connection, title: str, content: str) -> None:
    text = f'{title or ""} {(content or "")[:4000]}'
    terms = [t for t in _tokenize_terms(text) if len(t) >= 3 and t not in KEYWORD_STOPWORDS]
    if not terms:
        return

    frequencies: dict[str, int] = {}
    for term in terms:
        frequencies[term] = frequencies.get(term, 0) + 1

    top_terms = sorted(frequencies.items(), key=lambda item: item[1], reverse=True)[:25]
    for term, freq in top_terms:
        conn.execute(
            '''
            INSERT INTO keyword_stats (keyword, score, last_seen)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(keyword) DO UPDATE SET
                score = score + excluded.score,
                last_seen = CURRENT_TIMESTAMP
            ''',
            (term, max(1, min(freq, 5))),
        )


def _candidate_limit(limit: int) -> int:
    # Optimized: retrieve fewer candidates per query for faster response
    # Still maintains quality with good filtering
    return max(MIN_SEARCH_CANDIDATES, min(MAX_SEARCH_CANDIDATES, limit * SEARCH_CANDIDATE_MULTIPLIER))
    

def _build_multi_column_like_filter(columns: list[str], terms: list[str], require_all: bool = True) -> tuple[str, list[str]]:
    cleaned_terms = [t for t in terms if t]
    if not cleaned_terms:
        return '', []

    clauses: list[str] = []
    params: list[str] = []
    for term in cleaned_terms:
        # Optimized: prefer prefix search over substring search for better relevance
        part_clauses = [
            f'(LOWER(COALESCE({col}, "")) LIKE ? OR LOWER(COALESCE({col}, "")) LIKE ?)'
            for col in columns
        ]
        clauses.append('(' + ' OR '.join(part_clauses) + ')')
        # First pattern: word boundary start, second pattern: substring
        for col in columns:
            params.extend([f'{term}%', f'%{term}%'])

    joiner = ' AND ' if require_all else ' OR '
    return ' AND (' + joiner.join(clauses) + ')', params


def _invalidate_search_cache() -> None:
    _search_pages_cached.cache_clear()
    _search_media_pages_cached.cache_clear()
    _search_media_assets_cached.cache_clear()


def _keyword_score(url: str, title: str, content: str, keywords: list[str]) -> int:
    return _search_score(url, title, content, keywords, [])


def _search_score(url: str, title: str, content: str, query_terms: list[str], query_phrases: list[str]) -> int:
    url_lc = (url or '').lower()
    title_lc = (title or '').lower()
    content_lc = (content or '').lower()

    total = 0

    # Phrase matches are strong relevance signals.
    for phrase in query_phrases:
        if not phrase:
            continue
        # Even stronger boost for exact phrase in title
        if phrase in title_lc:
            total += 100
        total += title_lc.count(phrase) * 50
        total += url_lc.count(phrase) * 25
        total += content_lc.count(phrase) * 12

    # Token scoring prefers exact word boundaries
    for term in query_terms:
        if not term:
            continue
        title_matches = _count_word_matches(title_lc, term)
        url_matches = _count_word_matches(url_lc, term)
        content_matches = _count_word_matches(content_lc, term)
        
        # Boost for title matches significantly
        total += title_matches * 25
        total += url_matches * 12
        total += content_matches * 5

    # Exact title match is highest priority
    raw_query = ' '.join(query_phrases + query_terms).strip()
    if raw_query:
        compact = ' '.join(raw_query.split())
        if title_lc == compact:
            total += 150
        elif compact in title_lc:
            total += 60
        elif title_lc.startswith(compact):
            total += 45

    # Bonus for multi-term matches
    if query_terms:
        matched_terms = 0
        for term in query_terms:
            if _count_word_matches(title_lc, term) > 0 or _count_word_matches(content_lc, term) > 0:
                matched_terms += 1
        if matched_terms == len(query_terms):
            total += 40
        else:
            total += matched_terms * 8

    return total


def _parse_search_query(query: str) -> tuple[list[str], list[str], list[str]]:
    lowered = (query or '').strip().lower()
    if not lowered:
        return [], [], []

    excluded = _dedupe_preserve_order(re.findall(r'(?<!\S)-([a-z0-9]{2,})', lowered))

    # Support quoted phrase searches, e.g. "machine learning" basics
    raw_phrases = [m.group(1).strip() for m in re.finditer(r'"([^"]+)"', lowered)]
    lowered_without_phrases = re.sub(r'"[^"]+"', ' ', lowered)
    lowered_without_phrases = re.sub(r'(?<!\S)-[a-z0-9]{2,}', ' ', lowered_without_phrases)

    phrase_terms: list[str] = []
    for phrase in raw_phrases:
        phrase_terms.extend(_tokenize_terms(phrase))

    base_terms = _tokenize_terms(lowered_without_phrases)
    unique_terms = _dedupe_preserve_order(phrase_terms + base_terms)
    unique_phrases = _dedupe_preserve_order([' '.join(_tokenize_terms(p)) for p in raw_phrases if p.strip()])
    return unique_terms, unique_phrases, excluded


def _tokenize_terms(text: str) -> list[str]:
    return [part for part in re.findall(r'[\w]{2,}', text.lower())]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _count_word_matches(text: str, term: str) -> int:
    if not text or not term:
        return 0
    pattern = rf'(?<![\w]){re.escape(term)}(?![\w])'
    return len(re.findall(pattern, text))


def _match_count(url: str, title: str, content: str, query_terms: list[str], query_phrases: list[str]) -> int:
    haystack = f'{url} {title} {content}'.lower()
    total = 0
    for phrase in query_phrases:
        if phrase:
            total += haystack.count(phrase)
    for term in query_terms:
        if term:
            total += _count_word_matches(haystack, term)
    return total


def _media_score(kind: str, url: str, title: str, content: str) -> int:
    """Score media relevance. URL/title matches are weighted heavily.
    Uses word boundaries to avoid false positives like 'dodo' in random text.
    """
    url_lc = url.lower()
    title_lc = title.lower()
    content_lc = content.lower()
    hints = MEDIA_HINTS.get(kind, [])
    score = 0

    # Check hints with word boundaries (not just substring)
    for hint in hints:
        # Title/URL matches are much stronger indicators
        title_count = _count_word_matches(title_lc, hint)
        url_count = _count_word_matches(url_lc, hint)
        # Content matches are weaker (false positives like 'dodo' in text)
        content_count = _count_word_matches(content_lc, hint)
        
        score += title_count * 15  # Title match is very strong
        score += url_count * 10    # URL match is strong
        score += content_count * 2 # Content match is very weak

    if kind == 'youtube':
        if _extract_youtube_video_id(url):
            score += 30  # Strong signal
        if 'youtube.com' in url_lc or 'youtu.be' in url_lc:
            score += 20

    return score


def _extract_youtube_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.strip('/')

        if 'youtu.be' in host and path:
            return path.split('/')[0]

        if 'youtube.com' in host:
            if path == 'watch':
                video_id = parse_qs(parsed.query).get('v', [''])[0]
                return video_id or None
            if path.startswith('embed/'):
                return path.split('/', 1)[1] or None

        return None
    except Exception:
        return None


def _make_snippet(content: str, keywords: list[str], length: int = 220) -> str:
    if not content:
        return ''

    text = ' '.join(content.split())
    content_lc = text.lower()
    terms = [k.lower() for k in keywords if k]
    if not terms:
        raw = text[:length]
        return raw + ('…' if len(text) > length else '')

    positions: list[int] = []
    for kw in terms:
        if ' ' in kw:
            start = 0
            while True:
                pos = content_lc.find(kw, start)
                if pos == -1:
                    break
                positions.append(pos)
                start = pos + len(kw)
        else:
            for match in re.finditer(rf'(?<![\w]){re.escape(kw)}(?![\w])', content_lc):
                positions.append(match.start())

    if not positions:
        raw = text[:length]
        return raw + ('…' if len(text) > length else '')

    best_start = 0
    best_score = -1
    for pos in positions:
        start = max(0, pos - (length // 3))
        end = min(len(content_lc), start + length)
        score = sum(1 for p in positions if start <= p < end)
        if score > best_score:
            best_score = score
            best_start = start

    sentence_start = content.rfind('.', 0, best_start)
    if sentence_start != -1 and best_start - sentence_start < 80:
        best_start = sentence_start + 1

    start = max(0, best_start)
    end = min(len(text), start + length)
    snippet = text[start:end].strip()

    if start > 0:
        snippet = '…' + snippet
    if end < len(text):
        snippet = snippet + '…'

    return snippet
