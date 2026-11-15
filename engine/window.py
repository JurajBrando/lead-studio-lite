"""Open Lead Studio in its own desktop window instead of a browser tab.

pywebview renders the existing local web UI inside a native window using the
Edge WebView2 runtime that ships with Windows. Nothing about the server, the UI
or the data changes - this is only a different container for the same page.

Optional by design: if pywebview or the WebView2 runtime is missing, the caller
falls back to the browser. The app must never fail to start because a window
could not be created.
"""

TITLE = "Lead Studio Lite"


def available():
    """True when pywebview can be imported (the runtime check happens at start)."""
    try:
        import webview  # noqa: F401
    except Exception:                                  # noqa: BLE001 - any import problem
        return False
    return True


def reason():
    """Human-readable explanation for the diagnostics endpoint."""
    try:
        import webview  # noqa: F401
    except Exception as exc:                           # noqa: BLE001
        return "pywebview not available: %s" % exc
    try:
        import importlib.metadata as meta
        version = meta.version("pywebview")
    except Exception:                                  # noqa: BLE001
        version = "unknown"
    return "pywebview %s" % version


def run(url, title=TITLE, width=1280, height=860):
    """Create the window and block until it closes."""
    import webview

    window = webview.create_window(
        title, url,
        width=width, height=height, min_size=(1024, 700),
        confirm_close=False, text_select=True,
    )
    # gui defaults to edgechromium on Windows; the fallback chain is pywebview's job.
    webview.start()
    return window
