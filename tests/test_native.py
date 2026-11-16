"""The native OSM search and the native hot list."""
import json
import os
import sys
import tempfile
import unittest

if not os.environ.get("LS_DATA"):
    os.environ["LS_DATA"] = tempfile.mkdtemp(prefix="ls_native_")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, hotlist, osm  # noqa: E402


class OfferParsingTests(unittest.TestCase):
    def test_a_trade_known_to_the_map(self):
        offer, place = osm.parse_offer("roofers in Slovakia")
        self.assertEqual(place, "slovakia")
        self.assertIn("craft=roofer", offer["tags"])

    def test_comma_form(self):
        offer, place = osm.parse_offer("dentists, Bratislava")
        self.assertEqual(place, "bratislava")
        self.assertIn("healthcare=dentist", offer["tags"])

    def test_anything_else_falls_through_to_a_name_search(self):
        offer, _ = osm.parse_offer("quantum widget maker")
        self.assertTrue(any(tag.startswith("name~") for tag in offer["tags"]), offer)

    def test_an_empty_offer_is_refused(self):
        with self.assertRaises(ValueError):
            osm.parse_offer("   ")

    def test_domains_from_a_website(self):
        self.assertEqual(osm._domain_of("https://www.foo.sk/x"), "foo.sk")
        self.assertEqual(osm._domain_of("bar.com"), "bar.com")
        self.assertEqual(osm._domain_of(""), "")


class StoringTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        osm.ensure_table()
        db.execute("DROP TABLE IF EXISTS osm_business")
        osm.ensure_table()

    def element(self, name, **tags):
        payload = {"name": name}
        payload.update(tags)
        return {"type": "node", "id": abs(hash(name)) % 1000000, "tags": payload}

    def test_rows_are_stored_and_split_into_the_three_lists(self):
        elements = [
            self.element("With Email Ltd", email="hi@withe.email", website="https://withe.email"),
            self.element("Website Only Ltd", website="https://webonly.sk"),
            self.element("Phone Only Ltd", phone="+421 900 000 000"),
            {"type": "node", "id": 1, "tags": {}},                  # no name: ignored
        ]
        osm.save(elements, "craft=roofer", "Slovakia")
        counts = osm.lists(category="craft=roofer", area="Slovakia")
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["with_email"], 1)
        self.assertEqual(counts["website_only"], 1)
        self.assertEqual(counts["phone_only"], 1)

    def test_saving_twice_updates_instead_of_duplicating(self):
        element = self.element("Twice Ltd", email="one@twice.sk")
        osm.save([element], "shop=bakery", "Bratislava")
        element["tags"]["email"] = "two@twice.sk"
        osm.save([element], "shop=bakery", "Bratislava")
        rows = db.rows("SELECT * FROM osm_business WHERE name='Twice Ltd'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "two@twice.sk")

    def test_import_turns_the_rows_into_leads(self):
        db.execute("DELETE FROM leads WHERE source='osm'")
        osm.save([self.element("Import Me Ltd", email="contact@importme.sk",
                               website="https://importme.sk")], "shop=florist", "Bratislava")
        answer = osm.import_ready(category="shop=florist", area="Bratislava", include_website=False)
        self.assertTrue(answer["ok"], answer)
        self.assertGreaterEqual(answer["imported"], 1)
        lead = db.one("SELECT * FROM leads WHERE email='contact@importme.sk'")
        self.assertIsNotNone(lead)
        self.assertEqual(lead["domain"], "importme.sk")
        self.assertEqual(lead["source"], "osm")
        db.execute("DELETE FROM leads WHERE email='contact@importme.sk'")


class HotListTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM signals WHERE source='test-hot'")
        db.execute("DELETE FROM leads WHERE email LIKE '%hotlist-test.example'")
        self.lead_id = db.insert("leads", {
            "email": "dana@hotlist-test.example", "first_name": "Dana",
            "company": "Hotlist Test Ltd", "domain": "hotlist-test.example",
            "title": "Owner", "source": "crawl", "status": "new"})
        for kind, domain, title, payload in (
                ("dropped_attribution_tool", "hotlist-test.example", "Triple Whale is no longer on the site",
                 {"tool": "Triple Whale"}),
                ("new_lookalike_domain", "hotlist-test.example", "1 new lookalike domain(s) registered",
                 {"lookalike": "hotlist-test.shop"}),
                ("mx_missing", "hotlist-test.example", "No mail server appeared", {})):
            db.insert("signals", {"ts": db.now(), "kind": kind, "domain": domain,
                                  "company": "Hotlist Test Ltd", "title": title,
                                  "source": "test-hot", "payload": json.dumps(payload),
                                  "lead_id": self.lead_id, "seen": 0})

    def tearDown(self):
        db.execute("DELETE FROM signals WHERE source='test-hot'")
        db.execute("DELETE FROM leads WHERE email LIKE '%hotlist-test.example'")

    def test_a_dropped_tool_is_hot_and_gets_a_question(self):
        answer = hotlist.build(days=2, limit=50)
        rows = [row for row in answer["rows"] if row["email"].endswith("hotlist-test.example")]
        self.assertTrue(rows, answer)
        hot = [row for row in rows if row["heat"] == "HOT"]
        self.assertTrue(hot)
        self.assertIn("Triple Whale", hot[0]["reason"])
        self.assertIn("Hi Dana", hot[0]["first email question"])
        self.assertEqual(hot[0]["email confidence"], "your_own_data")

    def test_the_noise_kinds_are_left_out(self):
        answer = hotlist.build(days=2, limit=50)
        kinds = {row["signal"] for row in answer["rows"]}
        self.assertNotIn("mx_missing", kinds)

    def test_the_csv_has_the_columns_the_list_needs(self):
        answer = hotlist.build(days=2, limit=50)
        self.assertTrue(os.path.exists(answer["path"]))
        with open(answer["path"], "r", encoding="utf-8") as handle:
            header = handle.readline().strip()
        self.assertEqual(header.split(","), hotlist.COLUMNS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
