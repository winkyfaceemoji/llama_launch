"""Screenshots of the launcher GUI, for before/after comparisons of its look.

    python dev/gui_shot.py <launcher.pyw> <out dir> [--theme light|dark]

Builds the window from the given launcher.pyw (any version) and saves home.png,
settings.png and running.png. It never clicks Launch or starts a process; the running
view is only the layout, with a few sample log lines. Building the window clears the
model test lock, so it refuses to run while one exists.
"""
import argparse
import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import tkinter as tk
from ctypes import wintypes

from PIL import ImageGrab


def window_rect(win):
    # The whole frame, title bar included.
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("launcher")
    ap.add_argument("out")
    ap.add_argument("--theme", choices=("light", "dark"))
    args = ap.parse_args()

    path = os.path.abspath(args.launcher)
    if os.path.exists(os.path.join(os.path.dirname(path), "model_test.lock")):
        sys.exit("model_test.lock exists: a model test is running. Not building the GUI.")
    os.makedirs(args.out, exist_ok=True)
    sys.path.insert(0, os.path.dirname(path))
    loader = importlib.machinery.SourceFileLoader("launcher", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("launcher", loader))
    loader.exec_module(mod)

    root = tk.Tk()
    app = mod.CommandLauncherGUI(root)
    if args.theme and hasattr(mod, "apply_theme"):
        app._theme = args.theme  # else the System-mode watcher switches back to Windows' theme
        mod.apply_theme(root, args.theme)

    def shot(win, name):
        win.lift()
        win.attributes("-topmost", True)
        win.update()
        ImageGrab.grab(window_rect(win)).save(os.path.join(args.out, name))
        win.attributes("-topmost", False)

    def home():
        shot(root, "home.png")
        app._open_settings()
        root.after(600, settings)

    def settings():
        dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        shot(dlg, "settings.png")
        dlg.destroy()
        app._enter_running_state()
        for line in ("main: server is listening on http://127.0.0.1:8081",
                     "srv  update_slots: all slots are idle"):
            app._append_log(app.llama_log, line)
        app._append_log(app.proxy_log, "Proxy listening on 127.0.0.1:8080 -> 127.0.0.1:8081")
        root.after(600, running)

    def running():
        shot(root, "running.png")
        os._exit(0)  # skip _on_close: nothing was started, and the update check thread may still run

    root.after(1200, home)
    root.mainloop()


if __name__ == "__main__":
    main()
