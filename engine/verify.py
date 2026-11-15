"""Email verification without any mandatory third-party package.

Checks, cheapest first:
  1. syntax and obvious typos
  2. role addresses (info@, sales@ ...) flagged as risky, not invalid
  3. disposable / known-bad domains
  4. MX records: dnspython if installed -> nslookup if available -> A record fallback
  5. optional live SMTP RCPT probe (off by default: many servers block or greylist it)
"""
import re
import shutil
import socket
import subprocess
import time

from . import mail

DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com",
    "dispostable.com", "maildrop.cc", "fakeinbox.com", "mytemp.email", "spam4.me",
    "grr.la", "guerrillamailblock.com", "pokemail.net", "tempinbox.com", "emailondeck.com",
}

ROLE_PREFIXES = {
    "admin", "administrator", "info", "sales", "support", "contact", "hello", "help", "billing",
    "office", "team", "marketing", "enquiries", "inquiries", "enquiry", "noreply", "no-reply",
    "postmaster", "webmaster", "abuse", "privacy", "jobs", "careers", "hr", "accounts",
}

TYPO_DOMAINS = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmail.co": "gmail.com",
    "gnail.com": "gmail.com", "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com",
    "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "outlok.com": "outlook.com",
}

BAD_DOMAIN_RX = re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|css|js)$", re.I)
PLACEHOLDER_RX = re.compile(r"(example\.(com|org|net)|test@|your@email|email@domain|n/a|none|"
                            r"^\W*$|@\W)", re.I)


def domain_of(addr):
    return (addr or "").rsplit("@", 1)[-1].strip().lower().strip(".")


def _mx_dnspython(domain):
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=6)
        hosts = sorted(((r.preference, str(r.exchange).rstrip(".").lower()) for r in answers))
        return [h for _, h in hosts if h]
    except Exception:
        return []


def _mx_nslookup(domain):
    exe = shutil.which("nslookup")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "-type=MX", "-timeout=6", domain], capture_output=True, timeout=12)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = p.stdout or b""
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        text = str(raw)
    hosts = re.findall(r"mail exchanger\s*=\s*([A-Za-z0-9.\-]+)", text, re.I)
    if not hosts and "can't find" not in text.lower() and "non-existent" not in text.lower():
        # No MX line and no explicit "not found": let the caller fall back to A records.
        return None
    return [h.rstrip(".").lower() for h in hosts if h]


def _a_record(domain):
    try:
        return bool(socket.getaddrinfo(domain, None, proto=socket.IPPROTO_TCP))
    except (socket.gaierror, UnicodeError):
        return False


def mx_lookup(domain):
    """Returns (hosts, source). source is dnspython | nslookup | a_record | none."""
    for fn, name in ((_mx_dnspython, "dnspython"), (_mx_nslookup, "nslookup")):
        hosts = fn(domain)
        if hosts:
            return hosts, name
        if hosts == []:
            return [], name   # authoritative answer: domain has no MX
    if _a_record(domain):
        return [], "a_record"
    return [], "none"


def smtp_probe(host, to_addr, from_addr, timeout=8):
    """RCPT check. Returns 'accepted' | 'rejected' | 'unknown'."""
    try:
        with socket.create_connection((host, 25), timeout=timeout) as s:
            s.settimeout(timeout)
            f = s.makefile("rb")

            def read():
                lines = []
                while True:
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line.decode("latin-1", "replace"))
                    if len(line) < 4 or line[3:4] != b"-":
                        break
                return "".join(lines)

            read()
            s.sendall(b"EHLO leadstudio.local\r\n")
            read()
            s.sendall(("MAIL FROM:<%s>\r\n" % from_addr).encode())
            read()
            s.sendall(("RCPT TO:<%s>\r\n" % to_addr).encode())
            reply = read()
            s.sendall(b"QUIT\r\n")
            code = (reply[:3] or "").strip()
            if code.startswith("2"):
                return "accepted"
            if code.startswith("5"):
                return "rejected"
            return "unknown"
    except (OSError, socket.timeout, ValueError):
        return "unknown"


def verify(addr, opts=None, from_addr="check@example.com"):
    """Return {email, status, detail, mx}. status in valid|risky|invalid|catch_all|unknown."""
    opts = opts or {}
    addr = (addr or "").strip()
    res = {"email": addr, "status": "invalid", "detail": "", "mx": []}
    if not addr:
        res["detail"] = "No address."
        return res
    if not mail.valid_email(addr):
        res["detail"] = "Not a valid email address."
        return res
    local, domain = addr.rsplit("@", 1)
    domain = domain.lower()

    if PLACEHOLDER_RX.search(addr):
        res["detail"] = "Placeholder or example address."
        return res
    if BAD_DOMAIN_RX.search(domain):
        res["detail"] = "That is a file, not a domain."
        return res
    if domain in DISPOSABLE:
        res["status"] = "risky"
        res["detail"] = "Disposable mailbox provider."
        return res
    if domain in TYPO_DOMAINS:
        res["status"] = "risky"
        res["detail"] = "Likely typo of %s." % TYPO_DOMAINS[domain]
        return res

    hosts, source = ([], "skipped")
    if opts.get("mx", True):
        hosts, source = mx_lookup(domain)
        res["mx"] = hosts
        if source == "none":
            res["detail"] = "Domain does not resolve - the address cannot receive mail."
            return res
        if not hosts and source in ("dnspython", "nslookup"):
            res["detail"] = "Domain has no mail server (no MX record)."
            return res
        if not hosts and source == "a_record":
            hosts = [domain]     # RFC 5321 implicit MX

    role = local.split("+")[0].lower() in ROLE_PREFIXES
    if opts.get("smtp_probe") and hosts:
        verdict = smtp_probe(hosts[0], addr, from_addr)
        if verdict == "rejected":
            res["detail"] = "The mail server rejected this mailbox."
            return res
        if verdict == "accepted":
            res["status"] = "valid"
            res["detail"] = "Server accepted the address" + (" (role account)." if role else ".")

            return res
    res["status"] = "risky" if role else "valid"
    if role:
        res["detail"] = "Role address (info@, sales@...) - real, but lower reply rates."
    elif source == "a_record":
        res["detail"] = "Domain resolves, no MX record found."
    else:
        res["detail"] = "Mail server found%s." % (" via " + source if source not in ("skipped", "none") else "")
    return res


def verify_many(addresses, opts=None, limit=500):
    out = []
    for a in addresses[:limit]:
        out.append(verify(a, opts))
        time.sleep(0.02)
    return out
