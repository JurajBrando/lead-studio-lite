"""Check that nothing personal is about to be published.

    python tools/check_release.py                  # the publishable tree
    python tools/check_release.py --working-copy   # your own copy: allow PERSONAL_README.md
    python tools/check_release.py --zips           # also every zip in dist/

Exit code 0 means clean. Every problem is printed on its own line, so this is safe to
run from a release workflow or before pushing the public repository.

It looks for the things that only exist because you actually use the app: your data
folder, a real settings.json, a database, logs, a personal readme, and your own
`C:\\Users\\...` paths inside otherwise harmless documents.
"""
import glob
import os
import re
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {"data", "__pycache__", ".git", "dist", "build", ".venv", "venv",
             ".idea", ".vscode", ".pytest_cache", "node_modules"}
FORBIDDEN_NAMES = {"settings.json", "PERSONAL_README.md", "Thumbs.db", ".DS_Store"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".tmp")
PERSONAL_PATH = re.compile(
    r"[A-Za-z]:[\\/]Users[\\/]([A-Za-z0-9._-]+)"
    r"|/home/([A-Za-z0-9._-]+)/"
    r"|/Users/([A-Za-z0-9._-]+)/")
# Documented examples like C:/Users/you/... are fine; a real account name is not.
PLACEHOLDER_USERS = {"you", "user", "name", "me", "username", "your-name", "your-account"}
TEXT_SUFFIXES = (".md", ".py", ".js", ".html", ".css", ".json", ".csv", ".yml", ".yaml",
                 ".bat", ".vbs", ".txt", ".toml", ".spec")
TEXT_NAMES = {".gitignore", ".gitattributes"}


def _is_text(name):
    return name.lower().endswith(TEXT_SUFFIXES) or os.path.basename(name) in TEXT_NAMES


def _scan_paths(where, text, problems):
    """Flag absolute paths that point at a real user profile."""
    for match in PERSONAL_PATH.finditer(text):
        user = next((g for g in match.groups() if g), "")
        if user.lower() in PLACEHOLDER_USERS:
            continue
        line = text.count("\n", 0, match.start()) + 1
        problems.append("%s:%d contains a local user path (%s)" % (where, line, match.group(0)))


def version_of_tree(root=ROOT):
    try:
        with open(os.path.join(root, "engine", "config.py"), encoding="utf-8") as fh:
            match = re.search(r'VERSION\s*=\s*"([^"]+)"', fh.read())
        return match.group(1) if match else ""
    except OSError:
        return ""


def changelog_version(root=ROOT):
    try:
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as fh:
            match = re.search(r"^##\s*\[([0-9][^\]]*)\]", fh.read(), re.MULTILINE)
        return match.group(1).strip() if match else ""
    except OSError:
        return ""


def check_tree(root=ROOT, allow=()):
    problems = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if name in FORBIDDEN_NAMES and name not in allow:
                problems.append("%s is personal data and must not ship" % rel)
                continue
            if name in allow:
                continue
            if name.lower().endswith(FORBIDDEN_SUFFIXES):
                problems.append("%s is personal data and must not ship" % rel)
                continue
            if ".broken-" in name:
                problems.append("%s is a backup of a broken settings file" % rel)
                continue
            if name.lower().endswith(".exe"):
                continue          # the built executable is expected in dist/, checked with --zips
            if not _is_text(name):
                continue
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    _scan_paths(rel, fh.read(), problems)
            except OSError as exc:
                problems.append("%s could not be read (%s)" % (rel, exc))
    return problems


def check_version(root=ROOT):
    problems = []
    version = version_of_tree(root)
    notes = changelog_version(root)
    if not version:
        problems.append("engine/config.py has no VERSION")
    elif notes and notes != version:
        problems.append("CHANGELOG.md describes %s but VERSION is %s" % (notes, version))
    return problems


def check_zip(path):
    problems = []
    label = os.path.basename(path)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return ["%s could not be opened (%s)" % (label, exc)]
    with archive as zf:
        names = zf.namelist()
        for entry in names:
            low = entry.lower()
            leaf = os.path.basename(low)
            if leaf in {n.lower() for n in FORBIDDEN_NAMES}:
                problems.append("%s contains %s" % (label, entry))
                continue
            if low.endswith(FORBIDDEN_SUFFIXES):
                problems.append("%s contains %s" % (label, entry))
                continue
            if low.startswith("data/") or "/data/" in low:
                problems.append("%s contains the data folder (%s)" % (label, entry))
                continue
            if _is_text(leaf):
                try:
                    text = zf.read(entry).decode("utf-8", "replace")
                except (KeyError, OSError):
                    continue
                _scan_paths("%s!%s" % (label, entry), text, problems)
        has_exe = any(n.lower().endswith(".exe") for n in names)
        if "source" in label and has_exe:
            problems.append("%s is a source package but contains an executable" % label)
        if "windows" in label and not has_exe:
            problems.append("%s has no LeadStudio.exe in it" % label)
    return problems


def exe_is_windowed(path):
    """True for a GUI build, False for a console build, None if unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0x3C)
            offset = struct.unpack("<I", fh.read(4))[0]
            fh.seek(offset)
            if fh.read(4) != b"PE\0\0":
                return None
            fh.seek(offset + 4 + 20 + 68)
            return struct.unpack("<H", fh.read(2))[0] == 2
    except (OSError, struct.error, ValueError):
        return None


def check_dist(root=ROOT):
    problems = []
    for path in sorted(glob.glob(os.path.join(root, "dist", "*.zip"))):
        problems.extend(check_zip(path))
    exe = os.path.join(root, "dist", "LeadStudio.exe")
    if os.path.isfile(exe):
        windowed = exe_is_windowed(exe)
        if windowed is False:
            problems.append("dist/LeadStudio.exe is a console build; rebuild with --noconsole")
    return problems


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # A working copy is allowed to keep its own PERSONAL_README.md; nothing else is.
    allow = ("PERSONAL_README.md",) if "--working-copy" in args else ()
    problems = check_tree(allow=allow) + check_version()
    if "--zips" in args:
        problems.extend(check_dist())
    if problems:
        for problem in problems:
            print("FAIL %s" % problem)
        print("%d problem(s) - do not publish this build." % len(problems))
        return 1
    print("OK - nothing personal in %s" % ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
