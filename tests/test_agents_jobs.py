import json
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
        # A real job file sits outside jobs\; a traversal ID must not reach it.
        secret = os.path.join(self.tmp.name, "secret")
        os.makedirs(secret)
        with open(os.path.join(secret, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "secret", "kind": "research", "args": {}, "state": "done",
                       "created": 0, "updated": 0, "note": ""}, f)
        for bad in (r"..\..\secret", r"..\secret", "../secret", "20261014-000000-zzzz", "", "a/b"):
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

    def test_cancel_unfinished_fails_jobs_caught_applying_or_restoring(self):
        a = self.store.create("organize", {})
        b = self.store.create("organize", {})
        self.store.update(a["id"], state="applying")
        self.store.update(b["id"], state="restoring")
        self.store.cancel_unfinished()
        for job in (a, b):
            got = self.store.get(job["id"])
            self.assertEqual(got["state"], "failed")
            self.assertIn("use Restore to undo what ran", got["note"])

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

    def test_update_survives_reader_holding_job_file(self):
        # Windows refuses os.replace while another handle has job.json open.
        job = self.store.create("research", {})
        path = self.store.path(job["id"], "job.json")
        held = threading.Event()

        def hold():
            with open(path, encoding="utf-8"):
                held.set()
                time.sleep(0.3)

        reader = threading.Thread(target=hold)
        reader.start()
        held.wait()
        try:
            self.store.update(job["id"], state="running")
        finally:
            reader.join()
        self.assertEqual(self.store.get(job["id"])["state"], "running")

    def test_list_tolerates_record_without_created(self):
        job = self.store.create("research", {})
        os.makedirs(self.store.path("20261014-000000-abcd"))
        self.store.write_json("20261014-000000-abcd", "job.json",
                              {"id": "20261014-000000-abcd", "state": "done"})
        self.assertIn(job["id"], [j["id"] for j in self.store.list()])


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

    def _drain(self, worker):
        # Bounded join: a worker that dies leaves queue.join() blocked forever.
        t = threading.Thread(target=worker.queue.join, daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "worker did not drain the queue")

    def test_run_failure_does_not_stop_later_jobs(self):
        done = []

        def run(job):
            if job["args"].get("fail"):
                raise RuntimeError("boom")
            done.append(job["id"])
            self.store.update(job["id"], state="done")

        worker = jobs.Worker(self.store, run)
        bad = self.store.create("research", {"fail": True})["id"]
        good = self.store.create("research", {})["id"]
        worker.submit(bad)
        worker.submit(good)
        self._drain(worker)
        self.assertEqual(self.store.get(bad)["state"], "failed")
        self.assertEqual(done, [good])

    def test_store_error_does_not_stop_later_jobs(self):
        done = []
        real_transition = self.store.transition
        bad = self.store.create("research", {})["id"]
        good = self.store.create("research", {})["id"]

        def flaky(job_id, expected, new_state, **fields):
            if job_id == bad:
                raise OSError("disk gone")
            return real_transition(job_id, expected, new_state, **fields)

        self.store.transition = flaky
        worker = jobs.Worker(self.store, lambda job: done.append(job["id"]))
        worker.submit(bad)
        worker.submit(good)
        self._drain(worker)
        self.assertEqual(done, [good])


if __name__ == "__main__":
    unittest.main()
