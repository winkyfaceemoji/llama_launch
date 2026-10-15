import itertools
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

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
        self.counter = itertools.count()

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
        self.store.update(r["id"], state="done")
        self.store.update(o["id"], state="done")
        self.assertEqual(len(panel_api.jobs_payload(self.store, limit=1)), 1)

    def make(self, kind, args, state, created):
        # Job ids are second-resolution plus 4 random hex digits; 60 jobs in a test would sometimes collide.
        with mock.patch.object(jobs.secrets, "token_hex", side_effect=lambda n: f"{next(self.counter):04x}"):
            job = self.store.create(kind, args)
        return self.store.update(job["id"], state=state, created=created)

    def test_payload_keeps_awaiting_and_active_jobs_past_the_limit(self):
        awaiting = self.make("organize", {"folder": self.dl, "goal": "old"}, "awaiting_approval", 1)
        running = self.make("research", {"topic": "slow"}, "running", 2)
        done = [self.make("research", {"topic": f"t{i}"}, "done", 100 + i)["id"] for i in range(60)]
        ids = [j["id"] for j in panel_api.jobs_payload(self.store, limit=50)]
        self.assertEqual(len(ids), 50)
        self.assertIn(awaiting["id"], ids)
        self.assertIn(running["id"], ids)
        self.assertEqual(ids[:48], done[::-1][:48])  # the 48 newest others, newest first
        self.assertEqual(ids[48:], [running["id"], awaiting["id"]])

    def test_payload_with_only_pinned_jobs_beyond_limit_returns_them_all(self):
        for i in range(3):
            self.make("research", {"topic": f"r{i}"}, "running", i)
        self.assertEqual(len(panel_api.jobs_payload(self.store, limit=2)), 3)

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
        seen.clear()
        orig_restore = plans.restore

        def spy_restore(*a, **k):
            seen.append(runner.FILE_MOVES.locked())
            return orig_restore(*a, **k)

        plans.restore = spy_restore
        try:
            runner.undo(job["id"], self.ctx)
        finally:
            plans.restore = orig_restore
        self.assertEqual(seen, [True])
        self.assertFalse(runner.FILE_MOVES.locked())
        self.assertTrue(os.path.exists(a))


if __name__ == "__main__":
    unittest.main()
