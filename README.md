# llama_launch

A desktop GUI launcher for [llama.cpp](https://github.com/ggerganov/llama.cpp) that lets you select a model, pick a launch preset, and start a local AI server, a system prompt proxy, and a UVX MCP proxy with a single click.

---

## What it does

The launcher starts and manages three processes:

1. **llama-server** — runs a `.gguf` model file locally and exposes it as an API on port `8081`.
2. **System Prompt Proxy** (`proxy.py`) — sits on port `8080` (the public-facing port), injects a system prompt and the current date/time into every `/v1/chat/completions` request, then forwards to llama-server on `8081`.
3. **UVX MCP Proxy** — a middleware server on port `8001` that bridges the API to MCP tools (web search, fetch, time) for use with clients like Claude Desktop or Cursor.

Once launched, the app shows live output from all three processes in separate log panes, monitors server health, and tracks uptime. Closing the window while servers are running minimizes to the system tray rather than killing them.

---

## Requirements

- **Python 3.8+** — all core dependencies are part of the Python standard library.
- **llama.cpp** — must be built and available on your machine. The launcher uses your llama.cpp folder as the working directory when starting `llama-server`.
- **uv / uvx** — required for the MCP proxy. Install with `pip install uv`.
- **pystray + Pillow** — required for the system tray icon. The launcher installs these automatically on first run.

---

## How to run

Double-click `launcher.pyw`.

> `.pyw` files run with `pythonw.exe`, which launches the GUI without opening a terminal window.

Alternatively, run from a terminal:
```
pythonw launcher.pyw
```

---

## First-time setup

On the first launch, a setup screen asks you to locate your **models folder** — the directory where your `.gguf` files live. This path is saved to `config.json`. Make sure `llama-server` is on your system `PATH` so the launcher can find it regardless of working directory.

Click **⚙ Settings** in the top-right corner at any time to update the models folder path, toggle the Web UI option, or edit the UVX proxy command.

---

## Folder structure

```
llama_launch/
├── launcher.pyw      # the application
├── proxy.py          # system prompt injection proxy (port 8080 → 8081)
├── config.json       # all settings: llama.cpp path, presets, system prompt, MCP servers
└── README.md
```

Your `.gguf` model files should all live directly inside the folder you point `llama_dir` at — no subfolder needed:

```
GGUF Models/
├── gemma-4-E4B-it-Q8_0.gguf
├── qwen2.5-coder-14b-q8_0.gguf
└── Qwen_Qwen3-14B-Q5_K_M.gguf
```

`llama-server` itself must be on your system `PATH` (add your llama.cpp build folder to `PATH` if needed).

---

## Using the launcher

### Selecting a model
The **MODEL** dropdown lists all `.gguf` files found in your models folder. Use the **Refresh** button if you've added files since opening the launcher.

### Selecting a preset
The **PRESET** dropdown shows presets associated with your chosen model. Selecting one builds the full `llama-server` command automatically.

### Launching
Click **Launch Server**. The window switches to the running view, which shows:

- **Three log panes** — live output from `llama-server` (top), the UVX proxy (middle), and the system prompt proxy (bottom). Drag the dividers to resize. The top pane header shows the active model name and PID.
- **Status bar** — shows server health (`● online` / `● offline`), uptime counter, and an **Auto-scroll** toggle.

### Stopping
Click **Exit Processes** to kill all three servers and return to the launcher screen.

### System tray
Closing the window while servers are running minimizes to the system tray. Right-click the tray icon to **Show** the window or **Exit**.

---

## Settings

Click **⚙ Settings** to open the settings dialog, which has two sections:

### Llama.cpp folder
The directory scanned for `.gguf` model files and used as the working directory when launching `llama-server`. Use **Browse** to pick a folder.

### Launch options

- **Web UI** — set to **Yes** to automatically open `http://127.0.0.1:8080/` in the browser once the server comes online. Set to **No** to skip this (useful when connecting via API only).
- **UVX Settings** — click **▶ Show command** to expand and edit the `uvx mcp-proxy` command. Changes are saved when you click **Save**.

---

## Presets

Presets are stored in `config.json` under the `"presets"` key. Each preset has:

- **Name** — displayed in the dropdown
- **Command template** — the full `llama-server` command, using `{model}` as a placeholder for the model file path
- **Models** — the specific `.gguf` files this preset applies to, selected from a listbox of files in your models folder. A preset only appears in the dropdown when the selected model is one of its associated files.

Use the **+ Add**, **Edit**, and **Delete** buttons next to the preset dropdown to manage presets.

---

## System prompt

The system prompt is stored in `config.json` under the `"system_prompt"` key. The proxy automatically prepends the current date and time to it on every request, so the model always knows the current date without needing to call a time tool.

To edit the system prompt, update the `"system_prompt"` value in `config.json`.

---

## Config file

All settings live in a single `config.json` next to the launcher:

| Key | Purpose |
|---|---|
| `llama_dir` | Path to the folder containing your `.gguf` model files |
| `presets` | Saved launch presets |
| `system_prompt` | System prompt injected into every chat request |
| `mcpServers` | MCP tool definitions used by the UVX proxy |
| `uvx_command` | The `uvx mcp-proxy` command run at launch (editable in Settings) |
| `last_model` / `last_preset` | Remembered selections between sessions |

---

## Ports

| Port | Process |
|---|---|
| `8080` | System prompt proxy (public-facing — connect clients here) |
| `8081` | llama-server (internal — do not connect clients directly) |
| `8001` | UVX MCP proxy |

---

## MCP web UI setup

When using the llama-server web UI at `http://127.0.0.1:8080/`, three server URLs will appear in the MCP settings. Replace the address in each with `MCP` to ensure they connect correctly.

*Source: [How I got MCP working in the llama-server web UI](https://www.reddit.com/r/LocalLLaMA/comments/1rnyz75/how_i_got_mcp_working_in_the_llamaserver_web_ui_a/)*

---

## Models

Recommended models for this setup:

- **Gemma 4 E4B** (Q8_0) — general purpose, 131072 context, supports SWA
- **Qwen2.5-Coder 14B** (Q8_0) — coding tasks, 32768 context
- **Qwen3 14B** (Q5_K_M) — general + coding, good balance of speed and quality

Qwen3 models: [HuggingFace — HauhauCS/Qwen3.6-35B-A3B-Uncensored](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive/tree/main)
