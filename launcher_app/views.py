"""The window's three views: setup, home and running. Each is built fresh when it's shown and calls the
window's public methods for anything that isn't drawing (the window is the controller)."""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os

from .paths import AGENT_API_URL
from .theme import (ACCENT, BG, BG_INPUT, BG_PANEL, BORDER, DANGER, DANGER_HOVER, FONT_BTN, FONT_BTN_SM, FONT_ICON,
                    FONT_LOG, FONT_MONO, FONT_SMALL, FONT_TITLE, HOVER, H_CONFIG, H_NOTICE, LOG_MAX_LINES, SUCCESS,
                    SUCCESS_HOVER, TEXT_MUT, TEXT_PRI, TEXT_SEC, _palette)
from .widgets import (DropdownButton, LogTab, card, copy_btn, flat_btn, log_line_is_error, section_label, small_btn,
                      style_toggle, trim_log)
from . import theme
theme.follow(globals())  # colour names follow set_palette (ADR 0020)


def _title_row(view, app, with_settings=True):
    # "Llama Launcher" on the window background, with Settings on the right (no header bar, like the Web UI).
    hdr = tk.Frame(view, bg=BG)
    hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
    tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE, fg=TEXT_PRI, bg=BG).pack(side="left")
    if with_settings:
        small_btn(hdr, "Settings", app.open_settings).pack(side="right")


class SetupView(tk.Frame):
    # First run: asks for the llama.cpp folder, then hands it to app.finish_setup(path).
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self._app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        hdr = tk.Frame(self, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg=BG).pack(side="left", padx=32, pady=(20, 0))

        body = tk.Frame(self, bg=BG)
        body.grid(row=1, column=0, sticky="nsew", padx=32, pady=20)
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="Where is your llama.cpp folder?",
                 font=FONT_BTN, fg=TEXT_PRI, bg=BG).grid(row=0, column=0, sticky="w", pady=(0, 4))
        tk.Label(body, text="This folder is used as the working directory when launching servers.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).grid(row=1, column=0, sticky="w", pady=(0, 14))

        path_row = tk.Frame(body, bg=BG)
        path_row.grid(row=2, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)
        self._path_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self._path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        small_btn(path_row, "Browse", self._browse).grid(row=0, column=1, padx=(8, 0))
        flat_btn(body, "Confirm", self._confirm, color=ACCENT, fg=BG).grid(row=3, column=0, sticky="ew", pady=(16, 0))

    def _browse(self):
        path = filedialog.askdirectory(title="Select llama.cpp folder")
        if path:
            self._path_var.set(path)

    def _confirm(self):
        path = self._path_var.get().strip()
        if not path:
            messagebox.showerror("Required", "Please enter the path to your llama.cpp folder.")
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path", "That folder doesn't exist. Please choose a valid directory.")
            return
        self._app.finish_setup(path)


class _NoticeView(tk.Frame):
    # The llama.cpp update notice under the title, shown on home and running. Windows locks a running
    # llama-server.exe, so the running view shows it with Update disabled.
    def _build_notice(self, app, can_update):
        self._notice, notice_card = card(self)
        self._notice_label = tk.Label(notice_card, font=FONT_SMALL, fg=TEXT_PRI, bg=BG_PANEL,
                                      wraplength=320, justify="left")
        self._notice_label.pack(side="left", padx=12, pady=8)
        update_btn = small_btn(notice_card, "Update", app.start_update, color=ACCENT, fg=BG)
        update_btn.pack(side="right", padx=12, pady=8)
        if not can_update:
            update_btn.config(state=tk.DISABLED)

    def show_update(self, builds):
        # builds: (installed, newest), or None to hide the notice.
        if builds:
            installed, newest = builds
            self._notice_label.config(text=f"llama.cpp b{newest} is out (you have b{installed}). "
                                           "Updating accepts the package's terms.")
            self._notice.grid(row=1, column=0, padx=24, pady=(16, 0), sticky="ew")
        else:
            self._notice.grid_remove()


class HomeView(_NoticeView):
    # Title, the llama.cpp update notice (when a newer build exists), Model and Preset, Launch.
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self._app = app
        self.grid_columnconfigure(0, weight=1)
        _title_row(self, app)
        self._build_notice(app, can_update=True)

        model_outer, model_card = card(self)
        model_outer.grid(row=2, column=0, padx=24, pady=(16, 8), sticky="ew")
        model_card.grid_columnconfigure(1, weight=1)
        section_label(model_card, "Model").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.model_var = tk.StringVar()
        self.model_menu = DropdownButton(model_card, self.model_var)
        self.model_menu.grid(row=0, column=1, sticky="ew", pady=10)
        small_btn(model_card, "Refresh", app.refresh_models).grid(row=0, column=2, padx=(6, 12), pady=10)

        preset_outer, preset_card = card(self)
        preset_outer.grid(row=3, column=0, padx=24, pady=(0, 8), sticky="ew")
        preset_card.grid_columnconfigure(1, weight=1)
        section_label(preset_card, "Preset").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.preset_var = tk.StringVar()
        self.preset_menu = DropdownButton(preset_card, self.preset_var)
        self.preset_menu.grid(row=0, column=1, sticky="ew", pady=10)
        # + Add, Edit and Delete live behind one "⋯" button, so the row holds just the choice.
        self.preset_more = small_btn(preset_card, "⋯", app.open_preset_menu, width=3)
        self.preset_more.config(font=FONT_ICON, pady=0)
        self.preset_more.grid(row=0, column=2, padx=(6, 12), pady=10)

        tk.Button(self, text="Launch Server", command=app.launch,
                  bg=SUCCESS, fg=BG, activebackground=SUCCESS_HOVER, activeforeground=BG,
                  font=("Segoe UI", 11, "bold"), relief="flat", bd=0, pady=12, cursor="hand2",
                  ).grid(row=4, column=0, sticky="ew", padx=24, pady=(8, 24))

    def height(self):
        return H_CONFIG + (H_NOTICE if self._notice.winfo_manager() else 0)

    def show_selection(self, view):
        # Fill both dropdowns from the selection's view; picking an item goes back through the app.
        for dropdown, var, items, shown, pick in (
                (self.model_menu, self.model_var, view.models, view.model, self._app.pick_model),
                (self.preset_menu, self.preset_var, view.presets, view.preset, self._app.pick_preset)):
            dropdown.menu.delete(0, "end")
            for item in items:
                dropdown.menu.add_command(label=item, command=lambda i=item, pick=pick: pick(i))
            var.set(shown)


class RunningView(_NoticeView):
    # Built fresh at every launch, so nothing from the last run carries over: the status row, the closed
    # Connection row, empty log tabs without dots, Test disabled until the health poll says online.
    # Auto-scroll is the window's, so unticking it lasts across launches.
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1)
        _title_row(self, app)
        self._build_notice(app, can_update=False)

        status_outer, status = card(self)
        status_outer.grid(row=5, column=0, sticky="ew", padx=24, pady=(16, 0))
        self._health_dot = tk.Label(status, text="●", font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL)
        self._health_dot.pack(side="left", padx=(12, 4), pady=8)
        self._health_label = tk.Label(status, text="waiting", font=FONT_SMALL, fg=TEXT_SEC, bg=BG_PANEL)
        self._health_label.pack(side="left", pady=8)
        tk.Label(status, text="uptime", font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL).pack(side="left", padx=(20, 6), pady=8)
        self.elapsed_var = tk.StringVar(value="00:00:00")
        tk.Label(status, textvariable=self.elapsed_var, font=FONT_MONO, fg=TEXT_SEC, bg=BG_PANEL).pack(side="left", pady=8)
        self.autoscroll_var = app.autoscroll_var
        tk.Checkbutton(status, text="Auto-scroll", variable=self.autoscroll_var,
                       font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL,
                       activebackground=BG_PANEL, activeforeground=TEXT_SEC,
                       selectcolor=BG_INPUT, cursor="hand2").pack(side="right", padx=12, pady=8)

        # The direct API address for agents (bypasses the proxy, so no system prompt or date injection) and
        # the two keys, each with a Copy button, behind a row that starts closed.
        api_bar = tk.Frame(self, bg=BG)
        api_bar.grid(row=6, column=0, sticky="ew", padx=24, pady=(8, 0))
        self._conn_btn = small_btn(api_bar, "Connection  ▸", self.toggle_connection)
        self._conn_btn.config(padx=4)
        self._conn_btn.pack(anchor="w")
        self._conn_body = tk.Frame(api_bar, bg=BG)  # packed only while open
        api_row = tk.Frame(self._conn_body, bg=BG)
        api_row.pack(fill="x")
        tk.Label(api_row, text="Agent API", font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(side="left")
        tk.Label(api_row, text=AGENT_API_URL, font=FONT_MONO, fg=TEXT_SEC, bg=BG).pack(side="left", padx=(8, 0))
        copy_btn(api_row, lambda: AGENT_API_URL).pack(side="left", padx=(4, 0))
        # The keys themselves are never shown, only copied. A second row: three pairs don't fit 480 px.
        key_row = tk.Frame(self._conn_body, bg=BG)
        key_row.pack(fill="x", pady=(2, 0))
        tk.Label(key_row, text="API key", font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(side="left")
        copy_btn(key_row, lambda: app.api_key).pack(side="left", padx=(4, 0))
        tk.Label(key_row, text="Approval key (Jobs panel)", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left", padx=(16, 0))
        copy_btn(key_row, lambda: app.approval_key).pack(side="left", padx=(4, 0))

        # One log area with a tab per process, so each log gets the full height.
        self.llama_label_var = tk.StringVar(value="llama-server")
        self.uvx_label_var = tk.StringVar(value="UVX Proxy")
        self.proxy_label_var = tk.StringVar(value="System Prompt Proxy")
        log_pane = tk.Frame(self, bg=BG)
        log_pane.grid(row=7, column=0, sticky="nsew", padx=24, pady=(8, 8))
        tab_row = tk.Frame(log_pane, bg=BG)
        tab_row.pack(fill="x")
        # The shown log's full label (name, model, PID) under the tabs.
        self._log_header = tk.Label(log_pane, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, anchor="w")
        self._log_header.pack(fill="x", pady=(6, 2))
        body = tk.Frame(log_pane, bg=BORDER, padx=1, pady=1)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self._log_tabs = {}  # Text widget -> LogTab
        for name, label_var in (("llama-server", self.llama_label_var), ("UVX Proxy", self.uvx_label_var),
                                ("Prompt Proxy", self.proxy_label_var)):
            frame = tk.Frame(body, bg=BG_INPUT)
            frame.grid(row=0, column=0, sticky="nsew")
            txt = tk.Text(frame, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                          relief="flat", state=tk.DISABLED, wrap="none")
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            tab = tk.Frame(tab_row, bg=BG)
            tab.pack(side="left", padx=(0, 3))
            btn = tk.Button(tab, text=name, font=FONT_BTN_SM, relief="flat", bd=0, padx=10, pady=4,
                            cursor="hand2", command=lambda t=txt: self.show_log(t))
            btn.pack(side="left")
            dot = tk.Label(tab, text="●", font=FONT_SMALL, fg=DANGER, bg=BG)  # packed only after an error line
            self._log_tabs[txt] = LogTab(frame, btn, dot, label_var)
        self.llama_log, self.uvx_log, self.proxy_log = self._log_tabs  # in the order built above
        self.show_log(self.llama_log)

        # Test this model + Exit. Test is enabled only while the health poll says online.
        exit_row = tk.Frame(self, bg=BG)
        exit_row.grid(row=8, column=0, sticky="ew", padx=24, pady=(0, 24))
        # Outline button: a Tk button on Windows draws no border of its own, so a 1px frame does.
        test_border = tk.Frame(exit_row, bg=BORDER, padx=1, pady=1)
        test_border.pack(side="left", padx=(0, 8))
        self.test_btn = tk.Button(test_border, text="Test this model", command=app.start_model_test,
                                  state=tk.DISABLED, bg=BG, fg=TEXT_PRI, disabledforeground=TEXT_MUT,
                                  activebackground=HOVER, activeforeground=TEXT_PRI,
                                  font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
                                  padx=14, pady=11, cursor="hand2")
        self.test_btn.pack()
        tk.Button(exit_row, text="Exit Processes", command=app.stop,
                  bg=DANGER, fg=BG, activebackground=DANGER_HOVER, activeforeground=BG,
                  font=("Segoe UI", 11, "bold"), relief="flat", bd=0, pady=12, cursor="hand2",
                  ).pack(side="left", fill="x", expand=True)

    def toggle_connection(self, show=None):
        # The Agent API address and the keys, each with Copy: one click away, out of the logs' way.
        show = not self._conn_body.winfo_manager() if show is None else show
        if show:
            self._conn_body.pack(fill="x", pady=(2, 0))
        else:
            self._conn_body.pack_forget()
        self._conn_btn.config(text="Connection  ▾" if show else "Connection  ▸")

    def show_log(self, widget):
        # Bring one log's tab to the front and clear its error dot.
        self._shown_log = widget
        for txt, tab in self._log_tabs.items():
            style_toggle(tab.button, txt is widget)
            if txt is widget:
                tab.frame.tkraise()
                tab.dot.pack_forget()
                self._log_header.config(textvariable=tab.label_var)

    def append(self, widget, text):
        # One line into a log tab. A line for another view's tab (a reader thread outliving its run) is dropped.
        if widget not in self._log_tabs:
            return
        widget.config(state=tk.NORMAL)
        widget.insert("end", text + "\n")
        trim_log(widget, LOG_MAX_LINES)
        if widget is not self._shown_log and log_line_is_error(text):
            self._log_tabs[widget].dot.pack(side="left", padx=(0, 4))
        if self.autoscroll_var.get():
            widget.see("end")
        widget.config(state=tk.DISABLED)

    def set_health(self, text, role):
        # health_status's text starts with its own dot; the dot here is a separate label in the level's colour.
        self._health_dot.config(fg=_palette[role])
        self._health_label.config(text=text.removeprefix("● "))
