"""Spintax (`{|Hi|Hello|}`) and merge fields in the same pass."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import mail  # noqa: E402


class SpintaxTests(unittest.TestCase):
    def test_one_option_is_always_chosen(self):
        for _ in range(20):
            text, missing = mail.merge("Hi {|there|friend|},", {})
            self.assertIn(text, ("Hi there,", "Hi friend,"))
            self.assertEqual(missing, [])

    def test_merge_fields_still_run_afterwards(self):
        text, _ = mail.merge("{|Hello|Hi|} {{first_name|there}} at {{company}}",
                             {"company": "Acme"})
        self.assertIn("Acme", text)
        self.assertIn(text.split(" ")[0], ("Hello", "Hi"))
        self.assertNotIn("{", text)

    def test_text_without_spintax_is_untouched(self):
        text, _ = mail.merge("Plain text with a {{name}}", {"name": "Juraj"})
        self.assertEqual(text, "Plain text with a Juraj")

    def test_a_lone_pipe_is_left_alone(self):
        # "{|only|}" is not spintax (nothing to choose between), so it stays as written.
        self.assertEqual(mail.spin("{|only|}"), "{|only|}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
