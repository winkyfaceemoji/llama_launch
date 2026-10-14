import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class LoadPresetsTests(unittest.TestCase):
    def test_auto_preset_command_always_comes_from_code(self):
        config = {"presets": {"Mine": {"command": "x", "models": []},
                              launcher.AUTO_PRESET: {"command": "old auto -c 8192", "models": ["q"]}}}
        presets = launcher.load_presets(config)
        self.assertEqual(presets[launcher.AUTO_PRESET], {"command": launcher.AUTO_COMMAND, "models": []})
        self.assertEqual(next(iter(presets)), launcher.AUTO_PRESET)


class ResolveUvxCommandTests(unittest.TestCase):
    def test_substitutes_config_path(self):
        cmd = launcher.resolve_uvx_command('uvx mcp-proxy --named-server-config "{config}" --port 8001', r"D:\app\config.json")
        self.assertEqual(cmd, r'uvx mcp-proxy --named-server-config "D:\app\config.json" --port 8001')


class HealthStatusTests(unittest.TestCase):
    RUNNING = {"llama-server": None, "UVX proxy": None, "prompt proxy": None}

    def test_online_when_healthy(self):
        self.assertEqual(launcher.health_status(self.RUNNING, "ok"), ("● online", "ok"))

    def test_loading_is_not_offline(self):
        self.assertEqual(launcher.health_status(self.RUNNING, "loading"), ("● loading model", "warn"))

    def test_exited_process_is_reported_by_name(self):
        codes = dict(self.RUNNING, **{"UVX proxy": 1})
        self.assertEqual(launcher.health_status(codes, "ok"), ("● UVX proxy exited (code 1)", "error"))

    def test_offline_when_unreachable(self):
        self.assertEqual(launcher.health_status(self.RUNNING, "offline"), ("● offline", "idle"))


class PortOwnershipTests(unittest.TestCase):
    NETSTAT = (
        "  TCP    127.0.0.1:8001         0.0.0.0:0              LISTENING       18516\n"
        "  TCP    127.0.0.1:8001         127.0.0.1:64164        ESTABLISHED     18516\n"
        "  TCP    127.0.0.1:64164        127.0.0.1:8001         ESTABLISHED     11088\n"
        "  TCP    127.0.0.1:18001        0.0.0.0:0              LISTENING       999\n"
    )

    def test_finds_only_the_listener_on_that_exact_port(self):
        self.assertEqual(launcher.listening_pids(self.NETSTAT, 8001), {"18516"})

    def test_llama_server_is_ours(self):
        self.assertTrue(launcher.is_own_process("llama-server.exe", "llama-server -m x.gguf", r"C:\app"))

    def test_process_started_from_app_folder_is_ours(self):
        cmd = r'python.exe mcp-proxy.exe --named-server-config C:/App/config.json --port 8001'
        self.assertTrue(launcher.is_own_process("python.exe", cmd, r"C:\app"))

    def test_other_programs_are_not_ours(self):
        self.assertFalse(launcher.is_own_process("python.exe", r"python.exe C:\other\server.py", r"C:\app"))
        self.assertFalse(launcher.is_own_process("node.exe", "", r"C:\app"))


class EnsureAgentsServerTests(unittest.TestCase):
    def test_adds_entry_once(self):
        config = {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
        self.assertTrue(launcher.ensure_agents_server(config, r"C:\app\agents\server.py"))
        self.assertEqual(config["mcpServers"]["agents"],
                         {"command": "uv", "args": ["run", "--script", r"C:\app\agents\server.py"]})
        self.assertFalse(launcher.ensure_agents_server(config, r"C:\app\agents\server.py"))
        self.assertIn("fetch", config["mcpServers"])

    def test_updates_moved_path(self):
        config = {"mcpServers": {"agents": {"command": "uv", "args": ["run", "--script", r"D:\old\server.py"]}}}
        self.assertTrue(launcher.ensure_agents_server(config, r"C:\new\server.py"))
        self.assertEqual(config["mcpServers"]["agents"]["args"][-1], r"C:\new\server.py")


class JobActionsTests(unittest.TestCase):
    def job(self, kind, state, **extra):
        return dict({"id": "20261014-000000-abcd", "kind": kind, "state": state, "args": {}, "note": ""}, **extra)

    def test_awaiting_plan_can_be_reviewed(self):
        self.assertEqual(launcher.job_actions(self.job("organize", "awaiting_approval"), True, False, False),
                         {"open", "review"})

    def test_applied_or_partly_applied_can_be_restored(self):
        self.assertIn("restore", launcher.job_actions(self.job("organize", "applied"), True, True, False))
        self.assertIn("restore", launcher.job_actions(self.job("organize", "failed"), True, True, False))
        self.assertNotIn("restore", launcher.job_actions(self.job("organize", "failed"), True, False, False))

    def test_plan_steps_queue_once(self):
        self.assertIn("queue", launcher.job_actions(self.job("plan", "done"), True, False, True))
        self.assertNotIn("queue", launcher.job_actions(self.job("plan", "done", steps_queued=True), True, False, True))

    def test_running_job_has_nothing(self):
        self.assertEqual(launcher.job_actions(self.job("research", "running"), False, False, False), set())


if __name__ == "__main__":
    unittest.main()
