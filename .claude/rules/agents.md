---
paths: agents/**
---

# agents/

- **Only `server.py` may import `mcp`.** Everything else is standard library, because the launcher and tests import it without `uv` (ADR 0006).
- **mcp 2.x:** the class is `mcp.server.mcpserver.MCPServer`; `mcp.server.fastmcp` raises on import. The pin is `mcp>=2.3,<3`.
- **Jobs live inside the mcp-proxy child process.** It stays up between calls, so background threads work, and it dies with the UVX proxy. On start, `cancel_unfinished()` marks leftovers; the launcher does the same on Exit Processes (queued/running become `cancelled`, applying/restoring become `failed` with a note to use Restore).
- **Every path from the model goes through `Policy`.** `can_read` covers tools, `can_write` covers the sandbox minus `jobs\` and `trash\`, `can_change` covers plan operations. `is_inside` uses `realpath`, so junctions can't escape. Job IDs are checked against the exact format before they become paths. `agent_folders.json` stays outside the sandbox, so an agent can't widen its own access.
- **The launcher folder is unreadable to agents** (`api_key.txt`, `config.json`) even when a parent of it is a listed folder. `can_change` also refuses any folder that contains the launcher or the sandbox.
- **Web XOR user files, per job kind.** research and plan get web tools plus sandbox-only read/write; organize gets the listed folders, no web, and no `write_file`. A fetched page must never be able to steer an agent that can read private files, and the model must not copy listed-folder contents into the sandbox, which web jobs read. Don't combine the tool sets. Plan follow-ups can only name a folder the goal text gives, because the plan agent can't look inside folders.
- **Plan validation (`validate_plan`) is stricter than it looks:**
  - Operations may not overlap each other; the only exception is a later path under an earlier `mkdir`. Without this, one op could depend on another in ways validation can't see.
  - Moves are `os.rename` only (no cross-drive), and removals only on the sandbox's drive, because trashing is a rename into `trash\`.
  - Paths must be plain: drive-letter absolute, no `:` streams, wildcards, control characters, trailing dot or space, or device names (`CON`, `NUL`, ...).
  - `validate_plan` writes nothing. The same checks run again per operation in `_stop_reason` right before it executes (files may have changed since the proposal), and the first problem stops the apply.
- **Approval is a state transition under a lock** (`awaiting_approval` to `applying`). The launcher's Jobs pane calls the server's `apply` tool instead of applying itself, so pane and chat can't both apply a plan.
- **`applied.json` is written after every step (`on_step`)**, not only at the end, so a crash mid-apply still leaves a log Restore can use. Restore skips occupied or missing items and reports them instead of overwriting.
- **Trash layout is `trash\<job>\<step number>\<drive>\<path>`.** The step number keeps two removals of the same name apart. `empty_trash` never follows junctions or symlinks (`_delete` removes the link itself); don't swap in `shutil.rmtree`, which can recurse through a junction into real files.
- **Context budget:** `MAX_CALLS` 40, `MAX_SECONDS` 20 min, tool results cut to `MAX_RESULT_CHARS` (6000), and `_trim` replaces the oldest tool results with a stub once the history passes `CONTEXT_CHARS` (40000), always keeping the last 4 messages. A model or network error after the first turn ends the job and keeps what it has. `server.py` limits `job_result` to `MAX_FILE_CHARS` (8000) per file so a big plan can't flood the chat model.
- **Tool errors go back to the model as strings** (bad arguments, no access); they never raise out of the loop.
- `mcp_call` posts one JSON-RPC `tools/call` without `initialize`. That only works because mcp-proxy runs with `--stateless`.
