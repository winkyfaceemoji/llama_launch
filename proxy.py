import gzip, hmac, http.client, json, os, re, sys, zlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_SP = ""
_UP = 8081
_AGENTS = 8002  # agents server; argv[3] overrides
_LISTEN = 8080  # this proxy's own port; the panel only answers requests addressed to it

PANEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel")
PANEL_FILES = {
    "/agent-jobs": ("index.html", "text/html; charset=utf-8"),
    "/agent-jobs/": ("index.html", "text/html; charset=utf-8"),
    "/agent-jobs/inject.js": ("inject.js", "text/javascript; charset=utf-8"),
    "/agent-jobs/panel.js": ("panel.js", "text/javascript; charset=utf-8"),
    "/agent-jobs/panel.css": ("panel.css", "text/css; charset=utf-8"),
}
INJECT_TAG = b'<script src="/agent-jobs/inject.js"></script>'
# While the launcher runs a model test it writes this file; Web UI chat waits (the test uses 8081 directly).
MODEL_TEST_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_test.lock")
_CHAT_PATHS = ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completion", "/completions")
def _paused_for(token):
    # True while a model test runs, except for the test's own requests: the lock holds a random token
    # that only the launcher and the test know, so they get the user's system prompt like real chat.
    try:
        with open(MODEL_TEST_LOCK, encoding="utf-8") as f:
            secret = f.read().strip()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return not (secret and hmac.compare_digest(token.strip().encode(), secret.encode()))


_PAUSED = json.dumps({"error": {"message": "A model test is running in the launcher. Chat is paused until it "
                                           "finishes or you stop it.", "type": "unavailable"}}).encode()
WEBUI_PAGES = ("/", "/index.html")
_JOB_ID = r"\d{8}-\d{6}-[0-9a-f]{4}"
_API = [
    ("GET", re.compile(r"/agent-jobs/api/jobs"), lambda m: "/jobs"),
    ("POST", re.compile(rf"/agent-jobs/api/jobs/({_JOB_ID})/(apply|reject|restore|queue)"),
     lambda m: f"/jobs/{m.group(1)}/{m.group(2)}"),
    ("POST", re.compile(r"/agent-jobs/api/trash/empty"), lambda m: "/trash/empty"),
]
# The page llama-server serves is marked immutable for a year; ours changes, so never cache it.
_DROP_PAGE_HEADERS = ("content-encoding", "content-length", "transfer-encoding", "etag", "cache-control", "last-modified")


_DROP_REQUEST_HEADERS = ("host", "accept-encoding", "if-none-match", "if-modified-since", "if-match",
                         "if-unmodified-since", "if-range", "range")


def inject_script(html):
    """Insert the jobs-panel script tag before the last </body>, or append it."""
    i = html.lower().rfind(b"</body>")
    return html + INJECT_TAG if i < 0 else html[:i] + INJECT_TAG + html[i:]


def api_target(method, path):
    """The agents-server path for an allowed panel API call, else None."""
    for m, pattern, target in _API:
        match = pattern.fullmatch(path)
        if method == m and match:
            return target(match)
    return None


def _load():
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(cfg):
        with open(cfg, encoding="utf-8") as f:
            return json.load(f).get("system_prompt", "")
    return ""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, headers):
        try:
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass  # client went away

    def _webui_page(self):
        # llama-server only serves this page gzipped (no gzip -> 415), so ask for gzip, unpack, add our tag.
        # Conditional and range headers are dropped: a 304 or 206 would hand the browser its cached untagged copy.
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
        hdrs["Accept-Encoding"] = "gzip"
        conn = http.client.HTTPConnection("127.0.0.1", _UP, timeout=30)
        try:
            conn.request("GET", self.path, headers=hdrs)
            r = conn.getresponse()
            body = r.read()
            headers = r.getheaders()
            status = r.status
        finally:
            conn.close()
        enc = (dict((k.lower(), v) for k, v in headers).get("content-encoding") or "").lower()
        if status != 200 or enc not in ("", "gzip"):
            out = [(k, v) for k, v in headers if k.lower() not in ("content-length", "transfer-encoding")]
            return self._send(status, body, out)  # error pages and odd encodings pass through untouched
        if enc == "gzip":
            body = gzip.decompress(body)
        out = [(k, v) for k, v in headers if k.lower() not in _DROP_PAGE_HEADERS]
        out.append(("Cache-Control", "no-store"))
        self._send(200, inject_script(body), out)

    def _panel_file(self, path):
        name, ctype = PANEL_FILES[path]
        try:
            with open(os.path.join(PANEL_DIR, name), "rb") as f:
                body = f.read()
        except OSError:
            return self._send(404, b"Not found", [("Content-Type", "text/plain")])
        # The Web UI page sends COEP require-corp; an embedded page must match it or the browser blocks it.
        self._send(200, body, [("Content-Type", ctype), ("Cache-Control", "no-store"),
                               ("Cross-Origin-Embedder-Policy", "require-corp"),
                               ("Cross-Origin-Opener-Policy", "same-origin"),
                               ("Cross-Origin-Resource-Policy", "same-origin"),
                               ("Content-Security-Policy", "frame-ancestors 'self'"),  # only our own Web UI page may frame the panel
                               ("X-Frame-Options", "SAMEORIGIN"),
                               ("X-Content-Type-Options", "nosniff")])

    def _panel_api(self, target):
        # Pass the browser's Authorization through unchanged; never add a key here.
        hdrs = {"Host": f"127.0.0.1:{_AGENTS}", "Content-Length": "0"}
        if self.headers.get("Authorization"):
            hdrs["Authorization"] = self.headers["Authorization"]
        conn = http.client.HTTPConnection("127.0.0.1", _AGENTS, timeout=900)  # apply and restore can take minutes
        try:
            conn.request(self.command, target, body=b"" if self.command == "POST" else None, headers=hdrs)
            r = conn.getresponse()
            body, status, ctype = r.read(), r.status, r.getheader("Content-Type", "application/json")
        except OSError:
            body = json.dumps({"ok": False, "message": "Agents server isn't running."}).encode()
            return self._send(502, body, [("Content-Type", "application/json")])
        finally:
            conn.close()
        self._send(status, body, [("Content-Type", ctype), ("Cache-Control", "no-store")])

    def _relay(self):
        path = urlsplit(self.path).path
        if path.startswith("/agent-jobs"):
            # The agents server only sees the Host we rewrite, so refuse a rebound page's Host here.
            if (self.headers.get("Host") or "").lower() not in (f"127.0.0.1:{_LISTEN}", f"localhost:{_LISTEN}"):
                return self._send(403, b'{"ok": false, "message": "Forbidden host."}', [("Content-Type", "application/json")])
            if path.startswith("/agent-jobs/api/"):
                target = api_target(self.command, path)
                if target is None:
                    return self._send(404, b'{"ok": false, "message": "Not found."}', [("Content-Type", "application/json")])
                return self._panel_api(target)
            if self.command in ("GET", "HEAD") and path in PANEL_FILES:
                return self._panel_file(path)
            return self._send(404, b"Not found", [("Content-Type", "text/plain")])
        if self.command in ("GET", "HEAD") and path in WEBUI_PAGES:
            try:
                return self._webui_page()
            except (OSError, ValueError, EOFError, zlib.error, http.client.HTTPException) as e:  # bad gzip or short body
                return self._send(502, str(e).encode(), [("Content-Type", "text/plain")])

        cl = self.headers.get("Content-Length")
        te = self.headers.get("Transfer-Encoding", "").lower()
        if cl is not None:
            body = self.rfile.read(int(cl))
        elif "chunked" in te:
            # decode chunked request body manually
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                chunk_size = int(size_line, 16)
                if chunk_size == 0:
                    self.rfile.readline()  # trailing CRLF
                    break
                chunks.append(self.rfile.read(chunk_size))
                self.rfile.readline()  # CRLF after chunk
            body = b"".join(chunks) if chunks else None
        else:
            body = None

        # Checked per request (after reading the body), so no restart is needed either way.
        if self.command == "POST" and path in _CHAT_PATHS and _paused_for(self.headers.get("X-Model-Test", "")):
            return self._send(503, _PAUSED, [("Content-Type", "application/json")])

        if self.command == "POST" and "/v1/chat/completions" in self.path and _SP and body:
            try:
                d    = json.loads(body)
                m    = d.get("messages", [])
                # date only: a per-minute timestamp at the top of the prompt would
                # invalidate llama-server's prompt cache every minute
                today = datetime.now().strftime("%A, %B %d, %Y")
                sp    = f"Current date: {today}\n\n{_SP}"
                if not m or m[0].get("role") != "system":
                    d["messages"] = [{"role": "system", "content": sp}] + m
                else:
                    d["messages"][0]["content"] = sp
                body = json.dumps(d).encode()
            except Exception:
                pass

        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "transfer-encoding")}
        if body is not None:
            hdrs["Content-Length"] = str(len(body))

        try:
            # no timeout: long prompts can take minutes before the first byte
            conn = http.client.HTTPConnection("127.0.0.1", _UP, timeout=None)
            conn.request(self.command, self.path, body=body, headers=hdrs)
            r = conn.getresponse()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() != "transfer-encoding":
                    self.send_header(k, v)
            self.end_headers()
            while True:
                # read1 returns whatever has arrived; read(n) would hold
                # streamed tokens back until n bytes had accumulated
                chunk = r.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            conn.close()
        except (ConnectionAbortedError, BrokenPipeError):
            pass  # client disconnected mid-stream, normal
        except Exception as e:
            try:
                self.send_error(502, str(e))
            except Exception:
                pass  # connection already gone

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _relay

    def log_message(self, *_):
        pass  # suppress per-request noise


if __name__ == "__main__":
    listen = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    _UP    = int(sys.argv[2]) if len(sys.argv) > 2 else 8081
    _AGENTS = int(sys.argv[3]) if len(sys.argv) > 3 else 8002
    _LISTEN = listen

    _SP = _load()
    print(f"system prompt: {'loaded' if _SP else 'none'}", flush=True)
    print(f"proxy :{listen} -> :{_UP} (agents :{_AGENTS})", flush=True)

    ThreadingHTTPServer(("127.0.0.1", listen), Handler).serve_forever()
