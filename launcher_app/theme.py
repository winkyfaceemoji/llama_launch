"""Palettes, fonts, window sizes and live theme switching (ADR 0014, 0020)."""
from tkinter import ttk
import ctypes
from ctypes import wintypes
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
FONT_CAPTION = ("Segoe MDL2 Assets", 8)  # the title strip's minimise and close glyphs, as Windows draws them
FONT_MONO   = ("Cascadia Code", 10)
FONT_LOG    = ("Cascadia Code", 10)

# Window dimensions — fixed size, no manual resizing
W           = 480
H_SETUP     = 280    # height of the first-run setup screen
H_CONFIG    = 280    # height of the main config view
H_NOTICE    = 76     # added while the llama.cpp update notice shows
W_SETTINGS      = 460   # settings dialog width (its height follows the page shown)
H_RUNNING   = 750   # height of the running/log view
H_BAR       = 32    # the main window's own title strip (ADR 0022), added to each view's height
LOG_MAX_LINES = 5000  # lines each log pane keeps; older ones are dropped so a long session stays responsive


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


def remove_title_bar(win):
    # The main window draws its own minimise and close (ADR 0022). Clearing WS_CAPTION takes the icon,
    # title and maximise with it and keeps the frame: resize edges and the taskbar entry. Tk rebuilds the
    # window's style on resizable(), so call this again after each one. Returns True when it removed the
    # bar: Tk sized the window with the bar in it, so the caller sizes it again or a blank band shows
    # where the bar was. Asks for rounded corners too: without a title bar or resize edges (the fixed-size
    # home view), Windows 11 would draw square ones with no border. Cosmetic: never raises.
    # A window made fixed-size by set_resizable also loses its resize edges and maximise here.
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id())
        style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
        if not style & 0x00C00000:  # WS_CAPTION
            return False
        remove = 0x00C00000
        if getattr(win, "_fixed_size", False):
            remove |= 0x00040000 | 0x00010000  # WS_THICKFRAME, WS_MAXIMIZEBOX
        user32.SetWindowLongW(hwnd, -16, style & ~remove)
        # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED, so Windows redraws the frame now.
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        round_corners = ctypes.c_int(2)  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_corners), ctypes.sizeof(round_corners))
        return True
    except Exception:
        return False


def set_resizable(win, resizable):
    # Use instead of win.resizable() to make a window fixed-size, or resizable again, once its title bar
    # comes off (a dialog that is always resizable can keep Tk's default). Tk is always told the
    # window is resizable; a fixed one loses its resize edges in remove_title_bar instead. Tk pins a
    # window it thinks is fixed to its width plus the frame of the title bar it thinks is still there
    # (16 px more): Windows applied that on every step of a drag, Tk read the wider window back, and the
    # window grew 16 px per step. Like resizable(), it rebuilds the style: remove the title bar after it.
    win._fixed_size = not resizable
    win.resizable(True, True)


def set_client_size(win, width, height):
    # Size a window's inside to exactly width x height. Once remove_title_bar has run, Tk's sum for the
    # frame no longer matches Windows' (the client came out 2 px wider, 16 for Settings), so set it,
    # read the real client size, and correct once. Before that, the first set is already exact.
    win.geometry(f"{width}x{height}")
    win.update_idletasks()
    try:
        rect = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(ctypes.windll.user32.GetParent(win.winfo_id()), ctypes.byref(rect))
        dx, dy = rect.right - width, rect.bottom - height
    except Exception:
        return
    if dx or dy:
        win.geometry(f"{width - dx}x{height - dy}")
        win.update_idletasks()


def start_window_drag(win):
    # Hand a press on the title strip to Windows as a press on a title bar, so Windows moves the window
    # (and snaps it to screen edges) exactly as it would for a native one. Posted, not sent: Windows'
    # move loop then runs from Tk's own event loop. Sent from here, it ran inside this ctypes call with
    # Python's lock released, and any Tk timer or event that fired during the drag killed the process.
    # Never raises.
    try:
        user32 = ctypes.windll.user32
        user32.ReleaseCapture()
        user32.PostMessageW(user32.GetParent(win.winfo_id()), 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
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
