---
paths: proxy.py
---

# proxy.py

- **Stream with `r.read1(n)`, never `r.read(n)`.** `read(n)` waits until n bytes arrive, so streamed tokens (~200 bytes each) reach the Web UI in bursts.
- **Nothing that changes between requests goes at the start of the prompt.** llama-server reuses its KV cache only for the unchanged prefix, so a per-minute timestamp forced a full reprocess of the conversation every minute (ADR 0004). The date is fine; the time is not.
- **No upstream timeout.** A long first prompt can take minutes before the first byte; a timeout returns a 502 and throws the work away.
- **The response body is delimited by connection close.** The upstream `Transfer-Encoding` header is dropped and `read1` returns de-chunked bytes, which only works because `BaseHTTPRequestHandler` speaks HTTP/1.0. Don't set `protocol_version = "HTTP/1.1"` without re-adding chunking.
- Only `POST /v1/chat/completions` is rewritten, and it *replaces* any system message the client sent. Agents must use `:8081` directly to keep their own system prompt.
- **Forward the `Authorization` header untouched.** The Web UI's API key travels through this proxy to llama-server (ADR 0005); stripping it makes every chat return 401.
- `config.json` is read once at startup; edits to `system_prompt` need a relaunch.
- **llama-server serves the Web UI page (`/`, `/index.html`) gzip-only** (no `Accept-Encoding: gzip` gets 415). The proxy asks for gzip, unpacks it and inserts the `/agent-jobs/inject.js` tag. Non-200 or other-encoding replies pass through untouched.
- **The injected page is sent `no-store` with `ETag`, `Last-Modified` and `Content-Length` dropped.** llama-server marks it `immutable` for a year, so a cached copy would never show the tag or a later edit.
- **Panel files under `/agent-jobs` carry `Cross-Origin-Embedder-Policy: require-corp`** (plus CORP `same-origin`, `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN`, so a foreign site can't frame the panel and click Approve). The Web UI page sends COEP, so anything embedded in it must send it too or the browser blocks it. Only names in `PANEL_FILES` are served from `panel/`; everything else under `/agent-jobs` is 404.
- **The panel API is an allow-list** (`api_target`): `GET jobs`, `POST jobs/<id>/<apply|reject|restore|queue>`, `POST trash/empty`. Anything else under `/agent-jobs/api/` is 404 and never reaches the agents server.
- **The proxy never adds a key.** It forwards only the browser's own `Authorization` to the agents server, with `Host: 127.0.0.1:<agents port>`; the panel reads the key the Web UI stores. A down agents server gives 502 JSON.
- **The page fetch drops `If-*` and `Range` request headers.** Otherwise a reload sends the browser's `If-None-Match`, llama-server answers 304, and the browser keeps its cached untagged page.
