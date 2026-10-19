# Web UI Look Implementation Plan

> **For agentic workers:** implement task by task, test first (`tdd`) where there is logic, one implementer subagent per task plus a reviewer. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The launcher, its dialogs and the Jobs panel look like the llama Web UI: neutral greys, flat hairline boxes, quiet sentence-case labels, monochrome buttons apart from Launch (green) and Exit (red). Light or dark follows a System / Light / Dark setting, live.

**Architecture:**
- **Palettes** (`launcher.pyw` SECTION 1): `PALETTES = {"light": {...}, "dark": {...}}`, keyed by the existing constant names (`BG`, `BG_PANEL`, `TEXT_MUT`, ...). `set_palette(name)` rebinds those module globals, so the ~140 existing uses keep working unchanged.
- **Live recolouring** (SECTION 4): `apply_theme(root, name)` switches the palette, restyles ttk, then walks the root and every `Toplevel`. For each widget it swaps any colour option whose value is an old-palette colour for the same role's new colour (`remap_colors`, pure). This needs every colour to be unique within a palette, which a test enforces. Widgets carry no role tags.
- **Theme choice:**
  - `resolve_theme(setting, windows_light)` is pure; `windows_light()` reads the registry.
  - In System mode a 3-second `after` poll calls `apply_theme` when the result changes.
  - `set_dark_title_bar(win, dark)` makes the Windows title bar dark through `ctypes`/DWM.
- **Jobs panel:** `inject.js` watches the Web UI's `<html class="dark">` and posts `{type: "agent-jobs-theme", dark}` to the iframe. `panel.js` sets `light`/`dark` on its own `<html>`, and `panel.css` keys its tokens on those classes, falling back to `prefers-color-scheme` until the first message arrives.

**Tech stack:** Python standard library (`tkinter`, `winreg`, `ctypes`), plain JS/CSS. Pillow (already a dependency) for the screenshot script.

**Spec:** `docs/superpowers/specs/2026-10-19-web-ui-look.md`.

## Global constraints

- **Branch:** `web-ui-look`. At the end, merge to `main`, push and delete the branch. No PR. No Claude/Anthropic attribution in commits.
- **Ports:** never bind or touch 8001, 8002, 8080 or 8081. The panel check runs `proxy.py` on `18080` with a fake upstream on `18081`. Stop whatever you start.
- **Never click Launch, and never start llama-server, the agents server or a model test.** The screenshot script builds the GUI and can show the running layout without starting processes (`_enter_running_state` only changes the layout).
- **Building the GUI clears the model test lock** (`CommandLauncherGUI.__init__` calls `clear_test_lock`). Before running the screenshot script or `launcher.pyw`, check that `model_test.lock` doesn't exist. If it does, a test is running: stop and ask.
- **No colour defaults in signatures.** `flat_btn(..., color=ACCENT)` binds the colour once, when the function is defined, so it would stay stale after a switch. Use `color=None` and look the colour up at call time. The same goes for any colour captured in a closure or thread at start, such as `colors` in `_start_health_poll`.
- **Every palette colour is lowercase `#rrggbb` and unique within its palette.** That's what makes `remap_colors` unambiguous. Where two roles should look the same (window and box background), they differ by one step in one channel.
- **Code layout:** palettes and helpers in SECTION 1 and 4; dialogs in 6 and 7; home screen in 8. No new sections.
- **Tests:** `python -m unittest discover -s tests`. Syntax: `python -m py_compile launcher.pyw proxy.py`.
- **Subjective look is the user's call** (global §10). Each look task ends with before/after screenshots in both themes sent to the user; no reviewer round decides the look. Tuning is capped at 3 rounds (`Round N/3` in this file), then report.

## Review focus

1. **A theme switch reaches everything:**
  - widgets created before and after it;
  - the DropdownButton popup;
  - dialogs that are open at the time;
  - Text and Entry insert cursors, the Listbox selection, the Checkbutton `selectcolor`;
  - the health label's next update.
2. **Defaults and closures that capture a colour** at definition time (see the constraint above).
3. **The panel only trusts its parent:** the new message type goes through the existing `e.origin === location.origin && e.source === window.parent` check, and `inject.js` posts with `location.origin`.
4. **Theme code can't break startup:** a registry read or DWM call that fails falls back (light, no dark title bar). It must not stop the launcher.
5. **Contrast:** primary and muted text on the window, box and input backgrounds, and button text on green, red and primary, in both themes (tested numbers, Task 1).

---

## File structure

| File | Change |
|---|---|
| `launcher.pyw` | Palettes and theme helpers (S1, S4); restyled helpers and DropdownButton (S4, S5); dialogs (S6, S7); home and setup screens, theme poll (S8) |
| `tests/test_launcher.py` | Palette, contrast, `resolve_theme`, `remap_colors`, `theme` config tests |
| `dev/gui_shot.py` (new) | Screenshot script: builds the GUI from a given `launcher.pyw`, captures the views as PNGs |
| `panel/inject.js`, `panel/panel.js`, `panel/panel.css` | Neutral palette, theme message |
| `docs/adr/0013-...md`, `CLAUDE.md`, `.claude/rules/launcher.md`, `.claude/rules/panel.md`, `README.md` | Docs |

---

### Task 0: Screenshot script and "before" shots

**Files:** `dev/gui_shot.py`

- [ ] Write `dev/gui_shot.py <launcher.pyw path> <out dir> [--theme light|dark]`. It:
  - loads the file with `SourceFileLoader`;
  - refuses to run while `model_test.lock` exists;
  - builds `CommandLauncherGUI` on a `Tk` root and calls `set_palette`/`apply_theme` when present (the old file has neither);
  - after the window settles, saves `ImageGrab` captures of the home view, Settings (opened through `_open_settings`) and the running layout (`_enter_running_state`, a few sample log lines inserted, no processes);
  - destroys the window without calling `_on_close` (nothing to kill).

  It uses relative paths only and writes nothing into the repo.
- [ ] Take the "before" shots from `main`'s `launcher.pyw` into the scratchpad.
- [ ] Commit the script: `dev: gui screenshot script`.

### Task 1: Palettes and pure theme helpers

**Files:** `launcher.pyw` (S1, S4), `tests/test_launcher.py`

- [ ] Write failing tests first, then implement:

```python
PALETTES = {"light": {...}, "dark": {...}}   # same keys; the old constants plus SUCCESS_HOVER, DANGER_HOVER, WARN, HOVER
THEMES = ("system", "light", "dark")

def set_palette(name):            # rebinds the module globals from PALETTES[name]; returns name
def resolve_theme(setting, windows_light):
    # "light"/"dark" win; "system" or anything unknown -> "light" if windows_light is not False, else "dark".
    # windows_light None (registry unreadable) counts as light.
def remap_colors(options, old, new):
    # options: {option_name: value} as read from a widget. Returns only the options whose value
    # (case-insensitive) is a colour in `old`, mapped to the same role in `new`.
def contrast(fg, bg):             # WCAG contrast ratio of two #rrggbb colours
```

- [ ] Tests:
  - both palettes have the same keys, all lowercase `#rrggbb`, unique within each palette;
  - `TEXT_PRI` on `BG`, `BG_PANEL` and `BG_INPUT` ≥ 7;
  - `TEXT_MUT` on those three ≥ 4.5;
  - `BG` text on `SUCCESS`, `DANGER` and `ACCENT` ≥ 4.5;
  - every `resolve_theme` case;
  - `remap_colors` leaves non-palette values (`""`, `"SystemButtonFace"`, a hex not in the palette) alone;
  - `config.get("theme")` missing or invalid resolves as system.
- [ ] Starting values. Tokens are from the spec; Task 3 tunes them with the user.

| Role | Light | Dark |
|---|---|---|
| `BG` | `#ffffff` | `#0d0d0d` |
| `BG_PANEL` (boxes) | `#fefefe` | `#0e0e0e` |
| `BG_INPUT` | `#f5f5f5` | `#171717` |
| `HOVER` | `#eeeeee` | `#262626` |
| `BORDER` | `#e5e5e5` | `#2e2e2e` (white at 30% over `BG` is `#565656`, too heavy for a hairline without alpha) |
| `ACCENT` (primary) | `#171717` | `#e5e5e5` |
| `TEXT_PRI` | `#0a0a0a` | `#fafafa` |
| `TEXT_SEC` | `#404040` | `#d4d4d4` |
| `TEXT_MUT` | `#737373` | `#a1a1a1` |
| `SUCCESS` / `SUCCESS_HOVER` | `#15803d` / `#166534` | `#4ade80` / `#22c55e` |
| `DANGER` / `DANGER_HOVER` | `#e7000b` / `#c10007` | `#ff6467` / `#ff4d50` |
| `WARN` | `#b45309` | `#fbbf24` |

- [ ] Module start calls `set_palette("dark")` so nothing changes visually yet. Commit: `launcher: light and dark palettes, theme helpers`.

### Task 2: Live theme switching and the Settings row

**Files:** `launcher.pyw` (S4, S7, S8)

- [ ] `apply_theme(root, name)`:
  - `old = current palette`, `set_palette(name)`, `apply_ttk_style()`;
  - walk `root` and all descendants, Toplevels included;
  - for each widget, read the colour options it supports (`bg`, `fg`, `activebackground`, `activeforeground`, `disabledforeground`, `highlightbackground`, `highlightcolor`, `insertbackground`, `selectbackground`, `selectforeground`, `selectcolor`, `troughcolor`), skipping ones it lacks (`TclError`), and `configure(**remap_colors(...))`;
  - `set_dark_title_bar` on each Toplevel and the root.
- [ ] `set_dark_title_bar(win, dark)`: `DwmSetWindowAttribute(GetParent(win.winfo_id()), 20, c_int(dark), 4)`, all in `try/except`.
  - Open point: whether Windows repaints the title bar right away. If not, force a frame change (`SetWindowPos` with `SWP_FRAMECHANGED`) and verify by screenshot.
  - Every dialog calls it once in `__init__` after `update_idletasks()`.
- [ ] `windows_light()`: `winreg` read of `AppsUseLightTheme`; `None` on any error.
- [ ] `CommandLauncherGUI` applies the saved `theme` before building any screen. In System mode it runs a 3-second `after` poll that calls `apply_theme` only when `resolve_theme` changes. The poll runs on the UI thread, never from a background thread.
- [ ] Remove colour defaults from signatures (`flat_btn`, `small_btn`, `DropdownButton`), and make the health label look up `SUCCESS`/`WARN`/`DANGER`/`TEXT_MUT` when it updates, not when the poll starts.
- [ ] Settings: a **Theme** row under Launch options with three buttons, System / Light / Dark. They are styled like the Web UI Yes/No pair. A click applies at once and saves `theme` to `config.json`, like Web UI does today. `H_SETTINGS` and `H_SETTINGS_UVX` grow by one row.
- [ ] Check by hand: switch the theme with Settings open and the model dropdown open; switch Windows' setting while in System mode. Commit: `launcher: system, light and dark theme, switched live`.

### Task 3: Home and setup screens

**Files:** `launcher.pyw` (S4, S8)

- [ ] `section_label`: no stripe, `FONT_SMALL` in `TEXT_MUT`, sentence case. Callers change their text (`"MODEL"` → `"Model"`, `"LLAMA.CPP FOLDER"` → `"llama.cpp folder"`, and so on); `UpdateDialog` drops `title.upper()`.
- [ ] `card`: a 1px `BORDER` frame around `BG_PANEL`.
- [ ] `small_btn` becomes a ghost button (bg `BG`, hover `HOVER`). Add an outline variant: a 1px `BORDER` frame around a ghost button, since a Tk button on Windows draws no border of its own. Test uses it.
- [ ] Header bar removed in both `_build_setup_screen` and `_build_home_screen`. It's replaced by a title row on `BG`: "Llama Launcher" (`FONT_TITLE`, `TEXT_PRI`) on the left and a ghost "Settings" button on the right. The `#13141f` values go.
- [ ] Launch: filled `SUCCESS`, hover `SUCCESS_HOVER`. Exit: filled `DANGER`, hover `DANGER_HOVER`. The literal hover hexes go.
- [ ] Log pane headers: muted sentence case, no stripe; log text on `BG_INPUT`.
- [ ] Padding: 16 → 24 at the window edges, slightly more between rows. Fix `H_SETUP`, `H_CONFIG`, `H_NOTICE` and `H_RUNNING` so nothing clips, in both themes.
- [ ] Screenshots (Task 0 script), before/after in both themes, sent to the user. Tune palette values and spacing until they're happy (Round N/3). Commit: `launcher: web ui layout and buttons`.

### Task 4: Dialogs

**Files:** `launcher.pyw` (S5, S6, S7)

- [ ] `DropdownButton` and its popup, `PresetDialog`, `SettingsDialog`, `FoldersDialog`, `UpdateDialog` and `TestDialog` get the same treatment:
  - sentence-case muted labels and hairline-bordered inputs;
  - ghost and outline buttons, with Save as filled primary;
  - the selection is `ACCENT` on `BG`; the `PresetDialog` scrollbar follows the palette.
- [ ] Screenshots of each dialog in both themes to the user. Commit: `launcher: dialogs in the web ui style`.

### Task 5: Jobs panel

**Files:** `panel/inject.js`, `panel/panel.js`, `panel/panel.css`

- [ ] `panel.css`: neutral tokens on `:root` / `:root.light` (light) and `:root.dark` (dark), plus `@media (prefers-color-scheme: dark) { :root:not(.light) {...} }` as the fallback. Set `color-scheme` per class. Retune the chips (accent, warn, ok, bad, neutral) on the new greys, and keep the warn border for reviews.
- [ ] `inject.js`: the same neutral tokens for `#ajp-root`, keyed on an `ajp-dark` class. A `MutationObserver` on `document.documentElement`'s `class` toggles it and posts `{type: "agent-jobs-theme", dark}` to the frame (`location.origin`), also on the frame's `load`.
- [ ] `panel.js`: in the existing message handler (origin and source checked), `agent-jobs-theme` sets `light` or `dark` on `<html>`.
- [ ] Check in the built-in browser:
  - Run a fake upstream on `18081` serving a stub page: `<html>` plus a button that toggles `dark`, and the `</body>` the proxy injects before. Then run `python proxy.py 18080 18081` and open `http://127.0.0.1:18080/`.
  - Toggle the class: the button and panel switch at once.
  - With no message (panel opened at `/agent-jobs`), it follows the OS.
  - Panel API calls will fail without an agents server; that's fine for this check.
  - Screenshot light and dark to the user. Stop both servers.
- [ ] Commit: `panel: neutral palette, follows the web ui theme`.

### Task 6: Docs and finish

- [ ] ADR `docs/adr/0013-web-ui-look-in-tk.md` (rejected alternatives): no webview rewrite, no launcher controls in the Web UI (the proxy isn't up before Launch), no Canvas-drawn rounded widgets. Add it to `CLAUDE.md`'s ADR index.
- [ ] `.claude/rules/launcher.md`:
  - palette colours unique per palette (`remap_colors`);
  - no colour defaults in signatures or closures;
  - new Toplevels call `set_dark_title_bar`;
  - the System poll runs on the UI thread.
- [ ] `.claude/rules/panel.md`: the theme message, the class names it sets, and the `prefers-color-scheme` fallback.
- [ ] `README.md`: the Theme row in Settings.
- [ ] Tests and `py_compile` pass. Merge `web-ui-look` to `main`, push, delete the branch.
