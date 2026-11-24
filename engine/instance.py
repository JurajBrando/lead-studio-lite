"""One running copy per data folder.

Two copies on the same database means two send workers and two schedulers: the same person
can be mailed twice and the same job run twice. The first copy takes an OS-level lock on
`data/app.lock` (released by the system when the process ends, however it ends, so a crash
never leaves the app locked out) and writes its port to `data/app.port`. A second start sees
the lock, opens the copy that is already running and exits.
"""
import os

from . import config

_handle = None


def _paths():
    return (os.path.join(config.DATA_DIR, "app.lock"), os.path.join(config.DATA_DIR, "app.port"))


def acquire():
    """True when this process now owns the data folder, False when another one does."""
    global _handle
    if _handle is not None:
        return True
    if os.environ.get("LS_ALLOW_MULTI"):
        return True
    config.ensure_dirs()
    lock_path, _ = _paths()
    try:
        handle = open(lock_path, "a+")
    except OSError:
        return True                      # cannot even create the file: do not block start-up
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _handle = handle                     # kept open for the life of the process
    return True


def release():
    global _handle
    if _handle is None:
        return
    try:
        _handle.close()
    except OSError:
        pass
    _handle = None


def write_port(port):
    try:
        with open(_paths()[1], "w", encoding="utf-8") as fh:
            fh.write(str(int(port)))
    except OSError:
        pass


def running_port():
    """The port of the copy that holds the lock (the default when it was never written)."""
    try:
        with open(_paths()[1], encoding="utf-8") as fh:
            return int(fh.read().strip() or 0) or config.DEFAULT_PORT
    except (OSError, ValueError):
        return config.DEFAULT_PORT
