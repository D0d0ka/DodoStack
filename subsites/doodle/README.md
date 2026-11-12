# Doodle

Doodle runs inside [DodoStack](../../README.md). Open [Doodle](http://doodle.localhost:8000) for search, media and source filters, and optional local AI answers.

Use **Admin → Doodle** on the main server to manage the crawler, search history, recommendations, hidden domains, domain exports, and the AI model. Existing search data and settings live in `doodle.db` and `settings.json` in this directory. Local AI answers require Ollama at `http://localhost:11434`.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/search?q=<query>` | Search indexed pages |
| `GET`  | `/api/search/media?q=<query>&kind=<pictures/videos/sound/youtube>` | Search indexed media |
| `GET`  | `/api/social-search?q=<query>` | Search YouTube, Reddit, Wikipedia, Modrinth, CurseForge and Steam, plus an Epic Games store search link |
| `POST` | `/api/ai` | Ask Ollama (`{ "prompt": "..." }`) using model `phi` |
| `POST` | `/api/crawl` | Add `{ "url": "..." }` to queue |
| `POST` | `/api/start-crawl` | Begin crawling `{ "max_depth": 1 }` |
| `GET`  | `/api/queue` | List queued URLs |
| `GET`  | `/api/crawl-status` | Current crawl status + log |

### Social API credentials

`/api/social-search` uses official endpoints:

- YouTube Data API v3 (`https://www.googleapis.com/youtube/v3/search`)
- Reddit API (`https://oauth.reddit.com/search` with OAuth if credentials are set, otherwise `https://www.reddit.com/search.json`)
- Wikipedia API (`https://en.wikipedia.org/w/api.php`)
- Modrinth API (`https://api.modrinth.com/v2/search`)
- CurseForge API (`https://api.curseforge.com/v1/mods/search`)
- Steam Store API (`https://store.steampowered.com/api/storesearch/`)
- Epic Games official store search link (`https://store.epicgames.com/en-US/browse?...`)

Optional configuration (environment variables or `settings.json`):

- `YOUTUBE_API_KEY` (or `youtube_api_key` in settings)
- `REDDIT_CLIENT_ID` (or `reddit_client_id` in settings)
- `REDDIT_CLIENT_SECRET` (or `reddit_client_secret` in settings)
- `REDDIT_USER_AGENT` (or `reddit_user_agent` in settings)
- `WIKIPEDIA_USER_AGENT` (or `wikipedia_user_agent` in settings)
- `CURSEFORGE_API_KEY` (or `curseforge_api_key` in settings)
- `CURSEFORGE_GAME_ID` (or `curseforge_game_id` in settings, default `432` for Minecraft)

---

## Notes

- The SQLite database (`doodle.db`) is created automatically on first run.
- Crawling happens in a background thread — the UI remains responsive.
- The crawler stays within the same domain and caps links per page at 15 to avoid runaway crawls.
- Re-crawling a URL updates its record (`INSERT OR REPLACE`).
