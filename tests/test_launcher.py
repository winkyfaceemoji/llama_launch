import importlib.machinery
import importlib.util
import os
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "launcher.pyw")
_loader = importlib.machinery.SourceFileLoader("launcher", _PATH)
_spec = importlib.util.spec_from_loader("launcher", _loader)
launcher = importlib.util.module_from_spec(_spec)
_loader.exec_module(launcher)


class LoadApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "api_key.txt")

    def tearDown(self):
        self.dir.cleanup()

    def test_creates_key_file_when_missing(self):
        key = launcher.load_api_key(self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), key)
        self.assertGreaterEqual(len(key), 32)

    def test_reuses_existing_key(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("my-key\n")
        self.assertEqual(launcher.load_api_key(self.path), "my-key")

    def test_replaces_empty_key_file(self):
        open(self.path, "w").close()
        key = launcher.load_api_key(self.path)
        self.assertGreaterEqual(len(key), 32)


class BuildLaunchCommandTests(unittest.TestCase):
    def test_adds_api_key_file(self):
        cmd = launcher.build_launch_command('llama-server -m "{model}"', "C:/m/a.gguf", None, "C:/k/key.txt")
        self.assertEqual(cmd, 'llama-server -m "C:/m/a.gguf" --api-key-file "C:/k/key.txt"')

    def test_keeps_a_key_the_preset_already_sets(self):
        for flag in ("--api-key secret", '--api-key-file "D:/other.txt"'):
            cmd = launcher.build_launch_command(f'llama-server -m "{{model}}" {flag}', "a.gguf", None, "key.txt")
            self.assertNotIn("key.txt", cmd.replace("other.txt", ""))

    def test_adds_mmproj_for_vision_models(self):
        cmd = launcher.build_launch_command('llama-server -m "{model}"', "a.gguf", "mmproj.gguf", "key.txt")
        self.assertIn('--mmproj "mmproj.gguf"', cmd)


if __name__ == "__main__":
    unittest.main()
