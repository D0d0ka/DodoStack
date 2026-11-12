# Copilot Instructions for Dodobook

## Running the app

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py          # starts on http://localhost:8080
```

On first run the initial admin is created automatically. The terminal prints
a generated password unless `DODOBOOK_ADMIN_PASSWORD` is set.

There is no test suite, no lint config, and no Makefile. The closest syntax check is:

```bash
source .venv/bin/activate && python -m compileall server.py
```

To reset to a clean state, delete the database:

```bash
rm -f instance/dodobook.db
```

The schema is re-created by `db.create_all()` on the next startup — **no migration scripts exist or should be added**.

## Architecture

Everything lives in two places:

| Location | What it contains |
|---|---|
| `server.py` | SQLAlchemy models, all Flask routes (page + JSON API), auth decorators, file-upload helpers, Jinja2 filters |
| `templates/*.html` | Full-page Jinja2 templates; all JS is inline in each template |

There is no separate services layer, blueprints, or JS build step.

### Key models

| Model | Notes |
|---|---|
| `User` | `role`: user/moderator/admin · `status`: pending/accepted · soft-delete via `is_deleted` |
| `Friendship` | Directed: `requester_id` → `addressee_id` · `status`: pending/accepted |
| `DirectMessage` | 1-on-1 chat; `file_path` + `file_name` for attachments |
| `GroupMessage` | Group chat; `is_system=True` for kick/leave system notices |
| `Post` / `PostImage` / `PostFile` | Posts with image carousel and file attachments |
| `ChatLastRead` | Unread-count tracking per (user, chat_type, chat_id) |
| `PasswordResetRequest` | Admin-fulfilled manual password reset flow |

### Auth flow

Three decorators gate access:

- `@accepted_required` — user must be logged in **and** `status == 'accepted'`
- `@moderator_required` — role in `('moderator', 'admin')`
- `@admin_required` — role == `'admin'`

New accounts start as `status='pending'` and wait for moderator approval.

### Online / offline

`last_seen` is updated in `@app.before_request` on every HTTP request (including polling calls). `User.is_online` checks `last_seen` within the last 5 minutes. The `/api/unread` polling response also returns `online` status for all friends so the sidebar updates without a page reload.

### File uploads

Two helpers in `server.py`:

- `save_upload(file, subfolder)` — images only; returns relative path
- `save_any_file(file, subfolder)` — any file; returns `(path, file_type, original_name)`

All uploads land under `static/uploads/<subfolder>/` with a UUID-prefixed filename. The original filename is stored in the `file_name` column and sent back in API responses so the browser download prompt shows the correct name.

### Chat polling

Chats are polling-based (no WebSockets). The frontend polls `/api/chat/messages?type=&id=&after=` every 2.5 s when a chat is open, and `/api/unread` every 5 s for badge counts and online status.

## Conventions

### Soft-delete users
Never hard-delete a `User` row. Set `is_deleted = True` and anonymise `username`, `email`, `password_hash`, `profile_pic`. Posts and messages remain attributed to `[deleted user]` via `User.display_name`.

### Updating the schema
Add new columns/models to the class definitions in `server.py`. `db.create_all()` will pick them up on a fresh database. If a production DB exists, drop it and let the app recreate it — there is no migration path.

### Template + backend pairing
When changing a feature, update both the relevant route in `server.py` and the corresponding template. JS is co-located in each template's `<script>` block; there is no shared JS file.

### YouTube embeds in posts
Post content passes through the `post_content` Jinja2 filter which detects YouTube URLs and replaces them inline with `<iframe>` embeds while HTML-escaping all other text.

### File naming
- Uploaded files: `{uuid32}_{original_name}` on disk
- The original filename is preserved in a `file_name` DB column and returned in API responses
- Use `download="${file_name}"` in anchor tags so the browser saves with the original name

### Adding API endpoints
Follow the existing pattern: decorated with the appropriate auth decorator, return `jsonify({...})` for JSON routes and `render_template` / `redirect` for page routes. Keep page routes and their matching API calls co-located in `server.py`.
