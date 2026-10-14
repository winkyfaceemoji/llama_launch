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
