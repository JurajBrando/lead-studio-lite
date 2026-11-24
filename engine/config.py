"""Paths, defaults and settings storage for Lead Studio Lite.

Everything the app writes lives in ./data next to the launcher, so the whole
folder can be zipped, copied to another PC and still work.
"""
import os
import json
import secrets
import sys
import threading
import time

VERSION = "1.37.1"
APP_NAME = "Lead Studio Lite"

# The LinkedIn table (about 105 million contacts) is sold as a subscription. The app does not
# ship the downloader: the Leads > LinkedIn data button opens this page, and nothing else.
LINKEDIN_OFFER_URL = "https://123brando--verkaufstunnel.thrivecart.com/leadscm-unlimited-leads-offer/"

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # Packed with PyInstaller: the exe folder holds data, the bundle holds the UI.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    # .../LeadStudioLite/engine/config.py -> .../LeadStudioLite
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BUNDLE_DIR = APP_DIR

DATA_DIR = os.environ.get("LS_DATA") or os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "leadstudio.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
UI_DIR = os.path.join(BUNDLE_DIR, "ui")
LOG_DIR = os.path.join(DATA_DIR, "logs")

DEFAULT_PORT = int(os.environ.get("LS_PORT") or 8791)


def ensure_dirs():
    for d in (DATA_DIR, LOG_DIR):
        os.makedirs(d, exist_ok=True)


DEFAULTS = {
    "onboarded": False,
    "company_name": "",
    "sender_name": "",
    "contact_email": "",
    "sending": {
        "enabled": True,
        "window_start": 8,
        "window_end": 18,
        "weekdays_only": True,
        "gap_seconds": 45,
        "jitter_seconds": 30,
    },
    "tracking": {"enabled": True, "base_url": ""},
    "verify": {"mx": True, "smtp_probe": False},
    "connectors": {"hunter_key": "", "ai_base": "", "ai_key": "", "ai_model": "",
                   "serper_key": "", "dnsniffer_key": ""},
    # The offers shipped in the app are the distributor's recommendations; keep them
    # read-only for the person running it. Unlock in Settings to edit your own build.
    "offers_locked": True,
    "ui": {"density": "comfortable"},
}


# One lock for every read and write of settings.json inside this process. On Windows a file
# that another thread has open cannot be replaced (WinError 32), and every request reads it.
_SETTINGS_LOCK = threading.RLock()


def _retry_io(action, attempts=12, pause=0.05):
    """Run a file operation again when Windows says another process has the file open.

    The lock above covers this process; the MCP server, a second copy of the app or an
    antivirus scan can still hold settings.json for a moment.
    """
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _quarantine_settings():
    """Keep a copy of a settings file we could not read, instead of overwriting it."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = SETTINGS_PATH + ".broken-" + stamp
    try:
        os.replace(SETTINGS_PATH, backup)
        log("settings.json could not be read; the old file is kept at %s" % backup)
        return backup
    except OSError as exc:
        log("settings.json could not be read and could not be backed up: %s" % exc)
        return ""


def load_settings():
    ensure_dirs()

    def read():
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            return json.load(fh)

    with _SETTINGS_LOCK:
        data = {}
        broken = False
        try:
            data = _retry_io(read)
            if not isinstance(data, dict):
                # Valid JSON, but not an object: merge would crash on every request.
                data = {}
                broken = True
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError, UnicodeDecodeError):
            data = {}
            broken = True
        if broken:
            _quarantine_settings()
        s = _deep_merge(DEFAULTS, data)
        if not s.get("session_token"):
            s["session_token"] = secrets.token_hex(24)
            save_settings(s)
        return s


def save_settings(s):
    ensure_dirs()
    clean = _deep_merge(DEFAULTS, s)
    with _SETTINGS_LOCK:
        # A name of its own per writer: two processes sharing "settings.json.tmp" could
        # truncate each other's half-written file.
        tmp = "%s.%d.%d.tmp" % (SETTINGS_PATH, os.getpid(), threading.get_ident())
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, indent=2)
            _retry_io(lambda: os.replace(tmp, SETTINGS_PATH))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return clean


def log(msg):
    """Append to data/logs/app.log (best effort, never raises)."""
    try:
        ensure_dirs()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(LOG_DIR, "app.log"), "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (stamp, msg))
    except OSError:
        pass


def python_hint():
    return "Python %s on %s" % (sys.version.split()[0], sys.platform)
