"""English-language pages for the DodoStack workspace and shared sign-in."""
from urllib.parse import urlencode

from .framework import safe

SERVICE_DESCRIPTIONS = {"todo": "Capture tasks, see what’s left, and mark off what’s done."}


def document(title, body, origin=""):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#f7f6ee">
  <title>{safe(title)} · DodoStack</title>
  <link rel="stylesheet" href="{safe(origin)}/static/workspace.css">
</head>
<body>{body}</body>
</html>"""


def header(account="", origin=""):
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a class="wordmark" href="{safe(origin)}/">
      <img src="{safe(origin)}/static/dodo.png" width="38" height="38" alt="">
      <span>Dodo<span>Stack</span></span>
    </a>
    {account}
  </div>
</header>"""


def dashboard(user, services, selected):
    selected = selected if selected in services else ""
    if user:
        account = f"""<div class="account">
  <span class="username">{safe(user["username"])}</span>
  <form action="/logout" method="post"><button class="button" type="submit">Sign out</button></form>
</div>"""
    else:
        account = """<div class="account">
  <a class="button" href="/login">Sign in</a>
  <a class="button primary" href="/signup">Create account</a>
</div>"""

    tabs = [nav_link("Overview", "/", not selected)]
    cards = []
    for service in services:
        name = safe(service.title())
        tab_url = "/?" + urlencode({"service": service})
        tabs.append(nav_link(name, tab_url, selected == service))
        cards.append(f"""
<a class="service-card" href="{safe(tab_url)}">
  <span class="service-icon" aria-hidden="true">{'✓' if service == 'todo' else '↗'}</span>
  <div><h3>{name}</h3><p>{safe(SERVICE_DESCRIPTIONS.get(service, 'Open this service with your Dodo account.'))}</p></div>
  <span class="arrow" aria-hidden="true">→</span>
</a>""")

    if selected:
        name = safe(selected.title())
        description = safe(SERVICE_DESCRIPTIONS.get(selected, "Open this service with your Dodo account."))
        url = safe(services[selected])
        content = f"""
<section class="service-detail">
  <span class="service-icon" aria-hidden="true">{'✓' if selected == 'todo' else '↗'}</span>
  <h1>{name}</h1>
  <p>{description}</p>
  <a class="button primary" href="{url}">Open {name} <span aria-hidden="true">↗</span></a>
</section>"""
    else:
        content = f"""
<section class="hero">
  <div>
    <h1>Your Dodo<br>workspace</h1>
    <p>One account for all your Dodo services.</p>
  </div>
  <img src="/static/dodo.png" width="190" height="190" alt="">
</section>
<section class="services" aria-labelledby="services-heading">
  <h2 id="services-heading">Your services</h2>
  <div class="service-grid">{''.join(cards)}</div>
</section>"""
    return document("Workspace", f"""
{header(account)}
<main class="workspace">
  <nav class="tabs" aria-label="Workspace">{''.join(tabs)}</nav>
  {content}
</main>""")


def nav_link(label, url, current):
    active = ' aria-current="page"' if current else ""
    return f'<a href="{safe(url)}"{active}>{label}</a>'


def auth_page(mode, error=None, service=None, target="/"):
    signup = mode == "signup"
    title = "Create your account" if signup else "Sign in"
    other = "login" if signup else "signup"
    other_url = f"/{other}?" + urlencode({"service": service or "", "path": target})
    password_type = "new-password" if signup else "current-password"
    confirmation = """
<label for="confirmation">Confirm password</label>
<input id="confirmation" name="password_confirmation" type="password"
       autocomplete="new-password" minlength="8" required>""" if signup else ""
    alert = f'<p class="error" role="alert">{safe(error)}</p>' if error else ""
    return document(title, f"""
{header()}
<main class="auth-layout">
  <div class="auth-art" aria-hidden="true"><img src="/static/dodo.png" width="190" height="190" alt=""></div>
  <section class="auth-card">
    <h1>{title}</h1>
    <p>{f'Continue to {safe(service.title())} with your Dodo account.' if service else 'One account for all your Dodo services.'}</p>
    {alert}
    <form method="post" action="/{mode}">
      <input type="hidden" name="service" value="{safe(service or '')}">
      <input type="hidden" name="path" value="{safe(target)}">
      <label for="username">Username</label>
      <input id="username" name="username" autocomplete="username" minlength="3" maxlength="24" required>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="{password_type}" minlength="8" required>
      {confirmation}
      <button class="button primary" type="submit">{title}</button>
    </form>
    <p class="auth-switch">{'Already have an account?' if signup else 'New to Dodo?'}
      <a href="{safe(other_url)}">{'Sign in' if signup else 'Create an account'}</a>
    </p>
  </section>
</main>""")


def sign_in_problem(service, message, service_url, origin):
    return document("Sign-in interrupted", f"""
{header(origin=origin)}
<main class="workspace">
  <section class="service-detail">
    <h1>Sign-in interrupted</h1>
    <p>{safe(message)}</p>
    <a class="button primary" href="{safe(service_url)}">Return to {safe(service.title())}</a>
  </section>
</main>""", origin=origin)
