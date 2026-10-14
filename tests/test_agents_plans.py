import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import paths, plans


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dl = os.path.join(root, "Downloads")
        touch(os.path.join(self.dl, "a.pdf"))
        touch(os.path.join(self.dl, "b.tmp"))
        self.policy = paths.Policy(os.path.join(root, "sandbox"), folders_file=os.path.join(root, "f.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.dl)
        self.p = lambda *parts: os.path.join(self.dl, *parts)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_plan(self):
        ops = [{"op": "mkdir", "path": self.p("Docs")},
               {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs", "a.pdf")},
               {"op": "remove", "path": self.p("b.tmp")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])

    def test_rejects_bad_operations(self):
        outside = os.path.join(self.tmp.name, "elsewhere.txt")
        cases = [
            [],
            [{"op": "delete", "path": self.p("a.pdf")}],
            [{"op": "move", "src": self.p("a.pdf")}],
            [{"op": "remove", "path": outside}],
            [{"op": "remove", "path": "a.pdf"}],
            [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("b.tmp")}],
            [{"op": "move", "src": self.p("missing"), "dst": self.p("x")}],
            [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("NoSuchDir", "a.pdf")}],
            [{"op": "mkdir", "path": self.p("Sub")}, {"op": "move", "src": self.dl, "dst": self.p("Sub", "x")}],
            [{"op": "mkdir", "path": self.p("a.pdf")}],
        ]
        for ops in cases:
            self.assertNotEqual(plans.validate_plan(ops, self.policy), [], ops)

    def test_apply_then_restore_round_trip(self):
        ops = [{"op": "mkdir", "path": self.p("Docs")},
               {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs", "a.pdf")},
               {"op": "remove", "path": self.p("b.tmp")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertIsNone(error)
        self.assertTrue(os.path.exists(self.p("Docs", "a.pdf")))
        self.assertFalse(os.path.exists(self.p("b.tmp")))
        self.assertTrue(log[2]["to"].startswith(os.path.join(self.policy.trash_dir, "20261014-000000-abcd")))
        self.assertEqual(plans.restore(log), [])
        self.assertTrue(os.path.exists(self.p("a.pdf")))
        self.assertTrue(os.path.exists(self.p("b.tmp")))
        self.assertFalse(os.path.exists(self.p("Docs")))

    def test_apply_stops_at_first_error(self):
        ops = [{"op": "remove", "path": self.p("a.pdf")},
               {"op": "move", "src": self.p("gone"), "dst": self.p("x")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertEqual(len(log), 1)
        self.assertIn("step 2", error)

    def test_restore_skips_occupied_paths(self):
        ops = [{"op": "remove", "path": self.p("a.pdf")}, {"op": "remove", "path": self.p("b.tmp")}]
        log, _ = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        touch(self.p("a.pdf"), "new file")
        skipped = plans.restore(log)
        self.assertEqual(len(skipped), 1)
        self.assertIn("a.pdf", skipped[0])
        with open(self.p("a.pdf")) as f:
            self.assertEqual(f.read(), "new file")
        self.assertTrue(os.path.exists(self.p("b.tmp")))

    def test_empty_trash(self):
        plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], "20261014-000000-abcd", self.policy)
        self.assertEqual(plans.empty_trash(self.policy), 1)
        self.assertEqual(os.listdir(self.policy.trash_dir), [])


if __name__ == "__main__":
    unittest.main()
