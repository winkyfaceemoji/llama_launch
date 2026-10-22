# 0020. The launcher is a package; launcher.pyw is only the entry point

Status: Accepted

## Context

`launcher.pyw` had grown to about 3,000 lines in one file split by `# SECTION` banners. One class did about 16 jobs. The user wanted the GUI code restructured into deeper modules, with no visible change.

## Decision

The code lives in a package, `launcher_app/`, and `launcher.pyw` keeps only the entry point: single instance, AppUserModelID, loading the config (a bad value refuses to start), the window. Installing pystray and Pillow (`_bootstrap`) runs when `widgets` is imported. The modules are `paths`, `theme`, `config`, `selection`, `processes`, `updates`, `widgets`, `dialogs`, `views` and `window`.

Two invariants keep this working:
- **Paths come from the repo root** (`paths.ROOT`), never from a module's own folder. `config.json`, the keys, `agents/` and `proxy.py` stay where they were, and `is_own_process` still recognises our processes by that folder.
- **Colour constants are rebound live.** `theme.set_palette` updates the names in every module that called `theme.follow(globals())` after `from .theme import *`. A plain `from .theme import BG` elsewhere would freeze the palette it was imported with, and break live theme switching quietly.

Rejected:
- **Keeping one file with section banners:** no churn, but 3,000 lines in one module, and the restructure's modules would still share one namespace.

## Consequences

- Tests import the package. The old `SourceFileLoader` trick is needed only by `dev/gui_shot.py`, for screenshotting pre-package versions.
- New GUI modules must follow the palette (`theme.follow`) or read colours as `theme.BG` at call time.
- The "one file with SECTION banners" convention in CLAUDE.md is replaced by "one module per area".
