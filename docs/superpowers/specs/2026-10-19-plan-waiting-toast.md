# Plan waiting toast: design

Agreed in a grill session on 2026-10-19. Terms are in [CONTEXT.md](../../../CONTEXT.md). It came out of an LLM council on the Jobs button (`panel/inject.js`): in-page cues only reach the user while the Web UI tab is open and in view, so a plan that waits while the tab is closed needs a notice outside the page. The button redesign itself is a separate piece of work.

## Goal

When an organise plan is waiting for approval, the launcher shows a Windows toast, so the user hears about it even with the Web UI tab closed. Plans are still approved only in the Jobs panel (ADR 0007, 0009); the toast only points there.

## Facts this rests on

- The launcher can't tell whether the Web UI tab is open or in view, so the toast doesn't try to: it shows whenever the servers run.
- pystray (0.19.5 installed; `_bootstrap` installs the latest, unpinned) has `Icon.notify(message, title)`. On Windows it sets `NIF_INFO` on the tray icon, which Windows 11 shows as a toast under Python's name (there's no app ID). A new toast replaces the one on screen. Do Not Disturb hides it.
- pystray ignores clicks on the toast: its `_on_notify` only handles `WM_LBUTTONUP` and `WM_RBUTTONUP` on the icon. A toast click arrives there as `NIN_BALLOONUSERCLICK` (`WM_USER + 5`, `0x405`).
- `GET /jobs` on the agents server (`panel_api.jobs_payload`) needs `Authorization: Bearer <approval_key.txt>` and `Host: 127.0.0.1:8002` (`llm.check_request`), as `TestDialog._stop_sequence` already sends. It already hides test jobs and always lists jobs in `awaiting_approval`, with the full `plan`. Its `title` is "goal — folder name"; the folder path isn't in the payload.
- Exit Processes (`cancel_unfinished`) leaves `awaiting_approval` jobs as they are, so a plan can still be waiting at the next launch.
- Only organise jobs reach `awaiting_approval`.

## Decisions

- **A Windows toast only** (1B), through pystray's `notify`. The tray icon stays as it is.
- **Shown whenever the servers run** (2B), whether the window is open or hidden to the tray.
- **The launcher reads `GET /jobs` with the approval key** (3B), so it sees exactly what the panel sees.
- **Clicking the toast opens `http://127.0.0.1:8080/`** (4B), always in a new tab, as `webbrowser.open` does. The Jobs panel opens or not as the user left it; no URL flag in `inject.js`.
- **One toast per job ID per launcher session** (5A), kept in memory. A plan still waiting at the next launch gets one more toast; nothing is saved to disk.
- **The toast names the folder and size** (6B): title "Plan waiting for approval", message `Organise <folder>: <n> changes waiting for approval`, where `<n>` is the number of operations in the plan.
- **A poll thread of its own, every 10 s** (7C), so a slow `/jobs` can't delay the health label.
- **Several new plans in one poll get one combined toast** (8A): `<N> plans waiting for approval`, with no folder names, because a second toast would replace the first.
- **A Settings toggle, "Notify when a plan is waiting"** (9B), Yes / No like Open browser, saved as `notify_plans` in `config.json`, on by default.

## Components

### Agents server (`agents/panel_api.py`)

- `jobs_payload` adds `folder` (`args.folder`, the path as given) to each `awaiting_approval` item. The panel ignores it.

### Launcher (`launcher.pyw`)

- **SECTION 3, a pure helper** `plan_toast(jobs, seen)`: from a `/jobs` list and the set of job IDs already toasted, returns `(title, message)` or `None`, plus the IDs to add to `seen`. Only `awaiting_approval` jobs count. One new plan gives the folder message, two or more give the combined one. A plan with no list in `plan` counts as 0 changes.
- **The plan poll** (SECTION 8, next to `_start_health_poll`): a daemon thread started with the health poll and stopped with it (its own flag, cleared in `_stop_health_poll`). Every 10 s it reads `approval_key.txt`, calls `GET /jobs` (timeout 5 s), and passes the reply to `plan_toast`. The seen set lives on the app object for the whole launcher session, so a second Launch in the same session doesn't repeat toasts.
  - While `notify_plans` is off, it still adds waiting plans to the seen set, so turning it back on doesn't bring up old plans.
  - Every failure (agents server down, key refused, bad JSON, no key file) is silent: no toast, no log line, and the next poll tries again. The agents feature is optional.
  - It never touches Tk: `notify` is a Win32 call on the tray icon, and the config value is read as a plain dict lookup.
  - No poll when the tray isn't available (`_TRAY_AVAILABLE` false) or `_agents_error` is set.
- **The toast click** (SECTION 8, `_setup_tray`): the tray icon is a small subclass of `pystray.Icon` whose `_on_notify` opens the Web UI on `NIN_BALLOONUSERCLICK` (handed to the UI thread with `after(0, ...)`, as the health poll does) and passes everything else to pystray's own handler. If a future pystray renames `_on_notify`, toasts still show and clicking them does nothing.
- **Settings** (SECTION 7): a "Notify when a plan is waiting" row with Yes / No buttons, styled and saved like Open browser. Hidden when the tray isn't available. Existing configs have no `notify_plans` key, so the launcher reads it with `config.get("notify_plans", True)`; `_DEFAULT_CONFIG` gets it too.

## Security

- The approval key goes only to `127.0.0.1:8002`, in the header, as the model test's Stop already sends it. It is never shown or logged.
- The toast text is plain text from the plan's folder and operation count; the folder is the job's argument, which `Policy` already limits to the listed folders.
- The toast only opens the Web UI. Approving still happens only in the Jobs panel, which shows the whole plan.

## Out of scope

- A tray icon badge or a changed tray image.
- Telling whether the Web UI tab is open, or opening the Jobs panel from the toast.
- Reminders while a plan keeps waiting.
- The Jobs button redesign in `panel/inject.js`.

## Testing

- **Unit tests** (`tests/`, launcher loaded with `SourceFileLoader`):
  - `plan_toast`: one new plan, several new plans, plans already seen, non-waiting jobs ignored, a missing or bad `plan`;
  - `jobs_payload` includes `folder` for `awaiting_approval` jobs only;
  - the tray subclass's `_on_notify` override still matches a method on `pystray.Icon` (skipped when pystray isn't installed).
- **Live check, with the user's go-ahead** (it needs the launcher stack, so the model is loaded): start an organise job from chat, hide the launcher to the tray, close the Web UI tab; the toast appears once within about 10 s of the plan being proposed, clicking it opens the Web UI, and approving from the panel works as before. Turn the setting off and check that a second plan gives no toast.

## Docs

- [ADR 0015](../../adr/0015-plan-waiting-toast.md) records the rejected alternatives (written with this spec).
- `.claude/rules/launcher.md`: the plan poll (own thread, silent failures, seen set) and the `_on_notify` override.
- `.claude/rules/agents.md`: `folder` in the `/jobs` payload.
- README: the Settings row.

## Workflow

Branch `plan-toast`. Merge to `main`, push, and delete the branch when it's done and verified. No PR.
