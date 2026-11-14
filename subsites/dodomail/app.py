"""Private mailbox and calendar invitation responses."""

from contextlib import closing
from pathlib import Path

from dodo.pages import service_name
from dodo.web import (
    WebApplication, abort, current_user, redirect, render_template, request,
    require_login, send_file, url_for, verify_form_token,
)
from subsites.dodocalender.app import calendar_database

app = WebApplication(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024
LOGO = Path(__file__).resolve().parents[2] / "assets" / "dodomail.png"
FOLDERS = {"inbox": "Inbox", "archive": "Archive", "trash": "Trash"}


@app.after_request
def mail_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "strict-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/logo.png")
@require_login
def logo():
    return send_file(LOGO, mimetype="image/png")


def mailbox(db, folder, message=None):
    account_id = current_user.id
    address = db.execute("SELECT dmail_address FROM users WHERE id=?", (account_id,)).fetchone()[0]
    unread = db.execute("""SELECT count(*) FROM mail_messages
        WHERE recipient_id=? AND folder='inbox' AND is_read=0""", (account_id,)).fetchone()[0]
    messages = db.execute("""SELECT id, sender, subject, created_at, is_read FROM mail_messages
        WHERE recipient_id=? AND folder=? ORDER BY id DESC""", (account_id, folder)).fetchall()
    return render_template("mailbox.html", folders=FOLDERS, folder=folder, address=address,
                           unread=unread, messages=messages, message=message, sender_name=service_name,
                           calendar_url=request.identity.service_url("dodocalender"))


@app.get("/")
@require_login
def inbox():
    folder = request.args.get("folder", "inbox")
    if folder not in FOLDERS:
        abort(404)
    with closing(request.identity.accounts.connect()) as db:
        return mailbox(db, folder)


@app.route("/messages/<int:message_id>", methods=("GET", "POST"))
@require_login
def message_view(message_id):
    if not 0 < message_id < 2**63:
        abort(404)
    accounts = request.identity.accounts
    if request.method == "POST":
        verify_form_token()
        action = request.form.get("action")
        if action in {"accept", "decline"}:
            respond_to_invitation(accounts, message_id, action)
            return redirect(url_for("message_view", message_id=message_id), 303)
        with closing(accounts.connect()) as db, db:
            if action in FOLDERS:
                result = db.execute("UPDATE mail_messages SET folder=? WHERE id=? AND recipient_id=?",
                                    (action, message_id, current_user.id))
            elif action in {"read", "unread"}:
                result = db.execute("UPDATE mail_messages SET is_read=? WHERE id=? AND recipient_id=?",
                                    (action == "read", message_id, current_user.id))
            else:
                abort(400)
            if not result.rowcount:
                abort(404)
        return redirect(url_for("message_view", message_id=message_id), 303)
    with closing(accounts.connect()) as db:
        message = db.execute("SELECT * FROM mail_messages WHERE id=? AND recipient_id=?",
                             (message_id, current_user.id)).fetchone()
        if not message:
            abort(404)
        return mailbox(db, message["folder"], message)


def respond_to_invitation(accounts, message_id, action):
    with calendar_database(accounts=accounts) as db:
        db.execute("BEGIN IMMEDIATE")
        invite = db.execute("""SELECT event_id, invitation_status FROM mail_store.mail_messages
            WHERE id=? AND recipient_id=? AND sender='dodocalender' AND event_id IS NOT NULL""",
            (message_id, current_user.id)).fetchone()
        if not invite:
            abort(404)
        if invite["invitation_status"] != "pending":
            return
        event = db.execute("SELECT id FROM events WHERE id=?", (invite["event_id"],)).fetchone()
        if not event:
            status = "cancelled"
        elif action == "accept":
            db.execute("""INSERT INTO event_members (account_id, event_id) VALUES (?, ?)
                ON CONFLICT(account_id, event_id) DO NOTHING""", (current_user.id, event["id"]))
            status = "accepted"
        else:
            status = "declined"
        db.execute("""UPDATE mail_store.mail_messages SET invitation_status=?, is_read=1
            WHERE id=? AND recipient_id=?""", (status, message_id, current_user.id))
