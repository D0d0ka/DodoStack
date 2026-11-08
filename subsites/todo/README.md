# Dodo To-do

A small Flask to-do app with a Dodo-inspired look. Tasks are stored locally in SQLite at `instance/todos.sqlite3`.

Users can create an account with a username and password, sign in, and keep a private to-do list. Passwords are stored as secure hashes. A local session key is generated in `instance/session.key`; set `FLASK_SECRET_KEY` in the environment when deploying.

## Run locally

```bash
cd subsites/todo
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>. The database is created automatically on first run.
