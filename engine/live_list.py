"""Keeping only the domains that a downloaded live-websites list contains.

DNSniffer's Live Websites Dataset ships as a ZIP of CSV parts with one `Domain`
column - about 285 million names. They do not fit in a Python set, and filling a
Bloom filter of that size takes the better part of an hour, so the comparison
runs the other way round: the catalogue is read into a set once (a few million
rows) and the file is streamed through it. One pass, an exact answer, and
nothing is written to disk or kept in date.

Same policy as the DNSniffer live check: a catalogue domain that the list does
not contain goes to the quarantine (`domains_dropped`), the leads table is never
touched, and a domain that already produced a lead is kept - a contact proves it
is real. Nothing is removed before the whole file has been read, so a preview
and the real run cost the same; preview only skips the removal.
"""
import os
import zipfile
from itertools import islice

from . import db, domain_clean

SUGGESTED = (
    r"C:\Users\admin\Desktop\Modulario\all_domains-g8x08z4rmk6kyualximdqojd5d20ibm6.zip",
)
CHUNK = 2000                 # catalogue rows handled per quarantine batch
BLOCK = 4 << 20              # bytes read from the list at a time
HEADERS = (b"domain", b"domains", b"domain name", b"host")


def suggested_path():
    """The file the tile offers first: one of them, if it is on this computer."""
    for path in SUGGESTED:
        if os.path.isfile(path):
            return path
    return ""


def _parts(path):
    """What to read: every member of a ZIP, every file in a folder, or one plain file."""
    path = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if not path:
        raise ValueError("Give the path of the live-websites list first.")
    if not os.path.exists(path):
        raise ValueError("That file does not exist: %s" % path)
    if os.path.isdir(path):
        parts = [(os.path.join(path, name), os.path.getsize(os.path.join(path, name)))
                 for name in sorted(os.listdir(path))
                 if name.lower().endswith((".csv", ".txt"))]
        if not parts:
            raise ValueError("No .csv or .txt file in that folder.")
        return "folder", path, parts, sum(size for _name, size in parts)
    if zipfile.is_zipfile(path):
        archive = zipfile.ZipFile(path)
        parts = [(info.filename, info.file_size) for info in archive.infolist()
                 if not info.is_dir()]
        archive.close()
        if not parts:
            raise ValueError("That ZIP is empty.")
        return "zip", path, parts, sum(size for _name, size in parts)
    return "file", path, [(path, os.path.getsize(path))], os.path.getsize(path)


def status():
    """What the tile shows: the file it will read and how much there is of it."""
    path = suggested_path()
    out = {"path": path, "exists": False, "kind": "", "parts": 0, "bytes": 0}
    try:
        if path and os.path.exists(path):
            kind, _source, parts, total = _parts(path)
            out.update({"exists": True, "kind": kind, "parts": len(parts), "bytes": total})
    except Exception:                          # noqa: BLE001 - a badge is never fatal
        pass
    return out


def _name(raw):
    """One CSV cell to a bare name, or b"" for a header, a blank line or something unusable.

    The fast path is the one that matters: the export is a million lines of plain lowercase
    names, so a line is only reworked when it looks like a header, a URL or a quoted cell.
    A per-line `strip().lower()` chain here cost about five times the scan time.
    """
    if raw.islower():                       # the export is lowercase names, all of them
        value = raw.strip()
        if value and value not in HEADERS:
            return value
    value = raw.strip().lower()
    if not value or value in HEADERS:
        return b""
    head = value[:1]
    if head in (b'"', b"'", b"/") or b"://" in value or b"/" in value:
        value = value.strip(b'"').strip(b"'")
        if b"://" in value:
            value = value.split(b"://", 1)[1]
        if b"/" in value:
            value = value.split(b"/", 1)[0]
        value = value.strip(b".").strip()
    return value


def _leads_in(names):
    """Which of these catalogue rows already produced a lead; those are never removed."""
    found = set()
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        found.update(row[0] for row in db.conn().execute(
            "SELECT DISTINCT domain FROM leads WHERE domain IN (%s)"
            % ",".join("?" for _ in chunk), chunk))
    return found


def _sources(names):
    """The catalogue row's source, for the quarantine entry."""
    out = {}
    for start in range(0, len(names), 400):
        chunk = names[start:start + 400]
        out.update({row[0]: (row[1] or "live_list") for row in db.conn().execute(
            "SELECT domain, source FROM domains WHERE domain IN (%s)"
            % ",".join("?" for _ in chunk), chunk)})
    return out


def _match_block(body, wanted):
    """Drop everything this block of lines finds in the catalogue; return how many."""
    if body.islower() and b"/" not in body and b'"' not in body:
        found = wanted.intersection(body.split(b"\n"))
        if found:
            wanted.difference_update(found)
        return len(found)
    hits = 0
    for line in body.split(b"\n"):
        value = _name(line)
        if value and value in wanted:
            wanted.discard(value)
            hits += 1
    return hits


def _scan(ctx, open_part, parts, wanted, total_bytes, total):
    """Stream every part through the catalogue set, one block at a time.

    A block is split and intersected in C. The first version tested each line in Python and
    ran at a seventh of this, because every line paid for a loop, a strip and a lookup; a
    block that is plain lowercase - which the export is - now costs one split and one
    intersection. Only a block with a capital, a quote or a slash goes through `_name` line
    by line, where a header, a URL or a quoted cell is unwrapped first.
    """
    found = lines = read = 0
    for name, size in parts:
        ctx.check_cancel()
        handle = open_part(name)
        try:
            carry = b""
            while True:
                block = handle.read(BLOCK)
                if not block:
                    break
                read += len(block)
                if carry:
                    block = carry + block
                cut = block.rfind(b"\n")
                if cut < 0:
                    carry = block
                    continue
                carry = block[cut + 1:]
                body = block[:cut]
                lines += body.count(b"\n") + 1
                found += _match_block(body, wanted)
                ctx.check_cancel()
                ctx.progress(read, total_bytes, "reading the live websites list")
            if carry:
                lines += 1
                found += _match_block(carry, wanted)
        finally:
            handle.close()
        ctx.log("%s read | %.1f of %.1f GB | %d of %d catalogue domains found in the list"
                % (os.path.basename(name), read / 1e9, total_bytes / 1e9, found, total))
    return found, lines


def run(ctx, path="", dry_run=False):
    """Read the whole list once and quarantine every catalogue domain it does not contain."""
    path = path or suggested_path()
    kind, source, parts, total_bytes = _parts(path)
    ctx.log("Live websites list: %s (%s, %d part(s), %.1f GB of CSV to read)" % (
        os.path.basename(source), kind, len(parts), total_bytes / 1e9))
    ctx.log("Reading the list once is exact - no index, no key, no quota - and takes ten to "
            "fifteen minutes for the full DNSniffer export.")

    catalogue = db.conn().execute("SELECT domain FROM domains WHERE domain<>''")
    wanted = set()
    for row in catalogue:
        wanted.add(str(row[0]).encode("utf-8", "ignore"))
    total = len(wanted)
    ctx.log("%d catalogue domains to look for%s" % (
        total, " (PREVIEW, nothing is removed)" if dry_run else ""))
    if not total:
        return ctx.summary(ok=True, note="The catalogue has no domains to check.")

    archive = zipfile.ZipFile(source) if kind == "zip" else None
    try:
        found, lines = _scan(
            ctx,
            (lambda name: archive.open(name)) if archive else (lambda name: open(name, "rb")),
            parts, wanted, total_bytes, total)
    finally:
        if archive is not None:
            archive.close()

    missing = total - found
    ctx.log("list read: %d names | %d catalogue domains are in it | %d are not" % (
        lines, found, missing))
    if not missing:
        return ctx.summary(ok=True, checked=total, present=found, missing=0, junk=0,
                           kept_lead=0, removed=0, dry_run=bool(dry_run),
                           total_before=total, total_after=total)

    counts = {"junk": 0, "kept_lead": 0, "removed": 0}
    done = 0
    iterator = iter(wanted)
    while True:
        ctx.check_cancel()
        chunk = list(islice(iterator, CHUNK))
        if not chunk:
            break
        names = [value.decode("utf-8", "ignore") for value in chunk]
        doomed, alive = [], []
        for name in names:
            reason = domain_clean.junk_reason(name)
            if reason:
                doomed.append((name, reason))
            else:
                alive.append(name)
        counts["junk"] += len(doomed)
        if alive:
            with_lead = _leads_in(alive)
            counts["kept_lead"] += len(with_lead)
            rest = [name for name in alive if name not in with_lead]
            doomed.extend((name, "not in the live websites list") for name in rest)
        if doomed:
            sources = _sources([item[0] for item in doomed])
            rows = [(name, reason, sources.get(name, "live_list"))
                    for name, reason in doomed]
            if not dry_run:
                domain_clean._drop(rows)
                counts["removed"] += len(rows)
        done += len(chunk)
        ctx.progress(done, missing, "quarantining domains")
        ctx.log("%d/%d not in the list | %d junk | %d kept (has a lead) | %d quarantined" % (
            done, missing, counts["junk"], counts["kept_lead"], counts["removed"]))

    ctx.log("%d checked | %d in the list | %d missing | %d junk | %d kept (has a lead) | "
            "%d quarantined" % (total, found, missing, counts["junk"], counts["kept_lead"],
                                counts["removed"]))
    return ctx.summary(ok=True, checked=total, present=found, missing=missing,
                       junk=counts["junk"], kept_lead=counts["kept_lead"],
                       removed=counts["removed"], dry_run=bool(dry_run),
                       total_before=total,
                       total_after=db.scalar("SELECT COUNT(*) FROM domains"))
