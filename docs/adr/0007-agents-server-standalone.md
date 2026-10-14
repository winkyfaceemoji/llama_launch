# 0007. Agents server runs standalone with rebinding protection; plans are approved only in the Jobs UI

Status: Accepted

Supersedes [0006](0006-local-agents.md).

## Context

ADR 0006 put the agents MCP server behind the UVX MCP proxy (mcp-proxy 0.12) and treated the Web UI's per-tool prompt as the user's approval. The final review found three problems with that:

- **DNS rebinding.** mcp-proxy creates its session manager without security settings, so mcp's rebinding check is off. A live probe showed `POST /servers/fetch/mcp` with `Host: evil.example` returning the tools list. A rebound page could then list folders for agents, read them through organize jobs, and apply plans, with no prompt at all. mcp-proxy has no option to turn the check on.
- **One failure kills every tool.** mcp-proxy starts all named servers in one exit stack. When the agents server failed at startup (sandbox unwritable, `uv` offline), fetch and ddg-search went down with it.
- **Approving in chat approves an unseen plan.** The Web UI prompt shows only `apply(job_id=…)`, `job_result` cuts long plans, and the default system prompt tells the model to use tools silently.

## Decision

- The launcher starts `uv run --script agents/server.py` as its own process, serving streamable HTTP at `http://127.0.0.1:8002/mcp`, stateless with JSON replies as mcp-proxy did. Rebinding protection is passed explicitly: hosts `127.0.0.1:*` and `localhost:*`, origins `http://127.0.0.1:*` and `http://localhost:*`. A wrong `Host` gets 421 and a wrong `Origin` gets 403.
- `apply` is no longer an MCP tool. Plans are approved only in the launcher's Jobs pane, which shows every operation and the review. The pane calls `POST /jobs/<id>/apply|reject|restore|queue` on the agents server. Each request needs `Host` exactly `127.0.0.1:<port>` or `localhost:<port>` and `Authorization: Bearer <api_key.txt>`; anything else gets 403. Chat keeps `reject`, `restore` and `queue_steps`.
- A plan may only touch paths strictly inside its job's folder. This is checked when the plan is proposed and again at apply.

Rejected:
- Keeping the agents server behind mcp-proxy: mcp-proxy can't turn the Host check on or check a secret, and any agents failure would still take fetch and ddg-search down.
- Keeping `apply` in chat but printing the plan first: the prompt still shows only the arguments, and nothing makes a small model print the whole plan.

Still valid from 0006, unchanged:
- Agents are an MCP server built on the official `mcp` 2.x `MCPServer` and run with `uv run --script`; everything else in `agents/` is standard library.
- Jobs run in background threads in that server, one at a time, capped at 40 tool calls or 20 minutes.
- Agents write only inside `Documents\agent-sandbox`; reads are limited to user-listed local folders kept outside the sandbox.
- File changes go plan → local review → user approval → apply; removals go to a per-job trash, and Restore undoes a whole job.
- Recycle Bin deletes, synchronous tools, a general `agent(task)` tool and a standard-library-only MCP server stay rejected, for 0006's reasons.

## Consequences

- The launcher runs four processes. The agents server's output goes to the UVX log pane prefixed `[agents] `, and a crash shows in the status bar as "agents server exited".
- One-time Web UI setup changes to `http://127.0.0.1:8002/mcp` with **Use llama-server proxy** on. The old `agents` entry in `config.json` is removed on launch.
- Jobs die with the agents server; it marks leftovers `cancelled` (or `failed`, for an interrupted apply or restore) when it starts, and the launcher does the same at exit. Running a second copy while the launcher runs would do this to live jobs.
- fetch and ddg-search on `8001` still have no rebinding protection. That is unchanged from before this work.
