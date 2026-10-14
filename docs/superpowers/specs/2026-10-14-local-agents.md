# Local agents (pass 2): design

Agreed in a grilling session on 2026-10-14. Decision record: [ADR 0006](../../adr/0006-local-agents.md).

## Scope

- Agents do **research**, **planning** and **organising local files**. No coding agents (see `docs/research/local-coding-agents-2026.md`). No scheduled jobs in v1.
- Local drives only. Network paths (UNC, mapped network drives) are refused everywhere.

## Interface

- The user works in the llama Web UI. The chat model starts jobs through an **agents** MCP server in this repo (`agents/server.py`), built on the official `mcp` package (2.x, `MCPServer`) and run with `uv run --script`. The UVX MCP proxy serves it next to fetch and ddg-search at `http://127.0.0.1:8001/servers/agents/mcp`.
- The launcher keeps the `agents` entry in `config.json`'s `mcpServers` pointing at the current `agents/server.py` path.
- Tools for the chat model: `research(topic)`, `plan(goal)`, `organize(folder, goal)`, `job_status(job_id)`, `job_result(job_id)`, `apply(job_id)`, `reject(job_id)`, `restore(job_id)`, `queue_steps(job_id)`, `request_folder_access(path)`. The Web UI asks before every tool call; that prompt is the user's approval.

## Jobs

- Tools return at once with a job ID; jobs run in the background inside the agents server process, **one at a time**, others queued. They use whatever model the chat is using (only one fits in VRAM).
- Each job has a cap: **40 tool calls or 20 minutes**, after which it stops and reports what it has.
- Exit Processes (or closing the launcher) cancels running and queued jobs; they show as `cancelled`.
- Results are Markdown files in the sandbox: research reports cite their sources; `plan` writes a plan document plus a list of follow-up jobs that the Jobs pane can queue.

## Files

- Agents write only inside `Documents\agent-sandbox` (not inside its `jobs\` or `trash\` folders, which belong to the system).
- Agents read the sandbox plus folders on the readable-folders list. The user manages the list in the launcher's Settings (Add folder… / Remove) or by approving `request_folder_access` in chat. The list lives in `agent_folders.json` next to the launcher, outside the sandbox.
- `organize` never changes files directly. It produces a plan: an exact list of `mkdir`, `move` (also renames) and `remove` operations inside readable folders. A local review pass (same model, strict prompt) checks the plan. Nothing happens until the user approves; then exactly that list runs.
- `remove` moves the file to `agent-sandbox\trash\<job>\` and logs where it came from. **Restore** undoes a whole job; **Empty trash** is the user's button only.

## Jobs pane (launcher)

Lists every job with its state. Buttons: **Open result**, **Approve** / **Reject** (the dialog shows the full plan and the review), **Restore**, **Queue steps** (for a plan document's follow-up jobs), **Empty trash**.
