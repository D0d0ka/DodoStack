"""English-language pages for the DodoStack workspace and shared sign-in."""
from urllib.parse import urlencode

from .framework import safe

SERVICE_DESCRIPTIONS = {
    "todo": "Capture tasks, see what’s left, and mark off what’s done.",
    "doodle": "Search the web, media, and indexed sites.",
    "dodobook": "Share posts and chat with friends and groups.",
    "dodotv": "Browse the catalog and keep track of what you watch.",
    "dodokey": "Store and manage your passwords in an encrypted vault.",
    "dodocalender": "Plan personal events, see public holidays, and share events.",
}
SERVICE_NAMES = {
    "doodle": "Doodle", "dodobook": "DodoBook", "dodotv": "Dodo TV",
    "todo": "Todo", "dodokey": "DodoKey", "dodocalender": "Dodo Calender",
}


def service_name(key):
    return SERVICE_NAMES.get(key, key.title())


def brand_logo(service, css_class="service-logo"):
    name = service if service in SERVICE_NAMES else "stack"
    return f'<img class="{css_class}" src="/static/branding/{name}.png" alt="" width="52" height="52">'


def document(title, body, origin="", icon="stack"):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#f7f6ee">
  <title>{safe(title)} · DodoStack</title>
  <link rel="icon" type="image/png" href="{safe(origin)}/static/branding/{safe(icon)}.png">
  <link rel="stylesheet" href="{safe(origin)}/static/workspace.css">
</head>
<body>{body}</body>
</html>"""


def header(account="", origin=""):
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a class="wordmark" href="{safe(origin)}/">
      <img src="{safe(origin)}/static/branding/stack.png" width="38" height="38" alt="">
      <span>Dodo<span>Stack</span></span>
    </a>
    {account}
  </div>
</header>"""


def dashboard(user, services, *, private=False):
    if private and not user:
        services = {}
    if user:
        account = f"""<div class="account">
  <a class="username" href="/account/email">{safe(user["username"])}</a>
  {'<a class="button primary" href="/admin">Admin</a>' if user["is_admin"] else ''}
  <form action="/logout" method="post"><button class="button" type="submit">Sign out</button></form>
</div>"""
    else:
        account = """<div class="account">
  <a class="button" href="/login">Sign in</a>
  <a class="button primary" href="/signup">Create account</a>
</div>"""

    cards = []
    for service, url in services.items():
        name = safe(service_name(service))
        cards.append(f"""
<a class="service-card" href="{safe(url)}">
  {brand_logo(service)}
  <div><h3>{name}</h3><p>{safe(SERVICE_DESCRIPTIONS.get(service, 'Open this service with your Dodo account.'))}</p></div>
  <span class="arrow" aria-hidden="true">→</span>
</a>""")

    service_list = ""
    if cards:
        service_list = f"""
<section class="services" aria-labelledby="services-heading">
  <h2 id="services-heading">Your services</h2>
  <div class="service-grid">{''.join(cards)}</div>
</section>"""
    intro = ("Sign in with an approved account to open the services."
             if private and not user else "One account for all your Dodo services.")
    return document("Workspace", f"""
{header(account)}
<main class="workspace">
  <section class="hero">
    <div>
      <h1>Your Dodo<br>workspace</h1>
      <p>{intro}</p>
    </div>
    <img src="/static/dodo.png" width="190" height="190" alt="">
  </section>
  {service_list}
</main>""")


def nav_link(label, url, current):
    active = ' aria-current="page"' if current else ""
    return f'<a href="{safe(url)}"{active}>{label}</a>'


def auth_page(mode, error=None, service=None, target="/", *, private=False,
              min_password_length=8, fields=None):
    fields = fields or {}
    signup = mode == "signup"
    title = "Create your account" if signup else "Sign in"
    other = "login" if signup else "signup"
    other_url = f"/{other}?" + urlencode({"service": service or "", "path": target})
    password_type = "new-password" if signup else "current-password"
    confirmation = f"""
<label for="confirmation">Confirm password</label>
<input id="confirmation" name="password_confirmation" type="password"
       autocomplete="new-password" minlength="{min_password_length}" maxlength="255"
       value="{safe(fields.get('password_confirmation', ''))}" required>""" if signup else ""
    email = f"""
<label for="email">Email address</label>
<input id="email" name="email" type="email" autocomplete="email" maxlength="254"
       value="{safe(fields.get('email', ''))}" required>""" if signup else ""
    password_limits = f'minlength="{min_password_length}" maxlength="255"' if signup else 'maxlength="255"'
    approval_note = '<p>New accounts need approval from a DodoStack administrator.</p>' if signup and private else ""
    alert = f'<p class="error" role="alert">{safe(error)}</p>' if error else ""
    return document(title, f"""
{header()}
<main class="auth-layout">
  <div class="auth-art" aria-hidden="true"><img src="/static/dodo.png" width="190" height="190" alt=""></div>
  <section class="auth-card">
    <h1>{title}</h1>
    <p>{f'Continue to {safe(service_name(service))} with your Dodo account.' if service else 'One account for all your Dodo services.'}</p>
    {approval_note}
    {alert}
    <form method="post" action="/{mode}">
      <input type="hidden" name="service" value="{safe(service or '')}">
      <input type="hidden" name="path" value="{safe(target)}">
      <label for="username">{'Username' if signup else 'Username or email'}</label>
      <input id="username" name="username" autocomplete="username" {'minlength="3" maxlength="24"' if signup else 'maxlength="254"'}
             value="{safe(fields.get('username', ''))}" required>
      {email}
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="{password_type}" {password_limits}
             value="{safe(fields.get('password', ''))}" required>
      {confirmation}
      <button class="button primary" type="submit">{title}</button>
    </form>
    <p class="auth-switch">{'Already have an account?' if signup else 'New to Dodo?'}
      <a href="{safe(other_url)}">{'Sign in' if signup else 'Create an account'}</a>
    </p>
    {'' if signup else '<p><a href="/forgot-password">Request a password reset</a></p>'}
  </section>
</main>""")


def sign_in_problem(service, message, service_url, origin):
    return document("Sign-in interrupted", f"""
{header(origin=origin)}
<main class="workspace">
  <section class="service-detail">
    <h1>Sign-in interrupted</h1>
    <p>{safe(message)}</p>
    <a class="button primary" href="{safe(service_url)}">Return to {safe(service_name(service))}</a>
  </section>
</main>""", origin=origin)


def admin_navigation(tabs, selected=""):
    links = [nav_link("Accounts", "/admin", selected == "accounts")]
    links.extend(nav_link(safe(label), url, selected == key) for key, (label, url) in tabs.items())
    return f"""
<link rel="stylesheet" href="/static/admin-navigation.css">
<nav class="dodo-admin-tabs" aria-label="DodoStack administration">
  <a href="/">DodoStack</a>{''.join(links)}
</nav>"""


def admin_accounts(accounts, tabs, token, message="", *, min_password_length=8):
    rows = []
    for account in accounts:
        username = safe(account["username"])
        role = "Administrator" if account["is_admin"] else "Member"
        action = "Remove admin" if account["is_admin"] else "Make admin"
        enabled = 0 if account["is_admin"] else 1
        approval = f"""
<form method="post" action="/admin/approve">
  <input type="hidden" name="csrf" value="{token}">
  <input type="hidden" name="username" value="{username}">
  <button class="button primary" type="submit">Approve account</button>
</form>""" if not account["approved"] else ""
        rows.append(f"""
<section class="service-detail account-row">
  <h2>{username}</h2><p>{role} · {'Approved' if account["approved"] else 'Awaiting approval'}</p>
  <p>{safe(account["email"] or "Email required")}</p>
  {approval}
  {'<p role="status">Password reset requested</p>' if account["password_reset_requested"] else ''}
  <form method="post" action="/admin/role">
    <input type="hidden" name="csrf" value="{token}">
    <input type="hidden" name="username" value="{username}">
    <button class="button" name="enabled" value="{enabled}">{action}</button>
  </form>
  <form method="post" action="/admin/password">
    <input type="hidden" name="csrf" value="{token}">
    <input type="hidden" name="username" value="{username}">
    <label>New password for {username}
      <input type="password" name="password" autocomplete="new-password" minlength="{min_password_length}" maxlength="255" required>
    </label>
    <button class="button" type="submit">Reset password</button>
  </form>
</section>""")
    alert = f'<p role="status">{safe(message)}</p>' if message else ""
    return document("Administration", f"""
{header()} {admin_navigation(tabs, "accounts")}
<main class="workspace">
  <h1>Accounts</h1>
  {alert}
  <div class="service-grid admin-accounts">{''.join(rows)}</div>
</main>""")


def password_page(token, message="", *, min_password_length=8):
    alert = f'<p role="status">{safe(message)}</p>' if message else ""
    return document("Change password", f"""
{header()}
<main class="auth-layout">
  <section class="auth-card">
    <h1>Change password</h1>
    {alert}
    <form method="post" action="/account/password">
      <input type="hidden" name="csrf" value="{token}">
      <label for="current-password">Current password</label>
      <input id="current-password" type="password" name="current" autocomplete="current-password" required>
      <label for="new-password">New password</label>
      <input id="new-password" type="password" name="password" autocomplete="new-password" minlength="{min_password_length}" maxlength="255" required>
      <button class="button primary" type="submit">Change password</button>
    </form>
  </section>
</main>""")



def reset_request_page(submitted):
    if submitted:
        content = "<p>If this account exists, its reset request is now visible to a DodoStack administrator.</p>"
    else:
        content = """
<form action="/forgot-password" method="post">
  <label for="username">DodoStack username</label>
  <input id="username" name="username" autocomplete="username" maxlength="24" required>
  <button class="button primary" type="submit">Request reset</button>
</form>"""
    return document("Request a password reset", f"""
{header()}
<main class="auth-layout">
  <section class="auth-card">
    <h1>Request a password reset</h1>
    {content}
    <p><a href="/login">Back to sign in</a></p>
  </section>
</main>""")



def email_page(token, current_email, message=""):
    title = "Update your email"
    alert = f'<p class="error" role="alert">{safe(message)}</p>' if message else ""
    return document(title, f"""
{header()}
<main class="auth-layout">
  <section class="auth-card">
    <h1>{title}</h1>
    {alert}
    <form method="post" action="/account/email">
      <input type="hidden" name="csrf" value="{token}">
      <label for="email">Email address</label>
      <input id="email" name="email" type="email" autocomplete="email" maxlength="254"
             value="{safe(current_email or '')}" required>
      <button class="button primary" type="submit">Save email</button>
    </form>
    {('<p><a href="/account/password">Change password</a></p>' if current_email else '')}
    <form method="post" action="/logout"><button class="button" type="submit">Sign out</button></form>
  </section>
</main>""")


def pending_page():
    return document("Approval pending", f"""
{header()}
<main class="workspace">
  <section class="service-detail">
    <h1>Approval pending</h1>
    <p>A DodoStack administrator needs to approve your account before you can open the services.</p>
    <a class="button primary" href="/account/pending">Check approval status</a>
    <form method="post" action="/logout"><button class="button" type="submit">Sign out</button></form>
  </section>
</main>""")
