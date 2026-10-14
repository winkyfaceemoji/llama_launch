# llama_launch

A desktop GUI launcher for [llama.cpp](https://github.com/ggerganov/llama.cpp) that lets you select a model and start a local AI server, a system prompt proxy, and a UVX MCP proxy with a single click. It runs one model at a time, on this PC only.

---

## What it does

The launcher starts and manages three processes:

1. **llama-server** — runs a `.gguf` model file locally and exposes it as an API on port `8081`.
2. **System Prompt Proxy** (`proxy.py`) — sits on port `8080` (the Web UI port), injects your system prompt and today's date into every `/v1/chat/completions` request, then forwards to llama-server on `8081`.
3. **UVX MCP Proxy** — a middleware server on port `8001` that bridges MCP tools (web search, fetch) to the llama-server Web UI.

Once launched, the app shows live output from all three processes in separate log panes, monitors server health, and tracks uptime. Closing the window while servers are running minimizes to the system tray rather than killing them.

All three servers listen on `127.0.0.1` only, so other devices on your network can't reach them. Web pages open in your browser still can, so llama-server also requires an API key (see [API key](#api-key)).

---

## Requirements

- **Python 3.8+** — all core dependencies are part of the Python standard library.
- **llama.cpp** — a recent build with `--fit` support (2026 builds have it on by default). `llama-server` must be on your system `PATH`.
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
├── config.json       # all settings: models folder, presets, system prompt, MCP servers
├── api_key.txt       # llama-server API key, created on first run (keep private)
└── README.md
```

Text-only models can sit directly in your models folder. Vision models go in a **subfolder together with their `mmproj` file** — the launcher then adds `--mmproj` automatically so you can paste images into the Web UI:

```
GGUF Models/
├── Qwen_Qwen3-14B-Q5_K_M.gguf
├── Qwen_Qwen3-8B-Q8_0.gguf
└── gemma-4-12B-it-Q8_0/
    ├── gemma-4-12B-it-Q8_0.gguf
    └── mmproj-F16.gguf          # file name must start with "mmproj"
```

`mmproj` files are never shown in the model list. For split models, only the first shard (`-00001-of-0000N`) is listed.

---

## Using the launcher

### Selecting a model
The **MODEL** dropdown lists all `.gguf` models found in your models folder and its subfolders. Use the **Refresh** button if you've added files since opening the launcher.

### Selecting a preset
The **PRESET** dropdown is set to **Auto** whenever you pick a model. Auto lets llama.cpp size the context to your free VRAM (see [Presets](#presets)). Any other presets you've made for that model are listed too, as manual overrides.

### Launching
Click **Launch Server**. The window switches to the running view, which shows:

- **Status bar** — server health (`● online`, `● loading model`, `● offline`, or `● <process> exited (code N)` if one of the three crashed), uptime counter, and an **Auto-scroll** toggle.
- **Agent API** — the direct llama-server address for agents (`http://127.0.0.1:8081/v1`). Click it to copy.
- **API key** — click **click to copy** to put the key on the clipboard. The key itself is never shown.
- **Three log panes** — live output from `llama-server` (top), the UVX proxy (middle), and the system prompt proxy (bottom). Drag the dividers to resize. The top pane header shows the active model name and PID.

If a port the launcher needs (8080, 8081 or 8001) is still held by a leftover llama-server or proxy from an earlier run, the launcher stops it first. If any other program holds it, the launch is cancelled and the llama-server log pane names that program and its PID.

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

- **Web UI** — set to **Yes** to automatically open `http://127.0.0.1:8080/` in the browser once the server comes online. Set to **No** to skip this. The choice is saved between launches and has no other effect.
- **UVX Settings** — click **▶ Show command** to expand and edit the `uvx mcp-proxy` command. Changes are saved when you click **Save**.

---

## Presets

Presets are stored in `config.json` under the `"presets"` key. Each preset has:

- **Name** — displayed in the dropdown
- **Command template** — the full `llama-server` command, using `{model}` as a placeholder for the model file path
- **Models** — the specific `.gguf` files this preset applies to. A preset only appears in the dropdown when the selected model is one of them; a preset with no models applies to every model.

### The Auto preset

```
llama-server -m "{model}" --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --webui-mcp-proxy --tools get_datetime --port 8081
```

Auto deliberately leaves context size (`-c`) and GPU layers (`-ngl`) unset. llama-server's `--fit` (on by default) then picks the largest context that fits in free VRAM, keeping a 1 GB margin. The chosen context size appears in the llama-server log pane. Auto always exists, is pre-selected for every model, and can't be deleted.

Avoid adding `-c` or `-ngl` to a preset unless you mean to: setting either turns off `--fit` for that value, and a context that doesn't fit will spill out of VRAM and run far slower.

`--context-shift` and `--swa-full` are left off: when a chat fills the context, start a new chat. `--tools get_datetime` gives the model the exact time from your PC's clock without granting it file or shell access.

Never use `--tools all` (or `--agent`): it includes `exec_shell_command` and `write_file`, which any web page could then call.

The launcher adds `--api-key-file` to every preset's command, so you don't need to put it in your own presets.

Use the **+ Add**, **Edit**, and **Delete** buttons next to the preset dropdown to manage your own presets.

---

## System prompt

The system prompt is stored in `config.json` under the `"system_prompt"` key. The proxy puts today's date at the top of it on every request, so the model always knows the date without calling a tool. Only the date is injected — a timestamp that changed every minute would force llama-server to reprocess the whole conversation.

The proxy replaces any system message sent by the Web UI with this prompt, so edit it in `config.json` rather than in the Web UI's settings.

---

## Agents

Agents you run yourself (anything that speaks the OpenAI API) should connect straight to llama-server at:

```
http://127.0.0.1:8081/v1
```

This bypasses the system prompt proxy, so the agent's own system prompt and tools reach the model untouched. Only the Web UI uses port `8080`.

Agents must send the [API key](#api-key) as `Authorization: Bearer <key>`. In most OpenAI-compatible clients that's the "API key" setting.

---

## Agent jobs (research, planning, organising)

The chat model in the Web UI can start background jobs that run on your local model (ADR 0006):

- **research** a topic: a cited Markdown report. It can search and fetch web pages, but cannot read your files.
- **plan** a goal: a step-by-step plan, plus follow-up jobs you can queue from the Jobs pane. It also has web access and no access to your files, so it can only name a folder in a follow-up if your goal gives its path.
- **organize** a folder: a list of proposed moves, renames, new folders and removals, checked by a local review pass. It can read your listed folders but has no web access. **Nothing changes until you approve it.**

### Setup (once)

1. Launch the server. The launcher adds the `agents` MCP server to `config.json` automatically. The first launch takes a little longer while `uv` installs the `mcp` package.
2. In the Web UI's **MCP Servers** page, add `http://127.0.0.1:8001/servers/agents/mcp` and switch on **Use llama-server proxy**, as for the other servers.
3. In the launcher, open **⚙ Settings → Agent folders → Manage…** and add the local folders agents may read and organise. The chat model can also ask for a folder with `request_folder_access`; approving that tool call adds it.

Leave the Web UI's per-tool confirmation on for `apply` and `request_folder_access`. That confirmation is your approval.

### What agents can and can't touch

- **Write:** only `Documents\agent-sandbox`. Each job has a folder in `agent-sandbox\jobs\` with its `result.md` (and `plan.json` / `review.md` for organise jobs).
- **Read:** the folders you listed, plus the sandbox. Network drives are never read or changed. The launcher's own folder (`api_key.txt`, `config.json`) is off limits even if a parent folder is listed.
- **Changes to your files** happen only when you approve an organise plan, and only inside a listed folder, never the folder itself. A plan is refused if its operations overlap, if it moves a file to another drive, removes files on a different drive from `Documents`, or uses anything but plain paths (no `:`, wildcards or reserved names like `NUL`). Each step is checked again just before it runs, and a step that no longer fits stops the apply.
- **Removals** move files to `agent-sandbox\trash\<job>\<step>\<drive>\<path>`. **Restore** puts a whole job back, skipping anything that now has a new file at its old path. **Empty trash** deletes all trash for good, without following links out of the folder.

### Jobs pane

The running view lists agent jobs. Select one to **Open result**, **Approve / Reject…** a plan (shows every operation and the review), **Restore** an applied plan, or **Queue steps** from a plan job. **Empty trash** asks first. The pane sends approvals through the agents server, so approving there and in chat can't both apply a plan.

### Limits

- Jobs run one at a time and stop after 40 tool calls or 20 minutes. Tool results are cut to 6,000 characters, and older results are dropped from the model's context once it grows past 40,000.
- `job_result` shows at most 8,000 characters per file and gives the full file path when it cuts.
- **Exit Processes** cancels queued and running jobs. A job that was applying or restoring is marked failed; use **Restore** to undo what it finished.
- A 12-14B model makes mistakes: read the plan and the review before approving.

---

## API key

llama-server allows requests from every web origin, so without a key any web page open in your browser could chat with your model or use its `/cors-proxy` to read pages on your home network. The launcher therefore starts llama-server with an API key.

- **Where it comes from:** on first run the launcher generates a random key and saves it to `api_key.txt` next to `config.json`. It's gitignored.
- **Getting it:** click **click to copy** next to **API key** in the running view.
- **Web UI:** paste it once into the Web UI under **Settings → General → API Key**. The Web UI saves it in your browser.
- **Agents:** send it as a Bearer token (see [Agents](#agents)).
- **Replacing it:** delete `api_key.txt` and relaunch. A new key is generated; paste it into the Web UI and your agents again.

`/health` and `/v1/models` stay open without a key; everything else returns `401`.

---

## Config file

All settings live in a single `config.json` next to the launcher:

| Key | Purpose |
|---|---|
| `llama_dir` | Path to the folder containing your `.gguf` model files |
| `presets` | Saved launch presets |
| `system_prompt` | System prompt injected into every Web UI chat request |
| `mcpServers` | MCP tool definitions used by the UVX proxy |
| `uvx_command` | The `uvx mcp-proxy` command run at launch (editable in Settings). `{config}` stands for the path to `config.json` |
| `open_browser` | Whether to open the Web UI when the server comes online |
| `last_model` | Remembered model selection between sessions |

The API key lives in its own file, `api_key.txt`, not in `config.json`.

---

## Ports

| Port | Process |
|---|---|
| `8080` | System prompt proxy — the Web UI |
| `8081` | llama-server — agents connect here directly |
| `8001` | UVX MCP proxy |

---

## MCP web UI setup

The UVX proxy serves each MCP server from `config.json` at `http://127.0.0.1:8001/servers/<name>/mcp`, where `<name>` is its key under `mcpServers` (`fetch`, `ddg-search`, ...).

Set the [API key](#api-key) in the Web UI first, then for each server:

1. Click the **MCP Servers** icon in the Web UI's left sidebar (above the ⚙ settings icon). This is a separate page, not the **MCP** tab inside Settings.
2. Click **+ Add New Server**, enter the URL (for example `http://127.0.0.1:8001/servers/ddg-search/mcp`) and click **Add**. It shows **Failed to fetch** at this point; that's expected.
3. Click the pencil icon on the server's card, switch on **Use llama-server proxy**, and click **Update**. The switch is only in this edit form, not in the Add dialog.

The card then shows the server name, **Connected**, and the number of tools available.

The UVX proxy deliberately sends no CORS headers, so web pages can't call it directly. With **Use llama-server proxy** on, the Web UI reaches it through llama-server's `/cors-proxy`, which needs the API key. With it off, the browser blocks the connection.

---

## Models

Models in use with this setup (RX 7800 XT, 16 GB VRAM):

- **Gemma 4 12B** (Q8_0) — general purpose; with its `mmproj` file it can read images
- **Qwen3 14B** (Q5_K_M) — reasoning and coding, with thinking mode
- **Qwen3 8B** (Q8_0) — faster, for quick questions

Qwen3 models: [HuggingFace — HauhauCS/Qwen3.6-35B-A3B-Uncensored](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive/tree/main)
