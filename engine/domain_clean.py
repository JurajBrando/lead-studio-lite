"""Dropping the catalogue rows that are not domains.

The catalogue is filled from lead imports and open-data feeds, so it also holds phone
numbers, street addresses, wildcards, IP addresses and names reserved for tests. Each of
them costs a network query in every pass that walks the list, and none of them can ever
produce a contact.

`junk_reason` is the free half: a string that is not a host at all. `dns_state` is the
paid half: one lookup that says whether the name still exists. `run` walks the catalogue
in keyset pages, copies what it removes into `domains_dropped` (so a later import cannot
quietly add it back) and never touches the leads table.
"""
import re
import socket
import threading

from . import db, net

_LOCAL = threading.local()

RESERVED_TLDS = {"local", "localhost", "localdomain", "internal", "intranet", "corp",
                 "invalid", "test", "example", "lan", "home", "arpa"}
TEST_DOMAINS = {"example.com", "example.org", "example.net", "example.edu",
                "domain.com", "yourdomain.com", "mydomain.com"}
IPV4_RX = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
TLD_RX = re.compile(r"^(?:[a-z]{2,24}|xn--[a-z0-9-]{2,60})$")


def junk_reason(value):
    """Why this catalogue row is not a domain, or '' when it is one."""
    raw = str(value or "").strip()
    if not raw:
        return "empty"
    if IPV4_RX.match(raw):
        return "ip address"
    host = net.domain_of(raw)
    if not host:
        return "not a host"
    if IPV4_RX.match(host):
        return "ip address"
    if host in TEST_DOMAINS:
        return "placeholder"
    labels = host.split(".")
    if len(host) > 253 or any(len(label) > 63 for label in labels):
        return "too long"
    tld = labels[-1]
    if tld in RESERVED_TLDS:
        return "reserved tld"
    if not TLD_RX.match(tld):
        return "bad tld"
    return ""


def _resolver():
    resolver = getattr(_LOCAL, "resolver", None)
    if resolver is None:
        import dns.resolver                  # installed with the app; optional elsewhere
        resolver = dns.resolver.Resolver()
        resolver.timeout = 4
        resolver.lifetime = 8
        _LOCAL.resolver = resolver
    return resolver


def _socket_state(host):
    try:
        socket.getaddrinfo(host, None)
        return "alive"
    except socket.gaierror as exc:
        # 11001 on Windows, -2/-3 on the BSD resolver: the name does not exist.
        return "dead" if getattr(exc, "errno", None) in (11001, -2, -3) else "unknown"
    except (OSError, UnicodeError):
        return "unknown"


def dns_state(host):
    """'alive' (has an address), 'dead' (the name does not exist) or 'unknown'.

    Only NXDOMAIN counts as dead. A name that exists but has no web address yet, a
    resolver that refuses the question and a timeout all have to keep the row.
    """
    try:
        import dns.resolver
    except ImportError:
        return _socket_state(host)
    resolver = _resolver()
    for rtype in ("A", "AAAA"):
        try:
            resolver.resolve(host, rtype)
            return "alive"
        except dns.resolver.NXDOMAIN:
            return "dead"
        except dns.resolver.NoAnswer:
            continue
        except Exception:                     # noqa: BLE001 - one bad name is not fatal
            return "unknown"
    return "unknown"


def dropped_count():
    try:
        return db.scalar("SELECT COUNT(*) FROM domains_dropped")
    except Exception:                         # noqa: BLE001 - old database, no table yet
        return 0


def restore():
    """Forget the quarantine, so an import may bring those domains back."""
    cleared = dropped_count()
    db.execute("DELETE FROM domains_dropped")
    return {"ok": True, "cleared": cleared}


def _drop(rows):
    """Copy the removed rows into the quarantine, then delete them, in batches."""
    now = db.now()
    db.execute_many(
        "INSERT OR REPLACE INTO domains_dropped (domain, reason, source, dropped_ts) "
        "VALUES (?,?,?,?)",
        [(domain, reason, source, now) for domain, reason, source in rows])
    names = [domain for domain, _reason, _source in rows]
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        db.execute("DELETE FROM domains WHERE domain IN (%s)" % ",".join("?" * len(chunk)),
                   chunk)


def _estimate(ctx, sample, workers):
    """The share of the catalogue that no longer resolves, for the preview only."""
    from concurrent.futures import ThreadPoolExecutor

    rows = db.rows("SELECT domain FROM domains WHERE rowid % 997 = 0 LIMIT ?",
                   (max(50, int(sample)),))
    names = [row["domain"] for row in rows if not junk_reason(row["domain"])]
    if not names:
        return 0.0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        states = list(pool.map(dns_state, names))
    dead = sum(1 for state in states if state == "dead")
    share = round(100.0 * dead / len(names), 1)
    ctx.log("preview: %d sampled, %d do not resolve (%.1f%%)" % (len(names), dead, share))
    return share


def run(ctx, dry_run=False, dns=True, workers=64, batch=1000, sample=1000):
    """Walk the catalogue once, dropping junk and (optionally) names that do not exist."""
    from concurrent.futures import ThreadPoolExecutor

    total = db.scalar("SELECT COUNT(*) FROM domains")
    counts = {"checked": 0, "junk": 0, "dead": 0, "kept": 0, "unknown": 0}
    reasons = {}
    estimate = None
    ctx.log("%d domains in the catalogue%s" % (
        total, " - PREVIEW, nothing is removed" if dry_run else ""))
    if dry_run and dns and sample:
        estimate = _estimate(ctx, sample, workers)

    pool = ThreadPoolExecutor(max_workers=workers) if (dns and not dry_run) else None
    cursor = ""
    try:
        while True:
            ctx.check_cancel()
            rows = db.rows("SELECT domain, source FROM domains WHERE domain>? "
                           "ORDER BY domain LIMIT ?", (cursor, batch))
            if not rows:
                break
            cursor = rows[-1]["domain"]
            doomed, checkable = [], []
            for row in rows:
                counts["checked"] += 1
                reason = junk_reason(row["domain"])
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
                    doomed.append((row["domain"], reason, row["source"] or ""))
                else:
                    checkable.append(row)
            counts["junk"] += len(doomed)
            if pool is not None and checkable:
                states = list(pool.map(dns_state, [row["domain"] for row in checkable]))
                for row, state in zip(checkable, states):
                    if state == "dead":
                        counts["dead"] += 1
                        reasons["dead"] = reasons.get("dead", 0) + 1
                        doomed.append((row["domain"], "dead", row["source"] or ""))
                    elif state == "unknown":
                        counts["unknown"] += 1
                    else:
                        counts["kept"] += 1
            else:
                counts["kept"] += len(checkable)
            if doomed and not dry_run:
                _drop(doomed)
            ctx.progress(counts["checked"], total, "cleaning the catalogue")
            if counts["checked"] % 5000 < batch:
                ctx.log("%d/%d | %d junk | %d dead | %d kept" % (
                    counts["checked"], total, counts["junk"], counts["dead"],
                    counts["kept"]))
    finally:
        if pool is not None:
            pool.shutdown(wait=False)

    if dry_run and not dns:
        ctx.log("DNS was not checked, so dead domains are not counted in this preview")
    if reasons:
        ctx.log("removed: " + ", ".join("%s %d" % (name, count) for name, count in
                                        sorted(reasons.items(), key=lambda item: -item[1])))
    ctx.log("%d checked | %d junk | %d dead | %d kept | %d unknown" % (
        counts["checked"], counts["junk"], counts["dead"], counts["kept"],
        counts["unknown"]))
    summary = dict(counts)
    summary.update({"ok": True, "dry_run": bool(dry_run), "dns": bool(dns),
                    "total_before": total,
                    "total_after": db.scalar("SELECT COUNT(*) FROM domains"),
                    "reasons": reasons})
    if estimate is not None:
        summary["dead_estimate_pct"] = estimate
    return ctx.summary(**summary)
