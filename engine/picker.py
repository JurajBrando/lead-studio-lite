"""A native "open file" dialog, in its own process.

A browser cannot tell the page the real path of a file the user picks, and the big
imports (a 2 GB parquet, a 10 GB DuckDB) cannot travel through a form either - they
have to be read where they are. So the page asks the server for a path, the server
runs this module as a child process, and the dialog is a plain tkinter one: standard
library, no extra dependency, and its own main thread (tkinter must not be driven from
an HTTP worker thread).

The child writes the chosen path into a small file instead of printing it, so the
windowed build (pythonw / the packaged exe), where stdout is a null sink, works too.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile

from . import config

# Everything the lead importer can read.
IMPORT_EXTENSIONS = ("csv", "tsv", "txt", "xlsx", "xlsm", "parquet", "duckdb", "ddb",
                     "db", "sqlite", "sqlite3")
TIMEOUT = 600


class PickerError(Exception):
    """The dialog could not be opened - the page shows the message."""


def available():
    return importlib.util.find_spec("tkinter") is not None


def choose_many(title="Choose files", extensions=IMPORT_EXTENSIONS):
    """Show the dialog in multi-select mode and return a list of paths."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    pattern = " ".join("*." + str(ext).lstrip(".") for ext in extensions) or "*.*"
    try:
        chosen = filedialog.askopenfilenames(
            parent=root, title=title,
            filetypes=[("Files the importer reads", pattern), ("All files", "*.*")])
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return [str(x) for x in (chosen or ())]


def choose(title="Choose a file", extensions=IMPORT_EXTENSIONS):
    """Show the dialog and return the chosen path, or "" when cancelled."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    pattern = " ".join("*." + str(ext).lstrip(".") for ext in extensions) or "*.*"
    try:
        path = filedialog.askopenfilename(
            parent=root, title=title,
            filetypes=[("Files the importer reads", pattern), ("All files", "*.*")])
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return path or ""


def _command(out_path, title, extensions, multi=False):
    """How to start the child dialog: this app, so it works frozen as well."""
    if config.FROZEN:
        base = [sys.executable]
    else:
        base = [sys.executable, os.path.join(config.APP_DIR, "leadstudio.py")]
    cmd = base + ["--pick-file", "--out", out_path, "--title", title,
                  "--extensions", ",".join(extensions)]
    if multi:
        cmd.append("--multi")
    return cmd


def ask(title="Choose a file", extensions=None, timeout=TIMEOUT, multi=False):
    """Open the dialog from a request handler. Returns {ok, path} or {ok, cancelled}."""
    extensions = tuple(extensions or IMPORT_EXTENSIONS)
    if not available():
        raise PickerError("This build has no file dialog (tkinter is missing). "
                          "Type the path instead.")
    handle, out_path = tempfile.mkstemp(prefix="ls_pick_", suffix=".txt")
    os.close(handle)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(_command(out_path, title, extensions, multi),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, creationflags=flags,
                                env=env)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise PickerError("The file dialog failed: %s"
                              % (detail[-1] if detail else "exit code %d" % result.returncode))
        with open(out_path, "r", encoding="utf-8") as handle:
            chosen = handle.read().strip()
    except subprocess.TimeoutExpired:
        raise PickerError("The file dialog was left open for too long.")
    except OSError as exc:
        raise PickerError("Could not start the file dialog: %s" % exc)
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
    if not chosen:
        return {"ok": True, "cancelled": True}
    if multi:
        paths = [line.strip() for line in chosen.splitlines() if line.strip()]
        return {"ok": True, "cancelled": False, "paths": paths,
                "files": [{"path": x, "name": os.path.basename(x),
                           "exists": os.path.isfile(x),
                           "size_mb": round(os.path.getsize(x) / 1048576.0, 1)
                           if os.path.isfile(x) else 0} for x in paths]}
    return {"ok": True, "cancelled": False, "path": chosen, "paths": [chosen],
            "name": os.path.basename(chosen), "exists": os.path.isfile(chosen)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="picker")
    parser.add_argument("--out", required=True, help="file to write the chosen path into")
    parser.add_argument("--title", default="Choose a file")
    parser.add_argument("--extensions", default="")
    parser.add_argument("--multi", action="store_true")
    args = parser.parse_args(argv)
    extensions = tuple(x for x in args.extensions.split(",") if x) or IMPORT_EXTENSIONS
    try:
        if args.multi:
            text = "\n".join(choose_many(args.title, extensions))
        else:
            text = choose(args.title, extensions) or ""
    except Exception as exc:                       # noqa: BLE001 - report, never traceback
        print("dialog failed: %s" % exc, file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
