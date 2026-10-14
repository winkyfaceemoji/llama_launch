"""HTTP calls to llama-server and the UVX MCP proxy. Standard library only."""
import json
import os
import urllib.request

from .paths import APP_DIR

LLAMA_URL = "http://127.0.0.1:8081/v1/chat/completions"
MCP_URL = "http://127.0.0.1:8001/servers/{server}/mcp"
API_KEY_FILE = os.path.join(APP_DIR, "api_key.txt")


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(messages, tools=None, url=LLAMA_URL, key_file=API_KEY_FILE, timeout=900):
    with open(key_file, encoding="utf-8") as f:
        key = f.read().strip()
    body = {"messages": messages}
    if tools:
        body["tools"] = tools
    data = _post(url, body, {"Authorization": f"Bearer {key}"}, timeout)
    return data["choices"][0]["message"]


def mcp_call(server, tool, args, url=MCP_URL, timeout=120):
    # mcp-proxy runs --stateless, so one JSON-RPC POST per call works without
    # an initialize handshake (verified against mcp-proxy and mcp 2.3).
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    data = _post(url.format(server=server), request,
                 {"Accept": "application/json, text/event-stream"}, timeout)
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    result = data["result"]
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text)
    return text
