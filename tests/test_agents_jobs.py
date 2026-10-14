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
