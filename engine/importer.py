"""CSV / TSV / Excel / Parquet / DuckDB / SQLite import with column auto-mapping.

An email address is welcome but no longer required: a row that identifies a company (with
a description and maybe a domain) is a contact too. What every row does need is something
that names it - an address, a company, a domain, a LinkedIn URL or a person's name - so a
file of descriptions alone cannot turn into thousands of identical blank leads.
"""
import csv
import io
import json
import os
import re
import sqlite3

from . import db, lead_clean, mail, net

# Any of these column names maps onto the app's field of the same target.
ALIASES = {
    "email": ["email", "e-mail", "e_mail", "mail", "email address", "email_address", "contact_email",
              "best_email", "primary_email", "work_email", "business_email", "emailaddress",
              "emails", "email_addresses", "adresa", "epost"],
    "first_name": ["first_name", "firstname", "first", "given_name", "fname", "meno", "jmeno"],
    "last_name": ["last_name", "lastname", "last", "surname", "family_name", "lname", "priezvisko"],
    "name": ["name", "full_name", "fullname", "contact_name", "contact", "display_name",
             "person_name", "meno_a_priezvisko"],
    "company": ["company", "company_name", "organization", "organisation", "organization_name",
                "business", "business_name", "account", "employer", "biz_name", "brand",
                "firma", "spolocnost"],
    "title": ["title", "job_title", "position", "role", "headline", "function", "pozicia"],
    "domain": ["domain", "url", "site", "web", "homepage", "root_domain", "home_url"],
    "website": ["website", "company_website", "website_url"],
    "description": ["description", "company_description", "business_description", "about",
                    "company_about", "summary", "short_description", "site_description",
                    "text300", "bio", "popis"],
    "phone": ["phone", "phone_number", "mobile", "telephone", "tel", "cell", "telefon"],
    "city": ["city", "town", "locality", "mesto"],
    "country": ["country", "krajina", "nation"],
    "industry": ["industry", "vertical", "sector", "segment", "category", "odvetvie"],
    "company_size": ["company_size", "company size", "size", "employees", "employee_count",
                     "employees_count", "headcount", "staff", "company_size_range",
                     "number_of_employees", "velkost"],
    "technologies": ["technologies", "technology", "tech", "tech_stack", "techstack",
                     "technologies_used"],
    "linkedin": ["linkedin", "linkedin_url", "linkedin_profile", "li_url"],
    "notes": ["notes", "note", "comment", "comments", "remark", "poznamka"],
    "tags": ["tags", "tag", "labels", "list"],
    "source": ["source", "lead_source", "origin", "zdroj", "source_file"],
}

FIELD_ORDER = ["email", "first_name", "last_name", "company", "title", "domain", "website",
               "description", "phone", "city", "country", "industry", "company_size",
               "technologies", "linkedin", "notes", "tags", "source"]

# Company size is stored as one of these, so a "51-200" filter means the same thing for
# every row, whatever the source file called it ("120", "51 - 200", "1,000+"...).
SIZE_BUCKETS = ("1-10", "11-50", "51-200", "201-500", "501-1000", "1000+")


def size_bucket(value):
    """'120' / '51-200' / '1,000+' -> one of SIZE_BUCKETS."""
    text = _cell(value).strip().lower().replace("–", "-").replace("—", "-").replace(" ", "")
    text = text.replace(",", "")
    if not text:
        return ""
    for bucket in SIZE_BUCKETS:
        if text == bucket:
            return bucket
    match = re.match(r"^(\d+)", text)
    if not match:
        return ""
    number = int(match.group(1))
    if text.endswith("+") and number >= 1000:
        return "1000+"
    if number <= 10:
        return "1-10"
    if number <= 50:
        return "11-50"
    if number <= 200:
        return "51-200"
    if number <= 500:
        return "201-500"
    if number <= 1000:
        return "501-1000"
    return "1000+"

# A row with none of these cannot be told apart from the next one.
IDENTITY_FIELDS = ("email", "company", "domain", "linkedin", "name", "first_name", "last_name")

MAILTO_RX = re.compile(r"^mailto:", re.I)
ANGLE_RX = re.compile(r"<([^<>]+)>")
EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
DOMAIN_RX = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$")
BATCH = 500
MAX_SEEN_KEYS = 2000000   # in-file duplicate keys kept in memory before giving up


def _cell(value):
    """Any parquet/duckdb value as a clean string."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace").strip()
    if isinstance(value, float) and value != value:      # NaN
        return ""
    return str(value).strip()


def clean_email(value):
    v = _cell(value).strip('"').strip("'")
    if not v:
        return ""
    v = MAILTO_RX.sub("", v)
    m = ANGLE_RX.search(v)
    if m:
        v = m.group(1)
    v = v.replace(" ", "").strip(",;:'\"()[]<>")
    if EMAIL_RX.fullmatch(v):
        return v.lower()
    found = EMAIL_RX.search(v)
    if found:                                            # "a@b.com, c@d.com" or a JSON list
        return found.group(0).lower()
    return v.lower()


def clean_domain(value):
    """https://www.Example.com/path -> example.com. A bare word is not a domain."""
    v = _cell(value).lower()
    if not v:
        return ""
    v = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", v)
    v = v.split("/")[0].split("?")[0].split("#")[0]
    if "@" in v:
        v = v.split("@")[-1]
    v = v.split(":")[0]
    v = re.sub(r"^www\.", "", v).strip(". ")
    return v if DOMAIN_RX.match(v) else ""


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


# ------------------------------------------------------------------ sources
def path_kind(path):
    """csv/tsv/txt, json, excel, parquet, duckdb or sqlite - by the file name."""
    lower = str(path or "").lower()
    if lower.endswith((".csv", ".tsv", ".txt", ".dat", ".tab")):
        return "csv"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        return "excel"
    if lower.endswith((".parquet", ".pq")):
        return "parquet"
    if lower.endswith((".duckdb", ".ddb", ".duck")):
        return "duckdb"
    if lower.endswith((".db", ".sqlite", ".sqlite3")):
        return "sqlite"
    return ""


def _read_json(raw):
    """A JSON file: a list of records, or an object holding one (rows/data/contacts/leads)."""
    text = decode_bytes(raw) if isinstance(raw, bytes) else raw
    data = json.loads(text)
    if isinstance(data, dict):
        for key in ("rows", "data", "contacts", "leads", "items", "records", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list) or not data:
        return [], []
    headers = []
    for record in data:
        if isinstance(record, dict):
            for key in record:
                if str(key) not in headers:
                    headers.append(str(key))
    rows = [{key: _cell(record.get(key)) for key in headers}
            for record in data if isinstance(record, dict)]
    return headers, rows


def read_rows(name, raw):
    """(headers, rows) from CSV-ish text, Excel, Parquet, DuckDB or SQLite bytes."""
    lname = (name or "").lower()
    if lname.endswith(".json"):
        return _read_json(raw)
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
            rows.append({headers[i]: _cell(v)
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
        rows.append({h: _cell(record.get(h)) for h in headers})
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


# ------------------------------------------------------------------ files on this computer
# The files people actually have can be many gigabytes (LEAD_INDEX: a 10 GB DuckDB with 62
# million rows), so a path import streams instead of reading the file into memory, and it
# stops at a row cap the user sets.
def _check_path(path):
    full = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
    if not os.path.isfile(full):
        raise ValueError("That file does not exist: %s" % (full or "(empty)"))
    kind = path_kind(full)
    if not kind:
        raise ValueError("That file type cannot be imported. Use CSV, TSV, Excel, Parquet, "
                         "DuckDB or SQLite.")
    return full, kind


def _table_names(con, kind):
    if kind == "duckdb":
        rows = con.execute("SHOW TABLES").fetchall()
        names = [r[0] for r in rows]
        counts = {}
        try:
            for name, size in con.execute(
                    "SELECT table_name, estimated_size FROM duckdb_tables()").fetchall():
                counts[name] = int(size or 0)
        except Exception:                       # noqa: BLE001 - older duckdb
            counts = {}
        return [{"name": n, "rows": counts.get(n)} for n in names]
    if kind == "sqlite":
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        return [{"name": n, "rows": con.execute(
            'SELECT count(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0]}
            for n in names if n not in SQLITE_SKIP]
    return []


def _columns_of(con, kind, table):
    if kind == "duckdb":
        return [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=? "
            "ORDER BY ordinal_position", [table]).fetchall()]
    if kind == "sqlite":
        return [r[1] for r in con.execute(
            'PRAGMA table_info("%s")' % table.replace('"', '""')).fetchall()]
    return []


def inspect_source(path, table=""):
    """What a file on disk offers: tables, columns, row counts. No reading of the data."""
    try:
        full, kind = _check_path(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    size_mb = round(os.path.getsize(full) / 1048576.0, 1)
    info = {"ok": True, "path": full, "name": os.path.basename(full), "kind": kind,
            "size_mb": size_mb, "tables": [], "table": "", "columns": [], "rows": None}
    try:
        if kind == "parquet":
            import pyarrow.parquet as pq  # type: ignore
            meta = pq.ParquetFile(full).metadata
            info["rows"] = meta.num_rows
            info["columns"] = [f.name for f in pq.ParquetFile(full).schema_arrow]
            info["tables"] = [{"name": "(the file)", "rows": meta.num_rows}]
        elif kind in ("duckdb", "sqlite"):
            if kind == "duckdb":
                try:
                    import duckdb  # type: ignore
                except ImportError:
                    raise ValueError(DUCKDB_HINT)
                con = duckdb.connect(full, read_only=True)
            else:
                import sqlite3
                con = sqlite3.connect(full)
            try:
                info["tables"] = _table_names(con, kind)
                names = [t["name"] for t in info["tables"]]
                chosen = table if table in names else (names[0] if names else "")
                info["table"] = chosen
                if chosen:
                    info["rows"] = next((t.get("rows") for t in info["tables"]
                                         if t["name"] == chosen), None)
                    info["columns"] = _columns_of(con, kind, chosen)
            finally:
                con.close()
        elif kind == "excel":
            try:
                import openpyxl  # type: ignore
            except ImportError:
                raise ValueError("This build cannot read Excel files. Save the sheet as CSV.")
            wb = openpyxl.load_workbook(full, read_only=True, data_only=True)
            ws = wb.active
            info["columns"] = [str(c.value or "") for c in next(ws.iter_rows(max_row=1))[0:]]
            info["rows"] = ws.max_row
            info["tables"] = [{"name": ws.title, "rows": ws.max_row}]
            info["table"] = ws.title
            wb.close()
        else:                                    # csv
            with open(full, "rb") as fh:
                head = fh.read(65536)
            text = decode_bytes(head)
            first = text.splitlines()[0] if text else ""
            info["columns"] = [c.strip() for c in re.split(r"[,;\t|]", first) if c.strip()]
            info["rows"] = None
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                     # noqa: BLE001 - a broken file is not a crash
        return {"ok": False, "error": "Could not open that file: %s" % exc}
    return info


def _safe_where(where):
    text = str(where or "").strip().rstrip(";").strip()
    if not text:
        return ""
    if any(token in text for token in (";", "--", "/*", "*/")):
        raise ValueError("Put one condition in the filter - no semicolons or comments.")
    return text


CSV_SAMPLE_BYTES = 256 * 1024


def _csv_encoding(full):
    """The encoding to open a CSV with: its BOM if it has one, else the first that fits."""
    import codecs
    with open(full, "rb") as fh:
        head = fh.read(CSV_SAMPLE_BYTES)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "cp1252", "latin-1"):
        try:
            codecs.getincrementaldecoder(encoding)().decode(head, final=False)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _csv_source_rows(full, limit=0, report=None, size=0, skip=0):
    """A CSV/TSV file read one row at a time, so its size does not matter.

    Only a small sample at the start is decoded to find the encoding, the delimiter and
    the header; the rest is pulled row by row and never held in memory. `skip` rows are
    counted and dropped, which is how a resumed run continues where the last one stopped.
    """
    size = os.path.getsize(full) or size
    fh = open(full, "r", encoding=_csv_encoding(full), errors="replace", newline="")
    try:
        sample = fh.read(CSV_SAMPLE_BYTES)
        fh.seek(0)
        reader = csv.reader(fh, delimiter=sniff(sample))
        try:
            headers = [h.strip() for h in next(reader)]
        except StopIteration:
            fh.close()
            return [], []
    except Exception:
        fh.close()
        raise
    skip = int(skip or 0)

    def rows():
        seen = 0
        try:
            for values in reader:
                if not any((v or "").strip() for v in values):
                    continue
                seen += 1
                if report and seen % 5000 == 0:
                    # Bytes read, not rows: the row count of an 18 GB file is not known
                    # before its last line, but the end of the file is.
                    report(fh.buffer.tell(), size, "reading the file", seen)
                if seen <= skip:
                    continue
                yield {headers[i]: (values[i] or "").strip()
                       for i in range(min(len(headers), len(values))) if headers[i]}
                if limit and seen - skip >= limit:
                    return
        finally:
            fh.close()
    return headers, rows()


def _excel_source_rows(full, limit=0, report=None, skip=0):
    """An .xlsx sheet read row by row through the read-only mode of openpyxl."""
    try:
        import openpyxl  # type: ignore
    except ImportError:
        raise ValueError("This build cannot read Excel files. Save the sheet as CSV and import that.")
    wb = openpyxl.load_workbook(full, read_only=True, data_only=True)
    ws = wb.active
    try:
        it = ws.iter_rows(values_only=True)
        try:
            headers = [str(c).strip() if c is not None else "" for c in next(it)]
        except StopIteration:
            wb.close()
            return [], []
    except Exception:
        wb.close()
        raise
    total = int(getattr(ws, "max_row", 0) or 0)
    skip = int(skip or 0)

    def rows():
        seen = 0
        try:
            for values in it:
                if not any(v is not None and str(v).strip() for v in values):
                    continue
                seen += 1
                if report and seen % 2000 == 0:
                    report(seen + 1, total, "reading the file", seen)
                if seen <= skip:
                    continue
                yield {headers[i]: _cell(v) for i, v in enumerate(values)
                       if i < len(headers) and headers[i]}
                if limit and seen - skip >= limit:
                    return
        finally:
            wb.close()
    return headers, rows()


def iter_source_rows(path, table="", where="", limit=0, report=None, size=0, skip=0):
    """(headers, generator of row dicts). Every format streams; none is read whole."""
    full, kind = _check_path(path)
    where = _safe_where(where)
    if kind == "parquet":
        if where:
            raise ValueError("A filter needs a DuckDB or SQLite file; a Parquet file has no "
                             "query engine here. Use 'Only rows with an email', or import the "
                             "DuckDB file instead.")
        import pyarrow.parquet as pq  # type: ignore
        handle = pq.ParquetFile(full)
        headers = [f.name for f in handle.schema_arrow]
        total_rows = int(handle.metadata.num_rows or 0)

        def parquet_rows():
            seen = 0
            for batch in handle.iter_batches(batch_size=2000):
                for record in batch.to_pylist():
                    seen += 1
                    if seen <= int(skip or 0):
                        continue
                    yield {k: _cell(v) for k, v in record.items()}
                    if limit and seen - int(skip or 0) >= limit:
                        return
                if report:
                    report(min(seen, total_rows or seen), total_rows, "reading the file", seen)
        return headers, parquet_rows()

    if kind in ("duckdb", "sqlite"):
        if kind == "duckdb":
            try:
                import duckdb  # type: ignore
            except ImportError:
                raise ValueError(DUCKDB_HINT)
            con = duckdb.connect(full, read_only=True)
        else:
            import sqlite3
            con = sqlite3.connect(full)
        tables = [t["name"] for t in _table_names(con, kind)]
        chosen = table if table in tables else (tables[0] if tables else "")
        if not chosen:
            con.close()
            raise ValueError("That file has no tables.")
        sql = 'SELECT * FROM "%s"' % chosen.replace('"', '""')
        if where:
            sql += " WHERE " + where
        if limit:
            sql += " LIMIT %d OFFSET %d" % (int(limit), int(skip or 0))
        elif skip:
            sql += " LIMIT 9223372036854775807 OFFSET %d" % int(skip)
        cursor = con.execute(sql)
        headers = [d[0] for d in cursor.description]

        def db_rows():
            try:
                seen = 0
                while True:
                    batch = cursor.fetchmany(2000)
                    if not batch:
                        return
                    for record in batch:
                        if isinstance(record, (tuple, list)):
                            yield {h: _cell(v) for h, v in zip(headers, record)}
                        else:
                            yield {h: _cell(record[h]) for h in headers}
                        seen += 1
                    if report:
                        report(seen, 0, "reading the file", seen)
            finally:
                con.close()
        return headers, db_rows()

    if kind == "json":
        # A JSON document is parsed whole, so it is the one format that still needs a
        # memory budget. CSV and Excel stream and have no size limit.
        size = os.path.getsize(full) or size
        if size > 200 * 1048576:
            raise ValueError("A JSON file that large is read into memory whole. Save it as "
                             "CSV or a DuckDB file: those stream and have no size limit.")
        with open(full, "rb") as fh:
            raw = fh.read()
        if report:
            report(size, size, "parsing", 0)
        headers, rows = read_rows(os.path.basename(full), raw)
        if skip:
            rows = rows[int(skip):]
        if limit:
            rows = rows[:int(limit)]
        if report:
            report(len(rows), len(rows), "parsed", len(rows))
        return headers, rows

    if kind == "excel":
        return _excel_source_rows(full, limit=limit, report=report, skip=skip)

    return _csv_source_rows(full, limit=limit, report=report, size=size, skip=skip)


def _state_key(full, table, where):
    import hashlib
    raw = "%s|%s|%s" % (os.path.abspath(full), table or "", where or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _ensure_state_table():
    db.execute("CREATE TABLE IF NOT EXISTS import_state (key TEXT PRIMARY KEY, path TEXT, "
               "table_name TEXT DEFAULT '', where_sql TEXT DEFAULT '', offset_rows INTEGER DEFAULT 0, "
               "examined INTEGER DEFAULT 0, imported INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, "
               "finished INTEGER DEFAULT 0, summary TEXT DEFAULT '', updated_ts INTEGER DEFAULT 0)")


def import_state(path, table="", where=""):
    """What was already read from this file, so the next run continues where it stopped."""
    try:
        full = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
        _ensure_state_table()
        row = db.one("SELECT * FROM import_state WHERE key=?",
                     (_state_key(full, table, where),))
        return dict(row) if row else {}
    except Exception:                             # noqa: BLE001 - a broken state is just none
        return {}


def _save_state(key, full, table, where, offset_rows, summary, finished=False):
    try:
        _ensure_state_table()
        payload = {"offset_rows": int(offset_rows), "examined": int(summary.get("examined") or 0),
                   "imported": int(summary.get("imported") or 0),
                   "updated": int(summary.get("updated") or 0),
                   "finished": 1 if finished else 0,
                   "summary": json.dumps({k: summary.get(k) for k in
                                          ("imported", "updated", "duplicates", "invalid",
                                           "existing_email", "no_email", "examined")}),
                   "updated_ts": db.now()}
        existing = db.one("SELECT key FROM import_state WHERE key=?", (key,))
        if existing:
            # db.update() writes "WHERE id=?", and this table's primary key is a text `key`.
            db.execute("UPDATE import_state SET offset_rows=?, examined=?, imported=?, updated=?, "
                       "finished=?, summary=?, updated_ts=? WHERE key=?",
                       (payload["offset_rows"], payload["examined"], payload["imported"],
                        payload["updated"], payload["finished"], payload["summary"],
                        payload["updated_ts"], key))
        else:
            db.insert("import_state", dict(payload, key=key, path=full,
                                           table_name=table or "", where_sql=where or ""))
    except Exception:                             # noqa: BLE001 - never lose the run over state
        pass


def import_path(path, table="", where="", limit=0, source="", update_existing=False,
                mark_status="new", only_email=False, report=None, resume=True, restart=False,
                business_only=False):
    """Import rows from a file on this computer, streaming and resumable.

    `limit=0` means every row. Progress is saved every 5 000 rows, so a run that is interrupted
    (or a machine that restarts) continues from the row it stopped at; `restart=True` starts over.
    """
    try:
        full, kind = _check_path(path)
        size = os.path.getsize(full) if os.path.exists(full) else 0
        key = _state_key(full, table, where)
        skip, resumed_from = 0, 0
        if resume and not restart:
            state = import_state(full, table, where)
            if state and not int(state.get("finished") or 0) and int(state.get("offset_rows") or 0):
                skip = int(state["offset_rows"])
                resumed_from = skip
        if report:
            report(0, size, "resuming at row %d" % skip if skip else "reading the file")
        headers, rows = iter_source_rows(full, table=table, where=where, limit=limit,
                                         report=report, size=size, skip=skip)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    total = len(rows) if hasattr(rows, "__len__") else 0

    def checkpoint(examined, current):
        _save_state(key, full, table, where, skip + examined, current)

    try:
        summary = import_rows(os.path.basename(full), headers, rows, source=source or "import",
                              update_existing=update_existing, mark_status=mark_status,
                              limit=limit, only_email=only_email, report=report, total=total,
                              checkpoint=checkpoint, business_only=business_only)
    except Exception:
        # Leave the saved state alone: the last checkpoint is exactly where the next run
        # has to continue. (Writing the starting row back here threw away every checkpoint
        # of the run that just failed.)
        raise
    _save_state(key, full, table, where, skip + int(summary.get("examined") or 0), summary,
                finished=bool(summary.get("ok")))
    summary["path"] = full
    summary["kind"] = kind
    summary["resumed_at"] = resumed_from
    if table:
        summary["table"] = table
    return summary

# ------------------------------------------------------------------ the import itself
def _full_name(data):
    return " ".join(x for x in (data.get("first_name"), data.get("last_name")) if x).strip()


def identity_key(email, data):
    """A stable handle within one file. Email first, then what names the company or person."""
    if email:
        return "email:" + email
    company = (data.get("company") or "").strip().lower()
    domain = (data.get("domain") or "").strip().lower()
    if company and domain:
        return "company:%s|%s" % (company, domain)
    name = _full_name(data).lower()
    if name and company:
        return "person:%s|%s" % (name, company)
    if company:
        return "company:" + company
    if domain:
        return "domain:" + domain
    linkedin = (data.get("linkedin") or "").strip().lower()
    if linkedin:
        return "linkedin:" + linkedin
    if name:
        return "person:" + name
    return ""


def _find_existing(email, data):
    """The row this import would duplicate, if any. An address wins over a company match.

    The company comparisons are written as `company = ? COLLATE NOCASE` (not `lower(company)=`)
    so SQLite can use `idx_leads_company` / `idx_leads_company_blank`. With the function-wrapped
    form every row without a matching email scanned the whole table, which is what made a
    sixty-thousand-row file take minutes.
    """
    if email:
        row = db.one("SELECT * FROM leads WHERE email=?", (email,))
        if row:
            return row
    company = (data.get("company") or "").strip()
    domain = (data.get("domain") or "").strip()
    if company and domain:
        row = db.one("SELECT * FROM leads INDEXED BY idx_leads_company_blank WHERE email='' AND company = ? COLLATE NOCASE "
                     "AND lower(coalesce(domain,''))=lower(?) LIMIT 1", (company, domain))
        if row:
            return row
    name = _full_name(data)
    if name and company:
        return db.one(
            "SELECT * FROM leads INDEXED BY idx_leads_company_blank WHERE email='' AND company = ? COLLATE NOCASE "
            "AND lower(trim(coalesce(first_name,'')||' '||coalesce(last_name,'')))=lower(?) LIMIT 1",
            (company, name))
    if company:
        return db.one("SELECT * FROM leads INDEXED BY idx_leads_company_blank WHERE email='' AND company = ? COLLATE NOCASE "
                      "AND lower(coalesce(domain,''))='' LIMIT 1", (company,))
    return None


def import_rows(name, headers, rows, source="import", update_existing=False,
                mark_status="new", limit=0, only_email=False, mapping=None, report=None,
                total=0, checkpoint=None, business_only=False):
    """The import loop. `rows` is any iterable of dicts keyed by header name.

    `business_only` applies the Leads tab's rule while the file is read: a private mailbox
    with no website and no own domain behind it, and a row that has nothing but a name, are
    counted in `not_business` and never stored.
    """
    headers = list(headers or [])
    if not headers:
        return {"ok": False, "error": "That file has no header row.", "headers": []}
    mapping = mapping or map_headers(headers)
    if not any(field in mapping for field in IDENTITY_FIELDS):
        return {"ok": False, "headers": headers,
                "error": "Couldn't find an email, company, domain or name column. Rename one "
                         "column to 'email' or 'company' (or 'name') and try again."}

    summary = {"ok": True, "file": name, "headers": headers,
               "mapped": {k: headers[v] for k, v in mapping.items()},
               "imported": 0, "updated": 0, "duplicates": 0, "invalid": 0,
               "existing_email": 0, "skipped_blank": 0, "no_email": 0,
               "not_business": 0,
               "examined": 0, "stopped_at_limit": False,
               "duplicate_check_skipped": False}
    ts = db.now()
    base = {column: "" for column in db.LEAD_FIELD_NAMES}
    suppressed = {r["email"] for r in db.rows("SELECT email FROM suppressions")}
    # A 62M-row file cannot keep every identity key in memory (~80 bytes each would be gigabytes).
    # Above two million rows the in-file duplicate check is left to the unique email index instead.
    big = int(total or 0) > 2000000 or int(limit or 0) > 2000000
    seen = None if big else set()
    summary["duplicate_check_skipped"] = bool(big)
    pending = []
    pending_emails = set()

    def flush():
        if not pending:
            return
        try:
            db.insert_many("leads", pending)
        except sqlite3.IntegrityError:
            # A repeated address that slipped into the same batch: store what fits and count the
            # rest, instead of killing an import that has been running for hours.
            for record in pending:
                try:
                    db.insert("leads", record)
                except sqlite3.IntegrityError:
                    summary["duplicates"] += 1
                    summary["imported"] -= 1
        del pending[:]
        pending_emails.clear()

    for row in rows:
        if limit and summary["examined"] >= limit:
            summary["stopped_at_limit"] = True
            break
        summary["examined"] += 1
        if report and summary["examined"] % 500 == 0:
            report(summary["examined"], total, "importing",
                   summary["imported"] + summary["updated"])
        if checkpoint and summary["examined"] % 5000 == 0:
            # Store what is pending first. The checkpoint says "everything before this row
            # is done"; with rows still waiting in memory a crash would skip them on resume.
            # The current row is not processed yet, hence the minus one.
            flush()
            checkpoint(summary["examined"] - 1, summary)

        email = clean_email(row.get(headers[mapping["email"]])) if "email" in mapping else ""
        if email and not mail.valid_email(email):
            summary["invalid"] += 1
            continue
        if only_email and not email:
            summary["no_email"] += 1
            continue

        data = {}
        for field, idx in mapping.items():
            if field == "email":
                continue
            value = _cell(row.get(headers[idx]))
            if value:
                data[field] = value
        if "name" in data:
            fname, lname = split_name(data.pop("name"))
            data.setdefault("first_name", fname)
            if lname:
                data.setdefault("last_name", lname)
        if data.get("domain"):
            data["domain"] = clean_domain(data["domain"]) or data["domain"]
        elif data.get("website"):
            data["domain"] = clean_domain(data["website"])      # a site column names the domain
        if data.get("company_size"):
            data["company_size"] = size_bucket(data["company_size"])
        if data.get("country"):
            data["country"] = net.normalise_country(data["country"])
        if business_only:
            # Tested before the domain is filled from the address: gmail.com in the domain
            # column would otherwise read as the lead's own site.
            probe = dict(data)
            probe["email"] = email
            if lead_clean.junk_reason(probe):
                summary["not_business"] += 1
                continue
        if not data.get("domain") and email:
            data["domain"] = email.rsplit("@", 1)[-1]

        key = identity_key(email, data)
        if not key:
            summary["skipped_blank"] += 1
            continue
        if seen is not None and key in seen:
            summary["duplicates"] += 1
            continue
        if seen is not None:
            seen.add(key)
            if len(seen) > MAX_SEEN_KEYS:
                # A streamed DuckDB/Parquet source does not say how long it is, so the "big
                # file" switch above cannot see it coming. Past this point the set would
                # eat gigabytes; the unique email index catches repeats from here on.
                seen = None
                summary["duplicate_check_skipped"] = True

        existing = _find_existing(email, data)
        if existing:
            if update_existing:
                patch = {k: v for k, v in data.items() if v and not (existing.get(k) or "")}
                if email and not existing.get("email"):
                    patch["email"] = email          # the address this row was missing
                    patch["verify_status"] = "unverified"
                if patch:
                    patch["updated_ts"] = ts
                    db.update("leads", existing["id"], patch)
                    summary["updated"] += 1
                    continue
            summary["existing_email"] += 1
            continue

        if email and email in pending_emails:
            summary["duplicates"] += 1        # same address twice inside one batch
            continue
        record = dict(base)
        record.update(data)
        record.update({
            "email": email,
            "created_ts": ts,
            "updated_ts": ts,
            "status": mark_status or "new",
            "verify_status": "unverified" if email else "none",
        })
        if email and email in suppressed:
            record["status"] = "unsubscribed"
        pending.append(record)
        if email:
            pending_emails.add(email)
        summary["imported"] += 1
        if not email:
            summary["no_email"] += 1
        if len(pending) >= BATCH:
            flush()
    flush()

    if report:
        report(total or summary["examined"], total or summary["examined"], "finishing",
               summary["imported"] + summary["updated"])
    db.event("import", "%s: +%d new (%d without an email), %d existing, %d invalid" % (
        name, summary["imported"], summary["no_email"], summary["existing_email"],
        summary["invalid"]))
    return summary


def import_data(name, raw, source="import", update_existing=False, mark_status="new",
                only_email=False, report=None, business_only=False):
    """Import an uploaded file. Returns a summary dict.

    `report(done, total, phase, rows)` is called while it works, so the UI can draw a bar with
    an estimate instead of a spinner.
    """
    size = len(raw or b"")
    if report:
        report(0, size, "reading the file")
    headers, raw_rows = read_rows(name, raw)
    if report:
        report(size, size, "parsed", 0)
    if not raw_rows:
        return {"ok": False, "error": "No data rows found in that file.", "headers": headers}
    return import_rows(name, headers, raw_rows, source=source, update_existing=update_existing,
                       mark_status=mark_status, only_email=only_email, report=report,
                       total=len(raw_rows) if hasattr(raw_rows, "__len__") else 0,
                       business_only=business_only)


EXPORT_COLS = ["email", "first_name", "last_name", "company", "title", "domain", "description",
               "phone", "city", "country", "industry", "company_size", "technologies",
               "linkedin", "website", "status", "verify_status", "tags", "source", "notes",
               "created_ts", "last_contact_ts", "replied_ts"]


EXPORT_INT_COLS = ("created_ts", "last_contact_ts", "replied_ts")
EXPORT_BATCH = 5000


def _export_query(status="", search=""):
    sql = "SELECT * FROM leads"
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if search:
        where.append("(email LIKE ? OR company LIKE ? OR first_name LIKE ? OR last_name LIKE ? "
                     "OR description LIKE ?)")
        like = "%%%s%%" % search
        args.extend([like] * 5)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + " ORDER BY id", args


def iter_lead_batches(status="", search="", batch=EXPORT_BATCH):
    """Lists of export rows, a few thousand at a time.

    The exports used to load the whole table first; with millions of leads that is gigabytes
    of memory for one download. A connection of its own keeps the cursor independent of
    whatever else this thread reads while the file is being written.
    """
    sql, args = _export_query(status, search)
    connection = sqlite3.connect(db.config.DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(sql, args)
        while True:
            chunk = cursor.fetchmany(batch)
            if not chunk:
                return
            yield [{k: (row[k] if k in row.keys() else "") for k in EXPORT_COLS} for row in chunk]
    finally:
        connection.close()


def lead_records(status="", search=""):
    return [record for chunk in iter_lead_batches(status, search) for record in chunk]


def _has_leads(status="", search=""):
    sql, args = _export_query(status, search)
    return bool(db.one(sql.replace("SELECT *", "SELECT id", 1).replace(" ORDER BY id", "") +
                       " LIMIT 1", args))


def iter_export_csv(status="", search="", counter=None):
    """The CSV as text chunks, so the server can send it while it is being read.

    `counter`, when given, is a one-item list that ends up holding the number of rows.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLS, extrasaction="ignore")
    # The BOM tells Excel this is UTF-8; without it Windows Excel opens the file as ANSI
    # and every accented name turns into mojibake.
    buf.write("﻿")
    writer.writeheader()
    for chunk in iter_lead_batches(status, search):
        for record in chunk:
            writer.writerow({k: _csv_cell(record.get(k, "")) for k in EXPORT_COLS})
        if counter is not None:
            counter[0] += len(chunk)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
    if buf.tell():
        yield buf.getvalue()


def export_csv(status="", search=""):
    """The whole CSV as one string - for small tables and the tests; downloads are streamed."""
    counter = [0]
    text = "".join(iter_export_csv(status, search, counter=counter))
    return text, counter[0]


def _export_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _duckdb_fill(con, status, search):
    """Fill a `leads` table (every column text) in a DuckDB connection, in bulk.

    Not `executemany`: DuckDB runs that one row at a time, which is minutes for a few hundred
    thousand leads. With pyarrow each batch goes in as one Arrow table; without it the rows
    are written to a temporary CSV that DuckDB loads in one statement.
    """
    columns = ", ".join('"%s" VARCHAR' % c for c in EXPORT_COLS)
    try:
        import pyarrow as pa  # type: ignore
    except ImportError:
        pa = None
    if pa is not None:
        con.execute("CREATE TABLE leads (%s)" % columns)
        schema = pa.schema([(c, pa.string()) for c in EXPORT_COLS])
        for chunk in iter_lead_batches(status, search):
            batch = pa.Table.from_pydict(                                 # noqa: F841 - used by name below
                {c: [str(r.get(c) or "") for r in chunk] for c in EXPORT_COLS}, schema=schema)
            con.register("lead_batch", batch)
            con.execute("INSERT INTO leads SELECT * FROM lead_batch")
            con.unregister("lead_batch")
        return
    path = _temp_path(".csv")
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(EXPORT_COLS)
            for chunk in iter_lead_batches(status, search):
                writer.writerows([[str(r.get(c) or "") for c in EXPORT_COLS] for r in chunk])
        con.execute("CREATE TABLE leads AS SELECT %s FROM read_csv('%s', header=true, "
                    "all_varchar=true, delim=',', quote='\"', escape='\"')" % (
                        ", ".join("COALESCE(\"%s\", '') AS \"%s\"" % (c, c) for c in EXPORT_COLS),
                        path.replace("\\", "/").replace("'", "''")))
    finally:
        _remove_quietly(path)


def export_parquet_file(status="", search=""):
    """Write the Parquet export to a temporary file and return its path (the caller removes it).

    pyarrow when it is installed, otherwise duckdb.
    """
    if not _has_leads(status, search):
        raise ValueError("There are no leads to export yet.")
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        pa = None
    path = _temp_path(".parquet")
    try:
        if pa is not None:
            # A fixed schema: with one batch at a time, a column that happens to be empty in
            # one batch must not get a different type from the next.
            schema = pa.schema([(c, pa.int64() if c in EXPORT_INT_COLS else pa.string())
                                for c in EXPORT_COLS])
            writer = pq.ParquetWriter(path, schema, compression="snappy")
            try:
                for chunk in iter_lead_batches(status, search):
                    columns = {c: [(_export_int(r.get(c)) if c in EXPORT_INT_COLS
                                    else ("" if r.get(c) is None else str(r.get(c))))
                                   for r in chunk] for c in EXPORT_COLS}
                    writer.write_table(pa.Table.from_pydict(columns, schema=schema))
            finally:
                writer.close()
            return path
        try:
            import duckdb  # type: ignore
        except ImportError:
            raise ValueError("Parquet export needs pyarrow or duckdb: pip install pyarrow")
        con = duckdb.connect()
        try:
            _duckdb_fill(con, status, search)
            con.execute("COPY leads TO '%s' (FORMAT PARQUET)" % path.replace("\\", "/"))
        finally:
            con.close()
        return path
    except Exception:
        _remove_quietly(path)
        raise


def export_duckdb_file(status="", search=""):
    """A real .duckdb file with one `leads` table, as a temporary path (the caller removes it)."""
    try:
        import duckdb  # type: ignore
    except ImportError:
        raise ValueError("DuckDB export needs the duckdb package: pip install duckdb")
    if not _has_leads(status, search):
        raise ValueError("There are no leads to export yet.")
    path = _temp_path(".duckdb")
    try:
        con = duckdb.connect(path)
        try:
            _duckdb_fill(con, status, search)
            con.execute("CHECKPOINT")
        finally:
            con.close()
        return path
    except Exception:
        _remove_quietly(path)
        raise


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _read_and_remove(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        _remove_quietly(path)


def export_parquet(status="", search=""):
    """The Parquet export as bytes (small tables, tests). Downloads use export_parquet_file."""
    return _read_and_remove(export_parquet_file(status, search))


def export_duckdb(status="", search=""):
    """The DuckDB export as bytes (small tables, tests). Downloads use export_duckdb_file."""
    return _read_and_remove(export_duckdb_file(status, search))


# Excel and Google Sheets evaluate a cell that starts with one of these characters, so a
# company or note that came from a scraped page could run a formula on the user's machine.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    text = "" if value is None else str(value)
    if text[:1] in _FORMULA_START:
        return "'" + text
    return text
