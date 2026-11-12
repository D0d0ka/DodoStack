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
| Administration | [localhost:8000/admin](http://localhost:8000/admin) |

Dodo TV uses FFmpeg and FFprobe for video uploads, validation, and preview frames. Doodle's optional AI answers require a local Ollama instance. Set `DODO_PORT` to change the port.

## Administrators

Register a shared account in the workspace. From the terminal, grant the first account administrator access:

```bash
python server.py set-admin YOUR_USERNAME
```

Open **Admin** in the workspace. The **Accounts** tab manages shared roles and password resets. The other tabs contain Doodle's crawler and search settings, DodoBook's moderation and profile tools, and Dodo TV's catalog, uploads, profiles, and settings. DodoBook group ownership stays part of the group itself.

DodoBook still requires approval for new member profiles. Dodo TV can restrict creation of new service profiles through its settings. These policies govern access to each service.

## Existing data

Todo imports legacy Todo accounts on first startup and keeps its task database. For the other services, back up the source data and place a copy under the corresponding directory before first use. Keep the original IDs and directory layout:

| Service | Data paths relative to the project root |
| --- | --- |
| Doodle | `subsites/doodle/doodle.db` and optional `subsites/doodle/settings.json` |
| DodoBook | `subsites/dodobook/instance/dodobook.db` and `subsites/dodobook/static/uploads/` |
| Dodo TV | `subsites/DodoTV/instance/dodotv.db` and `subsites/DodoTV/uploads/` |

To attach an existing DodoBook or Dodo TV profile, first create the shared account in DodoStack. Run the appropriate command before the user opens that service for the first time. Use the local profile's numeric ID from the legacy database (`user` in DodoBook and `users` in Dodo TV).

```bash
python server.py link-profile dodobook YOUR_USERNAME 42
python server.py link-profile dodotv YOUR_USERNAME 17
```

A link preserves the old profile and its content, history, and relationships. Legacy passwords and administrator flags for these two services do not grant access to a shared account. Shared roles are assigned from DodoStack.

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

The tab and its routes appear automatically under `/admin/<service>` on the main server. Add admin actions to `endpoints` or use `prefixes=("admin.",)` for a whole admin blueprint. Use `assets` for explicitly allowed read-only dependencies such as stylesheets and media previews. Use `url_for()` for links and form actions, and include `{{ admin_navigation }}` in the service's admin template to show the shared tabs. The framework checks the current shared admin role and closes admin routes on the public service hostname.

Public routes can use `require_login` and a service profile loader with the shared account. `Identity.issue_assertion()` and `verify_assertion()` are available for short-lived, service-scoped calls.
