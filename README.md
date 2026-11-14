# DodoStack

DodoStack starts the workspace and all registered services in one Python process. Users sign in with one DodoStack account. Each service keeps its own application data.

## Run locally

From the DodoStack directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

| Page | Address |
| --- | --- |
| Workspace | [localhost:8000](http://localhost:8000) |
| Todo | [todo.localhost:8000](http://todo.localhost:8000) |
| Doodle | [doodle.localhost:8000](http://doodle.localhost:8000) |
| DodoBook | [dodobook.localhost:8000](http://dodobook.localhost:8000) |
| Dodo TV | [dodotv.localhost:8000](http://dodotv.localhost:8000) |
| DodoKey | [dodokey.localhost:8000](http://dodokey.localhost:8000) |
| Dodo Calender | [dodocalender.localhost:8000](http://dodocalender.localhost:8000) |
| DodoMail | [dodomail.localhost:8000](http://dodomail.localhost:8000) |
| Administration | [localhost:8000/admin](http://localhost:8000/admin) |

Dodo TV uses FFmpeg and FFprobe for video uploads, validation, and preview frames. Doodle's optional AI answers require a local Ollama instance. Set `DODO_PORT` to change the port.

## Sign-in and server settings

All services require a DodoStack account. Sign in with your username, email address, or DodoMail address. The root `.env` configures DodoStack. Copy `.env.example` if needed, and restart after changing settings.

- `DODO_MIN_PASSWORD_LENGTH` sets the minimum length for new and changed passwords. The default is 8.
- `DODO_PRIVATE=true` requires workspace approval for new accounts. Approve them in **Admin → Accounts**.
- `DODO_DEBUG=true` enables detailed diagnostics on stderr after a restart. The default is `false`.

### Debugging requests

Every HTTP response carries an `X-Request-ID` that matches the server log. Error pages also show it on screen. Debug mode adds startup timings, host and route matching, authentication decisions, and exception stack frames to the HTTP status and duration logs. Request payloads, cookies, raw paths, query values, and exception messages stay out of the request logs.

### Access from another device

Set the listening address and the workspace addresses in the server's root `.env`:

```dotenv
DODO_HOST=0.0.0.0
DODO_PORT=8000
DODO_WORKSPACE_HOSTS=dodoai,192.168.0.11=dodostack.home.arpa
```

Restart with `.venv/bin/python server.py`. In this example, the workspace opens at `http://dodoai:8000`, `http://192.168.0.11:8000`, and `http://dodostack.home.arpa:8000`. The domain after `=` is automatically registered as a workspace on the same browser-facing port. Both the IP and domain workspaces link to the same services. Those services use the domain workspace for sign-in.

Configure DNS or the client device's hosts file so that the service domain and its service subdomains resolve to the server. For example, on the LAN you can add this hosts-file entry:

```text
192.168.0.11 dodostack.home.arpa todo.dodostack.home.arpa doodle.dodostack.home.arpa dodobook.dodostack.home.arpa dodotv.dodostack.home.arpa dodokey.dodostack.home.arpa dodocalender.dodostack.home.arpa dodomail.dodostack.home.arpa
```

For `dodoai`, point those service names under `dodoai` to the Pi's Tailscale IP. The workspace name resolving on Tailscale does not itself create DNS records for its service names. DodoKey supports HTTP on these service names and still encrypts vault data in the browser. Use HTTP only over a trusted connection because a network attacker can modify the page or intercept the sign-in session.

### Direct HTTPS on port 8000

Keep `DODO_HTTPS=false` for HTTP. To serve HTTPS directly, obtain a certificate covering your workspace and all service hostnames, then set the following in the root `.env` on the server:

```dotenv
DODO_HOST=0.0.0.0
DODO_PORT=8000
DODO_WORKSPACE_HOSTS=dodoai.duckdns.org
DODO_HTTPS=true
DODO_TLS_CERT_FILE=/path/to/fullchain.pem
DODO_TLS_KEY_FILE=/path/to/privkey.pem
```

For DuckDNS, a certificate for both `dodoai.duckdns.org` and `*.dodoai.duckdns.org` covers the workspace and its services. Use an ACME client with DuckDNS DNS-01 validation. Keep its token and the private key outside the checkout and grant the DodoStack process read access to the certificate and key. Restart DodoStack after renewing the certificate. To reach it from the internet, point the names to the public address and forward TCP port 8000 to the server. Open `https://dodoai.duckdns.org:8000`. Register any additional hostnames only if the certificate covers them. A browser that has cached HSTS may continue to require HTTPS for up to a day after switching the same hostname back to HTTP.

Direct TLS encrypts the connection. The bundled listener uses Python's `http.server`, which Python advises against exposing in production. It has connection limits and timeouts, and can still be exhausted by untrusted traffic. Keep HTTP deployments on a trusted network.

### Cloudflare Tunnel

When `cloudflared` and DodoStack run on the same server, `cloudflared tunnel --url http://127.0.0.1:8123` can forward public HTTPS to local HTTP. Add the public address printed by `cloudflared` to the root `.env`, then restart DodoStack. Keep the original browser Host header when forwarding to DodoStack.

```dotenv
DODO_HOST=127.0.0.1
DODO_PORT=8123
DODO_HTTPS=false
DODO_WORKSPACE_HOSTS=https://example.trycloudflare.com
```

The `https://` prefix selects HTTPS for public URLs, origin checks and secure cookies for that workspace. Quick Tunnel hostnames also select HTTPS automatically when entered without the prefix. Other entries without a scheme continue to use `DODO_HTTPS`. When `cloudflared` prints a new Quick Tunnel address, replace the old entry and restart DodoStack while leaving the current tunnel running. Open a fresh sign-in page to receive cookies for the current host.

A Quick Tunnel has one temporary hostname, so it exposes the workspace and workspace admin routes. The service cards show descriptions there. For clickable service cards and all registered services, use a named tunnel with a domain you control, register `https://your-domain`, and route that domain and each service hostname to the same local HTTP listener.

## DodoKey password vault

Open DodoKey from **Your services** and set a separate vault password of at least 12 characters. Add, edit, generate, copy, and delete saved logins from the vault. Your browser encrypts the vault before saving it to DodoStack and locks it after five minutes of inactivity. Keep your vault password safe because resetting your DodoStack account password cannot recover the vault.

## Administrators

After a database reset, start the workspace with `.venv/bin/python server.py` and select **Create account**. Enter your username, password, date of birth, first name, last name, email address and DodoMail address. Then grant the first account administrator access from a separate terminal in the repo root. This also approves the account in private mode. Sign in again or reload the workspace to open **Admin**.

```bash
.venv/bin/python server.py set-admin YOUR_USERNAME
```

The workspace **Admin** tabs manage shared accounts, Doodle, DodoBook, and Dodo TV. In DodoBook, open **Moderation** from the user menu. DodoBook administrators can manage other members' DodoBook roles and delete posts and comments. Moderators can delete posts and comments they can see. DodoBook roles apply within DodoBook. DodoBook uses the shared email address and keeps its username, bio, and picture in the service profile.

## Local databases

DodoStack creates databases from the current schema. The shared account, DodoBook, Doodle, and Dodo TV databases are initialized on startup. Todo, DodoKey, and Dodo Calender initialize their databases on first use. DodoMail shares the account database so registration and delivery of the welcome message commit together.

To reset local data, stop the server and remove the application databases listed below along with any matching SQLite `-wal`, `-shm`, or `-journal` files. Check for a configured local Dodo TV database path if you set `DATABASE_URL`. Preserve `.env`, `instance/server.key`, the Dodo TV signing key, and other configuration. Restart the server and follow the first-account steps above.

| Database | Path relative to the project root |
| --- | --- |
| Shared accounts and DodoMail | `instance/accounts.sqlite3` |
| Todo | `subsites/todo/instance/todos.sqlite3` |
| Doodle | `subsites/doodle/doodle.db` |
| DodoBook | `subsites/dodobook/instance/dodobook.db` |
| Dodo TV | `subsites/DodoTV/instance/dodotv.db` |
| DodoKey | `subsites/dodokey/instance/vaults.sqlite3` |
| Dodo Calender | `subsites/dodocalender/instance/calendar.sqlite3` |

## DodoMail and calendar invitations

Use **Compose** to send plain-text mail to another DodoMail address. **Sent** shows your outgoing mail. Open a personal message to reply or forward it. Send questions to `support@dmail`. Administrators can read and reply to those messages from the workspace’s **Support** tab.

DodoMail also receives plain-text updates from DodoStack services. To deliver mail from an authenticated `WebApplication` handler, call `subsites.dodomail.delivery.deliver(request, recipient_id, subject, body)`. The sender comes from the server's service routing context and is authenticated with the shared service assertion. The delivery function accepts the DodoStack account ID of an approved recipient.

To invite someone, open an event you own in Dodo Calender and enter their username or DodoMail address under **Sharing**. They can accept or decline from their inbox. Accepted events appear in their calendar alongside events added through sharing links.

## Add a service and admin tab

Create an app module in `subsites/` and register it in `SERVICE_MODULES` in `server.py`. Use `Application` in `dodo/framework.py` for simple request handlers or `WebApplication` in `dodo/web.py` for templates, JSON, forms, and streaming.

```python
from dodo.web import WebApplication, admin_only, render_template

app = WebApplication(__name__)

@app.get("/management")
@admin_only
def management():
    return render_template("management.html")

app.admin_tab("Example", "/management", endpoints=("management",))
```

The tab and its routes appear automatically under `/admin/<service>` on the main server. Add admin actions to `endpoints` or use `prefixes=("admin.",)` for a whole admin blueprint. Use `assets` for explicitly allowed read-only dependencies such as stylesheets and media previews. Use `url_for()` for links and form actions, and include `{{ admin_navigation }}` in the service's admin template to show the shared tabs. Use `admin_only` to require the shared admin role. If a service explicitly exposes admin endpoints on its own hostname with `on_service=True`, its handlers must check the service roles.

Public routes can use `require_login` and a service profile loader with the shared account. `Identity.issue_assertion()` and `verify_assertion()` are available for short-lived, service-scoped calls.
