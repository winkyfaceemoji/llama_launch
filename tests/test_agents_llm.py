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


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN["auth"], SEEN["body"] = self.headers.get("Authorization"), body
        if self.path == "/v1/chat/completions":
            out = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
        elif body["params"]["name"] == "bad":
            out = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "nope"}], "isError": True}}
        else:
            out = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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

    def test_chat_sends_key_and_tools(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("k123\n")
        try:
            msg = llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}],
                           url=self.base + "/v1/chat/completions", key_file=f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(msg["content"], "hi")
        self.assertEqual(SEEN["auth"], "Bearer k123")
        self.assertEqual(SEEN["body"]["tools"], [{"type": "function"}])

    def test_mcp_call_joins_text(self):
        text = llm.mcp_call("ddg-search", "search", {"query": "q"}, url=self.base + "/servers/{server}/mcp")
        self.assertEqual(text, "a\nb")
        self.assertEqual(SEEN["body"]["params"], {"name": "search", "arguments": {"query": "q"}})

    def test_mcp_call_raises_on_tool_error(self):
        with self.assertRaises(RuntimeError):
            llm.mcp_call("x", "bad", {}, url=self.base + "/servers/{server}/mcp")


if __name__ == "__main__":
    unittest.main()
