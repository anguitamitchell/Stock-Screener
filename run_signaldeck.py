import threading
import time
import webbrowser
from pathlib import Path
import os
import sys

from self_screener.web_app import DEFAULT_HOST, DEFAULT_PORT, ServerStartupError, run_server


def _open_browser(host: str, port: int) -> None:
    time.sleep(1.0)
    webbrowser.open(f"http://{host}:{port}/")


def _show_startup_error(message: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("SignalDeck could not start", message)
        root.destroy()
    except Exception:
        print(message)


def main() -> None:
    app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    os.chdir(app_root)
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    threading.Thread(target=_open_browser, args=(host, port), daemon=True).start()
    try:
        run_server(host, port)
    except ServerStartupError as exc:
        _show_startup_error(str(exc))


if __name__ == "__main__":
    main()
