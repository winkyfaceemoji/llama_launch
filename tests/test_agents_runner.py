import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


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

    def test_old_tool_results_are_dropped_to_stay_in_budget(self):
        big = runner.tool("big", "Big", {}, [], lambda: "x" * 10000)
        chat = Script(*([call("big", {})] * 20), final("done"))
        text, stop = runner.run_agent(chat, "s", "t", [big])
        self.assertEqual((text, stop), ("done", ""))
        limit = runner.CONTEXT_CHARS + runner.MAX_RESULT_CHARS
        for messages, _ in chat.seen:
            self.assertLessEqual(sum(len(m["content"]) for m in messages), limit)
        last = chat.seen[-1][0]
        self.assertEqual(last[3]["role"], "tool")
        self.assertEqual(last[3]["content"], runner.ELIDED)
        self.assertNotEqual(last[-1]["content"], runner.ELIDED)  # the newest result is kept

    def test_model_error_after_first_turn_returns_what_it_has(self):
        reply = call("echo", {"text": "x"})
        reply["content"] = "working on it"
        chat = Script(reply, RuntimeError("HTTP 400: context exceeded"))
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()])
        self.assertEqual(text, "working on it")
        self.assertTrue(stop.startswith("stopped: model error"), stop)
        self.assertIn("context exceeded", stop)

    def test_network_errors_after_first_turn_also_stop_cleanly(self):
        for error in (TimeoutError("timed out"), ConnectionResetError("reset"), OSError("URLError")):
            reply = call("echo", {"text": "x"})
            reply["content"] = "notes so far"
            text, stop = runner.run_agent(Script(reply, error), "s", "t", [self.echo_tool()])
            self.assertEqual(text, "notes so far")
            self.assertTrue(stop.startswith("stopped: model error"), stop)

    def test_network_error_on_final_answer_after_cap(self):
        chat = Script(call("echo", {"text": "x"}), call("echo", {"text": "x"}), TimeoutError("timed out"))
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], max_calls=1)
        self.assertTrue(stop.startswith("stopped: model error"), stop)

    def test_tool_results_are_capped_at_6000_characters(self):
        self.assertLessEqual(runner.MAX_RESULT_CHARS, 6000)
        big = runner.tool("big", "Big", {}, [], lambda: "x" * 10000)
        chat = Script(call("big", {}), final("done"))
        runner.run_agent(chat, "s", "t", [big])
        self.assertLessEqual(len(chat.seen[1][0][-1]["content"]), 6000)

    def test_model_error_on_first_turn_propagates(self):
        with self.assertRaises(RuntimeError):
            runner.run_agent(Script(RuntimeError("down")), "s", "t", [self.echo_tool()])

    def test_model_error_on_final_answer_after_cap(self):
        chat = Script(call("echo", {"text": "x"}), call("echo", {"text": "x"}), RuntimeError("boom"))
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], max_calls=1)
        self.assertEqual(text, "")
        self.assertTrue(stop.startswith("stopped: model error"), stop)

    def test_parallel_calls_never_exceed_cap(self):
        ran = []
        counting = runner.tool("count", "Count", {}, [], lambda: ran.append(1) or "ok")
        three = {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "count", "arguments": "{}"}} for i in range(3)]}
        chat = Script(three, final("wrapped up"))
        text, stop = runner.run_agent(chat, "s", "t", [counting], max_calls=2)
        self.assertEqual(len(ran), 2)
        self.assertEqual(text, "wrapped up")
        self.assertIn("2 tool calls", stop)
        results = [m["content"] for m in chat.seen[-1][0] if m["role"] == "tool"]
        self.assertEqual(results, ["ok", "ok", "Limit reached."])

    def test_malformed_tool_call_shapes_return_error(self):
        bad = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": None}, {"id": "b"}, {"id": "c", "function": {"name": 7, "arguments": "{}"}},
            {"id": "d", "function": {"name": ["echo"], "arguments": "{}"}}]}
        chat = Script(bad, final("ok"))
        text, _ = runner.run_agent(chat, "s", "t", [self.echo_tool()])
        self.assertEqual(text, "ok")
        results = [m["content"] for m in chat.seen[1][0] if m["role"] == "tool"]
        self.assertEqual(results, ["error: malformed tool call"] * 4)

    def test_empty_final_answer_falls_back_to_earlier_text(self):
        first = call("echo", {"text": "x"})
        first["content"] = "draft findings"
        text, stop = runner.run_agent(Script(first, final("")), "s", "t", [self.echo_tool()])
        self.assertEqual(text, "draft findings")
        self.assertEqual(stop, "")

    def test_no_final_answer_is_noted(self):
        text, stop = runner.run_agent(Script(final("")), "s", "t", [self.echo_tool()])
        self.assertEqual(text, "")
        self.assertIn("the model gave no final answer", stop)

    def test_no_final_answer_after_cap_is_noted(self):
        chat = Script(call("echo", {"text": "x"}), call("echo", {"text": "x"}), final(""))
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], max_calls=1)
        self.assertEqual(text, "")
        self.assertIn("1 tool calls", stop)
        self.assertIn("the model gave no final answer", stop)

    def test_step_text_per_tool(self):
        st = runner.step_text
        self.assertEqual(st("web_search", {"query": "llama.cpp"}, "results"), 'Searched "llama.cpp"')
        self.assertEqual(st("fetch_url", {"url": "https://github.com/ggml-org/llama.cpp/releases?x=1"}, "page"),
                         "Read github.com/ggml-org/llama.cpp/releases")
        self.assertEqual(st("fetch_url", {"url": "https://u:p@host.example:8443/x?q=1"}, "page"), "Read host.example:8443/x")
        self.assertEqual(st("fetch_url", {"url": "https://user:secret@host.example/a"}, "page"), "Read host.example/a")
        self.assertEqual(st("fetch_url", {"url": "http://[::1"}, "page"), "Read a web page")
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

    def test_organize_expands_a_folder_destination_to_the_file_path(self):
        # Small models write "move a.pdf into PDFs" as dst=...\PDFs; the saved plan must say ...\PDFs\a.pdf.
        pdfs = os.path.join(self.dl, "PDFs")
        job_id = self.organize([{"op": "mkdir", "path": pdfs},
                                {"op": "move", "src": os.path.join(self.dl, "a.pdf"), "dst": pdfs}])
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        self.assertEqual(self.store.read_json(job_id, "plan.json")[1]["dst"], os.path.join(pdfs, "a.pdf"))

    def test_organize_expands_an_existing_folder_destination(self):
        old = os.path.join(self.dl, "Old")
        os.makedirs(old)
        job_id = self.organize([{"op": "move", "src": os.path.join(self.dl, "a.pdf"), "dst": old + "\\"}])
        self.assertEqual(self.store.read_json(job_id, "plan.json")[0]["dst"], os.path.join(old, "a.pdf"))

    def test_organize_waits_for_approval_with_review(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        self.assertEqual(self.store.read_text(job_id, "review.md"), "looks fine")
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))  # nothing changed yet

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

    def test_organize_without_valid_plan_fails(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        bad = [{"op": "remove", "path": "C:\\Windows"}]
        chat = Script(call("propose_plan", {"operations": bad}), final("gave up"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")
        self.assertIn("not allowed", chat.seen[1][0][-1]["content"])

    def test_rejected_later_proposal_clears_the_saved_plan(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        good = [{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}]
        bad = [{"op": "remove", "path": "C:\\Windows"}]
        chat = Script(call("propose_plan", {"operations": good}), call("propose_plan", {"operations": bad}),
                      final("removed C:\\Windows"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")
        self.assertFalse(self.store.has(job["id"], "plan.json"))

    def other_folder(self):
        other = os.path.join(self.tmp.name, "Other")
        os.makedirs(other)
        with open(os.path.join(other, "keep.txt"), "w") as f:
            f.write("x")
        self.policy.add_folder(other)
        return os.path.join(other, "keep.txt")

    def test_plan_outside_the_job_folder_is_refused(self):
        keep = self.other_folder()
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        chat = Script(call("propose_plan", {"operations": [{"op": "remove", "path": keep}]}), final("gave up"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")
        self.assertIn("outside the job's folder", chat.seen[1][0][-1]["content"])

    def test_approve_rechecks_the_job_folder(self):
        keep = self.other_folder()
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        self.store.write_json(job_id, "plan.json", [{"op": "remove", "path": keep}])
        out = runner.approve(job_id, self.ctx(Script()))
        self.assertIn("Not applied", out)
        self.assertIn("outside the job's folder", out)
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        self.assertTrue(os.path.exists(keep))

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

    def test_model_error_mid_research_still_writes_result(self):
        job = self.start("research", {"topic": "llama"})
        reply = call("web_search", {"query": "llama"})
        reply["content"] = "partial notes"
        runner.run_job(job, self.ctx(Script(reply, RuntimeError("HTTP 400: context exceeded"))))
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "done")
        self.assertTrue(got["note"].startswith("stopped: model error"), got["note"])
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "partial notes")

    def test_network_error_mid_research_still_writes_result(self):
        job = self.start("research", {"topic": "llama"})
        reply = call("web_search", {"query": "llama"})
        reply["content"] = "partial notes"
        runner.run_job(job, self.ctx(Script(reply, TimeoutError("timed out"))))
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "done")
        self.assertTrue(got["note"].startswith("stopped: model error"), got["note"])
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "partial notes")

    def test_model_error_mid_organize_without_plan_stays_failed(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        runner.run_job(job, self.ctx(Script(call("list_dir", {"path": self.dl}), RuntimeError("down"))))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")

    def read_attempt(self, kind, args, first_call):
        job = self.start(kind, args)
        chat = Script(first_call, final("ok"))
        runner.run_job(job, self.ctx(chat))
        return job, chat

    def test_research_and_plan_cannot_read_listed_folders(self):
        target = os.path.join(self.dl, "a.pdf")
        for kind, args in (("research", {"topic": "t"}), ("plan", {"goal": "g"})):
            for name, params in (("read_file", {"path": target}), ("list_dir", {"path": self.dl})):
                _, chat = self.read_attempt(kind, args, call(name, params))
                self.assertTrue(chat.seen[1][0][-1]["content"].startswith("error:"), (kind, name))
            names = {t["function"]["name"] for t in chat.seen[0][1]}
            self.assertLessEqual({"web_search", "fetch_url", "read_file"}, names)

    def test_research_can_read_its_own_sandbox_files_but_not_job_records(self):
        with open(os.path.join(self.policy.sandbox, "notes.md"), "w") as f:
            f.write("my notes")
        _, chat = self.read_attempt("research", {"topic": "t"}, call("read_file", {"path": "notes.md"}))
        self.assertEqual(chat.seen[1][0][-1]["content"], "my notes")
        job, _ = self.read_attempt("research", {"topic": "t"}, call("noop", {}))
        record = os.path.join(self.policy.jobs_dir, job["id"], "job.json")
        _, chat = self.read_attempt("research", {"topic": "t"}, call("read_file", {"path": record}))
        self.assertTrue(chat.seen[1][0][-1]["content"].startswith("error:"))

    def test_organize_has_no_web_tools_and_reads_listed_folders(self):
        _, chat = self.read_attempt("organize", {"folder": self.dl, "goal": "g"},
                                    call("list_dir", {"path": self.dl}))
        names = {t["function"]["name"] for t in chat.seen[0][1]}
        self.assertEqual(names, {"list_dir", "read_file", "propose_plan"})
        self.assertIn("a.pdf", chat.seen[1][0][-1]["content"])

    def test_relative_paths_resolve_against_sandbox(self):
        fns = {t[0]: t[2] for t in runner.file_tools(self.policy)}
        fns["write_file"]("notes/x.md", "hello")
        self.assertEqual(fns["read_file"]("notes/x.md"), "hello")
        self.assertIn("x.md", fns["list_dir"]("notes"))

    def test_read_file_truncation_is_marked_and_bounded(self):
        fns = {t[0]: t[2] for t in runner.file_tools(self.policy)}
        fns["write_file"]("long.txt", "y" * 20000)
        fns["write_file"]("short.txt", "z" * 100)
        out = fns["read_file"]("long.txt")
        self.assertLessEqual(len(out), runner.MAX_RESULT_CHARS)
        self.assertTrue(out.endswith("[truncated]"))
        self.assertEqual(fns["read_file"]("short.txt"), "z" * 100)

    def test_colon_paths_are_refused_by_file_tools(self):
        fns = {t[0]: t[2] for t in runner.file_tools(self.policy)}
        self.assertTrue(fns["write_file"]("jobs:evil", "x").startswith("error:"))

    def test_crash_mid_apply_is_failed_and_restorable(self):
        with open(os.path.join(self.dl, "b.pdf"), "w") as f:
            f.write("y")
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")},
                                {"op": "remove", "path": os.path.join(self.dl, "b.pdf")}])
        a, b = os.path.join(self.dl, "a.pdf"), os.path.join(self.dl, "b.pdf")
        real = self.policy.can_change

        def flaky(path):
            if path == b and not os.path.exists(a):
                raise PermissionError("folder list is being saved")
            return real(path)

        ctx = self.ctx(Script())
        with mock.patch.object(self.policy, "can_change", flaky):
            runner.approve(job_id, ctx)
        self.assertEqual(self.store.get(job_id)["state"], "failed")
        self.assertEqual(len(self.store.read_json(job_id, "applied.json")), 1)
        self.assertIn("Restored", runner.undo(job_id, ctx))
        self.assertTrue(os.path.exists(a))
        self.assertEqual(self.store.get(job_id)["state"], "restored")

    def test_apply_progress_is_saved_after_each_step(self):
        # A hard crash (process killed) leaves no chance to write the log afterwards, so approve must
        # have it written step by step. Simulate the crash right after step 1 completes.
        class Crash(BaseException):
            pass

        with open(os.path.join(self.dl, "b.pdf"), "w") as f:
            f.write("y")
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")},
                                {"op": "remove", "path": os.path.join(self.dl, "b.pdf")}])
        real = runner.plans.apply_plan

        def crashing(ops, jid, policy, on_step=None):
            def stepper(log):
                if on_step:
                    on_step(log)
                raise Crash()
            return real(ops, jid, policy, on_step=stepper)

        ctx = self.ctx(Script())
        with mock.patch.object(runner.plans, "apply_plan", crashing):
            with self.assertRaises(Crash):
                runner.approve(job_id, ctx)
        self.assertEqual(len(self.store.read_json(job_id, "applied.json")), 1)
        self.store.cancel_unfinished()  # what the next start does to a job left in "applying"
        self.assertEqual(self.store.get(job_id)["state"], "failed")
        self.assertIn("Restored", runner.undo(job_id, ctx))
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))

    def test_approve_that_cannot_start_returns_to_awaiting_approval(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        self.store.write_text(job_id, "plan.json", "{not json")
        out = runner.approve(job_id, self.ctx(Script()))
        self.assertIn("Not applied", out)
        got = self.store.get(job_id)
        self.assertEqual(got["state"], "awaiting_approval")
        self.assertTrue(got["note"])
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))

    def test_undo_crash_marks_failed_and_can_retry(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        ctx = self.ctx(Script())
        runner.approve(job_id, ctx)
        good = self.store.read_json(job_id, "applied.json")
        self.store.write_json(job_id, "applied.json", [{"op": "move"}])  # no "from"
        runner.undo(job_id, ctx)
        got = self.store.get(job_id)
        self.assertEqual(got["state"], "failed")
        self.assertTrue(got["note"].startswith("restore failed"), got["note"])
        self.assertTrue(got["note"].endswith("try Restore again"))
        self.store.write_json(job_id, "applied.json", good)
        self.assertIn("Restored", runner.undo(job_id, ctx))
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))


if __name__ == "__main__":
    unittest.main()
