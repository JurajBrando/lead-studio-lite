"""The provider pool: several keys on one endpoint, one of them out of quota."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import db, llm, net  # noqa: E402

BASE = "https://pool-test.local/v1"


class RetryTests(unittest.TestCase):
    def test_quota_auth_and_server_faults_are_worth_another_key(self):
        for status in (0, 401, 402, 403, 429, 500, 503):
            self.assertTrue(llm._retryable(net.NetError("x", status=status)), status)

    def test_a_bad_request_is_not(self):
        self.assertFalse(llm._retryable(net.NetError("x", status=400)))
        self.assertFalse(llm._retryable(llm.LLMError("x")))


class PoolTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.execute("DELETE FROM llm_providers WHERE base_url=?", (BASE,))
        self.ids = []
        for index in (1, 2, 3):
            self.ids.append(db.insert("llm_providers", {
                "name": "pool-test %d" % index, "kind": "openai", "base_url": BASE,
                "model": "m", "api_key": "k%d" % index, "is_default": 0,
                "created_ts": db.now(), "last_test_ts": 0, "last_test_ok": 0}))

    def tearDown(self):
        db.execute("DELETE FROM llm_providers WHERE base_url=?", (BASE,))

    def test_one_dead_key_does_not_fail_the_call(self):
        seen = []

        def fake(provider, messages, max_tokens, temperature, system):
            seen.append(provider["name"])
            if len(seen) == 1:
                raise net.NetError("no credits", status=402)
            return "answer from " + provider["name"]

        with mock.patch.object(llm, "_chat_once", side_effect=fake):
            text = llm.chat([{"role": "user", "content": "hi"}], provider_id=self.ids[0])
        self.assertTrue(text.startswith("answer from pool-test"), text)
        self.assertEqual(len(seen), 2)
        failed = db.one("SELECT last_test_ok FROM llm_providers WHERE id=?", (self.ids[0],))
        self.assertEqual(failed["last_test_ok"], 0)
        # whichever key answered is marked healthy (the cursor decides which one it is)
        succeeded = db.one("SELECT last_test_ok FROM llm_providers WHERE name=?", (seen[1],))
        self.assertEqual(succeeded["last_test_ok"], 1)

    def test_a_default_call_rotates_through_the_pool(self):
        starts = []

        def fake(provider, *args, **kwargs):
            starts.append(provider["name"])
            return "ok"

        with mock.patch.object(llm, "_chat_once", side_effect=fake):
            for _ in range(3):
                llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(sorted(starts), ["pool-test 1", "pool-test 2", "pool-test 3"])

    def test_a_pool_of_dead_keys_says_what_is_wrong(self):
        with mock.patch.object(llm, "_chat_once",
                               side_effect=net.NetError("no credits", status=402)):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.chat([{"role": "user", "content": "hi"}], provider_id=self.ids[0],
                         max_tries=2)
        self.assertIn("credits", str(ctx.exception))

    def test_a_bad_request_is_not_tried_on_the_next_key(self):
        calls = []

        def fake(*args, **kwargs):
            calls.append(1)
            raise net.NetError("bad request", status=400)

        with mock.patch.object(llm, "_chat_once", side_effect=fake):
            with self.assertRaises(net.NetError):
                llm.chat([{"role": "user", "content": "hi"}], provider_id=self.ids[0])
        self.assertEqual(len(calls), 1)

    def test_the_single_test_never_borrows_another_key(self):
        with mock.patch.object(llm, "_chat_once",
                               side_effect=net.NetError("no credits", status=402)) as fake:
            result = llm.test(self.ids[0])
        self.assertFalse(result["ok"])
        self.assertEqual(fake.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
