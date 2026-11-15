"""OAuth2 for Gmail and Microsoft 365 mailboxes.

Google and Microsoft both refuse plain passwords for IMAP/SMTP now. The flow is
the standard "installed application" one:

  1. you register an OAuth client once (Google Cloud Console / Azure Portal) and
     paste the client id and secret into Settings -> Mailboxes,
  2. *Connect* opens the provider's consent screen in your browser,
  3. the provider redirects to this app's local callback with a code,
  4. the code is exchanged for an access token and a refresh token,
  5. the refresh token is stored with the mailbox and used to mint access tokens
     when IMAP/SMTP need one (XOAUTH2).

Nothing here is specific to a mail library: `xoauth2()` returns the exact string
imaplib and smtplib expect.
"""
import base64
import json
import secrets
import time
import urllib.parse

from . import config, db, net

PROVIDERS = {
    "google": {
        "label": "Google / Gmail / Workspace",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "openid email https://mail.google.com/",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
    },
    "microsoft": {
        "label": "Microsoft 365 / Outlook",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": ("offline_access openid email profile "
                  "https://outlook.office.com/IMAP.AccessAsUser.All "
                  "https://outlook.office.com/SMTP.Send"),
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "extra_auth": {},
    },
}


class OAuthError(Exception):
    pass


def settings():
    value = config.load_settings().get("oauth") or {}
    return value if isinstance(value, dict) else {}


def status():
    s = settings()
    return {"redirect_uri": redirect_uri(),
            "google": {"configured": bool((s.get("google_client_id") or "").strip()),
                       "label": PROVIDERS["google"]["label"]},
            "microsoft": {"configured": bool((s.get("microsoft_client_id") or "").strip()),
                          "label": PROVIDERS["microsoft"]["label"]}}


def redirect_uri(port=None):
    s = settings()
    if (s.get("redirect_uri") or "").strip():
        return s["redirect_uri"].strip()
    return "http://127.0.0.1:%d/oauth/callback" % int(port or config.DEFAULT_PORT)


def client_for(provider):
    if provider not in PROVIDERS:
        raise OAuthError("Unknown provider %r." % provider)
    s = settings()
    client_id = (s.get(provider + "_client_id") or "").strip()
    client_secret = (s.get(provider + "_client_secret") or "").strip()
    if not client_id:
        raise OAuthError("No %s OAuth client configured. Add a client id and secret in Settings."
                         % PROVIDERS[provider]["label"])
    return client_id, client_secret


def auth_url(provider, port=None, state=""):
    client_id, _secret = client_for(provider)
    conf = PROVIDERS[provider]
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(port),
        "response_type": "code",
        "scope": conf["scope"],
        "state": state or secrets.token_urlsafe(16),
        "login_hint": "",
    }
    params.update(conf.get("extra_auth") or {})
    params = {k: v for k, v in params.items() if v}
    return conf["auth_url"] + "?" + urllib.parse.urlencode(params), params["state"]


def _post_form(url, form, timeout=45):
    body = urllib.parse.urlencode(form).encode()
    raw = net.fetch(url, timeout=timeout, retries=1, method="POST", data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "Accept": "application/json"})
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise OAuthError("The provider answered with something that is not JSON.")
    if data.get("error"):
        raise OAuthError("%s: %s" % (data.get("error"),
                                     data.get("error_description") or "no description"))
    return data


def exchange_code(provider, code, port=None):
    client_id, client_secret = client_for(provider)
    data = _post_form(PROVIDERS[provider]["token_url"], {
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri(port), "grant_type": "authorization_code"})
    return _normalise(data)


def refresh(provider, refresh_token):
    client_id, client_secret = client_for(provider)
    data = _post_form(PROVIDERS[provider]["token_url"], {
        "refresh_token": refresh_token, "client_id": client_id,
        "client_secret": client_secret, "grant_type": "refresh_token"})
    out = _normalise(data)
    out["refresh_token"] = out.get("refresh_token") or refresh_token
    return out


def _normalise(data):
    expires_in = int(data.get("expires_in") or 3600)
    return {"access_token": data.get("access_token") or "",
            "refresh_token": data.get("refresh_token") or "",
            "expires_at": int(time.time()) + expires_in - 60,
            "scope": data.get("scope") or "",
            "token_type": data.get("token_type") or "Bearer"}


def account_email(provider, access_token):
    """Ask the provider who this token belongs to (used to prefill the mailbox)."""
    try:
        if provider == "google":
            data = net.json_body("https://www.googleapis.com/oauth2/v3/userinfo",
                                 headers={"Authorization": "Bearer " + access_token})
            return (data.get("email") or "").strip()
        data = net.json_body("https://graph.microsoft.com/v1.0/me",
                             headers={"Authorization": "Bearer " + access_token})
        return (data.get("mail") or data.get("userPrincipalName") or "").strip()
    except (net.NetError, ValueError):
        return ""


def access_token_for(box):
    """A valid access token for this mailbox, refreshing it when needed."""
    provider = (box.get("oauth_provider") or "").strip()
    if provider not in PROVIDERS:
        raise OAuthError("This mailbox is not connected through OAuth.")
    access = (box.get("oauth_access") or "").strip()
    expires = int(box.get("oauth_expires") or 0)
    if access and expires > time.time() + 120:
        return access
    refresh_token = (box.get("oauth_refresh") or "").strip()
    if not refresh_token:
        raise OAuthError("The OAuth connection expired and no refresh token is stored. "
                         "Reconnect the mailbox.")
    tokens = refresh(provider, refresh_token)
    db.update("mailboxes", box["id"], {"oauth_access": tokens["access_token"],
                                       "oauth_refresh": tokens["refresh_token"],
                                       "oauth_expires": tokens["expires_at"]})
    return tokens["access_token"]


def xoauth2(email, access_token):
    """The exact SASL string imaplib.authenticate and smtplib.auth expect."""
    return "user=%s\x01auth=Bearer %s\x01\x01" % (email, access_token)


def xoauth2_b64(email, access_token):
    return base64.b64encode(xoauth2(email, access_token).encode()).decode()


def store_tokens(mailbox_id, provider, tokens, email=""):
    patch = {"auth_type": "oauth", "oauth_provider": provider,
             "oauth_access": tokens.get("access_token") or "",
             "oauth_refresh": tokens.get("refresh_token") or "",
             "oauth_expires": int(tokens.get("expires_at") or 0)}
    if email:
        patch["oauth_email"] = email
    db.update("mailboxes", mailbox_id, patch)
    return {"ok": True}
