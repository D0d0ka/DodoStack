# Dodo TV

Dodo TV runs inside [DodoStack](../../README.md). Open [Dodo TV](http://dodotv.localhost:8000) to browse published titles. Sign in with your shared DodoStack account to rate titles and save playback progress and history.

Open **Admin → Dodo TV** in the workspace to manage movies, shows, episodes, subtitles, uploads, publishing, and service profiles. Account roles and passwords are managed under **Admin → Accounts**. The Dodo TV profile policy is in the studio settings.

By default, the database is `instance/dodotv.db` and media lives under `uploads/` in this directory. Copy `.env.example` to `.env` to configure storage, FFmpeg, or upload limits. Dodo TV uses FFmpeg and FFprobe to validate videos and generate preview frames. Run `python server.py seed-tv-demo` from the project root to generate a demo catalog.

Use the **Creator or production company** field when adding or editing a movie or TV show. The credit appears on the title’s detail page.

Movie and episode forms also have **Import from YouTube**. Enter an individual video URL instead of choosing a video file, choose whether to download its thumbnail, and select no subtitles, specific language codes (for example `et, en`), or all available languages. Captions can be manually added, automatic, or both; manual captions take priority when both exist in the same language. An uploaded poster takes priority over the YouTube thumbnail. Save the form and keep the tab open until the download and preview generation finish. Importing into an existing title replaces its video and subtitles.

YouTube imports use yt-dlp from the workspace requirements and require FFmpeg and FFprobe. Imported videos, images and WebVTT subtitles go through the same validation and storage as uploaded files. Videos requiring YouTube sign-in are not supported. The isolated import regressions run from the repository root with `.venv/bin/python -m unittest discover -s checks -p test_dodotv_youtube.py -v`.
