"""Private mailbox and calendar invitation responses."""

from contextlib import closing
from pathlib import Path

from dodo.pages import service_name
from dodo.web import (
    WebApplication, abort, admin_only, current_user, redirect, render_template, request,
    require_login, send_file, url_for, verify_form_token,
)
from subsites.dodocalender.app import calendar_database
from .delivery import SUPPORT_ADDRESS

app = WebApplication(__name__)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024
LOGO = Path(__file__).resolve().parent / "static" / "dodomail.png"
FOLDERS = {"inbox": "Inbox", "sent": "Sent", "archive": "Archive", "trash": "Trash"}
RECEIVED_FOLDERS = FOLDERS.keys() - {"sent"}
app.admin_tab("Support", "/support", endpoints=("support",), assets=("static",))
USER_MAIL = """SELECT mail.*, sender.dmail_address AS sender_address,
    recipient.dmail_address AS recipient_address FROM user_mail AS mail
    LEFT JOIN users AS sender ON sender.id=mail.sender_id
    LEFT JOIN users AS recipient ON recipient.id=mail.recipient_id"""



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


def mailbox(db, folder, message=None, *, personal=False, draft=None, error=""):
    account_id = current_user.id
    address = db.execute("SELECT dmail_address FROM users WHERE id=?", (account_id,)).fetchone()[0]
    unread = sum(db.execute(f"""SELECT count(*) FROM {table}
        WHERE recipient_id=? AND folder='inbox' AND is_read=0""", (account_id,)).fetchone()[0]
        for table in ("mail_messages", "user_mail"))
    messages = []
    if message is None and draft is None:
        if folder != "sent":
            for row in db.execute("""SELECT id, sender, subject, created_at, is_read FROM mail_messages
                WHERE recipient_id=? AND folder=?""", (account_id, folder)):
                messages.append(dict(row) | {"label": service_name(row["sender"])})
        condition, values = ("mail.sender_id=?", (account_id,)) if folder == "sent" else (
            "mail.recipient_id=? AND mail.folder=?", (account_id, folder))
        for row in db.execute(USER_MAIL + " WHERE " + condition, values):
            label = row["recipient_address"] if folder == "sent" else row["sender_address"]
            messages.append(dict(row) | {"label": label or SUPPORT_ADDRESS, "personal": True,
                                         "is_read": folder == "sent" or row["is_read"]})
        messages.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
    return render_template("mailbox.html", folders=FOLDERS, folder=folder, address=address,
                           unread=unread, messages=messages, message=message, sender_name=service_name,
                           personal=personal, draft=draft, error=error, support_address=SUPPORT_ADDRESS,
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
            if action in RECEIVED_FOLDERS:
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


def owned_mail(db, message_id):
    if not 0 < message_id < 2**63:
        abort(404)
    row = db.execute(USER_MAIL + " WHERE mail.id=? AND (mail.recipient_id=? OR mail.sender_id=?)",
                     (message_id, current_user.id, current_user.id)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/mail/<int:message_id>", methods=("GET", "POST"))
@require_login
def personal_message(message_id):
    sent = request.args.get("folder") == "sent"
    with closing(request.identity.accounts.connect()) as db, db:
        message = owned_mail(db, message_id)
        received = message["recipient_id"] == current_user.id
        if not received or (sent and message["sender_id"] == current_user.id):
            folder = "sent"
        else:
            folder = message["folder"]
        if request.method == "POST":
            verify_form_token()
            if folder == "sent":
                abort(404)
            action = request.form.get("action")
            if action in RECEIVED_FOLDERS:
                db.execute("UPDATE user_mail SET folder=? WHERE id=? AND recipient_id=?",
                           (action, message_id, current_user.id))
            elif action in {"read", "unread"}:
                db.execute("UPDATE user_mail SET is_read=? WHERE id=? AND recipient_id=?",
                           (action == "read", message_id, current_user.id))
            else:
                abort(400)
            return redirect(url_for("personal_message", message_id=message_id), 303)
        return mailbox(db, folder, message, personal=True)


def message_fields():
    return {key: request.form.get(key, "").strip() for key in ("recipient", "subject", "body")}


def validate_message(draft):
    if not 1 <= len(draft["subject"]) <= 200 or not 1 <= len(draft["body"]) <= 12000:
        raise ValueError("Enter a subject of up to 200 characters and a message of up to 12,000 characters.")


@app.route("/compose", methods=("GET", "POST"))
@require_login
def compose():
    with closing(request.identity.accounts.connect()) as db, db:
        if request.method == "POST":
            verify_form_token()
            draft = message_fields()
            try:
                validate_message(draft)
                recipient = draft["recipient"].lower()
                account = db.execute("SELECT id FROM users WHERE dmail_address=? AND approved=1",
                                     (recipient,)).fetchone()
                if recipient != SUPPORT_ADDRESS and not account:
                    raise ValueError("Enter an active DodoMail address.")
                db.execute("INSERT INTO user_mail(sender_id, recipient_id, subject, body) VALUES (?, ?, ?, ?)",
                           (current_user.id, account["id"] if account and recipient != SUPPORT_ADDRESS else None,
                            draft["subject"], draft["body"]))
            except ValueError as error:
                return mailbox(db, "inbox", draft=draft, error=str(error)), 400
            return redirect(url_for("inbox", folder="sent"), 303)
        draft = {"recipient": SUPPORT_ADDRESS if request.args.get("to") == "support" else "",
                 "subject": "", "body": ""}
        source = request.args.get("source", type=int)
        mode = request.args.get("mode")
        if mode in {"reply", "forward"}:
            if source is None or not 0 < source < 2**63:
                abort(404)
            if request.args.get("kind") == "service" and mode == "forward":
                row = db.execute("SELECT * FROM mail_messages WHERE id=? AND recipient_id=?",
                                 (source, current_user.id)).fetchone()
                if row is None:
                    abort(404)
                sender = service_name(row["sender"])
            else:
                row = owned_mail(db, source)
                sender = row["sender_address"] or SUPPORT_ADDRESS
            prefix = "Re: " if mode == "reply" else "Fwd: "
            subject = row["subject"]
            draft["subject"] = (subject if subject.lower().startswith(prefix.lower()) else prefix + subject)[:200]
            if mode == "reply":
                draft["recipient"] = sender if row["recipient_id"] == current_user.id else (
                    row["recipient_address"] or SUPPORT_ADDRESS)
            else:
                draft["body"] = f"Forwarded message from {sender}\n\n{row['body']}"
        return mailbox(db, "inbox", draft=draft)


@app.route("/support", methods=("GET", "POST"))
@admin_only
def support():
    with closing(request.identity.accounts.connect()) as db, db:
        messages = db.execute(USER_MAIL + " WHERE mail.recipient_id IS NULL ORDER BY mail.id DESC").fetchall()
        error, draft = "", None
        if request.method == "POST":
            verify_form_token()
            source = request.form.get("source", type=int)
            original = next((row for row in messages if row["id"] == source), None)
            if original is None:
                abort(404)
            draft = message_fields() | {"source": source}
            try:
                validate_message(draft)
                db.execute("INSERT INTO user_mail(sender_id, recipient_id, subject, body) VALUES (NULL, ?, ?, ?)",
                           (original["sender_id"], draft["subject"], draft["body"]))
            except ValueError as exc:
                error = str(exc)
            else:
                return redirect(url_for("support"), 303)
        return render_template("support.html", messages=messages, error=error, draft=draft), 400 if error else 200


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
