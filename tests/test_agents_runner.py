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

    def test_old_tool_call_arguments_count_and_are_dropped_too(self):
        # A batched plan sits in the assistant's tool-call arguments, not in message content.
        chat = Script(*[call("echo", {"text": "x" * 10000}, f"c{i}") for i in range(8)], final("done"))
        runner.run_agent(chat, "s", "t", [self.echo_tool()])
        last = chat.seen[-1][0]
        self.assertEqual(last[2]["tool_calls"][0]["function"]["arguments"], runner.ELIDED_ARGS)
        json.loads(runner.ELIDED_ARGS)  # llama-server parses arguments as JSON
        self.assertIn("x" * 10000, last[-2]["tool_calls"][0]["function"]["arguments"])  # the newest call is kept

    def test_context_budget_follows_a_smaller_server_context(self):
        self.assertEqual(runner.context_chars(None), runner.CONTEXT_CHARS)
        self.assertEqual(runner.context_chars(262144), runner.CONTEXT_CHARS)  # never grows
        self.assertLess(runner.context_chars(8704), 8704 * 2)  # half the context, at 3 chars a token or fewer

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
        self.assertEqual(st("propose_plan", {"operations": []}, "added 4 operations; the plan now has 9"),
                         "Added 4 changes to the plan (9 in all)")
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


    def test_cancel_stops_before_the_next_model_call(self):
        reply = call("echo", {"text": "hi"})
        reply["content"] = "so far"
        chat = Script(reply, final("never asked"))
        # Cancelled while the model was answering: the tool call still runs, the next turn doesn't.
        text, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], cancelled=lambda: len(chat.seen) > 0)
        self.assertEqual((text, stop), ("so far", "cancelled"))
        self.assertEqual(len(chat.seen), 1)

    def test_cancel_also_skips_the_final_answer_after_a_cap(self):
        two = call("echo", {"text": "x"})
        two["tool_calls"].append({"id": "c2", "type": "function", "function": {"name": "echo", "arguments": "{}"}})
        chat = Script(two, final("never asked"))
        _, stop = runner.run_agent(chat, "s", "t", [self.echo_tool()], max_calls=1,
                                   cancelled=lambda: len(chat.seen) > 0)
        self.assertEqual(stop, "cancelled")
        self.assertEqual(len(chat.seen), 1)


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
        chat = Script(call("web_search", {"query": "llama"}), call("fetch_url", {"url": "https://x.test/a"}),
                      final("# Report\nSources: https://x.test/a"))
        runner.run_job(job, self.ctx(chat))
        got = self.store.get(job["id"])
        self.assertEqual((got["state"], got["note"]), ("done", ""))
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "# Report\nSources: https://x.test/a")
        self.assertIn("ddg-search/search", chat.seen[1][0][-1]["content"])

    def test_research_from_snippets_only_is_sent_back_to_read_its_sources(self):
        # Job 20261015-165707-2a6e: one search, then a report citing pages it never fetched.
        job = self.start("research", {"topic": "latest llama.cpp release"})
        chat = Script(call("web_search", {"query": "q"}),
                      final("v0.7.0 ([releases](https://github.com/ggml-org/llama.cpp/releases))"),
                      call("fetch_url", {"url": "https://github.com/ggml-org/llama.cpp/releases"}),
                      final("b11636 ([releases](https://github.com/ggml-org/llama.cpp/releases))"))
        runner.run_job(job, self.ctx(chat))
        nudge = chat.seen[2][0][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertIn("https://github.com/ggml-org/llama.cpp/releases", nudge["content"])
        self.assertIn("fetch_url", nudge["content"])
        got = self.store.get(job["id"])
        self.assertEqual((got["state"], got["note"]), ("done", ""))
        self.assertEqual(self.store.read_text(job["id"], "result.md"),
                         "b11636 ([releases](https://github.com/ggml-org/llama.cpp/releases))")

    def test_research_that_reads_nothing_is_sent_back_even_without_urls(self):
        job = self.start("research", {"topic": "t"})
        chat = Script(call("web_search", {"query": "q"}), final("# Report"),
                      call("fetch_url", {"url": "https://x.test/a"}), final("# Report\nhttps://x.test/a"))
        runner.run_job(job, self.ctx(chat))
        self.assertIn("fetch_url", chat.seen[2][0][-1]["content"])
        self.assertEqual(self.store.get(job["id"])["note"], "")

    def test_research_that_keeps_citing_unread_pages_is_flagged(self):
        job = self.start("research", {"topic": "t"})
        report = "# Report\nSee https://x.test/a and https://x.test/b."
        chat = Script(call("fetch_url", {"url": "https://x.test/a"}), *[final(report)] * 3)
        runner.run_job(job, self.ctx(chat))
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "done")
        self.assertIn("https://x.test/b", got["note"])
        self.assertNotIn("https://x.test/a", got["note"])
        result = self.store.read_text(job["id"], "result.md")
        self.assertTrue(result.startswith("> "), result)  # warning first, report kept below it
        self.assertIn("https://x.test/b", result.splitlines()[0])
        self.assertTrue(result.endswith(report))

    def test_research_citation_matching_ignores_punctuation_slash_and_fragment(self):
        job = self.start("research", {"topic": "t"})
        chat = Script(call("fetch_url", {"url": "HTTPS://X.test/a/"}),
                      final("(https://x.test/a), <https://x.test/a#notes>, https://x.test/a."))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["note"], "")

    def test_urls_in_code_are_not_citations(self):
        # A plan run on 2026-10-15 was sent back twice over `git clone https://github.com/ggml-org/llama.cpp.git`.
        text = ("Read [docs](https://x.test/a).\n```bash\ngit clone https://x.test/b.git\n```\n"
                "Run `curl https://x.test/c` then see https://x.test/d.\n~~~\nhttps://x.test/e\n~~~\n")
        self.assertEqual(runner.cited_urls(text), ["https://x.test/a", "https://x.test/d"])
        self.assertEqual(runner.cited_urls("```\nhttps://x.test/a\n(unclosed fence)"), [])

    def test_a_link_alone_in_backticks_is_a_citation(self):
        # Qwen3.6 and gpt-oss list sources as URL: `https://...` (model tests, 2026-10-16); that's a citation,
        # unlike a URL inside a command.
        text = "Sources:\n1. Qwen3 report. URL: `https://x.test/a`\n2. Run `curl https://x.test/c` to check.\n"
        self.assertEqual(runner.cited_urls(text), ["https://x.test/a"])

    def test_plan_with_a_command_url_is_not_sent_back(self):
        job = self.start("plan", {"goal": "g"})
        chat = Script(call("fetch_url", {"url": "https://x.test/a"}),
                      final("1. See https://x.test/a\n```\ngit clone https://x.test/repo.git\n```"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["note"], "")

    def test_research_failed_fetch_does_not_count_as_read(self):
        job = self.start("research", {"topic": "t"})

        def mcp_call(server, tool, args):
            raise RuntimeError("fetch failed: 404")
        ctx = runner.Context(self.policy, self.store, Script(
            call("fetch_url", {"url": "https://x.test/a"}), *[final("https://x.test/a")] * 3), mcp_call)
        runner.run_job(job, ctx)
        self.assertIn("https://x.test/a", self.store.get(job["id"])["note"])

    def test_job_budget_comes_from_the_server_context(self):
        # gemma-4-26B got n_ctx 8704 from --fit; a 40000-char history earned HTTP 400 (2026-10-16).
        job = self.start("plan", {"goal": "g"})  # a plan may finish without having read a page
        page = runner.tool("fetch_url", "Read", {"url": {"type": "string"}}, ["url"], lambda url: "p" * 6000)
        chat = Script(*[call("fetch_url", {"url": f"https://x.test/{i}"}, f"c{i}") for i in range(4)], final("r"))
        ctx = runner.Context(self.policy, self.store, chat, None, n_ctx=lambda: 8704)
        with mock.patch.object(runner, "web_tools", return_value=[page]):
            runner.run_job(job, ctx)
        self.assertEqual(chat.seen[-1][0][3]["content"], runner.ELIDED)

    def test_plan_keeps_earlier_steps_when_a_later_proposal_is_empty(self):
        # Ministral on 2026-10-16: "Saved 3 follow-up jobs", then "Saved 0 follow-up jobs" left steps.json [].
        job = self.start("plan", {"goal": "g"})
        steps = [{"kind": "research", "topic": "t"}]
        chat = Script(call("propose_steps", {"steps": steps}), call("propose_steps", {"steps": []}, "c2"),
                      call("propose_steps", {"steps": [{"kind": "hack"}]}, "c3"), final("1. do it"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.read_json(job["id"], "steps.json"), steps)
        for messages, _ in chat.seen[2:]:
            self.assertTrue(messages[-1]["content"].startswith("error:"), messages[-1]["content"])

    def test_one_follow_up_job_is_singular(self):
        # The panel showed "Saved 1 follow-up jobs" (2026-10-19).
        job = self.start("plan", {"goal": "g"})
        chat = Script(call("propose_steps", {"steps": [{"kind": "research", "topic": "t"}]}),
                      call("propose_steps", {"steps": []}, "c2"), final("1. do it"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(chat.seen[1][0][-1]["content"], "saved 1 follow-up job")
        self.assertEqual(chat.seen[2][0][-1]["content"],
                         "error: no valid follow-up jobs in that list; the 1 saved earlier is kept")

    def test_plan_saves_steps(self):
        job = self.start("plan", {"goal": "g"})
        steps = [{"kind": "research", "topic": "t"}, {"kind": "organize", "folder": self.dl, "goal": "tidy"},
                 {"kind": "hack", "x": 1}]
        chat = Script(call("propose_steps", {"steps": steps}), final("1. do it"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.read_json(job["id"], "steps.json"), steps[:2])
        self.assertEqual(self.store.get(job["id"])["state"], "done")

    def test_web_jobs_get_todays_date_and_organize_does_not(self):
        # Agent jobs bypass the proxy's date line; R1 on 2026-10-15 dated a release "October 15, 2023".
        with mock.patch.object(runner, "_today", return_value="Thursday, October 15, 2026"):
            for kind, args in (("research", {"topic": "t"}), ("plan", {"goal": "g"})):
                _, chat = self.read_attempt(kind, args, call("noop", {}))
                self.assertTrue(chat.seen[0][0][0]["content"].startswith(
                    "Current date: Thursday, October 15, 2026\n\n"), kind)
            job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
            chat = Script(final("nothing to do"))
            runner.run_job(job, self.ctx(chat))
            self.assertNotIn("Current date", chat.seen[0][0][0]["content"])

    def test_plan_citing_unread_pages_is_sent_back_to_read_them(self):
        job = self.start("plan", {"goal": "g"})
        chat = Script(call("web_search", {"query": "q"}), final("1. Use https://x.test/a"),
                      call("fetch_url", {"url": "https://x.test/a"}), final("1. Use https://x.test/a"))
        runner.run_job(job, self.ctx(chat))
        self.assertIn("https://x.test/a", chat.seen[2][0][-1]["content"])
        got = self.store.get(job["id"])
        self.assertEqual((got["state"], got["note"]), ("done", ""))
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "1. Use https://x.test/a")

    def test_plan_that_keeps_citing_unread_pages_is_flagged(self):
        job = self.start("plan", {"goal": "g"})
        runner.run_job(job, self.ctx(Script(*[final("1. Use https://x.test/a")] * 3)))
        self.assertIn("https://x.test/a", self.store.get(job["id"])["note"])
        self.assertTrue(self.store.read_text(job["id"], "result.md").startswith("> "))

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

    def test_organize_puts_mkdirs_first_when_the_model_writes_them_late(self):
        pdfs = os.path.join(self.dl, "PDFs")
        job_id = self.organize([{"op": "move", "src": os.path.join(self.dl, "a.pdf"), "dst": pdfs},
                                {"op": "mkdir", "path": pdfs}])
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        plan = self.store.read_json(job_id, "plan.json")
        self.assertEqual([o["op"] for o in plan], ["mkdir", "move"])
        self.assertEqual(plan[1]["dst"], os.path.join(pdfs, "a.pdf"))

    def test_organize_saves_removal_warnings_when_the_plan_is_proposed(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        warnings = self.store.read_json(job_id, "warnings.json")
        self.assertIn("Removes 1 item", warnings[0]["text"])

    def test_organize_waits_for_approval_with_review(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        self.assertEqual(self.store.get(job_id)["state"], "awaiting_approval")
        self.assertEqual(self.store.read_text(job_id, "review.md"), "looks fine")
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))  # nothing changed yet

    def test_a_big_plan_is_reviewed_in_chunks_that_fit_the_context(self):
        # gemma-4-26B got n_ctx 8704 from --fit; O4's 205-operation plan in one review prompt overflowed it.
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        ops = [{"op": "mkdir", "path": os.path.join(self.dl, f"Folder {i:03}")} for i in range(200)]
        chat = Script(call("propose_plan", {"operations": ops}), final("summary"),
                      *[final(f"review {i}") for i in range(20)])
        ctx = runner.Context(self.policy, self.store, chat, None, n_ctx=lambda: 8704)
        runner.run_job(job, ctx)
        self.assertEqual(self.store.get(job["id"])["state"], "awaiting_approval")
        reviews = [messages for messages, _ in chat.seen[2:]]
        self.assertGreater(len(reviews), 1)
        for messages in reviews:
            self.assertLessEqual(sum(len(m["content"]) for m in messages), runner.context_chars(8704))
        sent = "\n".join(messages[1]["content"] for messages in reviews)
        for n, op in enumerate(ops, 1):
            self.assertEqual(sent.count(f"\n{n}. {json.dumps(op)}"), 1, n)  # numbered across the whole plan
        review = self.store.read_text(job["id"], "review.md")
        for i in range(len(reviews)):
            self.assertIn(f"review {i}", review)

    def test_a_failed_review_still_waits_for_approval(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        ops = [{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}]
        chat = Script(call("propose_plan", {"operations": ops}), final("summary"), RuntimeError("HTTP 400"))
        runner.run_job(job, self.ctx(chat))
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "awaiting_approval")
        self.assertIn("HTTP 400", got["note"])
        self.assertEqual(self.store.read_json(job["id"], "plan.json"), ops)
        self.assertIn("HTTP 400", self.store.read_text(job["id"], "review.md"))

    def test_research_records_steps(self):
        job = self.start("research", {"topic": "llama"})
        runner.run_job(job, self.ctx(Script(call("web_search", {"query": "llama"}), final("# Report"),
                                            call("fetch_url", {"url": "https://x.test/a"}), final("# Report"))))
        count, last = self.store.read_steps(job["id"])
        self.assertEqual(last, ['Searched "llama"', "Sent back to read its sources", "Read x.test/a"])

    def test_organize_records_review_step(self):
        job_id = self.organize([{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}])
        _, last = self.store.read_steps(job_id)
        self.assertEqual(last[-2:], ["Added 1 changes to the plan (1 in all)", "Reviewed the plan"])

    def test_capped_job_records_stop(self):
        job = self.start("research", {"topic": "t"})
        replies = [call("web_search", {"query": "q"})] * 41 + [final("partial")]
        runner.run_job(job, self.ctx(Script(*replies)))
        _, last = self.store.read_steps(job["id"])
        self.assertTrue(last[-1].startswith("Stopped: stopped after 40 tool calls"))

    def run_cancelled(self, job, *replies):
        # Stop is pressed while the model answers its first turn.
        script = Script(*replies)

        def chat(messages, tools=None):
            reply = script(messages, tools)
            self.store.request_cancel(job["id"])
            return reply
        worker = jobs.Worker(self.store, lambda j: runner.run_job(j, self.ctx(chat)))
        worker.submit(job["id"])
        worker.queue.join()
        return script

    def test_cancelled_research_stops_at_its_next_turn_and_keeps_what_it_wrote(self):
        job = self.store.create("research", {"topic": "t"})
        reply = call("write_file", {"path": "notes.md", "content": "n"})
        reply["content"] = "draft"
        script = self.run_cancelled(job, reply, final("never asked"))
        self.assertEqual(len(script.seen), 1)
        got = self.store.get(job["id"])
        self.assertEqual(got["state"], "cancelled")
        self.assertTrue(got["note"])
        self.assertEqual(self.store.read_text(job["id"], "result.md"), "draft")
        self.assertTrue(os.path.exists(os.path.join(self.policy.sandbox, "notes.md")))

    def test_cancelled_organize_skips_the_review_and_cannot_be_approved(self):
        job = self.store.create("organize", {"folder": self.dl, "goal": "tidy"})
        ops = [{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}]
        script = self.run_cancelled(job, call("propose_plan", {"operations": ops}), final("never asked"))
        self.assertEqual(len(script.seen), 1)  # no review call either
        self.assertEqual(self.store.get(job["id"])["state"], "cancelled")
        self.assertIn("Not applied", runner.approve(job["id"], self.ctx(Script())))
        self.assertTrue(os.path.exists(os.path.join(self.dl, "a.pdf")))

    def test_organize_without_valid_plan_fails(self):
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        bad = [{"op": "remove", "path": "C:\\Windows"}]
        chat = Script(call("propose_plan", {"operations": bad}), final("gave up"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "failed")
        self.assertIn("not allowed", chat.seen[1][0][-1]["content"])

    def test_organize_adds_batches_to_one_plan(self):
        # O4 (about 200 files): one reply holding the whole plan ran past llm.chat's 900 s timeout (2026-10-16).
        with open(os.path.join(self.dl, "b.pdf"), "w") as f:
            f.write("x")
        pdfs = os.path.join(self.dl, "PDFs")
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        first = [{"op": "mkdir", "path": pdfs}, {"op": "move", "src": os.path.join(self.dl, "a.pdf"), "dst": pdfs}]
        second = [{"op": "move", "src": os.path.join(self.dl, "b.pdf"), "dst": pdfs}]  # into the earlier mkdir
        chat = Script(call("propose_plan", {"operations": first}), call("propose_plan", {"operations": second}, "c2"),
                      final("summary"), final("looks fine"))
        runner.run_job(job, self.ctx(chat))
        self.assertEqual(self.store.get(job["id"])["state"], "awaiting_approval")
        self.assertEqual(self.store.read_json(job["id"], "plan.json"), [
            {"op": "mkdir", "path": pdfs},
            {"op": "move", "src": os.path.join(self.dl, "a.pdf"), "dst": os.path.join(pdfs, "a.pdf")},
            {"op": "move", "src": os.path.join(self.dl, "b.pdf"), "dst": os.path.join(pdfs, "b.pdf")}])
        self.assertIn("now has 3", chat.seen[2][0][-1]["content"])

    def test_a_rejected_batch_keeps_earlier_batches_for_a_retry(self):
        with open(os.path.join(self.dl, "b.pdf"), "w") as f:
            f.write("x")
        job = self.start("organize", {"folder": self.dl, "goal": "tidy"})
        first = [{"op": "remove", "path": os.path.join(self.dl, "a.pdf")}]
        fixed = [{"op": "remove", "path": os.path.join(self.dl, "b.pdf")}]
        chat = Script(call("propose_plan", {"operations": first}),
                      call("propose_plan", {"operations": [{"op": "remove", "path": "C:\\Windows"}]}, "c2"),
                      call("propose_plan", {"operations": fixed}, "c3"), final("summary"), final("looks fine"))
        runner.run_job(job, self.ctx(chat))
        self.assertIn("still has 1", chat.seen[2][0][-1]["content"])
        self.assertEqual(self.store.read_json(job["id"], "plan.json"), first + fixed)

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
        chat = Script(first_call, *[final("ok")] * (1 + runner.MAX_CHECKS))  # research sends unsourced answers back
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
