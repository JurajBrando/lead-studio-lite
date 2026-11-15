"""System tray icon.

When the app runs without a console window (pythonw, or the --noconsole exe),
the tray icon is how you get back to it and how you quit it.

Optional: if pystray or Pillow is missing, the caller simply carries on without
a tray icon. Nothing about the app depends on it.
"""
import os
import threading
import webbrowser

TITLE = "Lead Studio Lite"


def available():
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:                                  # noqa: BLE001
        return False
    return True


def reason():
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:                           # noqa: BLE001
        return "tray unavailable: %s" % exc
    try:
        import importlib.metadata as meta
        return "pystray %s" % meta.version("pystray")
    except Exception:                                  # noqa: BLE001
        return "pystray"


def _icon_image(size=64):
    """The app mark, drawn at runtime so there is no asset to ship."""
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22),
                           fill=(91, 140, 255, 255))
    bar = max(4, int(size * 0.11))
    left = int(size * 0.30)
    top = int(size * 0.20)
    bottom = int(size * 0.76)
    right = int(size * 0.70)
    draw.rectangle([left, top, left + bar, bottom], fill=(255, 255, 255, 255))
    draw.rectangle([left, bottom - bar, right, bottom], fill=(255, 255, 255, 255))
    return image


def start(url, on_quit, on_open=None, title=TITLE):
    """Create the tray icon. Returns the icon object (keep a reference)."""
    import pystray

    def _open(icon=None, item=None):
        try:
            if on_open:
                on_open(url)
            else:
                webbrowser.open(url)
        except Exception:                              # noqa: BLE001 - a menu click must not crash
            pass

    def _quit(icon=None, item=None):
        try:
            icon.stop()
        except Exception:                              # noqa: BLE001
            pass
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("Open Lead Studio", _open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("leadstudio", _icon_image(), title, menu)
    icon.run_detached()
    return icon
