"""Lead Studio Lite - a small, self-contained sales development workspace.

Run it:   double-click START_LEAD_STUDIO.bat   (or)   python leadstudio.py

Options:
    --port N        use a specific port (default 8791, next free port if taken)
    --no-browser    don't open the browser automatically
    --seed-demo     load the demo dataset before starting
    --reset-demo    wipe demo data before starting
    --version       print the version and exit
"""
import argparse
import os
import sys

# A windowed build (pyinstaller --noconsole) and pythonw have no stdout/stderr.
# Printing to None raises, so give them a sink before anything else runs.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from engine import config, db, demo  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadstudio", add_help=True)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true",
                    help="open in its own desktop window (default when pywebview is available)")
    ap.add_argument("--browser", action="store_true", help="force the system browser")
    ap.add_argument("--no-tray", action="store_true", help="do not create the tray icon")
    ap.add_argument("--seed-demo", action="store_true")
    ap.add_argument("--reset-demo", action="store_true")
    ap.add_argument("--seed-offers", action="store_true",
                    help="import the starter offer pack from samples/offers-pack.json")
    ap.add_argument("--mcp", action="store_true",
                    help="run the MCP stdio server instead of the web app")
    ap.add_argument("--video-maker", action="store_true",
                    help="render one video from a spec file (used by the Social tab)")
    ap.add_argument("--spec", default="", help="path to the video spec (with --video-maker)")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        print("%s %s" % (config.APP_NAME, config.VERSION))
        return 0

    if args.video_maker:
        # Runs as a child process of the app, so the render cannot block the server. On a
        # frozen build sys.executable is this exe, which is why this mode exists at all.
        from engine import video_maker
        sys.argv = ["video_maker.py", "--spec", args.spec]
        video_maker.main()
        return 0

    if args.mcp:
        from engine import mcp as mcp_server
        mcp_server.main()
        return 0

    db.init_db()
    if args.reset_demo:
        print(demo.remove())
    if args.seed_demo:
        print(demo.seed())
    if args.seed_offers:
        from engine import offers as offers_module
        print(offers_module.import_pack())

    from engine import window as window_mod
    from engine.server import serve
    use_window = args.window or (not args.browser and not args.no_browser
                                 and window_mod.available())
    serve(port=args.port, open_browser=not args.no_browser and not use_window,
          window=use_window, tray=not args.no_tray)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
