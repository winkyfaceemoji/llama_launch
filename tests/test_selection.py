import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from launcher_app.selection import (AUTO_COMMAND, AUTO_PRESET, NO_MODELS, NO_PRESETS,  # noqa: E402
                                    PresetError, Selection)

KEY = "C:\\l\\api_key.txt"
QWEN = {"qwen-9b": ("C:\\m\\qwen-9b.gguf", None)}
TWO = {"Qwen-9B": ("C:\\m\\Qwen-9B.gguf", None), "gemma-26b": ("C:\\m\\v\\gemma-26b.gguf", "C:\\m\\v\\mmproj.gguf")}
QWEN_ONLY = {"Qwen only": {"command": "llama-server -m {model} -c 4096", "models": ["qwen"]}}


class Fixture:
    def __init__(self, found, presets=None, last_model=""):
        self.found = dict(found)
        self.remembered, self.saved = [], []
        self.sel = Selection(dict(presets or {}), last_model, "C:\\m", KEY,
                             remember_model=self.remembered.append, save_presets=self.saved.append,
                             scan=lambda folder: dict(self.found))


def command_for(path, mmproj=None):
    # What build_launch_command produces for the Auto preset (tested on its own in test_launcher).
    from launcher_app.selection import build_launch_command
    return build_launch_command(AUTO_COMMAND, path, mmproj, KEY)


class StartupTests(unittest.TestCase):
    def test_last_model_is_picked_and_not_written(self):
        f = Fixture(TWO, last_model="gemma-26b")
        v = f.sel.view
        self.assertEqual(v.models, ("gemma-26b", "Qwen-9B"))  # sorted ignoring case
        self.assertEqual((v.model, v.preset), ("gemma-26b", AUTO_PRESET))
        self.assertEqual(v.command, command_for(*TWO["gemma-26b"]))
        self.assertEqual(v.model_files, TWO["gemma-26b"])
        self.assertEqual(f.remembered, [])

    def test_unknown_last_model_falls_back_to_the_first(self):
        self.assertEqual(Fixture(TWO, last_model="gone").sel.view.model, "gemma-26b")

    def test_no_models(self):
        v = Fixture({}).sel.view
        self.assertEqual((v.models, v.model), ((NO_MODELS,), NO_MODELS))
        self.assertEqual((v.presets, v.preset, v.command), ((NO_PRESETS,), NO_PRESETS, ""))
        self.assertEqual(v.model_files, (None, None))

    def test_presets_filtered_by_keyword_auto_first(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        self.assertEqual(f.sel.view.presets, (AUTO_PRESET, "Qwen only"))  # keyword matched ignoring case
        self.assertEqual(f.sel.pick_model("gemma-26b").presets, (AUTO_PRESET,))


class ParenNameTests(unittest.TestCase):
    # As in the single-file launcher: a name starting with "(" was taken for a placeholder such as
    # "(no .gguf models found)", so such a model gets no presets (no command) and is never remembered.
    PAREN = {"(test)": ("C:\\m\\(test).gguf", None), "qwen-9b": ("C:\\m\\qwen-9b.gguf", None)}

    def test_no_presets_and_no_command(self):
        v = Fixture(self.PAREN, last_model="(test)").sel.view
        self.assertEqual((v.model, v.presets, v.preset, v.command), ("(test)", (NO_PRESETS,), NO_PRESETS, ""))

    def test_picking_it_is_not_remembered(self):
        f = Fixture(self.PAREN, last_model="qwen-9b")
        f.sel.pick_model("(test)")
        self.assertEqual(f.remembered, [])

    def test_refresh_fallback_to_it_is_not_remembered(self):
        f = Fixture(self.PAREN, last_model="qwen-9b")
        del f.found["qwen-9b"]
        self.assertEqual(f.sel.refresh().model, "(test)")
        self.assertEqual(f.remembered, [])


class PickTests(unittest.TestCase):
    def test_picking_a_model_remembers_it_and_resets_to_auto(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        f.sel.pick_preset("Qwen only")
        v = f.sel.pick_model("Qwen-9B")  # even the same model
        self.assertEqual(v.preset, AUTO_PRESET)
        self.assertEqual(f.remembered, ["Qwen-9B"])

    def test_picking_a_preset_builds_its_command(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        v = f.sel.pick_preset("Qwen only")
        self.assertEqual(v.preset, "Qwen only")
        self.assertIn("-c 4096", v.command)
        self.assertIn("C:\\m\\Qwen-9B.gguf", v.command)


class RefreshTests(unittest.TestCase):
    def test_same_model_new_mmproj_rebuilds_the_command(self):
        # The past bug: Refresh kept the old command when only the files changed.
        f = Fixture(QWEN)
        f.found["qwen-9b"] = ("C:\\m\\qwen-9b.gguf", "C:\\m\\mmproj.gguf")
        v = f.sel.refresh()
        self.assertIn("--mmproj", v.command)
        self.assertEqual(f.remembered, [])

    def test_a_kept_model_keeps_its_preset(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        f.sel.pick_preset("Qwen only")
        self.assertEqual(f.sel.refresh().preset, "Qwen only")

    def test_a_vanished_model_falls_back_and_is_remembered(self):
        f = Fixture(TWO, last_model="Qwen-9B")
        del f.found["Qwen-9B"]
        v = f.sel.refresh()
        self.assertEqual((v.model, v.preset), ("gemma-26b", AUTO_PRESET))
        self.assertEqual(f.remembered, ["gemma-26b"])

    def test_set_folder_scans_the_new_folder(self):
        seen = []
        sel = Selection({}, "", "C:\\old", KEY, scan=lambda folder: seen.append(folder) or dict(QWEN))
        sel.set_folder("C:\\new")
        self.assertEqual(seen, ["C:\\old", "C:\\new"])

    def test_model_names_rescans_without_changing_the_choice(self):
        f = Fixture(QWEN)
        f.found["another"] = ("C:\\m\\another.gguf", None)
        self.assertEqual(f.sel.model_names(), ["another", "qwen-9b"])
        self.assertEqual(f.sel.view.models, ("qwen-9b",))


class PresetChangeTests(unittest.TestCase):
    def test_add_saves_and_selects_a_matching_preset(self):
        f = Fixture(TWO, last_model="Qwen-9B")
        v = f.sel.add_preset("Small ctx", "llama-server -m {model} -c 2048", [])
        self.assertEqual(v.preset, "Small ctx")
        self.assertEqual(list(f.saved[-1]), [AUTO_PRESET, "Small ctx"])

    def test_add_a_preset_for_another_model_keeps_the_choice(self):
        f = Fixture(TWO, last_model="gemma-26b")
        self.assertEqual(f.sel.add_preset("Q", "x {model}", ["qwen"]).preset, AUTO_PRESET)

    def test_duplicate_name_is_refused(self):
        f = Fixture(TWO, presets=QWEN_ONLY)
        with self.assertRaises(PresetError) as e:
            f.sel.add_preset("Qwen only", "x", [])
        self.assertEqual((e.exception.kind, e.exception.title), ("error", "Duplicate"))
        self.assertEqual(f.saved, [])

    def test_edit_renames_and_moves_to_the_end(self):
        presets = {"A": {"command": "a {model}", "models": []}, "B": {"command": "b {model}", "models": []}}
        f = Fixture(TWO, presets=presets)
        f.sel.pick_preset("A")
        self.assertEqual(f.sel.preset_for_edit(), ("A", "a {model}", []))
        v = f.sel.edit_preset("A", "A2", "a2 {model}", [])
        self.assertEqual(list(f.saved[-1]), [AUTO_PRESET, "B", "A2"])
        self.assertEqual(v.preset, "A2")

    def test_edit_to_an_existing_name_is_refused(self):
        presets = {"A": {"command": "a", "models": []}, "B": {"command": "b", "models": []}}
        f = Fixture(TWO, presets=presets)
        with self.assertRaises(PresetError):
            f.sel.edit_preset("A", "B", "a", [])

    def test_auto_cannot_be_edited_or_deleted(self):
        f = Fixture(TWO)
        for call in (f.sel.preset_for_edit, lambda: f.sel.delete_preset(AUTO_PRESET)):
            with self.assertRaises(PresetError) as e:
                call()
            self.assertEqual((e.exception.kind, e.exception.title), ("info", "Auto Preset"))

    def test_nothing_to_edit_or_delete_without_models(self):
        f = Fixture({})
        for call in (f.sel.preset_for_edit, lambda: f.sel.delete_preset(NO_PRESETS)):
            with self.assertRaises(PresetError) as e:
                call()
            self.assertEqual(e.exception.kind, "warning")

    def test_delete_asks_only_after_its_checks_and_a_no_changes_nothing(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        asked = []
        with self.assertRaises(PresetError):
            f.sel.delete_preset(AUTO_PRESET, confirm=asked.append)
        self.assertEqual(asked, [])
        self.assertIsNone(f.sel.delete_preset("Qwen only", confirm=lambda name: asked.append(name) or False))
        self.assertEqual(asked, ["Qwen only"])
        self.assertEqual(f.saved, [])

    def test_deleting_the_selected_preset_falls_back_to_auto(self):
        f = Fixture(TWO, presets=QWEN_ONLY, last_model="Qwen-9B")
        f.sel.pick_preset("Qwen only")
        v = f.sel.delete_preset("Qwen only")
        self.assertEqual(v.preset, AUTO_PRESET)
        self.assertEqual(list(f.saved[-1]), [AUTO_PRESET])


if __name__ == "__main__":
    unittest.main()
