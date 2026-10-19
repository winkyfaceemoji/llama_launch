# 0016. Closing the window stops the servers and exits

Status: Accepted

## Context

Closing the window while servers ran used to hide the launcher to the tray and leave all four servers running. The user took the closed window as an exit, found servers still running, and started the launcher again. The new launcher's `_free_port` then killed the old one's servers as our own leftovers, so the old launcher stayed in the tray holding dead handles: its Exit Processes stopped nothing, and closing it hid it again. On 2026-10-19 three launchers were alive at once, two of them with no servers left.

## Decision

The window's X does what the tray's Exit does: `_force_exit` stops every server and exits. Nothing hides the window to the tray any more; the tray icon keeps Show and Exit, and minimising goes to the taskbar as usual.

Rejected:
- **Hide to the tray while servers run (the old behaviour):** keeps servers up with no window, but a closed window reads as an exit, which is how the leftovers built up.
- **Ask on every close:** keeps both choices, at one more click on every close.

## Consequences

- To keep the servers running, minimise the window instead of closing it.
- Plan toasts (ADR 0015) come only while the launcher runs, which is now only while its window is open or minimised.
- A second launcher is handled separately (ADR 0017).
