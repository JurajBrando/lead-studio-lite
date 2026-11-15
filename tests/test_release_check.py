"""The release guard must catch personal files before a download build ships.

Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import os
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "check_release", os.path.join(ROOT, "tools", "check_release.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


class ReleaseCheckTests(unittest.TestCase):
    def make_tree(self):
        tmp = tempfile.mkdtemp(prefix="ls_release_")
        os.makedirs(os.path.join(tmp, "engine"))
        with open(os.path.join(tmp, "engine", "config.py"), "w", encoding="utf-8") as fh:
            fh.write('APP_NAME = "Lead Studio Lite"\nVERSION = "1.2.3"\n')
        with open(os.path.join(tmp, "CHANGELOG.md"), "w", encoding="utf-8") as fh:
            fh.write("## [1.2.3] - today\n\nNothing yet.\n")
        return tmp

    def write(self, tmp, name, text):
        path = os.path.join(tmp, name)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_clean_tree_passes(self):
        tmp = self.make_tree()
        self.write(tmp, "README.md", "# Lead Studio Lite\n\nRun it.\n")
        self.assertEqual(check.check_tree(tmp), [])
        self.assertEqual(check.check_version(tmp), [])

    def test_personal_files_are_caught(self):
        tmp = self.make_tree()
        for name in ("settings.json", "leadstudio.db", "app.log", "PERSONAL_README.md"):
            self.write(tmp, name, "")
        problems = check.check_tree(tmp)
        self.assertEqual(len(problems), 4, problems)

    def test_a_real_user_path_is_caught_and_a_placeholder_is_not(self):
        tmp = self.make_tree()
        self.write(tmp, "NOTES.md",
                   "C:/Users/you/LeadStudioLite is an example\n"
                   "C:\\Users\\admin\\Desktop\\Modulario is not\n")
        problems = check.check_tree(tmp)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("admin", problems[0])

    def test_a_version_mismatch_is_caught(self):
        tmp = self.make_tree()
        self.write(tmp, "CHANGELOG.md", "## [9.9.9] - later\n")
        self.assertTrue(check.check_version(tmp))

    def test_a_zip_carrying_data_is_caught(self):
        tmp = self.make_tree()
        zpath = os.path.join(tmp, "dist", "LeadStudioLite-1.2.3-source.zip")
        os.makedirs(os.path.dirname(zpath))
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("LeadStudioLite/data/settings.json", "{}")
            zf.writestr("LeadStudioLite/docs/NOTES.md", "C:\\Users\\admin\\Desktop")
            zf.writestr("LeadStudioLite/README.md", "# ok")
        problems = check.check_zip(zpath)
        self.assertGreaterEqual(len(problems), 2, problems)
        self.assertEqual(check.check_dist(tmp), problems)

    def test_a_source_zip_may_not_ship_an_executable(self):
        tmp = self.make_tree()
        zpath = os.path.join(tmp, "dist", "LeadStudioLite-1.2.3-source.zip")
        os.makedirs(os.path.dirname(zpath))
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("LeadStudioLite/LeadStudio.exe", b"MZ")
        self.assertTrue(any("executable" in p for p in check.check_zip(zpath)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
