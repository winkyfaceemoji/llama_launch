import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import prompts

KINDS = ("research", "plan", "organize", "review")


class EffectiveTests(unittest.TestCase):
    def test_no_edits_means_the_shipped_prompts(self):
        self.assertEqual(prompts.effective({}), prompts.SHIPPED)
        self.assertEqual(set(prompts.SHIPPED), set(KINDS))

    def test_an_edited_prompt_replaces_only_its_own_kind(self):
        got = prompts.effective({"agent_prompts": {"research": "Cite everything you read."}})
        self.assertEqual(got["research"], "Cite everything you read.")
        for kind in ("plan", "organize", "review"):
            self.assertEqual(got[kind], prompts.SHIPPED[kind])


class CheckTests(unittest.TestCase):
    def test_shipped_prompts_pass(self):
        for kind, text in prompts.SHIPPED.items():
            self.assertIsNone(prompts.check(kind, text), kind)

    def test_organize_without_batch_is_refused_by_name(self):
        problem = prompts.check("organize", "Organise {folder}.")
        self.assertIn("{batch}", problem)

    def test_a_single_brace_from_a_json_example_is_refused(self):
        problem = prompts.check("organize", 'Organise {folder} in batches of {batch}: {"op": "mkdir"}')
        self.assertIsNotNone(problem)
        self.assertIn("{{", problem)

    def test_an_unknown_placeholder_is_refused(self):
        problem = prompts.check("review", "Goal: {goal}. Folder: {folder}.")
        self.assertIn("{folder}", problem)

    def test_a_placeholder_the_runner_cannot_fill_is_refused(self):
        # {folder} is a path string; ":d" only works on numbers, so every organize job would fail at its start.
        self.assertIsNotNone(prompts.check("organize", "Organise {folder:d} in batches of {batch}."))

    def test_research_and_plan_are_not_formatted_so_any_braces_are_fine(self):
        self.assertIsNone(prompts.check("research", 'Write {"a": 1} and {whatever}.'))
        self.assertIsNone(prompts.check("plan", "Unclosed { brace"))


class LoadTests(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_edits_in_config_json_are_used(self):
        path = self.write(json.dumps({"system_prompt": "x", "agent_prompts": {"plan": "Plan briefly."}}))
        got, notes = prompts.load(path)
        self.assertEqual(got["plan"], "Plan briefly.")
        self.assertEqual(notes, [])

    def test_a_missing_or_damaged_file_means_the_shipped_prompts(self):
        for path in (os.path.join(tempfile.mkdtemp(), "none.json"), self.write("{not json")):
            got, notes = prompts.load(path)
            self.assertEqual(got, prompts.SHIPPED)

    def test_an_edit_the_runner_cannot_fill_falls_back_with_a_note(self):
        # A hand edit of config.json skips the dialog's check; one bad brace must not fail every organize job.
        path = self.write(json.dumps({"agent_prompts": {"organize": "Sort {folder}.", "research": 42}}))
        got, notes = prompts.load(path)
        self.assertEqual(got["organize"], prompts.SHIPPED["organize"])
        self.assertEqual(got["research"], prompts.SHIPPED["research"])
        self.assertEqual(len(notes), 2)
        self.assertTrue(any("organize" in n and "{batch}" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
