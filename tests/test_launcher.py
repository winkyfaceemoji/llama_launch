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


class ModelTestHelperTests(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(_PATH), "eval", "model_test.py")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "file")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_command_runs_python_not_pythonw(self):
        # pythonw has no console streams, so the harness's output would never reach the dialog.
        for exe in ("C:\\Py\\pythonw.exe", "C:\\Py\\PythonW.EXE"):
            self.assertEqual(launcher.model_test_command(exe), ["C:\\Py\\python.exe", "-u", self.SCRIPT])

    def test_command_keeps_python(self):
        self.assertEqual(launcher.model_test_command("C:\\Py\\python.exe"),
                         ["C:\\Py\\python.exe", "-u", self.SCRIPT])

    def test_lock_holds_a_fresh_random_token(self):
        token = launcher.write_test_lock(self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), token)
        self.assertGreaterEqual(len(token), 32)
        self.assertNotEqual(launcher.write_test_lock(self.path), token)

    def test_clear_lock_removes_it(self):
        launcher.write_test_lock(self.path)
        launcher.clear_test_lock(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_clear_missing_lock_is_fine(self):
        launcher.clear_test_lock(self.path)

    def test_temp_dirs_are_the_stress_harness_folders(self):
        with mock.patch.dict(os.environ, {"TEMP": "C:\\T"}):
            self.assertEqual(launcher.test_temp_dirs(),
                             ["C:\\T\\agent-stress", "C:\\T\\agent-stress-outside"])

    def test_read_test_jobs(self):
        self.write('["20261015-120000-abcd", 5, "20261015-120001-ef01"]')
        self.assertEqual(launcher.read_test_jobs(self.path),
                         ["20261015-120000-abcd", "20261015-120001-ef01"])

    def test_read_test_jobs_missing_or_damaged_is_empty(self):
        self.assertEqual(launcher.read_test_jobs(self.path), [])
        for text in ("not json", '{"a": 1}', ""):
            self.write(text)
            self.assertEqual(launcher.read_test_jobs(self.path), [])


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

    def test_substitutes_guard_path(self):
        cmd = launcher.resolve_uvx_command('python "{guard}" --named-server-config "{config}"',
                                           r"D:\app\config.json", r"D:\app\mcp_guard.py")
        self.assertEqual(cmd, r'python "D:\app\mcp_guard.py" --named-server-config "D:\app\config.json"')


class MigrateUvxCommandTests(unittest.TestCase):
    OLD = 'uvx mcp-proxy --named-server-config "{config}" --port 8001 --stateless'

    def test_old_default_gets_the_host_check(self):
        # Saved configs keep their own command; only the untouched old default is swapped (ADR 0012).
        config = {"uvx_command": self.OLD}
        self.assertTrue(launcher.migrate_uvx_command(config))
        self.assertEqual(config["uvx_command"], launcher.UVX_COMMAND)
        self.assertIn("mcp_guard.py", launcher.UVX_COMMAND.replace("{guard}", "mcp_guard.py"))
        self.assertIn("mcp-proxy==0.13.0", launcher.UVX_COMMAND)

    def test_custom_or_missing_command_is_left_alone(self):
        for config in ({"uvx_command": self.OLD + " --allow-origin x"}, {}):
            before = dict(config)
            self.assertFalse(launcher.migrate_uvx_command(config))
            self.assertEqual(config, before)


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


class CloseWindowTests(unittest.TestCase):
    # _on_close on a stub: no window, no tray, no processes of the stack.
    def close(self, *procs):
        calls = []
        stub = mock.Mock(_llama_proc=procs[0], _uvx_proc=procs[1], _proxy_proc=procs[2],
                         _agents_proc=procs[3])
        stub._hide_to_tray.side_effect = lambda: calls.append("hide")
        stub._kill_all.side_effect = lambda: calls.append("kill_all")
        stub.master.destroy.side_effect = lambda: calls.append("destroy")
        with mock.patch.object(launcher, "_TRAY_AVAILABLE", True):
            launcher.CommandLauncherGUI._on_close(stub)
        return calls

    def test_hides_to_tray_while_a_server_runs(self):
        dead = mock.Mock(**{"poll.return_value": 1})
        alive = mock.Mock(**{"poll.return_value": None})
        self.assertEqual(self.close(dead, alive, dead, None), ["hide"])

    def test_exits_when_every_server_has_exited(self):
        # A second launcher's _free_port kills this one's servers; closing it must not
        # leave it in the tray holding dead handles.
        dead = mock.Mock(**{"poll.return_value": 1})
        self.assertEqual(self.close(dead, dead, dead, None), ["kill_all", "destroy"])


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
        self.assertFalse(launcher.remove_stale_agents_entry({"mcpServers": None}))  # hand-edited config


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

    def test_context_line_of_newer_builds_counts(self):
        # b11636 hides "new slot, n_ctx = ..." (trace level); its info line says n_ctx_slot.
        line = "0.00.512.004 I srv  load_model: initializing, n_slots = 4, n_ctx_slot = 32768, kv_unified = 'true'"
        self.server_log = [line + "\n"]
        wait, probe = self.patched()
        with wait, probe:
            results = self.runner.build_check(11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(results[-1], ("context (--fit)", True, line))

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
        self.assertFalse(self.runner.rolled_back)

    def test_failed_check_rolls_back_to_previous_build(self):
        self.versions = [11636, 9776]
        wait, probe = self.patched(probe=self.PROBE_LEAKY)
        with wait, probe:
            result = self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (False, "Build check failed: chat without key (HTTP 200). "
                                         "Rolled back to b9776."))
        self.assertIn(self.INSTALL_9776, self.calls)
        # Back on the previous build: Settings must not offer to roll back to the build in use.
        self.assertTrue(self.runner.rolled_back)

    def test_failed_roll_back_is_reported(self):
        self.exit_codes["install"] = 1
        wait, probe = self.patched(probe=self.PROBE_LEAKY)
        with wait, probe:
            result = self.runner.update(9776, 11636, "C:\\m\\a.gguf", None, self.key_file)
        self.assertEqual(result, (False, "Build check failed: chat without key (HTTP 200). "
                                         "Roll back to b9776 also failed: WinGet install failed (exit 1)."))
        # Still on the broken build: Settings' Roll back is the way out, so keep it.
        self.assertFalse(self.runner.rolled_back)

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


def contrast(fg, bg):
    # WCAG contrast ratio of two "#rrggbb" colours (1 to 21).
    def luminance(colour):
        def channel(c):
            c = int(c, 16) / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (channel(colour[i:i + 2]) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class PaletteTests(unittest.TestCase):
    def test_palettes_have_the_same_roles(self):
        self.assertEqual(set(launcher.PALETTES["light"]), set(launcher.PALETTES["dark"]))

    def test_colours_are_lowercase_hex_and_unique_per_palette(self):
        # remap_colors finds a widget's role by its colour, so no two roles may share one.
        for name, palette in launcher.PALETTES.items():
            for role, colour in palette.items():
                self.assertRegex(colour, r"^#[0-9a-f]{6}$", f"{name} {role}")
            self.assertEqual(len(set(palette.values())), len(palette), name)

    def test_text_contrast(self):
        for name, p in launcher.PALETTES.items():
            for bg in ("BG", "BG_PANEL", "BG_INPUT"):
                self.assertGreaterEqual(contrast(p["TEXT_PRI"], p[bg]), 7, f"{name} TEXT_PRI on {bg}")
                self.assertGreaterEqual(contrast(p["TEXT_MUT"], p[bg]), 4.5, f"{name} TEXT_MUT on {bg}")
            for fill in ("SUCCESS", "DANGER", "ACCENT"):
                self.assertGreaterEqual(contrast(p["BG"], p[fill]), 4.5, f"{name} BG on {fill}")

    def test_contrast_of_black_and_white(self):
        self.assertAlmostEqual(contrast("#000000", "#ffffff"), 21)
        self.assertAlmostEqual(contrast("#ffffff", "#000000"), 21)

    def test_set_palette_rebinds_the_constants(self):
        try:
            launcher.set_palette("light")
            self.assertEqual(launcher.BG, launcher.PALETTES["light"]["BG"])
            launcher.set_palette("dark")
            self.assertEqual(launcher.BG, launcher.PALETTES["dark"]["BG"])
        finally:
            launcher.set_palette("dark")


class ThemeTests(unittest.TestCase):
    def test_explicit_setting_wins(self):
        self.assertEqual(launcher.resolve_theme("light", False), "light")
        self.assertEqual(launcher.resolve_theme("dark", True), "dark")

    def test_system_follows_windows(self):
        self.assertEqual(launcher.resolve_theme("system", True), "light")
        self.assertEqual(launcher.resolve_theme("system", False), "dark")

    def test_unreadable_registry_counts_as_light(self):
        self.assertEqual(launcher.resolve_theme("system", None), "light")

    def test_unknown_setting_counts_as_system(self):
        self.assertEqual(launcher.resolve_theme("purple", False), "dark")
        self.assertEqual(launcher.resolve_theme(None, True), "light")

    def test_theme_setting_from_config(self):
        self.assertEqual(launcher.theme_setting({}), "system")
        self.assertEqual(launcher.theme_setting({"theme": "dark"}), "dark")
        self.assertEqual(launcher.theme_setting({"theme": "Dark"}), "system")
        self.assertEqual(launcher.theme_setting({"theme": 3}), "system")


class RemapColorsTests(unittest.TestCase):
    OLD = {"BG": "#000000", "TEXT_PRI": "#ffffff"}
    NEW = {"BG": "#fefefe", "TEXT_PRI": "#0a0a0a"}

    def test_maps_each_colour_to_its_role_in_the_new_palette(self):
        self.assertEqual(launcher.remap_colors({"bg": "#000000", "fg": "#FFFFFF"}, self.OLD, self.NEW),
                         {"bg": "#fefefe", "fg": "#0a0a0a"})

    def test_leaves_other_values_alone(self):
        options = {"bg": "", "fg": "SystemButtonFace", "highlightcolor": "#123456"}
        self.assertEqual(launcher.remap_colors(options, self.OLD, self.NEW), {})


if __name__ == "__main__":
    unittest.main()
