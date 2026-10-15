import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "launcher.pyw")
_loader = importlib.machinery.SourceFileLoader("launcher", _PATH)
_spec = importlib.util.spec_from_loader("launcher", _loader)
launcher = importlib.util.module_from_spec(_spec)
_loader.exec_module(launcher)

KEY = "test-key"


class FakeLlama(BaseHTTPRequestHandler):
    # A "good" llama-server: 401 without the key on chat and /tools, 404 on
    # /cors-proxy (not enabled), health ok.
    leaky = False
    health = 200

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(self.health, {"status": "ok"} if self.health == 200 else {"error": "loading"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        authorized = self.headers.get("Authorization") == f"Bearer {KEY}"
        if self.path == "/v1/chat/completions" and (authorized or self.leaky):
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": "Hi!"}}]})
        elif self.path in ("/v1/chat/completions", "/tools"):
            self._send(401, {"error": "Invalid API Key"})
        else:
            self._send(404, {"error": "not found"})


class LeakyLlama(FakeLlama):
    leaky = True


class LoadingLlama(FakeLlama):
    health = 503


class FakeServerTest(unittest.TestCase):
    handler = FakeLlama

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class GoodServerTests(FakeServerTest):
    def test_all_checks_pass(self):
        results = launcher.probe_server(self.base, KEY)
        self.assertEqual([(name, ok) for name, ok, _ in results], [
            ("chat with key", True),
            ("chat without key", True),
            ("tools without key", True),
            ("cors-proxy without key", True),
        ])
        self.assertTrue(launcher.check_verdict(results)[0])

    def test_wrong_key_fails_chat_with_key(self):
        name, ok, detail = launcher.probe_server(self.base, "wrong")[0]
        self.assertEqual((name, ok), ("chat with key", False))
        self.assertIn("401", detail)

    def test_wait_healthy_true_when_ok(self):
        self.assertTrue(launcher.wait_healthy(self.base, 2))


class LeakyServerTests(FakeServerTest):
    handler = LeakyLlama

    def test_chat_without_key_fails_and_verdict_names_it(self):
        results = launcher.probe_server(self.base, KEY)
        self.assertEqual(results[1][:2], ("chat without key", False))
        self.assertIn("200", results[1][2])
        passed, message = launcher.check_verdict(results)
        self.assertFalse(passed)
        self.assertIn("chat without key", message)


class LoadingServerTests(FakeServerTest):
    handler = LoadingLlama

    def test_wait_healthy_false_at_timeout(self):
        start = time.monotonic()
        self.assertFalse(launcher.wait_healthy(self.base, 2))
        self.assertLess(time.monotonic() - start, 4)

    def test_wait_healthy_stops_when_the_server_exits(self):
        # A build that crashes on start must not make the check sit out the whole timeout.
        start = time.monotonic()
        self.assertFalse(launcher.wait_healthy(self.base, 30, alive=lambda: False))
        self.assertLess(time.monotonic() - start, 4)


class NoServerTests(unittest.TestCase):
    def test_connection_error_fails_every_check(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        results = launcher.probe_server(f"http://127.0.0.1:{port}", KEY, timeout=2)
        self.assertEqual([ok for _, ok, _ in results], [False] * 4)
        self.assertTrue(all(detail for _, _, detail in results))


if __name__ == "__main__":
    unittest.main()
