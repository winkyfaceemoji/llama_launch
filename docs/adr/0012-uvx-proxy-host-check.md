# 0012. The UVX MCP proxy checks Host through a wrapper

Status: Accepted

## Context

mcp-proxy (the UVX MCP proxy on 8001, serving fetch, ddg-search and time) answers any `Host`. Checked on 2026-10-15: a request with `Host: evil.example:8001` got 200, the same as `127.0.0.1:8001`. A web page that rebinds its own name to 127.0.0.1 could therefore call those tools through this PC. CORS doesn't help, because after rebinding the page is same-origin with itself. mcp-server-fetch refuses private addresses, so the reach is public pages and searches, not the home network. mcp-proxy 0.13.0 has no Host or allowed-hosts option.

## Decision

The launcher starts mcp-proxy through `mcp_guard.py`, run as `uv run --no-project --with mcp-proxy==0.13.0 python mcp_guard.py ...`. The wrapper patches `uvicorn.Config.__init__` so the app mcp-proxy hands to uvicorn is wrapped in an ASGI check. Any request whose Host isn't `127.0.0.1:<port>` or `localhost:<port>` gets 421; lifespan events pass through. mcp-proxy is pinned, so the startup path the hook relies on changes only when the pin does. Saved configs that still hold the old default `uvx_command` get the new one at startup; an edited command is left alone.

Rejected:
- A forwarder on 8001 with mcp-proxy moved to an internal port, or routing MCP through the prompt proxy on 8080: the internal port still has no Host check, and a rebinding page can target any port.
- Patching mcp-proxy's own code, or the MCP SDK's session manager: internals that change between releases. `uvicorn.Config` is uvicorn's public setup API.

## Consequences

- Web UI server URLs are unchanged. llama-server's `/cors-proxy` and the agents' `mcp_call` both send `Host: 127.0.0.1:8001`.
- Upgrading mcp-proxy means bumping the pin and re-running the live probe (a foreign Host must get 421).
- The first launch after the change takes longer while `uv` builds the pinned environment.
