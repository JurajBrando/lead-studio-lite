"""Bulk setup for sender mailboxes: a CSV of senders, live probes, sender DNS.

The app creates mailboxes from a Manyreach export, but that export has no
passwords in it - so the boxes existed and could never sign in. This module
reads the provider's own CSV (address, password, SMTP/IMAP hosts), fills the
existing rows instead of duplicating them, and never blanks a saved password.
"""
import csv
import os

from . import db, mail

SECURITY = {
    "ssl": "ssl", "ssl/tls": "ssl", "ssl-tls": "ssl", "tls": "ssl", "smtps": "ssl",
    "starttls": "starttls", "tls/starttls": "starttls", "starttls/tls": "starttls",
    "none": "none", "plain": "none", "no": "none", "off": "none",
}


def security_value(raw, port=0):
    """`SSL/TLS` from a provider sheet means SSL on 465, STARTTLS on 587."""
    value = (raw or "").strip().lower()
    if value in SECURITY:
        mapped = SECURITY[value]
        if mapped == "ssl" and int(port or 0) == 587:
            return "starttls"
        return mapped
    return "ssl" if int(port or 0) == 465 else "starttls"


def _read_csv(path):
    last = None
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                text = fh.read(4096)
                fh.seek(0)
                delimiter = ","
                try:
                    delimiter = csv.Sniffer().sniff(text, delimiters=",;\t").delimiter
                except csv.Error:
                    pass
                return list(csv.DictReader(fh, delimiter=delimiter))
        except UnicodeDecodeError as exc:
            last = exc
    raise last or ValueError("Could not read that file.")


def _row(row, *names):
    for name in names:
        for key in row:
            if key and key.strip().lower() == name:
                return (row.get(key) or "").strip()
    return ""


def _int(value, default=0):
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def import_senders_csv(path, activate="yes", fill_suffix=""):
    """Fill mailboxes from a provider senders sheet.

    `activate`: "yes" - only rows whose connect_now starts with yes go live,
    the rest are imported paused (reserve); "all" - every row goes live;
    "keep" - an existing active flag is not changed.
    `fill_suffix` (".eu"): the password in this sheet also goes into every
    mailbox of that suffix that has none, and those boxes are activated - one
    Seznam account holds aliases on many of its domains.
    """
    path = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "That file does not exist: %s" % (path or "(no path)")}
    try:
        rows = _read_csv(path)
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": "Could not read the CSV: %s" % exc}
    if not rows:
        return {"ok": False, "error": "The file has no rows."}
    if not any(k and k.strip().lower() == "email" for k in rows[0]):
        return {"ok": False, "error": "No email column found. Expected email, password, "
                                      "smtp_host, smtp_port, smtp_security, imap_host, "
                                      "imap_port, connect_now."}
    names = {(r.get("from_name") or "").strip() for r in rows}
    shared_name = names.pop() if len(names) == 1 else ""
    created = updated = with_password = active = paused = skipped = 0
    shared_password = sender_host = sender_imap = ""
    errors = []
    for index, row in enumerate(rows, start=2):
        email = _row(row, "email").lower()
        if not mail.valid_email(email):
            skipped += 1
            if len(errors) < 20:
                errors.append("row %d: not an email address (%s)" % (index, email or "empty"))
            continue
        password = _row(row, "password", "smtp_pass", "pass")
        if password and not shared_password:
            shared_password = password
        sender_host = sender_host or _row(row, "smtp_host")
        sender_imap = sender_imap or _row(row, "imap_host")
        port = _int(_row(row, "smtp_port"))
        connect_now = _row(row, "connect_now")
        want_active = None
        if activate == "all":
            want_active = 1
        elif activate == "yes":
            want_active = 1 if connect_now.lower().startswith("yes") else 0
        existing = db.one("SELECT * FROM mailboxes WHERE lower(from_email)=?", (email,))
        local = email.split("@", 1)[0]
        data = {
            "from_name": _row(row, "from_name", "first_name") or (existing or {}).get("from_name") or "",
            "smtp_host": _row(row, "smtp_host") or (existing or {}).get("smtp_host") or "",
            "smtp_port": port or (existing or {}).get("smtp_port") or 587,
            "smtp_security": security_value(_row(row, "smtp_security"),
                                            port or (existing or {}).get("smtp_port") or 587),
            "smtp_user": _row(row, "smtp_user") or email,
            "imap_host": _row(row, "imap_host") or (existing or {}).get("imap_host") or "",
            "imap_port": _int(_row(row, "imap_port")) or (existing or {}).get("imap_port") or 993,
            "imap_user": _row(row, "imap_user") or email,
        }
        if password:
            data["smtp_pass"] = password
            data["imap_pass"] = password
            with_password += 1
        if want_active is not None:
            data["active"] = want_active
        if existing:
            name = ""
            if shared_name and len(rows) > 1:
                name = local
            elif data["from_name"]:
                name = data["from_name"]
            if name and (existing.get("name") or "").strip() != name:
                data["name"] = name
            db.update("mailboxes", existing["id"], data)
            updated += 1
            if data.get("active", existing.get("active")):
                active += 1
            else:
                paused += 1
        else:
            data["name"] = (local if shared_name else (data["from_name"] or local))
            data["from_email"] = email
            data["created_ts"] = db.now()
            data["source"] = "senders-csv"
            data["daily_limit"] = 20
            data.setdefault("active", 1)
            db.insert("mailboxes", data)
            created += 1
            if data["active"]:
                active += 1
            else:
                paused += 1
    filled = activated = made_active = 0
    suffix = (fill_suffix or "").strip().lower()
    if suffix and shared_password:
        for box in db.rows("SELECT * FROM mailboxes WHERE lower(from_email) LIKE ?",
                           ("%" + suffix,)):
            patch = {}
            had_password = bool((box.get("smtp_pass") or "").strip())
            if not (box.get("smtp_pass") or "").strip():
                patch["smtp_pass"] = shared_password
                filled += 1
            if not (box.get("imap_pass") or "").strip():
                patch["imap_pass"] = shared_password
            if sender_host and not (box.get("smtp_host") or "").strip():
                patch["smtp_host"] = sender_host
            if sender_imap and not (box.get("imap_host") or "").strip():
                patch["imap_host"] = sender_imap
            local = (box.get("from_email") or "").split("@", 1)[0]
            if local and (box.get("name") or "").strip() in (
                    "", (box.get("from_name") or "").strip()):
                patch["name"] = local
            if not int(box.get("active") or 0) and not had_password:
                patch["active"] = 1
                made_active += 1
            if patch:
                db.update("mailboxes", box["id"], patch)
                activated += 1
        active += made_active
    db.event("mailbox", "senders CSV %s: %d new, %d updated, %d with a password, "
             "%d active, %d filled for %s" % (os.path.basename(path), created, updated,
                                              with_password, active, filled, suffix or "-"))
    return {"ok": True, "path": path, "rows": len(rows), "created": created,
            "updated": updated, "with_password": with_password, "active": active,
            "paused": paused, "skipped": skipped, "filled": filled,
            "activated": made_active, "filled_touched": activated, "errors": errors}


def store_probe(box_id, result):
    db.update("mailboxes", box_id, {
        "last_test_ts": db.now(),
        "last_test_ok": 1 if result.get("smtp") == "ok" else 0,
        "last_imap_ok": 1 if result.get("imap") == "ok" else 0,
        "last_test_detail": (result.get("smtp_detail") or result.get("imap_detail") or "")[:400],
    })


def select_boxes(domains="", only_password=False):
    sql = "SELECT * FROM mailboxes"
    args = []
    wanted = [d.strip().lower() for d in (domains or "").split(",") if d.strip()]
    if wanted:
        # ".eu" matches every Seznam address on any of the account's domains.
        sql += " WHERE " + " OR ".join("lower(from_email) LIKE ?" for _ in wanted)
        args = [("%" + d) if d.startswith(".") else ("%@" + d) for d in wanted]
    sql += " ORDER BY id"
    rows = db.rows(sql, tuple(args))
    if only_password:
        rows = [r for r in rows if (r.get("smtp_pass") or "").strip()]
    return rows


# --------------------------------------------------------------- sender DNS
def domains_from_dns_csv(path):
    rows = _read_csv(os.path.abspath(os.path.expanduser((path or "").strip().strip('"'))))
    seen = []
    for row in rows:
        domain = _row(row, "domena", "domain", "doména").lower()
        if domain and domain not in seen:
            seen.append(domain)
    return seen


def _resolve(name, kind):
    import dns.resolver
    try:
        return [r for r in dns.resolver.resolve(name, kind, lifetime=8)]
    except dns.resolver.NXDOMAIN:
        return None
    except dns.resolver.NoAnswer:
        return []
    except Exception as exc:                            # noqa: BLE001
        return "error: %s" % exc


def _txt_values(name):
    answers = _resolve(name, "TXT")
    if not isinstance(answers, list):
        return answers
    values = []
    for record in answers:
        try:
            values.append(b"".join(record.strings).decode("utf-8", "replace"))
        except AttributeError:
            values.append(str(record).strip('"'))
    return values


def _cname_value(name):
    answers = _resolve(name, "CNAME")
    if not isinstance(answers, list):
        return answers
    return [str(r.target).rstrip(".").lower() for r in answers]


def check_dns(domains):
    """SPF, the three Seznam DKIM CNAMEs and DMARC, as the provider sheet asks."""
    report = []
    for domain in domains:
        txts = _txt_values(domain)
        issues = []
        spf_value = ""
        if isinstance(txts, str):
            issues.append("DNS lookup failed (%s)" % txts)
            spf_state = "unknown"
        else:
            spf = [t for t in txts if t.lower().startswith("v=spf1")]
            if not spf:
                spf_state = "missing"
                issues.append("no SPF TXT at the root")
            else:
                spf_value = spf[0]
                spf_state = "ok" if "include:spf.seznam.cz" in spf_value.lower() else "wrong"
                if spf_state == "wrong":
                    issues.append("SPF does not include spf.seznam.cz: %s" % spf_value)
        dkim_ok = 0
        for key in ("szn1", "szn2", "szn3"):
            name = "%s._domainkey.%s" % (key, domain)
            target = _cname_value(name)
            if target == ["%s._domainkey.seznam.cz" % key]:
                dkim_ok += 1
            else:
                issues.append("%s is %s, expected CNAME %s._domainkey.seznam.cz" % (
                    name, target if target else "missing", key))
        dmarc = _txt_values("_dmarc." + domain)
        dmarc_value = ""
        if isinstance(dmarc, str):
            dmarc_state = "unknown"
        elif not dmarc:
            dmarc_state = "missing"
            issues.append("no DMARC TXT at _dmarc")
        else:
            dmarc_value = dmarc[0]
            dmarc_state = "ok" if dmarc_value.lower().startswith("v=dmarc1") else "wrong"
            if dmarc_state == "wrong":
                issues.append("DMARC is not v=DMARC1: %s" % dmarc_value)
        leftovers = []
        old = _txt_values("dkim._domainkey." + domain)
        if isinstance(old, list) and old:
            leftovers.append("old dkim._domainkey TXT still present")
        for name in ("autoconfig." + domain, "autodiscover." + domain):
            target = _cname_value(name)
            if isinstance(target, list) and any("nevtan" in t for t in target):
                leftovers.append("%s still points at %s" % (name, target[0]))
        report.append({
            "domain": domain, "spf": spf_state, "spf_value": spf_value,
            "dkim_ok": dkim_ok, "dmarc": dmarc_state, "dmarc_value": dmarc_value,
            "leftovers": leftovers, "issues": issues,
            "ok": spf_state == "ok" and dkim_ok == 3 and dmarc_state == "ok" and not leftovers,
        })
    return report
