"""SQLite storage for Lead Studio Lite.

One file (data/leadstudio.db). Every timestamp is an integer unix time in UTC
seconds, so date maths never touches timezone strings.
"""
import os
import sqlite3
import threading
import time

from . import config, net

# The leads table is defined from one list so a migration can rebuild it from the same
# source of truth. `email` is optional: a contact can be a company row with a description
# and no address, and several such rows have to be allowed (hence the partial index).
LEAD_COLUMNS = [
    ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
    ("email", "TEXT DEFAULT '' COLLATE NOCASE"),
    ("first_name", "TEXT DEFAULT ''"),
    ("last_name", "TEXT DEFAULT ''"),
    ("company", "TEXT DEFAULT ''"),
    ("title", "TEXT DEFAULT ''"),
    ("domain", "TEXT DEFAULT ''"),
    ("phone", "TEXT DEFAULT ''"),
    ("city", "TEXT DEFAULT ''"),
    ("country", "TEXT DEFAULT ''"),
    ("industry", "TEXT DEFAULT ''"),
    ("company_size", "TEXT DEFAULT ''"),
    ("technologies", "TEXT DEFAULT ''"),
    ("description", "TEXT DEFAULT ''"),
    ("linkedin", "TEXT DEFAULT ''"),
    ("website", "TEXT DEFAULT ''"),
    ("notes", "TEXT DEFAULT ''"),
    ("source", "TEXT DEFAULT ''"),
    ("tags", "TEXT DEFAULT ''"),
    ("status", "TEXT DEFAULT 'new'"),
    ("verify_status", "TEXT DEFAULT 'unverified'"),
    ("verify_detail", "TEXT DEFAULT ''"),
    ("created_ts", "INTEGER DEFAULT 0"),
    ("updated_ts", "INTEGER DEFAULT 0"),
    ("last_contact_ts", "INTEGER DEFAULT 0"),
    ("replied_ts", "INTEGER DEFAULT 0"),
]
LEAD_FIELD_NAMES = [name for name, _ in LEAD_COLUMNS if name != "id"]

LEADS_DDL = "CREATE TABLE IF NOT EXISTS %s (\n%s\n);" % (
    "leads", ",\n".join("    %s %s" % (name, ddl) for name, ddl in LEAD_COLUMNS))
LEADS_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_email ON leads(email) WHERE email <> '';
CREATE INDEX IF NOT EXISTS idx_leads_email_lookup ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_leads_company_blank ON leads(company COLLATE NOCASE) WHERE email = '';
"""

# What the Leads tab's cleaner removes: the whole row, so "Allow them back" returns the
# person exactly as they were, plus the reason it went.
LEADS_DROPPED_DDL = "CREATE TABLE IF NOT EXISTS leads_dropped (\n%s,\n    reason TEXT DEFAULT '',\n    dropped_ts INTEGER DEFAULT 0\n);\nCREATE INDEX IF NOT EXISTS idx_leads_dropped_ts ON leads_dropped(dropped_ts);" % (
    ",\n".join("    %s %s" % (name, ddl) for name, ddl in LEAD_COLUMNS))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS mailboxes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    from_name     TEXT DEFAULT '',
    from_email    TEXT NOT NULL,
    reply_to      TEXT DEFAULT '',
    smtp_host     TEXT DEFAULT '',
    smtp_port     INTEGER DEFAULT 587,
    smtp_security TEXT DEFAULT 'starttls',
    smtp_user     TEXT DEFAULT '',
    smtp_pass     TEXT DEFAULT '',
    imap_host     TEXT DEFAULT '',
    imap_port     INTEGER DEFAULT 993,
    imap_user     TEXT DEFAULT '',
    imap_pass     TEXT DEFAULT '',
    daily_limit   INTEGER DEFAULT 30,
    signature     TEXT DEFAULT '',
    active        INTEGER DEFAULT 1,
    last_send_ts  INTEGER DEFAULT 0,
    created_ts    INTEGER DEFAULT 0,
    auth_type     TEXT DEFAULT 'password',
    source        TEXT DEFAULT ''
);

""" + LEADS_DDL + LEADS_DROPPED_DDL + """

CREATE TABLE IF NOT EXISTS lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    created_ts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS list_members (
    list_id INTEGER NOT NULL,
    lead_id INTEGER NOT NULL,
    PRIMARY KEY (list_id, lead_id)
);

CREATE TABLE IF NOT EXISTS sequences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_ts  INTEGER DEFAULT 0,
    updated_ts  INTEGER DEFAULT 0,
    stats_json  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id   INTEGER NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
    step_no       INTEGER NOT NULL,
    delay_days    INTEGER DEFAULT 0,
    subject       TEXT DEFAULT '',
    body          TEXT DEFAULT '',
    stop_on_reply INTEGER DEFAULT 1,
    UNIQUE(sequence_id, step_no)
);

CREATE TABLE IF NOT EXISTS enrollments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id  INTEGER NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
    lead_id      INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    mailbox_id   INTEGER NOT NULL DEFAULT 0,
    status       TEXT DEFAULT 'running',
    step_no      INTEGER DEFAULT 0,
    next_run_ts  INTEGER DEFAULT 0,
    started_ts   INTEGER DEFAULT 0,
    finished_ts  INTEGER DEFAULT 0,
    last_error   TEXT DEFAULT '',
    UNIQUE(sequence_id, lead_id)
);
CREATE INDEX IF NOT EXISTS idx_enroll_due ON enrollments(status, next_run_ts);

CREATE TABLE IF NOT EXISTS sends (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER DEFAULT 0,
    step_id       INTEGER DEFAULT 0,
    lead_id       INTEGER NOT NULL,
    mailbox_id    INTEGER DEFAULT 0,
    to_email      TEXT DEFAULT '',
    subject       TEXT DEFAULT '',
    body          TEXT DEFAULT '',
    message_id    TEXT DEFAULT '',
    token         TEXT DEFAULT '',
    attempts      INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'sent',
    error         TEXT DEFAULT '',
    sent_ts       INTEGER DEFAULT 0,
    opened_ts     INTEGER DEFAULT 0,
    clicked_ts    INTEGER DEFAULT 0,
    opens         INTEGER DEFAULT 0,
    clicks        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sends_lead ON sends(lead_id);
CREATE INDEX IF NOT EXISTS idx_sends_msgid ON sends(message_id);
-- Every tracking pixel and unsubscribe link looks a send up by token, and the dashboard
-- counts sends by status/date: without these it is a full table scan (including bodies).
CREATE INDEX IF NOT EXISTS idx_sends_token ON sends(token);
CREATE INDEX IF NOT EXISTS idx_sends_enrollment ON sends(enrollment_id, step_id);
CREATE INDEX IF NOT EXISTS idx_sends_status ON sends(status, sent_ts);
CREATE INDEX IF NOT EXISTS idx_leads_verify ON leads(verify_status);

CREATE TABLE IF NOT EXISTS replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER DEFAULT 0,
    mailbox_id  INTEGER DEFAULT 0,
    from_email  TEXT DEFAULT '',
    subject     TEXT DEFAULT '',
    snippet     TEXT DEFAULT '',
    message_id  TEXT DEFAULT '',
    received_ts INTEGER DEFAULT 0,
    is_bounce   INTEGER DEFAULT 0,
    seen        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS suppressions (
    email      TEXT PRIMARY KEY COLLATE NOCASE,
    reason     TEXT DEFAULT '',
    created_ts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     INTEGER DEFAULT 0,
    kind   TEXT DEFAULT '',
    detail TEXT DEFAULT ''
);

-- ------------------------------------------------------- automation layer
CREATE TABLE IF NOT EXISTS jobs (
    key          TEXT PRIMARY KEY,
    name         TEXT DEFAULT '',
    grp          TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    schedule     TEXT DEFAULT '',
    enabled      INTEGER DEFAULT 1,
    params       TEXT DEFAULT '{}',
    last_run_ts  INTEGER DEFAULT 0,
    last_status  TEXT DEFAULT '',
    last_summary TEXT DEFAULT '',
    next_run_ts  INTEGER DEFAULT 0,
    running      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key     TEXT NOT NULL,
    started_ts  INTEGER DEFAULT 0,
    finished_ts INTEGER DEFAULT 0,
    status      TEXT DEFAULT '',
    summary     TEXT DEFAULT '',
    log         TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_key, started_ts);

CREATE TABLE IF NOT EXISTS domains (
    domain          TEXT PRIMARY KEY COLLATE NOCASE,
    name            TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    tld             TEXT DEFAULT '',
    first_seen_ts   INTEGER DEFAULT 0,
    updated_ts      INTEGER DEFAULT 0,
    mx_status       TEXT DEFAULT '',
    mx_host         TEXT DEFAULT '',
    mx_checked_ts   INTEGER DEFAULT 0,
    cc_pages        INTEGER DEFAULT 0,
    cc_last_crawl   TEXT DEFAULT '',
    cc_checked_ts   INTEGER DEFAULT 0,
    ads_ts          INTEGER DEFAULT 0,
    hiring_count    INTEGER DEFAULT 0,
    bankrupt_ts     INTEGER DEFAULT 0,
    score           INTEGER DEFAULT 0,
    data            TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_domains_updated ON domains(updated_ts);

-- A dropped row is kept here so an import cannot quietly put it back: a phone number in
-- the domain column and a name without DNS cost a network query in every pass.
CREATE TABLE IF NOT EXISTS domains_dropped (
    domain     TEXT PRIMARY KEY COLLATE NOCASE,
    reason     TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    dropped_ts INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER DEFAULT 0,
    kind       TEXT DEFAULT '',
    domain     TEXT DEFAULT '',
    company    TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    payload    TEXT DEFAULT '{}',
    lead_id    INTEGER DEFAULT 0,
    seen       INTEGER DEFAULT 0,
    UNIQUE(kind, source_url, domain, title)
);
CREATE INDEX IF NOT EXISTS idx_signals_kind ON signals(kind, ts);

CREATE TABLE IF NOT EXISTS brands (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    domain         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    aliases        TEXT DEFAULT '',
    notes          TEXT DEFAULT '',
    active         INTEGER DEFAULT 1,
    created_ts     INTEGER DEFAULT 0,
    last_scan_ts   INTEGER DEFAULT 0,
    last_findings  INTEGER DEFAULT 0,
    checked        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_brands_scan ON brands(active, last_scan_ts);

-- ------------------------------------------------------- offers & providers
CREATE TABLE IF NOT EXISTS offers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    category     TEXT DEFAULT '',
    description  TEXT DEFAULT '',
    affiliate_url TEXT DEFAULT '',
    keywords     TEXT DEFAULT '',
    pitch_subject TEXT DEFAULT '',
    pitch_body   TEXT DEFAULT '',
    commission   TEXT DEFAULT '',
    active       INTEGER DEFAULT 1,
    created_ts   INTEGER DEFAULT 0,
    updated_ts   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS offer_connections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id   INTEGER NOT NULL,
    kind       TEXT DEFAULT 'link',
    label      TEXT DEFAULT '',
    config     TEXT DEFAULT '{}',
    created_ts INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conn_offer ON offer_connections(offer_id);

CREATE TABLE IF NOT EXISTS mcp_items (
    connection TEXT NOT NULL,
    kind       TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    name       TEXT DEFAULT '',
    status     TEXT DEFAULT '',
    payload    TEXT DEFAULT '',
    updated_ts INTEGER DEFAULT 0,
    PRIMARY KEY (connection, kind, item_id)
);

CREATE TABLE IF NOT EXISTS llm_providers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    kind         TEXT DEFAULT 'openai',
    base_url     TEXT DEFAULT '',
    model        TEXT DEFAULT '',
    api_key      TEXT DEFAULT '',
    is_default   INTEGER DEFAULT 0,
    created_ts   INTEGER DEFAULT 0,
    last_test_ts INTEGER DEFAULT 0,
    last_test_ok INTEGER DEFAULT 0
);
""" + LEADS_INDEXES

# Radar (offer cards, hunts, approved companies) and the Army (soldiers that send).
RADAR_ARMY_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_kv (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS radar_cards (
    offer_id   INTEGER PRIMARY KEY,
    card       TEXT DEFAULT '{}',
    weights    TEXT DEFAULT '{}',
    updated_ts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hunts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id    INTEGER NOT NULL,
    ts          INTEGER DEFAULT 0,
    finished_ts INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',
    params      TEXT DEFAULT '{}',
    funnel      TEXT DEFAULT '{}',
    dropped     TEXT DEFAULT '{}',
    sources     TEXT DEFAULT '{}',
    note        TEXT DEFAULT '',
    rows        TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_hunts_offer ON hunts(offer_id, id);
CREATE TABLE IF NOT EXISTS radar_leads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id         INTEGER NOT NULL,
    lead_id          INTEGER DEFAULT 0,
    hunt_id          INTEGER DEFAULT 0,
    domain           TEXT DEFAULT '' COLLATE NOCASE,
    company          TEXT DEFAULT '',
    email            TEXT DEFAULT '' COLLATE NOCASE,
    email_source     TEXT DEFAULT '',
    email_confidence INTEGER DEFAULT 0,
    score            INTEGER DEFAULT 0,
    rank             REAL DEFAULT 0,
    why              TEXT DEFAULT '',
    angle            TEXT DEFAULT '',
    profile          TEXT DEFAULT '{}',
    soldier_id       INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'approved',
    approved_ts      INTEGER DEFAULT 0,
    sent_ts          INTEGER DEFAULT 0,
    UNIQUE(offer_id, domain)
);
CREATE INDEX IF NOT EXISTS idx_radar_leads_work ON radar_leads(offer_id, status, rank);
CREATE INDEX IF NOT EXISTS idx_radar_leads_lead ON radar_leads(lead_id);
CREATE TABLE IF NOT EXISTS soldiers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    mailbox_id   INTEGER NOT NULL UNIQUE,
    offer_id     INTEGER DEFAULT 0,
    state        TEXT DEFAULT 'training',
    xp           INTEGER DEFAULT 0,
    angle        TEXT DEFAULT '',
    angle_locked INTEGER DEFAULT 0,
    persona      TEXT DEFAULT '',
    language     TEXT DEFAULT 'auto',
    created_ts   INTEGER DEFAULT 0,
    updated_ts   INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS army_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER DEFAULT 0,
    soldier_id INTEGER DEFAULT 0,
    lead_id    INTEGER DEFAULT 0,
    offer_id   INTEGER DEFAULT 0,
    kind       TEXT DEFAULT '',
    angle      TEXT DEFAULT '',
    send_id    INTEGER DEFAULT 0,
    xp         INTEGER DEFAULT 0,
    detail     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_army_events_soldier ON army_events(soldier_id, ts);
CREATE INDEX IF NOT EXISTS idx_army_events_lead ON army_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_army_events_kind ON army_events(kind, ts);
CREATE TABLE IF NOT EXISTS army_claims (
    key        TEXT PRIMARY KEY,
    soldier_id INTEGER DEFAULT 0,
    ts         INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS army_bandit (
    offer_id  INTEGER NOT NULL,
    angle     TEXT NOT NULL,
    segment   TEXT NOT NULL DEFAULT '',
    sends     INTEGER DEFAULT 0,
    replies   INTEGER DEFAULT 0,
    positives INTEGER DEFAULT 0,
    ts        INTEGER DEFAULT 0,
    PRIMARY KEY (offer_id, angle, segment)
);
CREATE TABLE IF NOT EXISTS army_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    soldier_id    INTEGER DEFAULT 0,
    lead_id       INTEGER DEFAULT 0,
    radar_lead_id INTEGER DEFAULT 0,
    offer_id      INTEGER DEFAULT 0,
    kind          TEXT DEFAULT 'send',
    angle         TEXT DEFAULT '',
    to_email      TEXT DEFAULT '',
    subject       TEXT DEFAULT '',
    body          TEXT DEFAULT '',
    writer        TEXT DEFAULT '',
    status        TEXT DEFAULT 'pending',
    error         TEXT DEFAULT '',
    created_ts    INTEGER DEFAULT 0,
    sent_ts       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_army_drafts_status ON army_drafts(status, soldier_id);
CREATE TABLE IF NOT EXISTS army_suppress (
    kind   TEXT NOT NULL,
    value  TEXT NOT NULL COLLATE NOCASE,
    reason TEXT DEFAULT '',
    ts     INTEGER DEFAULT 0,
    PRIMARY KEY (kind, value)
);
"""

_local = threading.local()


def conn():
    """One connection per thread (sqlite objects are not thread safe)."""
    c = getattr(_local, "c", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        # One SQL function for country names, so filtering and the facet list agree even
        # for the rows imported before the names were normalised.
        c.create_function("norm_country", 1, net.normalise_country, deterministic=True)
        _local.c = c
    return c


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(RADAR_ARMY_SCHEMA)
    _migrate(c)
    _ensure_leads_indexes(c)
    c.commit()
    return c


def _ensure_leads_indexes(c):
    """Make sure the leads indexes are on `leads`, not left behind on a rebuilt table.

    `CREATE INDEX IF NOT EXISTS` only checks the *name*: after `_rebuild_leads` renamed tables,
    an index with the same name could sit on another table, so every `WHERE email=?` scanned the
    whole table and an import crawled.
    """
    try:
        orphans = c.execute("SELECT name FROM sqlite_master WHERE type='index' "
                            "AND tbl_name<>'leads' AND name LIKE 'idx_leads_%'").fetchall()
    except sqlite3.Error:
        orphans = []
    for row in orphans:
        try:
            c.execute('DROP INDEX IF EXISTS "%s"' % str(row[0]).replace('"', '""'))
        except sqlite3.Error:
            pass
    try:
        c.executescript(LEADS_INDEXES)
    except sqlite3.Error:
        pass


def _leads_shape(c):
    try:
        return {row[1]: row for row in c.execute("PRAGMA table_info(leads)").fetchall()}
    except sqlite3.Error:
        return {}


def _rebuild_leads(c, db_path=None):
    """Copy an old leads table into the current shape.

    SQLite cannot drop a NOT NULL or a UNIQUE constraint in place. The old table made the
    address mandatory and unique, so only one contact could ever have none; the new one has
    a partial unique index instead. Ids are preserved (enrollments, sends and replies point
    at them) and foreign keys are off for the swap, so dropping the old table cannot
    cascade-delete anything.
    """
    columns = _leads_shape(c)
    path = db_path or config.DB_PATH
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = ""
    try:
        candidate = "%s.bak-%s" % (path, stamp)
        destination = sqlite3.connect(candidate)
        with destination:
            c.backup(destination)
        destination.close()
        backup = candidate
    except (sqlite3.Error, OSError):
        backup = ""
    c.commit()
    c.execute("PRAGMA foreign_keys=OFF")
    keep = [name for name in LEAD_FIELD_NAMES if name in columns]
    c.execute("DROP TABLE IF EXISTS leads_migrate")
    c.execute(LEADS_DDL.replace("leads (", "leads_migrate (", 1))
    if keep:
        c.execute("INSERT INTO leads_migrate (%s) SELECT %s FROM leads" % (
            ", ".join(keep), ", ".join(keep)))
    c.execute("DROP TABLE leads")
    c.execute("ALTER TABLE leads_migrate RENAME TO leads")
    c.executescript(LEADS_INDEXES)
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()
    return backup


def _migrate(c):
    """Bring an older database up to the current shape."""
    shape = _leads_shape(c)
    if shape and shape.get("email") and shape["email"][3]:
        backup = _rebuild_leads(c)
        config.log("leads table rebuilt (email is optional now); backup: %s" % (backup or "none"))
    additions = {
        "offers": [("disclosure", "TEXT DEFAULT ''")],
        "sends": [("attempts", "INTEGER DEFAULT 0")],
        "leads": [("description", "TEXT DEFAULT ''"),
                  ("company_size", "TEXT DEFAULT ''"),
                  ("technologies", "TEXT DEFAULT ''")],
        "sequences": [("stats_json", "TEXT DEFAULT ''")],
        "replies": [("class", "TEXT DEFAULT ''"), ("classified_ts", "INTEGER DEFAULT 0"),
                    ("soldier_id", "INTEGER DEFAULT 0")],
        "mailboxes": [("auth_type", "TEXT DEFAULT 'password'"),
                      ("source", "TEXT DEFAULT ''"),
                      ("last_test_ts", "INTEGER DEFAULT 0"),
                      ("last_test_ok", "INTEGER DEFAULT 0"),
                      ("last_imap_ok", "INTEGER DEFAULT 0"),
                      ("last_test_detail", "TEXT DEFAULT ''"),
                      ("oauth_provider", "TEXT DEFAULT ''"),
                      ("oauth_refresh", "TEXT DEFAULT ''"),
                      ("oauth_access", "TEXT DEFAULT ''"),
                      ("oauth_expires", "INTEGER DEFAULT 0"),
                      ("oauth_email", "TEXT DEFAULT ''")],
    }
    for table, columns in additions.items():
        try:
            existing = {row[1] for row in c.execute("PRAGMA table_info(%s)" % table).fetchall()}
        except sqlite3.Error:
            continue
        if not existing:
            continue
        for name, ddl in columns:
            if name not in existing:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))


def now():
    return int(time.time())


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def scalar(sql, args=(), default=0):
    r = conn().execute(sql, args).fetchone()
    if r is None or r[0] is None:
        return default
    return r[0]


LOCK_RETRIES = 6          # on top of the 30 s busy timeout of each attempt


def _is_locked(exc):
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _write(action):
    """Run one write and commit it; wait and try again while another writer holds the file.

    A long job (a bulk update, an import) can keep the write lock past the busy timeout. The
    failed statement is rolled back first, so nothing half-written stays open on this
    connection - an open transaction here is what would lock everybody else out in turn.
    """
    c = conn()
    for attempt in range(LOCK_RETRIES + 1):
        try:
            result = action(c)
            c.commit()
            return result
        except sqlite3.OperationalError as exc:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            if not _is_locked(exc) or attempt == LOCK_RETRIES:
                raise
            time.sleep(min(10, 1 + attempt * 2))
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise


def execute(sql, args=()):
    return _write(lambda c: c.execute(sql, args))


def transaction(action):
    """Run several statements in one commit: a bulk move out of a table has to be one."""
    return _write(action)


def insert(table, data):
    keys = list(data.keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ", ".join(keys), ", ".join("?" for _ in keys))
    cur = execute(sql, [data[k] for k in keys])
    return cur.lastrowid


def insert_many(table, rows):
    """One transaction for a whole batch. A per-row commit makes a 50k import crawl."""
    if not rows:
        return 0
    keys = list(rows[0].keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ", ".join(keys), ", ".join("?" for _ in keys))
    values = [[row.get(k) for k in keys] for row in rows]
    _write(lambda c: c.executemany(sql, values))
    return len(rows)


def execute_many(sql, rows):
    """One transaction for many statements of the same shape (an upsert, a delete batch)."""
    rows = list(rows or [])
    if not rows:
        return 0
    _write(lambda c: c.executemany(sql, rows))
    return len(rows)


def update(table, row_id, data):
    if not data:
        return
    keys = list(data.keys())
    sql = "UPDATE %s SET %s WHERE id=?" % (table, ", ".join("%s=?" % k for k in keys))
    execute(sql, [data[k] for k in keys] + [row_id])


_CACHE = {}
_CACHE_LOCK = threading.Lock()
SLOW_SECONDS = 0.15        # an answer slower than this is worth remembering
CACHE_TTL = 120


def cached(key, compute, ttl=CACHE_TTL):
    """`compute()`, remembered for `ttl` seconds - but only when it was slow to work out.

    Counts over a table of a few thousand rows take a millisecond and are always fresh. Over
    millions of leads the same counts take seconds, and the dashboard asks for them every half
    minute: those are served from here until they expire or `cache_clear()` is called.
    """
    now_ts = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now_ts:
            return hit[1]
    started = time.time()
    value = compute()
    if time.time() - started >= SLOW_SECONDS:
        with _CACHE_LOCK:
            _CACHE[key] = (time.time() + ttl, value)
    return value


def cache_clear():
    with _CACHE_LOCK:
        _CACHE.clear()


def event(kind, detail=""):
    try:
        insert("events", {"ts": now(), "kind": kind, "detail": str(detail)[:2000]})
        execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 5000)")
    except sqlite3.Error:
        pass


def add_suppression(email, reason):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return
    execute("INSERT OR IGNORE INTO suppressions (email, reason, created_ts) VALUES (?,?,?)",
            (email, reason, now()))


def is_suppressed(email):
    return bool(one("SELECT email FROM suppressions WHERE email=?", ((email or "").strip().lower(),)))


def close():
    """Close this thread's connection (used by tests and on shutdown)."""
    c = getattr(_local, "c", None)
    if c is not None:
        try:
            c.close()
        except sqlite3.Error:
            pass
        _local.c = None
