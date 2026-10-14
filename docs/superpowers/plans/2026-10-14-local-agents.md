# Local Agents (Pass 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat model in the llama Web UI start background research, planning and file-organising jobs that run on the local model, with every file change outside a sandbox reviewed, approved and undoable.

**Architecture:** A new `agents/` package. Five standard-library modules (`paths`, `jobs`, `plans`, `llm`, `runner`) hold all logic and are unit-tested. A thin `agents/server.py` exposes them as MCP tools using the official `mcp` 2.x package, started by the existing UVX MCP proxy with `uv run --script`. The launcher registers that server in `config.json`, manages readable folders, cancels jobs on exit, and shows a Jobs pane that reads job files directly and calls the server's tools over HTTP for actions.

**Tech Stack:** Python 3 standard library (tkinter, urllib, json, threading, shutil, ctypes), `mcp>=2.3,<3` (server only, via `uv`), llama-server OpenAI-compatible tool calling, mcp-proxy streamable HTTP (stateless JSON-RPC).

**Spec:** `docs/superpowers/specs/2026-10-14-local-agents.md` (decision record: `docs/adr/0006-local-agents.md`)

## Global Constraints

- Everything in `agents/` except `agents/server.py` uses the standard library only (ADR 0006).
- `mcp` is pinned as `mcp>=2.3,<3`. In 2.x the server class is `from mcp.server.mcpserver import MCPServer` (`FastMCP` no longer exists).
- Agents write only inside `Documents\agent-sandbox`, never inside its `jobs\` or `trash\`.
- Readable folders live in `agent_folders.json` next to `launcher.pyw` (gitignored), never inside the sandbox.
- Local drives only: UNC paths and mapped network drives are refused for reading, plans and the folder list.
- One job runs at a time. Caps per job: 40 tool calls or 20 minutes.
- Plan operations are exactly `mkdir {path}`, `move {src, dst}` (also renames) and `remove {path}` (moves to `agent-sandbox\trash\<job>\`).
- Job states: `queued`, `running`, `done`, `failed`, `cancelled`, `awaiting_approval`, `applying`, `applied`, `rejected`, `restoring`, `restored`.
- Ports: llama-server `8081` (needs `Authorization: Bearer <api_key.txt>`), UVX MCP proxy `8001` (`/servers/<name>/mcp`).
- Never test on ports 8080/8081/8001 while the user's own launcher session is running; use spare ports (for example 18001) or ask the user to click Exit Processes first.
- Commit straight to `main` and push; no branches or PRs. No Claude attribution lines in commits.
- Tests: `python -m unittest discover -s tests`. Each test file puts the repo root on `sys.path` itself.

## Review Focus

1. A junction or symlink inside a readable folder that points outside it: reads and plan operations through it are refused (Task 1, `test_junction_escape_is_refused`).
2. A `job_id` from the chat model containing `..\` or other path text: treated as unknown, nothing outside `jobs\` is read (Task 2, `test_bad_job_id_is_unknown`).
3. The same plan approved twice (Jobs pane and chat at once): the second approval is refused and files move once (Task 5, `test_second_approval_is_refused`).
4. Restore when something new now sits at an original path: that item is skipped and reported, everything else comes back (Task 3, `test_restore_skips_occupied_paths`).
5. The model sends malformed tool arguments (not JSON, or a list): the tool returns an error string to the model and the job carries on (Task 5, `test_malformed_tool_arguments_return_error`).

---

## File Structure

| File | Responsibility |
|---|---|
| `agents/__init__.py` | Marks the package (empty). |
| `agents/paths.py` | Sandbox location, readable-folder list, `is_local`, `Policy.can_read/can_write/can_change`. |
| `agents/jobs.py` | `JobStore` (job folders, atomic `job.json`, state transitions) and `Worker` (one-at-a-time queue). |
| `agents/plans.py` | `validate_plan`, `apply_plan`, `restore`, `empty_trash`. |
| `agents/llm.py` | `chat()` to llama-server, `mcp_call()` to the UVX MCP proxy. |
| `agents/runner.py` | `run_agent` loop with caps, tool builders, the three job kinds, `approve/reject/undo/queue_steps`. |
| `agents/server.py` | MCP tool definitions (`uv run --script`, `mcp` 2.x). |
| `launcher.pyw` | Registers the agents server, Agent folders dialog, cancel-on-exit, Jobs pane. |
| `tests/test_agents_*.py` | Unit tests per module. |
| `README.md`, `.claude/rules/agents.md`, `CLAUDE.md`, `.gitignore` | Docs and ignore rules. |

---

### Task 1: Sandbox and readable-folder policy (`agents/paths.py`)

**Files:**
- Create: `agents/__init__.py`, `agents/paths.py`
- Test: `tests/test_agents_paths.py`

**Interfaces:**
- Produces: `APP_DIR: str`, `FOLDERS_FILE: str`, `default_sandbox() -> str`, `is_inside(path, root) -> bool`, `is_local(path) -> bool`, class `Policy(sandbox, folders_file=FOLDERS_FILE)` with attributes `sandbox`, `jobs_dir`, `trash_dir`, `folders_file` and methods `ensure_dirs()`, `read_folders() -> list[str]`, `add_folder(path) -> str` (raises `ValueError`), `remove_folder(path)`, `can_read(path) -> bool`, `can_write(path) -> bool`, `can_change(path) -> bool`.

- [ ] **Step 1: Write the failing tests**

`agents/__init__.py` is an empty file. `tests/test_agents_paths.py`:

```python
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import paths


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.sandbox = os.path.join(root, "sandbox")
        self.allowed = os.path.join(root, "allowed")
        self.outside = os.path.join(root, "outside")
        for d in (self.allowed, self.outside):
            os.makedirs(d)
        self.policy = paths.Policy(self.sandbox, folders_file=os.path.join(root, "folders.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.allowed)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_sandbox_and_listed_folders(self):
        self.assertTrue(self.policy.can_read(os.path.join(self.allowed, "a.txt")))
        self.assertTrue(self.policy.can_read(os.path.join(self.sandbox, "notes.md")))
        self.assertFalse(self.policy.can_read(os.path.join(self.outside, "a.txt")))

    def test_sibling_with_same_prefix_is_not_inside(self):
        self.assertFalse(self.policy.can_read(self.allowed + "2"))

    def test_writes_only_in_sandbox_outside_system_folders(self):
        self.assertTrue(self.policy.can_write(os.path.join(self.sandbox, "report.md")))
        self.assertFalse(self.policy.can_write(os.path.join(self.policy.jobs_dir, "x", "job.json")))
        self.assertFalse(self.policy.can_write(os.path.join(self.policy.trash_dir, "x")))
        self.assertFalse(self.policy.can_write(os.path.join(self.allowed, "a.txt")))

    def test_plans_may_change_listed_folders_only(self):
        self.assertTrue(self.policy.can_change(os.path.join(self.allowed, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(self.sandbox, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(paths.APP_DIR, "config.json")))

    def test_junction_escape_is_refused(self):
        link = os.path.join(self.allowed, "escape")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, self.outside], check=True, capture_output=True)
        self.assertFalse(self.policy.can_read(os.path.join(link, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(link, "a.txt")))

    def test_network_paths_are_refused(self):
        self.assertFalse(paths.is_local(r"\\server\share\file.txt"))
        with self.assertRaises(ValueError):
            self.policy.add_folder(r"\\server\share")

    def test_folder_list_round_trip(self):
        self.policy.add_folder(self.allowed)  # duplicate is ignored
        self.assertEqual(len(self.policy.read_folders()), 1)
        self.policy.remove_folder(self.allowed)
        self.assertEqual(self.policy.read_folders(), [])

    def test_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            self.policy.add_folder(os.path.join(self.outside, "nope"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_agents_paths -v` (from the repo root)
Expected: ERROR, `cannot import name 'paths' from 'agents'`.

- [ ] **Step 3: Write the implementation**

`agents/paths.py`:

```python
"""Where agents may read and write. Standard library only (ADR 0006)."""
import ctypes
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDERS_FILE = os.path.join(APP_DIR, "agent_folders.json")

_DRIVE_REMOVABLE, _DRIVE_FIXED = 2, 3


def default_sandbox():
    return os.path.join(os.path.expanduser("~"), "Documents", "agent-sandbox")


def _real(path):
    # realpath resolves junctions and symlinks, so a link can't escape a root.
    return os.path.normcase(os.path.realpath(path))


def is_inside(path, root):
    p, r = _real(path), _real(root)
    return p == r or p.startswith(r.rstrip("\\") + "\\")


def is_local(path):
    # Check for UNC before realpath: resolving a dead share can hang.
    if os.path.abspath(path).startswith("\\\\"):
        return False
    real = os.path.realpath(path)
    if real.startswith("\\\\"):
        return False
    drive = os.path.splitdrive(real)[0]
    if not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") in (_DRIVE_REMOVABLE, _DRIVE_FIXED)


class Policy:
    def __init__(self, sandbox, folders_file=FOLDERS_FILE):
        self.sandbox = os.path.abspath(sandbox)
        self.jobs_dir = os.path.join(self.sandbox, "jobs")
        self.trash_dir = os.path.join(self.sandbox, "trash")
        self.folders_file = folders_file

    def ensure_dirs(self):
        for d in (self.sandbox, self.jobs_dir, self.trash_dir):
            os.makedirs(d, exist_ok=True)

    def read_folders(self):
        try:
            with open(self.folders_file, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []

    def _save(self, folders):
        tmp = self.folders_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(folders, f, indent=2)
        os.replace(tmp, self.folders_file)

    def add_folder(self, path):
        full = os.path.abspath(path)
        if not is_local(full):
            raise ValueError(f"only local drives are allowed: {full}")
        if not os.path.isdir(full):
            raise ValueError(f"not a folder: {full}")
        folders = self.read_folders()
        if not any(_real(f) == _real(full) for f in folders):
            folders.append(full)
            self._save(folders)
        return full

    def remove_folder(self, path):
        self._save([f for f in self.read_folders() if _real(f) != _real(path)])

    def _system(self, path):
        return is_inside(path, self.jobs_dir) or is_inside(path, self.trash_dir)

    def can_read(self, path):
        if not is_local(path):
            return False
        return is_inside(path, self.sandbox) or any(is_inside(path, f) for f in self.read_folders())

    def can_write(self, path):
        return is_inside(path, self.sandbox) and not self._system(path)

    def can_change(self, path):
        # What an approved plan may touch: listed folders, never the sandbox
        # or the launcher's own folder (config, key, folder list).
        return (is_local(path)
                and any(is_inside(path, f) for f in self.read_folders())
                and not is_inside(path, self.sandbox)
                and not is_inside(path, APP_DIR))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agents_paths -v`
Expected: 8 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add agents/__init__.py agents/paths.py tests/test_agents_paths.py
git commit -m "agents: sandbox and readable-folder policy"
```

---

### Task 2: Job records and worker (`agents/jobs.py`)

**Files:**
- Create: `agents/jobs.py`
- Test: `tests/test_agents_jobs.py`

**Interfaces:**
- Produces: class `JobStore(jobs_dir)` with `create(kind, args) -> dict`, `get(job_id) -> dict` (raises `KeyError`), `update(job_id, **fields) -> dict`, `transition(job_id, expected: tuple, new_state, **fields) -> dict` (raises `ValueError`), `list() -> list[dict]` (newest first), `cancel_unfinished()`, `path(job_id, name="") -> str`, `write_text/read_text(job_id, name[, text])`, `write_json/read_json(job_id, name[, data])`, `has(job_id, name) -> bool`. Class `Worker(store, run)` with `submit(job_id)`; `run(job)` is called with the job already `running` and must set the final state. A job dict has keys `id, kind, args, state, created, updated, note`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents_jobs.py`:

```python
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import jobs


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_get(self):
        job = self.store.create("research", {"topic": "x"})
        self.assertRegex(job["id"], r"^\d{8}-\d{6}-[0-9a-f]{4}$")
        self.assertEqual(self.store.get(job["id"])["state"], "queued")

    def test_bad_job_id_is_unknown(self):
        for bad in (r"..\..\secret", "20261014-000000-zzzz", "", "a/b"):
            with self.assertRaises(KeyError):
                self.store.get(bad)

    def test_transition_refuses_wrong_state(self):
        job = self.store.create("organize", {})
        self.store.transition(job["id"], ("queued",), "running")
        with self.assertRaises(ValueError):
            self.store.transition(job["id"], ("queued",), "running")

    def test_cancel_unfinished(self):
        a = self.store.create("research", {})
        b = self.store.create("research", {})
        self.store.update(b["id"], state="done")
        self.store.cancel_unfinished()
        self.assertEqual(self.store.get(a["id"])["state"], "cancelled")
        self.assertEqual(self.store.get(b["id"])["state"], "done")

    def test_list_newest_first_and_skips_junk(self):
        a = self.store.create("research", {})
        time.sleep(0.01)
        b = self.store.create("research", {})
        os.makedirs(os.path.join(self.store.jobs_dir, "not-a-job"))
        self.assertEqual([j["id"] for j in self.store.list()], [b["id"], a["id"]])

    def test_text_and_json_files(self):
        job = self.store.create("plan", {})
        self.store.write_text(job["id"], "result.md", "hello")
        self.store.write_json(job["id"], "steps.json", [{"kind": "research"}])
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "hello")
        self.assertEqual(self.store.read_json(job["id"], "steps.json"), [{"kind": "research"}])
        self.assertFalse(self.store.has(job["id"], "plan.json"))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = jobs.JobStore(os.path.join(self.tmp.name, "jobs"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_runs_one_at_a_time_in_order(self):
        running, peak, order = [0], [0], []
        lock = threading.Lock()

        def run(job):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            order.append(job["id"])
            self.store.update(job["id"], state="done")
            with lock:
                running[0] -= 1

        worker = jobs.Worker(self.store, run)
        ids = [self.store.create("research", {})["id"] for _ in range(3)]
        for i in ids:
            worker.submit(i)
        worker.queue.join()
        self.assertEqual(order, ids)
        self.assertEqual(peak[0], 1)

    def test_exception_marks_failed(self):
        def run(job):
            raise RuntimeError("boom")
        worker = jobs.Worker(self.store, run)
        job = self.store.create("research", {})
        worker.submit(job["id"])
        worker.queue.join()
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "failed")
        self.assertIn("boom", got["note"])

    def test_cancelled_job_is_skipped(self):
        ran = []
        worker = jobs.Worker(self.store, lambda job: ran.append(job["id"]))
        job = self.store.create("research", {})
        self.store.update(job["id"], state="cancelled")
        worker.submit(job["id"])
        worker.queue.join()
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_agents_jobs -v`
Expected: ERROR, `cannot import name 'jobs'`.

- [ ] **Step 3: Write the implementation**

`agents/jobs.py`:

```python
"""Job records on disk and a one-at-a-time worker. Standard library only."""
import json
import os
import queue
import re
import secrets
import threading
import time

_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
UNFINISHED = ("queued", "running")


class JobStore:
    def __init__(self, jobs_dir):
        self.jobs_dir = jobs_dir
        self._lock = threading.Lock()
        os.makedirs(jobs_dir, exist_ok=True)

    def path(self, job_id, name=""):
        # job_id comes from the chat model: only the exact ID format is a path.
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise KeyError(job_id)
        return os.path.join(self.jobs_dir, job_id, name)

    def create(self, kind, args):
        job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        os.makedirs(self.path(job_id))
        now = time.time()
        job = {"id": job_id, "kind": kind, "args": args, "state": "queued",
               "created": now, "updated": now, "note": ""}
        self._write(job)
        return job

    def get(self, job_id):
        try:
            with open(self.path(job_id, "job.json"), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            raise KeyError(job_id)

    def update(self, job_id, **fields):
        with self._lock:
            job = self.get(job_id)
            job.update(fields, updated=time.time())
            self._write(job)
            return job

    def transition(self, job_id, expected, new_state, **fields):
        # Check-and-set under the lock, so two approvals can't both run.
        with self._lock:
            job = self.get(job_id)
            if job["state"] not in expected:
                raise ValueError(f"job {job_id} is {job['state']}, not {' or '.join(expected)}")
            job.update(fields, state=new_state, updated=time.time())
            self._write(job)
            return job

    def list(self):
        found = []
        for name in os.listdir(self.jobs_dir):
            try:
                found.append(self.get(name))
            except KeyError:
                pass
        return sorted(found, key=lambda j: j["created"], reverse=True)

    def cancel_unfinished(self):
        for job in self.list():
            if job["state"] in UNFINISHED:
                self.update(job["id"], state="cancelled", note="stopped when the servers exited")

    def has(self, job_id, name):
        return os.path.exists(self.path(job_id, name))

    def write_text(self, job_id, name, text):
        tmp = self.path(job_id, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.path(job_id, name))

    def read_text(self, job_id, name):
        with open(self.path(job_id, name), encoding="utf-8") as f:
            return f.read()

    def write_json(self, job_id, name, data):
        self.write_text(job_id, name, json.dumps(data, indent=2))

    def read_json(self, job_id, name):
        return json.loads(self.read_text(job_id, name))

    def _write(self, job):
        self.write_text(job["id"], "job.json", json.dumps(job, indent=2))


class Worker:
    def __init__(self, store, run):
        self.store, self.run = store, run
        self.queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, job_id):
        self.queue.put(job_id)

    def _loop(self):
        while True:
            job_id = self.queue.get()
            try:
                job = self.store.transition(job_id, ("queued",), "running")
            except (KeyError, ValueError):
                self.queue.task_done()  # cancelled or gone
                continue
            try:
                self.run(job)
            except Exception as e:
                self.store.update(job_id, state="failed", note=f"{type(e).__name__}: {e}")
            finally:
                self.queue.task_done()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agents_jobs -v`
Expected: 9 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add agents/jobs.py tests/test_agents_jobs.py
git commit -m "agents: job records and one-at-a-time worker"
```

---

### Task 3: Plans, apply, restore, trash (`agents/plans.py`)

**Files:**
- Create: `agents/plans.py`
- Test: `tests/test_agents_plans.py`

**Interfaces:**
- Consumes: `paths.Policy` (`can_change`, `trash_dir`), `paths.is_inside`.
- Produces: `validate_plan(ops, policy) -> list[str]` (empty = valid), `apply_plan(ops, job_id, policy) -> (log: list[dict], error: str | None)`, `restore(log) -> list[str]` (skipped items), `empty_trash(policy) -> int` (entries removed). Log entries: `{"op": "mkdir", "path"}` or `{"op": "move"|"remove", "from", "to"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents_plans.py`:

```python
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import paths, plans


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dl = os.path.join(root, "Downloads")
        touch(os.path.join(self.dl, "a.pdf"))
        touch(os.path.join(self.dl, "b.tmp"))
        self.policy = paths.Policy(os.path.join(root, "sandbox"), folders_file=os.path.join(root, "f.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.dl)
        self.p = lambda *parts: os.path.join(self.dl, *parts)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_plan(self):
        ops = [{"op": "mkdir", "path": self.p("Docs")},
               {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs", "a.pdf")},
               {"op": "remove", "path": self.p("b.tmp")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])

    def test_rejects_bad_operations(self):
        outside = os.path.join(self.tmp.name, "elsewhere.txt")
        cases = [
            [],
            [{"op": "delete", "path": self.p("a.pdf")}],
            [{"op": "move", "src": self.p("a.pdf")}],
            [{"op": "remove", "path": outside}],
            [{"op": "remove", "path": "a.pdf"}],
            [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("b.tmp")}],
            [{"op": "move", "src": self.p("missing"), "dst": self.p("x")}],
            [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("NoSuchDir", "a.pdf")}],
            [{"op": "mkdir", "path": self.p("Sub")}, {"op": "move", "src": self.dl, "dst": self.p("Sub", "x")}],
            [{"op": "mkdir", "path": self.p("a.pdf")}],
        ]
        for ops in cases:
            self.assertNotEqual(plans.validate_plan(ops, self.policy), [], ops)

    def test_apply_then_restore_round_trip(self):
        ops = [{"op": "mkdir", "path": self.p("Docs")},
               {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs", "a.pdf")},
               {"op": "remove", "path": self.p("b.tmp")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertIsNone(error)
        self.assertTrue(os.path.exists(self.p("Docs", "a.pdf")))
        self.assertFalse(os.path.exists(self.p("b.tmp")))
        self.assertTrue(log[2]["to"].startswith(os.path.join(self.policy.trash_dir, "20261014-000000-abcd")))
        self.assertEqual(plans.restore(log), [])
        self.assertTrue(os.path.exists(self.p("a.pdf")))
        self.assertTrue(os.path.exists(self.p("b.tmp")))
        self.assertFalse(os.path.exists(self.p("Docs")))

    def test_apply_stops_at_first_error(self):
        ops = [{"op": "remove", "path": self.p("a.pdf")},
               {"op": "move", "src": self.p("gone"), "dst": self.p("x")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertEqual(len(log), 1)
        self.assertIn("step 2", error)

    def test_restore_skips_occupied_paths(self):
        ops = [{"op": "remove", "path": self.p("a.pdf")}, {"op": "remove", "path": self.p("b.tmp")}]
        log, _ = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        touch(self.p("a.pdf"), "new file")
        skipped = plans.restore(log)
        self.assertEqual(len(skipped), 1)
        self.assertIn("a.pdf", skipped[0])
        with open(self.p("a.pdf")) as f:
            self.assertEqual(f.read(), "new file")
        self.assertTrue(os.path.exists(self.p("b.tmp")))

    def test_empty_trash(self):
        plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], "20261014-000000-abcd", self.policy)
        self.assertEqual(plans.empty_trash(self.policy), 1)
        self.assertEqual(os.listdir(self.policy.trash_dir), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_agents_plans -v`
Expected: ERROR, `cannot import name 'plans'`.

- [ ] **Step 3: Write the implementation**

`agents/plans.py`:

```python
"""Validate, apply and undo organise plans. Standard library only."""
import os
import shutil

from .paths import is_inside

OPS = {"mkdir": ("path",), "move": ("src", "dst"), "remove": ("path",)}


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def validate_plan(ops, policy):
    if not isinstance(ops, list) or not ops:
        return ["the plan has no operations"]
    errors, created = [], set()
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            errors.append(f"{i}: unknown operation {op!r}; use mkdir, move or remove")
            continue
        fields = OPS[op["op"]]
        if any(not isinstance(op.get(k), str) or not op.get(k) for k in fields):
            errors.append(f"{i}: {op['op']} needs {', '.join(fields)}")
            continue
        bad = [op[k] for k in fields if not os.path.isabs(op[k]) or not policy.can_change(op[k])]
        if bad:
            errors.append(f"{i}: not allowed (use absolute paths inside the readable folders): {', '.join(bad)}")
            continue
        if op["op"] == "mkdir":
            if os.path.exists(op["path"]):
                errors.append(f"{i}: already exists: {op['path']}")
            created.add(_key(op["path"]))
        elif op["op"] == "move":
            src, dst = op["src"], op["dst"]
            parent = os.path.dirname(dst)
            if not os.path.exists(src):
                errors.append(f"{i}: source missing: {src}")
            elif os.path.exists(dst):
                errors.append(f"{i}: destination already exists: {dst}")
            elif not (os.path.isdir(parent) or _key(parent) in created):
                errors.append(f"{i}: destination folder missing (add a mkdir first): {parent}")
            elif is_inside(dst, src):
                errors.append(f"{i}: can't move a folder into itself: {src}")
        elif not os.path.exists(op["path"]):
            errors.append(f"{i}: nothing to remove at {op['path']}")
    return errors


def trash_path(policy, job_id, path):
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return os.path.join(policy.trash_dir, job_id, drive.rstrip(":"), rest.lstrip("\\/"))


def apply_plan(ops, job_id, policy):
    log = []
    for i, op in enumerate(ops, 1):
        try:
            if op["op"] == "mkdir":
                os.makedirs(op["path"])
                log.append({"op": "mkdir", "path": op["path"]})
            elif op["op"] == "move":
                shutil.move(op["src"], op["dst"])
                log.append({"op": "move", "from": op["src"], "to": op["dst"]})
            else:
                to = trash_path(policy, job_id, op["path"])
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.move(op["path"], to)
                log.append({"op": "remove", "from": op["path"], "to": to})
        except OSError as e:
            return log, f"step {i} failed: {e}"
    return log, None


def restore(log):
    skipped = []
    for entry in reversed(log):
        if entry["op"] == "mkdir":
            try:
                os.rmdir(entry["path"])
            except OSError:
                skipped.append(f"kept folder (not empty or gone): {entry['path']}")
        elif os.path.exists(entry["from"]):
            skipped.append(f"something new is already at {entry['from']}")
        elif not os.path.exists(entry["to"]):
            skipped.append(f"no longer there (trash emptied?): {entry['to']}")
        else:
            os.makedirs(os.path.dirname(entry["from"]), exist_ok=True)
            shutil.move(entry["to"], entry["from"])
    return skipped


def empty_trash(policy):
    count = 0
    for name in os.listdir(policy.trash_dir):
        shutil.rmtree(os.path.join(policy.trash_dir, name))
        count += 1
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agents_plans -v`
Expected: 6 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add agents/plans.py tests/test_agents_plans.py
git commit -m "agents: validate, apply and restore organise plans"
```

---

### Task 4: HTTP clients (`agents/llm.py`)

**Files:**
- Create: `agents/llm.py`
- Test: `tests/test_agents_llm.py`

**Interfaces:**
- Consumes: `paths.APP_DIR`.
- Produces: `API_KEY_FILE`, `chat(messages, tools=None, url=LLAMA_URL, key_file=API_KEY_FILE, timeout=900) -> dict` (the assistant `message`, may contain `tool_calls`), `mcp_call(server, tool, args, url=MCP_URL, timeout=120) -> str` (raises `RuntimeError` on tool or protocol error).

- [ ] **Step 1: Write the failing tests** (a real local HTTP server, no mocks)

`tests/test_agents_llm.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_agents_llm -v`
Expected: ERROR, `cannot import name 'llm'`.

- [ ] **Step 3: Write the implementation**

`agents/llm.py`:

```python
"""HTTP calls to llama-server and the UVX MCP proxy. Standard library only."""
import json
import os
import urllib.request

from .paths import APP_DIR

LLAMA_URL = "http://127.0.0.1:8081/v1/chat/completions"
MCP_URL = "http://127.0.0.1:8001/servers/{server}/mcp"
API_KEY_FILE = os.path.join(APP_DIR, "api_key.txt")


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(messages, tools=None, url=LLAMA_URL, key_file=API_KEY_FILE, timeout=900):
    with open(key_file, encoding="utf-8") as f:
        key = f.read().strip()
    body = {"messages": messages}
    if tools:
        body["tools"] = tools
    data = _post(url, body, {"Authorization": f"Bearer {key}"}, timeout)
    return data["choices"][0]["message"]


def mcp_call(server, tool, args, url=MCP_URL, timeout=120):
    # mcp-proxy runs --stateless, so one JSON-RPC POST per call works without
    # an initialize handshake (verified against mcp-proxy and mcp 2.3).
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    data = _post(url.format(server=server), request,
                 {"Accept": "application/json, text/event-stream"}, timeout)
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    result = data["result"]
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text)
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agents_llm -v`
Expected: 3 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add agents/llm.py tests/test_agents_llm.py
git commit -m "agents: llama-server and MCP proxy clients"
```

---

### Task 5: Agent loop and job kinds (`agents/runner.py`)

**Files:**
- Create: `agents/runner.py`
- Test: `tests/test_agents_runner.py`

**Interfaces:**
- Consumes: `paths.Policy`, `jobs.JobStore`, `plans.*`; `chat(messages, tools) -> message` and `mcp_call(server, tool, args) -> str` with the signatures from Task 4 (injected, so tests pass fakes).
- Produces: `MAX_CALLS = 40`, `MAX_SECONDS = 1200`, class `Context(policy, store, chat, mcp_call)`, `run_agent(chat, system, task, tools, max_calls, max_seconds, clock) -> (text, stop_reason)`, `run_job(job, ctx)`, `approve(job_id, ctx) -> str`, `reject(job_id, ctx) -> str`, `undo(job_id, ctx) -> str`, `queue_steps(job_id, ctx, submit) -> str`, `describe(job) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents_runner.py`:

```python
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import jobs, paths, runner


def call(name, args, cid="c1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": raw}}]}


def final(text):
    return {"role": "assistant", "content": text}


class Script:
    """A chat function that replays canned replies and records what it was sent."""
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools=None):
        self.seen.append((list(messages), tools))
        return self.replies.pop(0)


class RunAgentTests(unittest.TestCase):
    def echo_tool(self):
        return runner.tool("echo", "Echo", {"text": {"type": "string"}}, ["text"], lambda text: f"echo:{text}")

    def test_tool_result_goes_back_to_model(self):
        chat = Script(call("echo", {"text": "hi"}), final("done"))
        text, stop = runner.run_agent(chat, "sys", "task", [self.echo_tool()])
        self.assertEqual((text, stop), ("done", ""))
        self.assertEqual(chat.seen[1][0][-1], {"role": "tool", "tool_call_id": "c1", "content": "echo:hi"})

    def test_malformed_tool_arguments_return_error(self):
        chat = Script(call("echo", "not json"), call("echo", "[1, 2]"), call("nope", {}), final("ok"))
        text, _ = runner.run_agent(chat, "sys", "task", [self.echo_tool()])
        results = [msgs[-1]["content"] for msgs, _ in chat.seen[1:]]
        self.assertTrue(all(r.startswith("error:") for r in results), results)
        self.assertEqual(text, "ok")

    def test_call_cap_stops_and_asks_for_final_answer(self):
        chat = Script(*([call("echo", {"text": "x"})] * 3), final("partial"))
        text, stop = runner.run_agent(chat, "sys", "task", [self.echo_tool()], max_calls=2)
        self.assertEqual(text, "partial")
        self.assertIn("2 tool calls", stop)
        self.assertIsNone(chat.seen[-1][1])  # final call offers no tools

    def test_time_cap(self):
        ticks = iter([0, 0, 2000, 2000])
        chat = Script(call("echo", {"text": "x"}), call("echo", {"text": "y"}), final("late"))
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], max_seconds=1200, clock=lambda: next(ticks))
        self.assertIn("20 minutes", stop)


class JobKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dl = os.path.join(root, "Downloads")
        os.makedirs(self.dl)
        with open(os.path.join(self.dl, "a.pdf"), "w") as f:
            f.write("x")
        self.policy = paths.Policy(os.path.join(root, "sandbox"), folders_file=os.path.join(root, "f.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.dl)
        self.store = jobs.JobStore(self.policy.jobs_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, chat):
        return runner.Context(self.policy, self.store, chat, lambda s, t, a: f"{s}/{t}:{json.dumps(a)}")

    def start(self, kind, args):
        job = self.store.create(kind, args)
        return self.store.transition(job["id"], ("queued",), "running")

    def test_research_writes_report(self):
        job = self.start("research", {"topic": "llama"})
        chat = Script(call("web_search", {"query": "llama"}), final("# Report\nSources: x"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "done")
        self.assertIn("# Report", self.store.read_text(job["id"], "result.md"))
        self.assertIn("ddg-search/search", chat.seen[1][0][-1]["content"])

    def test_plan_saves_steps(self):
        job = self.start("plan", {"goal": "g"})
        steps = [{"kind": "research", "topic": "t"}, {"kind": "organize", "folder": self.dl, "goal": "tidy"},
                 {"kind": "hack", "x": 1}]
        chat = Script(call("propose_steps", {"steps": steps}), final("1. do it"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.read_json(job["id"], "steps.json"), steps[:2])
        self.assertEqual(self.store.get(job["id"])["state"], "done")

    def organize(self, ops):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        chat = Script(call("propose_plan", {"operations": ops}), final("summary"), final("looks fine"))
        runner.run_job(job, self.ctx(chat))
        return job["id"]

    def test_organize_waits_for_approval_with_review(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        self.assertEqual(self.store.read_text(job_id, "review.md"), "looks fine")
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))  # nothing changed yet

    def test_organize_without_valid_plan_fails(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        bad = [{"op": "remove", "path": "C:\\Windows"}]
        chat = Script(call("propose_plan", {"operations": bad}), final("gave up"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")
        self.assertIn("not allowed", chat.seen[1][0][-1]["content"])

    def test_approve_then_undo(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        ctx = self.ctx(Script())
        self.assertIn("Applied 1", runner.approve(job_id, ctx))
        self.assertFalse(os.path.exists(os.path.join(self.dl, "a.pdf")))
        self.assertIn("Restored", runner.undo(job_id, ctx))
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))
        self.assertEqual(self.store.get(job_id)["state"], "restored")

    def test_second_approval_is_refused(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        ctx = self.ctx(Script())
        runner.approve(job_id, ctx)
        self.assertIn("not awaiting_approval", runner.approve(job_id, ctx))
        self.assertEqual(len(self.store.read_json(job_id, "applied.json")), 1)

    def test_reject(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        runner.reject(job_id, self.ctx(Script()))
        self.assertEqual(self.store.get(job_id)["state"], "rejected")

    def test_queue_steps_once(self):
        job = self.start("plan", {"goal": "g"})
        runner.run_job(job, self.ctx(Script(call("propose_steps", {"steps": [{"kind": "research", "topic": "t"}]}), final("p"))))
        submitted = []
        self.assertIn("Queued 1", runner.queue_steps(job["id"], self.ctx(Script()), submitted.append))
        self.assertIn("already", runner.queue_steps(job["id"], self.ctx(Script()), submitted.append))
        self.assertEqual(len(submitted), 1)
        self.assertEqual(self.store.get(submitted[0])["args"], {"topic": "t"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_agents_runner -v`
Expected: ERROR, `cannot import name 'runner'`.

- [ ] **Step 3: Write the implementation**

`agents/runner.py`:

```python
"""Agent loop, the three job kinds, and plan approval. Standard library only."""
import json
import os
import time

from . import plans

MAX_CALLS = 40
MAX_SECONDS = 20 * 60
MAX_RESULT_CHARS = 12000


class Context:
    def __init__(self, policy, store, chat, mcp_call):
        self.policy, self.store, self.chat, self.mcp_call = policy, store, chat, mcp_call


def tool(name, description, params, required, fn):
    schema = {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": params, "required": required}}}
    return name, schema, fn


def _invoke(fns, name, raw_args):
    if name not in fns:
        return f"error: unknown tool {name!r}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
        return str(fns[name](**args))[:MAX_RESULT_CHARS]
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


def run_agent(chat, system, task, tools, max_calls=MAX_CALLS, max_seconds=MAX_SECONDS, clock=time.monotonic):
    schemas = [t[1] for t in tools]
    fns = {t[0]: t[2] for t in tools}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    start, used = clock(), 0
    while True:
        reply = chat(messages, schemas)
        requested = reply.get("tool_calls") or []
        if not requested:
            return reply.get("content") or "", ""
        messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": requested})
        over_calls = used >= max_calls
        if over_calls or clock() - start >= max_seconds:
            reason = (f"stopped after {used} tool calls" if over_calls
                      else f"stopped after {max_seconds // 60} minutes")
            for c in requested:
                messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": "Limit reached."})
            messages.append({"role": "user", "content": "You are out of tool calls. Write your final answer now from what you have."})
            return chat(messages, None).get("content") or "", reason
        for c in requested:
            used += 1
            fn = c.get("function", {})
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""),
                             "content": _invoke(fns, fn.get("name"), fn.get("arguments"))})


def file_tools(policy):
    def list_dir(path):
        if not policy.can_read(path):
            return f"error: no read access to {path}. Ask the user to add the folder."
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())[:500]
        lines = [f"[dir] {e.name}" if e.is_dir() else f"{e.name}  ({e.stat().st_size} bytes)" for e in entries]
        return "\n".join(lines) or "(empty)"

    def read_file(path):
        if not policy.can_read(path):
            return f"error: no read access to {path}. Ask the user to add the folder."
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(20000)

    def write_file(path, content):
        full = path if os.path.isabs(path) else os.path.join(policy.sandbox, path)
        if not policy.can_write(full):
            return f"error: you can only write inside {policy.sandbox}"
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {full}"

    s = {"type": "string"}
    return [tool("list_dir", "List a folder.", {"path": s}, ["path"], list_dir),
            tool("read_file", "Read a text file (first 20,000 characters).", {"path": s}, ["path"], read_file),
            tool("write_file", "Write a file inside the sandbox.", {"path": s, "content": s}, ["path", "content"], write_file)]


def web_tools(mcp_call):
    s = {"type": "string"}
    return [tool("web_search", "Search the web.", {"query": s}, ["query"],
                 lambda query: mcp_call("ddg-search", "search", {"query": query, "max_results": 8})),
            tool("fetch_url", "Read a web page as text.", {"url": s}, ["url"],
                 lambda url: mcp_call("fetch", "fetch", {"url": url, "max_length": MAX_RESULT_CHARS}))]


RESEARCH_PROMPT = (
    "You are a careful research assistant. Use web_search and fetch_url to find primary sources "
    "(official docs, papers, first-party pages). Then write a Markdown report: a short summary first, "
    "then the findings, then a Sources list. Cite a source URL for every claim, and say where sources disagree.")

PLAN_PROMPT = (
    "You plan projects. Research as needed, then write a Markdown plan with numbered steps. Before your "
    "final answer, call propose_steps once with follow-up jobs worth running, each either "
    '{"kind": "research", "topic": "..."} or {"kind": "organize", "folder": "<absolute path>", "goal": "..."}.')

ORGANIZE_PROMPT = (
    "You organise files inside {folder}. Look around with list_dir and read_file, then call propose_plan "
    "once with the full list of operations, using absolute paths:\n"
    '{{"op": "mkdir", "path": ...}}, {{"op": "move", "src": ..., "dst": ...}} (also for renames), '
    '{{"op": "remove", "path": ...}} (goes to a trash the user can restore).\n'
    "Nothing runs until the user approves. If propose_plan reports errors, fix them and call it again. "
    "Finish with a short summary of the plan.")

REVIEW_PROMPT = (
    "You review a file-organising plan before a person approves it. The goal was: {goal}\n"
    "List every operation that looks wrong, risky, or unrelated to the goal, especially removals, "
    "with its number and the reason. If everything looks right, say so in one line.")


def run_research(job, ctx):
    tools = web_tools(ctx.mcp_call) + file_tools(ctx.policy)
    text, stop = run_agent(ctx.chat, RESEARCH_PROMPT, job["args"]["topic"], tools)
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.update(job["id"], state="done", note=stop)


def run_plan(job, ctx):
    saved = []

    def propose_steps(steps):
        good = [s for s in steps if isinstance(s, dict) and (
            (s.get("kind") == "research" and s.get("topic"))
            or (s.get("kind") == "organize" and s.get("folder") and s.get("goal")))]
        saved[:] = good
        return f"saved {len(good)} follow-up jobs"

    steps_param = {"steps": {"type": "array", "items": {"type": "object"}}}
    tools = web_tools(ctx.mcp_call) + file_tools(ctx.policy) + [
        tool("propose_steps", "Save follow-up jobs for the user to queue.", steps_param, ["steps"], propose_steps)]
    text, stop = run_agent(ctx.chat, PLAN_PROMPT, job["args"]["goal"], tools)
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.write_json(job["id"], "steps.json", saved)
    ctx.store.update(job["id"], state="done", note=stop)


def run_organize(job, ctx):
    folder, goal = job["args"]["folder"], job["args"]["goal"]
    proposed = []

    def propose_plan(operations):
        errors = plans.validate_plan(operations, ctx.policy)
        if errors:
            return "error: plan rejected:\n" + "\n".join(errors)
        proposed[:] = operations
        return f"plan saved with {len(operations)} operations"

    ops_param = {"operations": {"type": "array", "items": {"type": "object"}}}
    tools = file_tools(ctx.policy) + [
        tool("propose_plan", "Propose the full list of file operations.", ops_param, ["operations"], propose_plan)]
    text, stop = run_agent(ctx.chat, ORGANIZE_PROMPT.format(folder=folder), goal, tools)
    ctx.store.write_text(job["id"], "result.md", text)
    if not proposed:
        ctx.store.update(job["id"], state="failed", note="no valid plan was proposed" + (f"; {stop}" if stop else ""))
        return
    ctx.store.write_json(job["id"], "plan.json", proposed)
    review = ctx.chat([{"role": "system", "content": REVIEW_PROMPT.format(goal=goal)},
                       {"role": "user", "content": json.dumps(proposed, indent=2)}], None)
    ctx.store.write_text(job["id"], "review.md", review.get("content") or "")
    ctx.store.update(job["id"], state="awaiting_approval", note=stop)


KINDS = {"research": run_research, "plan": run_plan, "organize": run_organize}


def run_job(job, ctx):
    KINDS[job["kind"]](job, ctx)


def approve(job_id, ctx):
    try:
        ctx.store.transition(job_id, ("awaiting_approval",), "applying")
    except (KeyError, ValueError) as e:
        return f"Not applied: {e}"
    ops = ctx.store.read_json(job_id, "plan.json")
    errors = plans.validate_plan(ops, ctx.policy)
    if errors:
        ctx.store.update(job_id, state="awaiting_approval", note="files changed since the plan was made")
        return "Not applied; the plan no longer matches the files:\n" + "\n".join(errors)
    log, error = plans.apply_plan(ops, job_id, ctx.policy)
    ctx.store.write_json(job_id, "applied.json", log)
    if error:
        ctx.store.update(job_id, state="failed", note=f"partly applied: {error}")
        return f"Partly applied ({len(log)} of {len(ops)}): {error}. Restore undoes the completed steps."
    ctx.store.update(job_id, state="applied")
    return f"Applied {len(log)} operations."


def reject(job_id, ctx):
    try:
        ctx.store.transition(job_id, ("awaiting_approval",), "rejected")
    except (KeyError, ValueError) as e:
        return f"Not rejected: {e}"
    return "Rejected; nothing was changed."


def undo(job_id, ctx):
    try:
        if not ctx.store.has(job_id, "applied.json"):
            return "Nothing to restore: this job never changed any files."
        ctx.store.transition(job_id, ("applied", "failed"), "restoring")
    except (KeyError, ValueError) as e:
        return f"Not restored: {e}"
    skipped = plans.restore(ctx.store.read_json(job_id, "applied.json"))
    ctx.store.update(job_id, state="restored", note=f"{len(skipped)} skipped" if skipped else "")
    return "Restored." + ("\nSkipped:\n" + "\n".join(skipped) if skipped else "")


def queue_steps(job_id, ctx, submit):
    try:
        job = ctx.store.get(job_id)
        if job.get("steps_queued"):
            return "Those steps were already queued."
        steps = ctx.store.read_json(job_id, "steps.json") if ctx.store.has(job_id, "steps.json") else []
        if not steps:
            return "This job has no follow-up steps."
        ctx.store.transition(job_id, ("done",), "done", steps_queued=True)
    except (KeyError, ValueError) as e:
        return f"Not queued: {e}"
    for s in steps:
        args = {k: v for k, v in s.items() if k != "kind"}
        submit(ctx.store.create(s["kind"], args)["id"])
    return f"Queued {len(steps)} jobs."


def describe(job):
    args = ", ".join(f"{k}={v}" for k, v in job["args"].items())
    note = f" ({job['note']})" if job.get("note") else ""
    return f"{job['id']}  {job['kind']}  {job['state']}{note}  [{args}]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_agents_runner -v`
Expected: 13 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add agents/runner.py tests/test_agents_runner.py
git commit -m "agents: agent loop with caps, job kinds, plan approval and undo"
```

---

### Task 6: MCP server (`agents/server.py`)

**Files:**
- Create: `agents/server.py`
- Test: smoke test through mcp-proxy on a spare port (no llama-server needed)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: MCP tools `research(topic)`, `plan(goal)`, `organize(folder, goal)`, `job_status(job_id="")`, `job_result(job_id)`, `apply(job_id)`, `reject(job_id)`, `restore(job_id)`, `queue_steps(job_id)`, `request_folder_access(path)`, all returning `str`. Served at `http://127.0.0.1:8001/servers/agents/mcp`.

- [ ] **Step 1: Write the server**

`agents/server.py`:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3"]
# ///
"""Agents MCP server (ADR 0006). Run by mcp-proxy with `uv run --script`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from agents import jobs, llm, paths, runner  # noqa: E402

policy = paths.Policy(paths.default_sandbox())
policy.ensure_dirs()
store = jobs.JobStore(policy.jobs_dir)
store.cancel_unfinished()  # anything left from a previous run was killed with it
ctx = runner.Context(policy, store, llm.chat, llm.mcp_call)
worker = jobs.Worker(store, lambda job: runner.run_job(job, ctx))

server = MCPServer("agents", instructions=(
    "Background jobs on the user's PC. Tools return a job ID at once; check progress with job_status "
    "and read output with job_result. organize only proposes a plan; the user approves it."))


def _start(kind, args):
    job = store.create(kind, args)
    worker.submit(job["id"])
    return f"Started {kind} job {job['id']}. Check it with job_status; the user can also watch the launcher's Jobs pane."


@server.tool()
def research(topic: str) -> str:
    """Research a topic on the web and write a cited Markdown report."""
    return _start("research", {"topic": topic})


@server.tool()
def plan(goal: str) -> str:
    """Write a step-by-step plan for a goal, with follow-up jobs the user can queue."""
    return _start("plan", {"goal": goal})


@server.tool()
def organize(folder: str, goal: str) -> str:
    """Propose a plan to organise a local folder. Nothing changes until the user approves it."""
    if not policy.can_read(folder):
        return f"No read access to {folder}. Call request_folder_access first."
    return _start("organize", {"folder": os.path.abspath(folder), "goal": goal})


@server.tool()
def job_status(job_id: str = "") -> str:
    """Status of one job, or the 10 most recent jobs if no ID is given."""
    try:
        found = [store.get(job_id)] if job_id else store.list()[:10]
    except KeyError:
        return f"No job {job_id}."
    return "\n".join(runner.describe(j) for j in found) or "No jobs yet."


@server.tool()
def job_result(job_id: str) -> str:
    """The job's report, plan document, or proposed file plan with its review."""
    try:
        job = store.get(job_id)
    except KeyError:
        return f"No job {job_id}."
    parts = [runner.describe(job)]
    for name in ("result.md", "plan.json", "review.md", "steps.json"):
        if store.has(job_id, name):
            parts.append(f"--- {name} ---\n{store.read_text(job_id, name)}")
    return "\n\n".join(parts)


@server.tool()
def apply(job_id: str) -> str:
    """Apply an approved organise plan exactly as proposed."""
    return runner.approve(job_id, ctx)


@server.tool()
def reject(job_id: str) -> str:
    """Reject an organise plan; nothing changes."""
    return runner.reject(job_id, ctx)


@server.tool()
def restore(job_id: str) -> str:
    """Undo an applied organise plan, putting moved and removed files back."""
    return runner.undo(job_id, ctx)


@server.tool()
def queue_steps(job_id: str) -> str:
    """Queue the follow-up jobs a plan job proposed."""
    return runner.queue_steps(job_id, ctx, worker.submit)


@server.tool()
def request_folder_access(path: str) -> str:
    """Ask to let agents read a local folder. The user's approval of this call grants it."""
    try:
        return f"Agents can now read {policy.add_folder(path)}."
    except ValueError as e:
        return f"Not added: {e}"


if __name__ == "__main__":
    server.run()
```

- [ ] **Step 2: Smoke test through mcp-proxy on port 18001**

Write a throwaway config next to the plan run (not in the repo), start mcp-proxy on 18001, call tools, stop it:

```bash
SP=$(mktemp -d)
python - "$SP/cfg.json" "$(cygpath -w "$PWD/agents/server.py")" <<'EOF'
import json, sys
json.dump({"mcpServers": {"agents": {"command": "uv", "args": ["run", "--script", sys.argv[2]]}}}, open(sys.argv[1], "w"))
EOF
uvx mcp-proxy --named-server-config "$(cygpath -w "$SP/cfg.json")" --port 18001 --stateless > "$SP/proxy.log" 2>&1 &
sleep 30
mcp() { curl -s -X POST http://127.0.0.1:18001/servers/agents/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}"; echo; }
curl -s -X POST http://127.0.0.1:18001/servers/agents/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python -c "import json,sys; print(sorted(t['name'] for t in json.load(sys.stdin)['result']['tools']))"
mcp job_status '{}'
mcp request_folder_access '{"path":"\\\\\\\\server\\\\share"}'
mcp job_result '{"job_id":"..\\\\..\\\\x"}'
PID=$(netstat -ano | grep ":18001 " | grep LISTENING | awk '{print $5}'); taskkill //F //T //PID $PID
```

Expected:
- The tool list prints `['apply', 'job_result', 'job_status', 'organize', 'plan', 'queue_steps', 'reject', 'request_folder_access', 'research', 'restore']`.
- `job_status` returns text (either "No jobs yet." or a job list).
- The UNC folder request returns "Not added: only local drives are allowed".
- The traversal ID returns "No job ...".

If the tool list fails, read `$SP/proxy.log` for the server's traceback.

- [ ] **Step 3: Commit**

```bash
git add agents/server.py
git commit -m "agents: MCP server exposing job tools"
```

---

### Task 7: Launcher wiring: server entry, Agent folders dialog, cancel on exit

**Files:**
- Modify: `launcher.pyw`: imports (top), Section 2 constants, Section 3 helpers, `SettingsDialog` (around lines 640–770), `_open_settings` (around line 1074), `run_commands` (around line 1460), `_kill_all` (around line 1410)
- Modify: `tests/test_launcher.py` (sys.path line plus new tests)

**Interfaces:**
- Consumes: `agents.paths.Policy`, `agents.paths.default_sandbox`, `agents.jobs.JobStore`.
- Produces: `AGENTS_SERVER: str`, `ensure_agents_server(config, server_path) -> bool`, `CommandLauncherGUI.agent_policy: Policy`, class `FoldersDialog(parent, policy)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_launcher.py`, add after the existing `import unittest` line:

```python
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

Then add before `if __name__ == "__main__":`:

```python
class EnsureAgentsServerTests(unittest.TestCase):
    def test_adds_entry_once(self):
        config = {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
        self.assertTrue(launcher.ensure_agents_server(config, r"C:\app\agents\server.py"))
        self.assertEqual(config["mcpServers"]["agents"],
                         {"command": "uv", "args": ["run", "--script", r"C:\app\agents\server.py"]})
        self.assertFalse(launcher.ensure_agents_server(config, r"C:\app\agents\server.py"))
        self.assertIn("fetch", config["mcpServers"])

    def test_updates_moved_path(self):
        config = {"mcpServers": {"agents": {"command": "uv", "args": ["run", "--script", r"D:\old\server.py"]}}}
        self.assertTrue(launcher.ensure_agents_server(config, r"C:\new\server.py"))
        self.assertEqual(config["mcpServers"]["agents"]["args"][-1], r"C:\new\server.py")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_launcher -v`
Expected: the two new tests ERROR with `module 'launcher' has no attribute 'ensure_agents_server'`; the other 16 pass.

- [ ] **Step 3: Implement**

1. Imports (after `import time`):

```python
from agents import jobs as agent_jobs, paths as agent_paths, plans as agent_plans, llm as agent_llm
```

2. Section 2, after `API_KEY_FILE`:

```python
# The agents MCP server (ADR 0006); mcp-proxy starts it from config.json.
AGENTS_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "server.py")
```

3. Section 3, after `resolve_uvx_command`:

```python
def ensure_agents_server(config, server_path):
    # Keep config.json's agents entry pointing at this folder's server.py.
    # mcp-proxy reads mcpServers itself, so a {config}-style placeholder can't work here.
    entry = {"command": "uv", "args": ["run", "--script", server_path]}
    servers = config.setdefault("mcpServers", {})
    if servers.get("agents") == entry:
        return False
    servers["agents"] = entry
    return True
```

4. New class at the end of Section 7 (after `SettingsDialog`):

```python
class FoldersDialog(tk.Toplevel):
    # Folders agents may read (and organise through approved plans).
    def __init__(self, parent, policy):
        super().__init__(parent)
        self.title("Agent folders")
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x320")
        self.grab_set()
        self._policy = policy
        section_label(self, "FOLDERS AGENTS CAN READ").pack(fill="x", padx=20, pady=(20, 6))
        tk.Label(self, text=f"Agents always write only to {policy.sandbox}. Local drives only.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG, wraplength=W_SETTINGS - 40,
                 justify="left").pack(padx=20, anchor="w")
        self._list = tk.Listbox(self, font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI, relief="flat",
                                highlightthickness=1, highlightbackground=BORDER, height=8)
        self._list.pack(fill="both", expand=True, padx=20, pady=10)
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 20))
        flat_btn(row, "Add folder…", self._add).pack(side="left", padx=(0, 8))
        small_btn(row, "Remove", self._remove).pack(side="left")
        small_btn(row, "Close", self.destroy).pack(side="right")
        self._refresh()

    def _refresh(self):
        self._list.delete(0, "end")
        for f in self._policy.read_folders():
            self._list.insert("end", f)

    def _add(self):
        path = filedialog.askdirectory(title="Let agents read this folder", parent=self)
        if not path:
            return
        try:
            self._policy.add_folder(path)
        except ValueError as e:
            messagebox.showerror("Not added", str(e), parent=self)
        self._refresh()

    def _remove(self):
        sel = self._list.curselection()
        if sel:
            self._policy.remove_folder(self._list.get(sel[0]))
            self._refresh()
```

5. `SettingsDialog`: add a parameter `on_open_folders=None` to `__init__`. After the Web UI description label (`"Open the browser automatically…"`), add:

```python
        if on_open_folders:
            agents_row = tk.Frame(self, bg=BG)
            agents_row.pack(fill="x", padx=20, pady=(0, 14))
            tk.Label(agents_row, text="Agent folders", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
            small_btn(agents_row, "Manage…", on_open_folders).pack(side="right")
```

Then raise the dialog heights by 40 in Section 1: `H_SETTINGS = 415`, `H_SETTINGS_UVX = 505`.

6. `CommandLauncherGUI.__init__`, right after `self.api_key = load_api_key(API_KEY_FILE)`:

```python
        self.agent_policy  = agent_paths.Policy(agent_paths.default_sandbox())
        self.agent_policy.ensure_dirs()
```

7. `_open_settings`: pass `on_open_folders=lambda: FoldersDialog(self.master, self.agent_policy),` to `SettingsDialog(...)`.

8. `run_commands`, as the first lines inside `try:`:

```python
            if ensure_agents_server(self.config, AGENTS_SERVER):
                save_config(self.config)
```

9. `_kill_all`, at the end:

```python
        # Jobs ran inside the UVX proxy's agents server, which is gone now.
        agent_jobs.JobStore(self.agent_policy.jobs_dir).cancel_unfinished()
```

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: every test passes, including the 2 new launcher tests.

- [ ] **Step 5: Headless check that the launcher still builds**

Run: `python -c "import importlib.machinery as m, importlib.util as u, tkinter as tk; l=m.SourceFileLoader('launcher','launcher.pyw'); s=u.spec_from_loader('launcher',l); L=u.module_from_spec(s); l.exec_module(L); r=tk.Tk(); r.withdraw(); a=L.CommandLauncherGUI(r); print(a.agent_policy.sandbox); L.FoldersDialog(r, a.agent_policy).destroy(); r.destroy()"`
Expected: prints `C:\Users\choua\Documents\agent-sandbox`, with no traceback.

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw tests/test_launcher.py
git commit -m "launcher: register agents server, agent folders dialog, cancel jobs on exit"
```

---

### Task 8: Jobs pane

**Files:**
- Modify: `launcher.pyw`: Section 3 (new pure helper), Section 1 constants, running-state build (around lines 1011–1050), `_enter_running_state` / `_exit_running_state`, and new methods in a "Jobs pane" block before "Process management"
- Modify: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `agent_jobs.JobStore`, `agent_plans.empty_trash`, `agent_llm.mcp_call(server, tool, args)`, `runner.describe` format (not imported; the pane formats its own rows).
- Produces: `job_actions(job, has_result, has_applied_log, has_steps) -> set[str]` with members from `{"open", "review", "restore", "queue"}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_launcher.py`:

```python
class JobActionsTests(unittest.TestCase):
    def job(self, kind, state, **extra):
        return dict({"id": "20261014-000000-abcd", "kind": kind, "state": state, "args": {}, "note": ""}, **extra)

    def test_awaiting_plan_can_be_reviewed(self):
        self.assertEqual(launcher.job_actions(self.job("organize", "awaiting_approval"), True, False, False),
                         {"open", "review"})

    def test_applied_or_partly_applied_can_be_restored(self):
        self.assertIn("restore", launcher.job_actions(self.job("organize", "applied"), True, True, False))
        self.assertIn("restore", launcher.job_actions(self.job("organize", "failed"), True, True, False))
        self.assertNotIn("restore", launcher.job_actions(self.job("organize", "failed"), True, False, False))

    def test_plan_steps_queue_once(self):
        self.assertIn("queue", launcher.job_actions(self.job("plan", "done"), True, False, True))
        self.assertNotIn("queue", launcher.job_actions(self.job("plan", "done", steps_queued=True), True, False, True))

    def test_running_job_has_nothing(self):
        self.assertEqual(launcher.job_actions(self.job("research", "running"), False, False, False), set())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_launcher -v`
Expected: the 4 new tests ERROR with `no attribute 'job_actions'`.

- [ ] **Step 3: Implement**

1. Section 3, after `ensure_agents_server`:

```python
def job_actions(job, has_result, has_applied_log, has_steps):
    # Which Jobs-pane buttons apply to this job.
    actions = set()
    if has_result:
        actions.add("open")
    if job["state"] == "awaiting_approval":
        actions.add("review")
    if job["state"] in ("applied", "failed") and has_applied_log:
        actions.add("restore")
    if job["kind"] == "plan" and job["state"] == "done" and has_steps and not job.get("steps_queued"):
        actions.add("queue")
    return actions
```

2. Section 1: change `H_RUNNING   = 750` to `H_RUNNING   = 970` to make room for the pane.

3. Running-state build, before `llama_frame, self.llama_log = _make_log_pane(...)`:

```python
        # Jobs pane: agent jobs read straight from their job folders (ADR 0006).
        jobs_frame = tk.Frame(self.log_pane, bg=BG)
        jobs_hdr = tk.Frame(jobs_frame, bg=BG)
        jobs_hdr.pack(fill="x", pady=(0, 4))
        tk.Frame(jobs_hdr, bg=ACCENT, width=3).pack(side="left", fill="y")
        tk.Label(jobs_hdr, text="Agent jobs", font=FONT_LABEL, fg=TEXT_MUT, bg=BG).pack(side="left", padx=(8, 0), pady=4)
        self._job_buttons = {}
        for key, label in (("open", "Open result"), ("review", "Approve / Reject…"),
                           ("restore", "Restore"), ("queue", "Queue steps")):
            b = small_btn(jobs_hdr, label, lambda k=key: self._job_action(k))
            b.pack(side="left", padx=(8, 0))
            self._job_buttons[key] = b
        small_btn(jobs_hdr, "Empty trash", self._empty_trash).pack(side="right")
        self._jobs_tree = ttk.Treeview(jobs_frame, columns=("kind", "state", "what"), show="headings", height=5)
        for col, width in (("kind", 80), ("state", 130), ("what", 400)):
            self._jobs_tree.heading(col, text=col)
            self._jobs_tree.column(col, width=width, stretch=(col == "what"))
        self._jobs_tree.pack(fill="both", expand=True)
        self._jobs_tree.bind("<<TreeviewSelect>>", lambda e: self._update_job_buttons())
        self.log_pane.add(jobs_frame, stretch="always", minsize=120)
```

4. New methods in a `# ── Jobs pane ──` block:

```python
    def _jobs_store(self):
        return agent_jobs.JobStore(self.agent_policy.jobs_dir)

    def _selected_job(self):
        sel = self._jobs_tree.selection()
        if not sel:
            return None
        try:
            return self._jobs_store().get(sel[0])
        except KeyError:
            return None

    def _refresh_jobs(self):
        if self._launch_time is None:
            return  # not running
        store = self._jobs_store()
        selected = self._jobs_tree.selection()
        self._jobs_tree.delete(*self._jobs_tree.get_children())
        for job in store.list()[:50]:
            what = ", ".join(str(v) for v in job["args"].values())
            state = job["state"] + (f" ({job['note']})" if job.get("note") else "")
            self._jobs_tree.insert("", "end", iid=job["id"], values=(job["kind"], state, what))
        keep = [i for i in selected if self._jobs_tree.exists(i)]
        if keep:
            self._jobs_tree.selection_set(keep)
        self._update_job_buttons()
        self._jobs_after_id = self.master.after(2000, self._refresh_jobs)

    def _update_job_buttons(self):
        job = self._selected_job()
        store = self._jobs_store()
        allowed = set() if job is None else job_actions(
            job, store.has(job["id"], "result.md"), store.has(job["id"], "applied.json"),
            store.has(job["id"], "steps.json") and bool(store.read_json(job["id"], "steps.json")))
        for key, b in self._job_buttons.items():
            b.config(state=tk.NORMAL if key in allowed else tk.DISABLED)

    def _call_agents(self, tool, job_id):
        # The agents server owns job state; go through it so approvals can't race.
        def work():
            try:
                msg = agent_llm.mcp_call("agents", tool, {"job_id": job_id})
            except Exception as e:
                msg = f"The agents server didn't answer: {e}"
            self.master.after(0, lambda: messagebox.showinfo("Agent job", msg))
        threading.Thread(target=work, daemon=True).start()

    def _job_action(self, key):
        job = self._selected_job()
        if job is None:
            return
        if key == "open":
            os.startfile(self._jobs_store().path(job["id"], "result.md"))
        elif key == "review":
            self._review_plan(job)
        elif key == "restore":
            if messagebox.askyesno("Restore", "Put every file this job moved or removed back where it was?"):
                self._call_agents("restore", job["id"])
        elif key == "queue":
            self._call_agents("queue_steps", job["id"])

    def _review_plan(self, job):
        store = self._jobs_store()
        ops = store.read_json(job["id"], "plan.json")
        review = store.read_text(job["id"], "review.md") if store.has(job["id"], "review.md") else ""
        dlg = tk.Toplevel(self.master)
        dlg.title(f"Plan {job['id']}")
        dlg.configure(bg=BG)
        dlg.geometry("820x560")
        dlg.grab_set()
        text = tk.Text(dlg, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, relief="flat", wrap="none")
        sb = ttk.Scrollbar(dlg, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=sb.set)
        lines = [f"Goal: {job['args'].get('goal', '')}", f"Folder: {job['args'].get('folder', '')}", ""]
        for i, op in enumerate(ops, 1):
            if op["op"] == "move":
                lines.append(f"{i:>3}. move    {op['src']}\n          → {op['dst']}")
            elif op["op"] == "remove":
                lines.append(f"{i:>3}. remove  {op['path']}   (to trash, restorable)")
            else:
                lines.append(f"{i:>3}. mkdir   {op['path']}")
        lines += ["", "── Local review ──", review]
        text.insert("1.0", "\n".join(lines))
        text.config(state=tk.DISABLED)
        row = tk.Frame(dlg, bg=BG)
        row.pack(side="bottom", fill="x", padx=12, pady=12)
        sb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        flat_btn(row, f"Approve all {len(ops)}", lambda: (dlg.destroy(), self._call_agents("apply", job["id"]))).pack(side="left")
        small_btn(row, "Reject", lambda: (dlg.destroy(), self._call_agents("reject", job["id"]))).pack(side="left", padx=8)
        small_btn(row, "Close", dlg.destroy).pack(side="right")

    def _empty_trash(self):
        if messagebox.askyesno("Empty trash",
                               f"Permanently delete everything in {self.agent_policy.trash_dir}?\n"
                               "Jobs whose files are in there can no longer be restored."):
            n = agent_plans.empty_trash(self.agent_policy)
            messagebox.showinfo("Empty trash", f"Deleted {n} job folders from the trash.")
```

5. `run_commands`: after `self._start_elapsed()`, add `self._refresh_jobs()`.

6. `_exit_running_state`: after `self._stop_elapsed()`, add:

```python
        if hasattr(self, "_jobs_after_id"):
            self.master.after_cancel(self._jobs_after_id)
```

- [ ] **Step 4: Run all tests**

Run: `python -m unittest discover -s tests -v`
Expected: all pass.

- [ ] **Step 5: Headless pane check with a fake job** (no servers)

Save as a scratch file outside the repo (for example `%TEMP%\pane_check.py`) and run `python %TEMP%\pane_check.py C:\Users\choua\Documents\Git\llama_launch`:

```python
import importlib.machinery, importlib.util, os, sys, tempfile, time, tkinter as tk
repo = sys.argv[1]
sys.path.insert(0, repo)
from agents import jobs, paths
ldr = importlib.machinery.SourceFileLoader("launcher", os.path.join(repo, "launcher.pyw"))
spec = importlib.util.spec_from_loader("launcher", ldr); L = importlib.util.module_from_spec(spec); ldr.exec_module(L)
tmp = tempfile.mkdtemp()
root = tk.Tk(); root.withdraw()
app = L.CommandLauncherGUI(root)
app.agent_policy = paths.Policy(os.path.join(tmp, "sandbox"), folders_file=os.path.join(tmp, "f.json"))
app.agent_policy.ensure_dirs()
store = jobs.JobStore(app.agent_policy.jobs_dir)
job = store.create("organize", {"folder": tmp, "goal": "tidy"})
store.write_json(job["id"], "plan.json", [{"op": "remove", "path": os.path.join(tmp, "x.tmp")}])
store.write_text(job["id"], "review.md", "fine")
store.write_text(job["id"], "result.md", "summary")
store.update(job["id"], state="awaiting_approval")
app._launch_time = time.time()
app._refresh_jobs()
app._jobs_tree.selection_set(job["id"])
app._update_job_buttons()
print({k: str(b.cget("state")) for k, b in app._job_buttons.items()})
app._review_plan(store.get(job["id"]))
dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
text = next(w for w in dlg.winfo_children() if isinstance(w, tk.Text))
print("remove" in text.get("1.0", "end"))
root.destroy()
```

Expected: the first line prints `{'open': 'normal', 'review': 'normal', 'restore': 'disabled', 'queue': 'disabled'}`, then `True`. No traceback.

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw tests/test_launcher.py
git commit -m "launcher: agent Jobs pane with plan review, restore, queue and empty trash"
```

---

### Task 9: Docs and ignore rules

**Files:**
- Modify: `.gitignore`, `README.md`, `CLAUDE.md`
- Create: `.claude/rules/agents.md`

- [ ] **Step 1: `.gitignore`**: add the line `agent_folders.json` after `api_key.txt`.

- [ ] **Step 2: `README.md`**: add a new section just before `## API key`. Name it `## Agent jobs (research, planning, organising)`, because `## Agents` already covers the agent API. Content:

````markdown
## Agent jobs (research, planning, organising)

The chat model in the Web UI can start background jobs that run on your local model (ADR 0006):

- **research** a topic → a cited Markdown report
- **plan** a goal → a step-by-step plan, plus follow-up jobs you can queue from the Jobs pane
- **organize** a folder → a list of proposed moves, renames, new folders and removals, checked by a local review pass. **Nothing changes until you approve it.**

### Setup (once)

1. Launch the server. The launcher adds the `agents` MCP server to `config.json` automatically. The first launch takes a little longer while `uv` installs the `mcp` package.
2. In the Web UI's **MCP Servers** page, add `http://127.0.0.1:8001/servers/agents/mcp` and switch on **Use llama-server proxy**, as for the other servers.
3. In the launcher, open **⚙ Settings → Agent folders → Manage…** and add the local folders agents may read and organise. The chat model can also ask for a folder with `request_folder_access`; approving that tool call adds it.

Leave the Web UI's per-tool confirmation on for `apply` and `request_folder_access`. That confirmation is your approval.

### Where things go

- Agents write only to `Documents\agent-sandbox`. Each job has a folder in `agent-sandbox\jobs\` with its `result.md` (and `plan.json` / `review.md` for organise jobs).
- Removals move files to `agent-sandbox\trash\<job>\`. **Restore** in the Jobs pane puts a whole job back. **Empty trash** deletes the trash for good.
- Network drives are never read or changed.

### Jobs pane

The running view lists agent jobs. Select one to **Open result**, **Approve / Reject…** a plan (shows every operation and the review), **Restore** an applied plan, or **Queue steps** from a plan job. Jobs run one at a time and stop after 40 tool calls or 20 minutes. **Exit Processes** cancels running jobs.
````

- [ ] **Step 3: `.claude/rules/agents.md`**:

```markdown
---
paths: agents/**
---

# agents/

- **Only `server.py` may import `mcp`.** Everything else is standard library, because the launcher and tests import it without `uv` (ADR 0006).
- **mcp 2.x:** the class is `mcp.server.mcpserver.MCPServer`; `mcp.server.fastmcp` raises on import.
- **Jobs live inside the mcp-proxy child process.** It stays up between calls, so background threads work, and it dies with the UVX proxy. On start, `cancel_unfinished()` marks leftovers; the launcher does the same on Exit Processes.
- **Every path from the model goes through `Policy`.** `can_read` covers tools, `can_write` covers the sandbox minus `jobs\`/`trash\`, and `can_change` covers plan operations. `is_inside` uses `realpath`, so junctions can't escape. Job IDs are checked against the exact format before they become paths.
- **Approval is a state transition under a lock** (`awaiting_approval → applying`). The Jobs pane calls the server's `apply` tool instead of applying itself, so pane and chat can't both apply a plan.
- **`apply` re-validates the plan** right before running it; files may have changed since it was proposed.
- `mcp_call` posts one JSON-RPC `tools/call` without `initialize`. That only works because mcp-proxy runs with `--stateless`.
- Tool results are cut to 12,000 characters to protect the model's context.
```

- [ ] **Step 4: `CLAUDE.md`**: in Commands, add `- Agents MCP server alone: \`uv run --script agents/server.py\` (stdio; normally started by mcp-proxy from config.json).` In Conventions, change the first bullet to: `- Standard library only, except pystray + Pillow for the tray icon (\`_bootstrap()\` installs them) and \`mcp\` in \`agents/server.py\` (run through \`uv\`).`

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md CLAUDE.md .claude/rules/agents.md
git commit -m "docs: agent jobs setup, rules and ignore list"
```

---

### Task 10: Live end-to-end check and push

Needs the real ports. Ask the user to click **Exit Processes** and close the launcher first, then confirm ports 8080/8081/8001 are free with `Get-NetTCPConnection -LocalPort 8080,8081,8001 -State Listen`.

- [ ] **Step 1: Drive the real stack headlessly**

Save as a scratch file outside the repo (for example `%TEMP%\agents_e2e.py`) and run `set PYTHONIOENCODING=utf-8 && python -u %TEMP%\agents_e2e.py C:\Users\choua\Documents\Git\llama_launch` in the background with a 40-minute timeout:

```python
import importlib.machinery, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, threading, time, tkinter as tk
repo = sys.argv[1]
sys.path.insert(0, repo)
from agents import jobs, llm, paths
ldr = importlib.machinery.SourceFileLoader("launcher", os.path.join(repo, "launcher.pyw"))
spec = importlib.util.spec_from_loader("launcher", ldr); L = importlib.util.module_from_spec(spec); ldr.exec_module(L)

test_dir = os.path.join(tempfile.gettempdir(), "agent-e2e")
shutil.rmtree(test_dir, ignore_errors=True)
os.makedirs(test_dir)
for name in ("a.pdf", "b.pdf", "notes.txt", "junk.tmp"):
    open(os.path.join(test_dir, name), "w").write(name)

root = tk.Tk(); app = L.CommandLauncherGUI(root)
app._open_browser = False
app.model_var.set("Qwen_Qwen3-14B-Q5_K_M")
app.run_commands()
store = jobs.JobStore(app.agent_policy.jobs_dir)

def call(tool, **args):
    out = llm.mcp_call("agents", tool, args)
    print(f"> {tool}: {out[:600]}", flush=True)
    return out

def wait(job_id, done_states, minutes=15):
    end = time.time() + minutes * 60
    while time.time() < end:
        state = store.get(job_id)["state"]
        if state in done_states:
            return state
        time.sleep(5)
    return "timeout"

def checks():
    for _ in range(180):
        if "online" in app._health_label.cget("text"):
            break
        time.sleep(1)
    time.sleep(20)  # first launch: uv installs mcp for the agents server
    try:
        call("request_folder_access", path=test_dir)
        out = call("organize", folder=test_dir, goal="put the PDFs in a PDFs folder and remove .tmp files")
        job_id = re.search(r"\d{8}-\d{6}-[0-9a-f]{4}", out).group(0)
        state = wait(job_id, ("awaiting_approval", "failed"))
        print("organize ended as:", state, "| files untouched:", sorted(os.listdir(test_dir)), flush=True)
        call("job_result", job_id=job_id)
        if state == "awaiting_approval":
            call("apply", job_id=job_id)
            print("after apply:", sorted(os.listdir(test_dir)), flush=True)
            call("restore", job_id=job_id)
            print("after restore:", sorted(os.listdir(test_dir)), flush=True)
        out = call("research", topic="What is the latest llama.cpp release number? Cite the release page.")
        rid = re.search(r"\d{8}-\d{6}-[0-9a-f]{4}", out).group(0)
        print("research ended as:", wait(rid, ("done", "failed")), flush=True)
        report = store.read_text(rid, "result.md") if store.has(rid, "result.md") else ""
        print("report has a URL:", "http" in report, flush=True)
    finally:
        root.after(0, lambda: (app._kill_all(), app._stop_health_poll()))
        time.sleep(5)
        net = subprocess.run("netstat -ano", shell=True, capture_output=True, text=True).stdout
        print("ports still listening:", {p: L.listening_pids(net, p) for p in (8080, 8081, 8001)}, flush=True)
        print("unfinished jobs:", [j["id"] for j in store.list() if j["state"] in ("queued", "running")], flush=True)
        app.agent_policy.remove_folder(test_dir)
        shutil.rmtree(test_dir, ignore_errors=True)
        root.after(0, root.destroy)

threading.Thread(target=checks, daemon=True).start()
root.mainloop()
```

Expected: every step prints its outcome. "files untouched" lists all four original files. After apply, the PDFs are in a `PDFs` folder and `junk.tmp` is gone (it's in the trash). After restore, all four are back. No ports are left listening, and there are no unfinished jobs. A model-quality failure (no valid plan, or a weak report) is a finding to report, not a code bug. Code bugs (tracebacks, wrong state, files touched before approval) block the push.

- [ ] **Step 2: Run the full test suite one last time**

Run: `python -m unittest discover -s tests`
Expected: all pass.

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Report to the user** the results of each live step, the model's plan and review text, and the one-time Web UI setup steps from the README.
