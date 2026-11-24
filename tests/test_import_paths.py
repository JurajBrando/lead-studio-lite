"""Contacts without an email, imports from files on disk, and the schema change behind them.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, importer, picker  # noqa: E402

HAVE_PYARROW = importlib.util.find_spec("pyarrow") is not None
HAVE_DUCKDB = importlib.util.find_spec("duckdb") is not None

# The table as it shipped up to 1.9.0: the address was mandatory and unique.
OLD_SCHEMA = """
CREATE TABLE leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    first_name     TEXT DEFAULT '',
    last_name      TEXT DEFAULT '',
    company        TEXT DEFAULT '',
    title          TEXT DEFAULT '',
    domain         TEXT DEFAULT '',
    phone          TEXT DEFAULT '',
    city           TEXT DEFAULT '',
    country        TEXT DEFAULT '',
    industry       TEXT DEFAULT '',
    linkedin       TEXT DEFAULT '',
    website        TEXT DEFAULT '',
    notes          TEXT DEFAULT '',
    source         TEXT DEFAULT '',
    tags           TEXT DEFAULT '',
    status         TEXT DEFAULT 'new',
    verify_status  TEXT DEFAULT 'unverified',
    verify_detail  TEXT DEFAULT '',
    created_ts     INTEGER DEFAULT 0,
    updated_ts     INTEGER DEFAULT 0,
    last_contact_ts INTEGER DEFAULT 0,
    replied_ts     INTEGER DEFAULT 0
);
CREATE TABLE sequences (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sequence_id INTEGER NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'running'
);
CREATE TABLE sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    to_email TEXT DEFAULT ''
);
"""


def wipe_leads():
    db.execute("DELETE FROM enrollments")
    db.execute("DELETE FROM sends")
    db.execute("DELETE FROM replies")
    db.execute("DELETE FROM leads")


class SchemaMigrationTests(unittest.TestCase):
    def test_the_old_leads_table_is_rebuilt_without_losing_children(self):
        folder = tempfile.mkdtemp(prefix="ls_mig_")
        path = os.path.join(folder, "old.db")
        con = sqlite3.connect(path)
        con.executescript(OLD_SCHEMA)
        con.execute("INSERT INTO leads (email, first_name, company) VALUES ('a@b-test.com','Ann','Acme')")
        lead_id = con.execute("SELECT id FROM leads").fetchone()[0]
        con.execute("INSERT INTO sequences (id, name) VALUES (1, 'cadence')")
        con.execute("INSERT INTO enrollments (sequence_id, lead_id, status) VALUES (1, ?, 'running')",
                    (lead_id,))
        con.execute("INSERT INTO sends (lead_id, to_email) VALUES (?, 'a@b-test.com')", (lead_id,))
        con.commit()

        db._rebuild_leads(con, path)

        columns = {row[1]: row for row in con.execute("PRAGMA table_info(leads)").fetchall()}
        self.assertEqual(columns["email"][3], 0, "email must no longer be NOT NULL")
        self.assertIn("description", columns)
        self.assertEqual(con.execute("SELECT count(*) FROM leads").fetchone()[0], 1)
        # Foreign keys were off for the swap, so the children are still there.
        self.assertEqual(con.execute("SELECT count(*) FROM enrollments").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT count(*) FROM sends").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT id FROM enrollments").fetchone()[0], 1)

        # Several contacts may now have no address; a real address stays unique.
        con.execute("INSERT INTO leads (email, company) VALUES ('', 'No Email Co')")
        con.execute("INSERT INTO leads (email, company) VALUES ('', 'Another Co')")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO leads (email, company) VALUES ('a@b-test.com', 'Dup')")
        con.close()

        self.assertTrue(any(name.startswith("old.db.bak-") for name in os.listdir(folder)),
                        "a backup of the database must be left behind")


class EmailOptionalTests(unittest.TestCase):
    def setUp(self):
        wipe_leads()

    def test_a_company_with_a_description_is_a_contact(self):
        res = importer.import_data("companies.csv",
                                   b"Company,About\nAcme GmbH,We build thingies\n")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(res["no_email"], 1)
        row = db.one("SELECT * FROM leads WHERE company='Acme GmbH'")
        self.assertEqual(row["email"], "")
        self.assertEqual(row["verify_status"], "none")

    def test_only_email_narrows_the_import(self):
        text = (b"company,email,description\n"
                b"With,one@with-test.com,has an address\n"
                b"Without,,no address here\n")
        res = importer.import_data("x.csv", text, only_email=True)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(res["no_email"], 1)
        self.assertEqual(db.scalar("SELECT count(*) FROM leads"), 1)

    def test_an_emails_column_takes_the_first_address(self):
        res = importer.import_data(
            "x.csv", b'company,emails\nAcme,"first@acme-test.com, second@acme-test.com"\n')
        self.assertEqual(res["imported"], 1)
        self.assertEqual(db.one("SELECT email FROM leads")["email"], "first@acme-test.com")

    def test_a_url_becomes_a_bare_domain(self):
        importer.import_data("x.csv", b"company,website\nAcme,https://www.Acme-Test.com/about?x=1\n")
        self.assertEqual(db.one("SELECT domain FROM leads")["domain"], "acme-test.com")


class PathImportTests(unittest.TestCase):
    def setUp(self):
        wipe_leads()
        self.folder = tempfile.mkdtemp(prefix="ls_path_")

    def test_a_missing_file_is_a_clear_error(self):
        res = importer.import_path(os.path.join(self.folder, "nope.csv"))
        self.assertFalse(res["ok"])
        self.assertIn("does not exist", res["error"])

    def test_inspect_reports_the_csv_headers(self):
        path = os.path.join(self.folder, "rows.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("company,description\nAcme,We build\n")
        info = importer.inspect_source(path)
        self.assertTrue(info["ok"], info)
        self.assertEqual(info["kind"], "csv")
        self.assertEqual(info["columns"], ["company", "description"])

    def test_a_csv_over_200_mb_streams_instead_of_being_refused(self):
        path = os.path.join(self.folder, "huge.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("email,company\n"
                     "one@path-test.com,One\n"
                     "two@path-test.com,Two\n")
        # The file proves the path, the patched size stands in for the 18 GB one on disk.
        with mock.patch.object(importer.os.path, "getsize", return_value=18 * 1024 ** 3):
            res = importer.import_path(path)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 2)
        self.assertEqual(db.scalar("SELECT count(*) FROM leads"), 2)

    def test_a_csv_is_read_row_by_row_not_into_a_list(self):
        path = os.path.join(self.folder, "stream.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("email;company\n"
                     'a@stream-test.com;"One, Inc"\n'
                     "b@stream-test.com;Two\n"
                     "c@stream-test.com;Three\n")
        headers, rows = importer.iter_source_rows(path, limit=2, skip=0)
        self.assertEqual(headers, ["email", "company"])
        self.assertFalse(hasattr(rows, "__len__"), "a CSV must stream, not load a list")
        self.assertEqual([r["email"] for r in rows],
                         ["a@stream-test.com", "b@stream-test.com"])

        headers, rows = importer.iter_source_rows(path, skip=1)
        self.assertEqual([r["company"] for r in rows], ["Two", "Three"])

    def test_a_cp1250_csv_with_a_multiline_field_streams(self):
        path = os.path.join(self.folder, "names.csv")
        with open(path, "w", encoding="cp1250", newline="") as fh:
            fh.write("email;company;notes\n"
                     'z@stream-test.com;\u017dlavy;"line one\nline two, with a comma"\n')
        res = importer.import_path(path)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 1)
        row = db.one("SELECT * FROM leads WHERE email=?", ("z@stream-test.com",))
        self.assertEqual(row["company"], "\u017dlavy")
        self.assertIn("line two", row["notes"])

    def test_a_filter_cannot_carry_a_second_statement(self):
        with self.assertRaises(ValueError):
            importer._safe_where("1=1; DROP TABLE leads")

    @unittest.skipUnless(HAVE_PYARROW, "pyarrow is not installed")
    def test_import_a_parquet_file_with_a_row_cap(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        path = os.path.join(self.folder, "leads.parquet")
        pq.write_table(pa.table({
            "email": ["p1@parquet-test.com", "p2@parquet-test.com", "", ""],
            "company": ["One", "Two", "Three", "Four"],
            "description": ["d1", "d2", "d3", "d4"],
        }), path)
        info = importer.inspect_source(path)
        self.assertTrue(info["ok"], info)
        self.assertEqual(info["rows"], 4)
        self.assertIn("company", info["columns"])

        res = importer.import_path(path, limit=3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["examined"], 3)
        self.assertEqual(res["imported"], 3)
        self.assertEqual(res["no_email"], 1)
        self.assertEqual(db.scalar("SELECT count(*) FROM leads"), 3)

    @unittest.skipUnless(HAVE_DUCKDB, "duckdb is not installed")
    def test_import_a_duckdb_table_with_a_filter(self):
        import duckdb
        path = os.path.join(self.folder, "contacts.duckdb")
        con = duckdb.connect(path)
        con.execute("CREATE TABLE contacts (email VARCHAR, company VARCHAR, description VARCHAR)")
        con.executemany("INSERT INTO contacts VALUES (?, ?, ?)", [
            ("d1@duck-test.com", "One", ""),
            ("", "Two", "a description"),
            ("", "Three", "another description"),
        ])
        con.execute("CREATE TABLE other (x INTEGER)")
        con.close()

        info = importer.inspect_source(path)
        self.assertTrue(info["ok"], info)
        self.assertEqual(sorted(t["name"] for t in info["tables"]), ["contacts", "other"])
        self.assertEqual(info["table"], "contacts")
        self.assertIn("description", info["columns"])

        info = importer.inspect_source(path, "other")
        self.assertEqual(info["columns"], ["x"])

        res = importer.import_path(path, table="contacts", where="email <> ''")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["imported"], 1)
        self.assertEqual(db.scalar("SELECT count(*) FROM leads WHERE email<>''"), 1)

        res = importer.import_path(path, table="contacts", where="company = 'Two'")
        self.assertEqual(res["imported"], 1)
        row = db.one("SELECT * FROM leads WHERE company='Two'")
        self.assertEqual(row["description"], "a description")
        self.assertEqual(row["verify_status"], "none")

    @unittest.skipUnless(HAVE_PYARROW, "pyarrow is not installed")
    def test_a_parquet_file_cannot_take_a_filter(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        path = os.path.join(self.folder, "plain.parquet")
        pq.write_table(pa.table({"company": ["One"]}), path)
        res = importer.import_path(path, where="company = 'One'")
        self.assertFalse(res["ok"])
        self.assertIn("DuckDB", res["error"])


class PickerTests(unittest.TestCase):
    """The dialog itself is a child process; here it is faked by writing its answer file."""

    def fake(self, answer, returncode=0, stderr=""):
        def run(cmd, **kwargs):
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w", encoding="utf-8") as handle:
                handle.write(answer)
            return subprocess.CompletedProcess(cmd, returncode, "", stderr)
        return run

    def test_the_child_writes_the_chosen_path_to_a_file(self):
        with mock.patch.object(picker.subprocess, "run",
                               side_effect=self.fake(r"C:\data\leads.parquet")):
            result = picker.ask("Choose a file", ["parquet"])
        self.assertTrue(result["ok"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["path"], r"C:\data\leads.parquet")
        self.assertEqual(result["name"], "leads.parquet")

    def test_cancelling_is_not_an_error(self):
        with mock.patch.object(picker.subprocess, "run", side_effect=self.fake("")):
            result = picker.ask()
        self.assertTrue(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertNotIn("path", result)

    def test_a_failing_child_is_reported(self):
        with mock.patch.object(picker.subprocess, "run",
                               side_effect=self.fake("", returncode=2, stderr="no display")):
            with self.assertRaises(picker.PickerError) as ctx:
                picker.ask()
        self.assertIn("no display", str(ctx.exception))

    def test_a_build_without_tkinter_says_so(self):
        with mock.patch.object(picker, "available", return_value=False):
            with self.assertRaises(picker.PickerError) as ctx:
                picker.ask()
        self.assertIn("tkinter", str(ctx.exception))

    def test_the_command_reuses_this_app(self):
        command = picker._command("answer.txt", "Choose", ("csv", "parquet"))
        self.assertIn("--pick-file", command)
        self.assertIn("answer.txt", command)
        self.assertEqual(command[command.index("--extensions") + 1], "csv,parquet")
        self.assertTrue(any(part.endswith("leadstudio.py") for part in command))


class PickEndpointTests(unittest.TestCase):
    def setUp(self):
        from engine import server
        self.server = server

    def test_the_endpoint_returns_the_chosen_path(self):
        with mock.patch.object(self.server.picker, "ask",
                               return_value={"ok": True, "path": "C:/x/leads.csv"}):
            answer = self.server.r_pick_file({"title": "x"})
        self.assertEqual(answer["path"], "C:/x/leads.csv")

    def test_a_dialog_error_becomes_a_normal_error(self):
        with mock.patch.object(self.server.picker, "ask",
                               side_effect=self.server.picker.PickerError("no dialog")):
            answer = self.server.r_pick_file({})
        self.assertFalse(answer["ok"])
        self.assertIn("no dialog", answer["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
