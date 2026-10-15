# Agent jobs side panel: design

Agreed in conversation on 2026-10-14. Builds on pass 2 ([spec](2026-10-14-local-agents.md), [ADR 0007](../../adr/0007-agents-server-standalone.md)).

## Goal

See and act on agent jobs from a panel on the right of the llama Web UI chat, styled like Claude's agent job cards, instead of the launcher window. When it works end to end, the launcher's Jobs pane is removed.

## Decisions

- **Painless embedding:** the panel is our own page, `/agent-jobs`, served by `proxy.py`. The proxy adds one `<script>` tag to the Web UI's page. That script draws a **Jobs** button and a right-side slide-out `<iframe>` showing `/agent-jobs`. It does not depend on the Web UI's DOM; `/agent-jobs` also works in its own tab.
- **Live step list (1B):** each job card shows its steps as they happen ("Searched …", "Read …", "Proposed plan: 4 changes").
- **Show and act, don't start (2A):** jobs are still started from chat. The panel approves, rejects, restores and queues follow-up steps, and empties the trash.
- **Look (3A):** as in the agreed mockup. Cards with a status chip; running jobs show the last steps and a counter; jobs that need review show every plan operation, the review note, and Approve / Reject; finished jobs collapse to one line. The palette is a Claude-style neutral one with light and dark following the system setting. No Claude name, logo or brand assets.
- **The launcher's Jobs pane is removed** once the panel works end to end (2B). Empty trash moves to the panel.

## Components

### Step recording (`agents/runner.py`, `agents/jobs.py`)

- `JobStore.append_step(job_id, text)` appends `{"t": <unix time>, "text": <text>}` as one line to `steps.jsonl` in the job's folder. It's best-effort: a failed write never fails the job.
- `run_agent` records one step per tool call, from the tool name and arguments:

| Tool | Step text |
|---|---|
| `web_search` | `Searched "<query>"` |
| `fetch_url` | `Read <host><path>` (cut to 80 characters) |
| `list_dir` | `Looked in <folder name>` |
| `read_file` | `Read <file name>` |
| `write_file` | `Wrote <file name>` |
| `propose_plan` | `Proposed plan: <n> changes`, or `Plan rejected: <first error>` |
| `propose_steps` | `Saved <n> follow-up jobs` |

  Errors become `Failed: <short error>`. A cap or model-error stop becomes `Stopped: <reason>`. The organise review pass becomes `Reviewed the plan`.
- Text is plain and capped at 200 characters. Control characters are stripped so a web page title can't fake extra lines.

### Agents server routes (`agents/server.py`, logic in stdlib modules)

Both routes are guarded by the existing `llm.check_request` (loopback Host plus API key):

- **`GET /jobs`** returns JSON for the 50 most recent jobs. Each job has `id`, `kind`, `state`, `note`, `title` (topic, goal, or folder plus goal), `created`, `updated`, `step_count`, and `steps` (the last 5). For a job awaiting approval it adds `plan` (the full operation list) and `review` (the full review text). The JSON is built by a pure function in a stdlib module so it can be unit-tested.
- **`POST /trash/empty`** empties the trash and returns `{"ok": bool, "message": str}`. It's refused while any job is `applying` or `restoring`, using the same helper the launcher uses today.

The existing `POST /jobs/{id}/{apply|reject|restore|queue}` routes are unchanged.

### Proxy (`proxy.py`)

- **Panel files:** serves `/agent-jobs` (the panel page) and `/agent-jobs/<file>` from the repo's `panel/` folder. Only known file names are served; anything else returns 404.
- **API pass-through:** passes `/agent-jobs/api/<rest>` to `http://127.0.0.1:8002/<rest>`, but only for `GET jobs`, `POST jobs/<id>/<action>` and `POST trash/empty`. It forwards the browser's `Authorization` header unchanged and sets `Host: 127.0.0.1:8002`. It **never adds a key itself**. If the agents server is down, it replies 502 with a short message.
- **Script injection:** for `GET /` and `/index.html` (the Web UI page), it asks llama-server for the page with `Accept-Encoding: gzip` (llama-server serves this page *only* gzipped; without gzip it answers 415 — verified 2026-10-14) and decompresses it with `gzip`. It then inserts `<script src="/agent-jobs/inject.js"></script>` before the last `</body>` (or at the end if there's none), sends it uncompressed with a fixed `Content-Length` and `Cache-Control: no-store`, and drops the `ETag`. A non-200 reply passes through untouched. A pure function does the insertion so it can be unit-tested. Every other path is relayed exactly as today.
- **Panel files carry `Cross-Origin-Embedder-Policy: require-corp`** (plus COOP same-origin and CORP same-origin), because the Web UI page sends COEP `require-corp`, and a browser blocks an embedded page that doesn't match.

### Panel files (`panel/`)

- **`inject.js`:** adds a fixed **Jobs** button at the top right with a count of running plus needs-review jobs, and a 360 px right-side frame showing `/agent-jobs` that slides in and out. The open state is remembered in `localStorage` under `agentJobsPanelOpen`. Everything it adds sits in its own root element with prefixed class names, so it can't clash with the Web UI's styles.
- **`index.html`, `panel.css`, `panel.js`:** the panel itself.
  - It reads the API key from the Web UI's saved settings: `localStorage["LlamaUi.config"].apiKey`. The frame has the same origin, so it can.
  - It polls `GET /agent-jobs/api/jobs` every 2 s, but only while visible.
  - It draws cards per the mockup. Approve, Reject, Restore and Queue steps post to the routes, and their buttons are disabled while a call is in flight. Restore and Empty trash ask for confirmation first; Empty trash defaults to No and says it's permanent.
  - It shows each route's `message` after an action.
  - It tells the inject script the job counts (`postMessage` to the same origin only) for the button's badge.
- **States the panel handles:**
  - No API key saved: "Paste your API key in Settings → General" plus a refresh button.
  - Agents server unreachable: "Agents server isn't running".
  - Key rejected: a 403 reply.
  - No jobs yet: an empty state.

### Launcher (`launcher.pyw`)

Remove the Jobs pane, `job_actions`, the `_refresh_jobs` polling, the review dialog, and the pane's Empty trash. Return `H_RUNNING` to 750. Keep the Agent folders dialog, the agents server process, and `cancel_unfinished` on exit. Remove any helpers this leaves unused, along with their tests.

## Security

- The panel never sees or stores a key beyond what the Web UI already stores. The proxy never adds one. The agents server checks the key and a loopback Host on every panel request.
- A rebinding page reaching `:8080` can't use `/agent-jobs/api` without the key, and the agents server's Host check still applies after the proxy rewrites Host.
- Step text and plan paths are shown with `textContent` and never as HTML.

## Risks (checked 2026-10-14 on build 9776)

- **CSP:** none, neither in a header nor in a `<meta>` tag.
- **Service worker:** `sw.js` exists but only handles `message` events, with no fetch handler, so it never caches or intercepts pages.
- **Compression:** gzip-only, as described under Proxy.
- **Browser cache:** the page is sent with `Cache-Control: public, max-age=31536000, immutable`. The proxy now sends `no-store`, but a browser that already cached the old page needs one hard refresh (Ctrl+F5). The README says so.
- **COEP:** the page sends `require-corp`; the panel files match it.
- **Empty trash:** it now runs inside the agents server under one lock shared with apply and restore. That closes the race with a restore started from chat, which was a deferred pass-2 item.

## Testing

- **Unit tests:**
  - script injection (normal page, no `</body>`, `</body>` inside a script string, upper case);
  - path allow-list and pass-through (header forwarding; no key added);
  - step-text building for each tool, with the cap and control-character stripping;
  - `append_step` best-effort behaviour;
  - the `GET /jobs` payload builder (cap, plan/review only when awaiting approval);
  - the empty-trash refusal.
- **Live check on the real stack, using the browser pane:**
  - the button appears and the panel opens;
  - a research job's steps arrive live;
  - an organise plan is shown in full, approved and then restored from the panel;
  - Empty trash is refused during an apply and works after it;
  - each panel state appears: no key, server down, no jobs.
- **After removing the launcher pane:** run the full suite, and check the launcher starts and exits cleanly.

## Workflow

Branch `jobs-side-panel`. Merge to `main`, push, and delete the branch when it's done and verified. No PR.

Record ADR 0008 ("The proxy injects a jobs panel into the Web UI"). Rejected alternatives: MCP UI resources (unverified support in this Web UI), a separate page only (not beside the chat), and the launcher pane (not where the chat is).
