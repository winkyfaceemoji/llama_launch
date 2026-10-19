# 0014. The Web UI look is a restyle of the Tk launcher

Status: Accepted

## Context

The launcher should look like the llama Web UI: neutral greys, flat, light or dark to match. The Web UI is shadcn-svelte with rounded corners, shadows and blur. The launcher is a fixed-size Tk window, and Tk draws none of those natively.

## Decision

Keep Tk and restyle it (spec `docs/superpowers/specs/2026-10-19-web-ui-look.md`): the Web UI's light and dark greys, flat widgets with 1px hairline borders, muted sentence-case labels, monochrome buttons except Launch (green) and Exit (red). A System / Light / Dark setting switches the palette live. The Jobs panel moves to the same greys and follows the Web UI's own theme.

Rejected:
- **Rebuilding the launcher as HTML in a webview** (pywebview): the closest match, but a new dependency and a rewrite of the whole GUI. The launcher stays standard library plus the tray icon.
- **Moving the launcher's controls into the Web UI**, like the Jobs panel: one window, but the proxy that serves the panel isn't running before Launch, so starting the servers would need yet another server.
- **Rounded corners and shadows drawn on a Tk Canvas:** closer to the Web UI, but custom widgets for every button, field and box, for little gain at this window size.

## Consequences

- Square corners stay; the look comes from colour, spacing and quiet type.
- Live switching recolours by colour value, so every palette colour must be unique within its palette (`.claude/rules/launcher.md`).
