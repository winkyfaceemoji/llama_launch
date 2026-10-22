"""Palettes, fonts, window sizes and live theme switching (ADR 0014, 0020)."""
from tkinter import ttk
import ctypes
import tkinter as tk
import winreg

# Neutral greys from the llama Web UI, light and dark. set_palette() rebinds the
# module constants (BG, TEXT_PRI, ...) to one of them. Every colour must be unique
# within its palette: apply_theme() finds a widget's role by its colour, which is why
# BG_PANEL is one step off BG.
PALETTES = {
    "light": {
        "BG":            "#ffffff",
        "BG_PANEL":      "#fefefe",   # boxes
        "BG_INPUT":      "#f5f5f5",
        "HOVER":         "#eeeeee",
        "BORDER":        "#e5e5e5",
        "ACCENT":        "#171717",   # primary: filled buttons, selection, links
        "TEXT_PRI":      "#0a0a0a",
        "TEXT_SEC":      "#404040",
        "TEXT_MUT":      "#6b6b6b",
        "SUCCESS":       "#15803d",
        "SUCCESS_HOVER": "#166534",
        "DANGER":        "#e7000b",
        "DANGER_HOVER":  "#c10007",
        "WARN":          "#b45309",
    },
    "dark": {
        "BG":            "#0d0d0d",
        "BG_PANEL":      "#0e0e0e",
        "BG_INPUT":      "#171717",
        "HOVER":         "#262626",
        "BORDER":        "#2e2e2e",
        "ACCENT":        "#e5e5e5",
        "TEXT_PRI":      "#fafafa",
        "TEXT_SEC":      "#d4d4d4",
        "TEXT_MUT":      "#a1a1a1",
        "SUCCESS":       "#22a35a",
        "SUCCESS_HOVER": "#1e8f4f",
        "DANGER":        "#ff6467",
        "DANGER_HOVER":  "#ff4d50",
        "WARN":          "#fbbf24",
    },
}

THEMES = ("system", "light", "dark")   # the Settings choice; resolve_theme() turns it into a palette name
_palette = {}                          # the current palette's colours, by role
_followers = []                        # module namespaces whose colour names set_palette rebinds too


def follow(namespace):
    # For a module that did `from .theme import BG, ...`: set_palette rebinds its copies too, so
    # widgets built later read the current palette (ADR 0020). Call as theme.follow(globals()).
    _followers.append(namespace)
    namespace.update(_palette)
    namespace["CURRENT_THEME"] = CURRENT_THEME


CURRENT_THEME = None                   # "light" or "dark"


def set_palette(name):
    # Rebind the colour constants. Code reads them when it builds a widget, so set this first.
    global CURRENT_THEME
    _palette.clear()
    _palette.update(PALETTES[name])
    globals().update(_palette)
    CURRENT_THEME = name
    for namespace in _followers:
        namespace.update(_palette)
        namespace["CURRENT_THEME"] = name


set_palette("dark")

# Fonts
FONT_TITLE  = ("Segoe UI", 13, "bold")
FONT_SMALL  = ("Segoe UI", 10)
FONT_BTN    = ("Segoe UI", 10, "bold")
FONT_BTN_SM = ("Segoe UI", 10)
FONT_ICON   = ("Segoe UI Symbol", 13)   # the preset "⋯" button
FONT_MONO   = ("Cascadia Code", 10)
FONT_LOG    = ("Cascadia Code", 10)

# Window dimensions — fixed size, no manual resizing
W           = 480
H_SETUP     = 280    # height of the first-run setup screen
H_CONFIG    = 280    # height of the main config view
H_NOTICE    = 76     # added while the llama.cpp update notice shows
W_SETTINGS      = 460   # settings dialog width (its height follows the page shown)
H_RUNNING   = 750   # height of the running/log view
LOG_MAX_LINES = 5000  # lines each log pane keeps; older ones are dropped so a long session stays responsive


def theme_setting(config):
    # The saved Settings choice; anything missing or unknown is "system".
    theme = config.get("theme")
    return theme if theme in THEMES else "system"


def resolve_theme(setting, windows_light):
    # "light"/"dark" win; otherwise follow Windows. windows_light is None when the registry
    # can't be read, which counts as light (Windows' default).
    if setting in ("light", "dark"):
        return setting
    return "dark" if windows_light is False else "light"


def remap_colors(options, old, new):
    # options: {option: value} read from a widget. Returns the options whose value is a
    # colour of the old palette, each mapped to the same role's colour in the new one.
    roles = {colour: role for role, colour in old.items()}
    changed = {}
    for option, value in options.items():
        role = roles.get(str(value).lower())
        if role:
            changed[option] = new[role]
    return changed


_COLOUR_OPTIONS = ("bg", "fg", "activebackground", "activeforeground", "disabledforeground",
                   "highlightbackground", "highlightcolor", "insertbackground",
                   "selectbackground", "selectforeground", "selectcolor", "troughcolor")


def windows_light():
    # Windows' app theme: True light, False dark, None if it can't be read.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return None


def set_dark_title_bar(win, dark):
    # Dark or light Windows title bar (DWMWA_USE_IMMERSIVE_DARK_MODE). Cosmetic: never raises.
    try:
        # Borderless windows (the dropdown list) have no title bar, and the frame-changed
        # call below would drop Tk's pending move and leave them at the screen's corner.
        if win.overrideredirect():
            return
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # Windows repaints the frame only when told it changed (SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED).
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def apply_theme(root, name):
    # Switch the palette and recolour every existing widget, dialogs included. Widgets carry
    # no role: each colour option holding an old-palette colour gets that role's new colour.
    old = dict(_palette)
    set_palette(name)
    apply_ttk_style()
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        try:
            keys = set(widget.keys())
            options = {o: widget.cget(o) for o in _COLOUR_OPTIONS if o in keys}
            changed = remap_colors(options, old, _palette)
            if changed:
                widget.configure(**changed)
        except tk.TclError:
            pass  # ttk widgets: apply_ttk_style covers them
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            set_dark_title_bar(widget, name == "dark")


def apply_ttk_style():
    # Style the ttk scrollbar from the current palette (apply_theme calls this again on a switch):
    # a thin flat thumb in a flat trough, no arrow buttons.
    style = ttk.Style()
    style.theme_use("clam")
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar",
        background=BORDER, troughcolor=BG_INPUT, bordercolor=BG_INPUT,
        darkcolor=BORDER, lightcolor=BORDER, gripcount=0, width=10,
    )
    style.map("Vertical.TScrollbar", background=[("active", TEXT_MUT)],
              darkcolor=[("active", TEXT_MUT)], lightcolor=[("active", TEXT_MUT)])
