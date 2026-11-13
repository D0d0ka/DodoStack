"""
Dodobook – A private social network for friends.
"""
from dodo.web import (
    WebApplication, UserMixin, render_template, jsonify, request, redirect,
    url_for, flash, send_from_directory, current_user, require_login, shared_admin, abort,
    shared_signin, shared_signout, verify_form_token,
)
from dodo.orm import Database, Profiles
from werkzeug.utils import secure_filename
from markupsafe import Markup, escape as html_escape
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import os
import posixpath
import re
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

# ── App configuration ─────────────────────────────────────────────────────────

app = WebApplication(__name__)
app.before_request(verify_form_token)


app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///dodobook.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB (supports video uploads)

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv', 'm4v'}

db = Database(app)


# ── Models ────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50, collation='NOCASE'), unique=True, nullable=False)
    bio = db.Column(db.Text, default='')
    profile_pic = db.Column(db.String(256), default='')
    role = db.Column(db.String(20), default='user')      # user | moderator | admin
    last_seen = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sent_friend_requests = db.relationship(
        'Friendship', foreign_keys='Friendship.requester_id', backref='requester', lazy='dynamic')
    received_friend_requests = db.relationship(
        'Friendship', foreign_keys='Friendship.addressee_id', backref='addressee', lazy='dynamic')
    group_memberships = db.relationship(
        'GroupMember', foreign_keys='GroupMember.user_id', backref='user', lazy='dynamic')
    posts = db.relationship(
        'Post', foreign_keys='Post.user_id', backref='author', lazy='dynamic')
    likes = db.relationship(
        'Like', foreign_keys='Like.user_id', backref='user', lazy='dynamic')
    comments = db.relationship(
        'Comment', foreign_keys='Comment.user_id', backref='commenter', lazy='dynamic')
    sent_messages = db.relationship(
        'DirectMessage', foreign_keys='DirectMessage.sender_id', backref='sender', lazy='dynamic')
    received_messages = db.relationship(
        'DirectMessage', foreign_keys='DirectMessage.receiver_id', backref='receiver', lazy='dynamic')
    group_messages = db.relationship(
        'GroupMessage', foreign_keys='GroupMessage.user_id', backref='sender', lazy='dynamic')

    @property
    def display_name(self):
        return '[deleted user]' if self.is_deleted else self.username

    @property
    def is_online(self):
        if not self.last_seen:
            return False
        return (datetime.utcnow() - self.last_seen).total_seconds() < 300  # 5 min

    def profile_pic_url(self):
        if self.is_deleted or not self.profile_pic:
            return url_for('static', filename='default-avatar.svg')
        return url_for('static', filename=self.profile_pic)

    def get_friends(self):
        sent = (db.session.query(User)
                .join(Friendship, Friendship.addressee_id == User.id)
                .filter(Friendship.requester_id == self.id,
                        Friendship.status == 'accepted',
                        User.is_deleted == False)
                .all())
        received = (db.session.query(User)
                    .join(Friendship, Friendship.requester_id == User.id)
                    .filter(Friendship.addressee_id == self.id,
                            Friendship.status == 'accepted',
                            User.is_deleted == False)
                    .all())
        return sent + received

    def get_groups(self):
        return (db.session.query(Group)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .filter(GroupMember.user_id == self.id, GroupMember.status == 'member')
                .all())


class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    addressee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending | accepted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('requester_id', 'addressee_id', name='uq_friendship'),)


class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    image = db.Column(db.String(256), default='')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('User', foreign_keys=[created_by])
    members = db.relationship(
        'GroupMember', foreign_keys='GroupMember.group_id', backref='group', lazy='dynamic')
    messages = db.relationship(
        'GroupMessage', foreign_keys='GroupMessage.group_id', backref='group', lazy='dynamic',
        order_by='GroupMessage.created_at')

    def image_url(self):
        if self.image:
            return url_for('static', filename=self.image)
        return url_for('static', filename='default-group.svg')


class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='member')  # member | invited
    is_admin = db.Column(db.Boolean, default=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='uq_group_member'),)


class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    file_path = db.Column(db.String(256), nullable=True)
    file_name = db.Column(db.String(256), nullable=True)   # original filename for download
    file_type = db.Column(db.String(50), nullable=True)
    is_system = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DirectMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    file_path = db.Column(db.String(256), nullable=True)
    file_name = db.Column(db.String(256), nullable=True)   # original filename for download
    file_type = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    subtitle = db.Column(db.String(200), default='')
    content = db.Column(db.Text, nullable=False, default='')
    visibility = db.Column(db.String(20), default='public')  # public | friends
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    images = db.relationship('PostImage', backref='post', lazy='dynamic',
                             order_by='PostImage.order', cascade='all, delete-orphan')
    attachments = db.relationship('PostFile', backref='post', lazy='dynamic',
                                  order_by='PostFile.order', cascade='all, delete-orphan')
    video_links = db.relationship('PostVideoLink', backref='post', lazy='dynamic',
                                  order_by='PostVideoLink.order', cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='post', lazy='dynamic',
                            cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='post', lazy='dynamic',
                               order_by='Comment.created_at', cascade='all, delete-orphan')


class PostImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    order = db.Column(db.Integer, default=0)


class PostFile(db.Model):
    """File and video attachments for posts."""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    file_path = db.Column(db.String(256), nullable=False)
    file_name = db.Column(db.String(256), nullable=False)
    media_type = db.Column(db.String(20), default='file')  # 'file' | 'video'
    order = db.Column(db.Integer, default=0)


class PostVideoLink(db.Model):
    """Embedded video links attached to posts (YouTube, Vimeo, etc.)."""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    order = db.Column(db.Integer, default=0)


class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)

    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='uq_like'),)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('User', foreign_keys=[user_id],
                             overlaps='commenter,comments')


class ChatLastRead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    chat_type = db.Column(db.String(20), nullable=False)  # direct | group
    chat_id = db.Column(db.Integer, nullable=False)
    last_read_id = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'chat_type', 'chat_id', name='uq_clr'),
    )


# ── Auth helpers ──────────────────────────────────────────────────────────────

def create_profile(account):
    return User(username=profiles.username(account))


profiles = Profiles(db, User, create_profile)


@app.before_request
def block_deleted_profile():
    if not request.admin_mount and current_user.is_authenticated and current_user.is_deleted:
        return app.failure(request, 403, api=request.path.startswith("/api/"),
                           detail="Your DodoBook profile has been deleted.")


app.profile_loader = profiles


@app.before_request
def update_last_seen():
    if current_user.is_authenticated and not current_user.is_deleted:
        now = datetime.utcnow()
        if (not current_user.last_seen or
                (now - current_user.last_seen).total_seconds() > 30):
            current_user.last_seen = now
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()


def can_manage_roles():
    return shared_admin() or current_user.role == 'admin'


def can_moderate():
    return can_manage_roles() or current_user.role == 'moderator'


def book_staff_required(view):
    @require_login
    @wraps(view)
    def protected(*args, **kwargs):
        if not can_moderate():
            abort(403)
        return view(*args, **kwargs)
    return protected


# ── File upload helpers ───────────────────────────────────────────────────────

def allowed_image(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)


def save_upload(file, subfolder):
    if not file or not file.filename:
        return None
    if not allowed_image(file.filename):
        return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(UPLOAD_FOLDER, subfolder)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, filename))
    return f"uploads/{subfolder}/{filename}"


def save_any_file(file, subfolder):
    if not file or not file.filename:
        return None, None, None
    original_name = secure_filename(file.filename)
    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
    unique_name = f"{uuid.uuid4().hex}_{original_name}"
    folder = os.path.join(UPLOAD_FOLDER, subfolder)
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, unique_name))
    if ext in ALLOWED_IMAGE_EXTENSIONS:
        file_type = 'image'
    elif ext in ALLOWED_VIDEO_EXTENSIONS:
        file_type = 'video'
    else:
        file_type = 'file'
    return f"uploads/{subfolder}/{unique_name}", file_type, original_name


def friendship_between(user_id, other_id):
    return Friendship.query.filter(
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == other_id)) |
        ((Friendship.requester_id == other_id) & (Friendship.addressee_id == user_id))
    ).filter_by(status='accepted').first()


def visible_posts(friends=None):
    if friends is None:
        friends = current_user.get_friends()
    friend_ids = [friend.id for friend in friends] + [current_user.id]
    return Post.query.filter(db.or_(
        Post.user_id.in_(friend_ids),
        Post.visibility == 'public',
    ))


def serve_static(filename):
    # Keep the existing URLs for attachments saved in older posts and chats.
    filename = posixpath.normpath(filename)
    if not filename.casefold().startswith('uploads/'):
        return app.send_static_file(filename)
    if not current_user.is_authenticated:
        return '', 404

    relative = filename.split('/', 1)[1]
    category = relative.split('/', 1)[0]
    download_name = None
    if category == 'chatfiles':
        message = DirectMessage.query.filter_by(file_path=filename).first()
        if message:
            participants = {message.sender_id, message.receiver_id}
            if current_user.id not in participants:
                return '', 404
            peer_id = (message.receiver_id if current_user.id == message.sender_id
                       else message.sender_id)
            if not friendship_between(current_user.id, peer_id):
                return '', 404
        else:
            message = GroupMessage.query.filter_by(file_path=filename).first()
            if not message or not GroupMember.query.filter_by(
                    group_id=message.group_id, user_id=current_user.id, status='member').first():
                return '', 404
        if message.file_type != 'image':
            download_name = message.file_name or posixpath.basename(filename)
    elif category in ('postpictures', 'postmedia', 'postfiles'):
        if category == 'postpictures':
            asset = PostImage.query.filter_by(filename=filename).first()
        else:
            asset = PostFile.query.filter_by(file_path=filename).first()
        if not asset or not visible_posts().filter(Post.id == asset.post_id).first():
            return '', 404
        if category == 'postfiles' or (category == 'postmedia' and asset.media_type != 'video'):
            download_name = asset.file_name or posixpath.basename(filename)
    elif category not in ('usericons', 'chaticons') or not allowed_image(filename):
        return '', 404

    response = send_from_directory(
        UPLOAD_FOLDER, relative, as_attachment=download_name is not None,
        download_name=download_name,
    )
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


app.view_functions['static'] = serve_static


# ── Template filters ──────────────────────────────────────────────────────────

_YT_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|embed/)|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})[^\s]*'
)
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
_VIMEO_RE = re.compile(r'https?://(?:www\.)?vimeo\.com/(?:video/)?(\d+)(?:[/?#]|$)')


@app.template_filter('post_content')
def post_content_filter(text):
    """Render post text: escape HTML, embed YouTube/Vimeo iframes, and link plain URLs."""
    if not text:
        return Markup('')

    embed_ranges = []
    matches = []
    for m in _YT_RE.finditer(text):
        embed_ranges.append((m.start(), m.end()))
        matches.append((m.start(), m.end(), 'yt', m.group(0)))
    for m in _VIMEO_RE.finditer(text):
        embed_ranges.append((m.start(), m.end()))
        matches.append((m.start(), m.end(), 'vimeo', m.group(0)))
    for m in _URL_RE.finditer(text):
        if any(m.start() >= s and m.end() <= e for s, e in embed_ranges):
            continue
        matches.append((m.start(), m.end(), 'link', m.group(0)))
    matches.sort(key=lambda item: item[0])

    parts = []
    cursor = 0
    for start, end, kind, url in matches:
        if start < cursor:
            continue
        parts.append(html_escape(text[cursor:start]))
        if kind == 'yt':
            vid = _YT_RE.search(url).group(1)
            parts.append(Markup(
                f'<div class="yt-embed">'
                f'<iframe src="https://www.youtube.com/embed/{html_escape(vid)}" '
                f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
                f'gyroscope; picture-in-picture" allowfullscreen></iframe>'
                f'</div>'
            ))
        elif kind == 'vimeo':
            vid = _VIMEO_RE.search(url).group(1)
            parts.append(Markup(
                f'<div class="yt-embed">'
                f'<iframe src="https://player.vimeo.com/video/{html_escape(vid)}" '
                f'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>'
                f'</div>'
            ))
        else:
            clean_url = url.rstrip('.,;:!?)]}')
            parts.append(Markup(
                f'<a href="{html_escape(clean_url)}" target="_blank" '
                f'rel="noopener noreferrer">{html_escape(clean_url)}</a>'
            ))
        cursor = end

    parts.append(html_escape(text[cursor:]))
    return Markup(str(Markup('').join(parts)).replace('\n', '<br>'))


@app.template_filter('video_embed')
def video_embed_filter(url):
    """Convert a dedicated video link into an embedded player block."""
    url = (url or '').strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ''
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''
    yt = _YT_RE.search(url)
    if yt:
        vid = yt.group(1)
        return Markup(
            f'<div class="yt-embed">'
            f'<iframe src="https://www.youtube.com/embed/{html_escape(vid)}" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
            f'gyroscope; picture-in-picture" allowfullscreen></iframe>'
            f'</div>'
        )
    vimeo = _VIMEO_RE.search(url)
    if vimeo:
        vid = vimeo.group(1)
        return Markup(
            f'<div class="yt-embed">'
            f'<iframe src="https://player.vimeo.com/video/{html_escape(vid)}" '
            f'allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>'
            f'</div>'
        )
    ext = url.rsplit('.', 1)[-1].lower().split('?')[0] if '.' in url else ''
    if ext in ALLOWED_VIDEO_EXTENSIONS:
        return Markup(
            f'<video class="post-video" controls>'
            f'<source src="{html_escape(url)}">'
            f'</video>'
        )
    return Markup(
        f'<div class="video-link-fallback">'
        f'🎬 <a href="{html_escape(url)}" target="_blank" rel="noopener noreferrer">'
        f'{html_escape(url)}</a>'
        f'</div>'
    )


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route('/')
@require_login
def index():
    friends = current_user.get_friends()
    groups = current_user.get_groups()

    pending_requests = (Friendship.query
                        .filter_by(addressee_id=current_user.id, status='pending')
                        .all())
    group_invitations = (GroupMember.query
                         .filter_by(user_id=current_user.id, status='invited')
                         .all())

    posts = (visible_posts(friends)
             .order_by(Post.created_at.desc())
             .limit(50)
             .all())

    post_data = []
    for post in posts:
        user_liked = (Like.query
                      .filter_by(user_id=current_user.id, post_id=post.id)
                      .first() is not None)
        post_data.append({
            'post': post,
            'user_liked': user_liked,
            'like_count': post.likes.count(),
        })

    return render_template(
        'index.html',
        friends=friends,
        groups=groups,
        pending_requests=pending_requests,
        group_invitations=group_invitations,
        post_data=post_data,
        can_moderate=can_moderate(),
    )


@app.get("/login")
def login():
    return redirect(url_for("index")) if current_user.is_authenticated else shared_signin()


@app.get("/signup")
def signup():
    return shared_signin(signup=True)


@app.get("/forgot-password")
def forgot_password():
    return redirect(f"{request.identity.workspace_url}/forgot-password")


@app.post("/logout")
@require_login
def logout():
    return shared_signout()


@app.route('/makeapost')
@require_login
def makeapost():
    return render_template('makeapost.html')


@app.route('/addfriend')
@require_login
def addfriend():
    pending_requests = (Friendship.query
                        .filter_by(addressee_id=current_user.id, status='pending')
                        .all())
    sent_requests = (Friendship.query
                     .filter_by(requester_id=current_user.id, status='pending')
                     .all())
    return render_template('addfriend.html',
                           pending_requests=pending_requests,
                           sent_requests=sent_requests)


@app.route('/createagroup')
@require_login
def createagroup():
    friends = current_user.get_friends()
    return render_template('createagroup.html', friends=friends)


@app.route('/profile')
@require_login
def profile():
    return render_template('usersettings.html')


@app.get('/adminboard')
@book_staff_required
def adminboard():
    manage_roles = can_manage_roles()
    users = User.query.filter_by(is_deleted=False).order_by(User.username).all() if manage_roles else []
    posts = Post.query if manage_roles else visible_posts()
    posts = posts.order_by(Post.created_at.desc()).all()
    return render_template('adminboard.html', manage_roles=manage_roles, users=users, posts=posts)


@app.get('/moderatorboard')
@book_staff_required
def moderatorboard():
    return redirect(url_for('adminboard'))


@app.route('/pagenotfound')
def pagenotfound():
    return render_template('notfound.html')


@app.errorhandler(404)
def not_found_error(error):
    return render_template('notfound.html'), 404


# ── API: User ─────────────────────────────────────────────────────────────────

@app.route('/api/user/settings', methods=['GET'])
@require_login
def get_user_settings():
    return jsonify({
        'id': current_user.id,
        'username': current_user.username,
        'email': request.shared_user['email'],
        'bio': current_user.bio or '',
        'profile_pic': current_user.profile_pic_url(),
    })


@app.route('/api/user/update', methods=['POST'])
@require_login
def update_user_settings():
    username = request.form.get('username', '').strip()
    bio = request.form.get('bio', '').strip()

    if not 1 <= len(username) <= 50:
        return jsonify({'success': False, 'message': 'Use a DodoBook username up to 50 characters.'}), 400

    current_user.username = username
    current_user.bio = bio
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Username already taken.'}), 409

    pic = request.files.get('profilepic')
    if pic and pic.filename:
        path = save_upload(pic, 'usericons')
        if path:
            current_user.profile_pic = path

    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Settings updated successfully.',
        'profile_pic': current_user.profile_pic_url(),
    })


@app.route('/api/user/delete', methods=['DELETE'])
@require_login
def delete_user_account():
    user = current_user._get_current_object()
    soft_delete_user(user)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Account deleted.'})


def soft_delete_user(user):
    user.is_deleted = True
    user.username = f'deleted_{uuid.uuid4().hex}'
    user.role = 'user'
    user.profile_pic = ''
    user.bio = ''
    Friendship.query.filter(
        (Friendship.requester_id == user.id) | (Friendship.addressee_id == user.id)
    ).delete(synchronize_session=False)
    GroupMember.query.filter_by(user_id=user.id).delete(synchronize_session=False)


# ── API: Posts ────────────────────────────────────────────────────────────────

@app.route('/api/posts/create', methods=['POST'])
@require_login
def create_post():
    title = request.form.get('title', '').strip()
    subtitle = request.form.get('subtitle', '').strip()
    content = request.form.get('content', '').strip()
    visibility = request.form.get('visibility', 'public')
    if visibility not in ('public', 'friends'):
        visibility = 'public'

    if not title or not content:
        flash('Title and content are required.', 'error')
        return redirect(url_for('makeapost'))

    post = Post(user_id=current_user.id, title=title,
                subtitle=subtitle, content=content, visibility=visibility)
    db.session.add(post)
    db.session.flush()

    # Media files (images → carousel, videos → inline player)
    for idx, f in enumerate(request.files.getlist('postmedia')):
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext in ALLOWED_IMAGE_EXTENSIONS:
            path = save_upload(f, 'postpictures')
            if path:
                db.session.add(PostImage(post_id=post.id, filename=path, order=idx))
        elif ext in ALLOWED_VIDEO_EXTENSIONS:
            path, _, fname = save_any_file(f, 'postmedia')
            if path:
                db.session.add(PostFile(post_id=post.id, file_path=path,
                                        file_name=fname, media_type='video', order=idx))

    # Regular file attachments
    for idx, f in enumerate(request.files.getlist('postfiles')):
        if f and f.filename:
            path, _, fname = save_any_file(f, 'postfiles')
            if path:
                db.session.add(PostFile(post_id=post.id, file_path=path,
                                        file_name=fname, media_type='file', order=idx))

    # Dedicated video links (YouTube, Vimeo, etc.)
    for idx, url_raw in enumerate(request.form.getlist('postvideolinks')):
        url_raw = url_raw.strip()
        if url_raw:
            db.session.add(PostVideoLink(post_id=post.id, url=url_raw, order=idx))

    db.session.commit()
    return redirect(url_for('index'))


@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
@require_login
def toggle_like(post_id):
    post = visible_posts().filter(Post.id == post_id).first()
    if not post:
        return jsonify({'success': False}), 404

    existing = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(Like(user_id=current_user.id, post_id=post_id))
        liked = True

    db.session.commit()
    return jsonify({'liked': liked, 'count': post.likes.count()})


@app.route('/api/posts/<int:post_id>/comment', methods=['POST'])
@require_login
def add_comment(post_id):
    if not visible_posts().filter(Post.id == post_id).first():
        return jsonify({'success': False}), 404

    data = request.get_json() or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'message': 'Comment text is required.'}), 400

    comment = Comment(user_id=current_user.id, post_id=post_id, text=text)
    db.session.add(comment)
    db.session.commit()

    return jsonify({
        'success': True,
        'comment': {
            'id': comment.id,
            'author': current_user.display_name,
            'text': comment.text,
        }
    })


@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
@require_login
def delete_post(post_id):
    posts = Post.query if can_manage_roles() else visible_posts()
    post = posts.filter(Post.id == post_id).first()
    if not post:
        return jsonify({'success': False}), 404
    if post.user_id != current_user.id and not can_moderate():
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403
    db.session.delete(post)
    db.session.commit()
    return jsonify({'success': True})


# ── API: Friends ──────────────────────────────────────────────────────────────

@app.route('/api/friends/search')
@require_login
def search_friends():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'users': []})

    match = User.username.ilike(f'%{q}%')
    if '@' in q:
        account_id = request.identity.accounts.account_id_by_email(q)
        if account_id is not None:
            profile_id = db.select(profiles.links.c.profile_id).where(profiles.links.c.account_id == account_id)
            match = db.or_(match, User.id.in_(profile_id))
    users = User.query.filter(User.is_deleted == False, User.id != current_user.id, match).limit(10).all()

    results = []
    for u in users:
        existing = Friendship.query.filter(
            ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == u.id)) |
            ((Friendship.requester_id == u.id) & (Friendship.addressee_id == current_user.id))
        ).first()
        status = None
        if existing:
            if existing.status == 'accepted':
                status = 'friends'
            elif existing.requester_id == current_user.id:
                status = 'sent'
            else:
                status = 'received'
        results.append({
            'id': u.id,
            'username': u.display_name,
            'profile_pic': u.profile_pic_url(),
            'friendship_status': status,
            'friendship_id': existing.id if existing and existing.status == 'pending' else None,
        })
    return jsonify({'users': results})


@app.route('/api/friends/request', methods=['POST'])
@require_login
def send_friend_request():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    target = db.session.get(User, user_id)
    if not target or target.is_deleted:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    existing = Friendship.query.filter(
        ((Friendship.requester_id == current_user.id) & (Friendship.addressee_id == user_id)) |
        ((Friendship.requester_id == user_id) & (Friendship.addressee_id == current_user.id))
    ).first()
    if existing:
        return jsonify({'success': False, 'message': 'Request already exists.'}), 409

    db.session.add(Friendship(requester_id=current_user.id, addressee_id=user_id))
    db.session.commit()
    return jsonify({'success': True, 'message': f'Friend request sent to {target.display_name}.'})


@app.route('/api/friends/respond', methods=['POST'])
@require_login
def respond_friend_request():
    data = request.get_json() or {}
    friendship_id = data.get('friendship_id')
    action = data.get('action')  # accept | decline

    if not friendship_id or action not in ('accept', 'decline'):
        return jsonify({'success': False, 'message': 'Invalid request.'}), 400

    friendship = db.session.get(Friendship, friendship_id)
    if not friendship or friendship.addressee_id != current_user.id:
        return jsonify({'success': False, 'message': 'Not found.'}), 404

    if action == 'accept':
        friendship.status = 'accepted'
    else:
        db.session.delete(friendship)

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/friends/cancel/<int:friendship_id>', methods=['DELETE'])
@require_login
def cancel_friend_request(friendship_id):
    friendship = db.session.get(Friendship, friendship_id)
    if (not friendship or
            friendship.requester_id != current_user.id or
            friendship.status != 'pending'):
        return jsonify({'success': False, 'message': 'Request not found.'}), 404
    db.session.delete(friendship)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/friends/<int:user_id>', methods=['DELETE'])
@require_login
def unfriend(user_id):
    friendship = friendship_between(current_user.id, user_id)
    if not friendship:
        return jsonify({'success': False, 'message': 'Not friends.'}), 404
    db.session.delete(friendship)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/friends/list')
@require_login
def friends_list():
    group_id = request.args.get('exclude_group', type=int)
    friends = current_user.get_friends()
    if group_id:
        existing_ids = {m.user_id for m in GroupMember.query.filter_by(group_id=group_id).all()}
        friends = [f for f in friends if f.id not in existing_ids]
    return jsonify({'friends': [
        {'id': f.id, 'username': f.display_name, 'profile_pic': f.profile_pic_url()}
        for f in friends
    ]})


# ── API: Groups ───────────────────────────────────────────────────────────────

@app.route('/api/groups/create', methods=['POST'])
@require_login
def create_group():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Group name is required.', 'error')
        return redirect(url_for('createagroup'))

    group = Group(name=name, created_by=current_user.id)
    pic = request.files.get('grouppic')
    if pic and pic.filename:
        path = save_upload(pic, 'chaticons')
        if path:
            group.image = path

    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMember(group_id=group.id, user_id=current_user.id,
                               status='member', is_admin=True))

    for uid_str in request.form.getlist('members'):
        try:
            uid = int(uid_str)
            if uid != current_user.id:
                db.session.add(GroupMember(group_id=group.id, user_id=uid, status='invited'))
        except ValueError:
            pass

    db.session.commit()
    return redirect(url_for('index'))


@app.route('/api/groups/<int:group_id>/invite', methods=['POST'])
@require_login
def invite_to_group(group_id):
    if not GroupMember.query.filter_by(
            group_id=group_id, user_id=current_user.id, status='member').first():
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403

    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    target = db.session.get(User, user_id)
    if not target or target.is_deleted:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    if GroupMember.query.filter_by(group_id=group_id, user_id=user_id).first():
        return jsonify({'success': False, 'message': 'Already a member or invited.'}), 409

    db.session.add(GroupMember(group_id=group_id, user_id=user_id, status='invited'))
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/groups/<int:group_id>/respond', methods=['POST'])
@require_login
def respond_group_invitation(group_id):
    membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=current_user.id, status='invited').first()
    if not membership:
        return jsonify({'success': False, 'message': 'No invitation found.'}), 404

    data = request.get_json() or {}
    action = data.get('action')

    if action == 'accept':
        membership.status = 'member'
        db.session.commit()
        return jsonify({'success': True})
    elif action == 'decline':
        db.session.delete(membership)
        db.session.commit()
        return jsonify({'success': True})

    return jsonify({'success': False, 'message': 'Invalid action.'}), 400


@app.route('/api/groups/<int:group_id>/leave', methods=['POST'])
@require_login
def leave_group(group_id):
    membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=current_user.id, status='member').first()
    if not membership:
        return jsonify({'success': False, 'message': 'Not a member.'}), 404
    db.session.delete(membership)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/groups/<int:group_id>/members')
@require_login
def get_group_members(group_id):
    my_membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=current_user.id, status='member').first()
    if not my_membership:
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403

    members = GroupMember.query.filter_by(group_id=group_id, status='member').all()
    return jsonify({
        'current_user_is_admin': my_membership.is_admin,
        'members': [
            {
                'id': m.user.id,
                'username': m.user.display_name,
                'profile_pic': m.user.profile_pic_url(),
                'is_admin': m.is_admin,
            }
            for m in members
        ]
    })


@app.route('/api/groups/<int:group_id>/kick/<int:target_id>', methods=['POST'])
@require_login
def kick_from_group(group_id, target_id):
    my_membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=current_user.id, status='member', is_admin=True).first()
    if not my_membership:
        return jsonify({'success': False, 'message': 'Not a group admin.'}), 403

    target_membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=target_id, status='member').first()
    if not target_membership:
        return jsonify({'success': False, 'message': 'Member not found.'}), 404

    target = db.session.get(User, target_id)
    name = target.display_name if target else 'Unknown'

    db.session.delete(target_membership)
    db.session.add(GroupMessage(
        group_id=group_id, user_id=current_user.id,
        content=f'{name} was kicked from the group.',
        is_system=True
    ))
    db.session.commit()
    return jsonify({'success': True})


# ── API: Chat ─────────────────────────────────────────────────────────────────

def _update_last_read(user_id, chat_type, chat_id, max_id):
    read = insert(ChatLastRead).values(
        user_id=user_id, chat_type=chat_type, chat_id=chat_id, last_read_id=max_id)
    db.session.execute(read.on_conflict_do_update(
        index_elements=['user_id', 'chat_type', 'chat_id'],
        set_={'last_read_id': read.excluded.last_read_id},
        where=read.excluded.last_read_id > ChatLastRead.last_read_id,
    ))


@app.route('/api/chat/messages')
@require_login
def get_chat_messages():
    chat_type = request.args.get('type')
    chat_id = request.args.get('id', type=int)
    after_id = request.args.get('after', default=0, type=int)

    if not chat_type or not chat_id:
        return jsonify({'messages': []})

    messages = []

    if chat_type == 'direct':
        if not friendship_between(current_user.id, chat_id):
            return jsonify({'messages': []}), 403

        msgs = (DirectMessage.query
                .filter(
                    ((DirectMessage.sender_id == current_user.id) &
                     (DirectMessage.receiver_id == chat_id)) |
                    ((DirectMessage.sender_id == chat_id) &
                     (DirectMessage.receiver_id == current_user.id)),
                    DirectMessage.id > after_id
                )
                .order_by(DirectMessage.id)
                .all())

        for m in msgs:
            messages.append({
                'id': m.id,
                'sender_id': m.sender_id,
                'sender_name': m.sender.display_name,
                'sender_pic': m.sender.profile_pic_url(),
                'content': m.content,
                'file_path': url_for('static', filename=m.file_path) if m.file_path else None,
                'file_name': m.file_name,
                'file_type': m.file_type,
                'is_system': False,
            })

    elif chat_type == 'group':
        if not GroupMember.query.filter_by(
                group_id=chat_id, user_id=current_user.id, status='member').first():
            return jsonify({'messages': []}), 403

        msgs = (GroupMessage.query
                .filter(GroupMessage.group_id == chat_id, GroupMessage.id > after_id)
                .order_by(GroupMessage.id)
                .all())

        for m in msgs:
            messages.append({
                'id': m.id,
                'sender_id': m.user_id,
                'sender_name': m.sender.display_name,
                'sender_pic': m.sender.profile_pic_url(),
                'content': m.content,
                'file_path': url_for('static', filename=m.file_path) if m.file_path else None,
                'file_name': m.file_name,
                'file_type': m.file_type,
                'is_system': m.is_system,
            })

    if messages:
        _update_last_read(current_user.id, chat_type, chat_id, messages[-1]['id'])
        db.session.commit()

    return jsonify({'messages': messages})


@app.route('/api/chat/message', methods=['POST'])
@require_login
def send_chat_message():
    chat_type = request.form.get('type')
    chat_id = request.form.get('id', type=int)
    content = request.form.get('content', '').strip()
    file = request.files.get('file')

    if not chat_type or not chat_id:
        return jsonify({'success': False, 'message': 'Invalid request.'}), 400
    if not content and not (file and file.filename):
        return jsonify({'success': False, 'message': 'Message cannot be empty.'}), 400

    if chat_type == 'direct':
        if not friendship_between(current_user.id, chat_id):
            return jsonify({'success': False, 'message': 'Not friends.'}), 403
        msg = DirectMessage(sender_id=current_user.id, receiver_id=chat_id,
                            content=content)

    elif chat_type == 'group':
        if not GroupMember.query.filter_by(
                group_id=chat_id, user_id=current_user.id, status='member').first():
            return jsonify({'success': False, 'message': 'Not a member.'}), 403
        msg = GroupMessage(group_id=chat_id, user_id=current_user.id,
                           content=content)
    else:
        return jsonify({'success': False, 'message': 'Invalid type.'}), 400

    if file and file.filename:
        msg.file_path, msg.file_type, msg.file_name = save_any_file(file, 'chatfiles')
    uploaded_path = msg.file_path
    try:
        db.session.add(msg)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        if uploaded_path:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, 'chatfiles', posixpath.basename(uploaded_path)))
            except OSError:
                app.logger.exception('Could not remove an unsaved chat attachment')
        raise

    return jsonify({
        'success': True,
        'message': {
            'id': msg.id,
            'sender_id': current_user.id,
            'sender_name': current_user.display_name,
            'sender_pic': current_user.profile_pic_url(),
            'content': msg.content,
            'file_path': url_for('static', filename=msg.file_path) if msg.file_path else None,
            'file_name': msg.file_name,
            'file_type': msg.file_type,
            'is_system': False,
        }
    })


@app.route('/api/unread')
@require_login
def get_unread_counts():
    unread = {}
    friends = current_user.get_friends()

    for friend in friends:
        clr = ChatLastRead.query.filter_by(
            user_id=current_user.id, chat_type='direct', chat_id=friend.id).first()
        last_id = clr.last_read_id if clr else 0
        count = DirectMessage.query.filter(
            DirectMessage.receiver_id == current_user.id,
            DirectMessage.sender_id == friend.id,
            DirectMessage.id > last_id
        ).count()
        if count:
            unread[f'direct_{friend.id}'] = count

    for group in current_user.get_groups():
        clr = ChatLastRead.query.filter_by(
            user_id=current_user.id, chat_type='group', chat_id=group.id).first()
        last_id = clr.last_read_id if clr else 0
        count = GroupMessage.query.filter(
            GroupMessage.group_id == group.id,
            GroupMessage.user_id != current_user.id,
            GroupMessage.id > last_id
        ).count()
        if count:
            unread[f'group_{group.id}'] = count

    online = {str(f.id): f.is_online for f in friends}
    return jsonify({**unread, 'online': online})


# ── API: Admin ────────────────────────────────────────────────────────────────

@app.post('/api/admin/users/<int:user_id>/role')
@require_login
def admin_set_role(user_id):
    if not can_manage_roles():
        abort(403)
    user = db.session.get(User, user_id)
    if not user or user.is_deleted:
        return jsonify({'success': False, 'message': 'Profile not found.'}), 404
    data = request.get_json(silent=True)
    role = data.get('role') if isinstance(data, dict) else None
    if role not in ('user', 'moderator', 'admin'):
        return jsonify({'success': False, 'message': 'Choose a valid DodoBook role.'}), 400
    if user.id == current_user.id:
        return jsonify({'success': False, 'message': 'Choose another DodoBook profile.'}), 400
    user.role = role
    db.session.commit()
    return jsonify({'success': True})


@app.delete('/api/moderator/posts/<int:post_id>')
@book_staff_required
def moderator_delete_post(post_id):
    return delete_post(post_id)


@app.delete('/api/moderator/comments/<int:comment_id>')
@book_staff_required
def moderator_delete_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if not comment or (not can_manage_roles() and not visible_posts().filter(Post.id == comment.post_id).first()):
        return jsonify({'success': False}), 404
    db.session.delete(comment)
    db.session.commit()
    return jsonify({'success': True})


# ── Database initialisation ───────────────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'usericons'), exist_ok=True)
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'chaticons'), exist_ok=True)
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'postpictures'), exist_ok=True)
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'postmedia'), exist_ok=True)
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'postfiles'), exist_ok=True)
        os.makedirs(os.path.join(UPLOAD_FOLDER, 'chatfiles'), exist_ok=True)

app.admin_tab(
    "DodoBook", "/adminboard", prefixes=("admin_", "moderator_"),
    endpoints=("adminboard", "moderatorboard"), assets=("static",), on_service=True,
)
init_db()
