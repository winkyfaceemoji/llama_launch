# 0022. The main window draws its own title strip: minimise and close only

Status: Superseded by 0023

## Context

The user wanted the main window's top to show only minimise and close at the top right: no icon, no title, no maximise. Windows' own title bar can't do that. With the minimise box on, Windows always draws maximise too (greyed out when off), and the caption icon is the icon Tk sets for every window, which the taskbar also uses. Tested on 2026-11-07: clearing `WS_MAXIMIZEBOX` and adding `WS_EX_DLGMODALFRAME` changed nothing visible.

Rejected: `overrideredirect(True)`. Tk's geometry is exact then, but the window loses its taskbar button, `wm iconify` refuses it, and the resize edges, border and shadow go too. Each needs its own workaround.

## Decision

The main window clears `WS_CAPTION` (`theme.remove_title_bar`), which takes the icon, title and maximise with it and keeps the frame: resize edges on the running view, the border, and the taskbar entry with its icon. A 32 px `TitleBar` strip at the top of the window holds minimise (`iconify`) and close (`_force_exit`, ADR 0016), 46 x 32 each, with Windows' own glyphs (Segoe MDL2 Assets). A press on the strip is handed to Windows as a press on a title bar (`WM_NCLBUTTONDOWN`, `HTCAPTION`), so Windows moves the window and snaps it to screen edges itself. Dialogs keep Windows' title bars.

## Consequences

- Tk rebuilds the window's style on every `resizable()` call, so the bar is removed again after each one (`_remove_title_bar`).
- Tk sizes the window for a title bar it then doesn't have and stretches the view into the freed space. So after each removal the view's intended size is set again (`H_* + H_BAR`); a fresh size from the current one keeps the blank band.
- Without a title bar or resize edges (the fixed-size home view), Windows 11 draws square corners with no border, so the window asks for rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`).
- The Windows system menu (Alt+Space, right-click on the bar) and double-click to maximise are gone from the main window.
