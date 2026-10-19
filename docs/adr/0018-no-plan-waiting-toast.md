# 0018. No toast for a waiting plan; the Jobs button count is the only cue

Status: Accepted (supersedes 0015)

## Context

ADR 0015 planned a Windows toast from the launcher whenever an organise plan starts waiting for approval, so the user would hear about it with the Web UI tab closed and the launcher hidden in the tray. It was built on branch `plan-toast` (55729c3, 47de80d, edefad1; not merged) and live-checked on 2026-10-19:

- pystray's tray balloon (`Icon.notify`) showed unreliably on Windows 11: the same call from a plain test icon and from the launcher's icon popped up on some runs and not others, with Do Not Disturb off, and the launcher's own plan toasts never showed.
- ADR 0016 took away the case the toast was for: closing the window now stops the servers and exits, so the launcher only runs while its window is open or minimised.

## Decision

No plan toast. A waiting plan is shown only by the Jobs button in the Web UI, whose count is for plans to review. `GET /jobs` keeps its shape; the `folder` field from the branch was not merged.

Rejected:
- **Keep the pystray toast as built:** it would show only while the window is open or minimised, and Windows didn't show it reliably.
- **Flash the launcher's taskbar button:** dependable, but it would add a poll thread and the approval key in the launcher for a cue that the Jobs button mostly gives already.
- **A native Windows toast (WinRT):** shows more reliably and stays in the notification list, but needs an app ID and much more code for the same reduced case.

## Consequences

- A plan proposed while no Web UI tab is open waits until the user next opens the Web UI and sees the Jobs button.
- The launcher sends the approval key only for the model test's Stop.
- Bringing a notice back means starting from branch `plan-toast` and choosing a display that works (see the rejected options above).
