# CLAUDE.md

Tkinter launcher (`launcher.pyw`) that starts llama-server, the system prompt proxy (`proxy.py`) and the UVX MCP proxy on this Windows PC. README.md covers what it does for the user.

## Commands

- Run the app: `pythonw launcher.pyw` (no console) or `python launcher.pyw` (errors go to the terminal).
- Tests: `python -m unittest discover -s tests`. They cover the pure helpers in `launcher.pyw` (it's loaded with `SourceFileLoader` because of the `.pyw` extension); the GUI has no tests.
- Syntax check: `python -m py_compile launcher.pyw proxy.py`.
- Run the proxy alone: `python proxy.py 8080 8081` (listen port, upstream port).
- Smoke-test a model without the GUI: run the Auto preset command (`AUTO_COMMAND` in `launcher.pyw`) with `-m` set to a real `.gguf`, then stream a `/v1/chat/completions` request through `:8080` and `:8081` and compare. The llama-server log shows the context `--fit` picked (`n_ctx = ...`).

## Conventions

- Standard library only. The one exception is pystray + Pillow for the tray icon, which `_bootstrap()` installs on first run.
- Windows only: process control uses `netstat`, `taskkill` and `shell=True`.
- `launcher.pyw` is one file split by `# SECTION N` banners. Keep new code in the matching section.
- Commit messages: short, lowercase, imperative (see `git log`).

## Project-wide gotchas

- `config.json` is gitignored and holds the user's real settings (models folder, presets, system prompt, MCP servers). Changing `_DEFAULT_CONFIG` only affects a first run; existing configs only pick up the Auto preset, which `load_presets` injects.
- Localhost is not a security boundary against the browser: llama-server allows every origin. Keep the API key on every launch (ADR 0005), and test new server flags with a foreign `Origin` header.
- Ports are fixed: `8080` proxy (Web UI), `8081` llama-server (agents connect here directly), `8001` UVX MCP proxy. Launching kills whatever is already listening on them.
- Hardware this is tuned for: RX 7800 XT (16 GB VRAM, Vulkan build of llama.cpp), 32 GB RAM. Anything that pushes a model out of VRAM silently spills to system RAM and runs several times slower rather than failing.

## ADR index

- [0001](docs/adr/0001-auto-preset-uses-fit.md) Auto preset leaves `-c`/`-ngl` to llama-server's `--fit` instead of per-model tuned presets
- [0002](docs/adr/0002-revert-show-all-presets.md) Reverted: show-all-presets dropdown with model-tagged auto-select
- [0003](docs/adr/0003-no-context-shift-or-swa-full.md) `--context-shift` and `--swa-full` stay off
- [0004](docs/adr/0004-proxy-injects-date-only.md) Proxy injects the date only; exact time comes from `--tools get_datetime`
- [0005](docs/adr/0005-api-key-and-no-mcp-cors.md) API key on llama-server; MCP proxy sends no CORS headers
