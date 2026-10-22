"""Shared widgets and helpers: buttons, cards, popup menu, dropdown, log tabs, tray image."""
import collections
import re
import tkinter as tk
from .paths import ICON_FILE
from .theme import ACCENT, BG, BG_INPUT, BG_PANEL, BORDER, FONT_BTN, FONT_BTN_SM, FONT_SMALL, HOVER, TEXT_MUT, TEXT_PRI, TEXT_SEC
from . import theme
theme.follow(globals())  # colour names follow set_palette (ADR 0020)


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
    from PIL import Image
    _TRAY_AVAILABLE = True
except Exception:
    pystray = Image = None
    _TRAY_AVAILABLE = False


_LOG_ERROR = re.compile(r"\b(error|failed|fatal|exception|traceback)\b|exited \(code", re.IGNORECASE)


def log_line_is_error(text):
    # Marks a log tab with a red dot when a line like this arrives while another tab is shown.
    return bool(_LOG_ERROR.search(text))


LogTab = collections.namedtuple("LogTab", "frame button dot label_var")  # one log tab's widgets


def trim_log(widget, max_lines):
    # Drop a Text widget's oldest lines beyond max_lines (the text ends with a newline).
    lines = int(widget.index("end-1c").split(".")[0]) - 1
    if lines > max_lines:
        widget.delete("1.0", f"{lines - max_lines + 1}.0")


def style_toggle(button, on):
    # One button of a segmented choice (Settings' Yes/No and Theme, the log tabs).
    if on:
        button.config(bg=ACCENT, fg=BG, activebackground=ACCENT, activeforeground=BG)
    else:
        button.config(bg=BG_INPUT, fg=TEXT_SEC, activebackground=BORDER, activeforeground=TEXT_PRI)


def flat_btn(parent, text, command, color=None, fg=None, **kw):
    # Full-size flat button for primary actions. Colours default at call time (theme switches).
    color, fg = color or ACCENT, fg or BG
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=color, activeforeground=fg,
        disabledforeground=TEXT_MUT, font=FONT_BTN, relief="flat", bd=0,
        padx=14, pady=7, cursor="hand2", **kw
    )


def small_btn(parent, text, command, color=None, fg=None, **kw):
    # Compact ghost button for secondary actions: the parent's background until hovered.
    color, fg = color or parent.cget("bg"), fg or TEXT_SEC
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=HOVER, activeforeground=TEXT_PRI,
        disabledforeground=TEXT_MUT, font=FONT_BTN_SM, relief="flat", bd=0,
        padx=10, pady=5, cursor="hand2", **kw
    )


def copy_btn(parent, value):
    # A ghost "Copy" button that puts value() on the clipboard and says "Copied" for a moment.
    def copy():
        parent.clipboard_clear()
        parent.clipboard_append(value())
        b.config(text="Copied")
        b.after(1200, lambda: b.winfo_exists() and b.config(text="Copy"))
    b = small_btn(parent, "Copy", copy, width=6)
    b.config(padx=8, pady=2)
    return b


def section_label(parent, text):
    # A quiet sentence-case heading in muted text, on the parent's background.
    bg = parent.cget("bg")
    frame = tk.Frame(parent, bg=bg)
    tk.Label(frame, text=text, font=FONT_SMALL,
             fg=TEXT_MUT, bg=bg).pack(side="left", pady=4)
    return frame


def card(parent, **kw):
    # A 1px bordered panel. Returns the outer border frame and inner content frame.
    outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=BG_PANEL, **kw)
    inner.pack(fill="both", expand=True)
    return outer, inner


def _make_tray_image():
    # The app icon (its largest frame); pystray scales it to the tray's size.
    return Image.open(ICON_FILE)


class _MenuProxy:
    # Stores the dropdown options in memory, mimicking the tk.Menu API.
    def __init__(self):
        self._items = []

    def delete(self, first, last):
        self._items.clear()

    def add_command(self, label="", command=None):
        self._items.append((label, command or (lambda: None)))


def popup_menu(anchor, items, width=None, right=False, on_close=None):
    # A themed borderless menu under anchor; items are (label, command) or (label, command, fg).
    # It closes on a pick, on focus loss or on Esc, and returns the popup window.
    anchor.update_idletasks()
    popup = tk.Toplevel(anchor)
    popup.overrideredirect(True)  # no OS chrome, so the border is ours
    popup.configure(bg=BORDER)
    inner = tk.Frame(popup, bg=BG_INPUT)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    def close():
        if popup.winfo_exists():
            popup.destroy()
        if on_close:
            on_close()

    for label, command, *fg in items:
        def pick(c=command):
            close()  # first, so a dialog the command opens doesn't sit under the menu
            c()
        tk.Button(inner, text=label, anchor="w",
                  bg=BG_INPUT, fg=fg[0] if fg else TEXT_PRI,
                  activebackground=ACCENT, activeforeground=BG,
                  font=FONT_SMALL, relief="flat", bd=0,
                  highlightthickness=0, padx=10, pady=5,
                  cursor="hand2", command=pick).pack(fill="x")
    popup.update_idletasks()
    w = width or inner.winfo_reqwidth() + 2
    h = inner.winfo_reqheight() + 2
    x = anchor.winfo_rootx() + (anchor.winfo_width() - w if right else 0)
    y = anchor.winfo_rooty() + anchor.winfo_height()
    popup.geometry(f"{w}x{h}+{x}+{y}")

    # 50ms delay lets the click handler fire before the popup is destroyed on focus loss
    def on_focus_out(event):
        def check():
            if not popup.winfo_exists():
                return
            focused = popup.focus_get()
            if focused is None or not str(focused).startswith(str(popup)):
                close()
        popup.after(50, check)

    popup.bind("<FocusOut>", on_focus_out)
    popup.bind("<Escape>", lambda e: (close(), "break")[1])
    popup.focus_set()
    return popup


class DropdownButton(tk.Frame):
    # A custom dropdown: a Button + arrow Label that opens a borderless popup window.
    def __init__(self, parent, var):
        super().__init__(parent, bg=BORDER, padx=1, pady=1)
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
        self._popup = popup_menu(self, self.menu._items, width=self.winfo_width(), on_close=self._forget_popup)

    def _forget_popup(self):
        self._popup = None
