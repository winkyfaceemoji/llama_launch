import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from launcher_app.config import ConfigError, ConfigStore, _DEFAULT_CONFIG  # noqa: E402
from launcher_app.selection import AUTO_PRESET  # noqa: E402


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    # loading

    def test_first_run_writes_the_defaults(self):
        store = ConfigStore.load(self.path)
        self.assertEqual(self.read(), _DEFAULT_CONFIG)
        self.assertEqual(store.data["llama_dir"], "")

    def test_a_typical_config_loads(self):
        self.write({"mcpServers": {"fetch": {}}, "system_prompt": "x", "llama_dir": "C:/m", "last_model": "m",
                    "presets": {}, "uvx_command": "uv run", "previous_build": "b11706", "theme": "dark",
                    "open_browser": False, "agent_prompts": {"plan": "p"}})
        store = ConfigStore.load(self.path)
        self.assertEqual(store.data["previous_build"], "b11706")

    def test_invalid_json_refuses_and_keeps_the_file(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"llama_dir": "C:/m",')
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertIn("isn't valid JSON", str(e.exception))
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"llama_dir": "C:/m",')

    def test_a_bad_value_names_its_key(self):
        cases = {"open_browser": "yes", "theme": "purple", "llama_dir": 3, "last_model": None,
                 "uvx_command": [], "system_prompt": 5, "previous_build": "latest",
                 "agent_prompts": ["plan"], "presets": {"Mine": {"command": 1}}}
        for key, value in cases.items():
            with self.subTest(key=key):
                self.write({key: value})
                with self.assertRaises(ConfigError) as e:
                    ConfigStore.load(self.path)
                self.assertIn(key, str(e.exception))
                self.assertEqual(self.read(), {key: value})  # nothing rewritten

    def test_a_legacy_preset_must_be_a_command(self):
        # A preset that isn't an object is an old-style command string; anything else would crash at launch.
        self.write({"presets": {"Old": 5}})
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertIn("Old", str(e.exception))

    def test_a_bom_refuses_and_says_how_to_fix_it(self):
        # proxy.py reads config.json too, so the launcher can't just skip the BOM.
        with open(self.path, "wb") as f:
            f.write(b'\xef\xbb\xbf{"llama_dir": "C:/m"}')
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertIn("UTF-8 without BOM", str(e.exception))

    def test_messages_read_as_one_sentence(self):
        for presets, message in (
                ({"x": {"command": "c", "models": 5}},
                 """config.json: "presets": invalid preset 'x': 'models' must be a list."""),
                ({"Old": 5}, """config.json: "presets": invalid preset 'Old': must be a command or an object.""")):
            with self.subTest(presets=presets):
                self.write({"presets": presets})
                with self.assertRaises(ConfigError) as e:
                    ConfigStore.load(self.path)
                self.assertEqual(str(e.exception), message)
        self.write({"theme": "purple"})
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertEqual(str(e.exception), 'config.json: "theme": must be one of system, light, dark.')

    def test_an_unreadable_file_refuses(self):
        with open(self.path, "wb") as f:
            f.write(b'{"llama_dir": "C:/\xff"}')
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertIn("UTF-8", str(e.exception))
        os.remove(self.path)
        os.mkdir(self.path)  # opening a folder raises an OSError, like a locked file
        with self.assertRaises(ConfigError) as e:
            ConfigStore.load(self.path)
        self.assertIn("can't be read", str(e.exception))
        os.rmdir(self.path)

    def test_presets_must_be_an_object(self):
        self.write({"presets": ["Auto"]})
        with self.assertRaises(ConfigError):
            ConfigStore.load(self.path)

    # changing

    def test_update_writes_and_keeps_keys_it_does_not_own(self):
        self.write({"mcpServers": {"fetch": {"command": "uvx"}}, "custom": 1, "llama_dir": "C:/m"})
        store = ConfigStore.load(self.path)
        store.update(open_browser=False, last_model="m")
        self.assertEqual(self.read(), {"mcpServers": {"fetch": {"command": "uvx"}}, "custom": 1,
                                       "llama_dir": "C:/m", "open_browser": False, "last_model": "m"})

    def test_none_removes_a_key(self):
        self.write({"previous_build": "b9776"})
        store = ConfigStore.load(self.path)
        store.update(previous_build=None)
        self.assertNotIn("previous_build", self.read())
        self.assertNotIn("previous_build", store.data)

    def test_a_bad_update_is_refused_before_anything_is_written(self):
        self.write({"llama_dir": "C:/m"})
        store = ConfigStore.load(self.path)
        with self.assertRaises(ConfigError):
            store.update(last_model="m", theme="purple")
        self.assertEqual(self.read(), {"llama_dir": "C:/m"})
        self.assertNotIn("last_model", store.data)

    def test_presets_keep_their_saved_shape(self):
        store = ConfigStore.load(self.path)
        presets = {AUTO_PRESET: {"command": "x", "models": []}, "Mine": {"command": "llama-server", "models": ["q"]}}
        store.update(presets=presets)
        self.assertEqual(self.read()["presets"], presets)

    def test_save_writes_changes_made_through_data(self):
        # For helpers that edit the whole dict (set_prompts, migrate_uvx_command); save checks it first.
        store = ConfigStore.load(self.path)
        store.data["system_prompt"] = "new"
        store.save()
        self.assertEqual(self.read()["system_prompt"], "new")
        store.data["open_browser"] = "no"
        with self.assertRaises(ConfigError):
            store.save()
        self.assertTrue(self.read()["open_browser"])


if __name__ == "__main__":
    unittest.main()
