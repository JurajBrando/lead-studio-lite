"""Fold the big DuckDB / Parquet / SQLite files into two databases.

    EMAILS.duckdb    contacts   - rows with a usable email
    NO_EMAIL.duckdb  contacts   - rows without one, one per normalised host

This is the big-file counterpart of `merge_files`. That job reads whole files
into memory, which is fine for a CSV and impossible for a 10 GB DuckDB, so this
one never moves rows through Python: DuckDB does the work in chunks and the job
only reports progress.

Rows are chunked on hash(dedupe key) % chunks, so every row sharing a key lands
in the same chunk and per-chunk dedupe is complete. Sources already recorded in
ingest_log are skipped, so the job is safe to re-run and safe to interrupt.
"""
import os
import time

from .. import config, db
from .base import Job, Context, JobCancelled

CANON = ["email", "name", "first_name", "last_name", "title", "company", "domain", "host",
         "website", "phone", "country", "state", "city", "industry", "technologies",
         "linkedin", "employees", "revenue", "segment", "source_file"]

ALIAS = {"biz_name": "company", "company_name": "company", "organization": "company",
         "full_name": "name", "contact_name": "name", "job_title": "title", "position": "title",
         "home_url": "website", "url": "website", "site": "website",
         "phones": "phone", "telephone": "phone", "phone_number": "phone",
         "region": "state", "province": "state", "tech": "technologies",
         "tech_stack": "technologies", "employee_count": "employees", "size": "employees",
         "annual_revenue": "revenue", "linkedin_url": "linkedin", "best_email": "email",
         "emails": "email", "email_address": "email", "contact_email": "email"}

HOST = ("lower(trim(regexp_replace(regexp_replace(regexp_extract("
        "regexp_replace(coalesce(nullif(trim(domain),''),nullif(trim(website),''),''),"
        "'^[a-zA-Z]+://',''),'^[^/?#]*'),'^www\\.',''),':[0-9]+$','')))")
HASMAIL = "email is not null and trim(email) <> '' and contains(email,'@')"

DEFAULT_SOURCES = ("leads1.duckdb:contacts, COMBINED_MASTER.duckdb:contacts, "
                   "rec_results.duckdb:rec_results, *.parquet")


def _quoted(cols):
    return ", ".join('"%s"' % c for c in cols)


def _score(cols):
    return " + ".join("""(case when "%s" is not null and "%s"<>'' then 1 else 0 end)""" % (c, c)
                      for c in cols)


def _expand(folder, spec):
    import glob
    if "*" in spec:
        return [os.path.basename(p) for p in sorted(glob.glob(os.path.join(folder, spec)))]
    return [spec]


def _attach_source(con, folder, spec):
    """(relation sql, attached alias or None). Raises ValueError when unusable."""
    name = spec.split(":")[0]
    table = spec.split(":")[1] if ":" in spec else ""
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        raise ValueError("missing")
    low = name.lower()
    if low.endswith(".parquet"):
        return "read_parquet('%s')" % path.replace("\\", "/"), None
    if low.endswith((".csv", ".tsv")):
        return ("read_csv_auto('%s', ignore_errors=true, all_varchar=true)"
                % path.replace("\\", "/")), None
    if low.endswith((".db", ".sqlite", ".sqlite3")):
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute("ATTACH '%s' AS SRC (TYPE sqlite, READ_ONLY)" % path.replace("\\", "/"))
        return "SRC.%s" % table, "SRC"
    con.execute("ATTACH '%s' AS SRC (READ_ONLY)" % path.replace("\\", "/"))
    return "SRC.%s" % table, "SRC"


def run(ctx):
    try:
        import duckdb                               # noqa: F401
    except ImportError:
        return ctx.summary(error="This job needs the duckdb package: pip install duckdb")

    folder = ctx.param("folder") or os.path.dirname(os.path.abspath(config.APP_DIR))
    chunks = max(1, ctx.int_param("chunks", 64))
    delete_source = bool(ctx.param("delete_source", False))
    out_e = os.path.join(folder, ctx.param("emails_db", "EMAILS.duckdb"))
    out_n = os.path.join(folder, ctx.param("nomail_db", "NO_EMAIL.duckdb"))
    specs = [s for raw in ctx.list_param("sources", DEFAULT_SOURCES.split(", "))
             for s in _expand(folder, raw.strip()) if s]

    con = duckdb.connect(out_e)
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory='%s'" % os.path.join(folder, "_dbtmp").replace("\\", "/"))
    con.execute("ATTACH '%s' AS NE" % out_n.replace("\\", "/"))
    cols_sql = ", ".join('"%s" VARCHAR' % c for c in CANON)
    con.execute("create table if not exists contacts (%s, source_db VARCHAR)" % cols_sql)
    con.execute("create table if not exists NE.contacts (%s, source_db VARCHAR)" % cols_sql)
    con.execute("create table if not exists ingest_log (source VARCHAR, ts TIMESTAMP, "
                "rows_in BIGINT, to_emails BIGINT, to_nomail BIGINT, deleted BOOLEAN)")
    one = lambda q: con.execute(q).fetchone()[0]
    done = {r[0] for r in con.execute("select source from ingest_log").fetchall()}

    allc, sc = _quoted(CANON), _score(CANON)
    per_source, errors = [], []
    t_all = time.time()

    pending = [x for x in specs if x not in done]
    steps_total = max(1, len(pending) * chunks * 2)
    steps = [0]

    def step(phase):
        steps[0] += 1
        ctx.progress(steps[0], steps_total, phase)

    ctx.progress(0, steps_total, "%d source(s) to do" % len(pending))

    for spec in specs:
        if spec in done:
            ctx.log("skip %s - already ingested" % spec)
            continue
        att = None
        try:
            rel, att = _attach_source(con, folder, spec)
            src_cols = [r[0] for r in con.execute("describe select * from %s" % rel).fetchall()]

            def pick(target):
                for c in src_cols:
                    cl = c.lower()
                    if cl == target or ALIAS.get(cl) == target:
                        return 'cast("%s" as varchar)' % c
                return "cast(null as varchar)"

            proj = ", ".join('%s as "%s"' % (pick(t), t) for t in CANON if t != "host")
            con.execute("create or replace temp view s0 as select %s from %s" % (proj, rel))
            con.execute("create or replace temp view s as select *, %s as host, '%s' as source_db "
                        "from s0" % (HOST, spec.replace("'", "''")))
            rows_in = one("select count(*) from s")
            b1 = one("select count(*) from contacts")
            b2 = one("select count(*) from NE.contacts")
            ctx.log("%s: %s rows" % (spec, format(rows_in, ",")))

            for i in range(chunks):
                ctx.check_cancel()
                con.execute("""insert into contacts select %s, source_db from (
                    select *, row_number() over (partition by lower(trim(email))
                        order by (%s) desc) rn
                    from s where %s and abs(hash(lower(trim(email)))) %% %d = %d) t
                    where t.rn=1 and not exists (select 1 from contacts x
                        where lower(trim(x.email))=lower(trim(t.email)))"""
                            % (allc, sc, HASMAIL, chunks, i))
                step("%s - emails %d/%d" % (os.path.basename(spec), i + 1, chunks))
                if (i + 1) % 8 == 0 or i == chunks - 1:
                    ctx.log("  emails   %d/%d  +%s" % (i + 1, chunks,
                            format(one("select count(*) from contacts") - b1, ",")))

            for i in range(chunks):
                ctx.check_cancel()
                con.execute("""insert into NE.contacts select %s, source_db from (
                    select *, row_number() over (partition by host order by (%s) desc) rn
                    from s where not (%s) and host is not null and host <> ''
                      and abs(hash(host)) %% %d = %d) t
                    where t.rn=1 and not exists (select 1 from NE.contacts x
                        where x.host=t.host)""" % (allc, sc, HASMAIL, chunks, i))
                step("%s - no-email %d/%d" % (os.path.basename(spec), i + 1, chunks))
                if (i + 1) % 8 == 0 or i == chunks - 1:
                    ctx.log("  no-email %d/%d  +%s" % (i + 1, chunks,
                            format(one("select count(*) from NE.contacts") - b2, ",")))

            con.execute("CHECKPOINT")
            a1 = one("select count(*) from contacts")
            a2 = one("select count(*) from NE.contacts")
            con.execute("insert into ingest_log values (?,now(),?,?,?,?)",
                        [spec, rows_in, a1 - b1, a2 - b2, False])
            con.execute("CHECKPOINT")
            done.add(spec)
            per_source.append({"source": spec, "rows_in": rows_in, "emails": a1 - b1,
                               "no_email": a2 - b2,
                               "collapsed": rows_in - (a1 - b1) - (a2 - b2)})
            ctx.log("%s done: +%s emails, +%s no-email"
                    % (spec, format(a1 - b1, ","), format(a2 - b2, ",")))
        except JobCancelled:
            ctx.log("cancelled during %s - what finished is already committed" % spec)
            if att:
                try:
                    con.execute("DETACH %s" % att)
                except Exception:                    # noqa: BLE001
                    pass
            raise
        except Exception as exc:                     # noqa: BLE001 - one bad source is not fatal
            errors.append("%s: %s" % (spec, exc))
            ctx.warn("skipped %s: %s" % (spec, exc))
        finally:
            if att:
                try:
                    con.execute("DETACH %s" % att)
                except Exception:                    # noqa: BLE001
                    pass

        if delete_source and spec in done:
            base = spec.split(":")[0]
            pending = [s for s in specs if s.split(":")[0] == base and s not in done]
            if pending:
                ctx.log("keeping %s - still pending: %s" % (base, pending))
            else:
                for ext in ("", "-wal", ".wal", "-shm"):
                    target = os.path.join(folder, base + ext)
                    try:
                        if os.path.exists(target):
                            os.remove(target)
                            ctx.log("deleted %s" % os.path.basename(target))
                    except OSError as exc:
                        ctx.warn("could not delete %s: %s" % (target, exc))
                con.execute("update ingest_log set deleted=true where source=?", [spec])

    total_e = one("select count(*) from contacts")
    total_n = one("select count(*) from NE.contacts")
    con.close()
    ctx.log("EMAILS %s / NO_EMAIL %s in %.1f min"
            % (format(total_e, ","), format(total_n, ","), (time.time() - t_all) / 60.0))
    db.event("consolidate_job", "%d source(s), %s emails, %s no-email"
             % (len(per_source), format(total_e, ","), format(total_n, ",")))
    return ctx.summary(sources=len(per_source), emails=total_e, no_email=total_n,
                       folder=folder, errors=errors, detail=per_source)


JOBS = [Job(
    key="consolidate_big",
    name="Consolidate big files into EMAILS / NO_EMAIL",
    group="Data",
    description="Folds the large DuckDB/Parquet/SQLite lead files into two databases - "
                "EMAILS.duckdb for rows with an address, NO_EMAIL.duckdb for the rest, one "
                "row per normalised host. Chunked, resumable, optionally deletes each source "
                "once it is safely ingested.",
    run=run,
    schedule="",
    params={"folder": "", "sources": DEFAULT_SOURCES, "chunks": 64,
            "delete_source": False, "emails_db": "EMAILS.duckdb", "nomail_db": "NO_EMAIL.duckdb"},
    needs_network=False,
)]

# --------------------------------------------------------------------------- parquet store

def run_export(ctx):
    """EMAILS.duckdb + NO_EMAIL.duckdb -> one sorted contacts.parquet.

    One file, every contact, queried in place. Rows that have an address sort first and
    are ordered by email, so DuckDB's row-group statistics let `WHERE email = ?` read one
    row group instead of scanning the file. `has_email` makes the two halves one predicate
    apart.
    """
    try:
        import duckdb
    except ImportError:
        return ctx.summary(error="This job needs the duckdb package: pip install duckdb")

    folder = ctx.param("folder") or os.path.dirname(os.path.abspath(config.APP_DIR))
    out_dir = ctx.param("out_dir") or os.path.join(folder, "LEAD_INDEX")
    out = os.path.join(out_dir, ctx.param("out_file", "contacts.parquet"))
    group = max(100000, ctx.int_param("row_group_size", 1000000))
    os.makedirs(out_dir, exist_ok=True)

    parts = []
    for name, flag in ((ctx.param("emails_db", "EMAILS.duckdb"), "true"),
                       (ctx.param("nomail_db", "NO_EMAIL.duckdb"), "false")):
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            ctx.log("skip %s - not there" % name)
            continue
        alias = "S%d" % len(parts)
        con_sql = "ATTACH '%s' AS %s (READ_ONLY)" % (path.replace("\\", "/"), alias)
        parts.append((alias, con_sql, flag, name))
    if not parts:
        return ctx.summary(error="Neither EMAILS.duckdb nor NO_EMAIL.duckdb was found in %s"
                                 % folder)

    con = duckdb.connect(":memory:")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET temp_directory='%s'" % os.path.join(folder, "_dbtmp").replace("\\", "/"))
    total = 0
    unions = []
    for alias, attach_sql, flag, name in parts:
        con.execute(attach_sql)
        try:
            n = con.execute("select count(*) from %s.contacts" % alias).fetchone()[0]
        except Exception as exc:                     # noqa: BLE001 - an empty db is not fatal
            ctx.warn("%s has no contacts table: %s" % (name, exc))
            continue
        if not n:
            ctx.log("%s is empty - skipping" % name)
            continue
        ctx.log("%s: %s rows" % (name, format(n, ",")))
        total += n
        cols = ", ".join('"%s"' % c for c in CANON)
        unions.append("select %s, source_db, %s as has_email from %s.contacts"
                      % (cols, flag, alias))
    if not unions:
        return ctx.summary(error="Both databases were empty - run consolidate_big first.")

    free_gb = 0.0
    try:
        import shutil
        free_gb = shutil.disk_usage(out_dir).free / 1e9
    except Exception:                                # noqa: BLE001
        pass
    ctx.log("writing %s rows to %s (%.1f GB free)" % (format(total, ","), out, free_gb))
    if free_gb and free_gb < 3:
        ctx.warn("only %.1f GB free - the export may not fit" % free_gb)

    started = time.time()
    ctx.progress(0, 1, "writing parquet (one long COPY, no finer progress)")
    ctx.check_cancel()
    con.execute("""COPY (
        select * from (%s)
        order by (email is null or email = ''), email
    ) TO '%s' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE %d)"""
                % (" union all ".join(unions), out.replace("\\", "/"), group))
    ctx.progress(1, 1, "written")
    size_gb = os.path.getsize(out) / 1e9 if os.path.exists(out) else 0
    rows = con.execute("select count(*) from read_parquet('%s')"
                       % out.replace("\\", "/")).fetchone()[0]
    with_mail = con.execute("select count(*) from read_parquet('%s') where has_email"
                            % out.replace("\\", "/")).fetchone()[0]
    con.close()
    ctx.log("done: %s rows, %.2f GB, %.1f min" % (format(rows, ","), size_gb,
                                                  (time.time() - started) / 60.0))
    db.event("parquet_store", "%s contacts -> %s" % (format(rows, ","), os.path.basename(out)))
    return ctx.summary(path=out, rows=rows, with_email=with_mail,
                       without_email=rows - with_mail, size_gb=round(size_gb, 2))


JOBS.append(Job(
    key="export_contacts_parquet",
    name="Export every contact to one Parquet file",
    group="Data",
    description="Writes EMAILS.duckdb and NO_EMAIL.duckdb into a single sorted "
                "LEAD_INDEX/contacts.parquet - every contact in one place, queried where it "
                "lies instead of imported. Rows with an address sort first and are ordered by "
                "email so a lookup reads one row group.",
    run=run_export,
    schedule="",
    params={"folder": "", "out_dir": "", "out_file": "contacts.parquet",
            "emails_db": "EMAILS.duckdb", "nomail_db": "NO_EMAIL.duckdb",
            "row_group_size": 1000000},
    needs_network=False,
))
