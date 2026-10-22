"""llama.cpp updates through WinGet and the build check (no tkinter)."""
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from .selection import AUTO_COMMAND, build_launch_command


# llama.cpp's WinGet package (a portable Vulkan zip on PATH).
WINGET_ID = "ggml.llamacpp"


def parse_build(text):
    # "b11636", "Version: b11636" or "version: 9776 (ac4105d68)" -> 11636 / 9776.
    # Matches any line, since llama-server prints device lines before its version.
    # Newer builds print "version: 0.6.0-dev (build 11636, commit ...)": the build wins.
    m = re.search(r"\(build (\d+)\b", text, re.IGNORECASE) or re.search(r"^\s*(?:b|version:\s*b?)(\d+)\b", text, re.IGNORECASE | re.MULTILINE)
    return int(m.group(1)) if m else None


def parse_winget_list(output):
    # (installed, available) from `winget list`'s row for llama.cpp; available is
    # None when the build is current. None when there's no row (an error message).
    # Read by position after the Id, so a localized header doesn't matter.
    for line in output.splitlines():
        parts = line.split()
        if WINGET_ID not in parts:
            continue
        rest = parts[parts.index(WINGET_ID) + 1:]
        installed = parse_build(rest[0]) if rest else None
        if installed is None:
            return None
        return installed, parse_build(rest[1]) if len(rest) > 1 else None
    return None


def parse_winget_show(output):
    # Newest build from `winget show`'s "Version: bNNNN" line, or None.
    for line in output.splitlines():
        if line.strip().lower().startswith("version:"):
            return parse_build(line)
    return None


def winget_args(action, build=None):
    # Read-only calls (list, show) never accept terms; upgrade and install do,
    # only after the user's click. --disable-interactivity makes a prompt fail
    # instead of hanging.
    args = ["winget", action, "--id", WINGET_ID, "--exact",
            "--source", "winget", "--disable-interactivity"]
    if action == "install":
        args += ["--version", f"b{build}", "--force"]
    if action in ("upgrade", "install"):
        args += ["--silent", "--accept-package-agreements", "--accept-source-agreements"]
    return args


def check_verdict(results):
    # results: ordered (check name, ok, detail); ok is None for a skipped check.
    # Returns (passed, message) naming the first failure, or the skipped checks.
    for name, ok, detail in results:
        if ok is False:
            return False, f"Build check failed: {name} ({detail})."
    skipped = [name for name, ok, _ in results if ok is None]
    if skipped:
        return True, f"Build check passed. Skipped: {', '.join(skipped)}."
    return True, "Build check passed."


def _http(url, timeout, data=None, headers=None):
    # (status, body) for any HTTP answer; error statuses are returned, not raised.
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read()


def probe_server(base, key, origin="http://evil.example", timeout=10):
    # Build check against a running llama-server: chat answers with the key, and
    # without it chat, /tools and /cors-proxy don't, even for a foreign Origin
    # (ADR 0005). Returns [(check name, ok, detail)] for check_verdict.
    chat = json.dumps({"messages": [{"role": "user", "content": "Say hi."}],
                       "max_tokens": 8}).encode()
    json_type = {"Content-Type": "application/json"}

    def chat_with_key():
        status, body = _http(base + "/v1/chat/completions", timeout, chat,
                             {**json_type, "Authorization": f"Bearer {key}"})
        if status != 200:
            return False, f"HTTP {status}"
        msg = json.loads(body)["choices"][0]["message"]
        # A thinking model can spend all 8 tokens on reasoning, leaving content empty.
        text = msg.get("content") or msg.get("reasoning_content")
        return bool(text), repr(text)

    def without_key(path, data):
        status, _ = _http(base + path, timeout, data, {**json_type, "Origin": origin})
        # 404 passes: the endpoint isn't enabled on this build, so nothing answers.
        return status in (401, 403, 404), f"HTTP {status}"

    checks = [
        ("chat with key", chat_with_key),
        ("chat without key", lambda: without_key("/v1/chat/completions", chat)),
        ("tools without key", lambda: without_key("/tools", b"{}")),
        ("cors-proxy without key", lambda: without_key("/cors-proxy?url=http://127.0.0.1:1/", None)),
    ]
    results = []
    for name, check in checks:
        try:
            ok, detail = check()
        except Exception as e:  # connection refused, timeout, bad JSON
            ok, detail = False, str(e) or type(e).__name__
        results.append((name, ok, detail))
    return results


def wait_healthy(base, timeout, alive=lambda: True):
    # Polls /health once a second until {"status": "ok"}; False at timeout, or as
    # soon as alive() says the server process has exited.
    deadline = time.monotonic() + timeout
    while alive():
        try:
            status, body = _http(base + "/health", 2)
            if status == 200 and json.loads(body).get("status") == "ok":
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)
    return False


# probe_server's check names, failed together when the server never got healthy.
_PROBE_CHECKS = ("chat with key", "chat without key", "tools without key", "cors-proxy without key")


_CHECK_PORT = 18081  # the build check's spare port; the real servers keep 8081


_MARKS = {True: "✓", False: "✗", None: "–"}


class UpdateRunner:
    # Runs WinGet and the build check from a background thread. report(line) is the
    # only output: every WinGet line and every check result. No tkinter in here;
    # run and popen are injectable so tests never touch WinGet, the GPU or ports.
    # config.json's previous_build is the GUI's job.

    def __init__(self, report, run=subprocess.run, popen=subprocess.Popen):
        self._report, self._run, self._popen = report, run, popen
        self.rolled_back = False  # update() put the previous build back

    def _text(self, args, timeout, **kw):
        # (exit code, combined output); decoded so an odd byte can't raise.
        # CREATE_NO_WINDOW: WinGet runs without a shell, so under pythonw it would flash a console.
        r = self._run(args, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW, **kw)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def _winget(self, action, build=None):
        # Runs an upgrade or install, reporting its output; returns an error or None.
        try:
            code, out = self._text(winget_args(action, build), 1800)
        except Exception as e:
            return f"WinGet {action} failed ({e})."
        for line in out.splitlines():
            if line.strip():
                self._report(line)
        return f"WinGet {action} failed (exit {code})." if code else None

    def _version(self):
        try:
            return parse_build(self._text("llama-server --version", 60, shell=True)[1])
        except Exception:
            return None

    def check(self):
        # (installed, newest) when WinGet lists a newer build; None when the build
        # is current or anything goes wrong, so the GUI shows nothing.
        try:
            code, out = self._text(winget_args("list"), 60)
        except Exception:
            return None
        found = parse_winget_list(out) if code == 0 else None
        return found if found and found[1] is not None else None

    def build_check(self, expected, model_path, mmproj, key_file):
        # The spec's five checks, in order, as [(name, ok, detail)] for check_verdict.
        results = []

        def add(name, ok, detail):
            results.append((name, ok, detail))
            self._report(f"{_MARKS[ok]} {name} — {detail if ok is not None else f'skipped ({detail})'}")

        version = self._version()
        add("version", version == expected,
            str(version) if version == expected else f"{version}, expected {expected}")
        later = ("health",) + _PROBE_CHECKS + ("context (--fit)",)
        if not model_path:
            for name in later:
                add(name, None, "no last model")
            return results

        with open(key_file, encoding="utf-8") as f:
            key = f.read().strip()
        cmd = build_launch_command(AUTO_COMMAND, model_path, mmproj, key_file)
        assert " --port 8081" in cmd
        cmd = cmd.replace(" --port 8081", f" --port {_CHECK_PORT}")
        base = f"http://127.0.0.1:{_CHECK_PORT}"
        proc = self._popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding="utf-8", errors="replace")
        n_ctx, last = [], []

        def reader():
            # Keeps draining the pipe (see the launcher rule), keeps the context line
            # and the last few lines, which say why a server that never got healthy failed.
            try:
                for line in proc.stdout:
                    # b9776 logs "n_ctx = N"; b11636 only "n_ctx_slot = N" at its default verbosity.
                    if not n_ctx and re.search(r"\bn_ctx(_slot)?\s*=", line):
                        n_ctx.append(line.strip())
                    last[:] = (last + [line.rstrip()])[-5:]
            except Exception:
                pass
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            healthy = wait_healthy(base, 300, alive=lambda: proc.poll() is None)
            add("health", healthy, "ok" if healthy else "not ok (server exited or 300 s passed)")
            if healthy:
                for result in probe_server(base, key):
                    add(*result)
            else:
                thread.join(2)
                for line in last:
                    self._report(f"  llama-server: {line}")
                for name in _PROBE_CHECKS:
                    add(name, False, "server never became healthy")
        finally:
            # shell=True makes proc.pid cmd.exe; /T takes llama-server with it.
            self._run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
            thread.join(5)
        add("context (--fit)", bool(n_ctx), n_ctx[0] if n_ctx else "no n_ctx line in the log")
        return results

    def update(self, previous, newest, model_path, mmproj, key_file):
        # WinGet upgrade, then the build check; a failed check rolls back to previous.
        # Returns (passed, message).
        error = self._winget("upgrade")
        if error:
            return False, error
        passed, message = self._verdict(newest, model_path, mmproj, key_file)
        if passed:
            return True, message
        ok, rolled = self.rollback(previous)
        if ok:
            self.rolled_back = True
            return False, f"{message} Rolled back to b{previous}."
        return False, f"{message} Roll back to b{previous} also failed: {rolled}"

    def _verdict(self, expected, model_path, mmproj, key_file):
        try:
            results = self.build_check(expected, model_path, mmproj, key_file)
        except Exception as e:
            results = [("build check", False, str(e) or type(e).__name__)]
            self._report(f"{_MARKS[False]} build check — {results[0][2]}")
        return check_verdict(results)

    def rollback_and_check(self, build, model_path, mmproj, key_file):
        # Settings' roll back: reinstall build, then a build check that only reports
        # (there's no older build to fall back to). (rolled back, message).
        ok, message = self.rollback(build)
        if not ok:
            return False, message
        return True, f"{message} {self._verdict(build, model_path, mmproj, key_file)[1]}"

    def rollback(self, build):
        # Reinstalls build through WinGet and confirms llama-server reports it.
        error = self._winget("install", build)
        if error:
            return False, error
        version = self._version()
        if version != build:
            return False, f"llama-server reports {version} after the install, expected {build}."
        return True, f"Rolled back to b{build}."
