"""Authentication, account and watch-history routes."""
from __future__ import annotations

from dodo.web import Blueprint, abort, flash, redirect, render_template, url_for
from dodo.web import current_user, require_login, shared_signin, shared_signout

from ..extensions import db
from ..models import WatchHistory, WatchProgress
from ..services import progress_card

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/register")
def register():
    return shared_signin(signup=True)


@auth_bp.get("/login")
def login():
    return redirect(url_for("main.index")) if current_user.is_authenticated else shared_signin()


@auth_bp.post("/logout")
@require_login
def logout():
    return shared_signout()


@auth_bp.get("/profile")
@require_login
def profile():
    recent = (
        WatchProgress.query.filter_by(user_id=current_user.id)
        .order_by(WatchProgress.updated_at.desc())
        .limit(8)
        .all()
    )
    cards = [card for item in recent if (card := progress_card(item))]
    return render_template("auth/profile.html", cards=cards)


@auth_bp.route("/history")
@require_login
def history():
    entries = (
        WatchHistory.query.filter_by(user_id=current_user.id)
        .order_by(WatchHistory.watched_at.desc())
        .limit(100)
        .all()
    )
    return render_template("auth/history.html", entries=entries)


@auth_bp.post("/history/clear")
@require_login
def clear_history():
    WatchHistory.query.filter_by(user_id=current_user.id).delete()
    WatchProgress.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    flash("Watch history and playback progress cleared.", "success")
    return redirect(url_for("auth.history"))


@auth_bp.post("/history/remove/<int:history_id>")
@require_login
def remove_history(history_id: int):
    entry = db.session.get(WatchHistory, history_id)
    if not entry or entry.user_id != current_user.id:
        abort(404)
    db.session.delete(entry)
    db.session.commit()
    flash("Removed from watch history.", "success")
    return redirect(url_for("auth.history"))
