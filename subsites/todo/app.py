from pathlib import Path
from functools import wraps
import os
import re
import secrets
import sqlite3

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "todos.sqlite3"
app = Flask(__name__)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


def get_secret_key():
    configured = os.environ.get("FLASK_SECRET_KEY")
    if configured:
        return configured
    key_file = DATABASE.parent / "session.key"
    key_file.parent.mkdir(exist_ok=True)
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


app.secret_key = get_secret_key()


def get_db():
    if "db" not in g:
        DATABASE.parent.mkdir(exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        todo_columns = {row[1] for row in g.db.execute("PRAGMA table_info(todos)")}
        if "user_id" not in todo_columns:
            g.db.execute("ALTER TABLE todos ADD COLUMN user_id INTEGER REFERENCES users(id)")
        g.db.commit()
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = get_db().execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone() if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view


@app.get("/signup")
@app.post("/signup")
def signup():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirmation = request.form.get("password_confirmation", "")
        error = None
        if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
            error = "Choose a username with 3–24 letters, numbers, or underscores."
        elif len(password) < 8:
            error = "Your password must be at least 8 characters long."
        elif password != confirmation:
            error = "Your passwords do not match."
        elif get_db().execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone():
            error = "That username is already taken. Try another one."

        if error:
            flash(error, "error")
        else:
            db = get_db()
            cursor = db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            db.commit()
            session.clear()
            session["user_id"] = cursor.lastrowid
            return redirect(url_for("index"))
    return render_template("auth.html", mode="signup")


@app.get("/login")
@app.post("/login")
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("The username or password is incorrect.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    selected = request.args.get("filter", "all")
    if selected not in {"all", "active", "completed"}:
        selected = "all"

    where = {
        "all": "user_id = ?",
        "active": "user_id = ? AND completed = 0",
        "completed": "user_id = ? AND completed = 1",
    }[selected]
    todos = get_db().execute(
        f"SELECT id, title, completed FROM todos WHERE {where} "
        "ORDER BY completed, id DESC",
        (g.user["id"],),
    ).fetchall()
    stats = get_db().execute(
        "SELECT COUNT(*) AS total, SUM(completed = 0) AS remaining, "
        "SUM(completed = 1) AS completed FROM todos WHERE user_id = ?",
        (g.user["id"],),
    ).fetchone()
    return render_template("index.html", todos=todos, selected=selected, stats=stats)


@app.post("/add")
@login_required
def add_todo():
    title = request.form.get("title", "").strip()
    if title:
        get_db().execute(
            "INSERT INTO todos (title, user_id) VALUES (?, ?)",
            (title[:180], g.user["id"]),
        )
        get_db().commit()
    return redirect(url_for("index"))


@app.post("/toggle/<int:todo_id>")
@login_required
def toggle_todo(todo_id):
    db = get_db()
    db.execute(
        "UPDATE todos SET completed = 1 - completed WHERE id = ? AND user_id = ?",
        (todo_id, g.user["id"]),
    )
    db.commit()
    return redirect(url_for("index", filter=request.form.get("filter", "all")))


@app.post("/delete/<int:todo_id>")
@login_required
def delete_todo(todo_id):
    db = get_db()
    db.execute("DELETE FROM todos WHERE id = ? AND user_id = ?", (todo_id, g.user["id"]))
    db.commit()
    return redirect(url_for("index", filter=request.form.get("filter", "all")))


@app.post("/clear-completed")
@login_required
def clear_completed():
    db = get_db()
    db.execute("DELETE FROM todos WHERE completed = 1 AND user_id = ?", (g.user["id"],))
    db.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
