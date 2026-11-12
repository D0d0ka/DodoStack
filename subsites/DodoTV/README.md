# Dodo TV

Dodo TV runs inside [DodoStack](../../README.md). Open [Dodo TV](http://dodotv.localhost:8000) to browse published titles. Sign in with your shared DodoStack account to rate titles and save playback progress and history.

Open **Admin → Dodo TV** in the workspace to manage movies, shows, episodes, subtitles, uploads, publishing, and service profiles. Account roles and passwords are managed under **Admin → Accounts**. The Dodo TV profile policy is in the studio settings.

By default, the database is `instance/dodotv.db` and media lives under `uploads/` in this directory. Copy `.env.example` to `.env` to configure storage, FFmpeg, or upload limits. Dodo TV uses FFmpeg and FFprobe to validate videos and generate preview frames. Run `python server.py seed-tv-demo` from the project root to generate a demo catalog.

The root README explains how to reuse existing data and attach legacy TV profiles to shared accounts.
