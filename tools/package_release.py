"""Build the download packages, using nothing but the standard library.

    python tools/package_release.py

Writes into dist/:

    LeadStudioLite-<version>-source.zip    the clean source tree (no data, no caches)
    LeadStudioLite-<version>-windows.zip   LeadStudio.exe + README + LICENSE + docs
                                           (only if dist/LeadStudio.exe exists)

Both zips are what you attach to a GitHub release or upload to whichever
download page you use.
"""
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

SKIP_DIRS = {"data", "__pycache__", ".git", "dist", "build", ".venv", "venv",
             ".idea", ".vscode", ".pytest_cache", "node_modules"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIX = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log", ".tmp", ".spec")
TOP_FOLDER = "LeadStudioLite"


def version():
    with open(os.path.join(ROOT, "engine", "config.py"), encoding="utf-8") as fh:
        match = re.search(r'VERSION\s*=\s*"([^"]+)"', fh.read())
    return match.group(1) if match else "0.0.0"


def _write(zf, full_path, arc_path):
    zf.write(full_path, arc_path.replace(os.sep, "/"))


def build_source(ver):
    out = os.path.join(DIST, "LeadStudioLite-%s-source.zip" % ver)
    os.makedirs(DIST, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name in SKIP_FILES or name.endswith(SKIP_SUFFIX):
                    continue
                full = os.path.join(base, name)
                rel = os.path.relpath(full, ROOT)
                _write(zf, full, os.path.join(TOP_FOLDER, rel))
                count += 1
    return out, count


def build_windows(ver):
    exe = os.path.join(DIST, "LeadStudio.exe")
    if not os.path.isfile(exe):
        return None, 0
    out = os.path.join(DIST, "LeadStudioLite-%s-windows.zip" % ver)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        _write(zf, exe, os.path.join(TOP_FOLDER, "LeadStudio.exe"))
        count += 1
        for name in ("README.md", "LICENSE"):
            full = os.path.join(ROOT, name)
            if os.path.isfile(full):
                _write(zf, full, os.path.join(TOP_FOLDER, name))
                count += 1
        docs = os.path.join(ROOT, "docs")
        for base, dirs, files in os.walk(docs):
            for name in sorted(files):
                full = os.path.join(base, name)
                rel = os.path.relpath(full, ROOT)
                _write(zf, full, os.path.join(TOP_FOLDER, rel))
                count += 1
    return out, count


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def main():
    ver = version()
    print("Lead Studio Lite %s" % ver)
    src, n = build_source(ver)
    print("  source : %s  (%d files, %s)" % (os.path.relpath(src, ROOT), n,
                                             human(os.path.getsize(src))))
    win, n2 = build_windows(ver)
    if win:
        print("  windows: %s  (%d files, %s)" % (os.path.relpath(win, ROOT), n2,
                                                 human(os.path.getsize(win))))
    else:
        print("  windows: skipped (build dist/LeadStudio.exe with BUILD_EXE.bat first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
