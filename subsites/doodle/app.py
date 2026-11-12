from dodo.web import WebApplication, request, jsonify, send_from_directory, Response, shared_admin, render_template, shared_signin, shared_signout
import requests
import re
import json
import os
import time
from functools import wraps
from werkzeug.exceptions import BadRequest

from . import database
from .crawler import crawler_manager

FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')

app = WebApplication(__name__)

database.init_db()


@app.errorhandler(BadRequest)
def invalid_request(error):
    return jsonify({'success': False, 'message': error.description}), 400


def _json_body() -> dict:
    if not request.get_data():
        return {}
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest('Expected a JSON object')
    return data


def _text_field(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return ''
    if not isinstance(value, str):
        raise BadRequest(f'{key} must be text')
    return value.strip()


# ------------------------------------------------------------------ Settings & admin auth

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'settings.json')
_reddit_token_cache = {
    'token': None,
    'expires_at': 0.0,
}


def _load_settings() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_settings(data: dict) -> None:
    with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def _init_settings() -> None:
    settings = _load_settings()
    changed = False
    if not settings.get('ai_model'):
        settings['ai_model'] = 'phi'
        changed = True
    if 'youtube_api_key' not in settings:
        settings['youtube_api_key'] = ''
        changed = True
    if 'reddit_client_id' not in settings:
        settings['reddit_client_id'] = ''
        changed = True
    if 'reddit_client_secret' not in settings:
        settings['reddit_client_secret'] = ''
        changed = True
    if not settings.get('reddit_user_agent'):
        settings['reddit_user_agent'] = 'doodle-social-search/1.0'
        changed = True
    if not settings.get('wikipedia_user_agent'):
        settings['wikipedia_user_agent'] = 'doodle-social-search/1.0 (Wikipedia client)'
        changed = True
    if 'curseforge_api_key' not in settings:
        settings['curseforge_api_key'] = ''
        changed = True
    if 'curseforge_game_id' not in settings:
        settings['curseforge_game_id'] = 432
        changed = True
    if changed:
        _save_settings(settings)


_init_settings()


def _require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not _is_admin_request():
            return jsonify({'success': False, 'message': 'Administrator access required'}), 403
        return f(*args, **kwargs)
    return wrapper


def _is_admin_request():
    return request.admin_mount and shared_admin()


def _setting_or_env(setting_key: str, env_key: str | None = None, default: str = '') -> str:
    settings = _load_settings()
    raw = settings.get(setting_key)
    if raw is not None and str(raw).strip():
        return str(raw).strip()

    env_name = env_key or setting_key.upper()
    env_value = os.getenv(env_name, '')
    if env_value.strip():
        return env_value.strip()
    return default


def _strip_html(raw: str) -> str:
    return re.sub(r'<[^>]+>', '', raw or '')


def _search_youtube_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    api_key = _setting_or_env('youtube_api_key', 'YOUTUBE_API_KEY')
    if not api_key:
        # YouTube is optional: if key is not configured, keep this source disabled silently.
        return [], None

    params = {
        'part': 'snippet',
        'type': 'video',
        'maxResults': max(1, min(limit, 15)),
        'q': query,
        'key': api_key,
    }

    try:
        res = requests.get('https://www.googleapis.com/youtube/v3/search', params=params, timeout=12)
    except requests.RequestException:
        return [], 'YouTube API is unreachable right now.'

    if not res.ok:
        return [], f'YouTube API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'YouTube API returned invalid JSON.'

    items = payload.get('items') or []
    out = []
    for item in items:
        vid = ((item.get('id') or {}).get('videoId') or '').strip()
        sn = item.get('snippet') or {}
        title = (sn.get('title') or '').strip()
        channel = (sn.get('channelTitle') or '').strip()
        desc = (sn.get('description') or '').strip()
        published = (sn.get('publishedAt') or '').strip()
        thumbs = sn.get('thumbnails') or {}
        thumb = ((thumbs.get('medium') or {}).get('url') or (thumbs.get('default') or {}).get('url') or '').strip()
        if not vid:
            continue
        out.append({
            'title': title,
            'url': f'https://www.youtube.com/watch?v={vid}',
            'channel': channel,
            'description': desc,
            'published_at': published,
            'thumbnail_url': thumb,
        })
    return out, None


def _get_reddit_app_token() -> tuple[str | None, str | None]:
    client_id = _setting_or_env('reddit_client_id', 'REDDIT_CLIENT_ID')
    client_secret = _setting_or_env('reddit_client_secret', 'REDDIT_CLIENT_SECRET')
    user_agent = _setting_or_env('reddit_user_agent', 'REDDIT_USER_AGENT', 'doodle-social-search/1.0')

    if not client_id:
        return None, None

    now = time.time()
    cached_token = _reddit_token_cache.get('token')
    cached_expiry = float(_reddit_token_cache.get('expires_at') or 0)
    if cached_token and now < (cached_expiry - 60):
        return str(cached_token), None

    try:
        res = requests.post(
            'https://www.reddit.com/api/v1/access_token',
            data={'grant_type': 'client_credentials'},
            auth=(client_id, client_secret),
            headers={'User-Agent': user_agent},
            timeout=12,
        )
    except requests.RequestException:
        return None, 'Reddit OAuth token request failed.'

    if not res.ok:
        return None, f'Reddit OAuth token request failed with status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return None, 'Reddit OAuth token response was invalid.'

    token = (payload.get('access_token') or '').strip()
    expires_in = int(payload.get('expires_in') or 0)
    if not token:
        return None, 'Reddit OAuth token missing in response.'

    _reddit_token_cache['token'] = token
    _reddit_token_cache['expires_at'] = now + max(0, expires_in)
    return token, None


def _search_reddit_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    user_agent = _setting_or_env('reddit_user_agent', 'REDDIT_USER_AGENT', 'doodle-social-search/1.0')
    token, token_error = _get_reddit_app_token()

    headers = {'User-Agent': user_agent}
    base_url = 'https://www.reddit.com/search.json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
        base_url = 'https://oauth.reddit.com/search'

    params = {
        'q': query,
        'limit': max(1, min(limit, 15)),
        'sort': 'relevance',
        't': 'all',
        'type': 'link',
    }

    try:
        res = requests.get(base_url, params=params, headers=headers, timeout=12)
    except requests.RequestException:
        return [], 'Reddit API is unreachable right now.'

    if not res.ok:
        return [], f'Reddit API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'Reddit API returned invalid JSON.'

    children = ((payload.get('data') or {}).get('children') or [])
    out = []
    for child in children:
        data = child.get('data') or {}
        permalink = (data.get('permalink') or '').strip()
        post_url = f'https://www.reddit.com{permalink}' if permalink else (data.get('url') or '').strip()
        if not post_url:
            continue

        out.append({
            'title': (data.get('title') or '').strip(),
            'url': post_url,
            'subreddit': (data.get('subreddit_name_prefixed') or data.get('subreddit') or '').strip(),
            'author': (data.get('author') or '').strip(),
            'score': int(data.get('score') or 0),
            'num_comments': int(data.get('num_comments') or 0),
            'created_utc': int(data.get('created_utc') or 0),
            'thumbnail_url': (data.get('thumbnail') or '').strip() if str(data.get('thumbnail') or '').startswith('http') else '',
        })

    if token_error and out:
        return out, 'Reddit OAuth credentials are not configured, using public API mode.'
    return out, token_error


def _search_wikipedia_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    wiki_user_agent = _setting_or_env(
        'wikipedia_user_agent',
        'WIKIPEDIA_USER_AGENT',
        'doodle-social-search/1.0 (Wikipedia client)',
    )
    headers = {
        'User-Agent': wiki_user_agent,
        'Accept': 'application/json',
    }

    params = {
        'action': 'query',
        'format': 'json',
        'generator': 'search',
        'gsrsearch': query,
        'gsrlimit': max(1, min(limit, 15)),
        'prop': 'info|pageimages|extracts',
        'inprop': 'url',
        'exintro': 1,
        'explaintext': 1,
        'exchars': 220,
        'piprop': 'thumbnail',
        'pithumbsize': 220,
        'origin': '*',
    }

    try:
        res = requests.get('https://en.wikipedia.org/w/api.php', params=params, headers=headers, timeout=12)
    except requests.RequestException:
        return [], 'Wikipedia API is unreachable right now.'

    if not res.ok:
        return [], f'Wikipedia API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'Wikipedia API returned invalid JSON.'

    pages = ((payload.get('query') or {}).get('pages') or {})
    sorted_pages = sorted(
        pages.values(),
        key=lambda p: int(p.get('index', 10_000_000)),
    )

    out = []
    for page in sorted_pages:
        fullurl = (page.get('fullurl') or '').strip()
        if not fullurl:
            title = (page.get('title') or '').strip().replace(' ', '_')
            if title:
                fullurl = f'https://en.wikipedia.org/wiki/{title}'
        if not fullurl:
            continue

        out.append({
            'title': (page.get('title') or '').strip(),
            'url': fullurl,
            'snippet': _strip_html((page.get('extract') or '').strip()),
            'thumbnail_url': ((page.get('thumbnail') or {}).get('source') or '').strip(),
        })

    return out, None


def _search_modrinth_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    params = {
        'query': query,
        'limit': max(1, min(limit, 20)),
        'index': 'relevance',
    }

    try:
        res = requests.get('https://api.modrinth.com/v2/search', params=params, timeout=12)
    except requests.RequestException:
        return [], 'Modrinth API is unreachable right now.'

    if not res.ok:
        return [], f'Modrinth API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'Modrinth API returned invalid JSON.'

    hits = payload.get('hits') or []
    out = []
    for item in hits:
        slug = (item.get('slug') or '').strip()
        project_id = (item.get('project_id') or '').strip()
        title = (item.get('title') or '').strip()
        description = (item.get('description') or '').strip()
        author = (item.get('author') or '').strip()
        icon_url = (item.get('icon_url') or '').strip()
        downloads = int(item.get('downloads') or 0)
        project_type = (item.get('project_type') or '').strip()

        url = ''
        if slug:
            url = f'https://modrinth.com/project/{slug}'
        elif project_id:
            url = f'https://modrinth.com/project/{project_id}'
        if not url:
            continue

        out.append({
            'title': title or url,
            'url': url,
            'description': description,
            'author': author,
            'downloads': downloads,
            'project_type': project_type,
            'thumbnail_url': icon_url,
        })

    return out, None


def _search_curseforge_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    api_key = _setting_or_env('curseforge_api_key', 'CURSEFORGE_API_KEY')
    if not api_key:
        # CurseForge is optional: if key is not configured, keep this source disabled silently.
        return [], None

    game_id_raw = _setting_or_env('curseforge_game_id', 'CURSEFORGE_GAME_ID', '432')
    try:
        game_id = int(str(game_id_raw).strip() or '432')
    except ValueError:
        game_id = 432

    headers = {
        'x-api-key': api_key,
        'Accept': 'application/json',
    }
    params = {
        'gameId': game_id,
        'searchFilter': query,
        'pageSize': max(1, min(limit, 50)),
        'sortOrder': 'desc',
    }

    try:
        res = requests.get('https://api.curseforge.com/v1/mods/search', params=params, headers=headers, timeout=12)
    except requests.RequestException:
        return [], 'CurseForge API is unreachable right now.'

    if not res.ok:
        return [], f'CurseForge API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'CurseForge API returned invalid JSON.'

    data = payload.get('data') or []
    out = []
    for item in data:
        links = item.get('links') or {}
        logo = item.get('logo') or {}
        categories = item.get('categories') or []
        category_names = [str(c.get('name') or '').strip() for c in categories if (c.get('name') or '').strip()]

        mod_id = int(item.get('id') or 0)
        url = (links.get('websiteUrl') or '').strip()
        if not url and mod_id:
            slug = (item.get('slug') or '').strip()
            if slug:
                url = f'https://www.curseforge.com/minecraft/mc-mods/{slug}'
        if not url:
            continue

        out.append({
            'title': (item.get('name') or '').strip() or url,
            'url': url,
            'summary': (item.get('summary') or '').strip(),
            'authors': [str(a.get('name') or '').strip() for a in (item.get('authors') or []) if (a.get('name') or '').strip()],
            'download_count': int(item.get('downloadCount') or 0),
            'categories': category_names,
            'thumbnail_url': (logo.get('thumbnailUrl') or logo.get('url') or '').strip(),
        })

    return out, None


def _format_steam_price(price_block: dict) -> str:
    if not price_block:
        return ''
    currency = (price_block.get('currency') or '').strip()
    final_value = price_block.get('final')
    if final_value in (None, ''):
        return ''
    try:
        amount = float(final_value) / 100
    except (TypeError, ValueError):
        return ''
    if currency:
        return f'{amount:.2f} {currency}'
    return f'{amount:.2f}'


def _search_steam_official(query: str, limit: int) -> tuple[list[dict], str | None]:
    params = {
        'term': query,
        'l': 'english',
        'cc': 'EE',
    }

    try:
        res = requests.get('https://store.steampowered.com/api/storesearch/', params=params, timeout=12)
    except requests.RequestException:
        return [], 'Steam API is unreachable right now.'

    if not res.ok:
        return [], f'Steam API returned status {res.status_code}.'

    try:
        payload = res.json()
    except ValueError:
        return [], 'Steam API returned invalid JSON.'

    items = (payload.get('items') or [])[:max(1, min(limit, 15))]
    out = []
    for item in items:
        app_id = item.get('id')
        try:
            app_id_num = int(app_id)
        except (TypeError, ValueError):
            continue

        out.append({
            'title': (item.get('name') or '').strip() or f'Steam App {app_id_num}',
            'url': f'https://store.steampowered.com/app/{app_id_num}/',
            'thumbnail_url': (item.get('tiny_image') or '').strip(),
            'type': (item.get('type') or '').strip(),
            'metascore': (item.get('metascore') or '').strip(),
            'price': _format_steam_price(item.get('price') or {}),
            'platforms': item.get('platforms') or {},
            'controller_support': (item.get('controller_support') or '').strip(),
        })

    return out, None


def _build_epic_games_search_url(query: str) -> str:
    return f'https://store.epicgames.com/en-US/browse?q={requests.utils.quote(query)}&sortBy=relevancy&sortDir=DESC&count=40'


# ------------------------------------------------------------------ /api/search

@app.get('/api/search/suggestions')
def search_suggestions():
    q = request.args.get('q', '').strip()
    try:
        limit = max(1, min(int(request.args.get('limit', 8)), 20))
    except (TypeError, ValueError):
        limit = 8

    if not q:
        return jsonify({'query': q, 'suggestions': [], 'count': 0})

    suggestions = database.get_search_suggestions(q, limit=limit)
    return jsonify({'query': q, 'suggestions': suggestions, 'count': len(suggestions), 'limit': limit})

@app.get('/api/search')
def search():
    q = request.args.get('q', '').strip()
    sort = (request.args.get('sort') or 'relevance').strip().lower()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    filters = {
        'from_date': request.args.get('from_date', '').strip(),
        'to_date': request.args.get('to_date', '').strip(),
        'domain': request.args.get('domain', '').strip(),
        'language': request.args.get('language', '').strip().lower(),
        'content_type': request.args.get('content_type', '').strip().lower(),
        'exact_phrase_only': (request.args.get('exact_phrase_only', '').strip().lower() in {'1', 'true', 'yes', 'on'}),
        'exclude': request.args.get('exclude', '').strip().lower(),
        'hidden_domains': [] if _is_admin_request() else database.get_hidden_domains(),
    }

    try:
        limit = max(1, min(int(request.args.get('limit', 20)), 100))
    except (TypeError, ValueError):
        limit = 20

    if not q:
        return jsonify({'results': [], 'query': q, 'count': 0, 'page': page, 'page_size': limit, 'total': 0, 'total_pages': 0, 'has_more': False, 'sort': sort, 'filters': filters})

    database.record_search_query(q)
    page_data = database.search_pages_page(q, page=page, page_size=limit, sort=sort, filters=filters)
    if page_data['total_pages'] and page > page_data['total_pages']:
        page_data = database.search_pages_page(q, page=page_data['total_pages'], page_size=limit, sort=sort, filters=filters)
    results = page_data['results']
    hidden = filters.get('hidden_domains') or []
    recommended = database.get_recommended_for_query(q, limit=12, hidden_domains=hidden)
    for r in results:
        r.pop('score', None)
        r.pop('canonical_url', None)
        r.pop('content_hash', None)

    return jsonify({
        'results': results,
        'query': q,
        'count': len(results),
        'limit': limit,
        'page': page_data['page'],
        'page_size': page_data['page_size'],
        'total': page_data['total'],
        'total_pages': page_data['total_pages'],
        'has_more': page_data['has_more'],
        'sort': sort,
        'filters': filters,
        'recommended': recommended,
        'recommended_count': len(recommended),
    })


@app.get('/api/recommended')
def get_recommended():
    q = request.args.get('q', '').strip()
    try:
        limit = max(1, min(int(request.args.get('limit', 12)), 50))
    except (TypeError, ValueError):
        limit = 12

    if not q:
        return jsonify({'query': q, 'results': [], 'count': 0, 'limit': limit})

    hidden = [] if _is_admin_request() else database.get_hidden_domains()
    items = database.get_recommended_for_query(q, limit=limit, hidden_domains=hidden)
    return jsonify({'query': q, 'results': items, 'count': len(items), 'limit': limit})


@app.get('/api/search/media')
def search_media():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    try:
        if kind == 'pictures':
            # Pictures: fetch more results (user wants all possible matches)
            limit = max(1, min(int(request.args.get('limit', 200)), 1000))
        else:
            # Other media types: standard limits
            limit = max(1, min(int(request.args.get('limit', 24)), 100))
    except (TypeError, ValueError):
        limit = 200 if kind == 'pictures' else 24
    if not q:
        return jsonify({'results': [], 'query': q, 'count': 0, 'kind': kind, 'limit': limit})

    hidden = [] if _is_admin_request() else database.get_hidden_domains()
    results = database.search_media_assets(q, kind, limit=limit, hidden_domains=hidden)
    for r in results:
        r.pop('score', None)

    return jsonify({'results': results, 'query': q, 'count': len(results), 'kind': kind, 'limit': limit})


@app.get('/api/social-search')
def social_search():
    q = request.args.get('q', '').strip()
    try:
        limit = max(1, min(int(request.args.get('limit', 6)), 15))
    except (TypeError, ValueError):
        limit = 6

    if not q:
        return jsonify({
            'query': q,
            'youtube': [],
            'reddit': [],
            'wikipedia': [],
            'modrinth': [],
            'curseforge': [],
            'steam': [],
            'epic_games_search_url': '',
            'counts': {'youtube': 0, 'reddit': 0, 'wikipedia': 0, 'modrinth': 0, 'curseforge': 0, 'steam': 0, 'total': 0},
            'errors': {},
            'limit': limit,
        })

    youtube, youtube_error = _search_youtube_official(q, limit)
    reddit, reddit_error = _search_reddit_official(q, limit)
    wikipedia, wikipedia_error = _search_wikipedia_official(q, limit)
    modrinth, modrinth_error = _search_modrinth_official(q, limit)
    curseforge, curseforge_error = _search_curseforge_official(q, limit)
    steam, steam_error = _search_steam_official(q, limit)
    epic_games_search_url = _build_epic_games_search_url(q)

    errors = {}
    if youtube_error:
        errors['youtube'] = youtube_error
    if reddit_error:
        errors['reddit'] = reddit_error
    if wikipedia_error:
        errors['wikipedia'] = wikipedia_error
    if modrinth_error:
        errors['modrinth'] = modrinth_error
    if curseforge_error:
        errors['curseforge'] = curseforge_error
    if steam_error:
        errors['steam'] = steam_error

    total = len(youtube) + len(reddit) + len(wikipedia) + len(modrinth) + len(curseforge) + len(steam)
    return jsonify({
        'query': q,
        'youtube': youtube,
        'reddit': reddit,
        'wikipedia': wikipedia,
        'modrinth': modrinth,
        'curseforge': curseforge,
        'steam': steam,
        'epic_games_search_url': epic_games_search_url,
        'counts': {
            'youtube': len(youtube),
            'reddit': len(reddit),
            'wikipedia': len(wikipedia),
            'modrinth': len(modrinth),
            'curseforge': len(curseforge),
            'steam': len(steam),
            'total': total,
        },
        'errors': errors,
        'limit': limit,
    })


# ------------------------------------------------------------------ /ai

def _build_ai_prompt(user_query: str) -> str:
    context_results = database.search_pages(user_query, limit=3)
    context_lines = []
    for item in context_results:
        title = (item.get('title') or '').strip()
        url = (item.get('url') or '').strip()
        snippet = (item.get('snippet') or '').strip()
        if len(snippet) > 260:
            snippet = snippet[:260] + '...'
        context_lines.append(f'- {title} | {url}\n  {snippet}')

    context_block = '\n'.join(context_lines) if context_lines else 'No local indexed context.'
    return (
        'You are a factual assistant.\n'
        'Rules:\n'
        '1) Be concise: 1 to 4 sentences.\n'
        '2) Do not create scenarios, games, test plans, roleplay, or extra tasks.\n'
        '3) If uncertain, clearly say that you are unsure instead of guessing.\n'
        '4) Prefer factual answers and avoid unrelated details.\n\n'
        f'User question: {user_query}\n\n'
        'Local context (may be empty):\n'
        f'{context_block}\n\n'
        'Answer now in Estonian only:'
    )


def _call_ollama(prompt: str) -> tuple:
    model = _load_settings().get('ai_model', 'phi')
    try:
        ollama_res = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': model,
                'prompt': prompt,
                'stream': False,
                'options': {
                    'temperature': 0.1,
                    'top_p': 0.8,
                    'num_predict': 220,
                    'repeat_penalty': 1.15,
                },
            },
            timeout=90,
        )
    except requests.RequestException:
        return False, 'Could not connect to Ollama. Ensure Ollama is running locally.', 502

    if not ollama_res.ok:
        return False, f'Ollama request failed with status {ollama_res.status_code}', 502

    try:
        payload = ollama_res.json()
    except ValueError:
        return False, 'Invalid response from Ollama', 502

    return True, (payload.get('response') or '').strip(), model, 200


def _stream_ollama(prompt: str):
    model = _load_settings().get('ai_model', 'phi')
    try:
        ollama_res = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': model,
                'prompt': prompt,
                'stream': True,
                'options': {
                    'temperature': 0.1,
                    'top_p': 0.8,
                    'num_predict': 220,
                    'repeat_penalty': 1.15,
                },
            },
            stream=True,
            timeout=90,
        )
    except requests.RequestException:
        return False, 'Could not connect to Ollama. Ensure Ollama is running locally.', 502

    if not ollama_res.ok:
        return False, f'Ollama request failed with status {ollama_res.status_code}', 502

    def generate():
        try:
            for line in ollama_res.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue

                chunk = (payload.get('response') or '')
                if chunk:
                    yield chunk

                if payload.get('done'):
                    break
        finally:
            ollama_res.close()

    return True, generate, model, 200


def _looks_off_topic(answer: str) -> bool:
    if not answer:
        return True
    return bool(re.search(r'here are the rules|test plan|setup phase|game scenario|first, we can start', answer, re.IGNORECASE))

@app.post('/api/ai')
def ai_generate():
    data = _json_body()
    user_query = _text_field(data, 'query') or _text_field(data, 'prompt')

    if not user_query:
        return jsonify({'success': False, 'message': 'Query is required'}), 400

    prompt = _build_ai_prompt(user_query)
    ok, *rest = _stream_ollama(prompt)
    if not ok:
        return jsonify({'success': False, 'message': rest[0]}), rest[-1]
    stream_body, model_used, status = rest[0], rest[1], rest[2]

    response = Response(
        stream_body(),
        mimetype='text/plain; charset=utf-8',
        status=status,
    )
    response.headers['X-Model-Used'] = model_used
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


# ------------------------------------------------------------------ /admin

@app.get('/api/admin/session')
@_require_admin
def admin_session():
    return jsonify({'success': True, 'authenticated': True})


@app.get('/api/admin/settings')
@_require_admin
def get_admin_settings():
    settings = _load_settings()
    return jsonify({'success': True, 'ai_model': settings.get('ai_model', 'phi')})


@app.post('/api/admin/settings')
@_require_admin
def update_admin_settings():
    data = _json_body()
    settings = _load_settings()
    if 'ai_model' in data:
        ai_model = _text_field(data, 'ai_model')
        if ai_model:
            settings['ai_model'] = ai_model
    if 'new_password' in data:
        new_password = _text_field(data, 'new_password')
        if len(new_password) < 4:
            return jsonify({'success': False, 'message': 'Password must be at least 4 characters'}), 400
        settings['admin_password_hash'] = generate_password_hash(new_password)
    _save_settings(settings)
    return jsonify({'success': True, 'message': 'Settings saved'})


@app.get('/api/admin/search-history')
@_require_admin
def get_admin_search_history():
    try:
        limit = max(1, min(int(request.args.get('limit', 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    history = database.get_search_history(limit=limit)
    return jsonify({'success': True, 'items': history})


@app.post('/api/admin/search-history')
@_require_admin
def add_admin_search_history():
    data = _json_body()
    query = _text_field(data, 'query')
    if not query:
        return jsonify({'success': False, 'message': 'Query is required'}), 400

    try:
        count = max(1, int(data.get('count', 1)))
    except (TypeError, ValueError, OverflowError):
        count = 1
    if count > 2**63 - 1:
        return jsonify({'success': False, 'message': 'Count is too large'}), 400

    ok = database.add_search_history_entry(query, count=count)
    if not ok:
        return jsonify({'success': False, 'message': 'Query is required'}), 400
    return jsonify({'success': True, 'message': 'History entry saved'})


@app.delete('/api/admin/search-history')
@_require_admin
def delete_admin_search_history():
    data = _json_body()
    query = _text_field(data, 'query')

    if not query:
        removed = database.clear_search_history()
        return jsonify({'success': True, 'message': f"Removed {removed} history entr{'y' if removed == 1 else 'ies'}"})

    deleted = database.delete_search_history_entry(query)
    if not deleted:
        return jsonify({'success': False, 'message': 'History entry not found'}), 404
    return jsonify({'success': True, 'message': 'History entry deleted'})


@app.get('/api/admin/recommended')
@_require_admin
def get_admin_recommended():
    try:
        limit = max(1, min(int(request.args.get('limit', 300)), 1000))
    except (TypeError, ValueError):
        limit = 300

    items = database.get_recommended_entries(limit=limit)
    return jsonify({'success': True, 'items': items})


@app.post('/api/admin/recommended')
@_require_admin
def add_admin_recommended():
    data = _json_body()
    query = _text_field(data, 'query')
    url = _text_field(data, 'url')
    title = _text_field(data, 'title')

    if not query or not url:
        return jsonify({'success': False, 'message': 'Query and URL are required'}), 400

    ok = database.add_recommended_entry(query=query, url=url, title=title)
    if not ok:
        return jsonify({'success': False, 'message': 'Invalid recommended entry'}), 400
    return jsonify({'success': True, 'message': 'Recommended entry saved'})


@app.delete('/api/admin/recommended')
@_require_admin
def delete_admin_recommended():
    data = _json_body()

    raw_id = data.get('id')
    if raw_id in (None, ''):
        removed = database.clear_recommended_entries()
        return jsonify({'success': True, 'message': f"Removed {removed} recommended entr{'y' if removed == 1 else 'ies'}"})

    try:
        entry_id = int(raw_id)
        if not 0 < entry_id <= 2**63 - 1:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return jsonify({'success': False, 'message': 'Valid id is required'}), 400

    deleted = database.delete_recommended_entry(entry_id)
    if not deleted:
        return jsonify({'success': False, 'message': 'Recommended entry not found'}), 404
    return jsonify({'success': True, 'message': 'Recommended entry deleted'})


# ------------------------------------------------------------------ /admin/hidden-domains

@app.get('/api/admin/hidden-domains')
@_require_admin
def get_hidden_domains():
    domains = database.get_hidden_domains()
    return jsonify({'success': True, 'domains': domains})


@app.post('/api/admin/hidden-domains')
@_require_admin
def add_hidden_domain():
    data = _json_body()
    domain = _text_field(data, 'domain').lower()
    if not domain:
        return jsonify({'success': False, 'message': 'Domain on nõutud'}), 400
    ok = database.add_hidden_domain(domain)
    if not ok:
        return jsonify({'success': False, 'message': 'Vigane domeen'}), 400
    return jsonify({'success': True, 'message': f'{domain} peidetud'})


@app.delete('/api/admin/hidden-domains')
@_require_admin
def remove_hidden_domain():
    data = _json_body()
    domain = _text_field(data, 'domain').lower()
    if not domain:
        return jsonify({'success': False, 'message': 'Domain on nõutud'}), 400
    deleted = database.remove_hidden_domain(domain)
    if not deleted:
        return jsonify({'success': False, 'message': 'Domeeni ei leitud'}), 404
    return jsonify({'success': True, 'message': f'{domain} kuvamine taastatud'})


# ------------------------------------------------------------------ /admin/domains/export

@app.get('/api/admin/domains/export')
@_require_admin
def export_domains():
    domains = database.get_all_distinct_domains()
    content = '\n'.join(domains)
    return Response(
        content,
        mimetype='text/plain; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename="domains.txt"'},
    )


# ------------------------------------------------------------------ /admin/domains (delete & recrawl)

@app.delete('/api/admin/domains')
@_require_admin
def delete_domain():
    data = _json_body()
    raw = _text_field(data, 'domain').lower()
    if not raw:
        return jsonify({'success': False, 'message': 'Domain on nõutud'}), 400
    domain = database.extract_domain(raw) or raw
    count = database.delete_domain_pages(domain)
    if count == 0:
        return jsonify({'success': False, 'message': f'Domeeni {domain} lehti ei leitud'}), 404
    return jsonify({'success': True, 'message': f'{count} lehte kustutatud ({domain})', 'deleted': count, 'domain': domain})


@app.get('/api/admin/domains/check')
@_require_admin
def check_domain():
    raw = (request.args.get('domain') or '').strip().lower()
    if not raw:
        return jsonify({'success': False, 'message': 'Domain on nõutud'}), 400
    domain = database.extract_domain(raw) or raw
    count = database.get_page_count_for_domain(domain)
    return jsonify({'success': True, 'domain': domain, 'count': count})


@app.post('/api/admin/domains/recrawl')
@_require_admin
def recrawl_domain():
    data = _json_body()
    raw = _text_field(data, 'domain').lower()
    if not raw:
        return jsonify({'success': False, 'message': 'Domain on nõutud'}), 400
    domain = database.extract_domain(raw) or raw
    urls = database.get_urls_for_domain(domain)
    if not urls:
        return jsonify({'success': False, 'message': f'Domeeni {domain} lehti ei leitud'}), 404
    added = crawler_manager.add_urls_for_recrawl(urls)
    return jsonify({'success': True, 'message': f'{added} URL-i lisatud järjekorda ({domain})', 'added': added, 'domain': domain})


# ------------------------------------------------------------------ /crawl

@app.post('/api/crawl')
@_require_admin
def add_to_queue():
    data = _json_body()
    url = _text_field(data, 'url')

    if not url:
        return jsonify({'success': False, 'message': 'URL is required'}), 400

    ok, msg = crawler_manager.add_url(url)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 409)


@app.post('/api/start-crawl')
@_require_admin
def start_crawl():
    data = _json_body()
    try:
        max_depth = max(0, min(int(data.get('max_depth', 1)), 3))
    except (TypeError, ValueError, OverflowError):
        max_depth = 1

    ok, msg = crawler_manager.start_crawl(max_depth=max_depth)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


@app.post('/api/backfill-media')
@_require_admin
def backfill_media():
    data = _json_body()
    try:
        limit = data.get('limit')
        limit = None if limit in (None, '') else max(1, min(int(limit), 2**63 - 1))
    except (TypeError, ValueError, OverflowError):
        limit = None

    ok, msg = crawler_manager.backfill_media(limit=limit)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


# ------------------------------------------------------------------ /queue

@app.get('/api/queue')
def get_queue():
    q = crawler_manager.get_queue()
    return jsonify({'queue': q, 'count': len(q)})


@app.delete('/api/queue')
@_require_admin
def remove_from_queue():
    data = _json_body()
    url = _text_field(data, 'url')
    if not url:
        return jsonify({'success': False, 'message': 'URL is required'}), 400

    ok, msg = crawler_manager.remove_url(url)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 404)


# ----------------------------------------------------------------- /crawl-status

@app.get('/api/crawl-status')
def crawl_status():
    status = crawler_manager.get_status()
    status['total_pages'] = database.get_page_count()
    return jsonify(status)


# ----------------------------------------------------------------- frontend

@app.get('/')
def index():
    return render_template('index.html')


@app.get('/login')
def login():
    return shared_signin()


@app.post('/logout')
def logout():
    return shared_signout()


@app.get('/search')
def search_page():
    return render_template('search.html')


@app.get('/admin')
def admin_page():
    return render_template('admin.html')


@app.get('/<path:filename>')
def static_files(filename):
    if filename not in {"app.js", "style.css", "dodo.png"}:
        return Response("Not found", status=404)
    return send_from_directory(FRONTEND, filename)


app.admin_tab(
    "Doodle", "/admin", prefixes=("admin_", "get_admin_", "update_admin_", "add_admin_", "delete_admin_"),
    endpoints=(
        "get_hidden_domains", "add_hidden_domain", "remove_hidden_domain",
        "export_domains", "delete_domain", "check_domain", "recrawl_domain",
        "add_to_queue", "start_crawl", "backfill_media", "get_queue",
        "remove_from_queue", "crawl_status",
    ),
    assets=(
        "static_files", "search_page", "search", "search_suggestions",
        "get_recommended", "search_media", "social_search",
    ),
)
