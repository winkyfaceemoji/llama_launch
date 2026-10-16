"""Starts mcp-proxy (the UVX MCP proxy on 8001) with DNS-rebinding protection (ADR 0012).

mcp-proxy 0.13 has no Host check, so a web page that rebinds its own name to
127.0.0.1 could call fetch, ddg-search and time. This wraps every app handed to
uvicorn.Config, which is where mcp-proxy passes its whole Starlette app, and
answers 421 unless Host is 127.0.0.1:<port> or localhost:<port>.

Run by the launcher: uv run --no-project --with mcp-proxy==0.13.0 python mcp_guard.py <mcp-proxy args>
mcp-proxy is pinned, so its startup path only changes when the pin does. If uvicorn
is ever called without port=, every request is refused (fails closed).
"""
import sys


def guard(app, port):
    allowed = {f"127.0.0.1:{port}".encode(), f"localhost:{port}".encode()}

    async def guarded(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host = dict(scope.get("headers") or []).get(b"host", b"").lower()
            if host not in allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send({"type": "http.response.start", "status": 421,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Misdirected request"})
                return
        await app(scope, receive, send)  # lifespan and allowed requests
    return guarded


def install():
    import uvicorn
    original = uvicorn.Config.__init__

    def init(self, app, *args, **kwargs):
        original(self, guard(app, kwargs.get("port")), *args, **kwargs)
    uvicorn.Config.__init__ = init


def main():
    install()
    from mcp_proxy.__main__ import main as proxy_main
    sys.argv[0] = "mcp-proxy"
    proxy_main()


if __name__ == "__main__":
    main()
