import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import llm

SEEN = {}
SSE_BODY = ('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":'
            '[{"type":"text","text":"sse ok"}]}}\n\n')


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN["auth"], SEEN["body"] = self.headers.get("Authorization"), body
        p = self.path
        if "/fail" in p:
            return self._reply(500, "text/plain", b"model not loaded")
        if "/sse" in p:
            return self._reply(200, "text/event-stream", SSE_BODY.encode())
        if "/empty" in p:
            out = {}
        elif p == "/v1/chat/completions":
            out = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        elif body["params"]["name"] == "bad":
            out = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "nope"}], "isError": True}}
        else:
            out = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
        self._reply(200, "application/json", json.dumps(out).encode())

    def _reply(self, status, ctype, data):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def chat(self, path, **kw):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("k123\n")
        try:
            return llm.chat([{"role": "user", "content": "x"}], url=self.base + path, key_file=f.name, **kw)
        finally:
            os.unlink(f.name)

    def test_chat_sends_key_and_tools(self):
        msg = self.chat("/v1/chat/completions", tools=[{"type": "function"}])
        self.assertEqual(msg["content"], "hi")
        self.assertEqual(SEEN["auth"], "Bearer k123")
        self.assertEqual(SEEN["body"]["tools"], [{"type": "function"}])

    def test_mcp_call_joins_text(self):
        text = llm.mcp_call("ddg-search", "search", {"query": "q"}, url=self.base + "/servers/{server}/mcp")
        self.assertEqual(text, "a\nb")
        self.assertEqual(SEEN["body"]["params"], {"name": "search", "arguments": {"query": "q"}})

    def test_mcp_call_raises_on_tool_error(self):
        with self.assertRaises(RuntimeError) as cm:
            llm.mcp_call("x", "bad", {}, url=self.base + "/servers/{server}/mcp")
        self.assertIn("nope", str(cm.exception))

    def test_http_error_keeps_body(self):
        with self.assertRaises(RuntimeError) as cm:
            self.chat("/v1/fail")
        self.assertIn("500", str(cm.exception))
        self.assertIn("model not loaded", str(cm.exception))

    def test_sse_reply_is_parsed(self):
        text = llm.mcp_call("sse", "search", {"query": "q"}, url=self.base + "/servers/{server}/mcp")
        self.assertEqual(text, "sse ok")

    def test_chat_missing_choices_is_runtime_error(self):
        with self.assertRaises(RuntimeError) as cm:
            self.chat("/v1/empty")
        self.assertIn("unexpected reply from llama-server", str(cm.exception))

    def test_mcp_missing_result_is_runtime_error(self):
        with self.assertRaises(RuntimeError) as cm:
            llm.mcp_call("empty", "search", {}, url=self.base + "/servers/{server}/mcp")
        self.assertIn("unexpected reply from empty/search", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
