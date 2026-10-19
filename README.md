# llama_launch

A desktop GUI launcher for [llama.cpp](https://github.com/ggerganov/llama.cpp) that lets you select a model and start a local AI server, a system prompt proxy, a UVX MCP proxy and an agents server with a single click. It runs one model at a time, on this PC only.

---

## What it does

The launcher starts and manages four processes:

1. **llama-server** — runs a `.gguf` model file locally and exposes it as an API on port `8081`.
2. **System Prompt Proxy** (`proxy.py`) — sits on port `8080` (the Web UI port), injects your system prompt and today's date into every `/v1/chat/completions` request, then forwards to llama-server on `8081`.
3. **UVX MCP Proxy** — a middleware server on port `8001` that bridges MCP tools (web search, fetch) to the llama-server Web UI.
4. **Agents server** (`agents/server.py`) — an MCP server on port `8002` that runs background [agent jobs](#agent-jobs-research-planning-organising). It's optional: if it can't start, everything else still runs.

Once launched, the app shows live output from all four processes in log panes (the agents server shares the UVX pane), monitors server health, and tracks uptime. Closing the window stops all four servers and exits; minimize it to keep them running. Starting the launcher again while it runs brings up the open window instead of a second launcher.

All four servers listen on `127.0.0.1` only, so other devices on your network can't reach them. Web pages open in your browser still can, so llama-server also requires an API key (see [API key](#api-key)).

---

## Requirements

- **Python 3.8+** — all core dependencies are part of the Python standard library.
- **llama.cpp** — a recent build with `--fit` support (2026 builds have it on by default). `llama-server` must be on your system `PATH`.
- **uv / uvx** — required for the MCP proxy and the agents server. Install with `pip install uv`.
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

Click **Settings** in the top-right corner at any time to update the models folder path, toggle the Web UI option, or edit the UVX proxy command.

---

## Folder structure

```
llama_launch/
├── launcher.pyw          # the application
├── proxy.py              # system prompt injection proxy (port 8080 → 8081)
├── mcp_guard.py          # starts the UVX MCP proxy (8001) with a Host check
├── agents/               # agents server (server.py, port 8002) and the job, plan and path code it uses
├── panel/                # the jobs panel the proxy adds to the Web UI (served at /agent-jobs)
├── config.json           # all settings: models folder, presets, system prompt, MCP servers
├── api_key.txt           # llama-server API key, created on first run (keep private)
├── approval_key.txt      # Jobs panel key for the agents server, created on first run (keep private)
├── agent_folders.json    # folders agents may read and organise (created when you add one)
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

- **Status bar** — server health (`● online`, `● loading model`, `● offline`, or `● <process> exited (code N)` if one of the four crashed, for example `● agents server exited (code 1)`), uptime counter, and an **Auto-scroll** toggle.
- **Agent API** — the direct llama-server address for agents (`http://127.0.0.1:8081/v1`). Click it to copy.
- **API key** — click **click to copy** to put the key on the clipboard. The key itself is never shown.
- **Approval key (Jobs panel)** — the same, for the key the Jobs panel needs (see [Approval key](#approval-key)).
- **Three log panes** — live output from `llama-server`, the UVX proxy (with the agents server's lines, marked `[agents]`), and the system prompt proxy. Drag the dividers to resize. The llama-server pane header shows the active model name and PID.

If a port the launcher needs (8080, 8081, 8001 or 8002) is still held by a leftover llama-server, proxy or agents server from an earlier run, the launcher stops it first. If any other program holds 8080, 8081 or 8001, the launch is cancelled and the llama-server log pane names that program and its PID. If another program holds 8002, only the agents server is skipped, and the UVX pane says why.

### Stopping
Click **Exit Processes** to kill all four servers and return to the launcher screen.

### System tray
Right-click the tray icon to **Show** the window or **Exit** (which stops the servers, like closing the window).

---

## Settings

Click **Settings** to open the settings dialog, which has two sections:

### Llama.cpp folder
The directory scanned for `.gguf` model files and used as the working directory when launching `llama-server`. Use **Browse** to pick a folder.

### Launch options

- **Web UI** — set to **Yes** to automatically open `http://127.0.0.1:8080/` in the browser once the server comes online. Set to **No** to skip this. The choice is saved between launches and has no other effect.
- **Theme** — **System** (the default) follows Windows' light or dark setting and switches when Windows does; **Light** and **Dark** stay put. It applies at once to the launcher and its dialogs. The Jobs panel follows the Web UI's own theme setting instead.
- **UVX Settings** — click **▶ Show command** to expand and edit the command that starts mcp-proxy. Changes are saved when you click **Save**.
- **llama.cpp** — **Roll back to bNNNN** shows after an update, while the servers are stopped. See [Updating llama.cpp](#updating-llamacpp).

---

## Updating llama.cpp

This needs llama.cpp installed through WinGet (package `ggml.llamacpp`). The Web UI is built into llama-server, so updating llama.cpp updates the Web UI too.

- **Notice:** at startup the launcher asks WinGet in the background whether a newer build is out. If so, a row under the header shows both build numbers and an **Update** button. Nothing installs until you click it, and updating accepts the package's terms. If WinGet or the network isn't available, nothing shows.
- **Servers stopped:** Windows locks a running `llama-server.exe`, so **Update** and roll back only work while the servers are stopped.
- **Build check:** right after the update, the launcher starts the new build once on the spare port `18081` with the selected model and the API key, and checks that it reports the new version, comes online, answers a chat with the key, refuses chat, `/tools` and `/cors-proxy` without the key (also from a foreign web page), and logs the context size `--fit` picked. This uses the GPU for about 30 seconds. With no model selected, only the version is checked.
- **Automatic roll back:** if any check fails, the launcher reinstalls the previous build and shows which check failed.
- **Roll back by hand:** **Settings → llama.cpp → Roll back to bNNNN** reinstalls the build you had before the last update, then runs the build check (it only reports; there's nothing older to fall back to). Afterwards the startup notice offers the newer build again.

---

## Testing a model

**Test this model**, next to **Exit Processes** in the running view, scores the loaded model so you can compare models on this PC. It's enabled while the status bar says `● online`.

- **What it measures:** agent jobs (the stress test's organise, risky-plan review, research, plan and tool-choice cases), 40 fixed reasoning questions (maths, logic, instruction following, dates and times), and speed (tokens per second and the context `--fit` picked). Each model uses its own sampling defaults, so a gap of a few points means little.
- **15 to 60 minutes of GPU,** depending on the model (gpt-oss-20b took 16). Web UI chat is paused while it runs: chat requests get a "model test is running" error until the test finishes or you stop it. The test's jobs never show in the Jobs panel.
- **The dialog** streams the test's log. When the test finishes, the report opens in the browser.
- **The report** is `eval/results/report.html`: one row per complete test, sorted by agent score, each expanding to per-area results and the model's wrong answers. It and the result files stay on this PC.
- **Stop** (or closing the dialog) asks first, then ends the test, cancels its jobs and deletes its temporary folders. Nothing is added to the report. **Exit Processes** during a test stops it the same way, without asking.

---

## Presets

Presets are stored in `config.json` under the `"presets"` key. Each preset has:

- **Name** — displayed in the dropdown
- **Command template** — the full `llama-server` command, using `{model}` as a placeholder for the model file path
- **Models** — the specific `.gguf` files this preset applies to. A preset only appears in the dropdown when the selected model is one of them; a preset with no models applies to every model.

### The Auto preset

```
llama-server -m "{model}" --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --webui-mcp-proxy --port 8081
```

Auto deliberately leaves context size (`-c`) and GPU layers (`-ngl`) unset. llama-server's `--fit` (on by default) then picks the largest context that fits in free VRAM, keeping a 1 GB margin. The chosen context size appears in the llama-server log pane. Auto always exists, is pre-selected for every model, and can't be deleted.

Avoid adding `-c` or `-ngl` to a preset unless you mean to: setting either turns off `--fit` for that value, and a context that doesn't fit will spill out of VRAM and run far slower.

`--context-shift` and `--swa-full` are left off: when a chat fills the context, start a new chat. Auto passes no `--tools`: llama-server's built-in tools now only read, write and run things on your PC. The exact time comes from the `time` MCP server instead (see [MCP servers](#mcp-servers)).

Never add `--tools` (any of them) or `--agent` to a preset: they include `exec_shell_command` and `write_file`, which any web page could then call.

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

The chat model in the Web UI can start background jobs that run on your local model (ADR 0007):

- **research** a topic: a cited Markdown report. It can search and fetch web pages, but cannot read your files. It may only cite pages it actually read; a report that still cites unread pages gets a warning line at the top.
- **plan** a goal: a step-by-step plan, plus follow-up jobs you can queue from the [Jobs panel](#jobs-panel). It also has web access and no access to your files, so it can only name a folder in a follow-up if your goal gives its path. Like research, it may only cite pages it read.
- **organize** a folder: a list of proposed moves, renames, new folders and removals, checked by a local review pass. It can read your listed folders but has no web access. **Nothing changes until you approve it in the Web UI's [Jobs panel](#jobs-panel).**

### Setup (once)

1. Launch the server. The launcher starts the agents server itself on port `8002`. The first launch takes a little longer while `uv` installs the `mcp` package; the UVX pane shows its progress on lines marked `[agents]`.
2. In the Web UI's **MCP Servers** page, add `http://127.0.0.1:8002/mcp` and switch on **Use llama-server proxy**, as for the other servers (see [MCP web UI setup](#mcp-web-ui-setup)). If you added the old `http://127.0.0.1:8001/servers/agents/mcp` before, delete it.
3. In the launcher, open **Settings → Agent folders → Manage…** and add the local folders agents may read and organise. The chat model can also ask for a folder with `request_folder_access`; approving that tool call adds it.
4. Paste your [API key](#api-key) into the Web UI under **Settings → General** if you haven't, and the [approval key](#approval-key) into the Jobs panel the first time it asks.

Leave the Web UI's per-tool confirmation on for `organize`, `restore`, `queue_steps` and `request_folder_access`, and check that it really asks before each of them (no "always allow"). That confirmation is your approval for those calls: `organize` reads your listed folders into its report, `restore` moves files, `queue_steps` starts the jobs a plan proposed, and `request_folder_access` widens what agents can read.

**Plans are approved only in the Web UI's Jobs panel**, which shows every operation and the review. The chat model can't apply a plan; it has no `apply` tool.

### What agents can and can't touch

- **Write:** only `Documents\agent-sandbox`. Each job has a folder in `agent-sandbox\jobs\` with its `result.md` (and `plan.json` / `review.md` for organise jobs).
- **Read:** the folders you listed, plus the sandbox. Network drives are never read or changed. The launcher's own folder (`api_key.txt`, `config.json`) is off limits even if a parent folder is listed.
- **Changes to your files** happen only when you approve an organise plan in the Jobs panel, and only inside the folder that job was asked to organise (a listed folder or one inside it), never that folder itself or a listed folder itself. A plan is refused if its operations overlap, if it moves a file to another drive, removes files on a different drive from `Documents`, or uses anything but plain paths (no `:`, wildcards or reserved names like `NUL`). Each step is checked again just before it runs, and a step that no longer fits stops the apply.
- **Removals** move files to `agent-sandbox\trash\<job>\<step>\<drive>\<path>`. **Restore** puts a whole job back, skipping anything that now has a new file at its old path. **Empty trash** deletes all trash for good, without following links out of the folder.

### Jobs panel

Click **Jobs** at the top right of the Web UI to open a 360 px panel beside the chat (**Close** in its header shuts it; the button shows how many jobs are running or waiting for review). The panel and its button hide themselves on the Web UI's Settings pages and come back when you leave them. Jobs are listed newest first, and the panel refreshes every 2 seconds while open.

- **Each card** shows the job's title, its state, the steps it has taken so far (the latest is marked while it runs), a count of steps against the 40-call limit with the minutes elapsed, and any note. Finished jobs with nothing to do collapse to one line; click one to expand it.
- **Approve / Reject** appear on a job waiting for review. The card shows the full plan, every move, new folder and removal, plus the local review, and the button says how many changes you are approving. This is the only place a plan can be approved.
- **Restore** puts back everything an applied (or part-applied) plan moved or removed. **Queue** starts the follow-up jobs a plan job proposed.
- **Empty trash** at the bottom deletes all trash for good. Click it once and it turns red and reads **Confirm empty trash**; click again (a click within the first 0.4 seconds is ignored, so a double-click doesn't count) within 5 seconds to go ahead, or wait and it reverts. It is refused while any job is applying or restoring, including a restore started from chat.

The panel talks to the agents server through the proxy, with the approval key, so the panel and the chat can't act on a job at the same time. If it says the agents server isn't running, start the servers from the launcher.

After updating the launcher, press Ctrl+F5 once in the Web UI so the browser drops its cached copy of the page.

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

### Approval key

The Jobs panel uses a second key, so a program you give the API key to can't approve plans that change your files.

- **Where it comes from:** the launcher generates it on first run into `approval_key.txt` (gitignored).
- **Getting it:** click **click to copy** next to **Approval key (Jobs panel)** in the running view.
- **Jobs panel:** paste it into the field the panel shows the first time, or after the key changes. The panel saves it in your browser.
- **Replacing it:** delete `approval_key.txt` and restart the launcher, then paste the new key into the panel.

---

## Config file

All settings live in a single `config.json` next to the launcher:

| Key | Purpose |
|---|---|
| `llama_dir` | Path to the folder containing your `.gguf` model files |
| `presets` | Saved launch presets |
| `system_prompt` | System prompt injected into every Web UI chat request |
| `mcpServers` | MCP tool definitions used by the UVX proxy |
| `uvx_command` | The command that starts mcp-proxy at launch (editable in Settings). `{config}` stands for the path to `config.json`, `{guard}` for `mcp_guard.py`, which adds the Host check |
| `open_browser` | Whether to open the Web UI when the server comes online |
| `last_model` | Remembered model selection between sessions |
| `previous_build` | The llama.cpp build before the last update (e.g. `b9776`), for roll back. Cleared after a roll back |

The API key lives in its own file, `api_key.txt`, not in `config.json`.

---

## Ports

| Port | Process |
|---|---|
| `8080` | System prompt proxy — the Web UI |
| `8081` | llama-server — agents connect here directly |
| `8001` | UVX MCP proxy |
| `8002` | Agents server (MCP at `/mcp`) |
| `18081` | llama-server during the build check after an update, for about 30 seconds |

---

## MCP web UI setup

The UVX proxy serves each MCP server from `config.json` at `http://127.0.0.1:8001/servers/<name>/mcp`, where `<name>` is its key under `mcpServers` (`fetch`, `ddg-search`, `time`, ...). `time` gives the model the exact time and converts between timezones. The agents server is separate, at `http://127.0.0.1:8002/mcp`.

Set the [API key](#api-key) in the Web UI first, then for each server:

1. Click the **MCP Servers** icon in the Web UI's left sidebar (above the ⚙ settings icon). This is a separate page, not the **MCP** tab inside Settings.
2. Click **+ Add New Server**, enter the URL (for example `http://127.0.0.1:8001/servers/ddg-search/mcp`) and click **Add**. It shows **Failed to fetch** at this point; that's expected.
3. Click the pencil icon on the server's card, switch on **Use llama-server proxy**, and click **Update**. The switch is only in this edit form, not in the Add dialog.

The card then shows the server name, **Connected**, and the number of tools available.

Neither MCP server sends CORS headers, so with **Use llama-server proxy** off the browser blocks the connection; with it on, the Web UI reaches them through llama-server's `/cors-proxy`, which needs the API key. Missing CORS headers don't stop DNS rebinding, where a web page points its own host name at `127.0.0.1`:

- The agents server checks every request's `Host` (and `Origin`, when sent) and answers only `127.0.0.1` and `localhost`, so a rebound page is refused. Its approval routes, used by the Jobs panel, also require the approval key. The panel's routes on the proxy (`/agent-jobs`) do the same Host check themselves, since the proxy rewrites Host before the agents server sees it.
- The UVX proxy on `8001` is started through `mcp_guard.py`, which answers only `Host: 127.0.0.1:8001` and `localhost:8001`, so a rebound page can't reach fetch, ddg-search or time either. If you edit the UVX command in Settings, keep `python "{guard}"` in it.

---

## Models

Models in use with this setup (RX 7800 XT, 16 GB VRAM):

- **Gemma 4 12B** (Q8_0) — general purpose; with its `mmproj` file it can read images
- **Qwen3 14B** (Q5_K_M) — reasoning and coding, with thinking mode
- **Qwen3 8B** (Q8_0) — faster, for quick questions

Qwen3 models: [HuggingFace — HauhauCS/Qwen3.6-35B-A3B-Uncensored](https://huggingface.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive/tree/main)
