# 0023. Every launcher window draws its own title strip; dialogs name themselves inside it

Status: Accepted (supersedes 0022)

## Context

ADR 0022 replaced the main window's Windows title bar with our own strip (minimise and close only) and kept Windows' bars on dialogs. The user then asked for the dialogs' bars to go too, with each dialog's name shown inside the window instead.

Windows' title bar can't be cut down to just close and a name: it always brings the icon, and maximise comes with minimise (tested 2026-11-07). `overrideredirect(True)` was rejected in 0022 and still is: it loses the taskbar button, `iconify`, the resize edges and the border.

## Decision

Every Tk window the launcher opens draws a `TitleBar` strip (32 px) and clears `WS_CAPTION` (`theme.remove_title_bar`), which takes the icon, title and maximise with it and keeps the frame:
- Main window: minimise and close, no name.
- Dialogs (Settings, Add/Edit preset, Agent folders, Prompts, Update/Roll back, Model test): their name at the left and close only. A minimised modal dialog would leave the main window blocked behind it. Close does what the X did (`close_like_x`: Settings cancels; Update and Test refuse while working).

A press on a strip is handed to Windows as a press on a title bar (`WM_NCLBUTTONDOWN`, `HTCAPTION`), so Windows moves the window and snaps it to edges itself. Windows' own message boxes and the folder picker keep their bars: Tk can't restyle them.

## Consequences

- Tk sums a window's frame as if the title bar were still there, so after the bar goes, `geometry()` gives a client 2 px too wide (16 px for a non-resizable window) and, on the first map, a band of the bar's height. Every size set after removal goes through `theme.set_client_size` (set, read `GetClientRect`, correct once), and the view's or dialog's intended size is set again right after removal.
- Tk rebuilds the main window's style on each `resizable()` call, so the bar is removed again after each one.
- A dialog's size reads wrong at `<Map>` (Tk hasn't applied its geometry yet), so its bar is removed in `after_idle`. It can show for one frame.
- Without a title bar or resize edges, Windows 11 draws square corners with no border, so each window asks for rounded corners (`DWMWA_WINDOW_CORNER_PREFERENCE`).
- The Windows system menu (Alt+Space, right-click on a bar) and double-click to maximise are gone.
