import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))
import report  # noqa: E402


def run(model, agents, unsafe=False, wrong=None, reasons=None):
    # Shaped like what model_test.main() writes.
    return {"kind": "model_test", "started": "2026-10-15 20:00:00", "finished": "2026-10-15 21:00:00",
            "model": model, "build": "b9776",
            "areas": {"organise": {"percent": agents, "cases": [["O1", True, "score 1.0"], ["O2", False, "score 0.5"]]}},
            "agents_score": agents,
            "kinds": {"maths": {"percent": 50.0, "questions": [["Q1", True, "29"], ["Q2", False, "30"]]}},
            "reasoning_score": 50.0, "wrong": wrong or [],
            "speed": {"predicted_per_second": 42.5, "prompt_per_second": 900.0}, "context": 32768,
            "unsafe": unsafe, "unsafe_reasons": reasons or []}


class RenderTests(unittest.TestCase):
    def test_repeat_runs_of_a_model_are_averaged_with_their_range(self):
        # Qwen3.6 scored 69, 71 and 78 on agents in three runs (2026-10-16): one run can't rank models.
        runs = [dict(run("q.gguf", a), test_version=2) for a in (69.0, 71.0, 78.0)]
        page = report.render(runs)
        self.assertEqual(page.count("q.gguf"), 4)  # the model row plus one row per run inside it
        self.assertIn("72.7 (69.0–78.0)", page)
        self.assertIn("3 runs", page)

    def test_older_test_versions_are_listed_apart(self):
        runs = [dict(run("q.gguf", 90.0), test_version=2), run("q.gguf", 40.0)]  # no version: before 2026-10-17
        page = report.render(runs)
        self.assertIn("Older test versions", page)
        self.assertNotIn("65.0", page)  # never averaged across versions
        self.assertLess(page.index("90.0"), page.index("Older test versions"))
        self.assertGreater(page.index("40.0"), page.index("Older test versions"))

    def test_an_unsafe_run_marks_its_model(self):
        runs = [dict(run("q.gguf", 90.0), test_version=2), dict(run("q.gguf", 80.0, unsafe=True), test_version=2)]
        page = report.render(runs)
        self.assertLess(page.index('class="unsafe"'), page.index("2 runs") + 200)

    def test_rows_sorted_by_agents_score(self):
        page = report.render([run("low.gguf", 40.0), run("high.gguf", 90.0)])
        self.assertLess(page.index("high.gguf"), page.index("low.gguf"))

    def test_unsafe_row_marked(self):
        page = report.render([run("safe.gguf", 80.0)])
        self.assertNotIn('class="unsafe"', page)
        page = report.render([run("bad.gguf", 80.0, unsafe=True, reasons=["O1: files changed before approval"])])
        self.assertIn('class="unsafe"', page)
        self.assertIn("O1: files changed before approval", page)

    def test_strings_escaped(self):
        bad = "<script>alert(1)</script>"
        wrong = [{"kind": "maths", "q": bad, "expected": {"exact": bad}, "given": bad, "cut_off": False}]
        page = report.render([run(bad, 50.0, wrong=wrong, reasons=[bad])])
        self.assertNotIn("<script", page)
        self.assertIn("&lt;script&gt;", page)

    def test_cut_off_labelled(self):
        wrong = [{"kind": "maths", "q": "What is 2+2?", "expected": {"exact": "4"}, "given": "Let me", "cut_off": True},
                 {"kind": "maths", "q": "What is 3+3?", "expected": {"exact": "6"}, "given": "7", "cut_off": False}]
        page = report.render([run("m.gguf", 50.0, wrong=wrong)])
        self.assertEqual(page.count("cut off at the token cap"), 1)

    def test_empty_answer_labelled_no_answer(self):
        # gpt-oss ended without a final answer (2026-10-16); that's not the same as answering wrong.
        wrong = [{"kind": "dates", "q": "Which weekday?", "expected": {"exact": "thursday"}, "given": "", "cut_off": False},
                 {"kind": "dates", "q": "Which date?", "expected": {"exact": "x"}, "given": "y", "cut_off": False}]
        page = report.render([run("m.gguf", 50.0, wrong=wrong)])
        self.assertEqual(page.count("no answer"), 1)


class BuildTests(unittest.TestCase):
    def test_reads_only_model_test_results(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("a.json", run("good.gguf", 70.0)),
                               ("b.json", {"started": "x", "model": "stress.gguf", "cases": {}})):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    json.dump(data, f)
            with open(os.path.join(d, "c.json"), "w", encoding="utf-8") as f:
                f.write("{not json")
            path = report.build(d)
            self.assertEqual(path, os.path.join(d, "report.html"))
            with open(path, encoding="utf-8") as f:
                page = f.read()
        self.assertIn("good.gguf", page)
        self.assertNotIn("stress.gguf", page)


if __name__ == "__main__":
    unittest.main()
