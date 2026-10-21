# Launcher layout: less clumped, easier to read

Agreed in a grill session on 2026-10-21. It follows the Web UI look ([spec](2026-10-19-web-ui-look.md), ADR 0014). Terms are in [CONTEXT.md](../../../CONTEXT.md).

## Goal

The launcher feels clumped. Make every view easier to read and its controls easier to find, without growing the main window: fewer things per row, related settings grouped on their own pages, rarely used details out of the way.

## Facts this rests on (main, 2026-10-21)

- **Fixed sizes:**
  - The main window is 480 px wide (`W`). Its heights are fixed per view: `H_CONFIG` 270, `H_NOTICE` +70, `H_RUNNING` 750 (resizable).
  - Settings is 460 px wide (`W_SETTINGS`), with heights `H_SETTINGS`, `H_SETTINGS_UVX` and `H_ROLLBACK`.
- **Fonts:** labels and small buttons are 9 pt (`FONT_SMALL`, `FONT_BTN_SM`), the title is 13 pt bold, and logs are 10 pt Cascadia Code.
- **Home:**
  - Model row: dropdown and Refresh.
  - Preset row: dropdown, + Add, Edit and Delete.
  - Launch below them. The update notice card appears above Model when a newer build exists.
- **Running view:** the status box, then three lines (Agent API address, API key, approval key) with Copy buttons, then the log tabs, then Test this model and Exit Processes.
- **Settings is one dialog, `SettingsDialog`,** with these rows:
  - the llama.cpp folder (Browse)
  - Web UI Yes/No and Theme System/Light/Dark, which both **apply at once**
  - Agent folders (Manage…, `FoldersDialog`) and Prompts (Edit…, `PromptsDialog`), each with its own Save
  - llama.cpp roll back, shown only when a previous build is recorded and the servers are stopped
  - the UVX command, in an accordion

  Save writes only the folder and the UVX command.

## Decisions

- **The main window stays 480 px wide** (round 1, 1A). Space comes from showing less at once, not from a bigger window.
- **Text goes up to 10 pt** for labels, small buttons and section headings (round 5, 1B). Log text stays as it is. The fixed heights in SECTION 1 grow to fit.
- **Home:**
  - The **Model row keeps Refresh inline** (round 4, 1A).
  - The **Preset row becomes the dropdown plus a "⋯" button** (round 1, 2B). It opens a small menu: + Add, Edit, Delete (Delete in red). The menu is themed like the model dropdown's list, not a native menu.
  - The **update notice stays a card on home** (round 4, 3A).
- **Running view:** the Agent API address, the API key and the approval key, each with its Copy button, move into a **collapsible "Connection ▸" row** (round 2, 3B) under the status box. It **starts closed on every launch** (round 4, 2A), and the log tabs get the space.
- **Settings becomes a drill-down from General** (round 1, 3B; round 3, 1B). It has no sidebar, and the dialog keeps about its current width.
  - **General** (the page it opens on): llama.cpp folder, Web UI on launch, Theme. It ends with links: **Agents ›**, **llama.cpp ›**, **Advanced ›** (round 3, 2A).
  - **Agents:** Agent folders (Manage…) and Prompts (Edit…).
  - **llama.cpp:** roll back, when available. It's also the natural home for future llama.cpp settings.
  - **Advanced:** the UVX command, shown directly with no accordion.
  - Each sub-page has a **"‹ General"** back link at the top.
- **Everything in Settings waits for Save** (round 2, 2B). Save and Cancel sit at the bottom of every page, and Save applies every page's changes.
  - **Theme previews** at once when picked; Save keeps it, while Cancel or the X restores the theme from before.
  - Web UI, the folder and the UVX command change only on Save.
- **Agent folders and Prompts keep their own Save** (round 3, 3A). They're editors opened from the Agents page; their changes are saved there, not by Settings' Save.
- **After the layout lands, the code is restructured** around these views (home, running, the Settings pages) with `codebase-design`. That's its own step, with no visible change.

## Rejected

- **A wider (about 640 px) or resizable main window:** it would solve the crowding with screen space instead of with less on each row.
- **A Presets page in Settings:** it would empty the home screen further, but manage presets far from where you pick one.
- **A sidebar or tabs in Settings:**
  - A sidebar needs a wider dialog.
  - Five tabs are tight at this width.
  - The user wanted General as the parent, with the rest inside it.
- **Saving Agent folders and Prompts with Settings' Save:** more unsaved state across pages, and Cancel would throw away prompt edits.
- **Refreshing the model list automatically when the dropdown opens:** a slow disk would make the dropdown slow to open.
- **Moving the update notice into Settings:** it's easy to miss there.

## Open points for the plan

- **The "⋯" menu:**
  - Reuse `DropdownButton`'s popup (the `overrideredirect` list, `_MenuProxy`) or a small sibling of it.
  - It must not get `set_dark_title_bar` (it already skips borderless windows) and must close on Esc and on focus loss.
- **The Settings drill-down:**
  - One `Toplevel` whose content frame is swapped per page.
  - The dialog's height per page, since sizes are fixed.
  - Where pending (unsaved) values live, so moving between pages keeps them.
  - The theme preview: apply on pick, remember the theme from before, and restore it on Cancel or close.
- **Esc:** today Esc closes a dialog the way its X does. On a sub-page it could go back to General first. Decide with the user's screenshots.
- **The Connection row:**
  - A disclosure button ("Connection ▸" / "▾") that shows or hides the three rows.
  - The running view's fixed `H_RUNNING` stays; the logs take whatever the closed row frees.
- **Fonts:** `FONT_SMALL` and `FONT_BTN_SM` go to 10 pt; check every fixed height (home, notice, running, Settings pages, dialogs) for clipping in both themes. `dev/gui_shot.py` must also capture the Settings sub-pages and the "⋯" menu.
- **Docs:**
  - `.claude/rules/launcher.md`: Settings pages and Save semantics, the theme preview and restore, the "⋯" menu.
  - README: the Settings section, the home and running views, keyboard shortcuts.
  - `CONTEXT.md`: only if a new term appears.
- **Coordination:** other sessions may be editing `launcher.pyw` in this checkout (the prompt editor added `PromptsDialog` and the Prompts row). Commit only this work's files, and check `git status` before each commit.
