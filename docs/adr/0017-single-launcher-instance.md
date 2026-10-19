# 0017. One launcher at a time; a second start shows the first

Status: Accepted

## Context

All four servers use fixed ports, so only one launcher can own them. A second launcher used to start anyway, and launching from it silently killed the first launcher's servers (`_free_port` treats them as our own leftovers). The first launcher stayed open with dead process handles, and Exit Processes there stopped nothing (see ADR 0016).

## Decision

At startup, before building any window, `claim_instance` creates a named event and then a named mutex (`Local\LlamaLauncher`) in the user's Windows session. If the mutex already exists, the new launcher signals the event and exits; the first launcher's watcher thread brings its window up (deiconify, lift, focus). The second one calls `AllowSetForegroundWindow` first, so Windows lets the first take the foreground.

Rejected:
- **Keep allowing a second launcher, but say whose servers it stops:** still leaves two launchers, one of them with dead handles.
- **A lock file or a localhost port:** a lock file outlives a crash and needs stale-lock handling; a port is one more fixed port. Windows frees a mutex when its process dies.

## Consequences

- The check is global to the Windows session, not per folder: a launcher run from a git worktree while the main one runs just shows the main one and exits.
- The check lives in the entry point only; `dev/gui_shot.py` and the tests build `CommandLauncherGUI` directly and skip it.
- A launcher older than this change doesn't create the mutex, so it doesn't block a new one.
