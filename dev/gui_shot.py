"""Screenshots of the launcher GUI, for before/after comparisons of its look.

    python dev/gui_shot.py <launcher.pyw> <out dir> [--theme light|dark]

Builds the window from the given launcher.pyw (any version) and saves home.png,
dropdown.png, preset_menu.png, settings.png (and its agents, llama and advanced pages), preset.png, folders.png, prompts.png (editable),
prompts_running.png (read-only), update.png and running.png. A view
an older version can't build is skipped with a note. It never clicks Launch or starts a
process: the running view is only the layout with a few sample log lines, and the update
dialog runs a fake job. Building the window clears the model test lock, so it refuses to
run while one exists.
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

    def newest_dialog():
        return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]

    def shot_dialog(name):
        dlg = newest_dialog()
        shot(dlg, name)
        dlg.destroy()

    def dropdown():
        root.focus_force()
        app.model_menu._show_popup()
        popup = app.model_menu._popup
        popup.unbind("<FocusOut>")  # the grab below would otherwise close it
        popup.update()
        left, top, right, bottom = window_rect(root)
        bottom = max(bottom, popup.winfo_rooty() + popup.winfo_height() + 8)
        ImageGrab.grab((left, top, right, bottom)).save(os.path.join(args.out, "dropdown.png"))
        popup.destroy()
        app.model_menu._popup = None

    def menu_shot(popup, name):
        # A borderless popup sits outside the window's frame: grab the window plus whatever hangs below it.
        popup.unbind("<FocusOut>")  # the grab below would otherwise close it
        popup.update()
        left, top, right, bottom = window_rect(root)
        bottom = max(bottom, popup.winfo_rooty() + popup.winfo_height() + 8)
        ImageGrab.grab((left, top, right, bottom)).save(os.path.join(args.out, name))
        popup.destroy()

    def preset_menu():
        root.focus_force()
        menu_shot(app._open_preset_menu(), "preset_menu.png")

    def settings_page(name):
        newest_dialog()._show(name)

    def preset():
        mod.PresetDialog(root, "Edit preset", name="Auto", command="llama-server -m {model} --fit on",
                         model_keywords=[], available_models=sorted(app._models)[:6])

    def prompts(editable):
        texts = dict(mod.agent_prompts.effective(app.config), chat=app.config.get("system_prompt", ""))
        mod.PromptsDialog(root, texts, mod.shipped_prompts(), editable, lambda texts: None)

    def update():
        mod.UpdateDialog(root, "Update to b99999", lambda report: (report("winget upgrade ..."),
                                                                  (True, "Updated. Build check passed."))[1])

    def running():
        app._enter_running_state()
        for line in ("main: server is listening on http://127.0.0.1:8081",
                     "srv  update_slots: all slots are idle"):
            app._append_log(app.llama_log, line)
        app._append_log(app.proxy_log, "Proxy listening on 127.0.0.1:8080 -> 127.0.0.1:8081")
        app._append_log(app.uvx_log, "[agents] exited (code 1)")  # a background tab's error shows its dot

    # (open the view, its screenshot) in order; the running view changes the layout, so it goes last.
    steps = [
        (lambda: None, lambda: shot(root, "home.png")),
        (dropdown, lambda: None),
        (preset_menu, lambda: None),
        (app._open_settings, lambda: shot(newest_dialog(), "settings.png")),
        (lambda: settings_page("agents"), lambda: shot(newest_dialog(), "settings_agents.png")),
        (lambda: settings_page("llama"), lambda: shot(newest_dialog(), "settings_llama.png")),
        (lambda: settings_page("advanced"), lambda: shot_dialog("settings_advanced.png")),
        (preset, lambda: shot_dialog("preset.png")),
        (lambda: mod.FoldersDialog(root, app.agent_policy, None), lambda: shot_dialog("folders.png")),
        (lambda: prompts(True), lambda: shot_dialog("prompts.png")),
        (lambda: prompts(False), lambda: shot_dialog("prompts_running.png")),
        (update, lambda: shot_dialog("update.png")),
        (running, lambda: shot(root, "running.png")),
    ]

    def run(i=0):
        if i == len(steps):
            os._exit(0)  # skip _on_close: nothing was started, and the update check thread may still run
        open_view, take = steps[i]
        try:
            open_view()
        except Exception as e:  # an older launcher.pyw may lack this view
            print(f"skipped step {i + 1}: {e}", flush=True)
            root.after(0, run, i + 1)
            return

        def capture():
            try:
                take()
            except Exception as e:
                print(f"skipped step {i + 1}: {e}", flush=True)
            run(i + 1)
        root.after(700, capture)

    root.after(1200, run)
    root.mainloop()


if __name__ == "__main__":
    main()
