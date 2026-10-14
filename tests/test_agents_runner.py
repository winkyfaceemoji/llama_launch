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
