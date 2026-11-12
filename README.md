# DodoStack

DodoStack runs a shared account server and its child web services in one Python process. The main workspace is at `http://localhost:8000`; Todo is routed by hostname to `http://todo.localhost:8000`.

## Run locally

```bash
cd DodoStack
python3 server.py
```

Open <http://localhost:8000>. The server creates `instance/accounts.sqlite3` and a private signing key on first start. Existing Todo accounts and their user IDs are imported into the shared account database automatically. Existing task data remains in `subsites/todo/instance/todos.sqlite3`. Todo no longer requires Flask; it uses the Dodo framework and Python's standard library. Werkzeug is retained as a small migration aid to validate password hashes made by the earlier Flask version.

Use the Todo tab to open the service in the browser. Sign in once on DodoStack to access Todo with the same account.

## Architecture

- `dodo/framework.py` provides route registration, request and response objects, static files, redirects, and authenticated-route protection.
- `dodo/auth.py` stores shared accounts and issues signed, expiring tokens scoped to one service. A child service sends unauthenticated users through the main server, then validates its own audience-scoped session. Services receive a small user identity object and can issue brief, audience-scoped assertions for registered service-to-service calls.
- `server.py` starts one threaded HTTP server and routes `localhost` to the workspace and registered service hostnames to child applications.
- `subsites/todo/app.py` implements Todo routes and keeps each user's task data in Todo's own SQLite database.

Passwords are stored as PBKDF2 hashes for new accounts. Session tokens are signed with a local key and set as HttpOnly, SameSite=Lax cookies. A per-account session version lets the main server revoke all service sessions when the user signs out. Set `DODO_SECRET_KEY` to a stable, random secret when deploying behind a trusted HTTPS endpoint. For local development, use `localhost` and its `.localhost` service subdomains.

## Add a service

1. Create an app module under `subsites/<service>/` and instantiate `Application("<service>", static_dir=...)`.
2. Register routes with `@app.route("/path", ("GET",))`; use `@require_user` for routes that need the shared account. The current request exposes `request.user` with the shared account's `id` and `username`.
3. Add `"<service>": "subsites.<service>.app"` to `SERVICE_MODULES` in `server.py`. The host mapping, application import, auth hookup, and dashboard tab are generated from this registry.
4. Use `/_auth/callback` for the standard sign-in round trip. Keep service data in its own database. For a service-to-service request, call `identity.issue_assertion(source, target, request.user, claims)` and have the target validate it with `identity.verify_assertion(source, target, token)`.

Todo is the first registered service. DodoBook, Doodlet, and Dodo TV have not been migrated and can be brought over one at a time using the same route, identity, and service registration APIs.
