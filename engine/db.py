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

""" + LEADS_DDL + """

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
        "mailboxes": [("auth_type", "TEXT DEFAULT 'password'"),
                      ("source", "TEXT DEFAULT ''"),
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


def execute(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


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
    c = conn()
    c.executemany(sql, [[row.get(k) for k in keys] for row in rows])
    c.commit()
    return len(rows)


def update(table, row_id, data):
    if not data:
        return
    keys = list(data.keys())
    sql = "UPDATE %s SET %s WHERE id=?" % (table, ", ".join("%s=?" % k for k in keys))
    execute(sql, [data[k] for k in keys] + [row_id])


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
