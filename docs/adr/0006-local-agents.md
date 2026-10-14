# 0006. Local agents run as an MCP server behind the Web UI

Status: Accepted

## Context

Pass 2 adds agents for research, planning and organising local files, driven from the llama Web UI by the chat model, on a 16 GB card that holds one model. Coding agents were left out: local 24–27B models still trail hosted ones by a wide margin and Cline reports small models failing (`docs/research/local-coding-agents-2026.md`). A 12–14B model makes mistakes, so anything that changes files must be reviewable and reversible.

## Decision

- Agents are an MCP server (`agents/server.py`, official `mcp` 2.x run with `uv run --script`) behind the existing UVX MCP proxy, not a new chat app. mcp-proxy keeps the stdio server process alive between calls (verified: same PID, background thread kept running), so jobs run in background threads inside it, one at a time, capped at 40 tool calls or 20 minutes.
- Agents write only inside `Documents\agent-sandbox`; reads are limited to a user-managed list of local folders kept outside the sandbox.
- Changes outside the sandbox go through plan → local review → user approval → apply. Removals move files to a per-job trash folder with a log, so a whole job can be restored.

Rejected:
- Recycle Bin deletes: no per-job undo, and large files can skip the bin.
- Synchronous tools: the Web UI gives up on a tool call after 300 s.
- A general `agent(task)` tool: a small model wanders; fixed job types are predictable.
- Standard-library-only MCP: more code to keep in step with the protocol.

## Consequences

- Agent code outside `agents/server.py` stays standard library only, so the launcher and tests can import it without `uv`.
- Jobs die with the UVX proxy; on start the server marks leftovers `cancelled`.
- Scheduled jobs and coding agents are future work.
