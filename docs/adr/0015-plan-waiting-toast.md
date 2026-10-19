# 0015. A waiting plan gets a Windows toast from the launcher

Status: Accepted

## Context

The Jobs button in the Web UI shows when an organise plan needs review, but only while that tab is open and in view. A plan proposed while the tab is closed can wait unnoticed. The launcher runs the whole time and has a tray icon, but it can't tell whether the Web UI tab is open.

## Decision

The launcher polls the agents server's `GET /jobs` every 10 s on its own thread, with the approval key, and shows a Windows toast through pystray for each plan that starts waiting for approval: once per job ID per launcher session, whenever the servers run. Clicking it opens the Web UI. A Settings toggle turns it off. Spec: `docs/superpowers/specs/2026-10-19-plan-waiting-toast.md`.

Rejected:
- **A tray icon badge or changed image:** quiet and lasting, but easy to miss in the overflow area, which is where the icon usually sits.
- **Reading the job files directly with `JobStore`:** no key and no HTTP, but it would repeat `/jobs`' test-job hiding, and two views of "waiting" could drift apart.
- **A separate lightweight route for waiting job IDs:** cheaper to poll, but one more guarded route for a payload that's small anyway.
- **Saving which plans were toasted, or repeating reminders:** saving needs state on disk that has to be pruned; reminders nag. A plan still waiting at the next launch gets one more toast instead.
- **Opening the Jobs panel from the toast:** needs a URL flag in `inject.js`; the toast opens the Web UI and the panel stays as the user left it.

## Consequences

- Clicking the toast relies on overriding pystray's private `_on_notify`. If pystray changes it, toasts still show and clicks do nothing.
- Windows shows the toast under Python's name, and Do Not Disturb hides it.
- Approval still happens only in the Jobs panel (ADR 0007, 0009).
