# CLAUDE.md

Tkinter launcher (`launcher.pyw`) that starts llama-server, the system prompt proxy (`proxy.py`), the UVX MCP proxy and the agents MCP server (`agents/server.py`) on this Windows PC. README.md covers what it does for the user.

## Commands

- Run the app: `pythonw launcher.pyw` (no console) or `python launcher.pyw` (errors go to the terminal).
- Tests: `python -m unittest discover -s tests`. They cover `agents/` (job store, path policy, plans, runner, HTTP helpers including the approval-route check) and the pure helpers in `launcher.pyw` (it's loaded with `SourceFileLoader` because of the `.pyw` extension). The GUI and `agents/server.py` (needs `mcp`) have no unit tests; smoke-test the server on a spare port.
- Syntax check: `python -m py_compile launcher.pyw proxy.py`.
- Run the proxy alone: `python proxy.py 8080 8081` (listen port, upstream port).
- Agents MCP server: the launcher starts it on `127.0.0.1:8002` (`uv run --script agents/server.py`, MCP at `/mcp`). **Never run it by hand while the launcher is running**: its startup cancels unfinished jobs in the real sandbox, including one the live server is applying. With the launcher stopped, use a spare port (`--port 18002`); it still touches the real sandbox.
- Smoke-test a model without the GUI: run the Auto preset command (`AUTO_COMMAND` in `launcher.pyw`) with `-m` set to a real `.gguf`, then stream a `/v1/chat/completions` request through `:8080` and `:8081` and compare. The llama-server log shows the context `--fit` picked (`n_ctx = ...`).

## Conventions

- Standard library only, except pystray + Pillow for the tray icon (`_bootstrap()` installs them) and `mcp` in `agents/server.py` (run through `uv`).
- Windows only: process control uses `netstat`, `taskkill` and `shell=True`.
- `launcher.pyw` is one file split by `# SECTION N` banners. Keep new code in the matching section.
- Commit messages: short, lowercase, imperative (see `git log`).

## Project-wide gotchas

- `config.json` is gitignored and holds the user's real settings (models folder, presets, system prompt, MCP servers). Changing `_DEFAULT_CONFIG` only affects a first run; existing configs only pick up the Auto preset, which `load_presets` injects.
- Localhost is not a security boundary against the browser: llama-server allows every origin. Keep the API key on every launch (ADR 0005), and test new server flags with a foreign `Origin` header.
- Ports are fixed: `8080` proxy (Web UI), `8081` llama-server (agents connect here directly), `8001` UVX MCP proxy, `8002` agents MCP server. Launching kills leftovers of our own on them and refuses to launch if anything else holds one (on `8002` it only skips the agents server). While testing, check first whether the user's own launcher session is running on these ports.
- Hardware this is tuned for: RX 7800 XT (16 GB VRAM, Vulkan build of llama.cpp), 32 GB RAM. Anything that pushes a model out of VRAM silently spills to system RAM and runs several times slower rather than failing.

## ADR index

- [0001](docs/adr/0001-auto-preset-uses-fit.md) Auto preset leaves `-c`/`-ngl` to llama-server's `--fit` instead of per-model tuned presets
- [0002](docs/adr/0002-revert-show-all-presets.md) Reverted: show-all-presets dropdown with model-tagged auto-select
- [0003](docs/adr/0003-no-context-shift-or-swa-full.md) `--context-shift` and `--swa-full` stay off
- [0004](docs/adr/0004-proxy-injects-date-only.md) Proxy injects the date only; exact time comes from `--tools get_datetime`
- [0005](docs/adr/0005-api-key-and-no-mcp-cors.md) API key on llama-server; MCP proxy sends no CORS headers
- [0007](docs/adr/0007-agents-server-standalone.md) Agents server runs standalone on 8002 with rebinding protection; plans are approved only in the Jobs UI (supersedes 0006: sandbox writes, plan-approve-apply, per-job trash)
- [0008](docs/adr/0008-jobs-panel-in-web-ui.md) The proxy injects a jobs panel into the Web UI; the launcher's Jobs pane is gone
- [0009](docs/adr/0009-separate-approval-key.md) The jobs panel uses its own approval key; llama API key holders can't approve plans
- [0010](docs/adr/0010-one-model-for-chat-and-agents.md) One model serves chat and agent jobs; no separate agent model, no chat priority
