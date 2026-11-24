"""Dropping leads that can never start a conversation.

The list is filled from imports and open data, so it also holds rows with no address at all
and rows whose only address is a consumer mailbox with nothing behind it - a gmail account
that names no company and no site. Neither can open a conversation, yet both sit in every
list, export and enrichment pass.

`junk_reason` is the rule: '' keeps the lead. A free mailbox stays only when a website or its
own domain is attached - the owner of a real business writing from gmail, which is the case
to keep. A company name, an industry, a job title or a city alone is not a business, so a
personal gmail or hotmail address in a government or fishery import still goes. `run` walks
the candidates in bounded id windows over the primary key, copies every removed lead into
`leads_dropped` whole - so "Allow them back" returns it exactly as it was - and takes its
sends, replies, enrollments and signals with it, the same as the Delete button.
"""
import re

from . import db, net

_FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "ymail.com", "rocketmail.com", "msn.com", "icloud.com",
    "me.com", "mac.com", "aol.com", "aim.com", "protonmail.com", "proton.me", "pm.me",
    "web.de", "mail.ru", "inbox.ru", "list.ru", "bk.ru", "ya.ru", "rambler.ru",
    "seznam.cz", "centrum.cz", "email.cz", "volny.cz", "azet.sk", "zoznam.sk", "pobox.sk",
    "post.sk", "t-online.de", "freenet.de", "orange.fr", "wanadoo.fr", "laposte.net",
    "free.fr", "sfr.fr", "libero.it", "virgilio.it", "tiscali.it", "qq.com", "163.com",
    "126.com", "naver.com", "hanmail.net", "rediffmail.com", "sbcglobal.net", "att.net",
    "comcast.net", "verizon.net", "cox.net", "btinternet.com", "sky.com", "bigpond.com",
    "optusnet.com.au", "shaw.ca", "rogers.com", "telus.net", "ukr.net", "interia.pl",
    "o2.pl", "wp.pl", "onet.pl",
}
_FREE_FAMILIES = ("yahoo.", "hotmail.", "outlook.", "live.", "gmx.", "yandex.")
_EMPTY = {"", "-", "--", "\u2014", "\u2013", "n/a", "na", "none", "null", "unknown", "0", "."}
_PLACEHOLDER_SQL = ",".join("'%s'" % value for value in sorted(_EMPTY))
_EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(value):
    return str(value or "").strip().lower()


def is_free_domain(domain):
    """A consumer mailbox provider, not a company's own domain."""
    value = _clean(domain)
    return value in _FREE_DOMAINS or any(value.startswith(fam) for fam in _FREE_FAMILIES)


def is_free_mailbox(email):
    value = _clean(email)
    return "@" in value and is_free_domain(value.rsplit("@", 1)[-1])


def has_business(row):
    """A company, a real site, a phone or a profile - anything that says what this is."""
    if _clean(row.get("company")) not in _EMPTY:
        return True
    if _clean(row.get("website")) not in _EMPTY:
        return True
    if _clean(row.get("phone")) not in _EMPTY:
        return True
    domain = _clean(row.get("domain"))
    if domain not in _EMPTY and not is_free_domain(domain):
        return True
    return "linkedin.com" in _clean(row.get("linkedin"))


def has_site(row):
    """A real website: a domain that is not a mailbox provider, or a website field."""
    if _clean(row.get("website")) not in _EMPTY:
        return True
    domain = _clean(row.get("domain"))
    return domain not in _EMPTY and not is_free_domain(domain)


def junk_reason(row):
    """Why this lead can never be contacted, or '' when it has to stay."""
    email = _clean(row.get("email"))
    if not email:
        return "" if has_business(row) else "no email, no business"
    if not _EMAIL_RX.match(email) or not net.domain_of(email.rsplit("@", 1)[-1]):
        return "not an email"
    if is_free_mailbox(email) and not has_site(row):
        return "private mailbox, no business"
    return ""


def _blank(column):
    return "trim(coalesce(%s,'')) IN (%s)" % (column, _PLACEHOLDER_SQL)


def _free_sql():
    expr = "lower(substr(email, instr(email,'@')+1))"
    parts = ["%s='%s'" % (expr, domain) for domain in sorted(_FREE_DOMAINS)]
    parts += ["%s LIKE '%s%%'" % (expr, family) for family in _FREE_FAMILIES]
    return " OR ".join(parts)


def _candidate_sql():
    """Rows worth a Python look, so the walk does not build 65M dicts to find a few million."""
    free = _free_sql()
    has_email = "trim(coalesce(email,'')) NOT IN (%s)" % _PLACEHOLDER_SQL
    return (
        "SELECT id, email, first_name, last_name, company, title, domain, website, phone, "
        "linkedin FROM leads WHERE id>? AND id<=? AND ("
        "(" + _blank("email") + " AND " + _blank("company") + " AND " + _blank("domain")
        + " AND " + _blank("website") + " AND " + _blank("phone")
        + " AND lower(coalesce(linkedin,'')) NOT LIKE '%linkedin.com%')"
        " OR (" + has_email + " AND instr(email,'@')=0)"
        " OR (" + has_email + " AND (" + free + ") AND " + _blank("domain") + " AND "
        + _blank("website") + ")"
        ") ORDER BY id LIMIT ?")


_CANDIDATES = _candidate_sql()

# Each query reads at most this many ids. One "next 1000 candidates" query would scan tens
# of millions of clean rows and freeze progress and Cancel until it answered.
_WINDOW = 200000


def dropped_count():
    try:
        return db.scalar("SELECT COUNT(*) FROM leads_dropped")
    except Exception:                         # noqa: BLE001 - old database, no table yet
        return 0


def _drop(pairs):
    """Copy the removed leads into the quarantine, then delete them and their history."""
    now = db.now()
    names = ",".join(name for name, _ddl in db.LEAD_COLUMNS)
    by_reason = {}
    for lead_id, reason in pairs:
        by_reason.setdefault(reason, []).append(lead_id)
    dropped = 0
    for reason, ids in by_reason.items():
        for start in range(0, len(ids), 300):
            chunk = tuple(ids[start:start + 300])
            marks = ",".join("?" * len(chunk))

            def work(c, chunk=chunk, marks=marks, reason=reason):
                c.execute(
                    "INSERT OR REPLACE INTO leads_dropped (%s, reason, dropped_ts) "
                    "SELECT %s, ?, ? FROM leads WHERE id IN (%s)" % (names, names, marks),
                    (reason, now) + chunk)
                for table in ("sends", "replies", "enrollments", "signals"):
                    c.execute("DELETE FROM %s WHERE lead_id IN (%s)" % (table, marks), chunk)
                c.execute("DELETE FROM leads WHERE id IN (%s)" % marks, chunk)

            db.transaction(work)
            dropped += len(chunk)
    return dropped


def run(ctx, dry_run=False, batch=1000):
    """Walk the candidates once, dropping what can never be contacted.

    The walk reads bounded id windows instead of asking for "the next N candidates":
    one such query over 65M rows takes minutes and holds Cancel hostage.
    """
    total = db.scalar("SELECT COUNT(*) FROM leads")
    last_id = db.scalar("SELECT COALESCE(MAX(id),0) FROM leads") or 1
    counts = {"candidates": 0, "dropped": 0, "kept": 0}
    reasons = {}
    ctx.log("%d leads in the list%s" % (
        total, " - PREVIEW, nothing is removed" if dry_run else ""))
    cursor = 0
    try:
        while True:
            ctx.check_cancel()
            stop = min(cursor + _WINDOW, last_id)
            rows = db.rows(_CANDIDATES, (cursor, stop, batch))
            if not rows:
                if stop >= last_id:
                    break
                cursor = stop
                continue
            cursor = stop if len(rows) < batch else rows[-1]["id"]
            doomed = []
            for row in rows:
                counts["candidates"] += 1
                reason = junk_reason(row)
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    doomed.append((row["id"], reason))
                else:
                    counts["kept"] += 1
            counts["dropped"] += len(doomed)
            if doomed and not dry_run:
                _drop(doomed)
            ctx.progress(cursor, last_id, "cleaning the list")
            if counts["candidates"] % 50000 < batch:
                ctx.log("%d candidates | %d %s | %d kept" % (
                    counts["candidates"], counts["dropped"],
                    "would go" if dry_run else "removed", counts["kept"]))
    finally:
        db.cache_clear()

    if reasons:
        ctx.log("removed: " + ", ".join("%s %d" % (name, count) for name, count in
                                        sorted(reasons.items(), key=lambda item: -item[1])))
    ctx.log("%d candidates | %d %s | %d kept" % (
        counts["candidates"], counts["dropped"], "would go" if dry_run else "removed",
        counts["kept"]))
    summary = dict(counts)
    summary.update({"ok": True, "dry_run": bool(dry_run), "total_before": total,
                    "total_after": db.scalar("SELECT COUNT(*) FROM leads"),
                    "quarantined": dropped_count(), "reasons": reasons})
    return ctx.summary(**summary)


def restore():
    """Put every quarantined lead back. One whose email is already in the list stays here."""
    names = ",".join(name for name, _ddl in db.LEAD_COLUMNS)
    restored = skipped = 0
    cursor = 0
    while True:
        ids = [row["id"] for row in db.rows(
            "SELECT id FROM leads_dropped WHERE id>? ORDER BY id LIMIT 300", (cursor,))]
        if not ids:
            break
        cursor = ids[-1]
        marks = ",".join("?" * len(ids))
        db.execute("INSERT OR IGNORE INTO leads (%s) SELECT %s FROM leads_dropped "
                   "WHERE id IN (%s)" % (names, names, marks), tuple(ids))
        back = [row["id"] for row in db.rows(
            "SELECT id FROM leads WHERE id IN (%s)" % marks, tuple(ids))]
        if back:
            back_marks = ",".join("?" * len(back))
            db.execute("DELETE FROM leads_dropped WHERE id IN (%s)" % back_marks, tuple(back))
        restored += len(back)
        skipped += len(ids) - len(back)
    db.cache_clear()
    if restored:
        db.event("leads", "restored %d lead(s) from the quarantine" % restored)
    return {"ok": True, "restored": restored, "skipped": skipped}
