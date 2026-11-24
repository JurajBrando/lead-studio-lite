"""What a job actually produced, and a look inside it.

A job that says "done" in a log and nothing else is a job you have to go and verify by
hand. This module turns a run's summary into the files it wrote - with row counts - and
reads the first rows of any of them, so the Daily tab can open the result instead of
describing it.

Nothing here writes; every path is checked against the folders the app is allowed to read.
"""
import csv
import io
import json
import os

from . import config

TABLE_EXTS = (".csv", ".tsv", ".txt", ".parquet", ".duckdb", ".ddb", ".db",
              ".sqlite", ".sqlite3", ".json", ".jsonl", ".ndjson", ".xlsx", ".xlsm")
PREVIEW_LIMIT = 50
CSV_COUNT_CAP = 200 * 1024 * 1024          # past this, report rows as an estimate


def _roots():
    """Folders a result may live in: the app, its data, and the folder holding the app."""
    roots = []
    for path in (getattr(config, "APP_DIR", ""), getattr(config, "DATA_DIR", "")):
        if path:
            roots.append(os.path.abspath(path))
    if roots:
        roots.append(os.path.abspath(os.path.dirname(roots[0])))
    return [r for r in roots if r]


def allowed(path):
    """True when `path` sits inside one of the roots - no escaping with .. or a symlink."""
    try:
        full = os.path.realpath(os.path.abspath(os.path.expanduser(str(path or "").strip())))
    except Exception:                              # noqa: BLE001
        return False
    for root in _roots():
        try:
            if os.path.commonpath([full, os.path.realpath(root)]) == os.path.realpath(root):
                return True
        except ValueError:                         # different drives on Windows
            continue
    return False


def _walk(value, out, depth=0):
    """Every string in a summary that names a file or folder that exists."""
    if depth > 4 or len(out) > 60:
        return
    if isinstance(value, str):
        text = value.strip()
        if len(text) > 3 and allowed(text) and os.path.exists(text):
            out.append(text)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("log", "logs", "error", "errors", "note"):
                continue
            _walk(item, out, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, out, depth + 1)


def files_from_summary(summary):
    """The output files named in a run summary, newest first, de-duplicated."""
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = {}
    found = []
    _walk(summary or {}, found)
    seen, files = set(), []
    for path in found:
        full = os.path.abspath(path)
        if full in seen:
            continue
        seen.add(full)
        if os.path.isdir(full):
            for name in sorted(os.listdir(full))[:40]:
                child = os.path.join(full, name)
                if os.path.isfile(child) and child.lower().endswith(TABLE_EXTS):
                    if child not in seen:
                        seen.add(child)
                        files.append(child)
        elif full.lower().endswith(TABLE_EXTS):
            files.append(full)
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    return files[:40]


def describe(path):
    """Name, size and row count for one result file. Row count is best effort."""
    full = os.path.abspath(path)
    info = {"path": full, "name": os.path.basename(full), "ok": True,
            "size_mb": 0.0, "rows": None, "kind": "", "tables": [], "error": ""}
    if not allowed(full) or not os.path.isfile(full):
        return {"path": full, "name": os.path.basename(full), "ok": False,
                "error": "not a readable result file", "size_mb": 0.0, "rows": None,
                "kind": "", "tables": []}
    info["size_mb"] = round(os.path.getsize(full) / 1048576.0, 1)
    low = full.lower()
    try:
        if low.endswith(".parquet"):
            info["kind"] = "parquet"
            info["rows"] = _parquet_rows(full)
        elif low.endswith((".duckdb", ".ddb")):
            info["kind"] = "duckdb"
            info["tables"] = _duckdb_tables(full)
            info["rows"] = sum(t.get("rows") or 0 for t in info["tables"])
        elif low.endswith((".db", ".sqlite", ".sqlite3")):
            info["kind"] = "sqlite"
            info["tables"] = _sqlite_tables(full)
            info["rows"] = sum(t.get("rows") or 0 for t in info["tables"])
        elif low.endswith((".csv", ".tsv", ".txt")):
            info["kind"] = "csv"
            info["rows"] = _csv_rows(full)
        elif low.endswith((".json", ".jsonl", ".ndjson")):
            info["kind"] = "json"
        elif low.endswith((".xlsx", ".xlsm")):
            info["kind"] = "excel"
    except Exception as exc:                        # noqa: BLE001 - a result is never a crash
        info["error"] = str(exc)[:200]
    return info


def _parquet_rows(path):
    try:
        import pyarrow.parquet as pq               # type: ignore
        return int(pq.ParquetFile(path).metadata.num_rows or 0)
    except Exception:                               # noqa: BLE001
        pass
    try:
        import duckdb                               # type: ignore
        con = duckdb.connect(":memory:")
        try:
            return int(con.execute("select count(*) from read_parquet(?)", [path]).fetchone()[0])
        finally:
            con.close()
    except Exception:                               # noqa: BLE001
        return None


def _duckdb_tables(path):
    import duckdb                                   # type: ignore
    con = duckdb.connect(path, read_only=True)
    try:
        out = []
        rows = con.execute("select table_schema, table_name from information_schema.tables "
                           "order by 1,2").fetchall()
        for schema, name in rows[:20]:
            try:
                n = con.execute('select count(*) from "%s"."%s"' % (schema, name)).fetchone()[0]
            except Exception:                       # noqa: BLE001
                n = None
            out.append({"name": name, "rows": n})
        return out
    finally:
        con.close()


def _sqlite_tables(path):
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("?", "%3f"), uri=True, timeout=5)
    try:
        out = []
        names = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' order by name").fetchall()]
        for name in names[:20]:
            try:
                n = con.execute('select count(*) from "%s"' % name).fetchone()[0]
            except Exception:                       # noqa: BLE001
                n = None
            out.append({"name": name, "rows": n})
        return out
    finally:
        con.close()


def _csv_rows(path):
    size = os.path.getsize(path)
    if size > CSV_COUNT_CAP:
        return None                                 # too big to count cheaply; preview still works
    count = 0
    with open(path, "rb") as handle:
        for _ in handle:
            count += 1
    return max(0, count - 1)                        # minus the header


def preview(path, limit=PREVIEW_LIMIT, table=""):
    """The first rows of a result file: {cols, rows, total, file}."""
    full = os.path.abspath(str(path or ""))
    limit = max(1, min(500, int(limit or PREVIEW_LIMIT)))
    if not allowed(full) or not os.path.isfile(full):
        return {"ok": False, "error": "That file is not somewhere this app may read."}
    low = full.lower()
    try:
        if low.endswith(".parquet"):
            return _preview_sql("select * from read_parquet('%s')" % full.replace("\\", "/"),
                                full, limit)
        if low.endswith((".duckdb", ".ddb")):
            tables = _duckdb_tables(full)
            chosen = table or (tables[0]["name"] if tables else "")
            if not chosen:
                return {"ok": False, "error": "That database has no tables."}
            return _preview_sql('select * from "%s"' % chosen, full, limit,
                                attach=full, tables=tables, table=chosen)
        if low.endswith((".db", ".sqlite", ".sqlite3")):
            return _preview_sqlite(full, limit, table)
        if low.endswith((".csv", ".tsv", ".txt")):
            return _preview_csv(full, limit)
        if low.endswith((".json", ".jsonl", ".ndjson")):
            return _preview_json(full, limit)
        return {"ok": False, "error": "No preview for this kind of file yet."}
    except Exception as exc:                        # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


def _preview_sql(sql, full, limit, attach="", tables=None, table=""):
    import duckdb                                   # type: ignore
    con = duckdb.connect(attach or ":memory:", read_only=bool(attach))
    try:
        cur = con.execute(sql + " limit %d" % limit)
        cols = [d[0] for d in cur.description]
        rows = [["" if v is None else str(v)[:300] for v in r] for r in cur.fetchall()]
        total = None
        try:
            total = con.execute("select count(*) from (%s)" % sql).fetchone()[0]
        except Exception:                           # noqa: BLE001
            pass
        return {"ok": True, "file": full, "cols": cols, "rows": rows, "total": total,
                "tables": tables or [], "table": table}
    finally:
        con.close()


def _preview_sqlite(full, limit, table=""):
    import sqlite3
    tables = _sqlite_tables(full)
    chosen = table or (tables[0]["name"] if tables else "")
    if not chosen:
        return {"ok": False, "error": "That database has no tables."}
    con = sqlite3.connect("file:%s?mode=ro" % full.replace("?", "%3f"), uri=True, timeout=5)
    try:
        cur = con.execute('select * from "%s" limit %d' % (chosen, limit))
        cols = [d[0] for d in cur.description]
        rows = [["" if v is None else str(v)[:300] for v in r] for r in cur.fetchall()]
        total = next((t["rows"] for t in tables if t["name"] == chosen), None)
        return {"ok": True, "file": full, "cols": cols, "rows": rows, "total": total,
                "tables": tables, "table": chosen}
    finally:
        con.close()


def _preview_csv(full, limit):
    with open(full, "rb") as handle:
        raw = handle.read(1024 * 512)
    text = raw.decode("utf-8", "replace")
    sample = text[:8192]
    delim = ","
    for candidate in (",", ";", "\t", "|"):
        if sample.count(candidate) > sample.count(delim):
            delim = candidate
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = []
    cols = []
    for i, row in enumerate(reader):
        if i == 0:
            cols = [str(c)[:120] for c in row]
            continue
        rows.append([str(c)[:300] for c in row])
        if len(rows) >= limit:
            break
    return {"ok": True, "file": full, "cols": cols, "rows": rows,
            "total": _csv_rows(full), "tables": [], "table": ""}


def _preview_json(full, limit):
    rows, cols = [], []
    with open(full, "r", encoding="utf-8", errors="replace") as handle:
        head = handle.read(1024 * 512)
    records = []
    try:
        data = json.loads(head)
        records = data if isinstance(data, list) else [data]
    except ValueError:
        for line in head.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                break
            if len(records) >= limit:
                break
    for record in records[:limit]:
        if isinstance(record, dict):
            for key in record:
                if key not in cols:
                    cols.append(key)
    for record in records[:limit]:
        if isinstance(record, dict):
            rows.append([str(record.get(c, ""))[:300] for c in cols])
    return {"ok": True, "file": full, "cols": cols, "rows": rows,
            "total": len(records), "tables": [], "table": ""}
