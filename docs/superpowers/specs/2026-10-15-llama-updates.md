# llama.cpp updates from the launcher: design

Agreed in a grill session on 2026-10-15. Terms are in [CONTEXT.md](../../../CONTEXT.md). The model test is a separate spec, built after this one.

## Goal

Keep llama.cpp, and the Web UI built into it, current from the launcher without leaving it, and get back to the last working build when an update breaks something.

## Facts this rests on

- llama.cpp is the WinGet package `ggml.llamacpp`: a portable Vulkan zip, installed in `%LOCALAPPDATA%\Microsoft\WinGet\Packages\ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe\`, whose folder is on `PATH`. Installed today: b9776; newest: b11636.
- WinGet lists 428 builds, b9776 among them, so an older build can be reinstalled while WinGet still lists it.
- The launcher runs `llama-server` by name from `PATH` and has no WinGet code.
- The Web UI is built into `llama-server.exe`, so updating the build updates the Web UI.

## Decisions

- **WinGet stays the installer** (1A). The launcher runs WinGet; it keeps no builds folder of its own. Only the previous build's number is kept (in `config.json`, `previous_build`).
- **Notice at startup, update on click** (2A). On startup the launcher checks in the background for a newer build and shows a notice with the current and newest build numbers and an **Update** button. It shows the notice whenever any newer build exists. Nothing installs without the click. If WinGet or the network isn't available, it shows nothing.
- **Update only while the servers are stopped.** Windows locks a running `llama-server.exe`; the button is disabled after Launch.
- **Build check after every update, with automatic roll back** (1A of round 3). Right after WinGet finishes, the launcher starts the new build once on a spare port with the last model and the API key, and checks:
  1. `llama-server --version` reports the new build.
  2. The server reaches `/health` = ok within a time limit.
  3. A chat completion with the key returns text.
  4. Without the key, `POST /v1/chat/completions`, `/tools` and `/cors-proxy` don't answer (401 or 404), including with a foreign `Origin` header (ADR 0005).
  5. The log shows the context `--fit` picked (`n_ctx = ...`) (ADR 0001).
  If any check fails, it rolls back to the previous build and shows which check failed. With no last model, checks 2, 3 and 5 are skipped and the notice says so. The check uses the GPU for about 30 seconds.
- **Roll back lives in Settings** (2B of the last round), shown whenever a previous build is recorded: "Roll back to bNNNN". It reinstalls that build through WinGet.

## Out of scope

- Model downloads (dropped: the browser plus Hugging Face's own hardware marks covers it).
- Choosing models for the user, or any online AI at run time.
- Separate chat and agent models (ADR 0010).

## Open points for the plan

- The exact WinGet commands, especially reinstalling an older version over a newer portable install (`winget install --id ggml.llamacpp --exact --version <b> --force` is the candidate). Verify on a spare machine or with `--dry-run`-style checks before relying on it; never run an install during development without asking.
- WinGet's `--accept-package-agreements` / `--accept-source-agreements`: the user's Update click is the consent; the notice must say an update accepts the package's terms.
- Where the pure parts go (version parsing from `winget` output, "is newer", build check verdicts) so they get unit tests; the WinGet calls and GUI stay untested.
- Rules and README updates: `.claude/rules/launcher.md` (update flow, locks), README (how updates and roll back work).
