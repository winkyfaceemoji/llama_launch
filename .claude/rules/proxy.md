---
paths: proxy.py
---

# proxy.py

- **Stream with `r.read1(n)`, never `r.read(n)`.** `read(n)` waits until n bytes arrive, so streamed tokens (~200 bytes each) reach the Web UI in bursts.
- **Nothing that changes between requests goes at the start of the prompt.** llama-server reuses its KV cache only for the unchanged prefix, so a per-minute timestamp forced a full reprocess of the conversation every minute (ADR 0004). The date is fine; the time is not.
- **No upstream timeout.** A long first prompt can take minutes before the first byte; a timeout returns a 502 and throws the work away.
- **The response body is delimited by connection close.** The upstream `Transfer-Encoding` header is dropped and `read1` returns de-chunked bytes, which only works because `BaseHTTPRequestHandler` speaks HTTP/1.0. Don't set `protocol_version = "HTTP/1.1"` without re-adding chunking.
- Only `POST /v1/chat/completions` is rewritten, and it *replaces* any system message the client sent. Agents must use `:8081` directly to keep their own system prompt.
- `config.json` is read once at startup; edits to `system_prompt` need a relaunch.
