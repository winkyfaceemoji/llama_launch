---
paths: panel/**
---

# panel/

- **Stacking is deliberately low: Jobs button `z-index` 40, frame 39.** The Web UI's modal dialogs are z-50; a huge z-index put the open panel over their buttons (the Save button in Settings could not be clicked).
- **The Web UI routes with the hash, and Settings pages are `#/settings...`.** `inject.js` hides the button and frame (`ajp-away`, frame inert, panel told it is hidden) while the hash starts with `#/settings`. **`hashchange` is not enough:** the Web UI (SvelteKit) navigates with `history.pushState`/`replaceState`, which fire no event, so `inject.js` also compares `location.href` every 300 ms (plus `popstate`/`hashchange`) and re-applies only when it changed. Don't monkey-patch `history`. It never changes the saved open flag, so leaving Settings restores exactly the earlier state. If the Web UI changes its routes, this prefix is the thing to update.
- **Empty trash is a two-click confirm inside the panel, not `window.confirm`** (a native confirm focuses OK, so Enter would delete). The first click only arms the button for 5 seconds, and a click within 400 ms of arming is ignored (double-click, held Enter); Restore still uses `window.confirm`.
- **`refresh()` must always re-arm its timer.** Rendering runs inside a try/catch that shows "Couldn't show jobs: ..." so a render bug can't freeze the polling.
- Everything from the model (paths, step text) goes in with `textContent`, never as HTML.
