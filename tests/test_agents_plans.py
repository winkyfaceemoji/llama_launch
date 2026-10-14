import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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
        self.assertTrue(log[2]["to"].startswith(os.path.join(self.policy.trash_dir, "20261014-000000-abcd", "3")))
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

    def test_on_step_sees_log_after_each_operation(self):
        seen = []
        ops = [{"op": "remove", "path": self.p("a.pdf")}, {"op": "remove", "path": self.p("b.tmp")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy,
                                      on_step=lambda l: seen.append(len(l)))
        self.assertIsNone(error)
        self.assertEqual(seen, [1, 2])

    def test_apply_never_raises(self):
        real = self.policy.can_change

        def flaky(path):
            if path == self.p("b.tmp") and not os.path.exists(self.p("a.pdf")):
                raise PermissionError("folder list is being saved")
            return real(path)

        ops = [{"op": "remove", "path": self.p("a.pdf")}, {"op": "remove", "path": self.p("b.tmp")}]
        with mock.patch.object(self.policy, "can_change", flaky):
            log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertEqual(len(log), 1)
        self.assertTrue(error.startswith("step 2 failed"), error)

    def test_on_step_failure_ends_run_with_log_intact(self):
        def boom(log):
            raise OSError("disk full")

        log, error = plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], "20261014-000000-abcd",
                                      self.policy, on_step=boom)
        self.assertEqual(len(log), 1)
        self.assertIn("step 1 failed", error)

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

    # R1: no operation may depend on another, except through mkdir parents.
    def test_r1_overlapping_operations_are_rejected(self):
        touch(self.p("D", "f"), "f")
        touch(self.p("D", "g"), "g")
        cases = [
            [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("x")},
             {"op": "move", "src": self.p("b.tmp"), "dst": self.p("x")}],
            [{"op": "remove", "path": self.p("D", "f")}, {"op": "remove", "path": self.p("D")}],
            [{"op": "remove", "path": self.p("D")}, {"op": "remove", "path": self.p("D", "f")}],
            [{"op": "mkdir", "path": self.p("Docs")},
             {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs")}],
            [{"op": "mkdir", "path": self.p("Docs")}, {"op": "mkdir", "path": self.p("Docs")}],
            [{"op": "mkdir", "path": self.p("Docs")},
             {"op": "move", "src": self.p("Docs"), "dst": self.p("Docs2")}],
        ]
        for ops in cases:
            errors = plans.validate_plan(ops, self.policy)
            self.assertTrue(any("overlaps operation" in e for e in errors), (ops, errors))
        errors = plans.validate_plan(cases[0], self.policy)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("2:") and "operation 1" in errors[0], errors)

    def test_r1_mkdir_parents_are_allowed(self):
        ops = [{"op": "mkdir", "path": self.p("Docs")},
               {"op": "move", "src": self.p("a.pdf"), "dst": self.p("Docs", "a.pdf")},
               {"op": "move", "src": self.p("b.tmp"), "dst": self.p("Docs", "b.pdf")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])
        nested = [{"op": "mkdir", "path": self.p("A")}, {"op": "mkdir", "path": self.p("A", "B")}]
        self.assertEqual(plans.validate_plan(nested, self.policy), [])

    # R2: apply re-checks every operation immediately before running it.
    def test_r2_apply_does_not_overwrite_file_created_after_validation(self):
        ops = [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("y.pdf")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])
        touch(self.p("y.pdf"), "USER NEW")
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertIn("step 1", error)
        self.assertEqual(log, [])
        with open(self.p("y.pdf")) as f:
            self.assertEqual(f.read(), "USER NEW")
        self.assertTrue(os.path.exists(self.p("a.pdf")))

    def test_r2_apply_does_not_move_into_directory_created_after_validation(self):
        ops = [{"op": "move", "src": self.p("a.pdf"), "dst": self.p("Q")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])
        os.makedirs(self.p("Q"))
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertIsNotNone(error)
        self.assertEqual(os.listdir(self.p("Q")), [])
        self.assertTrue(os.path.exists(self.p("a.pdf")))

    def test_r2_apply_stops_on_other_late_changes(self):
        job = "20261014-000000-abcd"
        os.makedirs(self.p("Docs"))
        log, error = plans.apply_plan([{"op": "mkdir", "path": self.p("Docs")}], job, self.policy)
        self.assertIsNotNone(error)
        log, error = plans.apply_plan([{"op": "remove", "path": self.p("gone")}], job, self.policy)
        self.assertIsNotNone(error)
        log, error = plans.apply_plan([{"op": "move", "src": self.p("gone"), "dst": self.p("x")}], job, self.policy)
        self.assertIsNotNone(error)
        self.policy.remove_folder(self.dl)
        log, error = plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], job, self.policy)
        self.assertIn("not allowed", error)
        self.assertEqual(log, [])
        self.assertTrue(os.path.exists(self.p("a.pdf")))

    # R3: trash target is unique per operation.
    def test_r3_removing_folder_after_its_child_does_not_nest_in_trash(self):
        touch(self.p("D", "f"), "f")
        touch(self.p("D", "g"), "g")
        job = "20261014-000000-abcd"
        ops = [{"op": "remove", "path": self.p("D", "f")}, {"op": "remove", "path": self.p("D")}]
        log, error = plans.apply_plan(ops, job, self.policy)
        self.assertIsNone(error)
        self.assertTrue(log[0]["to"].startswith(os.path.join(self.policy.trash_dir, job, "1")))
        self.assertTrue(log[1]["to"].startswith(os.path.join(self.policy.trash_dir, job, "2")))
        self.assertFalse(log[1]["to"].startswith(log[0]["to"]))
        self.assertEqual(plans.restore(log), [])
        self.assertTrue(os.path.exists(self.p("D", "f")))
        self.assertTrue(os.path.exists(self.p("D", "g")))
        self.assertFalse(os.path.exists(self.p("D", "D")))

    # R5: job ids that are not the job store's format are refused.
    def test_r5_trash_path_rejects_bad_job_ids(self):
        for bad in ["..\\x", "C:\\x", "", "20261014-000000-abcd\n", "20261014-000000-ABCD", "../../x", None]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                plans.trash_path(self.policy, bad, self.p("a.pdf"))
        self.assertTrue(plans.trash_path(self.policy, "20261014-000000-abcd", self.p("a.pdf")).endswith("a.pdf"))
        log, error = plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], "..\\evil", self.policy)
        self.assertIsNotNone(error)
        self.assertTrue(os.path.exists(self.p("a.pdf")))

    # R6: restore carries on past an entry that fails.
    def test_r6_restore_continues_after_an_entry_fails(self):
        touch(self.p("sub", "x.txt"), "x")
        ops = [{"op": "remove", "path": self.p("a.pdf")}, {"op": "remove", "path": self.p("sub", "x.txt")}]
        log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertIsNone(error)
        os.rmdir(self.p("sub"))
        touch(self.p("sub"), "a file where the folder used to be")
        skipped = plans.restore(log)
        self.assertEqual(len(skipped), 1)
        self.assertIn("could not restore " + self.p("sub", "x.txt"), skipped[0])
        self.assertTrue(os.path.exists(self.p("a.pdf")))

    # S1: only plain Windows paths are accepted.
    def test_s1_odd_paths_are_rejected(self):
        touch(self.p("e.txt"), "EEE")
        cases = [
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("e.txt:hidden")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x.txt.")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x.txt ")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("sub. ", "x.txt")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x*.txt")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x?.txt")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x<>.txt")},
            {"op": "move", "src": self.p("a.pdf"), "dst": self.p("x|.txt")},
            {"op": "mkdir", "path": self.p("bad:name")},
        ]
        for op in cases:
            errors = plans.validate_plan([op], self.policy)
            self.assertEqual(len(errors), 1, (op, errors))
            self.assertIn("plain", errors[0])
            log, error = plans.apply_plan([op], "20261014-000000-abcd", self.policy)
            self.assertEqual(log, [])
            self.assertIn("nothing changed", error)
        self.assertTrue(os.path.exists(self.p("a.pdf")))
        with open(self.p("e.txt")) as f:
            self.assertEqual(f.read(), "EEE")

    # S2: moves and removes never copy.
    def _other_drive(self):
        here = os.path.splitdrive(os.path.abspath(self.tmp.name))[0].upper()
        return "Q:" if here != "Q:" else "R:"

    def test_s2_cross_drive_operations_are_rejected_without_touching_the_disk(self):
        other = self._other_drive()
        move = {"op": "move", "src": self.p("a.pdf"), "dst": other + "\\Downloads\\a.pdf"}
        errors = plans.validate_plan([move], self.policy)
        self.assertEqual(len(errors), 1)
        self.assertIn("moves across drives aren't supported", errors[0])
        remove = {"op": "remove", "path": other + "\\Downloads\\a.pdf"}
        errors = plans.validate_plan([remove], self.policy)
        self.assertEqual(len(errors), 1)
        self.assertIn("removing files on another drive isn't supported", errors[0])
        for op in (move, remove):
            log, error = plans.apply_plan([op], "20261014-000000-abcd", self.policy)
            self.assertEqual(log, [])
            self.assertIn("nothing changed", error)

    def test_s2_move_of_folder_with_open_file_leaves_folder_in_place(self):
        for name in "abc":
            touch(self.p("D", name), name)
        ops = [{"op": "move", "src": self.p("D"), "dst": self.p("E")}]
        self.assertEqual(plans.validate_plan(ops, self.policy), [])
        with open(self.p("D", "b"), "rb"):
            log, error = plans.apply_plan(ops, "20261014-000000-abcd", self.policy)
        self.assertEqual(log, [])
        self.assertIn("nothing changed", error)
        self.assertEqual(sorted(os.listdir(self.p("D"))), ["a", "b", "c"])
        self.assertFalse(os.path.exists(self.p("E")))

    def test_s2_remove_of_folder_with_open_file_leaves_folder_in_place(self):
        for name in "abc":
            touch(self.p("D", name), name)
        with open(self.p("D", "b"), "rb"):
            log, error = plans.apply_plan([{"op": "remove", "path": self.p("D")}],
                                          "20261014-000000-abcd", self.policy)
        self.assertEqual(log, [])
        self.assertIn("nothing changed", error)
        self.assertEqual(sorted(os.listdir(self.p("D"))), ["a", "b", "c"])

    # S3: empty_trash never follows links.
    def _junction_in_trash_check(self):
        outside = os.path.join(self.tmp.name, "outside")
        outside_file = os.path.join(outside, "user_ro.txt")
        touch(outside_file, "keep")
        os.chmod(outside_file, stat.S_IREAD)
        touch(self.p("D", "a_ro.txt"))
        os.chmod(self.p("D", "a_ro.txt"), stat.S_IREAD)
        subprocess.run(["cmd", "/c", "mklink", "/J", self.p("D", "z_link"), outside],
                       check=True, capture_output=True)
        log, error = plans.apply_plan([{"op": "remove", "path": self.p("D")}], "20261014-000000-abcd", self.policy)
        self.assertIsNone(error)
        self.assertEqual(plans.empty_trash(self.policy), 1)
        self.assertEqual(os.listdir(self.policy.trash_dir), [])
        try:
            self.assertTrue(os.path.exists(outside_file))
            self.assertFalse(os.stat(outside_file).st_mode & stat.S_IWRITE)
        finally:
            os.chmod(outside_file, stat.S_IWRITE | stat.S_IREAD)

    def test_s3_empty_trash_leaves_junction_targets_alone(self):
        self._junction_in_trash_check()

    # T1: the link test must not depend on os.path.isjunction (missing before Python 3.12).
    def test_t1_empty_trash_leaves_junction_targets_alone_without_isjunction(self):
        with mock.patch.object(os.path, "isjunction", lambda p: False, create=True):
            self._junction_in_trash_check()

    # T2/T3: reserved device names and control characters are not plain paths.
    def test_t2_reserved_device_names_are_rejected(self):
        for name in ["CON", "aux.pdf", "Com1.log", "nul.txt", "LPT9", "prn", "CONIN$", "conout$", "COM9.tar.gz"]:
            for path in (self.p(name), self.p(name, "x.txt")):
                op = {"op": "move", "src": self.p("a.pdf"), "dst": path}
                errors = plans.validate_plan([op], self.policy)
                self.assertEqual(len(errors), 1, (path, errors))
                self.assertIn("reserved", errors[0])
        ok = {"op": "move", "src": self.p("a.pdf"), "dst": self.p("console.pdf")}
        self.assertEqual(plans.validate_plan([ok], self.policy), [])

    def test_t3_control_characters_are_rejected(self):
        for name in ["a	b.txt", "x.txt", "y"]:
            op = {"op": "move", "src": self.p("a.pdf"), "dst": self.p(name)}
            errors = plans.validate_plan([op], self.policy)
            self.assertEqual(len(errors), 1, (name, errors))
            self.assertIn("control", errors[0])
            log, error = plans.apply_plan([op], "20261014-000000-abcd", self.policy)
            self.assertEqual(log, [])
            self.assertIn("nothing changed", error)

    # R8: empty_trash copes with a missing folder, stray files and read-only files.
    def test_r8_empty_trash_edge_cases(self):
        shutil.rmtree(self.policy.trash_dir)
        self.assertEqual(plans.empty_trash(self.policy), 0)
        os.makedirs(self.policy.trash_dir)
        touch(os.path.join(self.policy.trash_dir, "stray.txt"))
        stray_ro = os.path.join(self.policy.trash_dir, "stray_ro.txt")
        touch(stray_ro)
        os.chmod(stray_ro, stat.S_IREAD)
        plans.apply_plan([{"op": "remove", "path": self.p("a.pdf")}], "20261014-000000-abcd", self.policy)
        trashed = plans.trash_path(self.policy, "20261014-000000-abcd", self.p("a.pdf"), 1)
        os.chmod(trashed, stat.S_IREAD)
        self.assertEqual(plans.empty_trash(self.policy), 3)
        self.assertEqual(os.listdir(self.policy.trash_dir), [])


if __name__ == "__main__":
    unittest.main()
