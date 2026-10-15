# llama.cpp Updates Implementation Plan

> **For agentic workers:** implement task by task, test first (`tdd`), one implementer subagent per task plus a reviewer. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The launcher tells you at startup when a newer llama.cpp build is out, updates it through WinGet on your click, checks the new build, and rolls back automatically if the check fails. Roll back is also available from Settings.

**Architecture:**
- **Pure helpers** (`launcher.pyw`, new `SECTION 3c — LLAMA.CPP UPDATES`): parse WinGet and `llama-server --version` output, compare builds, build WinGet argument lists, turn check results into a verdict.
- **Probe:** `probe_server(base, key)` sends the build check's HTTP requests to a running llama-server and returns one result per check. It's tested against a fake server on a spare port.
- **Runner:** `UpdateRunner` runs WinGet and the build check in a background thread, reporting lines to a callback. No tkinter inside it.
- **GUI:** an update notice row on the home screen, an `UpdateDialog` that shows progress, and a Roll back row in Settings.

**Tech stack:** Python standard library only (`subprocess`, `threading`, `urllib`, `http.server` in tests).

**Spec:** `docs/superpowers/specs/2026-10-15-llama-updates.md`. Terms: `CONTEXT.md` (build, update, previous build, roll back, build check).

## Global constraints

- **Branch:** `llama-updates`. At the end, merge to `main`, push and delete the branch. No PR. No Claude/Anthropic attribution in commits.
- **No WinGet install, upgrade or uninstall before Task 6.** Task 6 runs them only with the user's go-ahead in chat. `winget list` and `winget show` are read-only and fine.
- **No GPU before Task 6.** The build check loads a model; ask the user first (they sometimes need the GPU).
- **Ports:** never bind or touch 8001, 8002, 8080 or 8081. The build check uses `18081`, and tests use other spare ports (`18090`+). Stop whatever you start.
- **WinGet calls:**
  - Always pass `--id ggml.llamacpp --exact --source winget --disable-interactivity`. A prompt must make WinGet fail, never hang.
  - Read-only calls never pass `--accept-*` flags.
  - Install and upgrade calls pass `--silent --accept-package-agreements --accept-source-agreements`, only after the user clicks. The notice says that updating accepts the package's terms.
- **Output formats** (WinGet 1.x, verified 2026-10-15):
  - `winget list --id ggml.llamacpp --exact` prints a table whose data row is `llama.cpp ggml.llamacpp b9776   b11636    winget`. The `Available` column is empty when the build is current.
  - `winget show --id ggml.llamacpp --exact` prints `Version: b11636` on its second line.
  - `llama-server --version` prints `version: 9776 (ac4105d68)`.
  - Lines end in `\r\n`.
- **Updates only while the servers are stopped:** Windows locks a running `llama-server.exe`. The Update button and Roll back are disabled in the running state.
- **Config:** `config.json` gets `previous_build` (e.g. `"b9776"`), set right before an update starts. It's cleared after a successful roll back, so the startup notice offers the newer build again. No other new keys.
- **Code layout:** `launcher.pyw` keeps its `# SECTION N` banners. Pure helpers go in the new section 3c; dialogs in section 7; home-screen wiring in section 8.
- **Tests:** `python -m unittest discover -s tests` (launcher tests load the file with `SourceFileLoader`). Syntax: `python -m py_compile launcher.pyw proxy.py`.

## Review focus

1. **WinGet output in odd shapes:** no `Available` column value, extra spaces, `\r\n`, a localized header, or an error message instead of a table. The parser returns `None` and no notice is shown, never a crash or a wrong build number (Task 1).
2. **The build check can't pass a build that breaks ADR 0005:** a server answering chat without the key fails check 4 (Task 2, against a fake server that does exactly that).
3. **A failed check rolls back, and a failed roll back is reported**, not hidden. `previous_build` stays recorded so Settings can retry (Task 3).
4. **Nothing runs WinGet without a click,** and nothing updates while the servers run (Tasks 4 and 5).
5. **The build check always stops its llama-server**, including on timeout or exception (`try`/`finally`, `taskkill /T`) (Task 3).

---

## File structure

| File | Change |
|---|---|
| `launcher.pyw` | New section 3c (helpers, `probe_server`, `UpdateRunner`); `UpdateDialog` in section 7; Roll back row in `SettingsDialog`; update notice and startup check in section 8 |
| `tests/test_launcher.py` | Parser, verdict and WinGet-argument tests |
| `tests/test_launcher_probe.py` (new) | `probe_server` against fake llama-servers on spare ports |
| `README.md`, `.claude/rules/launcher.md`, `CLAUDE.md` | Docs |

---

### Task 1: Pure helpers

**Files:** `launcher.pyw` (section 3c), `tests/test_launcher.py`

- [ ] Write failing tests first, then implement:

```python
WINGET_ID = "ggml.llamacpp"

def parse_build(text):
    # "b11636", "version: 9776 (ac4105d68)" or "Version: b11636" -> 11636; anything else -> None.

def parse_winget_list(output):
    # -> (installed, available) as ints; available None when current. None when the row is missing.

def parse_winget_show(output):
    # -> newest build as an int from the "Version: bNNNN" line, or None.

def winget_args(action, build=None):
    # action "list" / "show": read-only, no --accept flags.
    # "upgrade": --silent and both --accept flags.
    # "install" with build: --version b<build> --force, --silent, both --accept flags.
    # Always: ["winget", action, "--id", WINGET_ID, "--exact", "--source", "winget", "--disable-interactivity", ...]

def check_verdict(results):
    # results: ordered list of (check name, ok: bool | None for skipped, detail).
    # -> (passed: bool, message) where message names the first failed check and its detail,
    #    or says which checks were skipped.
```

- [ ] Tests:
  - `parse_build` on the three real shapes, `"b"`, `""`, and `"version: abc"`.
  - `parse_winget_list` on the real table (with `\r\n`), a table where the build is current, `"No installed package found matching input criteria."`, and a row with extra columns.
  - `parse_winget_show` on the real output and on an error message.
  - `winget_args`: read-only calls carry no `--accept`; install carries `--version b9776 --force`; every call carries `--disable-interactivity`.
  - `check_verdict`: all pass; second check fails (the message names it); skipped checks are listed.
- [ ] Commit: `launcher: parse winget and llama-server versions`

### Task 2: Build check probe

**Files:** `launcher.pyw` (section 3c), `tests/test_launcher_probe.py`

- [ ] `probe_server(base, key, origin="http://evil.example", timeout=10)` returns results for checks 3 and 4 of the spec, in order:
  1. `chat with key`: `POST /v1/chat/completions` with the key and a one-line prompt (`max_tokens` 8). Passes on 200 with non-empty choice text.
  2. `chat without key`, `tools without key`, `cors-proxy without key`: each request sent without the key and with the foreign `Origin`. Passes on 401, 403 or 404. A 200, or any other 2xx, fails, and the detail quotes the status.
- [ ] `wait_healthy(base, timeout)`: polls `/health` once a second. True on `{"status": "ok"}`; False at timeout.
- [ ] Tests run fake servers (`ThreadingHTTPServer` on spare ports, started in `setUp` and shut down in `tearDown`):
  - A "good" server: 401 without the key, a chat reply with it. All checks pass.
  - A "leaky" server: it answers chat without the key. `chat without key` fails and `check_verdict` names it.
  - A server stuck at 503: `wait_healthy` returns False within a short timeout.
- [ ] Commit: `launcher: build check probe`

### Task 3: Update runner

**Files:** `launcher.pyw` (section 3c), `tests/test_launcher.py`

- [ ] `UpdateRunner(report, run=subprocess.run, popen=subprocess.Popen)` has these methods, each meant for a background thread:
  - `check()` returns `(installed, newest)` from `winget list`, or `None` on any error. Timeout 60 s.
  - `update(previous, model_path, mmproj, key_file)` runs `winget upgrade`, then the build check. On a failed check it runs `rollback(previous)`. It returns the verdict.
  - `rollback(build)` runs `winget install ... --version b<build> --force` and then `llama-server --version` to confirm.
  - `build_check(model_path, mmproj, key_file)`:
    1. `llama-server --version` reports the new build.
    2. Without a model, the remaining checks are skipped.
    3. Otherwise it starts `build_launch_command(AUTO_COMMAND, ...)` with `--port 8081` swapped for `--port 18081`, reading the log for `n_ctx = `.
    4. It calls `wait_healthy` (limit 300 s), then `probe_server`.
    5. It always stops the process in `finally` with `taskkill /F /T /PID`.
- [ ] `report(line)` receives every WinGet output line and every check result. It is the only output channel.
- [ ] Tests use fake `run` and `popen`, with no WinGet, no GPU and no ports:
  - The upgrade fails (non-zero exit): no build check runs, and the verdict reports the WinGet error.
  - The build check fails: `rollback` runs with the previous build.
  - The roll back also fails: the verdict says both failed, and `previous_build` is not cleared.
  - The build check process always gets killed, including when `wait_healthy` raises.
- [ ] Commit: `launcher: update runner with build check and roll back`

### Task 4: Startup notice and update dialog

**Files:** `launcher.pyw` (sections 7 and 8)

- [ ] On the home screen, a background thread runs `UpdateRunner.check()` once at startup. When `newest > installed`, it shows a row under the header: `llama.cpp b<newest> is out (you have b<installed>). Updating accepts the package's terms.` with an **Update** button. Errors show nothing. UI changes go through `after(0, ...)`.
- [ ] **Update**:
  1. Saves `previous_build = b<installed>` to the config.
  2. Opens `UpdateDialog`, a modal with a read-only log, a status line and Close. Close stays disabled while working.
  3. Runs `update(...)` in a thread with the selected model.
  4. On success it hides the notice. On a roll back it shows the failed check.
- [ ] The Update button is disabled while in the running state (`_enter_running_state` and `_exit_running_state`).
- [ ] Manual check (no WinGet run): use a temporary fake `check()` result to see the notice and the dialog layout. Remove the fake before committing.
- [ ] Commit: `launcher: update notice and dialog`

### Task 5: Roll back in Settings; docs

**Files:** `launcher.pyw` (section 7), `README.md`, `.claude/rules/launcher.md`, `CLAUDE.md`

- [ ] `SettingsDialog` gets an `on_rollback` callback and `previous_build`. When both are set and the servers are stopped, it shows a row: `llama.cpp` with the button `Roll back to b<N>`.
  - Clicking asks for confirmation (`messagebox.askyesno`).
  - It then opens `UpdateDialog`, running `rollback` and then the build check, which only reports here and never rolls back further.
  - On success it clears `previous_build`.
- [ ] Docs:
  - README: an "Updating llama.cpp" section covering the notice, what the build check tests, roll back, the WinGet requirement, and that updating accepts the package's terms.
  - Launcher rule: WinGet flags, servers stopped, the probe's spare port, always killing the check's process, and that `previous_build` is the only state.
  - CLAUDE.md: nothing, unless a new project-wide gotcha appears.
- [ ] Commit: `launcher: roll back from settings; docs`

### Task 6: Live check with the user, merge and push

- [ ] Ask the user before each step. Each one downloads about 90 MB and uses the GPU for about 30 s:
  1. Start the launcher with the servers stopped. The notice shows b9776 → b11636.
  2. Click Update. WinGet upgrades, the build check passes (all five checks listed), and `llama-server --version` says 11636.
  3. In Settings, roll back to b9776. The build check passes and `previous_build` is cleared.
  4. Restart the launcher. The notice is back; update again to b11636.
  5. Launch normally, and check that chat, the Jobs panel and the agents server still work on the new build.
- [ ] If the roll back command fails on a portable package, stop, report it, and record the working command (or an ADR if WinGet can't roll back) before merging.
- [ ] Full test suite, `py_compile`, merge `llama-updates` to `main`, push, delete the branch.
