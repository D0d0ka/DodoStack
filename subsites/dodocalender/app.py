"""Personal calendars and shared event subscriptions for DodoStack."""

from calendar import Calendar, monthrange
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
import re
import secrets
import sqlite3
from zoneinfo import ZoneInfo

from holidays import country_holidays, list_supported_countries

from dodo.web import (
    WebApplication, abort, admin_only, current_user, redirect, render_template,
    request, require_login, url_for, verify_form_token,
)

app = WebApplication(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
app.admin_tab("Dodo Calender", "/settings", prefixes=("admin_",), assets=("static",))

DATABASE = Path(app.instance_path) / "calendar.sqlite3"
ENGLAND = "GB-ENG"
REPEAT = {"none": "Does not repeat", "daily": "Daily", "weekly": "Weekly",
          "monthly": "Monthly", "yearly": "Yearly"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


@contextmanager
def calendar_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            if db.execute("PRAGMA user_version").fetchone()[0] < 2:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    db.execute("""CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
                        title TEXT NOT NULL, event_date TEXT NOT NULL, event_time TEXT NOT NULL,
                        notes TEXT NOT NULL, repeat TEXT NOT NULL, step INTEGER NOT NULL,
                        until_date TEXT NOT NULL, share_token TEXT UNIQUE
                    )""")
                    db.execute("CREATE INDEX events_owner ON events(owner_id, event_date)")
                    db.execute("""CREATE TABLE event_members (
                        account_id INTEGER NOT NULL,
                        event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                        PRIMARY KEY (account_id, event_id)
                    )""")
                    db.execute("CREATE INDEX event_members_event ON event_members(event_id)")
                    db.execute("""CREATE TABLE custom_days (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                        day TEXT NOT NULL, yearly INTEGER NOT NULL CHECK (yearly IN (0, 1))
                    )""")
                    db.execute("INSERT INTO custom_days(title, day, yearly) VALUES ('Dodo Day', '07-01', 1)")
                    db.execute("CREATE TABLE holiday_countries (code TEXT PRIMARY KEY)")
                elif version == 1:
                    # The original GB option represented England only.
                    db.execute("UPDATE holiday_countries SET code = ? WHERE code = 'GB'", (ENGLAND,))
                if version < 2:
                    db.execute("PRAGMA user_version = 2")
            yield db
    finally:
        db.close()


def today():
    return datetime.now(ZoneInfo("Europe/Tallinn")).date()


def parse_date(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError
    return date.fromisoformat(value)


def read_date(value, default):
    try:
        return parse_date(value)
    except ValueError:
        return default


def shifted_month(day, offset):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    return date(year, month + 1, 1) if 1 <= year <= 9999 else None


def repeats_on(event, start, day):
    if day < start or (event["until_date"] and day.isoformat() > event["until_date"]):
        return False
    step = event["step"]
    match event["repeat"]:
        case "daily":
            return (day - start).days % step == 0
        case "weekly":
            return (day - start).days % (7 * step) == 0
        case "monthly":
            months = (day.year - start.year) * 12 + day.month - start.month
            return day.day == start.day and months % step == 0
        case "yearly":
            return (day.month, day.day) == (start.month, start.day) and (day.year - start.year) % step == 0
        case _:
            return day == start


@lru_cache(maxsize=1)
def countries():
    labels = {}
    for code in list_supported_countries(include_aliases=False):
        calendar = country_holidays(code)
        module_name = type(calendar).__module__.rsplit(".", 1)[-1]
        labels[code] = module_name.replace("_", " ").title()
    labels.update({ENGLAND: "England", "AX": "Åland Islands"})
    return dict(sorted(labels.items(), key=lambda item: item[1].casefold()))


@lru_cache(maxsize=512)
def country_calendar(code, year):
    country, _, subdivision = code.partition("-")
    years = range(max(1, year - 1), min(9999, year + 1) + 1)
    return country_holidays(country, subdiv=subdivision or None, years=years, expand=False, language="en_US")


def month_entries(db, month):
    last_day = month.replace(day=monthrange(month.year, month.month)[1])
    dates = [month.replace(day=number) for number in range(1, last_day.day + 1)]
    rows = db.execute("""SELECT id, owner_id, title, event_date, event_time, repeat, step, until_date
        FROM events WHERE event_date <= ? AND (until_date = '' OR until_date >= ?)
        AND (repeat <> 'none' OR event_date >= ?)
        AND (owner_id = ? OR id IN (SELECT event_id FROM event_members WHERE account_id = ?))
        ORDER BY event_time, title COLLATE NOCASE, id""",
        (last_day.isoformat(), month.isoformat(), month.isoformat(), current_user.id, current_user.id),
    ).fetchall()
    entries = defaultdict(list)
    for event in rows:
        start = date.fromisoformat(event["event_date"])
        kind = "event" if event["owner_id"] == current_user.id else "shared"
        for day in dates:
            if repeats_on(event, start, day):
                entries[day].append({"title": event["title"], "kind": kind,
                                     "id": event["id"], "time": event["event_time"]})
    for custom in db.execute("SELECT title, day, yearly FROM custom_days ORDER BY title, id"):
        for day in dates:
            key = day.isoformat()
            if custom["day"] == (key[5:] if custom["yearly"] else key):
                entries[day].append({"title": custom["title"], "kind": "custom"})
    labels = countries()
    for (code,) in db.execute("SELECT code FROM holiday_countries ORDER BY code"):
        if code not in labels:
            continue
        holidays = country_calendar(code, month.year)
        for day in dates:
            if day in holidays:
                entries[day].append({"title": holidays[day], "kind": "holiday", "country": labels[code]})
    return entries


@app.after_request
def calendar_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
@require_login
def calendar_view():
    current = today()
    selection = read_date(request.args.get("day", ""), current)
    month = selection.replace(day=1)
    with calendar_database() as db:
        entries = month_entries(db, month)
    weeks = [[month.replace(day=number) if number else None for number in week]
             for week in Calendar().monthdayscalendar(month.year, month.month)]
    return render_template(
        "calendar.html", current=current, selection=selection, entries=entries, weeks=weeks,
        previous=shifted_month(month, -1), following=shifted_month(month, 1),
        month_name=MONTHS[month.month - 1], day_month=MONTHS[selection.month - 1],
    )


@app.get("/jump")
@require_login
def jump():
    return redirect(url_for("calendar_view", day=read_date(request.args.get("month", "") + "-01", today())), 303)


def event_values():
    form = request.form
    title = form.get("title", "").strip()
    notes = form.get("notes", "").strip()
    start = form.get("event_date", "")
    clock = form.get("event_time", "")
    repeat = form.get("repeat", "none")
    until = form.get("until_date", "") if repeat != "none" else ""
    if not 1 <= len(title) <= 120 or len(notes) > 2000:
        raise ValueError("Enter a title of up to 120 characters and notes of up to 2,000 characters.")
    try:
        start_day = parse_date(start)
        if until and parse_date(until) < start_day:
            raise ValueError
    except ValueError:
        raise ValueError("Enter a valid date and a repeat end date on or after the start date.") from None
    if clock:
        try:
            if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", clock):
                raise ValueError
            time.fromisoformat(clock)
        except ValueError:
            raise ValueError("Enter a valid time.") from None
    try:
        step = int(form.get("step", "1")) if repeat != "none" else 1
        if repeat not in REPEAT or not 1 <= step <= 999:
            raise ValueError
    except ValueError:
        raise ValueError("Choose a repeat frequency and an interval from 1 to 999.") from None
    return title, start, clock, notes, repeat, step, until


def get_event(db, event_id, *, owned=False):
    if not 0 < event_id < 2**63:
        abort(404)
    row = db.execute("""SELECT events.* FROM events
        LEFT JOIN event_members ON events.id = event_members.event_id AND event_members.account_id = ?
        WHERE events.id = ? AND (owner_id = ? OR (NOT ? AND event_members.account_id IS NOT NULL))""",
        (current_user.id, event_id, current_user.id, owned),
    ).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/events/new", methods=("GET", "POST"))
@app.route("/events/<int:event_id>/edit", methods=("GET", "POST"))
@require_login
def event_form(event_id=None):
    error = ""
    with calendar_database() as db:
        event = get_event(db, event_id, owned=True) if event_id is not None else {
            "event_date": read_date(request.args.get("day", ""), today()).isoformat(), "repeat": "none", "step": 1,
        }
        if request.method == "POST":
            verify_form_token()
            try:
                values = event_values()
            except ValueError as exc:
                error = str(exc)
                event = request.form
            else:
                if event_id is None:
                    result = db.execute("""INSERT INTO events(owner_id, title, event_date, event_time, notes, repeat, step, until_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (current_user.id, *values))
                    event_id = result.lastrowid
                else:
                    result = db.execute("""UPDATE events SET title=?, event_date=?, event_time=?, notes=?,
                        repeat=?, step=?, until_date=? WHERE id=? AND owner_id=?""", (*values, event_id, current_user.id))
                    if result.rowcount == 0:
                        abort(404)
                return redirect(url_for("event_details", event_id=event_id), 303)
    return render_template("event_form.html", event=event, event_id=event_id, repeats=REPEAT, error=error), 400 if error else 200


@app.get("/events/<int:event_id>")
@require_login
def event_details(event_id):
    with calendar_database() as db:
        event = get_event(db, event_id)
        owned = event["owner_id"] == current_user.id
        members = db.execute("SELECT account_id FROM event_members WHERE event_id = ?", (event_id,)).fetchall() if owned else []
    accounts = request.identity.accounts
    owner = accounts.account(event["owner_id"])
    people = sorted((account["username"] for (account_id,) in members
                     if (account := accounts.account(account_id))), key=str.casefold)
    share_url = url_for("shared_event", token=event["share_token"], _external=True) if owned and event["share_token"] else ""
    selected = read_date(request.args.get("day", ""), date.fromisoformat(event["event_date"]))
    return render_template("event.html", event=event, owned=owned, people=people, owner=owner,
                           share_url=share_url, selected=selected, repeats=REPEAT)


@app.post("/events/<int:event_id>/manage")
@require_login
def manage_event(event_id):
    verify_form_token()
    action = request.form.get("action")
    with calendar_database() as db:
        if action == "leave":
            if not 0 < event_id < 2**63:
                abort(404)
            db.execute("DELETE FROM event_members WHERE event_id=? AND account_id=?", (event_id, current_user.id))
            return redirect(url_for("calendar_view"), 303)
        event = get_event(db, event_id, owned=True)
        if action == "share":
            db.execute("UPDATE events SET share_token=? WHERE id=? AND owner_id=? AND share_token IS NULL",
                       (secrets.token_urlsafe(32), event_id, current_user.id))
        elif action == "disable_link":
            db.execute("UPDATE events SET share_token=NULL WHERE id=? AND owner_id=?", (event_id, current_user.id))
        elif action == "delete":
            db.execute("DELETE FROM events WHERE id=? AND owner_id=?", (event_id, current_user.id))
            return redirect(url_for("calendar_view", day=event["event_date"]), 303)
        else:
            abort(400)
    return redirect(url_for("event_details", event_id=event_id), 303)


@app.route("/share", methods=("GET", "POST"))
@require_login
def shared_event():
    token = request.args.get("token", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        abort(404)
    with calendar_database() as db:
        if request.method == "POST":
            verify_form_token()
            db.execute("BEGIN IMMEDIATE")
        event = db.execute("SELECT * FROM events WHERE share_token=?", (token,)).fetchone()
        if event is None:
            abort(404)
        if event["owner_id"] == current_user.id:
            return redirect(url_for("event_details", event_id=event["id"]), 303)
        if request.method == "POST":
            db.execute("""INSERT INTO event_members (account_id, event_id) VALUES (?, ?)
                ON CONFLICT(account_id, event_id) DO NOTHING""", (current_user.id, event["id"]))
            return redirect(url_for("event_details", event_id=event["id"]), 303)
        added = db.execute("SELECT 1 FROM event_members WHERE event_id=? AND account_id=?",
                           (event["id"], current_user.id)).fetchone()
    owner = request.identity.accounts.account(event["owner_id"])
    return render_template("share.html", event=event, owner=owner, token=token, added=added, repeats=REPEAT)


def custom_day_value(value, yearly):
    pattern = r"([0-9]{1,2})\.([0-9]{1,2})" if yearly else r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})"
    match = re.fullmatch(pattern + r"\.?", value.strip())
    if not match:
        raise ValueError
    day, month, *years = map(int, match.groups())
    iso = date(2000 if yearly else years[0], month, day).isoformat()
    return iso[5:] if yearly else iso


def admin_page(db, *, error="", form=None):
    enabled = {row[0] for row in db.execute("SELECT code FROM holiday_countries")}
    custom_days = []
    for row in db.execute("SELECT * FROM custom_days ORDER BY yearly DESC, day, id"):
        custom_days.append(dict(row) | {"day": ".".join(reversed(row["day"].split("-")))})
    return render_template("admin.html", countries=countries(), enabled=enabled,
                           custom_days=custom_days, error=error, form=form or {})


@app.route("/settings", methods=("GET", "POST"))
@admin_only
def admin_settings():
    with calendar_database() as db:
        if request.method == "GET":
            return admin_page(db)
        verify_form_token()
        enabled = set(request.form.getlist("countries"))
        if not enabled <= countries().keys():
            abort(400)
        db.execute("DELETE FROM holiday_countries")
        db.executemany("INSERT INTO holiday_countries(code) VALUES (?)", [(code,) for code in enabled])
    return redirect(url_for("admin_settings"), 303)


@app.post("/custom-days")
@admin_only
def admin_custom_days():
    verify_form_token()
    form = request.form
    with calendar_database() as db:
        raw_id = form.get("id", "")
        if raw_id and (not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 18):
            abort(400)
        day_id = int(raw_id) if raw_id else None
        if form.get("action") == "delete":
            if day_id is None:
                abort(400)
            result = db.execute("DELETE FROM custom_days WHERE id=?", (day_id,))
        elif form.get("action") == "save":
            title = form.get("title", "").strip()
            frequency = form.get("yearly")
            if frequency not in {"0", "1"}:
                abort(400)
            yearly = frequency == "1"
            if not 1 <= len(title) <= 120:
                return admin_page(db, error="Enter a name of up to 120 characters.", form=form), 400
            try:
                day = custom_day_value(form.get("day", ""), yearly)
            except ValueError:
                example = "14.11" if yearly else "14.11.2026"
                return admin_page(db, error=f"Enter a valid date like {example}.", form=form), 400
            if day_id is None:
                db.execute("INSERT INTO custom_days(title, day, yearly) VALUES (?, ?, ?)", (title, day, yearly))
                return redirect(url_for("admin_settings"), 303)
            result = db.execute("UPDATE custom_days SET title=?, day=?, yearly=? WHERE id=?", (title, day, yearly, day_id))
        else:
            abort(400)
        if result.rowcount == 0:
            abort(404)
    return redirect(url_for("admin_settings"), 303)
