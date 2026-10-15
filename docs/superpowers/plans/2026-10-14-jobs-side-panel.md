# Agent Jobs Side Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show and act on agent jobs from a slide-out panel on the right of the llama Web UI chat, styled like Claude's agent job cards, and remove the launcher's Jobs pane.

**Architecture:**
- **Steps:** jobs record each step to `steps.jsonl`.
- **Agents server:** gains a key-checked job list route and an Empty trash route. Empty trash, apply and restore share one lock, so the trash can't be emptied mid-move.
- **Proxy:** `proxy.py` serves the panel files, passes a small allow-list of API calls to the agents server without adding a key, and inserts one `<script>` tag into the Web UI page.
- **Panel:** that script draws a Jobs button and a right-side iframe showing the panel.

**Tech Stack:** Python standard library (`http.server`, `http.client`, `gzip`, `json`, `threading`), plain HTML/CSS/JS (no build step, no CDN), `mcp` 2.3 custom routes (server only).

**Spec:** `docs/superpowers/specs/2026-10-14-jobs-side-panel-design.md` (builds on `docs/superpowers/specs/2026-10-14-local-agents.md`, ADR 0007)

## Global Constraints

- Branch `jobs-side-panel`; commit there. At the end: merge to `main`, push, delete the branch. No PR. No Claude/Anthropic attribution in commits.
- Standard library only everywhere except `agents/server.py` (which may import `mcp`, `anyio`, `starlette`).
- Never bind or touch ports 8001, 8002, 8080 or 8081 while testing (the user's launcher may run). Use spare ports (18080, 18081, 18002). Stop whatever you start.
- llama-server serves the Web UI page **only gzip-compressed** (a request without `Accept-Encoding: gzip` gets `415`). The page carries `Cache-Control: public, max-age=31536000, immutable`, an `ETag`, `Cross-Origin-Embedder-Policy: require-corp` and `Cross-Origin-Opener-Policy: same-origin`, and has no CSP. Its service worker (`sw.js`) has no fetch handler. (Verified 2026-10-14 on build 9776.)
- Any page embedded under that COEP must itself send `Cross-Origin-Embedder-Policy: require-corp`, and its subresources must be same-origin or send `Cross-Origin-Resource-Policy`.
- The proxy never adds an API key. The panel reads the key the Web UI stores: `JSON.parse(localStorage["LlamaUi.config"]).apiKey`.
- Agents server routes check `llm.check_request(headers, PORT, key)`: Host exactly `127.0.0.1:<port>` or `localhost:<port>` plus `Authorization: Bearer <api_key.txt>`.
- Step text: plain, control characters removed, at most 200 characters, always shown with `textContent`.
- The panel polls every 2 s while visible and every 15 s while hidden (for the button's badge). It's 360 px wide. Its open state lives in `localStorage["agentJobsPanelOpen"]`.
- No Claude name, logo or brand assets. Sentence-case UI text. No emoji.
- Tests: `python -m unittest discover -s tests`. Each test file puts the repo root on `sys.path`.

## Review Focus

1. **The Web UI page comes back gzipped, in odd shapes, or with an error:** the proxy must decompress before inserting the script and must never send a corrupt page. An error page is passed through untouched (Task 3, `test_webui_page_error_is_relayed_untouched`).
2. **Untrusted text:** a step or plan path containing HTML or newlines (from a web page title or a file name) is shown as text and can't fake extra lines (Task 1, `test_append_step_strips_control_chars_and_caps`; Task 4 uses `textContent` only).
3. **Empty trash clicked while an apply or restore is moving files**, including a restore started from chat: refused, with nothing deleted (Task 2, `test_empty_trash_refused_while_files_move`).
4. **The panel API:** only the allow-listed calls are passed on, other paths get 404 without reaching the agents server, and the proxy never adds a key (Task 3, `test_api_passthrough_forwards_only_authorization` and `test_api_target_allow_list`).
5. **The agents server is down when the panel asks:** the proxy answers 502 with a clear JSON message instead of hanging or throwing (Task 3, `test_api_returns_502_when_agents_server_down`).

---

## File Structure

| File | Responsibility |
|---|---|
| `agents/jobs.py` | + `append_step`, `read_steps` |
| `agents/runner.py` | + `step_text`, `run_agent(on_step=...)`, step wiring in job kinds, `FILE_MOVES` lock around apply/restore |
| `agents/panel_api.py` (new) | `jobs_payload(store, limit)`, `trash_refusal(jobs)`, `empty_trash(ctx)`: pure or stdlib logic behind the server routes |
| `agents/server.py` | + `GET /jobs`, `POST /trash/empty` routes |
| `proxy.py` | + Web UI page script insertion, panel file serving, API pass-through |
| `panel/inject.js`, `panel/index.html`, `panel/panel.css`, `panel/panel.js` (new) | The button, the iframe and the panel UI |
| `launcher.pyw` | − Jobs pane, its helpers and polling; `H_RUNNING` back to 750 |
| `agents/llm.py` | − `agents_action` (only the pane used it) |
| `tests/test_agents_jobs.py`, `tests/test_agents_runner.py`, `tests/test_agents_panel_api.py` (new), `tests/test_proxy.py` (new), `tests/test_launcher.py`, `tests/test_agents_llm.py` | Tests |
| `README.md`, `CLAUDE.md`, `.claude/rules/*.md`, `docs/adr/0008-jobs-panel-in-web-ui.md` | Docs |

---

### Task 1: Record job steps

**Files:**
- Modify: `agents/jobs.py` (add two `JobStore` methods after `has`)
- Modify: `agents/runner.py` (add `step_text`, add `on_step` to `run_agent`, wire it in `run_research`, `run_plan`, `run_organize`)
- Test: `tests/test_agents_jobs.py`, `tests/test_agents_runner.py`

**Interfaces:**
- Produces: `JobStore.append_step(job_id, text) -> None` (never raises), `JobStore.read_steps(job_id, last=5) -> (int, list[str])`, `runner.step_text(name, args, result) -> str`, `runner.run_agent(..., on_step=None)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agents_jobs.py`, inside `class JobStoreTests`:

```python
    def test_append_and_read_steps(self):
        job = self.store.create("research", {})
        for i in range(7):
            self.store.append_step(job["id"], f"step {i}")
        count, last = self.store.read_steps(job["id"], last=5)
        self.assertEqual(count, 7)
        self.assertEqual(last, ["step 2", "step 3", "step 4", "step 5", "step 6"])

    def test_append_step_strips_control_chars_and_caps(self):
        job = self.store.create("research", {})
        self.store.append_step(job["id"], "Read evil\n<b>page</b>\x07" + "x" * 300)
        _, last = self.store.read_steps(job["id"])
        self.assertNotIn("\n", last[0])
        self.assertNotIn("\x07", last[0])
        self.assertIn("<b>page</b>", last[0])  # kept as text; the panel shows it with textContent
        self.assertLessEqual(len(last[0]), 200)

    def test_append_step_never_raises(self):
        self.store.append_step("not-an-id", "x")  # bad id: silently ignored
        job = self.store.create("research", {})
        os.makedirs(self.store.path(job["id"], "steps.jsonl"))  # a folder where the file should be
        self.store.append_step(job["id"], "x")  # write fails: still no exception

    def test_read_steps_skips_bad_lines_and_missing_file(self):
        job = self.store.create("research", {})
        self.assertEqual(self.store.read_steps(job["id"]), (0, []))
        with open(self.store.path(job["id"], "steps.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"t": 1, "text": "ok"}\nnot json\n{"t": 2}\n')
        self.assertEqual(self.store.read_steps(job["id"]), (1, ["ok"]))
```

Add to `tests/test_agents_runner.py`, inside `class RunAgentTests`:

```python
    def test_step_text_per_tool(self):
        st = runner.step_text
        self.assertEqual(st("web_search", {"query": "llama.cpp"}, "results"), 'Searched "llama.cpp"')
        self.assertEqual(st("fetch_url", {"url": "https://github.com/ggml-org/llama.cpp/releases?x=1"}, "page"),
                         "Read github.com/ggml-org/llama.cpp/releases")
        self.assertEqual(st("list_dir", {"path": "C:\\Users\\me\\Downloads\\"}, "a.pdf"), "Looked in Downloads")
        self.assertEqual(st("read_file", {"path": "C:\\x\\notes.txt"}, "text"), "Read notes.txt")
        self.assertEqual(st("write_file", {"path": "report.md", "content": "x"}, "wrote"), "Wrote report.md")
        self.assertEqual(st("propose_plan", {"operations": []}, "plan saved with 4 operations"), "Proposed plan: 4 changes")
        self.assertEqual(st("propose_plan", {}, "error: plan rejected:\n1: not allowed\n2: x"), "Plan rejected: 1: not allowed")
        self.assertEqual(st("propose_steps", {}, "saved 3 follow-up jobs"), "Saved 3 follow-up jobs")
        self.assertEqual(st("read_file", {"path": "x"}, "error: no read access to x"), "Failed: no read access to x")
        self.assertEqual(st("mystery", {}, "ok"), "Used mystery")

    def test_on_step_called_per_tool_call(self):
        seen = []
        chat = Script(call("echo", {"text": "hi"}), final("done"))
        runner.run_agent(chat, "sys", "task", [self.echo_tool()], on_step=seen.append)
        self.assertEqual(seen, ["Used echo"])

    def test_on_step_errors_never_stop_the_job(self):
        def boom(_):
            raise OSError("disk full")
        chat = Script(call("echo", {"text": "hi"}), final("done"))
        text, _ = runner.run_agent(chat, "sys", "task", [self.echo_tool()], on_step=boom)
        self.assertEqual(text, "done")
```

Add to `tests/test_agents_runner.py`, inside `class JobKindTests`:

```python
    def test_research_records_steps(self):
        job = self.start("research", {"topic": "llama"})
        runner.run_job(job, self.ctx(Script(call("web_search", {"query": "llama"}), final("# Report"))))
        count, last = self.store.read_steps(job["id"])
        self.assertEqual(last, ['Searched "llama"'])

    def test_organize_records_review_step(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        _, last = self.store.read_steps(job_id)
        self.assertEqual(last[-2:], ["Proposed plan: 1 changes", "Reviewed the plan"])

    def test_capped_job_records_stop(self):
        job = self.start("research", {"topic": "t"})
        replies = [call("web_search", {"query": "q"})] * 41 + [final("partial")]
        runner.run_job(job, self.ctx(Script(*replies)))
        _, last = self.store.read_steps(job["id"])
        self.assertTrue(last[-1].startswith("Stopped: stopped after 40 tool calls"))
```

(`self.organize`, `self.start`, `self.ctx`, `call`, `final`, `Script`, `self.echo_tool` already exist in that file; check their exact names before relying on them and adapt if they differ.)

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m unittest tests.test_agents_jobs tests.test_agents_runner -v`
Expected: the new tests ERROR with `AttributeError: ... 'append_step'` / `'step_text'` / unexpected keyword `on_step`.

- [ ] **Step 3: Implement**

`agents/jobs.py`, add at module top (after the existing imports):

```python
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
STEP_MAX = 200
```

and add these methods to `JobStore` (after `has`):

```python
    def append_step(self, job_id, text):
        # Best-effort progress line for the jobs panel; a failed write never fails the job.
        try:
            clean = _CONTROL.sub(" ", str(text)).strip()[:STEP_MAX]
            with open(self.path(job_id, "steps.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "text": clean}) + "\n")
        except (KeyError, OSError, ValueError):
            pass

    def read_steps(self, job_id, last=5):
        # (number of steps, the last `last` step texts); unreadable lines are skipped.
        try:
            raw = _retry(lambda: _read_text(self.path(job_id, "steps.jsonl")))
        except (KeyError, OSError):
            return 0, []
        texts = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
        return len(texts), texts[-last:] if last else []
```

(`re`, `json`, `time` are already imported in `jobs.py`; add any that aren't.)

`agents/runner.py`, add `from urllib.parse import urlsplit` to the imports, then add before `run_agent`:

```python
def _name_of(path):
    path = str(path or "").rstrip("\\/")
    return os.path.basename(path) or path


def step_text(name, args, result):
    """One line for the jobs panel describing a finished tool call."""
    args = args if isinstance(args, dict) else {}
    result = str(result or "")
    if name == "propose_plan":
        if result.startswith("error:"):
            lines = [l for l in result.splitlines()[1:] if l.strip()]
            return "Plan rejected: " + (lines[0] if lines else result[len("error:"):].strip())
        n = result.split("with ", 1)[-1].split(" ", 1)[0]
        return f"Proposed plan: {n} changes"
    if result.startswith("error:"):
        return "Failed: " + result[len("error:"):].strip()
    if name == "web_search":
        return f'Searched "{args.get("query", "")}"'
    if name == "fetch_url":
        parts = urlsplit(str(args.get("url", "")))
        return "Read " + (parts.netloc + parts.path)[:80]
    if name == "list_dir":
        return "Looked in " + _name_of(args.get("path"))
    if name == "read_file":
        return "Read " + _name_of(args.get("path"))
    if name == "write_file":
        return "Wrote " + _name_of(args.get("path"))
    if name == "propose_steps":
        return result[:1].upper() + result[1:]
    return f"Used {name}"


def _parse_args(raw):
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}
```

In `run_agent`: add the parameter `on_step=None` at the end of the signature. In the per-call loop, right after `result = _invoke(fns, fn["name"], fn.get("arguments"))`, add:

```python
                    if on_step:
                        try:
                            on_step(step_text(fn["name"], _parse_args(fn.get("arguments")), result))
                        except Exception:
                            pass  # progress lines are best-effort
```

Then wire the job kinds:

```python
def _steps(job, ctx):
    return lambda text: ctx.store.append_step(job["id"], text)
```

- `run_research`: pass `on_step=_steps(job, ctx)` to `run_agent`; after it returns, `if stop: ctx.store.append_step(job["id"], f"Stopped: {stop}")`.
- `run_plan`: the same.
- `run_organize`: the same, plus `ctx.store.append_step(job["id"], "Reviewed the plan")` right after the review chat call (before writing `review.md`).

- [ ] **Step 4: Run the tests**

Run: `python -m unittest tests.test_agents_jobs tests.test_agents_runner -v`, then the full suite: `python -m unittest discover -s tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agents/jobs.py agents/runner.py tests/test_agents_jobs.py tests/test_agents_runner.py
git commit -m "agents: record each job step for the jobs panel"
```

---

### Task 2: Job list and Empty trash on the agents server

**Files:**
- Create: `agents/panel_api.py`
- Modify: `agents/runner.py` (add the `FILE_MOVES` lock around applying and restoring)
- Modify: `agents/server.py` (two routes)
- Test: `tests/test_agents_panel_api.py`

**Interfaces:**
- Consumes: `JobStore.read_steps` (Task 1), `JobStore.list/has/read_json/read_text`, `plans.empty_trash(policy)`, `llm.check_request`.
- Produces: `panel_api.jobs_payload(store, limit=50) -> list[dict]`, `panel_api.trash_refusal(jobs) -> str | None`, `panel_api.empty_trash(ctx) -> dict` (`{"ok": bool, "message": str}`), `runner.FILE_MOVES` (a `threading.Lock`), and routes `GET /jobs` → `{"ok": true, "jobs": [...]}` and `POST /trash/empty` → `{"ok", "message"}`.

Each job dict from `jobs_payload` has `id, kind, state, note, created, updated, title, step_count, steps, can_restore, queue_count`, plus `plan` and `review` when `state == "awaiting_approval"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents_panel_api.py`:

```python
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import jobs, panel_api, paths, plans, runner


class PanelApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dl = os.path.join(root, "Downloads")
        os.makedirs(self.dl)
        self.policy = paths.Policy(os.path.join(root, "sandbox"), folders_file=os.path.join(root, "f.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.dl)
        self.store = jobs.JobStore(self.policy.jobs_dir)
        self.ctx = runner.Context(self.policy, self.store, None, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_titles_steps_and_cap(self):
        r = self.store.create("research", {"topic": "llama.cpp releases"})
        o = self.store.create("organize", {"folder": self.dl, "goal": "tidy PDFs"})
        for i in range(7):
            self.store.append_step(r["id"], f"s{i}")
        out = {j["id"]: j for j in panel_api.jobs_payload(self.store)}
        self.assertEqual(out[r["id"]]["title"], "llama.cpp releases")
        self.assertEqual(out[o["id"]]["title"], f"tidy PDFs — {os.path.basename(self.dl)}")
        self.assertEqual(out[r["id"]]["step_count"], 7)
        self.assertEqual(out[r["id"]]["steps"], ["s2", "s3", "s4", "s5", "s6"])
        self.assertNotIn("plan", out[r["id"]])
        self.assertEqual(len(panel_api.jobs_payload(self.store, limit=1)), 1)

    def test_payload_includes_full_plan_only_when_awaiting(self):
        job = self.store.create("organize", {"folder": self.dl, "goal": "g"})
        ops = [{"op": "mkdir", "path": os.path.join(self.dl, f"d{i}")} for i in range(300)]
        self.store.write_json(job["id"], "plan.json", ops)
        self.store.write_text(job["id"], "review.md", "fine")
        self.store.update(job["id"], state="awaiting_approval")
        item = panel_api.jobs_payload(self.store)[0]
        self.assertEqual(len(item["plan"]), 300)  # never cut: the user approves exactly this list
        self.assertEqual(item["review"], "fine")
        self.store.update(job["id"], state="rejected")
        self.assertNotIn("plan", panel_api.jobs_payload(self.store)[0])

    def test_payload_flags_restore_and_queue(self):
        a = self.store.create("organize", {"folder": self.dl, "goal": "g"})
        self.store.write_json(a["id"], "applied.json", [])
        self.store.update(a["id"], state="applied")
        p = self.store.create("plan", {"goal": "g"})
        self.store.write_json(p["id"], "steps.json", [{"kind": "research", "topic": "t"}] * 2)
        self.store.update(p["id"], state="done")
        out = {j["id"]: j for j in panel_api.jobs_payload(self.store)}
        self.assertTrue(out[a["id"]]["can_restore"])
        self.assertEqual(out[p["id"]]["queue_count"], 2)
        self.store.update(p["id"], steps_queued=True)
        self.assertEqual({j["id"]: j for j in panel_api.jobs_payload(self.store)}[p["id"]]["queue_count"], 0)

    def test_payload_survives_a_corrupt_plan(self):
        job = self.store.create("organize", {"folder": self.dl, "goal": "g"})
        with open(self.store.path(job["id"], "plan.json"), "w") as f:
            f.write("{not json")
        self.store.update(job["id"], state="awaiting_approval")
        self.assertIsNone(panel_api.jobs_payload(self.store)[0]["plan"])

    def test_trash_refusal(self):
        self.assertIsNone(panel_api.trash_refusal([{"id": "x", "state": "done"}]))
        self.assertIn("x (applying)", panel_api.trash_refusal([{"id": "x", "state": "applying"}]))

    def test_empty_trash_refused_while_files_move(self):
        with open(os.path.join(self.policy.trash_dir, "keep.txt"), "w") as f:
            f.write("x")
        job = self.store.create("organize", {"folder": self.dl, "goal": "g"})
        self.store.update(job["id"], state="restoring")
        self.assertFalse(panel_api.empty_trash(self.ctx)["ok"])
        self.store.update(job["id"], state="applied")
        with runner.FILE_MOVES:  # a restore started from chat holds the lock while it moves files
            res = panel_api.empty_trash(self.ctx)
        self.assertFalse(res["ok"])
        self.assertTrue(os.path.exists(os.path.join(self.policy.trash_dir, "keep.txt")))
        res = panel_api.empty_trash(self.ctx)
        self.assertTrue(res["ok"], res)
        self.assertEqual(os.listdir(self.policy.trash_dir), [])

    def test_approve_and_undo_hold_the_file_lock(self):
        seen = []
        orig = plans.apply_plan

        def spy(*a, **k):
            seen.append(runner.FILE_MOVES.locked())
            return orig(*a, **k)

        a = os.path.join(self.dl, "a.pdf")
        open(a, "w").close()
        job = self.store.create("organize", {"folder": self.dl, "goal": "g"})
        self.store.write_json(job["id"], "plan.json", [{"op": "remove", "path": a}])
        self.store.update(job["id"], state="awaiting_approval")
        plans.apply_plan = spy
        try:
            runner.approve(job["id"], self.ctx)
        finally:
            plans.apply_plan = orig
        self.assertEqual(seen, [True])
        self.assertFalse(runner.FILE_MOVES.locked())


if __name__ == "__main__":
    unittest.main()
```

(If `runner.approve` calls `apply_plan` through a different name, for example `from .plans import apply_plan`, patch that name instead; read `runner.py` first.)

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest tests.test_agents_panel_api -v`
Expected: ERROR `cannot import name 'panel_api'`.

- [ ] **Step 3: Implement**

`agents/runner.py`: add `import threading` and, near the constants:

```python
# Held while plan files move (apply, restore) and while the trash is emptied, so Empty trash
# can never delete files an apply or restore is moving, wherever that was started.
FILE_MOVES = threading.Lock()
```

In `approve`, wrap the `plans.apply_plan(...)` call and its result handling in `with FILE_MOVES:`. In `undo`, wrap the `plans.restore(...)` call in `with FILE_MOVES:`. Keep the existing state transitions and messages unchanged.

`agents/panel_api.py`:

```python
"""Logic behind the jobs panel's routes on the agents server. Standard library only."""
import os

from . import plans, runner

MOVING = ("applying", "restoring")


def _title(job):
    args = job.get("args") or {}
    if job.get("kind") == "organize":
        folder = str(args.get("folder", "")).rstrip("\\/")
        return f"{args.get('goal', '')} — {os.path.basename(folder) or folder}"
    return str(args.get("topic") or args.get("goal") or job.get("kind", ""))


def jobs_payload(store, limit=50):
    out = []
    for job in store.list()[:limit]:
        jid, state = job["id"], job.get("state")
        count, steps = store.read_steps(jid, last=5)
        item = {k: job.get(k) for k in ("id", "kind", "state", "note", "created", "updated")}
        item.update(title=_title(job), step_count=count, steps=steps, queue_count=0,
                    can_restore=state in ("applied", "failed") and store.has(jid, "applied.json"))
        if job.get("kind") == "plan" and state == "done" and not job.get("steps_queued") and store.has(jid, "steps.json"):
            try:
                item["queue_count"] = len(store.read_json(jid, "steps.json"))
            except (OSError, ValueError):
                pass
        if state == "awaiting_approval":
            try:
                item["plan"] = store.read_json(jid, "plan.json")
            except (OSError, ValueError):
                item["plan"] = None
            try:
                item["review"] = store.read_text(jid, "review.md") if store.has(jid, "review.md") else ""
            except OSError:
                item["review"] = ""
        out.append(item)
    return out


def trash_refusal(jobs):
    moving = [f"{j['id']} ({j['state']})" for j in jobs if j.get("state") in MOVING]
    if moving:
        return "These jobs are still moving files: " + ", ".join(moving) + ". Wait for them to finish."
    return None


def empty_trash(ctx):
    if not runner.FILE_MOVES.acquire(blocking=False):
        return {"ok": False, "message": "Files are moving right now. Try again when that finishes."}
    try:
        refusal = trash_refusal(ctx.store.list())
        if refusal:
            return {"ok": False, "message": refusal}
        n = plans.empty_trash(ctx.policy)
        return {"ok": True, "message": f"Trash emptied ({n} items)."}
    except OSError as e:
        return {"ok": False, "message": f"Couldn't empty the trash: {e}"}
    finally:
        runner.FILE_MOVES.release()
```

`agents/server.py`: add `panel_api` to the `from agents import ...` line, then after the existing `job_action` route:

```python
@server.custom_route("/jobs", methods=["GET"])
async def jobs_list(request):
    if not llm.check_request(request.headers, PORT, _key()):
        return JSONResponse({"ok": False, "message": "Forbidden."}, status_code=403)
    data = await anyio.to_thread.run_sync(panel_api.jobs_payload, store)
    return JSONResponse({"ok": True, "jobs": data})


@server.custom_route("/trash/empty", methods=["POST"])
async def trash_empty(request):
    if not llm.check_request(request.headers, PORT, _key()):
        return JSONResponse({"ok": False, "message": "Forbidden."}, status_code=403)
    return JSONResponse(await anyio.to_thread.run_sync(panel_api.empty_trash, ctx))
```

Also update the module docstring and the `server` instructions text: replace "the launcher's Jobs pane" with "the jobs panel in the Web UI".

- [ ] **Step 4: Run tests, then smoke-test the routes on a spare port**

Run: `python -m unittest tests.test_agents_panel_api -v` and the full suite.

Smoke test, entirely in temp locations. Point the server at a temp sandbox and a temp key the same way the earlier final-fix smoke test did. If there's no clean hook, run it with `USERPROFILE` set to a temp folder so `default_sandbox()` lands there, and with a temp `api_key.txt` reachable as `llm.API_KEY_FILE`. Read `agents/llm.py` and `agents/paths.py` first. Start `uv run --script agents/server.py --port 18002`, then:
- `GET http://127.0.0.1:18002/jobs` with the right Host and key → 200 `{"ok": true, "jobs": [...]}`;
- the same with no key → 403;
- `POST /trash/empty` with the key → `{"ok": true, ...}`.

Stop the server (`taskkill /T /F /PID <uv pid>`) and confirm port 18002 is free.

- [ ] **Step 5: Commit**

```bash
git add agents/panel_api.py agents/runner.py agents/server.py tests/test_agents_panel_api.py
git commit -m "agents: job list and empty-trash routes for the jobs panel; one lock for all file moves"
```

---

### Task 3: Proxy: insert the script, serve the panel, pass API calls through

**Files:**
- Modify: `proxy.py`
- Test: `tests/test_proxy.py` (new)

**Interfaces:**
- Consumes: agents server routes from Task 2 (`GET /jobs`, `POST /jobs/<id>/<action>`, `POST /trash/empty`) on `127.0.0.1:<agents port>`.
- Produces: `proxy.inject_script(html: bytes) -> bytes`, `proxy.api_target(method, path) -> str | None`, `proxy.PANEL_FILES` (dict of URL path → (file name, content type)), module globals `_UP` (llama port) and `_AGENTS` (agents port, default 8002, set from `argv[3]` if given). The panel files themselves come in Task 4; this task serves whatever is in `panel/`.

- [ ] **Step 1: Write the failing tests**

`tests/test_proxy.py`:

```python
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
        if self.path.startswith("/broken"):
            body, status = b"Error: gzip is not supported by this browser", 415
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
        r, body = self.get("GET", "/broken")
        self.assertEqual(r.status, 415)
        self.assertNotIn(TAG, body)

    def test_panel_files_served_with_coep(self):
        r, body = self.get("GET", "/agent-jobs")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader("Cross-Origin-Embedder-Policy"), "require-corp")
        self.assertEqual(r.getheader("Cross-Origin-Resource-Policy"), "same-origin")
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
```

`test_panel_files_served_with_coep` needs `panel/index.html` to exist. Create a one-line placeholder `panel/index.html` (`<!doctype html><title>Agent jobs</title>`) in this task; Task 4 replaces it.

- [ ] **Step 2: Run and confirm failure**

Run: `python -m unittest tests.test_proxy -v`
Expected: failures and errors (`inject_script`/`api_target` missing; `/` comes back gzipped without the script).

- [ ] **Step 3: Implement**

At the top of `proxy.py`, add `import gzip, re` and `from urllib.parse import urlsplit`, plus these module-level definitions:

```python
_AGENTS = 8002  # agents server; argv[3] overrides

PANEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel")
PANEL_FILES = {
    "/agent-jobs": ("index.html", "text/html; charset=utf-8"),
    "/agent-jobs/": ("index.html", "text/html; charset=utf-8"),
    "/agent-jobs/inject.js": ("inject.js", "text/javascript; charset=utf-8"),
    "/agent-jobs/panel.js": ("panel.js", "text/javascript; charset=utf-8"),
    "/agent-jobs/panel.css": ("panel.css", "text/css; charset=utf-8"),
}
INJECT_TAG = b'<script src="/agent-jobs/inject.js"></script>'
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
```

Inside `Handler`, add three methods:

```python
    def _send(self, status, body, headers):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _webui_page(self):
        # llama-server only serves this page gzipped (no gzip -> 415), so ask for gzip, unpack, add our tag.
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding")}
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
```

At the very start of `_relay`, before the request body is read:

```python
        path = urlsplit(self.path).path
        if path.startswith("/agent-jobs"):
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
            except (OSError, ValueError, EOFError) as e:  # EOFError/ValueError: a bad gzip body
                return self._send(502, str(e).encode(), [("Content-Type", "text/plain")])
```

(A POST to the API has a `Content-Length: 0` body; don't read it.) In `__main__`, after `_UP = ...`, add `_AGENTS = int(sys.argv[3]) if len(sys.argv) > 3 else 8002` and declare `global`-style assignment the same way `_UP` is set (module-level code, so a plain assignment works). Update the `print` to mention the agents port.

- [ ] **Step 4: Run tests**

Run: `python -m unittest tests.test_proxy -v` and the full suite.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add proxy.py tests/test_proxy.py panel/index.html
git commit -m "proxy: serve the jobs panel, pass its api calls through, add the script to the web ui page"
```

---

### Task 4: The panel UI

**Files:**
- Create: `panel/inject.js`, `panel/panel.css`, `panel/panel.js`
- Replace: `panel/index.html`

**Interfaces:**
- Consumes: `GET /agent-jobs/api/jobs` → `{"ok": true, "jobs": [job...]}` with the job fields from Task 2; `POST /agent-jobs/api/jobs/<id>/<apply|reject|restore|queue>` and `POST /agent-jobs/api/trash/empty` → `{"ok": bool, "message": str}`; statuses 403 (bad or missing key) and 502 (agents server down).
- Produces: `postMessage` between `inject.js` (parent) and `panel.js` (frame), same origin only: the parent sends `{type: "agent-jobs-visible", visible: bool}`, and the frame sends `{type: "agent-jobs-counts", running: n, review: n}`.

- [ ] **Step 1: `panel/inject.js`**

```js
(() => {
  if (window.top !== window || document.getElementById("ajp-root")) return;
  const OPEN_KEY = "agentJobsPanelOpen";
  const css = `
#ajp-root{all:initial}
#ajp-root .ajp-btn{position:fixed;top:10px;right:12px;z-index:2147483000;display:flex;align-items:center;gap:6px;
  font:500 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;padding:7px 11px;border-radius:8px;cursor:pointer;
  background:var(--ajp-surface);color:var(--ajp-text);border:0.5px solid var(--ajp-border)}
#ajp-root .ajp-badge{min-width:16px;padding:1px 5px;border-radius:8px;font-size:11px;text-align:center;
  background:var(--ajp-warn-bg);color:var(--ajp-warn-text)}
#ajp-root .ajp-badge[hidden]{display:none}
#ajp-root .ajp-frame{position:fixed;top:0;right:0;width:360px;max-width:100vw;height:100vh;z-index:2147482999;
  border:0;border-left:0.5px solid var(--ajp-border);background:var(--ajp-bg);transform:translateX(100%);
  transition:transform .18s ease-out}
#ajp-root.ajp-open .ajp-frame{transform:none}
#ajp-root.ajp-open .ajp-btn{right:372px}
#ajp-root{--ajp-bg:#faf9f5;--ajp-surface:#ffffff;--ajp-text:#1f1e1d;--ajp-border:rgba(31,30,29,.18);
  --ajp-warn-bg:#faeeda;--ajp-warn-text:#633806}
@media (prefers-color-scheme:dark){#ajp-root{--ajp-bg:#262624;--ajp-surface:#30302e;--ajp-text:#faf9f5;
  --ajp-border:rgba(250,249,245,.18);--ajp-warn-bg:#633806;--ajp-warn-text:#faeeda}}`;
  const style = document.createElement("style");
  style.textContent = css;
  const root = document.createElement("div");
  root.id = "ajp-root";
  const btn = document.createElement("button");
  btn.className = "ajp-btn";
  btn.type = "button";
  btn.setAttribute("aria-label", "Agent jobs");
  btn.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.textContent = "Jobs";
  const badge = document.createElement("span");
  badge.className = "ajp-badge";
  badge.hidden = true;
  btn.append(label, badge);
  const frame = document.createElement("iframe");
  frame.className = "ajp-frame";
  frame.title = "Agent jobs";
  frame.src = "/agent-jobs";
  root.append(btn, frame);
  document.head.append(style);
  document.body.append(root);

  let open = false;
  try { open = localStorage.getItem(OPEN_KEY) === "1"; } catch (e) {}
  const tell = () => {
    try { frame.contentWindow.postMessage({type: "agent-jobs-visible", visible: open}, location.origin); } catch (e) {}
  };
  const apply = () => {
    root.classList.toggle("ajp-open", open);
    btn.setAttribute("aria-expanded", String(open));
    tell();
  };
  btn.addEventListener("click", () => {
    open = !open;
    try { localStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (e) {}
    apply();
  });
  frame.addEventListener("load", tell);
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== frame.contentWindow) return;
    const d = e.data || {};
    if (d.type !== "agent-jobs-counts") return;
    const n = (d.running | 0) + (d.review | 0);
    badge.textContent = String(n);
    badge.hidden = n === 0;
  });
  apply();
})();
```

- [ ] **Step 2: `panel/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent jobs</title>
<link rel="stylesheet" href="/agent-jobs/panel.css">
</head>
<body>
<header class="top">
  <h1>Agent jobs</h1>
  <span id="summary" class="muted"></span>
</header>
<div id="status" class="status" hidden></div>
<div id="flash" class="flash" hidden></div>
<main id="jobs" class="jobs" aria-live="polite"></main>
<footer class="bottom">
  <button id="empty-trash" type="button">Empty trash</button>
</footer>
<script src="/agent-jobs/panel.js"></script>
</body>
</html>
```

- [ ] **Step 3: `panel/panel.css`**

```css
:root{--bg:#faf9f5;--surface:#ffffff;--surface-2:#f0eee6;--text:#1f1e1d;--text-2:#5e5d59;--text-3:#87867f;
  --border:rgba(31,30,29,.15);--border-strong:rgba(31,30,29,.3);
  --accent-bg:#e6f1fb;--accent-text:#0c447c;--warn-bg:#faeeda;--warn-text:#633806;--warn-border:#ef9f27;
  --ok-bg:#eaf3de;--ok-text:#27500a;--bad-bg:#fcebeb;--bad-text:#791f1f;--muted-bg:#f1efe8;--muted-text:#444441;
  --mono:ui-monospace,"Cascadia Mono",Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#262624;--surface:#30302e;--surface-2:#1f1e1d;--text:#faf9f5;--text-2:#c2c0b6;
  --text-3:#9c9a92;--border:rgba(250,249,245,.15);--border-strong:rgba(250,249,245,.3);
  --accent-bg:#0c447c;--accent-text:#e6f1fb;--warn-bg:#633806;--warn-text:#faeeda;--warn-border:#ba7517;
  --ok-bg:#27500a;--ok-text:#eaf3de;--bad-bg:#791f1f;--bad-text:#fcebeb;--muted-bg:#444441;--muted-text:#f1efe8}}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font:400 13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  display:flex;flex-direction:column}
.top{display:flex;align-items:baseline;justify-content:space-between;padding:14px 14px 8px}
h1{font-size:15px;font-weight:500;margin:0}
.muted{color:var(--text-2);font-size:12px}
.jobs{flex:1;overflow-y:auto;padding:0 14px 14px;display:flex;flex-direction:column;gap:10px}
.status,.flash{margin:0 14px 10px;padding:8px 10px;border-radius:8px;font-size:12px}
.status{background:var(--muted-bg);color:var(--muted-text)}
.flash{background:var(--surface-2);color:var(--text)}
.card{background:var(--surface);border:0.5px solid var(--border);border-radius:12px;padding:10px 12px}
.card.review{border-color:var(--warn-border)}
.card.compact{display:flex;align-items:center;gap:8px;cursor:pointer}
.head{display:flex;align-items:center;gap:8px}
.kind{width:8px;height:8px;border-radius:50%;background:var(--text-3);flex:none}
.title{font-weight:500;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{font-size:11px;padding:2px 8px;border-radius:8px;flex:none}
.chip.accent{background:var(--accent-bg);color:var(--accent-text)}
.chip.warn{background:var(--warn-bg);color:var(--warn-text)}
.chip.ok{background:var(--ok-bg);color:var(--ok-text)}
.chip.bad{background:var(--bad-bg);color:var(--bad-text)}
.chip.neutral{background:var(--muted-bg);color:var(--muted-text)}
.steps{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text-2)}
.steps li{display:flex;gap:6px;min-width:0}
.steps li span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.steps li.current{color:var(--text)}
.mark{flex:none;width:12px;text-align:center}
.meta{margin-top:6px;font-size:11px;color:var(--text-3)}
.note{margin-top:6px;font-size:12px;color:var(--text-2)}
.plan{margin-top:8px;font:11px/1.6 var(--mono);color:var(--text-2);background:var(--surface-2);border-radius:8px;
  padding:6px 8px;max-height:260px;overflow:auto;white-space:pre}
.review-text{margin-top:6px;font-size:12px;color:var(--text-2);white-space:pre-wrap}
.actions{margin-top:8px;display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-size:12px;padding:6px 10px;border-radius:8px;cursor:pointer;background:transparent;
  color:var(--text);border:0.5px solid var(--border-strong)}
button:hover{background:var(--surface-2)}
button:disabled{opacity:.5;cursor:default}
.bottom{padding:10px 14px;border-top:0.5px solid var(--border);display:flex;justify-content:flex-end}
.empty{color:var(--text-2);font-size:12px;padding:20px 4px;text-align:center}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;animation:spin 1s linear infinite}
```

- [ ] **Step 4: `panel/panel.js`**

```js
(() => {
  "use strict";
  const MAX_CALLS = 40;
  const VISIBLE_MS = 2000, HIDDEN_MS = 15000;
  const STATES = {
    queued: ["Queued", "accent"], running: ["Running", "accent"], awaiting_approval: ["Needs review", "warn"],
    applying: ["Applying", "accent"], restoring: ["Restoring", "accent"], applied: ["Applied", "ok"],
    restored: ["Restored", "ok"], done: ["Done", "ok"], failed: ["Failed", "bad"],
    cancelled: ["Cancelled", "neutral"], rejected: ["Rejected", "neutral"],
  };
  const ACTIVE = new Set(["queued", "running", "applying", "restoring"]);
  const $ = (id) => document.getElementById(id);
  const list = $("jobs"), statusEl = $("status"), flashEl = $("flash"), summary = $("summary");
  const embedded = window.parent !== window;
  let visible = !embedded, busy = false, timer = null, expanded = new Set(), lastJobs = [];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  function apiKey() {
    try { return String(JSON.parse(localStorage.getItem("LlamaUi.config") || "{}").apiKey || "").trim(); }
    catch (e) { return ""; }
  }

  async function api(method, path) {
    const key = apiKey();
    if (!key) { const e = new Error("no key"); e.kind = "no-key"; throw e; }
    let r;
    try { r = await fetch("/agent-jobs/api/" + path, {method, headers: {Authorization: "Bearer " + key}}); }
    catch (err) { const e = new Error("down"); e.kind = "down"; throw e; }
    if (r.status === 403) { const e = new Error("forbidden"); e.kind = "forbidden"; throw e; }
    if (r.status === 502) { const e = new Error("down"); e.kind = "down"; throw e; }
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) { const e = new Error(data.message || ("HTTP " + r.status)); e.kind = "error"; throw e; }
    return data;
  }

  function showStatus(text) {
    statusEl.textContent = text || "";
    statusEl.hidden = !text;
  }

  function flash(text) {
    flashEl.textContent = text;
    flashEl.hidden = !text;
    if (text) setTimeout(() => { if (flashEl.textContent === text) flashEl.hidden = true; }, 8000);
  }

  function minutes(job) {
    const end = ACTIVE.has(job.state) ? Date.now() / 1000 : job.updated;
    const m = Math.max(0, Math.round((end - job.created) / 60));
    return m < 1 ? "under 1 min" : m + " min";
  }

  function describeOp(op, i) {
    const n = String(i + 1).padStart(3);
    if (op && op.op === "move") return `${n} move    ${op.src}\n          → ${op.dst}`;
    if (op && op.op === "remove") return `${n} remove  ${op.path}   (to trash, restorable)`;
    if (op && op.op === "mkdir") return `${n} mkdir   ${op.path}`;
    return `${n} ?       ${JSON.stringify(op)}`;
  }

  function button(text, onClick) {
    const b = el("button", "", text);
    b.type = "button";
    b.disabled = busy;
    b.addEventListener("click", onClick);
    return b;
  }

  async function act(path, confirmText) {
    if (busy) return;
    if (confirmText && !window.confirm(confirmText)) return;
    busy = true;
    render(lastJobs);
    try {
      const res = await api("POST", path);
      flash(res.message || (res.ok ? "Done." : "That didn't work."));
    } catch (e) {
      flash(e.kind === "down" ? "Agents server isn't running." : e.kind === "forbidden" ? "The API key was refused." : e.message);
    } finally {
      busy = false;
      refresh();
    }
  }

  function stepsList(job) {
    const ul = el("ul", "steps");
    const active = ACTIVE.has(job.state);
    job.steps.forEach((text, i) => {
      const last = i === job.steps.length - 1;
      const li = el("li", active && last ? "current" : "");
      const mark = el("span", "mark" + (active && last ? " spin" : ""), active && last ? "◌" : "✓");
      li.append(mark, el("span", "", text));
      ul.append(li);
    });
    return ul;
  }

  function card(job) {
    const [label, tone] = STATES[job.state] || [job.state, "neutral"];
    const finished = !ACTIVE.has(job.state) && job.state !== "awaiting_approval" && !job.can_restore && !job.queue_count;
    const c = el("article", "card" + (job.state === "awaiting_approval" ? " review" : "") + (finished && !expanded.has(job.id) ? " compact" : ""));
    const head = el("div", "head");
    head.append(el("span", "kind"), el("span", "title", job.title || job.kind), el("span", "chip " + tone, label));
    c.append(head);
    if (finished && !expanded.has(job.id)) {
      c.title = "Show details";
      c.addEventListener("click", () => { expanded.add(job.id); render(lastJobs); });
      return c;
    }
    if (job.steps && job.steps.length) c.append(stepsList(job));
    if (ACTIVE.has(job.state) || job.step_count) {
      c.append(el("div", "meta", `${job.step_count} of ${MAX_CALLS} steps · ${minutes(job)}`));
    }
    if (job.note) c.append(el("div", "note", job.note));
    if (job.state === "awaiting_approval") {
      if (Array.isArray(job.plan)) {
        c.append(el("div", "plan", job.plan.map(describeOp).join("\n")));
        if (job.review) c.append(el("div", "review-text", "Review: " + job.review.trim()));
        const a = el("div", "actions");
        a.append(button(`Approve ${job.plan.length} changes`, () => act(`jobs/${job.id}/apply`)),
                 button("Reject", () => act(`jobs/${job.id}/reject`)));
        c.append(a);
      } else {
        c.append(el("div", "note", "The plan file couldn't be read, so it can't be approved here."));
      }
    }
    if (job.can_restore) {
      const a = el("div", "actions");
      a.append(button("Restore", () => act(`jobs/${job.id}/restore`,
        "Put every file this job moved or removed back where it was?")));
      c.append(a);
    }
    if (job.queue_count) {
      const a = el("div", "actions");
      a.append(button(`Queue ${job.queue_count} follow-up jobs`, () => act(`jobs/${job.id}/queue`)));
      c.append(a);
    }
    return c;
  }

  function render(jobs) {
    lastJobs = jobs;
    list.replaceChildren();
    if (!jobs.length) {
      list.append(el("div", "empty", "No agent jobs yet. Ask the chat to research, plan or organise something."));
    } else {
      jobs.forEach((j) => list.append(card(j)));
    }
    const running = jobs.filter((j) => ACTIVE.has(j.state)).length;
    const review = jobs.filter((j) => j.state === "awaiting_approval").length;
    summary.textContent = [running && `${running} running`, review && `${review} to review`].filter(Boolean).join(" · ");
    $("empty-trash").disabled = busy;
    if (embedded) window.parent.postMessage({type: "agent-jobs-counts", running, review}, location.origin);
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const data = await api("GET", "jobs");
      showStatus("");
      if (!busy) render(Array.isArray(data.jobs) ? data.jobs : []);
    } catch (e) {
      showStatus(e.kind === "no-key" ? "Paste your API key in the Web UI under Settings → General, then reopen this panel."
        : e.kind === "down" ? "Agents server isn't running. Start the servers from the launcher."
        : e.kind === "forbidden" ? "The API key was refused. Copy it again from the launcher and paste it in Settings → General."
        : "Couldn't load jobs: " + e.message);
    }
    timer = setTimeout(refresh, visible ? VISIBLE_MS : HIDDEN_MS);
  }

  $("empty-trash").addEventListener("click", () => act("trash/empty",
    "Permanently delete everything in the agent trash? Jobs whose files are in there can no longer be restored."));
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== window.parent) return;
    const d = e.data || {};
    if (d.type !== "agent-jobs-visible") return;
    const was = visible;
    visible = !!d.visible;
    if (visible && !was) refresh();
  });
  refresh();
})();
```

- [ ] **Step 5: Check the files load through the proxy**

Run the proxy test suite again (`python -m unittest tests.test_proxy -v`). Then, with a scratch script, serve `proxy.Handler` on port 18080, using a fake llama that returns a small gzipped HTML page (reuse `tests/test_proxy.py`'s `FakeLlama`). Fetch `/`, `/agent-jobs`, `/agent-jobs/panel.js`, `/agent-jobs/panel.css` and `/agent-jobs/inject.js`, and check each returns 200 with the right content type. Also run `node --check panel/panel.js` and `node --check panel/inject.js` (Node is installed) to catch syntax errors. Full UI behaviour is checked live in Task 6.

- [ ] **Step 6: Commit**

```bash
git add panel/
git commit -m "panel: agent jobs side panel ui and the web ui button"
```

---

### Task 5: Remove the launcher's Jobs pane; docs

**Files:**
- Modify: `launcher.pyw`, `agents/llm.py`, `tests/test_launcher.py`, `tests/test_agents_llm.py`
- Modify: `README.md`, `CLAUDE.md`, `.claude/rules/launcher.md`, `.claude/rules/agents.md`, `.claude/rules/proxy.md`
- Create: `docs/adr/0008-jobs-panel-in-web-ui.md`

**Interfaces:**
- Consumes: everything above; the launcher no longer calls the agents server's routes.

- [ ] **Step 1: Remove the pane**

In `launcher.pyw`:
- Delete the Jobs pane build block. It starts at the comment `# Jobs pane: agent jobs read straight from their job folders` (the `jobs_frame` … `self.log_pane.add(jobs_frame, …)` lines).
- Delete these methods:
  - `_jobs_store`, `_selected_job`, `_refresh_jobs`, `_fill_jobs`, `_update_job_buttons`
  - `_call_agents`, `_job_action`, `_review_plan`
  - `_empty_trash_refusal`, `_empty_trash`
- Delete the call `self._refresh_jobs()` in `run_commands`.
- Delete the `_jobs_after_id` cancel in `_exit_running_state`.
- Delete the `_jobs_busy` initialisation.
- Delete the module helpers `job_actions` and `empty_trash_refusal`; the refusal logic now lives in `agents/panel_api.py`.
- Set `H_RUNNING   = 750`.
- Remove imports this leaves unused. Check `agent_plans` and `agent_llm` with grep; keep `agent_jobs` and `agent_paths`, which the Agent folders dialog and `_kill_all` use.

In `agents/llm.py`, delete `agents_action` and any constant only it used (check with grep, e.g. `AGENTS_BASE`). In the tests:
- delete `JobActionsTests` and `EmptyTrashRefusalTests` from `tests/test_launcher.py`;
- delete the two `agents_action` tests from `tests/test_agents_llm.py`.

Run: `grep -n "_jobs_tree\|job_actions\|empty_trash_refusal\|agents_action\|_call_agents" launcher.pyw agents/*.py tests/*.py`
Expected: no output.

- [ ] **Step 2: Run the suite and a headless launcher check**

Run: `python -m unittest discover -s tests` (all pass), then:
`python -c "import importlib.machinery as m, importlib.util as u, sys, tkinter as tk; sys.path.insert(0,'.'); l=m.SourceFileLoader('launcher','launcher.pyw'); s=u.spec_from_loader('launcher',l); L=u.module_from_spec(s); l.exec_module(L); r=tk.Tk(); r.withdraw(); a=L.CommandLauncherGUI(r); print('ok', L.H_RUNNING); r.destroy()"`
Expected: `ok 750`, no traceback.

- [ ] **Step 3: Docs**

- `docs/adr/0008-jobs-panel-in-web-ui.md`: `# 0008. The proxy injects a jobs panel into the Web UI`, `Status: Accepted`.
  - **Context:** jobs were only visible in the launcher window, away from the chat.
  - **Decision:**
    - `proxy.py` adds one script tag to the Web UI page. It draws a Jobs button and a 360 px iframe showing `/agent-jobs`.
    - The panel's calls go through an allow-list on the proxy to key-checked agents-server routes. The proxy never adds a key, and the panel reads the key the Web UI stores.
    - One lock covers apply, restore and Empty trash.
  - **Rejected:**
    - MCP UI resources: support in this Web UI is unverified.
    - A separate page only: not beside the chat.
    - Keeping the launcher pane: two places to act, away from the chat.
  - **Consequences:**
    - The page is sent `Cache-Control: no-store`, but a browser that cached the old page (marked immutable for a year) needs one hard refresh.
    - Panel files must send COEP `require-corp`.
    - A Web UI redesign can at worst misplace the button.
- `CLAUDE.md`: in the ADR index, add `- [0008](docs/adr/0008-jobs-panel-in-web-ui.md) The proxy injects a jobs panel into the Web UI; the launcher's Jobs pane is gone`. Update any lines that mention the Jobs pane.
- `README.md`:
  - Replace the Jobs pane section with a "Jobs panel" section:
    - click **Jobs** at the top right of the Web UI;
    - what each card shows;
    - Approve / Reject show the full plan;
    - Restore and Queue follow-up jobs;
    - Empty trash is permanent and waits for moves.
  - Add: "After updating the launcher, press Ctrl+F5 once in the Web UI so the browser drops its cached copy of the page."
  - Update the setup and approval wording that mentions "the launcher's Jobs pane".
  - Update the folder tree (add `panel/`).
- `.claude/rules/proxy.md`: add bullets for:
  - llama-server's gzip-only page and why the proxy asks for gzip and unpacks it;
  - `no-store` and dropping the `ETag`;
  - COEP on panel files;
  - the API allow-list (`api_target`), and that the proxy never adds a key.
- `.claude/rules/agents.md`: add bullets for:
  - `steps.jsonl` (best-effort, sanitised, shown as text);
  - `FILE_MOVES`, the one lock for apply, restore and Empty trash;
  - the `GET /jobs` and `POST /trash/empty` routes and their guard.

  Remove or adjust bullets that mention the Jobs pane.
- `.claude/rules/launcher.md`: remove the bullets that describe the Jobs pane.

- [ ] **Step 4: Commit**

```bash
git add launcher.pyw agents/llm.py tests/test_launcher.py tests/test_agents_llm.py README.md CLAUDE.md .claude/rules docs/adr/0008-jobs-panel-in-web-ui.md
git commit -m "launcher: drop the jobs pane in favour of the web ui panel; docs and adr 0008"
```

---

### Task 6: Live end-to-end check, merge and push

Needs the real ports and the browser pane, so the controller runs it, not a subagent. Before starting, confirm the user's launcher isn't running: `Get-NetTCPConnection -LocalPort 8080,8081,8001,8002 -State Listen` returns nothing.

- [ ] **Step 1: Start the real stack headlessly.** Use a scratch script that loads `launcher.pyw`, selects `Qwen_Qwen3-14B-Q5_K_M`, calls `run_commands()`, and keeps running until a stop file appears.
- [ ] **Step 2: In the browser pane**, open `http://127.0.0.1:8080/` and hard-reload. Check:
  - the **Jobs** button shows and the panel opens and closes;
  - its open state survives a reload;
  - the "no jobs" state shows with the API key set, and the "paste your API key" state shows with the key temporarily cleared in `localStorage` (restore it afterwards).
- [ ] **Step 3: Start jobs through the agents server.** Make an `agent-e2e` temp folder (`a.pdf`, `b.pdf`, `junk.tmp`) and call MCP `tools/call` on `http://127.0.0.1:8002/mcp` with Host `127.0.0.1:8002`: `request_folder_access`, then `organize` (goal: put the PDFs in a PDFs folder and remove .tmp files), then `research` (a short topic). Check in the panel that:
  - the research card shows steps arriving live;
  - the organise card reaches "Needs review" with the full plan and review;
  - the button's badge counts them.
- [ ] **Step 4: Act from the panel.** Click Approve, and check the files moved. Click Empty trash while a Restore is running, and check it's refused (start the restore from the panel and click Empty trash straight away; if the restore finishes too fast to catch, record that and rely on the unit test). Click Restore, and check the files are back. Click Empty trash, and check the trash is empty.
- [ ] **Step 5: Stop the agents server only** (`taskkill` its PID). Check the panel shows "Agents server isn't running." Then stop everything with the stop file, and check all four ports are free and no jobs are left `queued` or `running`. Remove the temp folder from `agent_folders.json` and delete it.
- [ ] **Step 6: Final review and finish.** Run the whole-branch review (most capable model) over `main..jobs-side-panel`, handle findings, then:

```bash
python -m unittest discover -s tests
git checkout main
git merge --ff-only jobs-side-panel
git push origin main
git branch -d jobs-side-panel
```
