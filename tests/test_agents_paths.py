import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import paths


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.sandbox = os.path.join(root, "sandbox")
        self.allowed = os.path.join(root, "allowed")
        self.outside = os.path.join(root, "outside")
        for d in (self.allowed, self.outside):
            os.makedirs(d)
        self.policy = paths.Policy(self.sandbox, folders_file=os.path.join(root, "folders.json"))
        self.policy.ensure_dirs()
        self.policy.add_folder(self.allowed)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_sandbox_and_listed_folders(self):
        self.assertTrue(self.policy.can_read(os.path.join(self.allowed, "a.txt")))
        self.assertTrue(self.policy.can_read(os.path.join(self.sandbox, "notes.md")))
        self.assertFalse(self.policy.can_read(os.path.join(self.outside, "a.txt")))

    def test_sibling_with_same_prefix_is_not_inside(self):
        self.assertFalse(self.policy.can_read(self.allowed + "2"))

    def test_writes_only_in_sandbox_outside_system_folders(self):
        self.assertTrue(self.policy.can_write(os.path.join(self.sandbox, "report.md")))
        self.assertFalse(self.policy.can_write(os.path.join(self.policy.jobs_dir, "x", "job.json")))
        self.assertFalse(self.policy.can_write(os.path.join(self.policy.trash_dir, "x")))
        self.assertFalse(self.policy.can_write(os.path.join(self.allowed, "a.txt")))

    def test_plans_may_change_listed_folders_only(self):
        self.assertTrue(self.policy.can_change(os.path.join(self.allowed, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(self.sandbox, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(paths.APP_DIR, "config.json")))

    def test_plans_skip_sandbox_under_listed_ancestor(self):
        root = self.tmp.name
        policy = paths.Policy(self.sandbox, folders_file=os.path.join(root, "ancestor.json"))
        policy.ensure_dirs()
        policy.add_folder(root)
        self.assertTrue(policy.can_change(os.path.join(root, "ordinary.txt")))
        self.assertFalse(policy.can_change(os.path.join(self.sandbox, "a.txt")))

    def test_plans_skip_launcher_folder_under_listed_ancestor(self):
        root = self.tmp.name
        app_parent = os.path.dirname(paths.APP_DIR)
        policy = paths.Policy(self.sandbox, folders_file=os.path.join(root, "app.json"))
        policy.ensure_dirs()
        policy.add_folder(root)
        policy.add_folder(app_parent)
        self.assertTrue(policy.can_change(os.path.join(app_parent, "other", "a.txt")))
        self.assertFalse(policy.can_change(os.path.join(paths.APP_DIR, "config.json")))
        self.assertFalse(policy.can_change(os.path.join(self.sandbox, "a.txt")))

    def test_plans_may_not_change_a_listed_folder_itself(self):
        self.assertFalse(self.policy.can_change(self.allowed))
        self.assertTrue(self.policy.can_change(os.path.join(self.allowed, "sub")))

    def test_plans_may_not_change_a_listed_folder_nested_in_another(self):
        inner = os.path.join(self.allowed, "inner")
        os.makedirs(inner)
        self.policy.add_folder(inner)
        self.assertFalse(self.policy.can_change(inner))
        self.assertFalse(self.policy.can_change(self.allowed))
        self.assertTrue(self.policy.can_change(os.path.join(inner, "a.txt")))
        self.assertTrue(self.policy.can_change(os.path.join(self.allowed, "other.txt")))

    def test_plans_may_not_change_ancestors_of_launcher_folder(self):
        grandparent = os.path.dirname(os.path.dirname(paths.APP_DIR))
        policy = paths.Policy(self.sandbox, folders_file=os.path.join(self.tmp.name, "anc.json"))
        policy.ensure_dirs()
        policy.add_folder(grandparent)
        self.assertFalse(policy.can_change(os.path.dirname(paths.APP_DIR)))
        self.assertTrue(policy.can_change(os.path.join(grandparent, "unrelated-folder-for-test", "a.txt")))

    def test_plans_may_not_change_ancestors_of_sandbox(self):
        root = os.path.join(self.tmp.name, "listed")
        sandbox = os.path.join(root, "mid", "sandbox")
        policy = paths.Policy(sandbox, folders_file=os.path.join(self.tmp.name, "anc2.json"))
        policy.ensure_dirs()
        policy.add_folder(root)
        self.assertFalse(policy.can_change(os.path.join(root, "mid")))
        self.assertTrue(policy.can_change(os.path.join(root, "other", "a.txt")))

    def test_reads_skip_launcher_folder_under_listed_ancestor(self):
        root = self.tmp.name
        policy = paths.Policy(self.sandbox, folders_file=os.path.join(root, "app.json"))
        policy.ensure_dirs()
        policy.add_folder(os.path.dirname(paths.APP_DIR))
        self.assertFalse(policy.can_read(os.path.join(paths.APP_DIR, "api_key.txt")))
        self.assertFalse(policy.can_read(os.path.join(paths.APP_DIR, "config.json")))

    def test_junction_escape_is_refused(self):
        link = os.path.join(self.allowed, "escape")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, self.outside], check=True, capture_output=True)
        self.assertFalse(self.policy.can_read(os.path.join(link, "a.txt")))
        self.assertFalse(self.policy.can_change(os.path.join(link, "a.txt")))

    def test_colon_after_drive_is_refused(self):
        # NTFS alternate data streams: "jobs:evil" would write into jobs\ itself.
        self.assertFalse(self.policy.can_write(os.path.join(self.sandbox, "jobs:evil")))
        self.assertFalse(self.policy.can_write(os.path.join(self.sandbox, "trash:evil")))
        self.assertFalse(self.policy.can_write(os.path.join(self.sandbox, "note.md:stream")))
        self.assertFalse(self.policy.can_read(os.path.join(self.allowed, "a.txt:stream")))
        self.assertFalse(self.policy.can_change(os.path.join(self.allowed, "a.txt:stream")))
        self.assertTrue(self.policy.can_write(os.path.join(self.sandbox, "note.md")))

    def test_network_paths_are_refused(self):
        self.assertFalse(paths.is_local(r"\\server\share\file.txt"))
        with self.assertRaises(ValueError):
            self.policy.add_folder(r"\\server\share")

    def test_folder_list_round_trip(self):
        self.policy.add_folder(self.allowed)  # duplicate is ignored
        self.assertEqual(len(self.policy.read_folders()), 1)
        self.policy.remove_folder(self.allowed)
        self.assertEqual(self.policy.read_folders(), [])

    def test_missing_folder_is_refused(self):
        with self.assertRaises(ValueError):
            self.policy.add_folder(os.path.join(self.outside, "nope"))


if __name__ == "__main__":
    unittest.main()
