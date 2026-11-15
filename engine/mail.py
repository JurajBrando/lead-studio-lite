"""Mail plumbing: SMTP send, IMAP read, template merge, HTML rendering.

Adapted from the battle-tested code in the original Lead Studio mailer, trimmed
to what the lite edition needs and hardened for unattended use.
"""
import email
import email.header
import email.utils
import html as html_mod
import imaplib
import re
import smtplib
import ssl
import time
from email.message import EmailMessage

from . import oauth

EMAIL_RX = re.compile(r"^[^@\s,;<>]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
VAR_RX = re.compile(r"\{\{\s*([A-Za-z0-9_ .\-]+?)\s*(?:\|([^}]*))?\}\}")
URL_RX = re.compile(r"https?://[^\s<>\"')]+", re.I)
AUTOREPLY_RX = re.compile(
    r"(out of (the )?office|automatic(al)? repl|auto[- ]?reply|autoreply|vacation|abwesenheit|"
    r"undeliverable|delivery status|mail delivery|returned mail|failure notice|mailer-daemon|"
    r"postmaster|delivery has failed)", re.I)


def valid_email(addr):
    addr = (addr or "").strip()
    if len(addr) > 254 or addr.count("@") != 1:
        return False
    if ".." in addr or addr.startswith(".") or addr.endswith("."):
        return False
    return bool(EMAIL_RX.match(addr))


# --------------------------------------------------------------- templates
def merge(text, fields):
    """Replace {{field}} / {{field|fallback}}. Returns (text, missing_fields)."""
    missing = []
    f = {str(k).strip().lower(): v for k, v in (fields or {}).items()}

    def sub(m):
        key = m.group(1).strip().lower().replace(" ", "_")
        val = f.get(key)
        val = "" if val is None else str(val).strip()
        if val:
            return val
        if m.group(2) is not None:
            return m.group(2).strip()
        missing.append(key)
        return ""

    return VAR_RX.sub(sub, text or ""), missing


def lead_fields(lead):
    lname = (lead.get("last_name") or "").strip()
    fname = (lead.get("first_name") or "").strip()
    if not fname and not lname:
        full = (lead.get("company") or "").strip()
    else:
        full = " ".join(x for x in (fname, lname) if x).strip()
    return {
        "email": lead.get("email") or "",
        "first_name": fname,
        "last_name": lname,
        "name": full,
        "full_name": " ".join(x for x in (fname, lname) if x).strip(),
        "company": lead.get("company") or "",
        "title": lead.get("title") or "",
        "domain": lead.get("domain") or "",
        "website": lead.get("website") or "",
        "phone": lead.get("phone") or "",
        "city": lead.get("city") or "",
        "country": lead.get("country") or "",
        "industry": lead.get("industry") or "",
        "linkedin": lead.get("linkedin") or "",
    }


def plain_to_html(body, track_url=None, unsub_url=None):
    """Turn a plain-text body into a clean, email-client-friendly HTML part."""
    text = (body or "").strip()
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        safe = html_mod.escape(b)
        safe = URL_RX.sub(lambda m: '<a href="%s" style="color:#2f6fed">%s</a>' % (
            html_mod.escape(m.group(0)), html_mod.escape(m.group(0))), safe)
        out.append('<p style="margin:0 0 16px 0">%s</p>' % safe.replace("\n", "<br>"))
    inner = "".join(out)
    if unsub_url:
        inner += ('<p style="margin:24px 0 0 0;font-size:12px;color:#8a8f98">'
                  'Don\'t want these emails? <a href="%s" style="color:#8a8f98">Unsubscribe</a>.</p>'
                  % html_mod.escape(unsub_url))
    if track_url:
        inner += ('<img src="%s" width="1" height="1" alt="" '
                  'style="display:block;border:0;outline:none">' % html_mod.escape(track_url))
    return ('<!doctype html><html><body style="margin:0;padding:0;background:#ffffff">'
            '<div style="font:15px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            'color:#1d2330;max-width:560px">%s</div></body></html>' % inner)


def build_message(box, to_addr, subject, text, html=None, unsub_mailto="", in_reply_to="",
                  message_id="", unsub_url=""):
    frm = (box.get("from_email") or box.get("smtp_user") or "").strip()
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr(((box.get("from_name") or "").strip(), frm)) if box.get("from_name") else frm
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = message_id or email.utils.make_msgid(
        domain=frm.split("@")[-1] if "@" in frm else None)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if (box.get("reply_to") or "").strip():
        msg["Reply-To"] = box["reply_to"].strip()
    # List-Unsubscribe tells Gmail and Outlook how to offer their own unsubscribe button.
    # "One-Click" (RFC 8058) is only valid with an https URL that accepts a POST, so it is
    # advertised only when the app has a public tracking base url to serve it.
    targets = ["<mailto:%s?subject=unsubscribe>" % unsub_mailto] if unsub_mailto else []
    if unsub_url:
        targets.append("<%s>" % unsub_url)
    if targets:
        msg["List-Unsubscribe"] = ", ".join(targets)
    if unsub_url:
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text or "")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


# ------------------------------------------------------------------- SMTP
def smtp_open(box, timeout=40):
    host = (box.get("smtp_host") or "").strip()
    port = int(box.get("smtp_port") or 587)
    if not host:
        return None, "This mailbox has no SMTP server configured."
    ctx = ssl.create_default_context()
    security = (box.get("smtp_security") or "starttls").strip().lower()
    try:
        if port == 465 or security == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=timeout)
            s.ehlo()
            if security != "none":
                s.starttls(context=ctx)
                s.ehlo()
        user = (box.get("smtp_user") or box.get("from_email") or "").strip()
        pw = box.get("smtp_pass") or ""
        if (box.get("auth_type") or "").strip() == "oauth":
            try:
                token = oauth.access_token_for(box)
            except oauth.OAuthError as exc:
                return None, str(exc)
            s.auth("XOAUTH2", lambda: oauth.xoauth2_b64(user, token))
        elif user and pw:
            s.login(user, pw)
        return s, ""
    except smtplib.SMTPAuthenticationError as e:
        return None, ("The server rejected the username/password (%s). Gmail, Outlook and most providers "
                      "need an app password, not your normal login." % (
                          getattr(e, "smtp_error", b"") or e))
    except smtplib.SMTPNotSupportedError as e:
        return None, "The server does not support a required step: %s" % e
    except (OSError, smtplib.SMTPException) as e:
        return None, "Could not connect to %s:%s (%s). Check the host, the port and the security mode." % (
            host, port, e)


def smtp_send(box, to_addr, subject, text, html=None, unsub_mailto="", message_id="",
              unsub_url=""):
    """Send one message. Returns (ok, message_id_or_error)."""
    s, err = smtp_open(box)
    if not s:
        return False, err
    msg = build_message(box, to_addr, subject, text, html, unsub_mailto,
                        message_id=message_id, unsub_url=unsub_url)
    try:
        s.send_message(msg)
        return True, (msg.get("Message-ID") or "").strip()
    except smtplib.SMTPRecipientsRefused as e:
        return False, "The server refused the recipient: %s" % (list(e.recipients.values())[:1] or e)
    except (smtplib.SMTPException, OSError) as e:
        return False, "Sending failed: %s" % e
    finally:
        try:
            s.quit()
        except (smtplib.SMTPException, OSError):
            pass


# ------------------------------------------------------------------- IMAP
def imap_open(box, timeout=40):
    host = (box.get("imap_host") or "").strip()
    port = int(box.get("imap_port") or 993)
    if not host:
        return None, "This mailbox has no IMAP server configured."
    user = (box.get("imap_user") or box.get("smtp_user") or box.get("from_email") or "").strip()
    pw = box.get("imap_pass") or box.get("smtp_pass") or ""
    try:
        if port == 143:
            m = imaplib.IMAP4(host, port, timeout=timeout)
            m.starttls(ssl.create_default_context())
        else:
            m = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=timeout)
        if (box.get("auth_type") or "").strip() == "oauth":
            token = oauth.access_token_for(box)
            m.authenticate("XOAUTH2", lambda _challenge: oauth.xoauth2(user, token).encode())
        else:
            m.login(user, pw)
        return m, ""
    except oauth.OAuthError as e:
        return None, str(e)
    except imaplib.IMAP4.error as e:
        return None, "IMAP login failed (%s). Check the address and app password." % e
    except (OSError, ssl.SSLError) as e:
        return None, "Could not reach IMAP at %s:%s (%s)." % (host, port, e)


def decode_header(value):
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        out = []
        for p, enc in parts:
            if isinstance(p, bytes):
                out.append(p.decode(enc or "utf-8", "replace"))
            else:
                out.append(p)
        return "".join(out).strip()[:300]
    except (ValueError, LookupError):
        return str(value)[:300]


def message_ids_of(msg):
    ids = []
    for h in ("In-Reply-To", "References"):
        raw = msg.get(h) or ""
        ids.extend(re.findall(r"<[^<>\s]+>", raw))
    return ids


def fetch_recent(box, days=14, limit=120):
    """Read recent inbox headers. Returns (messages, error)."""
    m, err = imap_open(box)
    if not m:
        return [], err
    items = []
    try:
        m.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - max(1, int(days)) * 86400))
        typ, data = m.search(None, "SINCE", since)
        if typ != "OK":
            return [], "The mail server refused the search."
        ids = (data[0] or b"").split()[-int(limit):]
        for i in reversed(ids):
            typ, d = m.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])")
            if typ != "OK" or not d or not isinstance(d[0], tuple):
                continue
            msg = email.message_from_bytes(d[0][1])
            frm = decode_header(msg.get("From"))
            items.append({
                "from": frm,
                "address": (email.utils.parseaddr(frm)[1] or "").lower(),
                "subject": decode_header(msg.get("Subject")),
                "date": decode_header(msg.get("Date")),
                "message_id": (msg.get("Message-ID") or "").strip(),
                "in_reply_to": message_ids_of(msg),
            })
        return items, ""
    except (imaplib.IMAP4.error, OSError) as e:
        return [], "Reading the inbox failed: %s" % e
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def fetch_snippet(box, message_id, chars=400):
    """Best-effort plain-text preview of one message (by Message-ID)."""
    m, err = imap_open(box)
    if not m:
        return ""
    try:
        m.select("INBOX", readonly=True)
        typ, data = m.search(None, 'HEADER', 'Message-ID', '"%s"' % message_id.strip('<>'))
        if typ != "OK" or not data or not data[0]:
            return ""
        typ, d = m.fetch(data[0].split()[-1], "(BODY.PEEK[TEXT]<0.%d>)" % max(1000, chars * 4))
        if typ != "OK" or not d or not isinstance(d[0], tuple):
            return ""
        raw = d[0][1]
        try:
            msg = email.message_from_bytes(raw)
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        raw = part.get_payload(decode=True) or b""
                        break
            else:
                raw = msg.get_payload(decode=True) or raw
        except Exception:
            pass
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:chars]
    except (imaplib.IMAP4.error, OSError):
        return ""
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
