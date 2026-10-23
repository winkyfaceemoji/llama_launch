"""Dialogs: Preset, Settings (drill-down), Agent folders, Prompts, Update, Test."""
from agents import llm as agent_llm
from agents import paths as agent_paths
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from .paths import MODEL_TEST_LOCK
from .theme import ACCENT, BG, BG_INPUT, BORDER, DANGER, FONT_BTN, FONT_BTN_SM, FONT_LOG, FONT_MONO, FONT_SMALL, HOVER, TEXT_MUT, TEXT_PRI, TEXT_SEC, THEMES, W_SETTINGS
from .config import PROMPT_TABS
from .processes import clear_test_lock, read_test_jobs, test_temp_dirs
from .updates import _http, parse_build
from .widgets import flat_btn, outline_btn, section_label, small_btn, style_toggle
from . import theme
theme.follow(globals())  # colour names follow set_palette (ADR 0020)


class PresetDialog(tk.Toplevel):
    def __init__(self, parent, title, name="", command="", model_keywords=None, available_models=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("720x540")
        self.grab_set()

        self.result_name    = None
        self.result_command = None
        self.result_models  = None
        self._available_models = available_models or []

        section_label(self, "Preset name").pack(fill="x", padx=20, pady=(16, 2))
        self.name_var = tk.StringVar(value=name)
        tk.Entry(self, textvariable=self.name_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(fill="x", padx=20, ipady=6)

        section_label(self, "Applies to models").pack(fill="x", padx=20, pady=(14, 2))
        tk.Label(self, text="Select models this preset applies to — leave none selected to match all",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 4))

        list_frame = tk.Frame(self, bg=BORDER)
        list_frame.pack(fill="x", padx=20)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.models_listbox = tk.Listbox(
            list_frame, selectmode=tk.MULTIPLE,
            bg=BG_INPUT, fg=TEXT_PRI,
            selectbackground=ACCENT, selectforeground=BG,
            font=FONT_MONO, relief="flat", bd=0,
            highlightthickness=0,
            activestyle="none",
            yscrollcommand=scrollbar.set,
            height=min(max(len(self._available_models), 1), 5),
        )
        scrollbar.config(command=self.models_listbox.yview)
        self.models_listbox.pack(side="left", fill="x", expand=True)
        if self._available_models:
            scrollbar.pack(side="right", fill="y")

        for i, model in enumerate(self._available_models):
            self.models_listbox.insert(tk.END, model)
            # Pre-select models that match any existing keyword (migration aid)
            if model_keywords:
                lower = model.lower()
                if any(kw.lower() in lower for kw in model_keywords):
                    self.models_listbox.selection_set(i)

        if not self._available_models:
            self.models_listbox.insert(tk.END, "(no models found — will match all)")
            self.models_listbox.config(fg=TEXT_MUT)

        section_label(self, "Command template: use {model} as the model path placeholder").pack(
            fill="x", padx=20, pady=(14, 2)
        )
        self.cmd_text = tk.Text(
            self, height=6, font=FONT_MONO,
            bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
            relief="flat", wrap="word",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self.cmd_text.pack(padx=20, fill="both", expand=True)
        self.cmd_text.insert("1.0", command)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(padx=20, pady=14, fill="x")
        flat_btn(btn_row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        small_btn(btn_row, "Cancel", self.destroy).pack(side="left")

    def _save(self):
        name = self.name_var.get().strip()
        cmd  = self.cmd_text.get("1.0", "end").strip()
        if not name:
            messagebox.showerror("Error", "Preset name cannot be empty.", parent=self)
            return
        if not cmd:
            messagebox.showerror("Error", "Command template cannot be empty.", parent=self)
            return

        selected = self.models_listbox.curselection()
        self.result_models = [self._available_models[i] for i in selected] if self._available_models else []

        self.result_name    = name
        self.result_command = cmd
        self.destroy()


class SettingsDialog(tk.Toplevel):
    # A drill-down: General, which links to Agents, llama.cpp and Advanced. Every page's changes wait for
    # Save; the theme previews at once and Cancel (or the X, or Esc) restores it. Agent folders and Prompts
    # are editors with their own Save. All pages are built once and swapped, so unsaved values survive.
    def __init__(self, parent, current_path, on_save,
                 uvx_command="", on_save_uvx=None,
                 open_browser=True, on_set_open_browser=None,
                 on_open_folders=None, previous_build=None, on_rollback=None,
                 theme="system", on_set_theme=None, on_preview_theme=None,
                 on_open_prompts=None, prompts_editable=True):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()

        self._on_save        = on_save
        self._on_rollback    = on_rollback
        self._on_save_uvx    = on_save_uvx
        self._on_set_open_browser = on_set_open_browser
        self._on_set_theme   = on_set_theme
        self._on_preview_theme = on_preview_theme
        self._theme_saved    = theme
        # on_rollback is None while the servers run (Windows locks llama-server.exe).
        self._previous_build = parse_build(previous_build or "")
        self._rollback_build = self._previous_build if on_rollback else None

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        self._pages = {
            "general":  self._general_page(body, current_path, open_browser, theme),
            "agents":   self._agents_page(body, on_open_folders, on_open_prompts, prompts_editable),
            "llama":    self._llama_page(body),
            "advanced": self._advanced_page(body, uvx_command),
        }

        # ── Save / Cancel, for every page ─────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(padx=20, pady=(10, 20), fill="x")
        flat_btn(btn_row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        small_btn(btn_row, "Cancel", self._cancel).pack(side="left")
        self._show("general")

    def _show(self, name):
        for page in self._pages.values():
            page.pack_forget()
        self._pages[name].pack(fill="both", expand=True)
        self.update_idletasks()
        self.geometry(f"{W_SETTINGS}x{self.winfo_reqheight()}")

    # ── page building blocks ──────────────────────────────────

    def _page(self, body, title, back=False):
        page = tk.Frame(body, bg=BG)
        if back:
            top = tk.Frame(page, bg=BG)
            top.pack(fill="x", padx=12, pady=(12, 0))
            small_btn(top, "‹ General", lambda: self._show("general")).pack(side="left")
        tk.Label(page, text=title, font=FONT_BTN, fg=TEXT_PRI, bg=BG).pack(
            anchor="w", padx=20, pady=(8 if back else 20, 12))
        return page

    def _row(self, page, label, note=None):
        # A label on the left (its control goes on the right of the returned row), then a muted note under it.
        row = tk.Frame(page, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 4 if note else 14))
        tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
        if note:
            tk.Label(page, text=note, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, wraplength=W_SETTINGS - 40,
                     justify="left").pack(padx=20, anchor="w", pady=(0, 14))
        return row

    def _link(self, page, title, note, target):
        # A whole-row link to a sub-page: title, a muted note, and a chevron.
        row = tk.Frame(page, bg=BG, cursor="hand2")
        row.pack(fill="x", padx=12, pady=1)
        parts = [row,
                 tk.Label(row, text=title, font=FONT_SMALL, fg=TEXT_PRI, bg=BG, cursor="hand2"),
                 tk.Label(row, text=note, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, cursor="hand2"),
                 tk.Label(row, text="›", font=FONT_BTN, fg=TEXT_MUT, bg=BG, cursor="hand2")]
        parts[1].pack(side="left", padx=(8, 0), pady=8)
        parts[2].pack(side="left", padx=(10, 0))
        parts[3].pack(side="right", padx=(0, 8))

        def hover(on):
            for w in parts:
                w.config(bg=HOVER if on else BG)
        for w in parts:
            w.bind("<Button-1>", lambda e: (hover(False), self._show(target)))
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))

    # ── pages ─────────────────────────────────────────────────

    def _general_page(self, body, current_path, open_browser, theme):
        page = self._page(body, "General")

        self._row(page, "llama.cpp folder")
        path_row = tk.Frame(page, bg=BG)
        path_row.pack(fill="x", padx=20, pady=(0, 4))
        path_row.columnconfigure(0, weight=1)
        self._path_var = tk.StringVar(value=current_path)
        tk.Entry(path_row, textvariable=self._path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        outline_btn(path_row, "Browse", self._browse).grid(row=0, column=1, padx=(8, 0))
        tk.Label(page, text="Where your models are; llama-server runs from here.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 14))

        web_row = self._row(page, "Open the Web UI on launch",
                            "Opens the browser when the server comes online.")
        grp = tk.Frame(web_row, bg=BG)
        grp.pack(side="right")
        self._yes_btn_dlg = tk.Button(grp, text="Yes", width=5, font=FONT_BTN_SM, relief="flat", bd=0,
                                      padx=6, pady=5, cursor="hand2",
                                      command=lambda: self._set_open_browser_dlg(True))
        self._yes_btn_dlg.pack(side="left", padx=(0, 3))
        self._no_btn_dlg = tk.Button(grp, text="No", width=5, font=FONT_BTN_SM, relief="flat", bd=0,
                                     padx=6, pady=5, cursor="hand2",
                                     command=lambda: self._set_open_browser_dlg(False))
        self._no_btn_dlg.pack(side="left")
        self._open_browser_dlg = open_browser
        self._apply_open_browser_style()

        theme_row = self._row(page, "Theme")
        grp = tk.Frame(theme_row, bg=BG)
        grp.pack(side="right")
        self._theme_btns = {}
        for i, t in enumerate(THEMES):
            b = tk.Button(grp, text=t.capitalize(), width=6,
                          font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
                          command=lambda t=t: self._set_theme_dlg(t))
            b.pack(side="left", padx=(0, 3) if i < len(THEMES) - 1 else 0)
            self._theme_btns[t] = b
        self._theme_dlg = theme
        self._apply_theme_style()

        tk.Frame(page, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(0, 6))
        self._link(page, "Agents", "Agent folders and prompts", "agents")
        self._link(page, "llama.cpp", "Roll back to an earlier build", "llama")
        self._link(page, "Advanced", "The MCP proxy command", "advanced")
        return page

    def _agents_page(self, body, on_open_folders, on_open_prompts, prompts_editable):
        page = self._page(body, "Agents", back=True)
        if on_open_folders:
            row = self._row(page, "Agent folders", "The folders agents may read and organise.")
            outline_btn(row, "Manage…", lambda: on_open_folders(self)).pack(side="right")
        if on_open_prompts:
            row = self._row(page, "Prompts", "What the chat and each agent job are told. Saved in their own dialog.")
            outline_btn(row, "Edit…" if prompts_editable else "View…",
                        lambda: on_open_prompts(self)).pack(side="right")
        return page

    def _llama_page(self, body):
        page = self._page(body, "llama.cpp", back=True)
        if self._rollback_build:
            row = self._row(page, "Roll back",
                            "Reinstalls the build you had before the last update, then runs the build check.")
            outline_btn(row, f"Roll back to b{self._rollback_build}", self._rollback).pack(side="right")
        else:
            note = ("Roll back is available while the servers are stopped." if self._previous_build
                    else "No earlier build is recorded. After an update, you can roll back here.")
            tk.Label(page, text=note, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, wraplength=W_SETTINGS - 40,
                     justify="left").pack(padx=20, anchor="w", pady=(0, 14))
        return page

    def _advanced_page(self, body, uvx_command):
        page = self._page(body, "Advanced", back=True)
        self._row(page, "UVX command", "Starts the MCP proxy bridge on port 8001.")
        self._uvx_text = tk.Text(
            page, height=5, font=FONT_MONO,
            bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
            relief="flat", wrap="word", padx=8, pady=6,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self._uvx_text.pack(fill="x", padx=20, pady=(0, 6))
        self._uvx_text.insert("1.0", uvx_command)
        return page

    # ── choices, Save and Cancel ──────────────────────────────

    def _set_open_browser_dlg(self, open_browser):
        self._open_browser_dlg = open_browser
        self._apply_open_browser_style()

    def _apply_open_browser_style(self):
        style_toggle(self._yes_btn_dlg, self._open_browser_dlg)
        style_toggle(self._no_btn_dlg, not self._open_browser_dlg)

    def _set_theme_dlg(self, theme):
        self._theme_dlg = theme
        if self._on_preview_theme:
            self._on_preview_theme(theme)  # recolours this dialog too
        self._apply_theme_style()

    def _apply_theme_style(self):
        for t, b in self._theme_btns.items():
            style_toggle(b, t == self._theme_dlg)

    def _rollback(self):
        build = self._rollback_build
        if messagebox.askyesno(
                "Roll back llama.cpp",
                f"Reinstall llama.cpp b{build} through WinGet, then run the build check? "
                "This accepts the package's terms and uses the GPU for about 30 seconds. "
                "Unsaved changes in Settings are discarded.",
                parent=self):
            # Settings closes first (unsaved changes dropped), so the progress dialog takes the grab.
            self._cancel()
            self._on_rollback(build)

    def _browse(self):
        path = filedialog.askdirectory(title="Select llama.cpp folder", parent=self)
        if path:
            self._path_var.set(path)

    def _cancel(self):
        # Drop every unsaved change; a previewed theme goes back to the saved one.
        if self._on_preview_theme:
            self._on_preview_theme(None)
        self.destroy()

    def _save(self):
        path = self._path_var.get().strip()
        if not path:
            self._show("general")
            messagebox.showerror("Required", "Please enter a path.", parent=self)
            return
        if not os.path.isdir(path):
            self._show("general")
            messagebox.showerror("Invalid Path",
                "That folder doesn't exist. Please choose a valid directory.", parent=self)
            return
        self._on_save(path)
        if self._on_save_uvx:
            self._on_save_uvx(self._uvx_text.get("1.0", "end").strip())
        if self._on_set_open_browser:
            self._on_set_open_browser(self._open_browser_dlg)
        if self._on_set_theme and self._theme_dlg != self._theme_saved:
            self._on_set_theme(self._theme_dlg)  # also ends the preview
        elif self._on_preview_theme:
            self._on_preview_theme(None)
        self.destroy()


class FoldersDialog(tk.Toplevel):
    # Folders agents may read (and organise through approved plans).
    def __init__(self, parent, policy, error=None):
        super().__init__(parent)
        self.title("Agent folders")
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x320")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self._parent = parent
        self._policy = policy
        section_label(self, "Folders agents can read").pack(fill="x", padx=20, pady=(20, 6))
        note = f"Agents always write only to {policy.sandbox}. Local drives only."
        if error:
            note += f" The sandbox can't be created: {error}"
        tk.Label(self, text=note,
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG, wraplength=W_SETTINGS - 40,
                 justify="left").pack(padx=20, anchor="w")
        self._list = tk.Listbox(self, font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI, relief="flat",
                                highlightthickness=1, highlightbackground=BORDER, height=8)
        self._list.pack(fill="both", expand=True, padx=20, pady=10)
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 20))
        flat_btn(row, "Add folder…", self._add).pack(side="left", padx=(0, 8))
        small_btn(row, "Remove", self._remove).pack(side="left")
        small_btn(row, "Close", self._close).pack(side="right")
        self._refresh()

    def _close(self):
        # Tk drops the grab when this window goes; hand it back to Settings.
        self.destroy()
        try:
            if self._parent.winfo_exists():
                self._parent.grab_set()
        except tk.TclError:
            pass

    def _refresh(self):
        self._list.delete(0, "end")
        try:
            folders = self._policy.read_folders()
        except (OSError, ValueError) as e:
            messagebox.showerror("Folder list unreadable", str(e), parent=self)
            return
        for f in folders:
            self._list.insert("end", f)

    def _add(self):
        path = filedialog.askdirectory(title="Let agents read this folder", parent=self)
        if not path:
            return
        try:
            self._policy.add_folder(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Not added", str(e), parent=self)
        self._refresh()

    def _remove(self):
        sel = self._list.curselection()
        if sel:
            try:
                self._policy.remove_folder(self._list.get(sel[0]))
            except (OSError, ValueError) as e:
                messagebox.showerror("Not removed", str(e), parent=self)
            self._refresh()


class PromptsDialog(tk.Toplevel):
    # Every prompt the models get. The proxy and agents server read them once at startup, so editing is
    # only allowed while the servers are stopped; while they run, this shows exactly what they use.
    FEEDS = {
        "chat": "Web UI chat, through the proxy on 8080. The proxy puts “Current date: {today}” above it.",
        "research": "Research jobs. “Current date: {today}” is added above it.",
        "plan": "Plan jobs. “Current date: {today}” is added above it.",
        "organize": "Organize jobs. Must use {{folder}} and {{batch}}; write any other brace twice ({{{{ }}}}).",
        "review": "The model's check of an organize plan before you approve it. Must use {{goal}}; "
                  "write any other brace twice ({{{{ }}}}).",
    }

    def __init__(self, parent, texts, shipped, editable, on_save):
        super().__init__(parent)
        self.title("Prompts")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("760x580")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self._parent, self._shipped, self._editable, self._on_save = parent, shipped, editable, on_save
        self._texts, self._saved = dict(texts), dict(texts)
        self._key = None

        tabs = tk.Frame(self, bg=BG)
        tabs.pack(fill="x", padx=20, pady=(20, 8))
        self._tab_btns = {}
        for key, name in PROMPT_TABS:
            b = tk.Button(tabs, text=name, font=FONT_BTN_SM, relief="flat", bd=0, padx=10, pady=5,
                          cursor="hand2", command=lambda k=key: self._select(k))
            b.pack(side="left", padx=(0, 3))
            self._tab_btns[key] = b

        self._feeds = tk.Label(self, font=FONT_SMALL, fg=TEXT_MUT, bg=BG, justify="left", anchor="w")
        self._feeds.pack(fill="x", padx=20)
        self._feeds.bind("<Configure>", lambda e: self._feeds.config(wraplength=e.width))
        self._status = tk.Label(self, font=FONT_SMALL, fg=TEXT_SEC, bg=BG, anchor="w")
        self._status.pack(fill="x", padx=20, pady=(2, 8))

        box = tk.Frame(self, bg=BG)
        box.pack(fill="both", expand=True, padx=20)
        scrollbar = ttk.Scrollbar(box, orient="vertical")
        self._text = tk.Text(box, font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                             relief="flat", wrap="word", padx=8, pady=6, undo=True,
                             highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                             yscrollcommand=scrollbar.set)
        scrollbar.config(command=self._text.yview)
        scrollbar.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        self._text.bind("<<Modified>>", self._on_modified)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=14)
        if editable:
            flat_btn(row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
            small_btn(row, "Reset to default", self._reset).pack(side="left")
        else:
            tk.Label(row, text="The servers are running, so these are read-only. Stop them to edit.",
                     font=FONT_SMALL, fg=TEXT_SEC, bg=BG).pack(side="left")
        small_btn(row, "Close", self._close).pack(side="right")
        self._select("chat")

    def _current(self):
        return self._text.get("1.0", "end-1c")

    def _select(self, key):
        if self._key is not None and self._editable:
            self._texts[self._key] = self._current()
        self._key = key
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", self._texts.get(key, ""))
        self._text.edit_reset()
        if not self._editable:
            self._text.config(state="disabled")
        for k, b in self._tab_btns.items():
            style_toggle(b, k == key)
        self._feeds.config(text=self.FEEDS[key].format(today=time.strftime("%A, %B %d, %Y")))
        self._update_status()

    def _on_modified(self, _event=None):
        self._text.edit_modified(False)
        self._update_status()

    def _update_status(self):
        text = self._current()
        # A blank agent prompt means the shipped one, so it isn't "modified".
        modified = text != self._shipped[self._key] and (self._key == "chat" or text.strip())
        state = "Modified from the default" if modified else "Default"
        self._status.config(text=f"{state}  ·  {len(text):,} characters")

    def _reset(self):
        self._text.delete("1.0", "end")
        self._text.insert("1.0", self._shipped[self._key])

    def _all_texts(self):
        if self._editable:
            self._texts[self._key] = self._current()
        return self._texts

    def _save(self):
        problem = self._on_save(dict(self._all_texts()))
        if problem:
            messagebox.showerror("Not saved", problem, parent=self)
            return
        self._saved = dict(self._texts)
        self._close()

    def _close(self):
        if self._editable and self._all_texts() != self._saved:
            if not messagebox.askyesno("Prompts", "Discard your unsaved changes?", parent=self):
                return
        # Tk drops the grab when this window goes; hand it back to Settings.
        self.destroy()
        try:
            if self._parent.winfo_exists():
                self._parent.grab_set()
        except tk.TclError:
            pass


class UpdateDialog(tk.Toplevel):
    # Progress of a WinGet job (update or roll back). job(report) runs in a
    # background thread and returns (ok, message); its report lines reach the
    # log through after(0, ...). Close stays disabled until the job ends.
    def __init__(self, parent, title, job, on_done=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x360")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self._on_done = on_done
        self._working = True
        section_label(self, title).pack(fill="x", padx=20, pady=(20, 6))
        border = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=20)
        self._log = tk.Text(border, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, height=10,
                            relief="flat", state=tk.DISABLED, wrap="word")
        sb = ttk.Scrollbar(border, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._status = tk.Label(self, text="Working… this can take a few minutes.",
                                font=FONT_SMALL, fg=TEXT_SEC, bg=BG,
                                wraplength=W_SETTINGS - 40, justify="left")
        self._status.pack(padx=20, anchor="w", pady=(10, 0))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(10, 20))
        self._close_btn = small_btn(row, "Close", self._close, state=tk.DISABLED)
        self._close_btn.pack(side="right")
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def report(self, line):
        # Called from the job's thread.
        self.after(0, self._append, line)

    def _append(self, line):
        self._log.config(state=tk.NORMAL)
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.config(state=tk.DISABLED)

    def _work(self, job):
        try:
            ok, message = job(self.report)
        except Exception as e:  # Close must never stay disabled
            ok, message = False, str(e) or type(e).__name__
        self.after(0, self._finish, ok, message)

    def _finish(self, ok, message):
        self._working = False
        self._status.config(text=message, fg=TEXT_PRI)
        self._close_btn.config(state=tk.NORMAL)
        if self._on_done:
            self._on_done(ok)

    def _close(self):
        if not self._working:
            self.destroy()


class TestDialog(tk.Toplevel):
    # Progress of a model test. proc is the harness (eval/model_test.py); a reader
    # thread drains its output into the log through after(0, ...). No grab: Exit
    # Processes stays clickable and stops the test first (stop_now).
    def __init__(self, parent, proc):
        super().__init__(parent)
        self.title("Model test")
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x420")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._proc = proc
        self._phase = "running"  # running / stopping / done
        self._report_path = None
        self._last = ""
        section_label(self, "Model test").pack(fill="x", padx=20, pady=(20, 6))
        border = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=20)
        self._log = tk.Text(border, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, height=10,
                            relief="flat", state=tk.DISABLED, wrap="word")
        sb = ttk.Scrollbar(border, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._status = tk.Label(self, text="Running… 15 to 60 minutes. Web UI chat is paused.",
                                font=FONT_SMALL, fg=TEXT_SEC, bg=BG,
                                wraplength=W_SETTINGS - 40, justify="left")
        self._status.pack(padx=20, anchor="w", pady=(10, 0))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(10, 20))
        self._close_btn = small_btn(row, "Close", self._close, state=tk.DISABLED)
        self._close_btn.pack(side="right")
        self._stop_btn = small_btn(row, "Stop", self._stop, fg=DANGER)
        self._stop_btn.pack(side="right", padx=(0, 8))
        threading.Thread(target=self._read, daemon=True).start()

    def busy(self):
        return self._phase != "done"

    def report(self, line):
        # Called from the reader and stop threads.
        self.after(0, self._append, line)

    def _append(self, line):
        if line.strip():
            self._last = line
        if line.startswith("[report] "):
            self._report_path = line[len("[report] "):].strip()
        self._log.config(state=tk.NORMAL)
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.config(state=tk.DISABLED)

    def _read(self):
        # Keeps draining the pipe until EOF (see the launcher rule), then reports the exit code.
        try:
            for line in self._proc.stdout:
                self.report(line.rstrip("\r\n"))
        except Exception:
            pass
        self.after(0, self._ended, self._proc.wait())

    def _ended(self, code):
        if self._phase != "running":
            return  # Stop killed it; the stop sequence sets the status
        self._phase = "done"
        clear_test_lock(MODEL_TEST_LOCK)
        self._stop_btn.config(state=tk.DISABLED)
        self._close_btn.config(state=tk.NORMAL)
        if code == 0 and self._report_path:
            self._status.config(text="Done. The report is open in the browser.", fg=TEXT_PRI)
            webbrowser.open(pathlib.Path(self._report_path).as_uri())
        else:
            self._status.config(text=f"Failed (exit {code}): {self._last}", fg=DANGER)

    def _stop(self):
        # The question runs its own event loop, so the test may have ended meanwhile.
        if (self._phase == "running"
                and messagebox.askyesno("Stop the model test",
                                        "Stop the test? Nothing is added to the report.", parent=self)
                and self._phase == "running"):
            self._begin_stop()
            threading.Thread(target=self._stop_sequence, daemon=True).start()

    def stop_now(self):
        # Stop Servers: the same sequence without the question, on the UI thread,
        # so it ends before the agents server is killed.
        if self._phase == "running":
            self._begin_stop()
            self._stop_sequence()

    def _begin_stop(self):
        self._phase = "stopping"
        self._stop_btn.config(state=tk.DISABLED)
        self._status.config(text="Stopping…", fg=TEXT_SEC)

    def _stop_sequence(self):
        # In this order: the harness first, so it starts no more jobs; then its jobs (a
        # running one stops before its next model call); then its folders; the lock last.
        try:
            subprocess.run(f"taskkill /F /T /PID {self._proc.pid}", shell=True, capture_output=True)
            try:
                key = agent_llm.read_key(agent_llm.APPROVAL_KEY_FILE)
            except OSError:
                key = ""
            for job_id in read_test_jobs(agent_paths.TEST_JOBS_FILE):
                try:
                    _, body = _http(f"{agent_llm.AGENTS_BASE}/jobs/{job_id}/cancel", 5, b"",
                                    {"Authorization": f"Bearer {key}",
                                     "Host": f"127.0.0.1:{agent_llm.AGENTS_PORT}"})
                    self.report(f"{job_id}: {json.loads(body).get('message')}")
                except Exception as e:
                    self.report(f"{job_id}: not cancelled ({e})")
            for folder in test_temp_dirs():
                shutil.rmtree(folder, ignore_errors=True)
            clear_test_lock(MODEL_TEST_LOCK)
        finally:
            self.after(0, self._stopped)

    def _stopped(self):
        self._phase = "done"
        self._status.config(text="Stopped; nothing was added to the report.", fg=TEXT_PRI)
        self._close_btn.config(state=tk.NORMAL)

    def _close(self):
        # Closing the window while the test runs means Stop.
        if self._phase == "running":
            self._stop()
        elif self._phase == "done":
            self.destroy()
