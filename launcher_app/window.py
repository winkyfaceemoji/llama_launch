"""The launcher window: setup, home and running views, and process control (ADR 0021)."""
from agents import jobs as agent_jobs
from agents import llm as agent_llm
from agents import paths as agent_paths
from agents import prompts as agent_prompts
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
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
from .paths import AGENTS_SERVER, AGENT_API_URL, API_KEY_FILE, CONFIG_FILE, ICON_FILE, MODEL_TEST_LOCK, ROOT
from .theme import ACCENT, BG, BG_INPUT, BG_PANEL, BORDER, CURRENT_THEME, DANGER, DANGER_HOVER, FONT_BTN, FONT_BTN_SM, FONT_ICON, FONT_LOG, FONT_MONO, FONT_SMALL, FONT_TITLE, HOVER, H_CONFIG, H_NOTICE, H_RUNNING, H_SETUP, LOG_MAX_LINES, SUCCESS, SUCCESS_HOVER, TEXT_MUT, TEXT_PRI, TEXT_SEC, W, _palette, apply_theme, apply_ttk_style, resolve_theme, set_dark_title_bar, set_palette, theme_setting, windows_light
from .selection import PresetError, Selection
from .config import ConfigStore, UVX_COMMAND, backup_config, load_api_key, migrate_uvx_command, remove_stale_agents_entry, resolve_uvx_command, set_prompts, shipped_prompts
from .processes import clear_test_lock, health_status, is_own_process, listening_pids, model_test_command, write_test_lock
from .updates import UpdateRunner
from .widgets import DropdownButton, LogTab, _TRAY_AVAILABLE, _make_tray_image, card, copy_btn, flat_btn, log_line_is_error, popup_menu, pystray, section_label, small_btn, style_toggle, trim_log
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

        # Route to setup or home based on whether a path is already configured
        if not self.llama_dir:
            self._build_setup_screen()
        else:
            self._build_home_screen()


    # ── Setup screen ─────────────────────────────────────────
    # Shown on first launch. Requires the user to provide the
    # llama.cpp folder path before proceeding to the home screen.

    def _build_setup_screen(self):
        self.master.geometry(f"{W}x{H_SETUP}")
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_rowconfigure(1, weight=1)

        # Title row, on the window background (no header bar, like the Web UI)
        hdr = tk.Frame(self.master, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg=BG).pack(side="left", padx=32, pady=(20, 0))

        # Body — holds all setup content in a single frame for easy cleanup
        self._setup_frame = tk.Frame(self.master, bg=BG)
        self._setup_frame.grid(row=1, column=0, sticky="nsew", padx=32, pady=20)
        self._setup_frame.columnconfigure(0, weight=1)

        tk.Label(self._setup_frame,
                 text="Where is your llama.cpp folder?",
                 font=FONT_BTN, fg=TEXT_PRI, bg=BG).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        tk.Label(self._setup_frame,
                 text="This folder is used as the working directory when launching servers.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).grid(
            row=1, column=0, sticky="w", pady=(0, 14))

        # Path entry row — text field + browse button side by side
        path_row = tk.Frame(self._setup_frame, bg=BG)
        path_row.grid(row=2, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)

        self._setup_path_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self._setup_path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        small_btn(path_row, "Browse", self._browse_setup).grid(
            row=0, column=1, padx=(8, 0))

        flat_btn(self._setup_frame, "Confirm", self._confirm_setup,
                 color=ACCENT, fg=BG).grid(row=3, column=0, sticky="ew", pady=(16, 0))

    def _browse_setup(self):
        # Open a folder picker and put the chosen path into the setup entry field.
        path = filedialog.askdirectory(title="Select llama.cpp folder")
        if path:
            self._setup_path_var.set(path)

    def _confirm_setup(self):
        # Validate the entered path, save it to config.json, and transition to home.
        path = self._setup_path_var.get().strip()
        if not path:
            messagebox.showerror("Required",
                "Please enter the path to your llama.cpp folder.")
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path",
                "That folder doesn't exist. Please choose a valid directory.")
            return

        self.llama_dir = path
        self._save(llama_dir=path)

        # Clear the setup screen and build the home screen in its place
        for widget in self.master.winfo_children():
            widget.destroy()
        self._build_home_screen()


    # ── Home screen ──────────────────────────────────────────
    # The main launcher UI — model/preset selection, UVX command,
    # launch button, and running state (log + exit button).

    def _build_home_screen(self):
        self.master.geometry(f"{W}x{H_CONFIG}")
        self.master.grid_columnconfigure(0, weight=1)

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

        self.master.columnconfigure(0, weight=1)

        # ── Row 0: Title row with Settings on the right, on the window background
        hdr = tk.Frame(self.master, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg=BG).pack(side="left")
        small_btn(hdr, "Settings", self._open_settings).pack(side="right")

        # ── Row 1: llama.cpp update notice (hidden until the startup check finds a newer build)
        self._notice, notice_card = card(self.master)
        self._notice_label = tk.Label(notice_card, font=FONT_SMALL, fg=TEXT_PRI, bg=BG_PANEL,
                                      wraplength=320, justify="left")
        self._notice_label.pack(side="left", padx=12, pady=8)
        self._update_btn = small_btn(notice_card, "Update", self._start_update, color=ACCENT, fg=BG)
        self._update_btn.pack(side="right", padx=12, pady=8)

        # ── Row 2: Model selector
        self.model_outer, model_card = card(self.master)
        self.model_outer.grid(row=2, column=0, padx=24, pady=(16, 8), sticky="ew")
        model_card.grid_columnconfigure(1, weight=1)

        section_label(model_card, "Model").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.model_var  = tk.StringVar()
        self.model_menu = DropdownButton(model_card, self.model_var)
        self.model_menu.grid(row=0, column=1, sticky="ew", pady=10)
        small_btn(model_card, "Refresh", self._refresh_models).grid(row=0, column=2, padx=(6, 12), pady=10)


        # ── Row 3: Preset selector
        self.preset_outer, preset_card = card(self.master)
        self.preset_outer.grid(row=3, column=0, padx=24, pady=(0, 8), sticky="ew")
        preset_card.grid_columnconfigure(1, weight=1)

        section_label(preset_card, "Preset").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.preset_var  = tk.StringVar()
        self.preset_menu = DropdownButton(preset_card, self.preset_var)
        self.preset_menu.grid(row=0, column=1, sticky="ew", pady=10)

        # + Add, Edit and Delete live behind one "⋯" button, so the row holds just the choice.
        self._preset_popup = None
        self._preset_more = small_btn(preset_card, "⋯", self._open_preset_menu, width=3)
        self._preset_more.config(font=FONT_ICON, pady=0)
        self._preset_more.grid(row=0, column=2, padx=(6, 12), pady=10)

        # ── Row 4: Launch Server button
        self.run_button = tk.Button(
            self.master, text="Launch Server",
            command=self.run_commands,
            bg=SUCCESS, fg=BG,
            activebackground=SUCCESS_HOVER, activeforeground=BG,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            pady=12, cursor="hand2",
        )
        self.run_button.grid(row=4, column=0,
                             sticky="ew", padx=24, pady=(8, 24))
        # Enter launches from the home view; Ctrl+, opens Settings. Bound on the root's own tag, so they never
        # fire from a dialog (each dialog is its own toplevel with its own tags).
        self.master.bind("<Return>", self._on_return)
        self.master.bind("<Control-comma>", lambda e: self._open_settings())

        # ── Row 5: Status row in a hairline box (hidden until running state)
        self.status_bar, status = card(self.master)

        # Left: health, a coloured dot and its text
        self._health_dot = tk.Label(status, text="●", font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL)
        self._health_dot.pack(side="left", padx=(12, 4), pady=8)
        self._health_label = tk.Label(status, text="waiting", font=FONT_SMALL, fg=TEXT_SEC, bg=BG_PANEL)
        self._health_label.pack(side="left", pady=8)

        # Middle: uptime label + elapsed counter
        tk.Label(status, text="uptime",
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL).pack(side="left", padx=(20, 6), pady=8)
        self._elapsed_var = tk.StringVar(value="00:00:00")
        tk.Label(status, textvariable=self._elapsed_var,
            font=FONT_MONO, fg=TEXT_SEC, bg=BG_PANEL).pack(side="left", pady=8)

        # Right: auto-scroll checkbox
        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(status, text="Auto-scroll",
            variable=self._autoscroll_var,
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL,
            activebackground=BG_PANEL, activeforeground=TEXT_SEC,
            selectcolor=BG_INPUT, cursor="hand2").pack(side="right", padx=12, pady=8)

        # Below the status row: the direct API address for agents (bypasses the
        # proxy, so no system prompt or date injection), and the two keys, each with a Copy button.
        self.api_bar = tk.Frame(self.master, bg=BG)
        self._conn_btn = small_btn(self.api_bar, "Connection  ▸", self._toggle_connection)
        self._conn_btn.config(padx=4)
        self._conn_btn.pack(anchor="w")
        self._conn_body = tk.Frame(self.api_bar, bg=BG)  # packed only while open
        api_row = tk.Frame(self._conn_body, bg=BG)
        api_row.pack(fill="x")
        tk.Label(api_row, text="Agent API", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left")
        tk.Label(api_row, text=AGENT_API_URL, font=FONT_MONO,
                 fg=TEXT_SEC, bg=BG).pack(side="left", padx=(8, 0))
        copy_btn(api_row, lambda: AGENT_API_URL).pack(side="left", padx=(4, 0))
        # The keys themselves are never shown, only copied. A second row: three pairs don't fit 480 px.
        key_row = tk.Frame(self._conn_body, bg=BG)
        key_row.pack(fill="x", pady=(2, 0))
        tk.Label(key_row, text="API key", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left")
        copy_btn(key_row, lambda: self.api_key).pack(side="left", padx=(4, 0))
        tk.Label(key_row, text="Approval key (Jobs panel)", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left", padx=(16, 0))
        copy_btn(key_row, lambda: self.approval_key).pack(side="left", padx=(4, 0))

        # ── Rows 7–8: Running state (hidden until Launch is pressed)
        # One log area with a tab per process, so each log gets the full height.

        self._llama_label_var  = tk.StringVar(value="llama-server")
        self._uvx_label_var    = tk.StringVar(value="UVX Proxy")
        self._proxy_label_var  = tk.StringVar(value="System Prompt Proxy")

        self.log_pane = tk.Frame(self.master, bg=BG)
        tab_row = tk.Frame(self.log_pane, bg=BG)
        tab_row.pack(fill="x")
        # The shown log's full label (name, model, PID) under the tabs.
        self._log_header = tk.Label(self.log_pane, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, anchor="w")
        self._log_header.pack(fill="x", pady=(6, 2))
        body = tk.Frame(self.log_pane, bg=BORDER, padx=1, pady=1)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self._log_tabs = {}   # Text widget -> LogTab
        for name, label_var in (("llama-server", self._llama_label_var), ("UVX Proxy", self._uvx_label_var),
                                ("Prompt Proxy", self._proxy_label_var)):
            frame = tk.Frame(body, bg=BG_INPUT)
            frame.grid(row=0, column=0, sticky="nsew")
            txt = tk.Text(frame, font=FONT_LOG,
                          bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                          relief="flat", state=tk.DISABLED, wrap="none")
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            tab = tk.Frame(tab_row, bg=BG)
            tab.pack(side="left", padx=(0, 3))
            btn = tk.Button(tab, text=name, font=FONT_BTN_SM, relief="flat", bd=0, padx=10, pady=4,
                            cursor="hand2", command=lambda t=txt: self._show_log(t))
            btn.pack(side="left")
            dot = tk.Label(tab, text="●", font=FONT_SMALL, fg=DANGER, bg=BG)  # packed only after an error line
            self._log_tabs[txt] = LogTab(frame, btn, dot, label_var)
        self.llama_log, self.uvx_log, self.proxy_log = self._log_tabs  # in the order built above
        self._show_log(self.llama_log)

        # ── Test this model + Exit buttons. Test is enabled only while the health poll says online.
        self.exit_row = tk.Frame(self.master, bg=BG)
        # Outline button: a Tk button on Windows draws no border of its own, so a 1px frame does.
        test_border = tk.Frame(self.exit_row, bg=BORDER, padx=1, pady=1)
        test_border.pack(side="left", padx=(0, 8))
        self.test_btn = tk.Button(
            test_border, text="Test this model",
            command=self._start_model_test, state=tk.DISABLED,
            bg=BG, fg=TEXT_PRI, disabledforeground=TEXT_MUT,
            activebackground=HOVER, activeforeground=TEXT_PRI,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            padx=14, pady=11, cursor="hand2",
        )
        self.test_btn.pack()
        self.exit_btn = tk.Button(
            self.exit_row, text="Exit Processes",
            command=self._exit_running_state,
            bg=DANGER, fg=BG,
            activebackground=DANGER_HOVER, activeforeground=BG,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            pady=12, cursor="hand2",
        )

        # When the model changes, re-filter the preset list.
        # When the preset changes, rebuild the launch command.

        self._show_selection(self.selection.view)

        # Set up system tray icon if available
        self._setup_tray()

        threading.Thread(target=self._check_for_update, daemon=True).start()


    # ── Settings ─────────────────────────────────────────────

    def _open_settings(self):
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
        running = bool(self.exit_btn.winfo_manager())

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
        self._update_builds = (installed, newest)
        self._notice_label.config(text=f"llama.cpp b{newest} is out (you have b{installed}). "
                                       "Updating accepts the package's terms.")
        self._notice.grid(row=1, column=0, padx=24, pady=(16, 0), sticky="ew")
        # Windows locks a running llama-server.exe, so no updates while running.
        running = bool(self.exit_btn.winfo_manager())
        self._update_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        if not running:
            self._resize_config()

    def _start_update(self):
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
            self._notice.grid_remove()
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
        extra = H_NOTICE if self._notice.winfo_manager() else 0
        self.master.geometry(f"{W}x{H_CONFIG + extra}")

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
        if self.run_button.winfo_manager():
            self.run_commands()

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
        # Hide config widgets and show the status bar, two log panes + exit button.
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid_remove()
        self._update_btn.config(state=tk.DISABLED)
        self.status_bar.grid(row=5, column=0,
                             sticky="ew", padx=24, pady=(16, 0))
        self._toggle_connection(False)  # closed on every launch
        self.api_bar.grid(row=6, column=0, sticky="ew", padx=24, pady=(8, 0))
        self.master.grid_rowconfigure(7, weight=1)
        self.log_pane.grid(row=7, column=0,
                           sticky="nsew", padx=24, pady=(8, 8))
        self.exit_row.grid(row=8, column=0,
                           sticky="ew", padx=24, pady=(0, 24))
        # winfo_manager() of exit_btn tells the running state apart (Settings, update notice).
        self.exit_btn.pack(side="left", fill="x", expand=True)
        self.test_btn.config(state=tk.DISABLED)
        self.master.resizable(True, True)
        self.master.geometry(f"{W}x{H_RUNNING}")

    def _exit_running_state(self):
        # Kill both processes, then restore the config view.
        self._stop_health_poll()  # first, so the kills below aren't reported as an agents crash
        self._kill_all()
        self._stop_elapsed()
        self.master.resizable(False, False)
        self.master.grid_rowconfigure(7, weight=0)
        self.status_bar.grid_remove()
        self.api_bar.grid_remove()
        self.log_pane.grid_remove()
        self.exit_btn.pack_forget()
        self.exit_row.grid_remove()
        self.test_btn.config(state=tk.DISABLED)
        # Reset pane labels for the next launch
        self._llama_label_var.set("llama-server")
        self._uvx_label_var.set("UVX Proxy")
        self._proxy_label_var.set("System Prompt Proxy")
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid()
        self._update_btn.config(state=tk.NORMAL)
        self._resize_config()


    # ── Model & preset list management ───────────────────────

    def _show_selection(self, view):
        # Fill both dropdowns from the selection's view; picking an item goes back through the selection.
        for dropdown, var, items, shown, pick in (
                (self.model_menu, self.model_var, view.models, view.model, self._pick_model),
                (self.preset_menu, self.preset_var, view.presets, view.preset, self._pick_preset)):
            dropdown.menu.delete(0, "end")
            for item in items:
                dropdown.menu.add_command(label=item, command=lambda i=item, pick=pick: pick(i))
            var.set(shown)

    def _pick_model(self, name):
        self._show_selection(self.selection.pick_model(name))

    def _pick_preset(self, name):
        self._show_selection(self.selection.pick_preset(name))

    def _refresh_models(self):
        self._show_selection(self.selection.refresh())


    # ── Preset CRUD ───────────────────────────────────────────

    def _open_preset_menu(self):
        # A second click on "⋯" closes the open menu, as the dropdowns do.
        if self._preset_popup and self._preset_popup.winfo_exists():
            self._preset_popup.destroy()
            self._preset_popup = None
            return None
        self._preset_popup = popup_menu(self._preset_more, [("+ Add preset", self._add_preset),
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
        # health_status's text starts with its own dot; the dot here is a separate label in the level's colour.
        self._health_dot.config(fg=_palette[role])
        self._health_label.config(text=text.removeprefix("● "))


    # ── Model test ────────────────────────────────────────────

    def _set_test_btn(self, healthy):
        # Only while llama-server is online and no test is running. _polling drops
        # a poll result that arrives after Exit Processes.
        busy = self._test_dialog is not None and self._test_dialog.busy()
        self.test_btn.config(state=tk.NORMAL if healthy and self._polling and not busy else tk.DISABLED)

    def _start_model_test(self):
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

    def _toggle_connection(self, show=None):
        # The Agent API address and the keys, each with Copy: one click away, out of the logs' way.
        show = not self._conn_body.winfo_manager() if show is None else show
        if show:
            self._conn_body.pack(fill="x", pady=(2, 0))
        else:
            self._conn_body.pack_forget()
        self._conn_btn.config(text="Connection  ▾" if show else "Connection  ▸")

    def _show_log(self, widget):
        # Bring one log's tab to the front and clear its error dot.
        self._shown_log = widget
        for txt, tab in self._log_tabs.items():
            style_toggle(tab.button, txt is widget)
            if txt is widget:
                tab.frame.tkraise()
                tab.dot.pack_forget()
                self._log_header.config(textvariable=tab.label_var)

    def _append_log(self, widget, text):
        # Append one line to a log pane. Safe to call from any thread via .after().
        widget.config(state=tk.NORMAL)
        widget.insert("end", text + "\n")
        trim_log(widget, LOG_MAX_LINES)
        if widget is not self._shown_log and log_line_is_error(text):
            self._log_tabs[widget].dot.pack(side="left", padx=(0, 4))
        if self._autoscroll_var.get():
            widget.see("end")
        widget.config(state=tk.DISABLED)

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

    def run_commands(self):
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

        # Clear all logs before entering the running state, and start on llama-server's
        for w in (self.llama_log, self.uvx_log, self.proxy_log):
            w.config(state=tk.NORMAL)
            w.delete("1.0", "end")
            w.config(state=tk.DISABLED)
            self._log_tabs[w].dot.pack_forget()
        self._show_log(self.llama_log)
        self._enter_running_state()

        model_name = self.model_var.get()
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
            self._llama_label_var.set(f"llama-server  ·  {self.model_var.get()}  ·  PID {self._llama_proc.pid}")
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
            self._exit_running_state()  # hides the log panes, so the reason goes in a dialog too
            messagebox.showerror("Launch failed", str(e))
