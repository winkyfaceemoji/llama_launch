"""HTTP calls to llama-server, the UVX MCP proxy and the agents server's approval routes. Standard library only."""
import hmac
import http.client
import json
import os
import urllib.error
import urllib.request

from .paths import APP_DIR

LLAMA_URL = "http://127.0.0.1:8081/v1/chat/completions"
MCP_URL = "http://127.0.0.1:8001/servers/{server}/mcp"
API_KEY_FILE = os.path.join(APP_DIR, "api_key.txt")
# The jobs panel's key for the agents server's routes, never the llama API key (ADR 0009).
APPROVAL_KEY_FILE = os.path.join(APP_DIR, "approval_key.txt")
AGENTS_PORT = 8002
AGENTS_BASE = f"http://127.0.0.1:{AGENTS_PORT}"
AGENTS_URL = f"{AGENTS_BASE}/mcp"  # the agents server's own MCP endpoint (not behind mcp-proxy)


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        with e:
            text = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {e.code}: {text[:500]}") from None
    except (http.client.HTTPException, UnicodeDecodeError) as e:
        # A reply cut off mid-way (IncompleteRead) is not an OSError; callers only catch RuntimeError/OSError.
        raise RuntimeError(f"{url} sent a broken reply: {e!r}"[:300]) from None
    if not ctype.startswith("text/event-stream"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"unexpected reply from {url}: {raw[:200]}") from None
    # The proxy may answer with one SSE event; the JSON-RPC reply is its last data line.
    for line in reversed(raw.splitlines()):
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"unexpected reply from {url}: {raw[:200]}")


def _snippet(data):
    return json.dumps(data)[:200]


def read_key(key_file=API_KEY_FILE):
    with open(key_file, encoding="utf-8") as f:
        return f.read().strip()


def check_request(headers, port, key):
    """True only if Host is this server's loopback address and the request carries the key.

    Guards the approval routes: a rebound web page sends its own Host, and no page knows the key.
    The server passes the approval key, so a client holding only the llama API key can't approve plans.
    """
    h = {str(k).lower(): v for k, v in headers.items()}
    if h.get("host", "").lower() not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return False
    return bool(key) and hmac.compare_digest(h.get("authorization", "").encode(), f"Bearer {key}".encode())


def chat(messages, tools=None, url=LLAMA_URL, key_file=API_KEY_FILE, timeout=900):
    key = read_key(key_file)
    body = {"messages": messages}
    if tools:
        body["tools"] = tools
    data = _post(url, body, {"Authorization": f"Bearer {key}"}, timeout)
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected reply from llama-server: {_snippet(data)}") from None


def mcp_call(server, tool, args, url=MCP_URL, timeout=120):
    # mcp-proxy runs --stateless, so one JSON-RPC POST per call works without
    # an initialize handshake (verified against mcp-proxy and mcp 2.3).
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    data = _post(url.format(server=server), request,
                 {"Accept": "application/json, text/event-stream"}, timeout)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected reply from {server}/{tool}: {_snippet(data)}")
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text)
    return text
