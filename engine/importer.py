"""CSV / TSV / Excel import with column auto-mapping and de-duplication."""
import csv
import io
import os
import re

from . import db, mail

# Any of these column names maps onto the app's field of the same target.
ALIASES = {
    "email": ["email", "e-mail", "e_mail", "mail", "email address", "email_address", "contact_email",
              "best_email", "work_email", "business_email", "emailaddress", "adresa", "epost"],
    "first_name": ["first_name", "firstname", "first", "given_name", "fname", "meno", "jmeno"],
    "last_name": ["last_name", "lastname", "last", "surname", "family_name", "lname", "priezvisko"],
    "name": ["name", "full_name", "fullname", "contact_name", "contact", "display_name", "meno_a_priezvisko"],
    "company": ["company", "company_name", "organization", "organisation", "business", "account",
                "employer", "firma", "spolocnost", "brand"],
    "title": ["title", "job_title", "position", "role", "headline", "function", "pozicia"],
    "domain": ["domain", "website", "company_website", "url", "site", "web", "homepage"],
    "phone": ["phone", "phone_number", "mobile", "telephone", "tel", "cell", "telefon"],
    "city": ["city", "town", "locality", "mesto"],
    "country": ["country", "krajina", "nation"],
    "industry": ["industry", "vertical", "sector", "segment", "category", "odvetvie"],
    "linkedin": ["linkedin", "linkedin_url", "linkedin_profile", "li_url"],
    "notes": ["notes", "note", "comment", "comments", "remark", "poznamka"],
    "tags": ["tags", "tag", "labels", "list"],
    "source": ["source", "lead_source", "origin", "zdroj"],
}

FIELD_ORDER = ["email", "first_name", "last_name", "company", "title", "domain", "phone", "city",
               "country", "industry", "linkedin", "notes", "tags", "source"]

MAILTO_RX = re.compile(r"^mailto:", re.I)
ANGLE_RX = re.compile(r"<([^<>]+)>")


def clean_email(value):
    v = (value or "").strip().strip('"').strip("'")
    v = MAILTO_RX.sub("", v)
    m = ANGLE_RX.search(v)
    if m:
        v = m.group(1)
    v = v.replace(" ", "").strip(",;:")
    return v.lower()


def split_name(full):
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def norm_header(h):
    return re.sub(r"[\s\-]+", "_", (h or "").strip().lower()).strip("_")


def map_headers(headers):
    """Return {target_field: column_index} using the alias table."""
    mapping = {}
    used = set()
    normed = [norm_header(h) for h in headers]
    for field, names in ALIASES.items():
        for want in names:
            wn = norm_header(want)
            for i, h in enumerate(normed):
                if i in used or h != wn:
                    continue
                mapping[field] = i
                used.add(i)
                break
            if field in mapping:
                break
    return mapping


def sniff(text):
    sample = text[:20000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def decode_bytes(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "utf-8", "cp1250", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_rows(name, raw):
    """Yield (headers, list_of_dicts) from CSV-ish text, Excel, Parquet, DuckDB or SQLite."""
    lname = (name or "").lower()
    if lname.endswith(".parquet"):
        return _read_parquet(raw)
    if lname.endswith((".duckdb", ".ddb", ".duck")):
        return _read_duckdb(raw)
    if lname.endswith((".db", ".sqlite", ".sqlite3")):
        return _read_sqlite(raw)
    if lname.endswith(".xlsx") or lname.endswith(".xlsm"):
        try:
            import openpyxl  # type: ignore
        except ImportError:
            raise ValueError("This build cannot read Excel files. Save the sheet as CSV and import that.")
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        try:
            headers = [str(c).strip() if c is not None else "" for c in next(it)]
        except StopIteration:
            return [], []
        rows = []
        for values in it:
            if not any(v is not None and str(v).strip() for v in values):
                continue
            rows.append({headers[i]: ("" if v is None else str(v).strip())
                         for i, v in enumerate(values) if i < len(headers) and headers[i]})
        wb.close()
        return headers, rows

    text = decode_bytes(raw) if isinstance(raw, bytes) else raw
    delimiter = sniff(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], []
    rows = []
    for values in reader:
        if not any((v or "").strip() for v in values):
            continue
        rows.append({headers[i]: (values[i] or "").strip()
                     for i in range(min(len(headers), len(values))) if headers[i]})
    return headers, rows


def _scratch(raw, suffix):
    """Write uploaded bytes to a temp file (the DB formats need a real path)."""
    import tempfile
    handle, path = tempfile.mkstemp(prefix="ls_import_", suffix=suffix)
    with os.fdopen(handle, "wb") as fh:
        fh.write(raw)
    return path


def _temp_path(suffix):
    """A path that does not exist yet (DuckDB refuses to open an empty file)."""
    import tempfile
    handle, path = tempfile.mkstemp(prefix="ls_export_", suffix=suffix)
    os.close(handle)
    os.remove(path)
    return path


def _rows_from_records(records):
    headers = []
    for record in records:
        for key in record:
            if key not in headers:
                headers.append(key)
    rows = []
    for record in records:
        rows.append({h: ("" if record.get(h) is None else str(record.get(h))) for h in headers})
    return headers, rows


def _read_parquet(raw):
    """pyarrow first, duckdb second - either can read parquet."""
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        pq = None
    if pq is not None:
        table = pq.read_table(io.BytesIO(raw))
        return _rows_from_records(table.to_pylist())
    try:
        import duckdb  # type: ignore
    except ImportError:
        raise ValueError("Reading Parquet needs pyarrow or duckdb: pip install pyarrow")
    path = _scratch(raw, ".parquet")
    try:
        con = duckdb.connect()
        records = con.execute("SELECT * FROM read_parquet(?)", [path]).fetchdf().to_dict("records")
        con.close()
        return _rows_from_records(records)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


DUCKDB_HINT = ("Reading a DuckDB file needs the duckdb package: pip install duckdb "
               "(then restart the app).")
SQLITE_SKIP = {"sqlite_sequence", "sqlite_stat1"}


def _read_duckdb(raw):
    try:
        import duckdb  # type: ignore
    except ImportError:
        raise ValueError(DUCKDB_HINT)
    path = _scratch(raw, ".duckdb")
    try:
        con = duckdb.connect(path, read_only=True)
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        if not tables:
            raise ValueError("That DuckDB file has no tables.")
        table = tables[0]
        records = con.execute('SELECT * FROM "%s"' % table.replace('"', '""')).fetchdf().to_dict("records")
        con.close()
        return _rows_from_records(records)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _read_sqlite(raw):
    """SQLite works out of the box: the standard library can read it."""
    import sqlite3
    path = _scratch(raw, ".sqlite")
    try:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        names = [n for n in names if n not in SQLITE_SKIP]
        if not names:
            raise ValueError("That SQLite file has no tables.")
        table = names[0]
        rows = con.execute('SELECT * FROM "%s" LIMIT 200000' % table.replace('"', '""')).fetchall()
        records = [dict(r) for r in rows]
        con.close()
        return _rows_from_records(records)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def import_data(name, raw, source="import", update_existing=False, mark_status="new"):
    """Import rows. Returns a summary dict."""
    headers, raw_rows = read_rows(name, raw)
    if not raw_rows:
        return {"ok": False, "error": "No data rows found in that file.", "headers": headers}
    mapping = map_headers(headers)
    if "email" not in mapping:
        return {"ok": False, "headers": headers,
                "error": "Couldn't find an email column. Rename one column to 'email' and try again."}

    seen = set()
    summary = {"ok": True, "file": name, "headers": headers,
               "mapped": {k: headers[v] for k, v in mapping.items()},
               "imported": 0, "updated": 0, "duplicates": 0, "invalid": 0,
               "existing_email": 0, "skipped_blank": 0}
    ts = db.now()
    for row in raw_rows:
        email = clean_email(row.get(headers[mapping["email"]], ""))
        if not email:
            summary["skipped_blank"] += 1
            continue
        if not mail.valid_email(email):
            summary["invalid"] += 1
            continue
        if email in seen:
            summary["duplicates"] += 1
            continue
        seen.add(email)

        data = {}
        for field, idx in mapping.items():
            if field == "email":
                continue
            val = (row.get(headers[idx], "") or "").strip()
            if val:
                data[field] = val
        if "name" in data:
            fname, lname = split_name(data.pop("name"))
            data.setdefault("first_name", fname)
            if lname:
                data.setdefault("last_name", lname)
        if not data.get("domain"):
            data["domain"] = email.rsplit("@", 1)[-1]
        data["email"] = email
        data.setdefault("source", source)

        existing = db.one("SELECT id FROM leads WHERE email=?", (email,))
        if existing:
            if update_existing:
                patch = {k: v for k, v in data.items() if v and not db.one(
                    "SELECT %s AS v FROM leads WHERE id=?" % k, (existing["id"],))["v"]}
                if patch:
                    patch["updated_ts"] = ts
                    db.update("leads", existing["id"], patch)
                    summary["updated"] += 1
                    continue
            summary["existing_email"] += 1
            continue

        data.update({"created_ts": ts, "updated_ts": ts,
                     "status": mark_status or "new", "verify_status": "unverified"})
        if db.is_suppressed(email):
            data["status"] = "unsubscribed"
        db.insert("leads", data)
        summary["imported"] += 1

    db.event("import", "%s: +%d new, %d existing, %d invalid" % (
        name, summary["imported"], summary["existing_email"], summary["invalid"]))
    return summary


EXPORT_COLS = ["email", "first_name", "last_name", "company", "title", "domain", "phone", "city",
               "country", "industry", "linkedin", "website", "status", "verify_status", "tags",
               "source", "notes", "created_ts", "last_contact_ts", "replied_ts"]


def lead_records(status="", search=""):
    sql = "SELECT * FROM leads"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if search:
        where.append("(email LIKE ? OR company LIKE ? OR first_name LIKE ? OR last_name LIKE ?)")
        like = "%%%s%%" % search
        args.extend([like] * 4)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    data = db.rows(sql, args)
    return [{k: r.get(k, "") for k in EXPORT_COLS} for r in data]


def export_parquet(status="", search=""):
    """Parquet via pyarrow, or duckdb if that is what is installed."""
    records = lead_records(status, search)
    if not records:
        raise ValueError("There are no leads to export yet.")
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        pa = None
    if pa is not None:
        buf = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(records), buf, compression="snappy")
        return buf.getvalue()
    try:
        import duckdb  # type: ignore
    except ImportError:
        raise ValueError("Parquet export needs pyarrow or duckdb: pip install pyarrow")
    path = _temp_path(".parquet")
    try:
        import pandas as pd  # type: ignore
        con = duckdb.connect()
        con.register("frame", pd.DataFrame(records))
        con.execute("COPY frame TO '%s' (FORMAT PARQUET)" % path.replace("\\", "/"))
        con.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def export_duckdb(status="", search=""):
    """A real .duckdb file with one `leads` table."""
    try:
        import duckdb  # type: ignore
    except ImportError:
        raise ValueError("DuckDB export needs the duckdb package: pip install duckdb")
    records = lead_records(status, search)
    if not records:
        raise ValueError("There are no leads to export yet.")
    path = _temp_path(".duckdb")
    try:
        con = duckdb.connect(path)
        con.execute("CREATE TABLE leads (%s)" % ", ".join('"%s" VARCHAR' % c for c in EXPORT_COLS))
        con.executemany("INSERT INTO leads VALUES (%s)" % ",".join("?" for _ in EXPORT_COLS),
                        [[str(r.get(c) or "") for c in EXPORT_COLS] for r in records])
        con.execute("CHECKPOINT")
        con.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# Excel and Google Sheets evaluate a cell that starts with one of these characters, so a
# company or note that came from a scraped page could run a formula on the user's machine.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    text = "" if value is None else str(value)
    if text[:1] in _FORMULA_START:
        return "'" + text
    return text


def export_csv(status="", search=""):
    sql = "SELECT * FROM leads"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if search:
        where.append("(email LIKE ? OR company LIKE ? OR first_name LIKE ? OR last_name LIKE ?)")
        like = "%%%s%%" % search
        args.extend([like] * 4)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    data = db.rows(sql, args)
    cols = ["email", "first_name", "last_name", "company", "title", "domain", "phone", "city",
            "country", "industry", "linkedin", "website", "status", "verify_status", "tags",
            "source", "notes", "created_ts", "last_contact_ts", "replied_ts"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in data:
        w.writerow({k: _csv_cell(r.get(k, "")) for k in cols})
    # The BOM tells Excel this is UTF-8; without it Windows Excel opens the file as ANSI
    # and every accented name turns into mojibake.
    return "\ufeff" + buf.getvalue(), len(data)
