# The app window

Lead Studio opens in its own desktop window by default when `pywebview` is installed:

```bash
python leadstudio.py            # own window if pywebview is available
python leadstudio.py --window   # force the window
python leadstudio.py --browser  # force the system browser
python leadstudio.py --no-browser   # headless: just the server
```

Under the hood the HTTP server still runs locally and the window is a WebView2 view onto it. Nothing
in the app changes: same UI, same data, same port.

## Requirements

- `pip install pywebview` (about 2 MB, no other dependencies on Windows).
- The **WebView2 runtime**, which is preinstalled on Windows 11 and Windows 10 21H2+. On older
  machines install the small "Evergreen Bootstrapper" from Microsoft; without it, pywebview falls
  back to the old MSHTML engine, which renders the UI poorly, so install it.

## Fallbacks

| Situation | What happens |
| --- | --- |
| pywebview not installed | The app prints a note and opens the browser. |
| Window cannot be created | The error is printed and the browser opens instead. |
| Window closes within two seconds | Treated as a failure; the browser opens so the app is still usable. |
| OAuth sign-in | Deliberately opens in the system browser: Google and Microsoft refuse their consent screens inside embedded webviews. |

## Packaging

The `.exe` built by `BUILD_EXE.bat` includes pywebview when it is installed at build time
(~4 MB larger, 27 MB total). The release workflow installs it too. If you build without pywebview,
the exe simply uses the browser path.

## No console, and the tray icon

`START_LEAD_STUDIO.bat` runs `pythonw`, so double-clicking gives you the window and nothing else.
The exe is built with `--noconsole`. Use `START_CONSOLE.bat` when you need to read messages.

With no console there is no obvious way to quit, so the app installs a **tray icon**:

| Tray action | Effect |
| --- | --- |
| Double-click / *Open Lead Studio* | opens the app in your browser |
| *Quit* | stops the server and exits |

`--no-tray` disables it (headless runs). If pystray or Pillow is missing, the app prints a note and
carries on - the tray is never required.

## Known limits

- File downloads (Parquet / DuckDB export) are handled by WebView2's download prompt, not the
  browser's downloads shelf.
- No auto-update and no taskbar pinning yet - that is the Electron/Tauri step.
