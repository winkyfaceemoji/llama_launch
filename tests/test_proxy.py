import gzip
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy

TAG = b'<script src="/agent-jobs/inject.js"></script>'
PAGE = b"<!doctype html><html><head></head><body><div id=app></div><script>var s='</body>';</script></body></html>"
SEEN = {}


class FakeLlama(BaseHTTPRequestHandler):
    def do_POST(self):
        sent = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if b'"stream": true' in sent:  # a streamed chat reply, set by the test
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(SEEN["stream_reply"])
            return
        if "dies" in self.path:  # llama-server crashing mid-reply: headers and part of the stream, then gone
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"data: {}\n\n")
            return
        body = b'{"choices": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        SEEN["llama_headers"] = dict(self.headers)
        if "broken" in self.path:
            body, status = b"Error: gzip is not supported by this browser", 415
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "badgzip" in self.path:
            body = b"this is not gzip data"
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "corruptgzip" in self.path or "shortbody" in self.path:
            body = gzip.compress(PAGE)
            body = body[:12] + bytes([255]) * 20 if "corruptgzip" in self.path else body
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body) + (50 if "shortbody" in self.path else 0)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "If-None-Match" in self.headers:
            self.send_response(304)
            self.send_header("ETag", '"abc"')
            self.end_headers()
            return
        body = gzip.compress(PAGE)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("ETag", '"abc"')
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class FakeAgents(BaseHTTPRequestHandler):
    def _any(self):
        SEEN["agents"] = (self.command, self.path, dict(self.headers))
        data = json.dumps({"ok": True, "jobs": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _any

    def log_message(self, *_):
        pass


def serve(handler):
    s = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


class InjectTests(unittest.TestCase):
    def test_inserts_before_last_body(self):
        out = proxy.inject_script(PAGE)
        self.assertEqual(out.count(TAG), 1)
        self.assertTrue(out.endswith(TAG + b"</body></html>"))

    def test_no_body_tag_appends(self):
        self.assertTrue(proxy.inject_script(b"<p>hi</p>").endswith(TAG))

    def test_upper_case_body(self):
        self.assertIn(TAG + b"</BODY>", proxy.inject_script(b"<BODY>x</BODY>"))

    def test_api_target_allow_list(self):
        t = proxy.api_target
        self.assertEqual(t("GET", "/agent-jobs/api/jobs"), "/jobs")
        self.assertEqual(t("POST", "/agent-jobs/api/jobs/20261014-182345-3014/apply"), "/jobs/20261014-182345-3014/apply")
        self.assertEqual(t("POST", "/agent-jobs/api/trash/empty"), "/trash/empty")
        for method, path in [("POST", "/agent-jobs/api/jobs"), ("GET", "/agent-jobs/api/jobs/x/apply"),
                             ("POST", "/agent-jobs/api/jobs/..%2F..%2Fx/apply"), ("POST", "/agent-jobs/api/jobs/20261014-182345-3014/delete"),
                             ("GET", "/agent-jobs/api/mcp"), ("DELETE", "/agent-jobs/api/trash/empty")]:
            self.assertIsNone(t(method, path), (method, path))


def fetch_call(url, name="fetch"):
    # An assistant turn as the Web UI sends it back: the tool call, then the tool's result.
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": name, "arguments": json.dumps({"url": url})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "page text"}]


class UnopenedCitationTests(unittest.TestCase):
    def test_a_cited_page_the_conversation_never_opened_is_reported(self):
        messages = [{"role": "user", "content": "latest llama.cpp?"}, *fetch_call("https://a.test/read")]
        answer = "See https://a.test/read/ and https://b.test/snippet-only."
        self.assertEqual(proxy.unopened_citations(messages, answer), ["https://b.test/snippet-only"])

    def test_pages_opened_earlier_in_the_chat_or_by_any_url_tool_count(self):
        messages = [*fetch_call("https://a.test/x"), {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "and more?"}, *fetch_call("https://b.test/y", name="fetch_content")]
        self.assertEqual(proxy.unopened_citations(messages, "https://a.test/x https://b.test/y#part"), [])

    def test_a_link_the_user_gave_is_not_reported(self):
        messages = [{"role": "user", "content": "Summarise https://u.test/doc please"}]
        self.assertEqual(proxy.unopened_citations(messages, "From https://u.test/doc: ..."), [])

    def test_urls_in_code_are_not_citations(self):
        self.assertEqual(proxy.unopened_citations([], "Run `curl https://c.test/install.sh`."), [])


def sse(*deltas, done=True):
    # A streamed reply as llama-server sends it: one data line per chunk, then [DONE].
    out = b"".join(b"data: " + json.dumps({"id": "chatcmpl-1", "object": "chat.completion.chunk",
                                           "choices": [{"index": 0, "delta": d}]}).encode() + b"\n\n" for d in deltas)
    return out + (b"data: [DONE]\n\n" if done else b"")


def through(stream, data, size=7):
    # Feed the filter in small uneven pieces, as read1 returns them.
    return b"".join(stream.feed(data[i:i + size]) for i in range(0, len(data), size)) + stream.finish()


def content_of(data):
    text = ""
    for line in data.split(b"\n"):
        if line.startswith(b"data: {"):
            text += json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
    return text


class CitationStreamTests(unittest.TestCase):
    def test_a_final_answer_citing_an_unopened_page_ends_with_a_warning_before_done(self):
        data = sse({"content": "See https://b.test/"}, {"content": "snippet."}, {})
        out = through(proxy.CitationStream([]), data)
        self.assertTrue(out.startswith(data[:-len(b"data: [DONE]\n\n")]))
        self.assertTrue(out.endswith(b"data: [DONE]\n\n"))
        self.assertEqual(content_of(out), "See https://b.test/snippet.\n\n> Warning: cites pages that were never "
                                          "opened: https://b.test/snippet")

    def test_a_reply_that_calls_a_tool_or_cites_only_opened_pages_passes_unchanged(self):
        calls = sse({"content": "Let me check https://b.test/x"},
                    {"tool_calls": [{"index": 0, "function": {"name": "fetch", "arguments": "{}"}}]})
        self.assertEqual(through(proxy.CitationStream([]), calls), calls)
        opened = sse({"content": "From https://a.test/read."})
        self.assertEqual(through(proxy.CitationStream(fetch_call("https://a.test/read")), opened), opened)

    def test_a_failing_check_never_cuts_the_reply_short(self):
        data = sse({"content": "See https://b.test/x"})
        with mock.patch.object(proxy, "unopened_citations", side_effect=RuntimeError("bug")):
            self.assertEqual(through(proxy.CitationStream([]), data), data)

    def test_bytes_are_passed_on_as_soon_as_a_line_is_complete(self):
        stream = proxy.CitationStream([])
        line = sse({"content": "hi"}, done=False)
        self.assertEqual(stream.feed(line), line)


class ProxyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.llama = serve(FakeLlama)
        cls.agents = serve(FakeAgents)
        proxy._UP = cls.llama.server_port
        proxy._AGENTS = cls.agents.server_port
        cls.proxy = serve(proxy.Handler)
        proxy._LISTEN = cls.proxy.server_port

    @classmethod
    def tearDownClass(cls):
        for s in (cls.proxy, cls.llama, cls.agents):
            s.shutdown()
            s.server_close()

    def get(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=10)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_upstream_dying_mid_stream_adds_no_second_response(self):
        real_read1 = http.client.HTTPResponse.read1
        calls = []

        def read1_then_reset(resp, n=-1):  # a killed llama-server: the next read after the first chunk fails
            calls.append(n)
            if len(calls) > 1:
                raise ConnectionResetError(10054, "connection reset by peer")
            return real_read1(resp, n)

        with mock.patch.object(http.client.HTTPResponse, "read1", read1_then_reset), \
                socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=10) as s:
            s.sendall(b"POST /v1/dies HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 200"), data[:40])
        self.assertEqual(data.count(b"HTTP/1."), 1, data)  # no "HTTP/1.0 502" written into the stream
        self.assertTrue(data.endswith(b"data: {}\n\n"), data[-40:])

    def test_webui_page_gets_script_and_no_store(self):
        r, body = self.get("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(TAG, body)  # plain, decompressed
        self.assertIsNone(r.getheader("Content-Encoding"))
        self.assertEqual(r.getheader("Content-Length"), str(len(body)))
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIsNone(r.getheader("ETag"))
        self.assertEqual(r.getheader("Cross-Origin-Embedder-Policy"), "require-corp")
        self.assertIn("gzip", SEEN["llama_headers"].get("Accept-Encoding", ""))

    def test_webui_page_error_is_relayed_untouched(self):
        r, body = self.get("GET", "/?broken")  # path "/" routes through _webui_page
        self.assertEqual(r.status, 415)
        self.assertEqual(body, b"Error: gzip is not supported by this browser")
        self.assertNotIn(TAG, body)

    def test_other_path_error_is_relayed_untouched(self):
        r, body = self.get("GET", "/broken")
        self.assertEqual(r.status, 415)
        self.assertNotIn(TAG, body)

    def test_webui_page_ignores_conditional_and_range_headers(self):
        for h in ("If-None-Match", "If-Modified-Since", "If-Match", "If-Unmodified-Since", "If-Range", "Range"):
            SEEN.pop("llama_headers", None)
            r, body = self.get("GET", "/", {h: '"abc"'})
            self.assertEqual(r.status, 200, h)
            self.assertIn(TAG, body, h)
            self.assertNotIn(h.lower(), {k.lower() for k in SEEN["llama_headers"]}, h)

    def test_webui_page_bad_gzip_is_502(self):
        for q in ("badgzip", "corruptgzip", "shortbody"):
            r, _ = self.get("GET", "/?" + q)
            self.assertEqual(r.status, 502, q)

    def test_panel_files_served_with_coep(self):
        r, body = self.get("GET", "/agent-jobs")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Cross-Origin-Embedder-Policy"), "require-corp")
        self.assertEqual(r.getheader("Cross-Origin-Resource-Policy"), "same-origin")
        self.assertEqual(r.getheader("Content-Security-Policy"), "frame-ancestors 'self'")
        self.assertEqual(r.getheader("X-Frame-Options"), "SAMEORIGIN")
        r, _ = self.get("GET", "/agent-jobs/../proxy.py")
        self.assertEqual(r.status, 404)
        r, _ = self.get("GET", "/agent-jobs/secret.txt")
        self.assertEqual(r.status, 404)

    def test_api_passthrough_forwards_only_authorization(self):
        r, body = self.get("GET", "/agent-jobs/api/jobs", {"Authorization": "Bearer k", "Cookie": "c=1", "X-Other": "1"})
        self.assertEqual(r.status, 200)
        method, path, headers = SEEN["agents"]
        lower = {k.lower(): v for k, v in headers.items()}
        self.assertEqual((method, path), ("GET", "/jobs"))
        self.assertEqual(lower["authorization"], "Bearer k")
        self.assertEqual(lower["host"], f"127.0.0.1:{self.agents.server_port}")
        self.assertNotIn("cookie", lower)
        self.assertNotIn("x-other", lower)

    def test_api_never_adds_a_key(self):
        self.get("GET", "/agent-jobs/api/jobs")
        lower = {k.lower() for k in SEEN["agents"][2]}
        self.assertNotIn("authorization", lower)

    def test_api_unknown_path_is_404_without_reaching_agents(self):
        SEEN.pop("agents", None)
        r, _ = self.get("POST", "/agent-jobs/api/mcp")
        self.assertEqual(r.status, 404)
        self.assertNotIn("agents", SEEN)

    def test_panel_refuses_a_foreign_host(self):
        for path in ("/agent-jobs", "/agent-jobs/panel.js", "/agent-jobs/api/jobs", "/agent-jobs/nothing"):
            for host in ("evil.example", f"evil.example:{proxy._LISTEN}", "127.0.0.1", f"127.0.0.1:{proxy._LISTEN + 1}",
                         f"127.0.0.1.evil.example:{proxy._LISTEN}"):
                SEEN.pop("agents", None)
                r, _ = self.get("GET", path, {"Host": host, "Authorization": "Bearer k"})
                self.assertEqual(r.status, 403, (path, host))
                self.assertNotIn("agents", SEEN, (path, host))

    def test_panel_accepts_the_proxys_own_host(self):
        for host in (f"127.0.0.1:{proxy._LISTEN}", f"localhost:{proxy._LISTEN}", f"LOCALHOST:{proxy._LISTEN}"):
            SEEN.pop("agents", None)
            r, _ = self.get("GET", "/agent-jobs/api/jobs", {"Host": host, "Authorization": "Bearer k"})
            self.assertEqual(r.status, 200, host)
            self.assertIn("agents", SEEN, host)
            r, _ = self.get("GET", "/agent-jobs", {"Host": host})
            self.assertEqual(r.status, 200, host)

    def test_other_routes_do_not_check_host(self):
        r, body = self.get("GET", "/", {"Host": "evil.example"})
        self.assertEqual(r.status, 200)
        self.assertIn(TAG, body)

    CHAT_PATHS = ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completion", "/completions")

    def post(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=10)
        c.request("POST", path, body=b'{"messages": []}', headers={"Content-Type": "application/json"})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_chat_is_paused_while_a_model_test_runs(self):
        # The launcher writes the lock; the test itself talks to llama-server on 8081, not here.
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "model_test.lock")
            open(lock, "w").close()
            with mock.patch.object(proxy, "MODEL_TEST_LOCK", lock):
                for path in self.CHAT_PATHS:
                    r, body = self.post(path)
                    self.assertEqual(r.status, 503, path)
                    self.assertIn("model test is running", json.loads(body)["error"]["message"])
                r, body = self.get("GET", "/")  # the Web UI page and the panel stay up
                self.assertEqual(r.status, 200)
                r, _ = self.get("GET", "/agent-jobs/panel.js")
                self.assertEqual(r.status, 200)

    def post_with(self, path, headers):
        c = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=10)
        c.request("POST", path, body=b'{"messages": []}', headers={"Content-Type": "application/json", **headers})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_the_model_test_itself_gets_through_with_the_lock_token(self):
        # The test's own requests go through the proxy so they get the user's system prompt and date (1B).
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "model_test.lock")
            with open(lock, "w") as f:
                f.write("tok-123\n")
            with mock.patch.object(proxy, "MODEL_TEST_LOCK", lock):
                self.assertEqual(self.post_with("/v1/chat/completions", {"X-Model-Test": "tok-123"}).status, 200)
                self.assertEqual(self.post_with("/v1/chat/completions", {"X-Model-Test": "wrong"}).status, 503)
                self.assertEqual(self.post_with("/v1/chat/completions", {}).status, 503)
            open(lock, "w").close()  # an empty lock has no token: nothing gets through
            with mock.patch.object(proxy, "MODEL_TEST_LOCK", lock):
                self.assertEqual(self.post_with("/v1/chat/completions", {"X-Model-Test": ""}).status, 503)

    def test_chat_passes_without_the_lock(self):
        with mock.patch.object(proxy, "MODEL_TEST_LOCK", os.path.join(os.path.dirname(__file__), "no-such.lock")):
            for path in self.CHAT_PATHS:
                r, _ = self.post(path)
                self.assertEqual(r.status, 200, path)

    def test_a_streamed_chat_answer_citing_an_unopened_page_gets_the_warning(self):
        SEEN["stream_reply"] = sse({"content": "Per https://b.test/news and https://a.test/read."})
        body = json.dumps({"stream": True, "messages": [{"role": "user", "content": "news?"},
                                                         *fetch_call("https://a.test/read")]})
        with mock.patch.object(proxy, "MODEL_TEST_LOCK", os.path.join(os.path.dirname(__file__), "no-such.lock")):
            c = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=10)
            c.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
            out = c.getresponse().read()
            c.close()
        self.assertTrue(out.endswith(b"data: [DONE]\n\n"))
        self.assertIn("never opened: https://b.test/news", content_of(out))
        self.assertNotIn("a.test", content_of(out).split("Warning")[1])

    def test_api_returns_502_when_agents_server_down(self):
        saved = proxy._AGENTS
        proxy._AGENTS = 1  # nothing listens on port 1
        try:
            r, body = self.get("GET", "/agent-jobs/api/jobs")
        finally:
            proxy._AGENTS = saved
        self.assertEqual(r.status, 502)
        self.assertEqual(json.loads(body)["message"], "Agents server isn't running.")


if __name__ == "__main__":
    unittest.main()
