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

    def test_an_exit_already_shown_gives_way_to_llama_status(self):
        # The agents server is optional: its crash is shown once, then llama's status comes back.
        codes = dict(self.RUNNING, **{"agents server": 1})
        self.assertEqual(launcher.health_status(codes, "ok", shown={"agents server"}), ("● online", "ok"))

    def test_a_shown_exit_never_hides_another_crash(self):
        codes = dict(self.RUNNING, **{"agents server": 1, "UVX proxy": 2})
        self.assertEqual(launcher.health_status(codes, "ok", shown={"agents server"}),
                         ("● UVX proxy exited (code 2)", "error"))


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


class RemoveStaleAgentsEntryTests(unittest.TestCase):
    def test_removes_old_mcp_proxy_entry(self):
        config = {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
                                 "agents": {"command": "uv", "args": ["run", "--script", r"C:\app\agents\server.py"]}}}
        self.assertTrue(launcher.remove_stale_agents_entry(config))
        self.assertEqual(list(config["mcpServers"]), ["fetch"])
        self.assertFalse(launcher.remove_stale_agents_entry(config))

    def test_nothing_to_remove(self):
        self.assertFalse(launcher.remove_stale_agents_entry({}))
        self.assertFalse(launcher.remove_stale_agents_entry({"mcpServers": {"fetch": {}}}))


class ParseBuildTests(unittest.TestCase):
    def test_real_shapes(self):
        self.assertEqual(launcher.parse_build("b11636"), 11636)
        self.assertEqual(launcher.parse_build("version: 9776 (ac4105d68)"), 9776)
        self.assertEqual(launcher.parse_build("Version: b11636"), 11636)

    def test_anything_else_is_none(self):
        for text in ("b", "", "version: abc"):
            self.assertIsNone(launcher.parse_build(text), text)


class ParseWingetListTests(unittest.TestCase):
    TABLE = ("Name      Id            Version Available\r\n"
             "-----------------------------------------\r\n"
             "llama.cpp ggml.llamacpp b9776   b11636\r\n")

    def test_installed_and_available(self):
        self.assertEqual(launcher.parse_winget_list(self.TABLE), (9776, 11636))

    def test_available_empty_when_current(self):
        table = ("Name      Id            Version\r\n"
                 "-------------------------------\r\n"
                 "llama.cpp ggml.llamacpp b11636\r\n")
        self.assertEqual(launcher.parse_winget_list(table), (11636, None))

    def test_no_package_is_none(self):
        self.assertIsNone(launcher.parse_winget_list("No installed package found matching input criteria.\r\n"))

    def test_row_with_extra_columns(self):
        table = ("Name      Id            Version Available Source\r\n"
                 "------------------------------------------------\r\n"
                 "llama.cpp ggml.llamacpp b9776   b11636    winget\r\n")
        self.assertEqual(launcher.parse_winget_list(table), (9776, 11636))

    def test_current_build_with_source_column(self):
        table = ("Name      Id            Version Source\r\n"
                 "--------------------------------------\r\n"
                 "llama.cpp ggml.llamacpp b11636  winget\r\n")
        self.assertEqual(launcher.parse_winget_list(table), (11636, None))


class ParseWingetShowTests(unittest.TestCase):
    def test_real_output(self):
        out = ("Found llama.cpp [ggml.llamacpp]\r\n"
               "Version: b11636\r\n"
               "Publisher: ggml\r\n"
               "Publisher Url: https://github.com/ggml-org\r\n")
        self.assertEqual(launcher.parse_winget_show(out), 11636)

    def test_error_message_is_none(self):
        self.assertIsNone(launcher.parse_winget_show("No package found matching input criteria.\r\n"))


class WingetArgsTests(unittest.TestCase):
    BASE = ["--id", "ggml.llamacpp", "--exact", "--source", "winget", "--disable-interactivity"]

    def test_read_only_calls_accept_nothing(self):
        for action in ("list", "show"):
            self.assertEqual(launcher.winget_args(action), ["winget", action] + self.BASE)

    def test_upgrade_is_silent_and_accepts_terms(self):
        self.assertEqual(launcher.winget_args("upgrade"),
                         ["winget", "upgrade"] + self.BASE
                         + ["--silent", "--accept-package-agreements", "--accept-source-agreements"])

    def test_install_pins_the_build(self):
        self.assertEqual(launcher.winget_args("install", 9776),
                         ["winget", "install"] + self.BASE
                         + ["--version", "b9776", "--force",
                            "--silent", "--accept-package-agreements", "--accept-source-agreements"])

    def test_every_call_disables_interactivity(self):
        for args in (launcher.winget_args("list"), launcher.winget_args("show"),
                     launcher.winget_args("upgrade"), launcher.winget_args("install", 9776)):
            self.assertIn("--disable-interactivity", args)


class CheckVerdictTests(unittest.TestCase):
    def test_all_pass(self):
        results = [("version", True, "11636"), ("health", True, ""), ("chat with key", True, "hi")]
        self.assertEqual(launcher.check_verdict(results), (True, "Build check passed."))

    def test_second_check_fails(self):
        results = [("version", True, "11636"), ("chat without key", False, "status 200"),
                   ("tools without key", False, "status 200")]
        self.assertEqual(launcher.check_verdict(results),
                         (False, "Build check failed: chat without key (status 200)."))

    def test_skipped_checks_are_listed(self):
        results = [("version", True, "11636"), ("health", None, "no model"), ("context", None, "no model")]
        self.assertEqual(launcher.check_verdict(results),
                         (True, "Build check passed. Skipped: health, context."))


if __name__ == "__main__":
    unittest.main()
