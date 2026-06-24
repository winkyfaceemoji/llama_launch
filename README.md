# llama_launch

A desktop GUI launcher for [llama.cpp](https://github.com/ggerganov/llama.cpp) that lets you select a model, pick a launch preset, and start both a local AI server and a UVX MCP proxy with a single click.

---

## What it does

The launcher starts and manages two processes:

1. **llama-server** — runs a `.gguf` model file locally and exposes it as an API on port `8080`. This is the AI inference engine.
2. **UVX MCP Proxy** — a middleware server on port `8001` that bridges the llama-server API to tools that speak the MCP protocol, such as Claude Desktop or Cursor.

Once launched, the app shows live output from both processes in separate log panes, monitors server health, and tracks uptime. Closing the window while servers are running minimizes to the system tray rather than killing them.

---

## Requirements

- **Python 3.6+** — all core dependencies are part of the Python standard library; nothing extra to install for the launcher itself.
- **llama.cpp** — must be built and available on your machine. The launcher uses your llama.cpp folder as the working directory when starting `llama-server`.
- **uv / uvx** — required for the MCP proxy. Install with `pip install uv`.
- **pystray + Pillow** — required for the system tray icon. The launcher installs these automatically on first run.

---

## How to run

Double-click `launcher.pyw`.

> `.pyw` files run with `pythonw.exe`, which launches the GUI without opening a terminal window. If Python is installed and associated with `.pyw` files (the default), no extra steps are needed.

Alternatively, run from a terminal:
```
pythonw launcher.pyw
```

---

## First-time setup

On the first launch, a setup screen asks you to locate your **llama.cpp folder** — the directory where `llama-server.exe` lives. This path is saved to `config.json` next to the launcher and used as the working directory for all subprocess launches.

You can change this path at any time via the **⚙ Settings** button in the top-right corner of the main screen.

---

## Folder structure

```
llama_launch/
├── launcher.pyw      # the application
├── config.json       # saved settings (llama.cpp path, last model/preset)
├── presets.json      # your saved launch presets
└── README.md
```

Your `.gguf` model files are expected to live in a `Models/` subfolder inside your llama.cpp directory:

```
llama.cpp/
├── llama-server.exe
└── Models/
    ├── gemma-3-12b.gguf
    └── qwen2.5-14b.gguf
```

---

## Using the launcher

### Selecting a model
The **MODEL** dropdown lists all `.gguf` files found in the `Models/` subfolder of your llama.cpp directory. Use the **Refresh** button if you've added files since opening the launcher. Your last selection is remembered between sessions.

### Selecting a preset
The **PRESET** dropdown shows launch presets filtered to match your chosen model. Selecting a preset builds the full `llama-server` command automatically. Your last selection is remembered.

### UVX Proxy command
Click **▶ UVX Proxy Command** to expand and edit the proxy command if needed. The default command points to a `config.json` in your working directory (the llama.cpp folder).

### Launching
Click **Launch Server**. The window switches to the running view, which shows:

- **Two log panes** — live stdout output from `llama-server` (top) and the UVX proxy (bottom). Drag the divider between them to resize. The pane headers show the process PID.
- **Status bar** — shows server health (`● online` / `● offline`), uptime counter, and an **Auto-scroll** toggle. Uncheck Auto-scroll to freely read the logs without being pulled to the bottom.

### Stopping
Click **Exit Processes** to kill both servers and return to the config screen.

### System tray
Closing the window while servers are running minimizes to the system tray instead of killing them. Right-click the tray icon to **Show** the window or **Exit** (which kills both servers and closes the app).

---

## Presets

Presets are stored in `presets.json` and define how `llama-server` is invoked. Each preset has:

- **Name** — displayed in the dropdown
- **Command template** — the full `llama-server` command, using `{model}` as a placeholder for the model file path
- **Model keywords** — a comma-separated list of keywords (e.g. `gemma`, `qwen`). The preset is only shown when the selected model filename contains one of these keywords. Leave blank to show for all models.

Use the **+ Add**, **Edit**, and **Delete** buttons next to the preset dropdown to manage presets. Two defaults ship with the launcher — one for Gemma models and one for Qwen models.

---

## Config files

| File | Purpose |
|---|---|
| `config.json` | Stores the llama.cpp folder path and the last selected model and preset |
| `presets.json` | Stores all saved launch presets |

Both files are created automatically next to `launcher.pyw` on first use.
