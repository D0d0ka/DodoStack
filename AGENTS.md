# DodoStack

- Run the workspace and its services with `.venv/bin/python server.py` from the repository root. Install dependencies with `python -m pip install -r requirements.txt`.
- Use the shared DodoStack identity in `dodo/auth.py`. Require sign-in on service routes, check ownership with the shared account ID, and validate CSRF tokens on state-changing browser requests.
- Keep each service in `subsites/`. Register it in `SERVICE_MODULES` and `BRAND_FILES` in `server.py` and add its name and description in `dodo/pages.py`. Link workspace cards directly to their services.
- Keep local data and signing keys out of Git. Services initialize their databases from the current schema. Run destructive data operations only when explicitly requested, and use isolated data for verification.
- DodoKey encrypts and decrypts vault entries in the browser. Keep vault passwords, derived keys, and unencrypted entries out of requests, logs, and persistent browser storage. Keep scripts and styles local to DodoKey.
- Verify changes through the normal workspace and service routes. Check signed-out access and account isolation when editing authentication or personal data.
- Use .venv as python environment.
- Always when you learn something new about this project that would help in developing it later write it into this file.
- Use a referrer policy such as `strict-origin` for native POST forms. With `no-referrer`, browsers send `Origin: null` and the shared origin check rejects the form.
- An HTTP reverse proxy should preserve the browser's Host header. Register each proxy workspace as `host:port` in `DODO_WORKSPACE_HOSTS` when the browser port differs from `DODO_PORT`. Workspace and service redirects use the configured browser port.
- In `DODO_WORKSPACE_HOSTS`, `host[:port]=service-domain` also registers the bare service domain as a workspace on that browser-facing port. Services on that domain use its workspace for sign-in. Entries sharing a service domain must agree on the port.
- `DODO_DEBUG` enables detailed server diagnostics. Use `X-Request-ID` to match a response to its stderr logs. Keep request payloads, query values, credentials, and exception messages out of diagnostics. Route browser error responses through `dodo/pages.py` or the service's error handlers and preserve API response formats and HTTP error headers.
- `DODO_HTTPS=true` enables direct TLS with `DODO_TLS_CERT_FILE` and `DODO_TLS_KEY_FILE`. It also selects HTTPS URLs, origin checks, and `__Host-` secure cookies. Restart after certificate renewal. The bundled listener uses Python's `ThreadingHTTPServer`, which Python advises against exposing in production.
- Shared sign-in tickets are tied to a service host and browser cookie, expire after two minutes, and are redeemed once in the shared account database. Keep `/_auth/callback` query strings out of proxy access logs. Workspace and service mutations require a form CSRF token or `X-CSRFToken`, depending on the framework.
- Doodle connects to the public IP address it verified for each crawl request and redirect. HTTPS certificate verification and the Host header still use the original hostname.
