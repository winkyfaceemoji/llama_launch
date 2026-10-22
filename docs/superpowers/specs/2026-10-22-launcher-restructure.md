# Launcher restructure: a package of deeper modules

Agreed with the user on 2026-10-22, after the layout work ([layout spec](2026-10-21-launcher-layout.md)). Vocabulary from `codebase-design`: module, interface, seam, depth. Terms are in [CONTEXT.md](../../../CONTEXT.md).

## Goal

The same launcher, **with no visible change**, but with its code in modules that each hide a lot behind a small interface:
- `CommandLauncherGUI` was about 1,080 lines doing about 16 jobs.
- `_build_home_screen` was a 220-line method building widgets for every other part.
- "Running" was read from whether the Exit button was on screen.
- `config` was changed and saved from seven places.

## Decisions

- **A package, `launcher_app/`** ([ADR 0020](../../adr/0020-launcher-package.md)). `launcher.pyw` is only the entry point. The modules:
  - `paths`: file locations, from the repo root rather than from the module's own folder.
  - `theme`: palettes, fonts, sizes, live theme switching.
  - `config`: the config store.
  - `selection`: models and presets.
  - `processes`: the pure process helpers (health text, port parsing, own-process check, single instance, test lock).
  - `updates`: llama.cpp updates.
  - `widgets`: buttons, cards, popup menu, dropdown, log helpers, tray image.
  - `dialogs`: Preset, Settings, Folders, Prompts, Update, Test.
  - `views`: setup, home, running.
  - `window`: the app.
- **The config store checks values and refuses to start on a bad one.**
  - `ConfigStore.load()` checks every key it knows: `llama_dir`, `presets` (through `load_presets`), `open_browser`, `theme`, `uvx_command`, `last_model`, `previous_build`, `system_prompt`, `agent_prompts`. A bad value raises `ConfigError`, naming the key and the problem. The entry point shows it and exits, and nothing is rewritten.
  - Keys it doesn't own (`mcpServers` and any others) pass through every save untouched.
  - Changes go through `update(**changes)`, which checks each value before writing.
  - A fallback with a warning was rejected: the next save of anything would have written over the real value, losing it.
- **Selection is a stateful module that saves through callbacks** (design "B3").
  - It has `pick_model`, `pick_preset`, `refresh`, `set_folder`, `add_preset`, `edit_preset`, `delete_preset`, `model_names` and `preset_for_edit`. Each returns a `View`: what both dropdowns show and the launch command.
  - The command is derived on every change, never stored, so the Refresh bug class can't return.
  - `remember_model` and `save_presets` callbacks write through the config store.
  - Refusals raise `PresetError(kind, title, message)`, and the window shows them as today.
- **Views are rebuilt on every switch, and their actions are typed methods on the window** (a hybrid of designs "C1" and "C3").
  - `SetupView`, `HomeView` and `RunningView` take the window and call its methods: `launch()`, `stop()`, `pick_model(name)`, `refresh_models()`, `open_settings()`, `open_preset_menu()` and so on.
  - Switching views destroys the old one and builds the new one, so a fresh running view can't carry dots, labels or an open Connection row over from the last run.
  - There is no separate controller class: the window is the controller.
- **Process control stays in the window** ([ADR 0021](../../adr/0021-process-control-stays-in-window.md)): launch, `_free_port`, `_kill_proc`, `_kill_all`, the health poll and the model test, with their ordering rules, untouched. The only change is a real `running` flag in place of `exit_btn.winfo_manager()`.

## Order (each step committed and checked on its own)

1. **Skeleton.**
   - Move the code into the package unchanged. The colour constants must keep switching live: `theme.set_palette` also updates the names in every module that imported them (`theme.follow(globals())`).
   - Point the paths at the repo root.
   - Tests and the screenshot script import the package.
   - Check: all tests pass, and every screenshot is identical to the baseline.
2. **Config store**, test first. Also check that the user's real `config.json` loads.
3. **Selection**, test first, through its interface only.
4. **Views and the window**: rebuilt on switch, typed methods, and the `running` flag.
5. **Finish**: identical screenshots in both themes, a two-axis review, rules, README, CLAUDE.md, then merge to `main`.

## Must not change

- Every ordering rule in `.claude/rules/launcher.md`: the kill order, the health poll stopped before kills, `set_palette` before any widget, `claim_instance` and the AppUserModelID before `tk.Tk()`, tkinter only on the UI thread.
- Nothing in the code reads a colour at import time, so live theme switching keeps working.
- The paths the processes and `is_own_process` see: the launcher folder is still the repo root.
