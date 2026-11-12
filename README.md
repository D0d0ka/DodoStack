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
| Administration | [localhost:8000/admin](http://localhost:8000/admin) |

Dodo TV uses FFmpeg and FFprobe for video uploads, validation, and preview frames. Doodle's optional AI answers require a local Ollama instance. Set `DODO_PORT` to change the port.

## Sign-in and server settings

All services require a DodoStack account. Sign in with your username or email address. The root `.env` configures DodoStack. Copy `.env.example` if needed, and restart after changing settings.

- `DODO_MIN_PASSWORD_LENGTH` sets the minimum length for new and changed passwords. The default is 8.
- `DODO_PRIVATE=true` requires workspace approval for new accounts. Approve them in **Admin → Accounts**.

### Access from another device

Set the listening address and the workspace addresses in the server's root `.env`:

```dotenv
DODO_HOST=0.0.0.0
DODO_PORT=8000
DODO_WORKSPACE_HOSTS=dodoai,192.168.0.11=dodostack.home.arpa
```

Restart with `.venv/bin/python server.py`. In this example, you can open the workspace at `http://dodoai:8000` or `http://192.168.0.11:8000`. Each workspace address links to its own set of service hostnames. A DNS workspace name doubles as the service domain by default. For a workspace opened by IPv4 address, specify a separate service domain after `=`.

Configure DNS or the client device's hosts file so that `todo`, `doodle`, `dodobook`, `dodotv`, and `dodokey` under each service domain resolve to the server. For example, on the LAN you can add this hosts-file entry:

```text
192.168.0.11 todo.dodostack.home.arpa doodle.dodostack.home.arpa dodobook.dodostack.home.arpa dodotv.dodostack.home.arpa dodokey.dodostack.home.arpa
```

For `dodoai`, point those five names under `dodoai` to the Pi's Tailscale IP. The workspace name resolving on Tailscale does not itself create DNS records for its service names. DodoKey's browser encryption requires a secure context such as localhost or HTTPS.

## DodoKey password vault

Open DodoKey from **Your services** and set a separate vault password of at least 12 characters. Add, edit, generate, copy, and delete saved logins from the vault. Your browser encrypts the vault before saving it to DodoStack and locks it after five minutes of inactivity. Keep your vault password safe because resetting your DodoStack account password cannot recover the vault.

## Administrators

Register a shared account in the workspace. Grant the first account administrator access from the terminal.

```bash
python server.py set-admin YOUR_USERNAME
```

The workspace **Admin** tabs manage shared accounts, Doodle, DodoBook, and Dodo TV. In DodoBook, open **Moderation** from the user menu. DodoBook administrators can manage other members' DodoBook roles and delete posts and comments. Moderators can delete posts and comments they can see. DodoBook roles apply within DodoBook. DodoBook uses the shared email address and keeps its username, bio, and picture in the service profile.

## Local databases

DodoStack creates databases from the current schema. The shared account, DodoBook, Doodle, and Dodo TV databases are initialized on startup. Todo and DodoKey initialize their databases on first use.

| Database | Path relative to the project root |
| --- | --- |
| Shared accounts | `instance/accounts.sqlite3` |
| Todo | `subsites/todo/instance/todos.sqlite3` |
| Doodle | `subsites/doodle/doodle.db` |
| DodoBook | `subsites/dodobook/instance/dodobook.db` |
| Dodo TV | `subsites/DodoTV/instance/dodotv.db` |
| DodoKey | `subsites/dodokey/instance/vaults.sqlite3` |

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
