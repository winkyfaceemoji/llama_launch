# ============================================================
# launcher.py  —  Llama Server Launcher
# ============================================================
#
# WHAT THIS DOES
# --------------
# This is a desktop GUI that lets you launch two servers with one click:
#
#   1. llama-server  — runs a local AI model (a .gguf file) and exposes it
#                      as an API on port 8080. The model and launch options
#                      are controlled by the "preset" you select.
#
#   2. UVX MCP Proxy — a middleware server on port 8001 that bridges the
#                      AI model's API to tools that speak the MCP protocol
#                      (e.g. Claude Desktop, Cursor).
#
# HOW IT'S STRUCTURED
# --------------------
# The file is divided into four layers:
#
#   1. CONFIGURATION  (top of file)
#      Colors, fonts, window sizes, file paths, and the default presets
#      that ship with the launcher. These are the only values you'd need
#      to change to restyle the app or add new default presets.
#
#   2. HELPER FUNCTIONS  (load_presets, detect_models, etc.)
#      Small standalone utilities that handle file I/O, model detection,
#      and building the shared visual components (buttons, cards, labels).
#      They don't depend on each other and have no side effects.
#
#   3. UI COMPONENT CLASSES  (_MenuProxy, DropdownButton, PresetDialog)
#      Self-contained widgets used by the main window:
#        - DropdownButton  : a fully-themed dropdown (the model/preset selectors)
#        - PresetDialog    : the pop-up form for adding or editing a preset
#        - _MenuProxy      : an internal helper that lets DropdownButton behave
#                            like a standard menu without using the OS menu widget
#
#   4. MAIN APPLICATION CLASS  (CommandLauncherGUI)
#      Builds the window, wires up all the widgets, and owns the two-state
#      UI flow:
#        - Setup state    : first-run screen to configure the llama.cpp folder
#        - Config state   : model picker, preset picker, UVX command, Launch button
#        - Running state  : status log + Exit Processes button
#      It also owns the launched processes and handles starting/killing them.
#
# TWO-STATE WINDOW FLOW
# ---------------------
# The window has two distinct views that swap on Launch / Exit:
#
#   [ Setup view ]   →  shown on first run, asks for the llama.cpp folder path.
#                        Required before the home screen is accessible.
#                        Can be reopened at any time via the Settings button.
#
#   [ Config view ]  →  user picks model + preset, optionally edits UVX command,
#                        then clicks "Launch Server"
#
#   [ Running view ] →  config widgets are hidden, the status log appears showing
#                        which processes launched and their PIDs. "Exit Processes"
#                        kills both servers and returns to config view.
#
# PRESET SYSTEM
# -------------
# Presets are stored in presets.json next to this file. Each preset has:
#   - A name
#   - A command template  (uses {model} as a placeholder for the model path)
#   - A list of model keywords  (e.g. ["gemma"] — only shown when the selected
#     model filename contains that word)
#
# When the user picks a model, the preset dropdown automatically filters to
# only show presets that are tagged for that model family. Selecting a preset
# instantly builds the final launch command by substituting {model} with the
# actual file path.
#
# ============================================================
# ── Standard library imports
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import os
import sys
import json
import webbrowser
import re
import threading
import urllib.request
import time


# ── System tray bootstrap (pystray + Pillow)
def _bootstrap():
    import importlib.util, subprocess, sys
    for mod, pkg in [("pystray", "pystray"), ("PIL", "Pillow")]:
        if importlib.util.find_spec(mod) is None:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
try:
    _bootstrap()
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:
    _TRAY_AVAILABLE = False


# ============================================================
# SECTION 1 — VISUAL CONFIGURATION
# All colors, fonts, and window dimensions live here so they're
# easy to find and change without touching any logic.
# ============================================================

# Tokyo Night color palette — a dark blue-grey theme
BG        = "#1a1b26"
BG_PANEL  = "#24283b"
BG_INPUT  = "#1f2335"
ACCENT    = "#7aa2f7"
SUCCESS   = "#9ece6a"
DANGER    = "#f7768e"
TEXT_PRI  = "#c0caf5"
TEXT_SEC  = "#a9b1d6"
TEXT_MUT  = "#565f89"
BORDER    = "#292e42"

# Fonts
FONT_TITLE  = ("Segoe UI", 13, "bold")
FONT_LABEL  = ("Segoe UI", 9, "bold")
FONT_SMALL  = ("Segoe UI", 9)
FONT_BTN    = ("Segoe UI", 10, "bold")
FONT_BTN_SM = ("Segoe UI", 9)
FONT_MONO   = ("Cascadia Code", 10)
FONT_LOG    = ("Cascadia Code", 10)

# Window dimensions — fixed size, no manual resizing
W           = 480
H_SETUP     = 280    # height of the first-run setup screen
H_CONFIG    = 270    # height of the main config view
H_SETTINGS      = 375   # settings dialog, UVX collapsed
H_SETTINGS_UVX  = 465   # settings dialog, UVX expanded
W_SETTINGS      = 460   # settings dialog width
H_RUNNING   = 750    # height of the running/log view


# ============================================================
# SECTION 2 — FILE PATHS
# All paths are resolved relative to this script's location.
# ============================================================

MODELS_DIR  = "Models"  # resolved at runtime relative to llama_dir
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


# ============================================================
# SECTION 3 — CONFIG, PRESET & MODEL FILE HELPERS
# ============================================================

_DEFAULT_CONFIG = {
    "mcpServers": {
        "time":       {"command": "uvx", "args": ["mcp-server-time", "--local-timezone=America/Chicago"]},
        "fetch":      {"command": "uvx", "args": ["mcp-server-fetch"]},
        "ddg-search": {"command": "uvx", "args": ["duckduckgo-mcp-server"]},
    },
    "system_prompt": (
        "The current date and time is provided at the top of every message. "
        "Treat it as ground truth — do not estimate or assume the date from training memory.\n\n"
        "You have real-time web access via three MCP tools: ddg-search, mcp-fetch, and mcp-time.\n\n"
        "TOOL USAGE RULES:\n"
        "1. For any question involving facts, current events, people, software versions, prices, "
        "or anything that may have changed — use ddg-search BEFORE generating a response. "
        "Do not answer from training memory alone.\n"
        "2. Your training data is outdated. If search results conflict with your training knowledge, "
        "always trust the search results. Never override web results with what you think you know.\n"
        "3. If a search result requires more detail, use mcp-fetch to read the full page content.\n"
        "4. Use mcp-time only if you need precision timezone-aware time beyond what is already provided.\n"
        "5. Do not explain your tool usage to the user. Execute tools silently and respond based on what you find."
    ),
    "llama_dir": "",
    "last_preset": "",
    "last_model": "",
    "presets": {
        "Default (any model, ctx 8192)": {
            "command": 'llama-server -m "{model}" -c 8192 --jinja --webui-mcp-proxy --keep -1 --context-shift -ngl 999 --tools all --port 8081',
            "models": [],
        },
        "Default Qwen (Q8_0, ctx 32768)": {
            "command": 'llama-server -m "{model}" -c 32768 --jinja --webui-mcp-proxy --keep -1 --context-shift --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 -ngl 999 --tools all --port 8081',
            "models": ["qwen"],
        },
        "Default Gemma (Q8_0, ctx 131072)": {
            "command": 'llama-server -m "{model}" -c 131072 --jinja --webui-mcp-proxy --keep -1 --context-shift --swa-full --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 -ngl 999 --tools all --port 8081',
            "models": ["gemma"],
        },
    },
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        except IOError:
            pass
        return dict(_DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error reading config file: {e}")
        return dict(_DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except IOError as e:
        messagebox.showerror("Save Error", f"Could not save settings:\n{e}")


def load_presets(config: dict) -> dict:
    """Load and validate presets with enhanced migration logic."""
    presets = config.get("presets", {})
    
    # Validate and migrate presets
    migrated = {}
    for name, value in presets.items():
        if not isinstance(value, dict):
            # Handle legacy string-based presets
            migrated[name] = {
                "command": value,
                "models": []
            }
        else:
            # Validate modern dict-based presets
            if not isinstance(value.get("command"), str):
                raise ValueError(f"Invalid preset '{name}': 'command' must be a string")
                
            models = value.get("models", [])
            if not isinstance(models, list):
                raise ValueError(f"Invalid preset '{name}': 'models' must be a list")
                
            # Ensure all model entries are strings
            if not all(isinstance(model, str) for model in models):
                raise ValueError(f"Invalid preset '{name}': all models must be strings")
                
            migrated[name] = {
                "command": value["command"],
                "models": models
            }
    
    return migrated


def detect_models(llama_dir=""):
    # Scan llama_dir directly for .gguf files.
    models_dir = llama_dir if llama_dir else MODELS_DIR
    if not os.path.isdir(models_dir):
        return []
    return sorted(f for f in os.listdir(models_dir) if f.lower().endswith(".gguf"))


def preset_matches_model(preset, model_filename):
    # A preset matches if any of its keywords appear in the model filename.
    # A preset with no keywords matches all models.
    keywords = preset.get("models", [])
    if not keywords:
        return True
    lower = model_filename.lower()
    return any(kw.lower() in lower for kw in keywords)


# ============================================================
# SECTION 4 — THEME & SHARED UI HELPERS
# ============================================================

def apply_ttk_style():
    # Style the ttk scrollbar to match the dark theme.
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Vertical.TScrollbar",
        background=BG_PANEL, troughcolor=BG_INPUT,
        arrowcolor=TEXT_MUT, bordercolor=BORDER,
        darkcolor=BG_PANEL, lightcolor=BG_PANEL,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])


def flat_btn(parent, text, command, color=ACCENT, fg=BG, **kw):
    # Full-size flat button for primary actions.
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=color, activeforeground=fg,
        font=FONT_BTN, relief="flat", bd=0,
        padx=14, pady=7, cursor="hand2", **kw
    )


def small_btn(parent, text, command, color=BG_PANEL, fg=TEXT_SEC, **kw):
    # Compact flat button for secondary actions.
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=BORDER, activeforeground=TEXT_PRI,
        font=FONT_BTN_SM, relief="flat", bd=0,
        padx=10, pady=5, cursor="hand2", **kw
    )


def section_label(parent, text):
    # A label with a coloured left accent bar — used as a section heading.
    frame = tk.Frame(parent, bg=BG)
    tk.Frame(frame, bg=ACCENT, width=3).pack(side="left", fill="y")
    tk.Label(frame, text=text, font=FONT_LABEL,
             fg=TEXT_MUT, bg=BG).pack(side="left", padx=(8, 0), pady=4)
    return frame


def card(parent, **kw):
    # A 1px bordered panel. Returns the outer border frame and inner content frame.
    outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=BG_PANEL, **kw)
    inner.pack(fill="both", expand=True)
    return outer, inner


# ============================================================
# SECTION 4b — TRAY IMAGE HELPER
# ============================================================

def _make_tray_image():
    # Create a 64x64 RGBA icon: dark background with an accent-colored circle.
    img = Image.new("RGBA", (64, 64), (26, 27, 38, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(122, 162, 247, 255))
    return img


# ============================================================
# SECTION 5 — DROPDOWN WIDGET
# A fully-themed dropdown that avoids Windows native rendering.
# See the header comment for a full explanation of why this
# exists instead of using ttk.Combobox.
# ============================================================

class _MenuProxy:
    # Stores the dropdown options in memory, mimicking the tk.Menu API.
    def __init__(self):
        self._items = []

    def delete(self, first, last):
        self._items.clear()

    def add_command(self, label="", command=None):
        self._items.append((label, command or (lambda: None)))


class SlideToggle(tk.Canvas):
    """Compact sliding pill toggle between two labeled states."""
    _W, _H = 130, 26

    def __init__(self, parent, callback, initial=True):
        super().__init__(parent, width=self._W, height=self._H,
                         bg=BG_PANEL, highlightthickness=0, bd=0, cursor="hand2")
        self._state    = initial
        self._callback = callback
        self._draw()
        self.bind("<Button-1>", self._click)

    def _pill(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
        self.create_polygon(
            x1 + r, y1,  x2 - r, y1,  x2, y1,
            x2, y1 + r,  x2, y2 - r,  x2, y2,
            x2 - r, y2,  x1 + r, y2,  x1, y2,
            x1, y2 - r,  x1, y1 + r,  x1, y1,
            smooth=True, **kw,
        )

    def _draw(self):
        self.delete("all")
        W, H, r, mid = self._W, self._H, self._H // 2, self._W // 2
        self._pill(0, 0, W, H, r, fill=BG_INPUT)
        if self._state:
            self._pill(0, 0, mid, H, r, fill=ACCENT)
        else:
            self._pill(mid, 0, W, H, r, fill=ACCENT)
        self.create_text(mid // 2, H // 2, text="Search",
                         fill=BG if self._state else TEXT_MUT, font=FONT_SMALL)
        self.create_text(mid + mid // 2, H // 2, text="Code",
                         fill=BG if not self._state else TEXT_MUT, font=FONT_SMALL)

    def _click(self, _):
        self._state = not self._state
        self._draw()
        self._callback(self._state)

    @property
    def search_mode(self):
        return self._state


class DropdownButton(tk.Frame):
    # A custom dropdown: a Button + arrow Label that opens a borderless popup window.
    def __init__(self, parent, var):
        super().__init__(parent, bg=BG, padx=1, pady=1)
        self._var   = var
        self._popup = None
        self.menu   = _MenuProxy()

        self._arrow = tk.Label(self, text="▾", bg=BG_INPUT, fg=TEXT_MUT,
                               font=FONT_SMALL, padx=6)
        self._arrow.pack(side="right", fill="y")

        self._btn = tk.Button(
            self, textvariable=var,
            bg=BG_INPUT, fg=TEXT_PRI,
            activebackground=BG_PANEL, activeforeground=TEXT_PRI,
            font=FONT_SMALL, relief="flat", bd=0,
            highlightthickness=0, anchor="w", padx=8,
            cursor="hand2", command=self._toggle_popup,
        )
        self._btn.pack(side="left", fill="both", expand=True)
        self._arrow.bind("<Button-1>", lambda e: self._toggle_popup())

    def _toggle_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
            self._popup = None
            return
        self._show_popup()

    def _show_popup(self):
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = self.winfo_width()

        # overrideredirect strips all OS window chrome so we control the border fully
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=BG)

        inner = tk.Frame(popup, bg=BG_INPUT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        for label, cmd in self.menu._items:
            def on_select(c=cmd, p=popup):
                c()
                p.destroy()
                self._popup = None

            tk.Button(
                inner, text=label, anchor="w",
                bg=BG_INPUT, fg=TEXT_PRI,
                activebackground=ACCENT, activeforeground=BG,
                font=FONT_SMALL, relief="flat", bd=0,
                highlightthickness=0, padx=8, pady=4,
                cursor="hand2", command=on_select,
            ).pack(fill="x")

        popup.update_idletasks()
        h = inner.winfo_reqheight() + 2
        popup.geometry(f"{w}x{h}+{x}+{y}")

        # 50ms delay lets the click handler fire before the popup is destroyed on focus loss
        def on_focus_out(event):
            def check():
                if not popup.winfo_exists():
                    return
                focused = popup.focus_get()
                if focused is None or not str(focused).startswith(str(popup)):
                    popup.destroy()
                    self._popup = None
            popup.after(50, check)

        popup.bind("<FocusOut>", on_focus_out)
        popup.focus_set()
        self._popup = popup


# ============================================================
# SECTION 6 — PRESET EDITOR DIALOG
# A modal form for adding or editing a preset.
# ============================================================

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

        section_label(self, "PRESET NAME").pack(fill="x", padx=20, pady=(16, 2))
        self.name_var = tk.StringVar(value=name)
        tk.Entry(self, textvariable=self.name_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(fill="x", padx=20, ipady=6)

        section_label(self, "APPLIES TO MODELS").pack(fill="x", padx=20, pady=(14, 2))
        tk.Label(self, text="Select models this preset applies to — leave none selected to match all",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 4))

        list_frame = tk.Frame(self, bg=BORDER)
        list_frame.pack(fill="x", padx=20)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", bg=BG_PANEL,
                                 troughcolor=BG_INPUT, relief="flat")
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

        section_label(self, "COMMAND TEMPLATE  --  use {model} as the model path placeholder").pack(
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


# ============================================================
# SECTION 7 — SETTINGS DIALOG
# A small modal that lets the user change the llama.cpp folder
# path after initial setup. Accessible via the Settings button
# in the header of the home screen.
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, current_path, on_save,
                 uvx_command="", on_save_uvx=None,
                 search_mode=True, on_set_mode=None):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry(f"{W_SETTINGS}x{H_SETTINGS}")
        self.grab_set()

        self._on_save        = on_save
        self._on_save_uvx    = on_save_uvx
        self._on_set_mode    = on_set_mode
        self._uvx_expanded   = False

        # ── Llama.cpp folder ─────────────────────────────────────
        section_label(self, "LLAMA.CPP FOLDER").pack(fill="x", padx=20, pady=(20, 6))
        tk.Label(self, text="The working directory used when launching llama-server.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 10))

        path_row = tk.Frame(self, bg=BG)
        path_row.pack(fill="x", padx=20)
        path_row.columnconfigure(0, weight=1)

        self._path_var = tk.StringVar(value=current_path)
        tk.Entry(path_row, textvariable=self._path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        small_btn(path_row, "Browse", self._browse).grid(row=0, column=1, padx=(8, 0))

        # ── Launch options ────────────────────────────────────────
        section_label(self, "LAUNCH OPTIONS").pack(fill="x", padx=20, pady=(20, 6))

        # Web UI row: label left, equal-width Yes/No buttons right
        web_row = tk.Frame(self, bg=BG)
        web_row.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(web_row, text="Web UI", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")

        btn_grp = tk.Frame(web_row, bg=BG)
        btn_grp.pack(side="right")
        self._search_btn_dlg = tk.Button(
            btn_grp, text="Yes", width=5,
            font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
            command=lambda: self._set_mode_dlg(True),
        )
        self._search_btn_dlg.pack(side="left", padx=(0, 3))
        self._code_btn_dlg = tk.Button(
            btn_grp, text="No", width=5,
            font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
            command=lambda: self._set_mode_dlg(False),
        )
        self._code_btn_dlg.pack(side="left")

        self._search_mode_dlg = search_mode
        self._apply_mode_style()

        tk.Label(self, text="Open the browser automatically when the server comes online.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 14))

        # UVX Settings: label + description + accordion toggle
        uvx_hdr = tk.Frame(self, bg=BG)
        uvx_hdr.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(uvx_hdr, text="UVX Settings", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
        self._uvx_btn = small_btn(uvx_hdr, "▶ Show command", self._toggle_uvx_dlg)
        self._uvx_btn.pack(side="right")

        tk.Label(self, text="Command used to start the MCP proxy bridge on port 8001.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 6))

        self._uvx_text = tk.Text(
            self, height=4, font=FONT_MONO,
            bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
            relief="flat", wrap="word", padx=8, pady=6,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self._uvx_text.insert("1.0", uvx_command)

        # ── Save / Cancel ─────────────────────────────────────────
        self._btn_row = tk.Frame(self, bg=BG)
        self._btn_row.pack(padx=20, pady=(10, 20), fill="x")
        flat_btn(self._btn_row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        small_btn(self._btn_row, "Cancel", self.destroy).pack(side="left")

    def _set_mode_dlg(self, search):
        self._search_mode_dlg = search
        self._apply_mode_style()
        if self._on_set_mode:
            self._on_set_mode(search)

    def _apply_mode_style(self):
        if self._search_mode_dlg:
            self._search_btn_dlg.config(bg=ACCENT, fg=BG,
                                        activebackground=ACCENT, activeforeground=BG)
            self._code_btn_dlg.config(bg=BG_INPUT, fg=TEXT_SEC,
                                      activebackground=BORDER, activeforeground=TEXT_PRI)
        else:
            self._search_btn_dlg.config(bg=BG_INPUT, fg=TEXT_SEC,
                                        activebackground=BORDER, activeforeground=TEXT_PRI)
            self._code_btn_dlg.config(bg=ACCENT, fg=BG,
                                      activebackground=ACCENT, activeforeground=BG)

    def _toggle_uvx_dlg(self):
        self._uvx_expanded = not self._uvx_expanded
        if self._uvx_expanded:
            self._uvx_text.pack(fill="x", padx=20, pady=(0, 6), before=self._btn_row)
            self._uvx_btn.config(text="▼ Hide command")
            self.geometry(f"{W_SETTINGS}x{H_SETTINGS_UVX}")
        else:
            self._uvx_text.pack_forget()
            self._uvx_btn.config(text="▶ Show command")
            self.geometry(f"{W_SETTINGS}x{H_SETTINGS}")

    def _browse(self):
        path = filedialog.askdirectory(title="Select llama.cpp folder")
        if path:
            self._path_var.set(path)

    def _save(self):
        path = self._path_var.get().strip()
        if not path:
            messagebox.showerror("Required", "Please enter a path.", parent=self)
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path",
                "That folder doesn't exist. Please choose a valid directory.", parent=self)
            return
        self._on_save(path)
        if self._on_save_uvx:
            self._on_save_uvx(self._uvx_text.get("1.0", "end").strip())
        self.destroy()


# ============================================================
# SECTION 8 — MAIN APPLICATION WINDOW
# CommandLauncherGUI manages the full window lifecycle:
#
#   _build_setup_screen()  — first-run path configuration
#   _confirm_setup()       — validates path, saves config, transitions to home
#   _build_home_screen()   — the main launcher UI
#
# On startup, the app checks config.json. If no llama.cpp path
# is saved, the setup screen is shown. Otherwise the home screen
# loads directly.
# ============================================================

class CommandLauncherGUI:
    def __init__(self, master):
        self.master = master
        master.title("")
        master.configure(bg=BG)
        master.resizable(False, False)
        master.iconbitmap(default="")

        apply_ttk_style()

        # Kill all child processes when the window is closed
        master.protocol("WM_DELETE_WINDOW", self._on_close)

        # Load persisted config — contains the llama.cpp folder path
        self.config    = load_config()
        self.llama_dir = self.config.get("llama_dir", "")

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

        # Header bar
        hdr = tk.Frame(self.master, bg="#13141f", height=54)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg="#13141f").pack(side="left", padx=24, pady=14)

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
        self.config["llama_dir"] = path
        save_config(self.config)

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

        # Application state
        try:
            self.presets = load_presets(self.config)
        except ValueError as e:
            messagebox.showerror("Config Error",
                f"One or more presets in config.json are invalid:\n{e}\n\n"
                "Proceeding with an empty preset list.")
            self.presets = {}
        self._search_mode  = True
        self.uvx_command   = self.config.get(
            "uvx_command",
            f'uvx mcp-proxy --named-server-config "{CONFIG_FILE}" --allow-origin "*" --port 8001 --stateless'
        )
        self.llama_cmd     = ""
        self._llama_proc   = None
        self._uvx_proc     = None
        self._proxy_proc   = None
        self._polling      = False
        self._launch_time  = None

        self.master.columnconfigure(0, weight=1)

        # ── Row 0: Header bar with Settings button on the right
        hdr = tk.Frame(self.master, bg="#13141f", height=54)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.columnconfigure(0, weight=1)
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg="#13141f").pack(side="left", padx=24, pady=14)
        small_btn(hdr, "⚙ Settings", self._open_settings,
                  color="#13141f", fg=TEXT_MUT).pack(side="right", padx=16, pady=14)

        # ── Row 1: Model selector
        self.model_outer, model_card = card(self.master)
        self.model_outer.grid(row=1, column=0, padx=16, pady=(12, 6), sticky="ew")
        model_card.grid_columnconfigure(1, weight=1)

        section_label(model_card, "MODEL").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.model_var  = tk.StringVar()
        self.model_menu = DropdownButton(model_card, self.model_var)
        self.model_menu.grid(row=0, column=1, sticky="ew", pady=10)
        small_btn(model_card, "Refresh", self._refresh_models,
                  color=BG_PANEL, fg=TEXT_SEC).grid(row=0, column=2, padx=(6, 12), pady=10)

        self._refresh_models(init=True)

        # ── Row 2: Preset selector
        self.preset_outer, preset_card = card(self.master)
        self.preset_outer.grid(row=2, column=0, padx=16, pady=(0, 6), sticky="ew")
        preset_card.grid_columnconfigure(1, weight=1)

        section_label(preset_card, "PRESET").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.preset_var  = tk.StringVar()
        self.preset_menu = DropdownButton(preset_card, self.preset_var)
        self.preset_menu.grid(row=0, column=1, sticky="ew", pady=10)

        preset_btns = tk.Frame(preset_card, bg=BG_PANEL)
        preset_btns.grid(row=0, column=2, padx=(6, 12), pady=10)
        small_btn(preset_btns, "+ Add",  self._add_preset).pack(side="left", padx=(0, 6))
        small_btn(preset_btns, "Edit",   self._edit_preset).pack(side="left", padx=(0, 6))
        small_btn(preset_btns, "Delete", self._delete_preset,
                  color=BG_PANEL, fg=DANGER).pack(side="left")

        # ── Row 3: Launch Server button
        self.run_button = tk.Button(
            self.master, text="Launch Server",
            command=self.run_commands,
            bg=SUCCESS, fg=BG,
            activebackground="#7db84e", activeforeground=BG,
            font=("Segoe UI", 12, "bold"), relief="flat", bd=0,
            pady=14, cursor="hand2",
        )
        self.run_button.grid(row=3, column=0,
                             sticky="ew", padx=16, pady=(6, 8))

        # ── Row 6: Status bar (hidden until running state)
        self.status_bar = tk.Frame(self.master, bg=BG_PANEL)

        # Left: health dot
        self._health_label = tk.Label(self.status_bar, text="● waiting",
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL)
        self._health_label.pack(side="left", padx=(16, 0), pady=8)

        # Middle: uptime label + elapsed counter
        tk.Label(self.status_bar, text="uptime",
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL).pack(side="left", padx=(20, 6), pady=8)
        self._elapsed_var = tk.StringVar(value="00:00:00")
        tk.Label(self.status_bar, textvariable=self._elapsed_var,
            font=FONT_MONO, fg=TEXT_SEC, bg=BG_PANEL).pack(side="left", pady=8)

        # Right: auto-scroll checkbox
        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.status_bar, text="Auto-scroll",
            variable=self._autoscroll_var,
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL,
            activebackground=BG_PANEL, activeforeground=TEXT_SEC,
            selectcolor=BG_INPUT, cursor="hand2").pack(side="right", padx=16, pady=8)

        # ── Rows 7–8: Running state (hidden until Launch is pressed)
        # Two stacked log panes separated by a draggable sash.

        self._llama_label_var  = tk.StringVar(value="llama-server")
        self._uvx_label_var    = tk.StringVar(value="UVX Proxy")
        self._proxy_label_var  = tk.StringVar(value="System Prompt Proxy")

        # PanedWindow lets the user drag the divider to resize each log pane.
        self.log_pane = tk.PanedWindow(
            self.master, orient="vertical",
            bg=BORDER, sashwidth=5, sashpad=0,
            sashrelief="flat", bd=0, relief="flat",
        )

        def _make_log_pane(label_var):
            frame = tk.Frame(self.log_pane, bg=BG)
            hdr = tk.Frame(frame, bg=BG)
            hdr.pack(fill="x", pady=(0, 4))
            tk.Frame(hdr, bg=ACCENT, width=3).pack(side="left", fill="y")
            tk.Label(hdr, textvariable=label_var,
                     font=FONT_LABEL, fg=TEXT_MUT, bg=BG).pack(
                side="left", padx=(8, 0), pady=4)
            border = tk.Frame(frame, bg=BORDER, padx=1, pady=1)
            border.pack(fill="both", expand=True)
            txt = tk.Text(border, font=FONT_LOG,
                          bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                          relief="flat", state=tk.DISABLED, wrap="none")
            sb = ttk.Scrollbar(border, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            return frame, txt

        llama_frame,  self.llama_log  = _make_log_pane(self._llama_label_var)
        uvx_frame,    self.uvx_log    = _make_log_pane(self._uvx_label_var)
        proxy_frame,  self.proxy_log  = _make_log_pane(self._proxy_label_var)
        self.log_pane.add(llama_frame,  stretch="always", minsize=80)
        self.log_pane.add(uvx_frame,    stretch="always", minsize=80)
        self.log_pane.add(proxy_frame,  stretch="always", minsize=80)

        # ── Exit button
        self.exit_btn = tk.Button(
            self.master, text="Exit Processes",
            command=self._exit_running_state,
            bg=DANGER, fg=BG,
            activebackground="#c45c6e", activeforeground=BG,
            font=("Segoe UI", 12, "bold"), relief="flat", bd=0,
            pady=14, cursor="hand2",
        )

        # When the model changes, re-filter the preset list.
        # When the preset changes, rebuild the launch command.
        self.model_var.trace_add("write",  lambda *_: self._on_model_changed())
        self.preset_var.trace_add("write", lambda *_: self._load_preset())

        self._refresh_preset_menu(init=True)

        # Set up system tray icon if available
        self._setup_tray()


    # ── Settings ─────────────────────────────────────────────

    def _open_settings(self):
        def on_save(new_path):
            self.llama_dir = new_path
            self.config["llama_dir"] = new_path
            save_config(self.config)

        def on_save_uvx(cmd):
            self.uvx_command = cmd
            self.config["uvx_command"] = cmd
            save_config(self.config)

        SettingsDialog(
            self.master, self.llama_dir, on_save,
            uvx_command=self.uvx_command,
            on_save_uvx=on_save_uvx,
            search_mode=self._search_mode,
            on_set_mode=self._set_mode,
        )


    # ── Window sizing ─────────────────────────────────────────

    def _resize_config(self):
        self.master.geometry(f"{W}x{H_CONFIG}")

    def _set_mode(self, search):
        self._search_mode = search


    # ── Two-state view switching ──────────────────────────────

    def _enter_running_state(self):
        # Hide config widgets and show the status bar, two log panes + exit button.
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid_remove()
        self.status_bar.grid(row=4, column=0,
                             sticky="ew", padx=16, pady=(10, 0))
        self.master.grid_rowconfigure(5, weight=1)
        self.log_pane.grid(row=5, column=0,
                           sticky="nsew", padx=16, pady=(6, 6))
        self.exit_btn.grid(row=6, column=0,
                           sticky="ew", padx=16, pady=(0, 14))
        self.master.resizable(True, True)
        self.master.geometry(f"{W}x{H_RUNNING}")

    def _exit_running_state(self):
        # Kill both processes, then restore the config view.
        self._kill_all()
        self._stop_health_poll()
        self._stop_elapsed()
        self.master.resizable(False, False)
        self.master.grid_rowconfigure(5, weight=0)
        self.status_bar.grid_remove()
        self.log_pane.grid_remove()
        self.exit_btn.grid_remove()
        # Reset pane labels for the next launch
        self._llama_label_var.set("llama-server")
        self._uvx_label_var.set("UVX Proxy")
        self._proxy_label_var.set("System Prompt Proxy")
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid()
        self._resize_config()


    # ── Model & preset list management ───────────────────────

    def _refresh_models(self, init=False):
        models = detect_models(self.llama_dir)
        menu = self.model_menu.menu
        menu.delete(0, "end")
        if models:
            display = [m[:-5] if m.lower().endswith(".gguf") else m for m in models]
            for d in display:
                menu.add_command(label=d, command=lambda v=d: self.model_var.set(v))
            current = self.model_var.get()
            if init or not current or current not in display:
                last = self.config.get("last_model")
                if init and last and last in display:
                    self.model_var.set(last)
                else:
                    self.model_var.set(display[0])
        else:
            menu.add_command(label="(no .gguf models found)",
                             command=lambda: self.model_var.set("(no .gguf models found)"))
            self.model_var.set("(no .gguf models found)")

    def _on_model_changed(self):
        m = self.model_var.get()
        if m and not m.startswith("("):
            self.config["last_model"] = m
            save_config(self.config)
        self._refresh_preset_menu()

    def _filtered_presets(self):
        model = self.model_var.get()
        if not model or model.startswith("("):
            return {}
        return {n: d for n, d in self.presets.items() if preset_matches_model(d, model)}

    def _refresh_preset_menu(self, init=False):
        filtered = self._filtered_presets()
        menu = self.preset_menu.menu
        menu.delete(0, "end")
        if filtered:
            for name in filtered:
                menu.add_command(label=name, command=lambda v=name: self.preset_var.set(v))
            current = self.preset_var.get()
            if init or not current or current not in filtered:
                last = self.config.get("last_preset")
                self._auto_preset_update = True
                if init and last and last in filtered:
                    self.preset_var.set(last)
                else:
                    self.preset_var.set(next(iter(filtered)))
                self._auto_preset_update = False
            else:
                self._load_preset()
        else:
            menu.add_command(label="(no matching presets)",
                             command=lambda: self.preset_var.set("(no matching presets)"))
            self._auto_preset_update = True
            self.preset_var.set("(no matching presets)")
            self._auto_preset_update = False
            self.llama_cmd = ""

    def _load_preset(self):
        # Build the launch command by substituting {model} with the actual file path.
        name  = self.preset_var.get()
        model = self.model_var.get()
        if not name or name not in self.presets:
            return
        template   = self.presets[name]["command"]
        models_dir = self.llama_dir if self.llama_dir else "."
        model_path = os.path.join(models_dir, model + ".gguf") if model and not model.startswith("(") else model
        self.llama_cmd = template.replace("{model}", model_path)
        if not getattr(self, "_auto_preset_update", False):
            self.config["last_preset"] = name
            save_config(self.config)


    # ── Preset CRUD ───────────────────────────────────────────

    def _add_preset(self):
        models = [m[:-5] if m.lower().endswith(".gguf") else m for m in detect_models(self.llama_dir)]
        dlg = PresetDialog(self.master, "Add Preset", available_models=models)
        self.master.wait_window(dlg)
        if dlg.result_name:
            if dlg.result_name in self.presets:
                messagebox.showerror("Duplicate",
                    f'A preset named "{dlg.result_name}" already exists.')
                return
            self.presets[dlg.result_name] = {
                "command": dlg.result_command, "models": dlg.result_models
            }
            self.config["presets"] = self.presets
            save_config(self.config)
            self._refresh_preset_menu()
            if dlg.result_name in self._filtered_presets():
                self.preset_var.set(dlg.result_name)

    def _edit_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            messagebox.showwarning("No Preset", "Please select a preset to edit.")
            return
        data   = self.presets[name]
        models = [m[:-5] if m.lower().endswith(".gguf") else m for m in detect_models(self.llama_dir)]
        dlg    = PresetDialog(self.master, "Edit Preset", name=name,
                              command=data["command"],
                              model_keywords=data.get("models", []),
                              available_models=models)
        self.master.wait_window(dlg)
        if dlg.result_name:
            if dlg.result_name != name and dlg.result_name in self.presets:
                messagebox.showerror("Duplicate",
                    f'A preset named "{dlg.result_name}" already exists.')
                return
            del self.presets[name]
            self.presets[dlg.result_name] = {
                "command": dlg.result_command, "models": dlg.result_models
            }
            self.config["presets"] = self.presets
            save_config(self.config)
            self._refresh_preset_menu()
            if dlg.result_name in self._filtered_presets():
                self.preset_var.set(dlg.result_name)

    def _delete_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            messagebox.showwarning("No Preset", "Please select a preset to delete.")
            return
        if not messagebox.askyesno("Confirm Delete", f'Delete preset "{name}"?'):
            return
        del self.presets[name]
        self.config["presets"] = self.presets
        save_config(self.config)
        self._refresh_preset_menu()


    # ── Server health polling ─────────────────────────────────

    def _start_health_poll(self):
        self._polling = True
        def poll_loop():
            browser_opened = False
            while self._polling:
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8080/health", timeout=2
                    ) as resp:
                        data = json.loads(resp.read().decode())
                        status = data.get("status", "unknown")
                    if status == "ok":
                        color = SUCCESS
                        text  = "● online"
                        if not browser_opened and self._search_mode:
                            browser_opened = True
                            self.master.after(0, lambda: webbrowser.open("http://127.0.0.1:8080/"))
                    else:
                        color = "#f7c948"
                        text  = f"● {status}"
                except Exception:
                    color = TEXT_MUT
                    text  = "● offline"
                self.master.after(0, lambda c=color, t=text: self._health_label.config(fg=c, text=t))
                time.sleep(3)
        threading.Thread(target=poll_loop, daemon=True).start()

    def _stop_health_poll(self):
        self._polling = False
        self._health_label.config(text="● waiting", fg=TEXT_MUT)


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
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _show_from_tray(self, icon=None, item=None):
        self.master.after(0, self.master.deiconify)

    def _hide_to_tray(self):
        self.master.withdraw()

    def _tray_exit(self, icon=None, item=None):
        self._tray_icon.stop()
        self.master.after(0, self._force_exit)

    def _force_exit(self):
        if hasattr(self, "_llama_proc"):
            self._kill_all()
        self.master.destroy()


    # ── Process management ────────────────────────────────────

    def _free_port(self, cmd, default_port, widget):
        # Kill any process already listening on the port this command will use.
        m = re.search(r'--port\s+(\d+)', cmd)
        port = int(m.group(1)) if m else default_port
        try:
            result = subprocess.run(
                f'netstat -ano | findstr ":{port} "',
                shell=True, capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if "LISTENING" in line and len(parts) >= 5 and parts[-1].isdigit():
                    pid = parts[-1]
                    if pid != "0":
                        subprocess.run(f"taskkill /F /T /PID {pid}",
                                       shell=True, capture_output=True)
                        self._append_log(widget, f"  Port {port} was in use — freed PID {pid}")
        except Exception:
            pass

    def _kill_proc(self, proc, widget=None):
        # Kill a process and its full child tree (/T) — ensures GPU workers are also stopped.
        if proc is None:
            return
        try:
            subprocess.run(f"taskkill /F /T /PID {proc.pid}",
                           shell=True, capture_output=True)
            if widget:
                self._append_log(widget, f"─── killed (PID {proc.pid}) ───")
        except Exception as e:
            if widget:
                self._append_log(widget, f"─── kill failed: {e} ───")

    def _kill_all(self):
        self._kill_proc(self._llama_proc, getattr(self, "llama_log", None))
        self._llama_proc = None
        self._kill_proc(self._uvx_proc, getattr(self, "uvx_log", None))
        self._uvx_proc = None
        self._kill_proc(self._proxy_proc, getattr(self, "proxy_log", None))
        self._proxy_proc = None

    def _on_close(self):
        # If servers are running and tray is available, minimize to tray instead of closing.
        if (self._llama_proc is not None or self._uvx_proc is not None or self._proxy_proc is not None) and _TRAY_AVAILABLE:
            self._hide_to_tray()
        else:
            if hasattr(self, "_llama_proc"):
                self._kill_all()
            self.master.destroy()


    # ── Log helpers ───────────────────────────────────────────

    def _append_log(self, widget, text):
        # Append one line to a log pane. Safe to call from any thread via .after().
        widget.config(state=tk.NORMAL)
        widget.insert("end", text + "\n")
        if self._autoscroll_var.get():
            widget.see("end")
        widget.config(state=tk.DISABLED)

    def _stream_output(self, proc, widget):
        # Read the process's stdout line-by-line in a background thread and
        # schedule each line onto the main thread so tkinter stays thread-safe.
        def reader():
            try:
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    widget.after(0, lambda l=line: self._append_log(widget, l))
            except Exception:
                pass
            widget.after(0, lambda: self._append_log(widget, "─── process ended ───"))
        threading.Thread(target=reader, daemon=True).start()


    # ── Launch sequence ───────────────────────────────────────

    def run_commands(self):
        uvx_cmd = self.uvx_command.strip()

        if not self.llama_cmd:
            messagebox.showerror("No Command",
                "No Llama command available. Please select a model and preset.")
            return
        if not uvx_cmd:
            messagebox.showerror("No Command",
                "UVX command is empty. Open Settings to fill it in.")
            return

        # Clear all log panes before entering the running state
        for w in (self.llama_log, self.uvx_log, self.proxy_log):
            w.config(state=tk.NORMAL)
            w.delete("1.0", "end")
            w.config(state=tk.DISABLED)
        self._enter_running_state()

        model_name = self.model_var.get()
        cwd = self.llama_dir if self.llama_dir else None

        try:
            self._free_port(self.llama_cmd, 8081, self.llama_log)
            self._llama_proc = subprocess.Popen(
                self.llama_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
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
                cwd=cwd,
            )
            self._uvx_label_var.set(f"UVX Proxy  ·  PID {self._uvx_proc.pid}")
            self._append_log(self.uvx_log, f"Launched (PID {self._uvx_proc.pid})")
            self._stream_output(self._uvx_proc, self.uvx_log)

            self._free_port("--port 8080", 8080, self.proxy_log)
            proxy_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.py")
            if not os.path.isfile(proxy_script):
                raise FileNotFoundError(f"proxy.py not found at {proxy_script}")
            mode = "search" if self._search_mode else "code"
            self._proxy_proc = subprocess.Popen(
                [sys.executable, proxy_script, "8080", "8081", mode],
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._proxy_label_var.set(f"System Prompt Proxy  ·  PID {self._proxy_proc.pid}")
            self._append_log(self.proxy_log, f"Launched (PID {self._proxy_proc.pid})")
            self._stream_output(self._proxy_proc, self.proxy_log)

            self._start_health_poll()
            self._start_elapsed()


        except Exception as e:
            self._append_log(self.llama_log, f"LAUNCH FAILED: {e}")
            self._kill_all()
            self._exit_running_state()


# ============================================================
# ENTRY POINT
# Creates the root window, hands it to CommandLauncherGUI,
# and starts the tkinter event loop.
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app  = CommandLauncherGUI(root)
    root.mainloop()
