"""Plain-text mailbox delivery for trusted in-process DodoStack services."""

from contextlib import closing


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mail_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_id INTEGER NOT NULL REFERENCES users(id),
        sender TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        is_read INTEGER NOT NULL DEFAULT 0,
        folder TEXT NOT NULL DEFAULT 'inbox' CHECK (folder IN ('inbox', 'archive', 'trash')),
        event_id INTEGER,
        invitation_status TEXT CHECK (invitation_status IN ('pending', 'accepted', 'declined', 'cancelled'))
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS mail_recipient_folder
        ON mail_messages(recipient_id, folder, id DESC)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS mail_welcome
        ON mail_messages(recipient_id) WHERE sender = 'stack'""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS mail_pending_invite
        ON mail_messages(event_id, recipient_id) WHERE invitation_status = 'pending'""")


def welcome(db, account_id):
    db.execute("""INSERT INTO mail_messages(recipient_id, sender, subject, body)
        VALUES (?, 'stack', 'Welcome to DodoStack', ?)""",
        (account_id, "Your DodoMail inbox is ready. DodoStack services can send you updates and calendar invitations here."))


def deliver(request, recipient_id, subject, body, *, calendar_transaction=None, event_id=None):
    """Use the server's authenticated service context and signed user assertion."""
    identity, sender, actor = request.identity, request.service, request.shared_user
    if not actor or sender not in identity.services or "dodomail" not in identity.services:
        raise PermissionError("Mail delivery requires a registered service and signed-in account.")
    if event_id is not None and (sender != "dodocalender" or calendar_transaction is None):
        raise PermissionError("Calendar invitations must come from the calendar transaction.")
    if not 1 <= len(subject.strip()) <= 200 or not 1 <= len(body.strip()) <= 4000:
        raise ValueError("Enter a subject and message within the supported lengths.")
    token = identity.issue_assertion(sender, "dodomail", actor)
    if not identity.verify_assertion(sender, "dodomail", token):
        raise PermissionError("The sending account is unavailable.")
    if calendar_transaction is not None:
        return _insert(calendar_transaction, "mail_store.", recipient_id, sender, subject, body, event_id)
    with closing(identity.accounts.connect()) as db, db:
        return _insert(db, "", recipient_id, sender, subject, body, event_id)


def _insert(db, schema, recipient_id, sender, subject, body, event_id):
    return db.execute(f"""INSERT INTO {schema}mail_messages
        (recipient_id, sender, subject, body, event_id, invitation_status)
        SELECT id, ?, ?, ?, ?, ? FROM {schema}users WHERE id=? AND approved=1
        ON CONFLICT DO NOTHING""",
        (sender, subject, body, event_id, "pending" if event_id is not None else None, recipient_id),
    ).rowcount == 1
