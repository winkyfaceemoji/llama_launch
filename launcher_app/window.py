"""The launcher window: setup, home and running views, and process control (ADR 0021)."""
from agents import jobs as agent_jobs
from agents import llm as agent_llm
from agents import paths as agent_paths
from agents import prompts as agent_prompts
from tkinter import messagebox
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from .paths import AGENTS_SERVER, API_KEY_FILE, CONFIG_FILE, ICON_FILE, MODEL_TEST_LOCK, ROOT
from .theme import BG, CURRENT_THEME, DANGER, H_RUNNING, H_SETUP, SUCCESS, TEXT_MUT, W, apply_theme, apply_ttk_style, resolve_theme, set_dark_title_bar, set_palette, theme_setting, windows_light
from .selection import PresetError, Selection
from .config import ConfigStore, UVX_COMMAND, backup_config, load_api_key, migrate_uvx_command, remove_stale_agents_entry, resolve_uvx_command, set_prompts, shipped_prompts
from .processes import clear_test_lock, health_status, is_own_process, listening_pids, model_test_command, write_test_lock
from .updates import UpdateRunner
from .widgets import _TRAY_AVAILABLE, _make_tray_image, popup_menu, pystray
from .views import HomeView, RunningView, SetupView
from .dialogs import FoldersDialog, PresetDialog, PromptsDialog, SettingsDialog, TestDialog, UpdateDialog
from . import theme
theme.follow(globals())  # colour names follow set_palette (ADR 0020)


class CommandLauncherGUI:
    def __init__(self, master, store=None):
        self.master = master

        # config.json, through the store: it checks every value and is the only writer (ConfigError if bad).
        self.store     = store or ConfigStore.load()
        self.config    = self.store.data  # read-only here: every change goes through _save
        self.llama_dir = self.config.get("llama_dir", "")

        # The palette must be set before any widget is built; widgets read it when created.
        self._theme = theme_setting(self.config)
        self._theme_preview = None  # a theme picked in Settings but not saved yet
        set_palette(resolve_theme(self._theme, windows_light()))
        master.title("")
        master.configure(bg=BG)
        master.resizable(False, False)
        master.iconbitmap(default=ICON_FILE)  # default= gives every dialog the icon too

        apply_ttk_style()
        # Every dialog gets the title bar of the current theme when it first shows.
        # Class bindings fire for the window itself, not for its children.
        for cls in ("Tk", "Toplevel"):
            master.bind_class(cls, "<Map>",
                              lambda e: set_dark_title_bar(e.widget, CURRENT_THEME == "dark"), add="+")
        self._watch_windows_theme()

        # Esc closes the dialog it's pressed in the way its X would (dialogs with their own close logic keep it).
        master.bind_all("<Escape>", self._on_escape, add="+")

        # Kill all child processes when the window is closed
        master.protocol("WM_DELETE_WINDOW", self._force_exit)

        # A model test lock left by a crash would keep Web UI chat paused.
        clear_test_lock(MODEL_TEST_LOCK)

        master.grid_columnconfigure(0, weight=1)
        master.grid_rowconfigure(0, weight=1)
        self.view = None            # the shown SetupView, HomeView or RunningView
        self.running = False        # the servers are up: between Launch and Exit Processes
        self._update_builds = None  # (installed, newest) once the startup check finds a newer build
        if not self.llama_dir:
            self._switch(SetupView)
            master.geometry(f"{W}x{H_SETUP}")
        else:
            self._start()


    # ── Views ────────────────────────────────────────────────

    def _switch(self, view_class):
        # Throw the shown view away and build a fresh one in its place.
        if self.view is not None:
            self.view.destroy()
        self.view = view_class(self.master, self)
        self.view.grid(row=0, column=0, sticky="nsew")
        return self.view

    def finish_setup(self, path):
        # From the setup view: remember the folder, then start as on any later run.
        self.llama_dir = path
        self._save(llama_dir=path)
        self._start()

    def _start(self):
        # Once per run, with a llama.cpp folder known: the app's state, then the home view.
        # Application state. The store already checked the presets.
        self.selection = Selection(self.config.get("presets", {}), self.config.get("last_model", ""),
                                   self.llama_dir, API_KEY_FILE,
                                   remember_model=lambda name: self._save(last_model=name),
                                   save_presets=lambda presets: self._save(presets=presets))
        self._open_browser = self.config.get("open_browser", True)
        if migrate_uvx_command(self.config):
            self._save()
        self.uvx_command   = self.config.get("uvx_command", UVX_COMMAND)
        self.api_key       = load_api_key(API_KEY_FILE)
        # Only the jobs panel sends this one, so llama API key holders can't approve plans (ADR 0009).
        self.approval_key  = load_api_key(agent_llm.APPROVAL_KEY_FILE)
        self.agent_policy  = agent_paths.Policy(agent_paths.default_sandbox())
        self._agents_error = None
        try:
            self.agent_policy.ensure_dirs()
        except OSError as e:
            # Agents are optional: an unusable sandbox must never stop the launcher.
            self._agents_error = str(e)
        self._llama_proc   = None
        self._uvx_proc     = None
        self._proxy_proc   = None
        self._agents_proc  = None
        self._polling      = False
        self._poll_gen     = 0      # each launch's health poll has its own number; an older loop stops
        self._launch_time  = None
        self._test_dialog  = None
        self._preset_popup = None   # the open "⋯" menu, if any

        # Enter launches from the home view; Ctrl+, opens Settings. Bound on the root's own tag, so they never
        # fire from a dialog (each dialog is its own toplevel with its own tags).
        self.master.bind("<Return>", self._on_return)
        self.master.bind("<Control-comma>", lambda e: self.open_settings())
        self._show_home()
        self._setup_tray()
        threading.Thread(target=self._check_for_update, daemon=True).start()

    def _show_home(self):
        home = self._switch(HomeView)
        home.show_selection(self.selection.view)
        home.show_update(self._update_builds)
        self.master.resizable(False, False)
        self._resize_config()


    # ── Settings ─────────────────────────────────────────────

    def open_settings(self):
        def on_save(new_path):
            changed = new_path != self.llama_dir
            self.llama_dir = new_path
            self._save(llama_dir=new_path)
            if changed:  # the model list and launch command must come from the new folder
                self._show_selection(self.selection.set_folder(new_path))

        def on_save_uvx(cmd):
            self.uvx_command = cmd
            self._save(uvx_command=cmd)

        # The proxy and agents server read the prompts once at startup: edit only while they're stopped.
        running = self.running

        def open_prompts(parent):
            texts = dict(agent_prompts.effective(self.config), chat=self.config.get("system_prompt", ""))
            PromptsDialog(parent, texts, shipped_prompts(), not running, self._save_prompts)

        SettingsDialog(
            self.master, self.llama_dir, on_save,
            uvx_command=self.uvx_command,
            on_save_uvx=on_save_uvx,
            open_browser=self._open_browser,
            on_set_open_browser=self._set_open_browser,
            on_open_folders=lambda parent: FoldersDialog(parent, self.agent_policy, self._agents_error),
            previous_build=self.config.get("previous_build"),
            on_rollback=None if running else self._start_rollback,
            theme=self._theme, on_set_theme=self._set_theme, on_preview_theme=self._preview_theme,
            on_open_prompts=open_prompts, prompts_editable=not running,
        )

    def _save_prompts(self, texts):
        problem = set_prompts(self.config, texts)
        if problem:
            return problem
        try:
            backup_config()
        except OSError as e:
            return f"Couldn't back up config.json first, so nothing was saved: {e}"
        self._save()
        return None


    # ── llama.cpp updates ─────────────────────────────────────

    def _check_for_update(self):
        # Background thread, once per start. WinGet or network errors show nothing.
        found = UpdateRunner(lambda line: None).check()
        if found:
            self.master.after(0, self._show_update_notice, *found)

    def _show_update_notice(self, installed, newest):
        # The notice lives on the home view. Windows locks a running llama-server.exe, so while the servers
        # run it waits for the home view that Exit Processes builds.
        self._update_builds = (installed, newest)
        if isinstance(self.view, HomeView):
            self.view.show_update(self._update_builds)
            self._resize_config()

    def start_update(self):
        installed, newest = self._update_builds
        self._save(previous_build=f"b{installed}")
        model_path, mmproj = self.selection.view.model_files
        runners = []

        def job(report):
            runners.append(UpdateRunner(report))
            return runners[0].update(installed, newest, model_path, mmproj, API_KEY_FILE)
        UpdateDialog(self.master, f"Update to b{newest}", job,
                     on_done=lambda ok: self._update_done(ok, runners and runners[0].rolled_back))

    def _update_done(self, ok, rolled_back):
        if ok:
            self._update_builds = None
            if isinstance(self.view, HomeView):
                self.view.show_update(None)
                self._resize_config()
        elif rolled_back:
            # Back on the build previous_build names: nothing to roll back to. A failed
            # roll back keeps it, so Settings can retry from the broken build.
            self._save(previous_build=None)

    def _start_rollback(self, build):
        model_path, mmproj = self.selection.view.model_files
        UpdateDialog(self.master, f"Roll back to b{build}",
                     lambda report: UpdateRunner(report).rollback_and_check(
                         build, model_path, mmproj, API_KEY_FILE),
                     on_done=self._rollback_done)

    def _rollback_done(self, ok):
        # Cleared so the startup notice offers the newer build again.
        if ok:
            self._save(previous_build=None)


    # ── Window sizing ─────────────────────────────────────────

    def _resize_config(self):
        if isinstance(self.view, HomeView):
            self.master.geometry(f"{W}x{self.view.height()}")

    def _on_escape(self, event):
        win = event.widget.winfo_toplevel()
        if win is self.master:
            return
        close = win.protocol("WM_DELETE_WINDOW")
        if close:
            win.tk.call(close)
        else:
            win.destroy()

    def _on_return(self, event):
        # Enter launches from the home view (not while running, not from a dialog).
        if isinstance(self.view, HomeView):
            self.launch()

    def _preview_theme(self, theme):
        # From Settings, before Save: show theme (None = back to the saved one) without saving it.
        self._theme_preview = theme
        name = resolve_theme(theme or self._theme, windows_light())
        if name != CURRENT_THEME:
            apply_theme(self.master, name)

    def _set_theme(self, theme):
        # From Settings' Save.
        self._theme_preview = None
        self._theme = theme
        self._save(theme=theme)
        name = resolve_theme(theme, windows_light())
        if name != CURRENT_THEME:
            apply_theme(self.master, name)

    def _watch_windows_theme(self):
        # In System mode, follow a change of Windows' setting. Runs on the UI thread (tkinter isn't thread-safe).
        # A Settings preview counts as the setting, or the watcher would undo it.
        if (self._theme_preview or self._theme) == "system":
            name = resolve_theme("system", windows_light())
            if name != CURRENT_THEME:
                apply_theme(self.master, name)
        self.master.after(3000, self._watch_windows_theme)

    def _save(self, **changes):
        # Every config.json write goes through the store (None removes a key; no changes saves edits made
        # in place by migrate_uvx_command, set_prompts and the like). A disk error shows as before.
        try:
            self.store.update(**changes) if changes else self.store.save()
        except OSError as e:
            messagebox.showerror("Save Error", f"Could not save settings:\n{e}")

    def _set_open_browser(self, open_browser):
        self._open_browser = open_browser
        self._save(open_browser=open_browser)


    # ── Two-state view switching ──────────────────────────────

    def _enter_running_state(self):
        # A fresh running view. The process code writes to its logs and labels by these names (ADR 0021).
        self.running = True
        rv = self._switch(RunningView)
        self.llama_log, self.uvx_log, self.proxy_log = rv.llama_log, rv.uvx_log, rv.proxy_log
        self._llama_label_var, self._uvx_label_var = rv.llama_label_var, rv.uvx_label_var
        self._proxy_label_var, self._elapsed_var, self.test_btn = rv.proxy_label_var, rv.elapsed_var, rv.test_btn
        self.master.resizable(True, True)
        self.master.geometry(f"{W}x{H_RUNNING}")

    def stop(self):
        # Exit Processes: stop everything, then a fresh home view.
        self._stop_health_poll()  # first, so the kills below aren't reported as an agents crash
        self._kill_all()
        self._stop_elapsed()
        self.running = False
        self._show_home()


    # ── Model & preset list management ───────────────────────

    def _show_selection(self, view):
        if isinstance(self.view, HomeView):
            self.view.show_selection(view)

    def pick_model(self, name):
        self._show_selection(self.selection.pick_model(name))

    def pick_preset(self, name):
        self._show_selection(self.selection.pick_preset(name))

    def refresh_models(self):
        self._show_selection(self.selection.refresh())


    # ── Preset CRUD ───────────────────────────────────────────

    def open_preset_menu(self):
        # A second click on "⋯" closes the open menu, as the dropdowns do.
        if self._preset_popup and self._preset_popup.winfo_exists():
            self._preset_popup.destroy()
            self._preset_popup = None
            return None
        self._preset_popup = popup_menu(self.view.preset_more, [("+ Add preset", self._add_preset),
                                                            ("Edit", self._edit_preset),
                                                            ("Delete", self._delete_preset, DANGER)], right=True)
        return self._preset_popup

    def _add_preset(self):
        dlg = PresetDialog(self.master, "Add Preset", available_models=self.selection.model_names())
        self.master.wait_window(dlg)
        if dlg.result_name:
            self._change_presets(lambda: self.selection.add_preset(
                dlg.result_name, dlg.result_command, dlg.result_models))

    def _edit_preset(self):
        try:
            name, command, keywords = self.selection.preset_for_edit()
        except PresetError as e:
            return self._refused(e)
        dlg = PresetDialog(self.master, "Edit Preset", name=name, command=command,
                           model_keywords=keywords, available_models=self.selection.model_names())
        self.master.wait_window(dlg)
        if dlg.result_name:
            self._change_presets(lambda: self.selection.edit_preset(
                name, dlg.result_name, dlg.result_command, dlg.result_models))

    def _delete_preset(self):
        name = self.selection.view.preset
        self._change_presets(lambda: self.selection.delete_preset(
            name, confirm=lambda n: messagebox.askyesno("Confirm Delete", f'Delete preset "{n}"?')))

    def _change_presets(self, change):
        try:
            view = change()
        except PresetError as e:
            return self._refused(e)
        if view:  # None: the user said no
            self._show_selection(view)

    @staticmethod
    def _refused(e):
        getattr(messagebox, "show" + e.kind)(e.title, e.message)


    # ── Server health polling ─────────────────────────────────

    def _start_health_poll(self):
        self._polling = True
        self._poll_gen += 1
        gen = self._poll_gen
        current = lambda: self._polling and gen == self._poll_gen

        def on_ui(fn):
            # Run fn on the UI thread unless this poll was stopped or replaced meanwhile (Exit, a quick relaunch).
            self.master.after(0, lambda: current() and fn())

        def poll_loop():
            browser_opened = False
            shown = set()  # the agents server is optional: its crash shows for one poll, then llama's status returns
            # Role names, looked up when the label updates, so a theme switch can't leave stale colours.
            colors = {"ok": "SUCCESS", "warn": "WARN", "error": "DANGER", "idle": "TEXT_MUT"}
            while current():
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8080/health", timeout=2
                    ) as resp:
                        health = "ok" if json.loads(resp.read().decode()).get("status") == "ok" else "loading"
                except urllib.error.HTTPError as e:
                    # llama-server answers 503 while the model is still loading
                    health = "loading" if e.code == 503 else "offline"
                except Exception:
                    health = "offline"
                procs = {"llama-server": self._llama_proc, "UVX proxy": self._uvx_proc,
                         "prompt proxy": self._proxy_proc, "agents server": self._agents_proc}
                codes = {n: p.poll() for n, p in procs.items() if p is not None}
                text, level = health_status(codes, health, shown)
                agents_code = codes.get("agents server")
                if not current():
                    break  # stopped while the request above was in flight
                if agents_code is not None and "agents server" not in shown:
                    shown.add("agents server")
                    note = f"[agents] exited (code {agents_code}); agent jobs and the Jobs panel are off until the next launch"
                    on_ui(lambda n=note: self._append_log(self.uvx_log, n))
                if level == "ok" and not browser_opened and self._open_browser:
                    browser_opened = True
                    on_ui(lambda: webbrowser.open("http://127.0.0.1:8080/"))
                on_ui(lambda c=colors[level], t=text: self._set_health(t, c))
                on_ui(lambda ok=level == "ok": self._set_test_btn(ok))
                time.sleep(3)
        threading.Thread(target=poll_loop, daemon=True).start()

    def _stop_health_poll(self):
        self._polling = False
        self._set_health("waiting", "TEXT_MUT")

    def _set_health(self, text, role):
        if isinstance(self.view, RunningView):
            self.view.set_health(text, role)


    # ── Model test ────────────────────────────────────────────

    def _set_test_btn(self, healthy):
        # Only while llama-server is online and no test is running. _polling drops
        # a poll result that arrives after Exit Processes.
        busy = self._test_dialog is not None and self._test_dialog.busy()
        self.test_btn.config(state=tk.NORMAL if healthy and self._polling and not busy else tk.DISABLED)

    def start_model_test(self):
        if not messagebox.askyesno("Test this model",
                "15 to 60 minutes, depending on the model. Uses the GPU. Web UI chat is paused until it finishes or you stop it."):
            return
        self.test_btn.config(state=tk.DISABLED)
        try:
            write_test_lock(MODEL_TEST_LOCK)
            proc = subprocess.Popen(
                model_test_command(sys.executable),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=ROOT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as e:
            clear_test_lock(MODEL_TEST_LOCK)
            messagebox.showerror("Test this model", f"The test couldn't start:\n{e}")
            return
        self._test_dialog = TestDialog(self.master, proc)


    # ── Elapsed time ──────────────────────────────────────────

    def _start_elapsed(self):
        self._launch_time = time.time()
        self._tick_elapsed()

    def _tick_elapsed(self):
        if self._launch_time is None:
            return
        elapsed = int(time.time() - self._launch_time)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._elapsed_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self._elapsed_after_id = self.master.after(1000, self._tick_elapsed)

    def _stop_elapsed(self):
        self._launch_time = None
        if hasattr(self, "_elapsed_after_id"):
            self.master.after_cancel(self._elapsed_after_id)
        self._elapsed_var.set("00:00:00")


    # ── System tray ───────────────────────────────────────────

    def _setup_tray(self):
        if not _TRAY_AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_from_tray, default=True),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self._tray_icon = pystray.Icon(
            "Llama Launcher", _make_tray_image(), "Llama Launcher", menu
        )
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

    def _show_from_tray(self, icon=None, item=None):
        self.master.after(0, self.master.deiconify)

    def _tray_exit(self, icon=None, item=None):
        self.master.after(0, self._force_exit)

    def _force_exit(self):
        # The window's X and the tray's Exit both stop every server and exit (ADR 0016).
        if hasattr(self, "_llama_proc"):
            self._kill_all()
        # Without stop() the icon outlives the process in the tray until the mouse passes over it.
        # stop() only asks the tray thread to remove it, so wait for that thread (briefly) before exiting.
        if getattr(self, "_tray_icon", None):
            try:
                self._tray_icon.stop()
                self._tray_thread.join(timeout=2)
            except Exception:
                pass  # cosmetic: never keep the window from closing
        self.master.destroy()


    # ── Process management ────────────────────────────────────

    def _free_port(self, cmd, default_port, widget):
        # Free the port this command will use, but only from a leftover of our
        # own (see is_own_process). Anything else aborts the launch.
        m = re.search(r'--port\s+(\d+)', cmd)
        port = int(m.group(1)) if m else default_port
        netstat = subprocess.run("netstat -ano", shell=True, capture_output=True, text=True).stdout
        app_dir = ROOT
        for pid in listening_pids(netstat, port) - {"0"}:
            info = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; $p.Name; $p.CommandLine"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.splitlines()
            name = info[0] if info else "unknown"
            if not is_own_process(name, " ".join(info[1:]), app_dir):
                raise RuntimeError(f"port {port} is in use by {name} (PID {pid}), "
                                   "which the launcher didn't start. Close it and launch again.")
            subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
            self._append_log(widget, f"  Port {port} was in use — freed PID {pid} ({name})")

    def _kill_proc(self, proc, widget=None, prefix=""):
        # Kill a process and its full child tree (/T) — ensures GPU workers are also stopped.
        if proc is None:
            return
        try:
            subprocess.run(f"taskkill /F /T /PID {proc.pid}",
                           shell=True, capture_output=True)
            if widget:
                self._append_log(widget, f"{prefix}─── killed (PID {proc.pid}) ───")
        except Exception as e:
            if widget:
                self._append_log(widget, f"{prefix}─── kill failed: {e} ───")

    def _kill_all(self):
        # A running model test stops first, while the agents server can still cancel its jobs.
        if self._test_dialog is not None:
            try:
                self._test_dialog.stop_now()
            except Exception:
                pass
        self._kill_proc(self._llama_proc, getattr(self, "llama_log", None))
        self._llama_proc = None
        self._kill_proc(self._uvx_proc, getattr(self, "uvx_log", None))
        self._uvx_proc = None
        self._kill_proc(self._proxy_proc, getattr(self, "proxy_log", None))
        self._proxy_proc = None
        self._kill_proc(self._agents_proc, getattr(self, "uvx_log", None), "[agents] ")
        self._agents_proc = None
        # Jobs ran inside the agents server, which is gone now (so it can't race these writes).
        # A failure here (e.g. unreadable sandbox) must never stop the exit.
        try:
            agent_jobs.JobStore(self.agent_policy.jobs_dir).cancel_unfinished()
        except Exception:
            pass
        clear_test_lock(MODEL_TEST_LOCK)  # a leftover from a crash would keep Web UI chat paused

    # ── Log helpers ───────────────────────────────────────────

    def _append_log(self, widget, text):
        # One log line, on the UI thread. A line for a previous run's view (a reader thread that outlived it)
        # is dropped by the view.
        if isinstance(self.view, RunningView):
            self.view.append(widget, text)

    def _stream_output(self, proc, widget, prefix=""):
        # Read the process's stdout line-by-line in a background thread and
        # schedule each line onto the main thread so tkinter stays thread-safe.
        # This thread must keep draining the pipe: if it stopped, the pipe would
        # fill and the child would block on its next write. Output is decoded as
        # UTF-8 with errors="replace" so an odd byte can't kill it. prefix marks
        # lines from a process that shares a pane (the agents server).
        def reader():
            try:
                for line in proc.stdout:
                    line = prefix + line.rstrip("\r\n")
                    widget.after(0, lambda l=line: self._append_log(widget, l))
            except Exception:
                pass
            widget.after(0, lambda: self._append_log(widget, f"{prefix}─── process ended ───"))
        threading.Thread(target=reader, daemon=True).start()


    # ── Launch sequence ───────────────────────────────────────

    def _start_agents_server(self):
        # Optional (ADR 0007): any failure is logged and the launch carries on.
        # A crash after start shows once in the status bar as "agents server exited", then in the log.
        if self._agents_error:
            self._append_log(self.uvx_log, f"[agents] not started: {self._agents_error}")
            return
        port = agent_llm.AGENTS_PORT
        try:
            self._free_port(f"--port {port}", port, self.uvx_log)
            self._agents_proc = subprocess.Popen(
                ["uv", "run", "--script", AGENTS_SERVER, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=ROOT,
                creationflags=subprocess.CREATE_NO_WINDOW,  # no shell, so hide uv's console
            )
        except Exception as e:
            self._agents_proc = None
            self._append_log(self.uvx_log, f"[agents] not started: {e}")
            return
        self._uvx_label_var.set(f"{self._uvx_label_var.get()}  ·  agents PID {self._agents_proc.pid}")
        self._append_log(self.uvx_log, f"[agents] launched on 127.0.0.1:{port} (PID {self._agents_proc.pid})")
        self._stream_output(self._agents_proc, self.uvx_log, "[agents] ")

    def launch(self):
        uvx_cmd = resolve_uvx_command(self.uvx_command.strip(), CONFIG_FILE)
        llama_cmd = self.selection.view.command

        if not llama_cmd:
            messagebox.showerror("No Command",
                "No Llama command available. Please select a model and preset.")
            return
        if not uvx_cmd:
            messagebox.showerror("No Command",
                "UVX command is empty. Open Settings to fill it in.")
            return

        self._enter_running_state()

        model_name = self.selection.view.model
        cwd = self.llama_dir if self.llama_dir else None

        try:
            if remove_stale_agents_entry(self.config):
                self._save()
            self._free_port(llama_cmd, 8081, self.llama_log)
            self._llama_proc = subprocess.Popen(
                llama_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
            )
            self._llama_label_var.set(f"llama-server  ·  {self.selection.view.model}  ·  PID {self._llama_proc.pid}")
            self._append_log(self.llama_log, f"Launched (PID {self._llama_proc.pid})")
            self._stream_output(self._llama_proc, self.llama_log)

            self._free_port(uvx_cmd, 8001, self.uvx_log)
            self._uvx_proc = subprocess.Popen(
                uvx_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
            )
            self._uvx_label_var.set(f"UVX Proxy  ·  PID {self._uvx_proc.pid}")
            self._append_log(self.uvx_log, f"Launched (PID {self._uvx_proc.pid})")
            self._stream_output(self._uvx_proc, self.uvx_log)

            self._start_agents_server()

            self._free_port("--port 8080", 8080, self.proxy_log)
            proxy_script = os.path.join(ROOT, "proxy.py")
            if not os.path.isfile(proxy_script):
                raise FileNotFoundError(f"proxy.py not found at {proxy_script}")
            self._proxy_proc = subprocess.Popen(
                [sys.executable, proxy_script, "8080", "8081"],
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._proxy_label_var.set(f"System Prompt Proxy  ·  PID {self._proxy_proc.pid}")
            self._append_log(self.proxy_log, f"Launched (PID {self._proxy_proc.pid})")
            self._stream_output(self._proxy_proc, self.proxy_log)

            self._start_health_poll()
            self._start_elapsed()


        except Exception as e:
            self._append_log(self.llama_log, f"LAUNCH FAILED: {e}")
            self._kill_all()
            self.stop()  # the home view replaces the logs, so the reason goes in a dialog too
            messagebox.showerror("Launch failed", str(e))
