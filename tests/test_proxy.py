import gzip
import http.client
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy

TAG = b'<script src="/agent-jobs/inject.js"></script>'
PAGE = b"<!doctype html><html><head></head><body><div id=app></div><script>var s='</body>';</script></body></html>"
SEEN = {}


class FakeLlama(BaseHTTPRequestHandler):
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
