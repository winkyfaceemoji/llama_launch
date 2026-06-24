import http.client, json, os, sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SP = ""
_UP = 8081


def _load():
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(cfg):
        with open(cfg, encoding="utf-8") as f:
            return json.load(f).get("system_prompt", "")
    return ""


class Handler(BaseHTTPRequestHandler):
    def _relay(self):
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

        if self.command == "POST" and "/v1/chat/completions" in self.path and _SP and body:
            try:
                d    = json.loads(body)
                m    = d.get("messages", [])
                now  = datetime.now().strftime("%A, %B %d, %Y %H:%M")
                sp   = f"Current date and time: {now}\n\n{_SP}"
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
            conn = http.client.HTTPConnection("127.0.0.1", _UP, timeout=300)
            conn.request(self.command, self.path, body=body, headers=hdrs)
            r = conn.getresponse()
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() != "transfer-encoding":
                    self.send_header(k, v)
            self.end_headers()
            while True:
                chunk = r.read(4096)
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

    _SP = _load()
    print(f"system prompt: {'loaded' if _SP else 'none'}", flush=True)
    print(f"proxy :{listen} -> :{_UP}", flush=True)

    ThreadingHTTPServer(("0.0.0.0", listen), Handler).serve_forever()
