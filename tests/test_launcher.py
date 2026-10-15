import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from unittest import mock
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

    def test_auto_preset_enables_no_built_in_tools(self):
        # b11636 dropped get_datetime and refuses to start with it; its remaining
        # built-in tools read, write and run things. The time comes from MCP (ADR 0011).
        self.assertNotIn("--tools", launcher.AUTO_COMMAND)
        self.assertEqual(launcher._DEFAULT_CONFIG["mcpServers"]["time"],
                         {"command": "uvx", "args": ["mcp-server-time"]})


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
        # Newer builds (b11636) print a semantic version first; the build number is in brackets.
        self.assertEqual(launcher.parse_build(
            "version: 0.6.0-dev (build 11636, commit 529ac893e)\r\nbuilt with Clang 20.1.8 for Windows x86_64\r\n"), 11636)

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


class _FakeProc:
    pid = 4242

    def __init__(self, lines):
        self.stdout = iter(lines)

    def poll(self):
        return None  # still running


class UpdateRunnerTests(unittest.TestCase):
    # Fake run/popen: no WinGet, no GPU, no ports.
    LIST_NEWER = ("Name      Id            Version Available Source\r\n"
                  "------------------------------------------------\r\n"
                  "llama.cpp ggml.llamacpp b9776   b11636    winget\r\n")
    LIST_CURRENT = ("Name      Id            Version Source\r\n"
                    "--------------------------------------\r\n"
                    "llama.cpp ggml.llamacpp b11636  winget\r\n")
    PROBE_PASS = [("chat with key", True, "'hi'"), ("chat without key", True, "HTTP 401"),
                  ("tools without key", True, "HTTP 401"), ("cors-proxy without key", True, "HTTP 404")]
    PROBE_LEAKY = [("chat with key", True, "'hi'"), ("chat without key", False, "HTTP 200"),
                   ("tools without key", True, "HTTP 401"), ("cors-proxy without key", True, "HTTP 404")]
    KILL = "taskkill /F /T /PID 4242"
    INSTALL_9776 = ["winget", "install", "--id", "ggml.llamacpp", "--exact", "--source", "winget",
                    "--disable-interactivity", "--version", "b9776", "--force",
                    "--silent", "--accept-package-agreements", "--accept-source-agreements"]

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.key_file = os.path.join(self.dir.name, "api_key.txt")
        with open(self.key_file, "w", encoding="utf-8") as f:
            f.write("secret-key\n")
        self.reported, self.calls, self.popened = [], [], []
        self.exit_codes = {"list": 0, "upgrade": 0, "install": 0}
        self.list_output = self.LIST_NEWER
        self.versions = [11636]          # successive `llama-server --version` answers
        self.server_log = ["load_tensors: offloaded 49/49 layers to GPU\n",
                           "llama_context: n_ctx         = 32768\n"]
        self.runner = launcher.UpdateRunner(self.reported.append, run=self.fake_run, popen=self.fake_popen)

    def tearDown(self):
        self.dir.cleanup()

    def fake_run(self, args, **kw):
        self.calls.append(args)
        if isinstance(args, list) and args[0] == "winget":
            action = args[1]
            if isinstance(self.exit_codes[action], Exception):
                raise self.exit_codes[action]
            out = self.list_output if action == "list" else f"winget {action} line\r\n"
            return subprocess.CompletedProcess(args, self.exit_codes[action], out, "")
        if "--version" in args:
            return subprocess.CompletedProcess(args, 0, f"version: {self.versions.pop(0)} (ac4105d68)\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")  # taskkill

    def fake_popen(self, cmd, **kw):
        self.popened.append(cmd)
        return _FakeProc(self.server_log)

    def patched(self, healthy=True, probe=None):
        return (mock.patch.object(launcher, "wait_healthy", return_value=healthy),
                mock.patch.object(launcher, "probe_server", return_value=probe or self.PROBE_PASS))

    # check()

    def test_check_finds_newer_build(self):
        self.assertEqual(self.runner.check(), (9776, 11636))

    def test_check_is_none_when_current(self):
        self.list_output = self.LIST_CURRENT
        self.assertIsNone(self.runner.check())

    def test_check_is_none_on_winget_error(self):
        self.exit_codes["list"] = FileNotFoundError("winget")
        self.assertIsNone(self.runner.check())

    def test_check_is_none_on_error_message(self):
        self.list_output = "No installed package found matching input criteria.\r\n"
        self.assertIsNone(self.runner.check())

    # build_check()

    def test_build_check_passes_on_spare_port(self):
        wait, probe = self.patched()
        with wait as w, probe as p:
            results = self.runner.build_check(11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(results, [("version", True, "11636"), ("health", True, "ok")]
                         + self.PROBE_PASS
                         + [("context (--fit)", True, "llama_context: n_ctx         = 32768")])
        self.assertIn("--port 18081", self.popened[0])
        self.assertNotIn("--port 8081", self.popened[0])
        w.assert_called_once_with("http://127.0.0.1:18081", 300, alive=mock.ANY)
        p.assert_called_once_with("http://127.0.0.1:18081", "secret-key")
        self.assertIn(self.KILL, self.calls)
        self.assertIn("✓ version — 11636", self.reported)

    def test_wrong_version_fails(self):
        self.versions = [9776]
        results = self.runner.build_check(11636, "", None, self.key_file)
        self.assertEqual(results[0], ("version", False, "9776, expected 11636"))

    def test_unhealthy_server_fails_remaining_checks(self):
        wait, probe = self.patched(healthy=False)
        with wait, probe as p:
            results = self.runner.build_check(11636, "C:\\m\\a.gguf", None, self.key_file)
        p.assert_not_called()
        self.assertEqual(results[1], ("health", False, "not ok (server exited or 300 s passed)"))
        self.assertEqual(results[2], ("chat with key", False, "server never became healthy"))
        self.assertIn(self.KILL, self.calls)

    def test_no_model_skips_server_checks(self):
        results = self.runner.build_check(11636, "", None, self.key_file)
        self.assertEqual(results, [("version", True, "11636")]
                         + [(name, None, "no last model") for name in
                            ("health", "chat with key", "chat without key", "tools without key",
                             "cors-proxy without key", "context (--fit)")])
        self.assertEqual(self.popened, [])
        self.assertIn("– health — skipped (no last model)", self.reported)
        self.assertTrue(launcher.check_verdict(results)[0])

    def test_process_killed_when_wait_healthy_raises(self):
        with mock.patch.object(launcher, "wait_healthy", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.runner.build_check(11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertIn(self.KILL, self.calls)

    # update() and rollback()

    def test_upgrade_failure_skips_build_check(self):
        self.exit_codes["upgrade"] = 1
        self.assertEqual(self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file),
                         (False, "WinGet upgrade failed (exit 1)."))
        self.assertEqual(self.popened, [])
        self.assertNotIn(self.INSTALL_9776, self.calls)
        self.assertIn("winget upgrade line", self.reported)

    def test_update_passes(self):
        wait, probe = self.patched()
        with wait, probe:
            self.assertEqual(self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file),
                             (True, "Build check passed."))
        self.assertNotIn(self.INSTALL_9776, self.calls)

    def test_failed_check_rolls_back_to_previous_build(self):
        self.versions = [11636, 9776]
        wait, probe = self.patched(probe=self.PROBE_LEAKY)
        with wait, probe:
            result = self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (False, "Build check failed: chat without key (HTTP 200). "
                                         "Rolled back to b9776."))
        self.assertIn(self.INSTALL_9776, self.calls)

    def test_failed_roll_back_is_reported(self):
        self.exit_codes["install"] = 1
        wait, probe = self.patched(probe=self.PROBE_LEAKY)
        with wait, probe:
            result = self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (False, "Build check failed: chat without key (HTTP 200). "
                                         "Roll back to b9776 also failed: WinGet install failed (exit 1)."))

    def test_build_check_error_still_rolls_back(self):
        self.versions = [11636, 9776]
        with mock.patch.object(launcher, "wait_healthy", side_effect=RuntimeError("boom")):
            result = self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (False, "Build check failed: build check (boom). Rolled back to b9776."))
        self.assertIn(self.KILL, self.calls)
        # The dialog's log must show the error too, not only the status line.
        self.assertIn("✗ build check — boom", self.reported)

    def test_rollback_confirms_version(self):
        self.versions = [11636]
        self.assertEqual(self.runner.rollback(9776),
                         (False, "llama-server reports 11636 after the install, expected 9776."))
        self.versions = [9776]
        self.assertEqual(self.runner.rollback(9776), (True, "Rolled back to b9776."))

    # rollback_and_check() (Settings)

    def test_failed_install_skips_build_check(self):
        self.exit_codes["install"] = 1
        self.assertEqual(self.runner.rollback_and_check(9776, "C:\\m\\a.gguf", None, self.key_file),
                         (False, "WinGet install failed (exit 1)."))
        self.assertEqual(self.popened, [])

    def test_roll_back_then_build_check(self):
        self.versions = [9776, 9776]
        wait, probe = self.patched()
        with wait, probe:
            self.assertEqual(self.runner.rollback_and_check(9776, "C:\\m\\a.gguf", None, self.key_file),
                             (True, "Rolled back to b9776. Build check passed."))

    def test_failed_check_after_roll_back_only_reports(self):
        self.versions = [9776, 9776]
        wait, probe = self.patched(probe=self.PROBE_LEAKY)
        with wait, probe:
            result = self.runner.rollback_and_check(9776, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (True, "Rolled back to b9776. "
                                        "Build check failed: chat without key (HTTP 200)."))
        self.assertEqual(self.calls.count(self.INSTALL_9776), 1)

    def test_build_check_error_after_roll_back_is_reported(self):
        self.versions = [9776, 9776]
        with mock.patch.object(launcher, "wait_healthy", side_effect=RuntimeError("boom")):
            result = self.runner.rollback_and_check(9776, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (True, "Rolled back to b9776. Build check failed: build check (boom)."))


if __name__ == "__main__":
    unittest.main()
