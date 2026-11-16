"""Every row, resume where it stopped, and the JSON/CSV readers behind the upload."""
import json
import os
import shutil
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_resume_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, importer  # noqa: E402


class ResumeTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        importer._ensure_state_table()
        db.execute("DELETE FROM leads WHERE email LIKE '%resume-test.example'")
        db.execute("DELETE FROM import_state")
        self.tmp = tempfile.mkdtemp(prefix="ls_resume_files_")
        self.duck = os.path.join(self.tmp, "contacts.duckdb")
        import duckdb
        connection = duckdb.connect(self.duck)
        connection.execute("CREATE TABLE contacts (email VARCHAR, company VARCHAR, title VARCHAR)")
        for index in range(10):
            connection.execute("INSERT INTO contacts VALUES (?, ?, ?)",
                               ("user%d@resume-test.example" % index, "Company %d" % index, "Owner"))
        connection.close()

    def tearDown(self):
        db.execute("DELETE FROM leads WHERE email LIKE '%resume-test.example'")
        db.execute("DELETE FROM import_state")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_limit_means_every_row(self):
        answer = importer.import_path(self.duck, table="contacts", limit=0)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["imported"], 10)
        self.assertEqual(answer["resumed_at"], 0)

    def test_an_interrupted_run_continues_from_its_offset(self):
        full = os.path.abspath(self.duck)
        key = importer._state_key(full, "contacts", "")
        importer._save_state(key, full, "contacts", "", 5,
                             {"examined": 5, "imported": 5}, finished=False)
        answer = importer.import_path(self.duck, table="contacts", limit=0)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["resumed_at"], 5)
        self.assertEqual(answer["examined"], 5)          # only the second half was read
        self.assertEqual(answer["imported"], 5)
        stored = db.scalar("SELECT COUNT(*) FROM leads WHERE email LIKE '%resume-test.example'")
        self.assertEqual(stored, 5)

    def test_a_finished_run_is_not_resumed(self):
        importer.import_path(self.duck, table="contacts", limit=0)
        second = importer.import_path(self.duck, table="contacts", limit=0)
        self.assertEqual(second["resumed_at"], 0)       # finished state: start over
        self.assertEqual(second["imported"], 0)         # and the addresses already exist
        self.assertEqual(second["existing_email"], 10)

    def test_restart_ignores_the_saved_offset(self):
        full = os.path.abspath(self.duck)
        key = importer._state_key(full, "contacts", "")
        importer._save_state(key, full, "contacts", "", 7,
                             {"examined": 7, "imported": 7}, finished=False)
        answer = importer.import_path(self.duck, table="contacts", limit=0, restart=True)
        self.assertEqual(answer["resumed_at"], 0)
        self.assertEqual(answer["examined"], 10)


class ReaderTests(unittest.TestCase):
    def test_json_rows_are_read(self):
        payload = json.dumps([{"email": "a@x.sk", "company": "A"},
                              {"email": "b@x.sk", "company": "B"}]).encode()
        headers, rows = importer.read_rows("contacts.json", payload)
        self.assertEqual(headers, ["email", "company"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["email"], "b@x.sk")

    def test_json_wrapped_in_an_object_is_read(self):
        payload = json.dumps({"rows": [{"email": "c@x.sk"}]}).encode()
        headers, rows = importer.read_rows("contacts.json", payload)
        self.assertEqual(headers, ["email"])
        self.assertEqual(rows[0]["email"], "c@x.sk")

    def test_a_json_path_is_recognised(self):
        self.assertEqual(importer.path_kind("contacts.json"), "json")
        self.assertEqual(importer.path_kind("x.parquet"), "parquet")


if __name__ == "__main__":
    unittest.main(verbosity=2)
