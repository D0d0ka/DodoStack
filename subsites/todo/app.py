"""Dodo Todo service, implemented with the Dodo web framework."""
from pathlib import Path
import sqlite3

from dodo.framework import Application, Request, Response, redirect, require_user, safe

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "todos.sqlite3"
app = Application("todo", static_dir=BASE_DIR / "static")


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, user_id INTEGER NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    db.commit()
    return db


def page_start(title: str, user: dict, workspace_url: str, csrf_field: str):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#f8f7ef"><title>{safe(title)} — Dodo To-do</title><link rel="icon" type="image/png" href="/static/images/dodo_sign.png"><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap" rel="stylesheet"><link rel="stylesheet" href="/static/css/style.css"></head><body><div class="page-shell"><header class="topbar"><a class="brand" href="/" aria-label="Dodo To-do home"><img src="/static/images/dodo_sign.png" alt=""><span>dodo<span class="brand-light">list</span></span></a><div class="account-tools"><a class="workspace-link" href="{safe(workspace_url)}/">Workspace</a><span class="account-name">{safe(user['username'])}</span><form action="/logout" method="post">{csrf_field}<button class="signout-button" type="submit">Sign out</button></form></div></header>'''


def page_end():
    return '<div class="decor decor-left" aria-hidden="true">✳</div><div class="decor decor-right" aria-hidden="true">✦</div></div></body></html>'


@app.route("/")
@require_user
def index(request: Request):
    user = request.user
    csrf_field = f'<input type="hidden" name="csrf" value="{safe(request.csrf)}">'
    selected = request.value("filter", "all")
    if selected not in {"all", "active", "completed"}:
        selected = "all"
    conditions = {"all": "", "active": " AND completed=0", "completed": " AND completed=1"}
    db = get_db()
    todos = db.execute("SELECT id,title,completed FROM todos WHERE user_id=?" + conditions[selected] + " ORDER BY completed,id DESC", (user["id"],)).fetchall()
    stats = db.execute(
        "SELECT COUNT(*) total, SUM(completed=0) remaining, SUM(completed=1) completed FROM todos WHERE user_id=?",
        (user["id"],),
    ).fetchone()
    total, remaining, completed = stats["total"], stats["remaining"] or 0, stats["completed"] or 0
    filters = "".join(f'<a class="filter-link {"active" if selected == name else ""}" href="/?filter={name}" {"aria-current=page" if selected == name else ""}>{label}{f"<span class=filter-count>{total}</span>" if name == "all" else ""}</a>' for name, label in [("all", "All"), ("active", "To do"), ("completed", "Done")])
    task_rows = "".join(f'''<li class="task-row {"is-done" if todo['completed'] else ""}"><form action="/toggle/{todo['id']}" method="post" class="task-main">{csrf_field}<input type="hidden" name="filter" value="{selected}"><button class="check-button" type="submit" aria-label="Mark as {'to do' if todo['completed'] else 'done'}: {safe(todo['title'])}">{'<span>✓</span>' if todo['completed'] else ''}</button><span class="task-title">{safe(todo['title'])}</span></form><form action="/delete/{todo['id']}" method="post" class="delete-form">{csrf_field}<input type="hidden" name="filter" value="{selected}"><button class="delete-button" type="submit" aria-label="Delete task" title="Delete task">×</button></form></li>''' for todo in todos)
    if not todos:
        empty = {"completed": ("No finished tasks yet", "Complete a task and it’ll show up here."), "active": ("All caught up!", "Nothing on your list right now. Enjoy the breathing room."), "all": ("Your list is a blank canvas", "Add a task above and make today yours.")}[selected]
        tasks = f'<div class="empty-state"><div class="empty-art" aria-hidden="true"><span>✳</span><i>✓</i></div><h3>{empty[0]}</h3><p>{empty[1]}</p></div>'
    else:
        tasks = f'<ul class="task-list">{task_rows}</ul>'
    clear = f'<form action="/clear-completed" method="post">{csrf_field}<button class="clear-button" type="submit">Clear done</button></form>' if completed else ""
    content = f'''<main><section class="intro"><div class="eyebrow"><span class="sparkle">✳</span> YOUR DAY, YOUR PACE</div><h1>Make room for<br><span>what matters.</span></h1><p>One small step at a time. You’ve got this.</p></section><section class="todo-card" aria-label="To-do list"><div class="card-topline"><div><span class="section-label">YOUR LIST</span><h2>{safe(user['username'])}’s list <span class="sun">☀</span></h2></div><div class="progress-badge"><span class="progress-icon">✓</span>{completed} done</div></div><form class="add-form" action="/add" method="post">{csrf_field}<label class="visually-hidden" for="new-task">Add a task</label><span class="add-plus" aria-hidden="true">+</span><input id="new-task" name="title" type="text" maxlength="180" placeholder="What would you like to do?" autocomplete="off" required><button type="submit">Add task <span aria-hidden="true">↗</span></button></form><div class="list-toolbar"><nav class="filters" aria-label="Filter tasks">{filters}</nav>{clear}</div>{tasks}<footer class="card-footer"><span><span class="footer-sparkle">✦</span> Small steps count.</span><span>{remaining} {'task' if remaining == 1 else 'tasks'} left</span></footer></section><p class="closing-note">Made for the things that make a day <span>yours.</span></p></main>'''
    db.close()
    response = Response.html(page_start("Make room for what matters", user, request.workspace_url, csrf_field) + content + page_end())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/add", ("POST",))
@require_user
def add_todo(request: Request):
    title = request.value("title").strip()
    if title:
        db = get_db()
        db.execute("INSERT INTO todos(title,user_id) VALUES (?,?)", (title[:180], request.user["id"]))
        db.commit()
        db.close()
    return redirect("/")


@app.route("/toggle/<int:todo_id>", ("POST",))
@require_user
def toggle_todo(request: Request, todo_id: int):
    db = get_db()
    db.execute("UPDATE todos SET completed=1-completed WHERE id=? AND user_id=?", (todo_id, request.user["id"]))
    db.commit()
    db.close()
    selected = request.value("filter", "all")
    return redirect("/?filter=" + (selected if selected in {"all", "active", "completed"} else "all"))


@app.route("/delete/<int:todo_id>", ("POST",))
@require_user
def delete_todo(request: Request, todo_id: int):
    db = get_db()
    db.execute("DELETE FROM todos WHERE id=? AND user_id=?", (todo_id, request.user["id"]))
    db.commit()
    db.close()
    selected = request.value("filter", "all")
    return redirect("/?filter=" + (selected if selected in {"all", "active", "completed"} else "all"))


@app.route("/clear-completed", ("POST",))
@require_user
def clear_completed(request: Request):
    db = get_db()
    db.execute("DELETE FROM todos WHERE completed=1 AND user_id=?", (request.user["id"],))
    db.commit()
    db.close()
    return redirect("/")


@app.route("/logout", ("POST",))
@require_user
def logout(request: Request):
    request.delete_cookie(request.identity.service_session)
    return request.identity.sign_out_service(request.user)
