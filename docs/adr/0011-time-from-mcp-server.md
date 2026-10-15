# 0011. The exact time comes from a time MCP server

Status: Accepted (supersedes the time part of 0004; the proxy still injects the date only)

## Context

ADR 0004 got the exact time from llama-server's built-in `--tools get_datetime`. Build b11636 removed that tool: its built-in tools are now `read_file`, `file_glob_search`, `grep_search`, `exec_shell_command`, `write_file`, `edit_file` and `get_info` (OS version and working folder only), and llama-server refuses to start when `--tools` names an unknown tool. The first update's build check caught this on 2026-10-15 and rolled back to b9776.

## Decision

- The Auto preset passes no `--tools`. None of the remaining built-in tools is safe to expose to a browser chat.
- The exact time comes from `mcp-server-time` (`uvx mcp-server-time`), served by the UVX MCP proxy like fetch and ddg-search, as `time` in `config.json`'s `mcpServers`. It detects the local timezone itself.

Rejected:
- Dropping exact time entirely: simplest, but the model then guesses the time of day.
- The proxy injecting the time into every message: a prompt that changes every minute defeats llama-server's prompt cache (the reason for 0004).
- Staying on b9776: about 1,860 builds behind, and it only postpones the same problem.

## Consequences

- A time source that doesn't depend on llama.cpp's built-in tools, which are marked experimental.
- One-time setup: add the `time` server on the Web UI's MCP servers page, as with fetch. Existing `config.json` files don't get new default servers, so the entry and the system prompt's tool rule were edited by hand once.
- With no `--tools`, b11636 keeps its default CORS (`*`); the API key (ADR 0005) is still what keeps web pages out.
