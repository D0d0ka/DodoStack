"""DodoKey stores a private, client-encrypted vault for each DodoStack account."""

from base64 import b64decode, b64encode
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from dodo.web import (
    WebApplication, current_user, jsonify, render_template, request,
    require_login, shared_signout, verify_form_token,
)

app = WebApplication(__name__)
app.config["MAX_CONTENT_LENGTH"] = 768 * 1024
DATABASE = Path(app.instance_path) / "vaults.sqlite3"
MAX_CIPHERTEXT = 512 * 1024


@contextmanager
def vault_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS vaults (
                account_id INTEGER PRIMARY KEY,
                revision INTEGER NOT NULL,
                version INTEGER NOT NULL,
                salt BLOB NOT NULL,
                nonce BLOB NOT NULL,
                ciphertext BLOB NOT NULL
            )""")
            yield connection
    finally:
        connection.close()


def decode_field(value, minimum, maximum):
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise ValueError
    decoded = b64decode(value, validate=True)
    if not minimum <= len(decoded) <= maximum:
        raise ValueError
    return decoded


@app.after_request
def secure_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "strict-origin"
    return response


@app.get("/")
@require_login
def index():
    return render_template("vault.html")


@app.post("/logout")
@require_login
def logout():
    verify_form_token()
    return shared_signout()


@app.get("/api/vault")
@require_login
def read_vault():
    with vault_database() as connection:
        row = connection.execute(
            "SELECT revision, version, salt, nonce, ciphertext FROM vaults WHERE account_id = ?",
            (current_user.id,),
        ).fetchone()
    if row is None:
        return jsonify(revision=0, vault=None)

    revision, version, salt, nonce, ciphertext = row
    return jsonify(revision=revision, vault={
        "version": version,
        "salt": b64encode(salt).decode("ascii"),
        "nonce": b64encode(nonce).decode("ascii"),
        "ciphertext": b64encode(ciphertext).decode("ascii"),
    })


@app.route("/api/vault", methods=("PUT",))
@require_login
def save_vault():
    verify_form_token()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid vault update."), 400

    revision, vault = data.get("revision"), data.get("vault")
    if (type(revision) is not int or not 0 <= revision < 2**53 - 1
            or not isinstance(vault, dict)
            or type(vault.get("version")) is not int or vault["version"] != 1):
        return jsonify(error="Invalid vault update."), 400
    try:
        salt = decode_field(vault.get("salt"), 16, 16)
        nonce = decode_field(vault.get("nonce"), 12, 12)
        ciphertext = decode_field(vault.get("ciphertext"), 16, MAX_CIPHERTEXT)
    except ValueError:
        return jsonify(error="Invalid encrypted vault."), 400

    with vault_database() as connection:
        if revision == 0:
            result = connection.execute(
                """INSERT INTO vaults (account_id, revision, version, salt, nonce, ciphertext)
                   VALUES (?, 1, 1, ?, ?, ?) ON CONFLICT(account_id) DO NOTHING""",
                (current_user.id, salt, nonce, ciphertext),
            )
        else:
            result = connection.execute(
                """UPDATE vaults SET revision = revision + 1, nonce = ?, ciphertext = ?
                   WHERE account_id = ? AND revision = ? AND salt = ?""",
                (nonce, ciphertext, current_user.id, revision, salt),
            )
        if result.rowcount != 1:
            return jsonify(error="The vault changed in another tab. Reopen it to load the latest entries."), 409
    return jsonify(revision=revision + 1)
