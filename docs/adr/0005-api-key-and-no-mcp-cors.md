# 0005. API key on llama-server, no CORS on the MCP proxy

Status: Accepted

## Context

Binding to `127.0.0.1` keeps other devices out, but not web pages in the user's own browser. Tested on build 9776: llama-server copies any `Origin` into `Access-Control-Allow-Origin`, so any site could read its responses. With `--tools all`, a plain cross-origin `POST /tools` ran a shell command. With `--webui-mcp-proxy`, `/cors-proxy?url=` fetched local URLs for a foreign origin. `uvx mcp-proxy --allow-origin "*"` let any site call the MCP tools (the fetch server itself refuses private addresses).

## Decision

- The launcher generates a random key into gitignored `api_key.txt` and appends `--api-key-file` to every launch command unless the preset sets its own key. With a key, chat, `/tools` and `/cors-proxy` return 401; `/health` and `/v1/models` stay open.
- `--webui-mcp-proxy` stays. The Web UI reaches MCP servers through it ("use proxy" on), sending the key.
- `mcp-proxy` runs without `--allow-origin`, so browsers can't call it directly.

Rejected: dropping `--webui-mcp-proxy` and limiting mcp-proxy to `http://127.0.0.1:8080`. It closes the MCP paths but leaves chat open to every site, which only the key fixes.

## Consequences

- One-time setup: paste the key into the Web UI and switch on "use proxy" for each MCP server. Agents on `:8081` must send `Authorization: Bearer <key>`.
- Programs already running on the PC can still read the key file and call everything; this protects against web pages, not local malware.
