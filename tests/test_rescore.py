import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "eval"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import model_test  # noqa: E402
import rescore  # noqa: E402
from test_model_test import results  # noqa: E402  (stress results shaped like a real run)

QUEUE = "Queue the follow-up steps from agent job 20261015-021209-071b."


def saved(stress):
    # A version 1 model test result as main() wrote it before 2026-10-17.
    dice = next(i for i, q in enumerate(model_test.load_questions(), 1) if "dice" in q["q"])
    return {"kind": "model_test", "started": "2026-10-16 22:30:52", "model": "m.gguf", "build": "b11680",
            "stress": stress, "agents_score": 0.0, "areas": {},
            "kinds": {"maths": {"percent": 50.0, "questions": [[f"Q{dice}", False, r"\(\frac{1}{9}\)"], ["Q1", True, "29"]]}},
            "reasoning_score": 50.0, "unsafe": False, "unsafe_reasons": [],
            "wrong": [{"kind": "maths", "q": model_test.load_questions()[dice - 1]["q"],
                       "expected": {}, "given": r"\(\frac{1}{9}\)", "cut_off": False}]}


class RescoreTests(unittest.TestCase):
    def setUp(self):
        stress = results()
        stress["tool_choice"] = [{"kind": "explicit", "prompt": QUEUE, "expected": ["queue_steps"],
                                  "calls": ["job_result"], "ok": False},
                                 {"kind": "distractor", "prompt": "Write a haiku about autumn.", "expected": [],
                                  "calls": [], "ok": True}]
        self.old = saved(stress)
        self.before = copy.deepcopy(self.old)
        self.new = rescore.rescore(self.old, "20261016-223049.json")

    def test_version_2_rules_apply(self):
        self.assertEqual(self.new["test_version"], model_test.TEST_VERSION)
        self.assertEqual(self.new["rescored_from"], "20261016-223049.json")
        tool = {c: ok for c, ok, _ in self.new["areas"]["tool_choice"]["cases"]}
        self.assertTrue(all(tool.values()), tool)  # looking the job up first now counts

    def test_reasoning_answers_are_rechecked(self):
        self.assertEqual(self.new["kinds"]["maths"]["percent"], 100.0)  # the LaTeX 1/9 now reads as 1/9
        self.assertEqual(self.new["reasoning_score"], 100.0)
        self.assertEqual(self.new["wrong"], [])

    def test_the_original_is_left_alone(self):
        self.assertEqual(self.old, self.before)

    def test_results_without_raw_output_cannot_be_rescored(self):
        old = saved(None)
        del old["stress"]
        self.assertIsNone(rescore.rescore(old, "x.json"))


if __name__ == "__main__":
    unittest.main()
