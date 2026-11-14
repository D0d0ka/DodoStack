"""DodoTodo task planning on the shared DodoStack identity."""
from calendar import monthrange
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from dodo.framework import Application, Response, redirect, require_user, safe

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "todos.sqlite3"
app = Application("todo", static_dir=BASE_DIR / "static")
VIEWS = {
    "inbox": "Inbox", "today": "Today", "upcoming": "Upcoming",
    "all": "All tasks", "completed": "Completed", "search": "Search",
    "project": "Project", "label": "Label",
}
REPEATS = {"": "Never", "daily": "Daily", "weekly": "Weekly",
           "monthly": "Monthly", "yearly": "Yearly"}
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, user_id INTEGER NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE todo_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
        UNIQUE(user_id, name COLLATE NOCASE))""",
    """CREATE TABLE todo_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        project_id INTEGER REFERENCES todo_projects(id) ON DELETE CASCADE,
        name TEXT NOT NULL)""",
    """CREATE UNIQUE INDEX todo_section_names
        ON todo_sections(user_id, coalesce(project_id, 0), name COLLATE NOCASE)""",
    """CREATE TABLE todo_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL,
        criteria TEXT NOT NULL, UNIQUE(user_id, name COLLATE NOCASE))""",
)
TASK_COLUMNS = {
    "description": "TEXT NOT NULL DEFAULT ''",
    "project_id": "INTEGER REFERENCES todo_projects(id) ON DELETE SET NULL",
    "section_id": "INTEGER REFERENCES todo_sections(id) ON DELETE SET NULL",
    "parent_id": "INTEGER REFERENCES todos(id) ON DELETE SET NULL",
    "due": "TEXT NOT NULL DEFAULT ''",
    "priority": "INTEGER NOT NULL DEFAULT 4",
    "repeat": "TEXT NOT NULL DEFAULT ''",
    "repeat_anchor": "TEXT NOT NULL DEFAULT ''",
    "labels": "TEXT NOT NULL DEFAULT '[]'",
}


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        if not db.execute("PRAGMA user_version").fetchone()[0]:
            with db:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute("PRAGMA user_version").fetchone()[0]:
                    for statement in SCHEMA:
                        db.execute(statement)
                    for name, definition in TASK_COLUMNS.items():
                        db.execute(f"ALTER TABLE todos ADD COLUMN {name} {definition}")
                    db.execute("CREATE INDEX todo_account ON todos(user_id, completed)")
                    db.execute("CREATE INDEX todo_parent ON todos(parent_id)")
                    db.execute("PRAGMA user_version=1")
    except Exception:
        db.close()
        raise
    return db


def account_id(request):
    return request.user["id"]


def id_value(value):
    return int(value) if value.isascii() and value.isdigit() and 0 < len(value) <= 18 else None


def owned(db, table, user_id, item_id):
    return db.execute(f"SELECT * FROM {table} WHERE user_id=? AND id=?",
                      (user_id, item_id)).fetchone()


def criteria(request):
    view = request.value("view")
    if view not in VIEWS:
        view = {"all": "all", "active": "all", "completed": "completed"}.get(
            request.value("filter"), "inbox")
    return {"view": view, "project": id_value(request.value("project")) if view == "project" else None,
            "label": request.value("label")[:40] if view == "label" else "",
            "q": request.value("q").strip()[:180] if view == "search" else "",
            "p": request.value("p") if view == "search" and request.value("p") in {"1", "2", "3", "4"} else ""}


def url(**values):
    query = urlencode({key: value for key, value in values.items() if value})
    return "/?" + query if query else "/"


def hidden(**values):
    return "".join(f'<input type="hidden" name="{key}" value="{safe(value)}">'
                   for key, value in values.items() if value is not None)


def post(request, action, button, **fields):
    return (f'<form method="post" action="{action}">'
            f'{hidden(csrf=request.csrf, **fields)}{button}</form>')


def option(value, title, current):
    selected = " selected" if value == current else ""
    return f'<option value="{safe(value)}"{selected}>{safe(title)}</option>'


def page(request, title, sidebar, content, status=200):
    signout = post(request, "/logout", '<button type="submit">Sign out</button>')
    response = Response.html(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe(title)} · DodoTodo</title><link rel="icon" href="/static/images/dodo_sign.png">
<link rel="stylesheet" href="/static/css/style.css"></head>
<body><div class="shell"><aside class="sidebar">
<a class="brand" href="/"><img src="/static/images/dodo_sign.png" alt="">DodoTodo</a>
<a class="workspace-link" href="{safe(request.workspace_url)}/">← DodoStack</a>
{sidebar}<footer>
<span>{safe(request.user["username"])}</span>{signout}</footer></aside>
<main>{content}</main></div></body></html>''', status)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "strict-origin"})
    return response


def error(request, message, status=400):
    return page(request, "DodoTodo", "", f'<h1>{safe(message)}</h1><a href="/">Back to Inbox</a>', status)


def task_form(request, projects, sections, state, task=None, *, parent=None,
              project=None, section=None, default_due="", default_label=""):
    values = dict(task) if task is not None else {}
    task_id = values.get("id")
    project = values.get("project_id", project)
    section = values.get("section_id", section)
    parent = values.get("parent_id", parent)
    location = f'{project or 0}:{section or 0}'
    locations = []
    for pid, name in [(None, "Inbox"), *((row["id"], row["name"]) for row in projects)]:
        locations.append(option(f'{pid or 0}:0', name, location))
        locations.extend(option(f'{pid or 0}:{row["id"]}', f'{name} / {row["name"]}', location)
                         for row in sections if row["project_id"] == pid)
    location_field = "" if parent else f'<label>Location<select name="location">{"".join(locations)}</select></label>'
    priorities = "".join(option(p, f'P{p}', values.get("priority", 4)) for p in range(1, 5))
    repeats = "".join(option(k, v, values.get("repeat", "")) for k, v in REPEATS.items())
    action = f'/edit/{task_id}' if task_id else '/add'
    label_list = ", ".join(json.loads(values["labels"])) if task_id else default_label
    return f'''<form class="task-editor" method="post" action="{action}">
{hidden(csrf=request.csrf, parent=parent, **state)}
<label>Task name<input name="title" maxlength="180" value="{safe(values.get("title", ""))}" required></label>
<label>Description<textarea name="description" maxlength="4000" rows="2">{safe(values.get("description", ""))}</textarea></label>
<div class="fields">{location_field}
<label>Date<input name="due" type="date" value="{safe(values.get("due", default_due))}"></label>
<label>Priority<select name="priority">{priorities}</select></label>
<label>Repeat<select name="repeat">{repeats}</select></label>
<label>Labels<input name="labels" maxlength="300" value="{safe(label_list)}" placeholder="work, errands"></label></div>
<button class="primary" type="submit">{'Save changes' if task_id else 'Add task'}</button></form>'''


def task_row(request, task, projects, sections, state, names, today, children=()):
    tid = task["id"]
    done = task["completed"]
    title = safe(task["title"])
    mark = "Reopen" if done else "Complete"
    repeat = f'<span>{REPEATS[task["repeat"]]}</span>' if task["repeat"] else ""
    due_class = " overdue" if task["due"] and task["due"] < today and not done else ""
    due = f'<time class="date{due_class}" datetime="{task["due"]}">{task["due"]}</time>' if task["due"] else ""
    labels = "".join(f'<a href="{safe(url(view="label", label=label))}">@{safe(label)}</a>'
                     for label in json.loads(task["labels"]))
    project = names.get(task["project_id"], "Inbox")
    project_url = url(view="project", project=task["project_id"]) if task["project_id"] else "/"
    description = f'<p class="description">{safe(task["description"])}</p>' if task["description"] else ""
    toggle = post(request, f'/toggle/{tid}',
                  f'<button class="check p{task["priority"]}" type="submit" aria-label="{mark} {title}" title="{mark}">{ "✓" if done else "" }</button>',
                  **state, expected=done, occurrence=task["due"] or "-")
    delete = post(request, f'/delete/{tid}', '<button class="danger" type="submit">Delete task</button>', **state)
    edit = task_form(request, projects, sections, state, task)
    subtask = ""
    if not task["parent_id"] and not done:
        subtask = ('<details><summary>Add subtask</summary>'
                   + task_form(request, projects, sections, state, parent=tid) + '</details>')
    nested = "".join(task_row(request, child, projects, sections, state, names, today)
                     for child in children)
    return f'''<li class="task{' done' if done else ''}"><div class="task-line">{toggle}
<div class="task-content"><details><summary>{title}</summary>{description}{edit}{subtask}{delete}</details>
<div class="metadata">{due}{repeat}<a href="{safe(project_url)}">{safe(project)}</a>{labels}</div></div></div>
{f'<ul class="tasks children">{nested}</ul>' if nested else ''}</li>'''


@app.route("/")
@require_user
def index(request):
    user = account_id(request)
    state = criteria(request)
    saved_id = id_value(request.value("saved"))
    with closing(get_db()) as db:
        projects = db.execute("SELECT * FROM todo_projects WHERE user_id=? ORDER BY name COLLATE NOCASE", (user,)).fetchall()
        sections = db.execute("SELECT * FROM todo_sections WHERE user_id=? ORDER BY id", (user,)).fetchall()
        filters = db.execute("SELECT * FROM todo_filters WHERE user_id=? ORDER BY name COLLATE NOCASE", (user,)).fetchall()
        tasks = db.execute("""SELECT * FROM todos WHERE user_id=?
            ORDER BY due='', due, priority, id""", (user,)).fetchall()
    names = {row["id"]: row["name"] for row in projects}
    saved = next((row for row in filters if row["id"] == saved_id), None)
    if saved:
        state = json.loads(saved["criteria"])
    view, pid, label, query, priority = (state[k] for k in ("view", "project", "label", "q", "p"))
    if view == "project" and pid not in names:
        return error(request, "Project not found", 404)
    title = saved["name"] if saved else names[pid] if view == "project" else f"@{label}" if view == "label" else VIEWS[view]
    layout = request.value("layout")
    state = dict(state, saved=saved_id if saved else None, layout="board" if layout == "board" else "")
    today = date.today().isoformat()

    def matches(task):
        if bool(task["completed"]) != (view == "completed"):
            return False
        if view == "inbox":
            return task["project_id"] is None
        if view == "project":
            return task["project_id"] == pid
        if view == "today":
            return bool(task["due"]) and task["due"] <= today
        if view == "upcoming":
            return task["due"] > today
        if view == "label":
            return label.casefold() in {value.casefold() for value in json.loads(task["labels"])}
        if view == "search":
            haystack = f'{task["title"]} {task["description"]} {" ".join(json.loads(task["labels"]))}'
            return query.casefold() in haystack.casefold() and (not priority or priority == str(task["priority"]))
        return True

    active = [task for task in tasks if matches(task)]
    visible = {task["id"] for task in active}
    nested = {}
    groups = {}
    for task in active:
        if view in {"project", "inbox", "all", "completed"} and task["parent_id"] in visible:
            nested.setdefault(task["parent_id"], []).append(task)
            continue
        key = None
        if view in {"inbox", "project"}:
            key = task["section_id"]
        elif view == "upcoming":
            key = task["due"]
        elif view == "today" and task["due"] < today:
            key = "Overdue"
        groups.setdefault(key, []).append(task)

    def nav(caption, selected=False, **params):
        current = ' aria-current="page"' if selected else ""
        return f'<a href="{safe(url(**params))}"{current}>{safe(caption)}</a>'

    links = "".join(nav(VIEWS[key], view == key and not saved, view=key)
                    for key in ("inbox", "today", "upcoming", "all", "completed", "search"))
    project_links = "".join(nav(row["name"], view == "project" and pid == row["id"],
                                view="project", project=row["id"]) for row in projects)
    labels = {}
    for task in tasks:
        for value in json.loads(task["labels"]):
            labels.setdefault(value.casefold(), value)
    label_links = "".join(nav("@" + value, view == "label" and key == label.casefold(), view="label", label=value)
                          for key, value in sorted(labels.items()))
    filter_links = "".join(nav(row["name"], saved_id == row["id"], saved=row["id"]) for row in filters)
    create_project = named_form(request, "/projects", "Project name", "Add project")
    sidebar = f'''<nav aria-label="Task views">{links}</nav>
<div class="side-group"><h2>Projects</h2><nav aria-label="Projects">{project_links}</nav>
<details><summary>Add project</summary>{create_project}</details></div>
{f'<div class="side-group"><h2>Filters</h2><nav aria-label="Saved filters">{filter_links}</nav></div>' if filters else ''}
{f'<div class="side-group"><h2>Labels</h2><nav aria-label="Labels">{label_links}</nav></div>' if labels else ''}'''

    actions = search_tools(request, state, saved) if view == "search" else ""
    section_names = {row["id"]: row["name"] for row in sections if row["project_id"] == pid}
    board = view in {"inbox", "project"} and layout == "board"
    if view in {"inbox", "project"}:
        actions += nav("List view" if board else "Board view", **dict(state, layout="" if board else "board"))
        actions += ('<details><summary>Add section</summary>'
                    + named_form(request, "/sections", "Section name", "Add section", state) + '</details>')
    if view == "project":
        rename = named_form(request, f"/projects/{pid}", "Project name", "Save name", state, title)
        delete = post(request, f"/projects/{pid}", '<button class="danger" type="submit">Delete project and move tasks to Inbox</button>', operation="delete")
        actions += f'<details><summary>Project settings</summary>{rename}{delete}</details>'
    if view == "completed" and active:
        clear = post(request, "/clear-completed", '<button class="danger" type="submit">Delete all completed tasks</button>')
        actions += f'<details><summary>Clear completed</summary>{clear}</details>'
    add = ""
    if view != "completed":
        default_due = today if view == "today" else (date.today() + timedelta(days=1)).isoformat() if view == "upcoming" else ""
        add = ('<details class="add"><summary>＋ Add task</summary>'
               + task_form(request, projects, sections, state, project=pid, default_due=default_due, default_label=label)
               + '</details>')

    if view in {"inbox", "project"}:
        keys = [None, *section_names]
    elif view == "today":
        keys = [key for key in ("Overdue", None) if key in groups]
    else:
        keys = list(groups)
    content = []
    for key in keys:
        section_title = section_names.get(key, key)
        if key is None and board:
            section_title = "Tasks"
        heading = f'<h2>{safe(section_title)}</h2>' if section_title else ""
        settings = ""
        if key in section_names:
            rename = named_form(request, f"/sections/{key}", "Section name", "Save name", state, section_title)
            remove = post(request, f"/sections/{key}", '<button class="danger" type="submit">Delete section and keep tasks</button>',
                          operation="delete", **state)
            add_to_section = task_form(request, projects, sections, state, project=pid, section=key)
            settings = (f'<details class="section-settings"><summary>Section settings</summary>{rename}{remove}</details>'
                        f'<details class="section-settings"><summary>＋ Add task</summary>{add_to_section}</details>')
        rows = "".join(task_row(request, task, projects, sections, state, names, today, nested.get(task["id"], ()))
                       for task in groups.get(key, ()))
        if rows or heading:
            content.append(f'<section class="group">{heading}{settings}<ul class="tasks">{rows}</ul></section>')
    empty = '<p class="empty">No tasks in this view.</p>' if not active else ""
    return page(request, title, sidebar, f'''<header class="view-header"><h1>{safe(title)}</h1>
<div class="view-actions">{actions}</div></header>{add}{empty}
<div class="{'board' if board else 'list'}">{"".join(content)}</div>''')


def named_form(request, action, label, button, state=None, value=""):
    return f'''<form class="named-form" method="post" action="{action}">
{hidden(csrf=request.csrf, **(state or {}))}
<label>{label}<input name="name" maxlength="80" value="{safe(value)}" required></label>
<button type="submit">{button}</button></form>'''


def search_tools(request, state, saved):
    priorities = option("", "Any priority", state["p"])
    priorities += "".join(option(str(p), f"P{p}", state["p"]) for p in range(1, 5))
    search = f'''<form class="search" method="get" action="/">
{hidden(view="search")}<label>Search tasks<input name="q" type="search" maxlength="180" value="{safe(state["q"])}"></label>
<label>Priority<select name="p">{priorities}</select></label><button type="submit">Search</button></form>'''
    if saved:
        return search + post(request, f'/filters/{saved["id"]}', '<button class="danger" type="submit">Delete filter</button>')
    return search + ('<details><summary>Save filter</summary>'
                     + named_form(request, "/filters", "Filter name", "Save filter", state) + '</details>')


def task_return(request):
    return redirect(url(**criteria(request), saved=id_value(request.value("saved")),
                        layout="board" if request.value("layout") == "board" else ""))


def form_date(value):
    if not value:
        return ""
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use a valid date.")
    return value


def task_fields(request):
    title = request.value("title").strip()
    repeat = request.value("repeat")
    due = form_date(request.value("due"))
    priority = request.value("priority", "4")
    if not title or repeat not in REPEATS or (repeat and not due) or priority not in {"1", "2", "3", "4"}:
        raise ValueError("Enter a task name and choose a date for any repeat.")
    labels = {}
    for raw in request.value("labels")[:300].split(","):
        label = raw.strip().lstrip("@")[:40].strip()
        if label:
            labels.setdefault(label.casefold(), label)
    return (title[:180], request.value("description").strip()[:4000],
            due, int(priority), repeat, json.dumps(list(labels.values()), ensure_ascii=False))


def task_location(db, request, user, parent):
    if parent:
        return parent["project_id"], parent["section_id"]
    project, sep, section = request.value("location", "0:0").partition(":")
    if not sep or any(value != "0" and not id_value(value) for value in (project, section)):
        raise ValueError("Choose a valid task location.")
    project_id, section_id = int(project) or None, int(section) or None
    if project_id and not owned(db, "todo_projects", user, project_id):
        raise ValueError("Choose one of your projects.")
    if section_id:
        row = owned(db, "todo_sections", user, section_id)
        if not row or row["project_id"] != project_id:
            raise ValueError("Choose a section from this project.")
    return project_id, section_id


@app.route("/add", ("POST",))
@app.route("/edit/<int:todo_id>", ("POST",))
@require_user
def save_task(request, todo_id=None):
    user = account_id(request)
    try:
        fields = task_fields(request)
    except ValueError:
        return error(request, "Enter a valid task name, date and repeat.")
    due, repeat = fields[2], fields[4]
    with closing(get_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        task = owned(db, "todos", user, todo_id)
        if todo_id is not None and not task:
            return error(request, "Task not found", 404)
        parent_id = task["parent_id"] if task else id_value(request.value("parent"))
        parent = owned(db, "todos", user, parent_id)
        if parent_id and (not parent or parent["parent_id"] or (not task and parent["completed"])):
            return error(request, "Parent task not available", 404)
        try:
            project, section = task_location(db, request, user, parent)
        except ValueError as exc:
            return error(request, str(exc))
        anchor = ""
        if repeat:
            anchor = task["repeat_anchor"] if task and task["due"] == due and task["repeat"] == repeat else due
        if task:
            db.execute("""UPDATE todos SET title=?,description=?,due=?,priority=?,repeat=?,labels=?,
                project_id=?,section_id=?,repeat_anchor=? WHERE id=? AND user_id=?""",
                       (*fields, project, section, anchor, todo_id, user))
            if project != task["project_id"] or section != task["section_id"]:
                db.execute("UPDATE todos SET project_id=?,section_id=? WHERE parent_id=? AND user_id=?",
                           (project, section, todo_id, user))
        else:
            db.execute("""INSERT INTO todos(title,description,due,priority,repeat,labels,
                project_id,section_id,repeat_anchor,parent_id,user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (*fields, project, section, anchor, parent_id, user))
    return task_return(request)


def next_due(task, today):
    due = date.fromisoformat(task["due"])
    anchor = date.fromisoformat(task["repeat_anchor"])
    threshold = max(today, due)
    repeat = task["repeat"]
    if repeat in {"daily", "weekly"}:
        step = 1 if repeat == "daily" else 7
        return due + timedelta(days=((threshold - due).days // step + 1) * step)
    step = 1 if repeat == "monthly" else 12
    elapsed = (threshold.year - due.year) * 12 + threshold.month - due.month
    offset = max(step, elapsed // step * step)
    while True:
        year, month = divmod(due.year * 12 + due.month - 1 + offset, 12)
        candidate = date(year, month + 1, min(anchor.day, monthrange(year, month + 1)[1]))
        if candidate > threshold:
            return candidate
        offset += step


@app.route("/toggle/<int:todo_id>", ("POST",))
@require_user
def toggle_todo(request, todo_id):
    user = account_id(request)
    with closing(get_db()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        task = owned(db, "todos", user, todo_id)
        if not task:
            return error(request, "Task not found", 404)
        expected = request.value("expected")
        occurrence = request.value("occurrence")
        if ((expected and expected != str(task["completed"]))
                or (occurrence and occurrence != (task["due"] or "-"))):
            return task_return(request)
        if task["repeat"] and not task["completed"]:
            try:
                due = next_due(task, date.today()).isoformat()
            except (ValueError, OverflowError):
                return error(request, "Change the date to schedule another occurrence.")
            db.execute("UPDATE todos SET due=? WHERE id=? AND user_id=?", (due, todo_id, user))
        else:
            db.execute("UPDATE todos SET completed=1-completed WHERE id=? AND user_id=?", (todo_id, user))
    return task_return(request)


@app.route("/delete/<int:todo_id>", ("POST",))
@require_user
def delete_todo(request, todo_id):
    with closing(get_db()) as db, db:
        db.execute("DELETE FROM todos WHERE id=? AND user_id=?", (todo_id, account_id(request)))
    return task_return(request)


@app.route("/clear-completed", ("POST",))
@require_user
def clear_completed(request):
    with closing(get_db()) as db, db:
        db.execute("DELETE FROM todos WHERE completed=1 AND user_id=?", (account_id(request),))
    return redirect(url(view="completed"))


@app.route("/projects", ("POST",))
@app.route("/projects/<int:item_id>", ("POST",))
@require_user
def save_project(request, item_id=None):
    user = account_id(request)
    name = request.value("name").strip()[:80]
    try:
        with closing(get_db()) as db, db:
            if item_id is not None and request.value("operation") == "delete":
                db.execute("DELETE FROM todo_projects WHERE id=? AND user_id=?", (item_id, user))
                return redirect("/")
            if not name:
                return error(request, "Enter a project name.")
            if item_id is None:
                item_id = db.execute("INSERT INTO todo_projects(user_id,name) VALUES (?,?)",
                                     (user, name)).lastrowid
            elif not db.execute("UPDATE todo_projects SET name=? WHERE id=? AND user_id=?",
                                (name, item_id, user)).rowcount:
                return error(request, "Project not found", 404)
    except sqlite3.IntegrityError:
        return error(request, "A project with that name already exists.")
    return redirect(url(view="project", project=item_id))


@app.route("/sections", ("POST",))
@app.route("/sections/<int:item_id>", ("POST",))
@require_user
def save_section(request, item_id=None):
    user = account_id(request)
    name = request.value("name").strip()[:80]
    project = criteria(request)["project"]
    try:
        with closing(get_db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if item_id is not None and request.value("operation") == "delete":
                db.execute("DELETE FROM todo_sections WHERE id=? AND user_id=?", (item_id, user))
                return task_return(request)
            if not name:
                return error(request, "Enter a section name.")
            if item_id is None:
                if project and not owned(db, "todo_projects", user, project):
                    return error(request, "Project not found", 404)
                db.execute("INSERT INTO todo_sections(user_id,project_id,name) VALUES (?,?,?)",
                           (user, project, name))
            elif not db.execute("UPDATE todo_sections SET name=? WHERE id=? AND user_id=?",
                                (name, item_id, user)).rowcount:
                return error(request, "Section not found", 404)
    except sqlite3.IntegrityError:
        return error(request, "A section with that name already exists here.")
    return task_return(request)


@app.route("/filters", ("POST",))
@require_user
def create_filter(request):
    state = criteria(request)
    name = request.value("name").strip()[:80]
    if state["view"] != "search" or not name:
        return error(request, "Enter a name for the search filter.")
    try:
        with closing(get_db()) as db, db:
            saved = db.execute("INSERT INTO todo_filters(user_id,name,criteria) VALUES (?,?,?)",
                               (account_id(request), name, json.dumps(state))).lastrowid
    except sqlite3.IntegrityError:
        return error(request, "A filter with that name already exists.")
    return redirect(url(saved=saved))


@app.route("/filters/<int:item_id>", ("POST",))
@require_user
def delete_filter(request, item_id):
    with closing(get_db()) as db, db:
        db.execute("DELETE FROM todo_filters WHERE id=? AND user_id=?", (item_id, account_id(request)))
    return redirect(url(view="search"))


@app.route("/logout", ("POST",))
@require_user
def logout(request):
    request.delete_cookie(request.identity.service_session)
    return request.identity.sign_out_service(request.user)
