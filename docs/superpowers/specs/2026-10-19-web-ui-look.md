# Launcher and Jobs panel look like the Web UI: design

Agreed in a grill session on 2026-10-19. Terms are in [CONTEXT.md](../../../CONTEXT.md).

## Goal

Make the launcher window, its dialogs and the Jobs panel look like the llama Web UI: neutral greys, flat, quiet, in the restrained style of Claude and ChatGPT, light or dark to match.

## Facts this rests on

- The launcher is Tk with a Tokyo Night palette in SECTION 1 of `launcher.pyw`; colours are passed to each widget when it is created (`bg=`, `fg=`), and `ttk` is only used for the scrollbar.
- The Web UI's source is `tools/ui` in llama.cpp (read at commit `797de63`). It uses shadcn tokens, all neutral grey (light / dark):
  - background `#ffffff` / `#0d0d0d`; card `#ffffff` / `#171717`; muted `#f5f5f5` / `#262626`
  - foreground `#0a0a0a` / `#fafafa`; muted-foreground `#737373` / `#a1a1a1`
  - border `#d6d6d6` / white at 30%; input `#e4e4e4` / white at 30%
  - primary `#171717` / `#e5e5e5`; primary-foreground `#fafafa` / `#171717`
  - accent `#eeeeee` / `#262626`; destructive `#e7000b` / `#ff6467`
  - Font is the system UI font (Segoe UI on Windows); text is mostly 14px.
- The Web UI's theme setting is System / Light / Dark (default System); `mode-watcher` puts a `dark` class on the page's `<html>`.
- The Jobs panel is an iframe that `panel/inject.js` adds to the Web UI page. `panel/panel.css` and the Jobs button styles in `inject.js` use a warm Claude palette and follow `prefers-color-scheme`.
- Windows' app theme is `AppsUseLightTheme` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` (`winreg`). A dark title bar is `DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE = 20, ...)` through `ctypes`. Both are standard library.

## Decisions

- **Restyle the existing Tk window** (1A). Standard library only. Rejected: rebuilding it as HTML in a webview, and moving its controls into the Web UI (the proxy isn't running before Launch).
- **Neutral greys from the Web UI** (round 2, 1A), light and dark palettes in SECTION 1, replacing Tokyo Night. Fonts stay Segoe UI and Cascadia Code.
- **Flat widgets with hairline borders** (2A). No rounded corners drawn on a Canvas.
- **Theme follows a System / Light / Dark setting, live** (round 4, 1C). It is a new Settings row saved in `config.json` (default System). In System mode the launcher reads the Windows setting every few seconds and, when it changes, recolours the main window and every open dialog. The title bar goes dark in dark mode.
- **Layout loosens, order stays** (3B):
  - The 54px header bar goes. A plain title row on the main background replaces it: "Llama Launcher" on the left and a ghost Settings button on the right (round 3, 1A).
  - Model and Preset sit in boxes with a 1px border on the window background (2A).
  - Section labels and log pane headers become muted sentence case ("Model", "llama-server") with no blue stripe.
  - Side padding goes to about 24px and rows get a little more space; the fixed heights in SECTION 1 grow to fit. The row order and window width stay the same.
- **Buttons are monochrome, except Launch and Exit** (round 3, 3B). Ghost and outline buttons everywhere else, Test included. Launch is a filled muted green tuned to each theme; Exit is filled with the destructive red. Delete keeps red text.
- **All dialogs get the full treatment** (round 4, 3A): Settings, Preset editor, Folders, Update and Test.
- **The Jobs panel moves to the same neutral greys** (round 2), in both `panel.css` and the Jobs button styles in `inject.js`. Its status chips (amber, green, red) stay, retuned to sit on the greys (round 4, 2A).
- **The Jobs panel follows the Web UI's own theme** (round 5, 1B). `inject.js` watches the `dark` class on the page's `<html>` and posts it to the iframe; the panel switches palette on that message rather than on `prefers-color-scheme`.
- **Tray icon unchanged.**

## Out of scope

- Rounded corners, shadows, blur.
- A sidebar layout, or any change to what the launcher does or the order of its rows.
- Changing the Web UI itself, or driving its theme from the launcher.

## Open points for the plan

- The recolouring mechanism. One option: widgets keep a role (`"bg"`, `"muted"`, ...) rather than a hex value, and a walk over the widget tree reapplies the current palette. The fixed hex values in `small_btn`, `card`, `DropdownButton` and the dialogs all have to go through it.
- Where the pure parts go (theme resolution from the setting and registry value, palette lookup) so they get unit tests; the GUI, registry and DWM calls stay untested.
- What the panel shows before its first theme message (likely `prefers-color-scheme` as a fallback), and the `postMessage` origin check (`location.origin`, as the existing messages use).
- Contrast of muted text and the retuned chips in both themes.
- Docs:
  - an ADR for the rejected approaches (a webview rewrite, controls in the Web UI, Canvas rounded widgets);
  - `.claude/rules/launcher.md` (theme and recolouring) and `.claude/rules/panel.md` (theme message);
  - README (the Settings row).
