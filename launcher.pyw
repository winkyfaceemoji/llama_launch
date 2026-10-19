# ============================================================
# launcher.py  —  Llama Server Launcher
# ============================================================
#
# WHAT THIS DOES
# --------------
# This is a desktop GUI that lets you launch two servers with one click:
#
#   1. llama-server  — runs a local AI model (a .gguf file) and exposes it
#                      as an API on port 8081. The model and launch options
#                      are controlled by the "preset" you select.
#
#   2. UVX MCP Proxy — a middleware server on port 8001 that bridges the
#                      AI model's API to tools that speak the MCP protocol
#                      (e.g. Claude Desktop, Cursor).
#
# HOW IT'S STRUCTURED
# --------------------
# The file is divided into four layers:
#
#   1. CONFIGURATION  (top of file)
#      Colors, fonts, window sizes, file paths, and the default presets
#      that ship with the launcher. These are the only values you'd need
#      to change to restyle the app or add new default presets.
#
#   2. HELPER FUNCTIONS  (load_presets, detect_models, etc.)
#      Small standalone utilities that handle file I/O, model detection,
#      and building the shared visual components (buttons, cards, labels).
#      They don't depend on each other and have no side effects.
#
#   3. UI COMPONENT CLASSES  (_MenuProxy, DropdownButton, PresetDialog)
#      Self-contained widgets used by the main window:
#        - DropdownButton  : a fully-themed dropdown (the model/preset selectors)
#        - PresetDialog    : the pop-up form for adding or editing a preset
#        - _MenuProxy      : an internal helper that lets DropdownButton behave
#                            like a standard menu without using the OS menu widget
#
#   4. MAIN APPLICATION CLASS  (CommandLauncherGUI)
#      Builds the window, wires up all the widgets, and owns the two-state
#      UI flow:
#        - Setup state    : first-run screen to configure the llama.cpp folder
#        - Config state   : model picker, preset picker, UVX command, Launch button
#        - Running state  : status log + Exit Processes button
#      It also owns the launched processes and handles starting/killing them.
#
# TWO-STATE WINDOW FLOW
# ---------------------
# The window has two distinct views that swap on Launch / Exit:
#
#   [ Setup view ]   →  shown on first run, asks for the llama.cpp folder path.
#                        Required before the home screen is accessible.
#                        Can be reopened at any time via the Settings button.
#
#   [ Config view ]  →  user picks model + preset, optionally edits UVX command,
#                        then clicks "Launch Server"
#
#   [ Running view ] →  config widgets are hidden, the status log appears showing
#                        which processes launched and their PIDs. "Exit Processes"
#                        kills both servers and returns to config view.
#
# PRESET SYSTEM
# -------------
# Presets are stored in config.json next to this file. Each preset has:
#   - A name
#   - A command template  (uses {model} as a placeholder for the model path)
#   - A list of model names  (only shown when the selected model matches one;
#     an empty list matches every model)
#
# The "Auto" preset always exists and is pre-selected whenever a model is
# picked. It leaves context size and GPU layers unset so llama-server's --fit
# sizes them to the free VRAM. Other presets are optional manual overrides.
# Selecting a preset builds the final launch command by substituting {model}
# with the actual file path (and adding --mmproj for vision models).
#
# ============================================================
# ── Standard library imports
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
import os
import sys
import json
import webbrowser
import re
import secrets
import shutil
import pathlib
import threading
import urllib.request
import time
import ctypes
import winreg
from agents import jobs as agent_jobs, paths as agent_paths, llm as agent_llm


# ── System tray bootstrap (pystray + Pillow)
def _bootstrap():
    import importlib.util, subprocess, sys
    for mod, pkg in [("pystray", "pystray"), ("PIL", "Pillow")]:
        if importlib.util.find_spec(mod) is None:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
try:
    _bootstrap()
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except Exception:
    _TRAY_AVAILABLE = False


# ============================================================
# SECTION 1 — VISUAL CONFIGURATION
# All colors, fonts, and window dimensions live here so they're
# easy to find and change without touching any logic.
# ============================================================

# Neutral greys from the llama Web UI, light and dark. set_palette() rebinds the
# module constants (BG, TEXT_PRI, ...) to one of them. Every colour must be unique
# within its palette: apply_theme() finds a widget's role by its colour, which is why
# BG_PANEL is one step off BG.
PALETTES = {
    "light": {
        "BG":            "#ffffff",
        "BG_PANEL":      "#fefefe",   # boxes
        "BG_INPUT":      "#f5f5f5",
        "HOVER":         "#eeeeee",
        "BORDER":        "#e5e5e5",
        "ACCENT":        "#171717",   # primary: filled buttons, selection, links
        "TEXT_PRI":      "#0a0a0a",
        "TEXT_SEC":      "#404040",
        "TEXT_MUT":      "#6b6b6b",
        "SUCCESS":       "#15803d",
        "SUCCESS_HOVER": "#166534",
        "DANGER":        "#e7000b",
        "DANGER_HOVER":  "#c10007",
        "WARN":          "#b45309",
    },
    "dark": {
        "BG":            "#0d0d0d",
        "BG_PANEL":      "#0e0e0e",
        "BG_INPUT":      "#171717",
        "HOVER":         "#262626",
        "BORDER":        "#2e2e2e",
        "ACCENT":        "#e5e5e5",
        "TEXT_PRI":      "#fafafa",
        "TEXT_SEC":      "#d4d4d4",
        "TEXT_MUT":      "#a1a1a1",
        "SUCCESS":       "#22a35a",
        "SUCCESS_HOVER": "#1e8f4f",
        "DANGER":        "#ff6467",
        "DANGER_HOVER":  "#ff4d50",
        "WARN":          "#fbbf24",
    },
}
THEMES = ("system", "light", "dark")   # the Settings choice; resolve_theme() turns it into a palette name
_palette = {}                          # the current palette's colours, by role
CURRENT_THEME = None                   # "light" or "dark"


def set_palette(name):
    # Rebind the colour constants. Code reads them when it builds a widget, so set this first.
    global CURRENT_THEME
    _palette.clear()
    _palette.update(PALETTES[name])
    globals().update(_palette)
    CURRENT_THEME = name


set_palette("dark")

# Fonts
FONT_TITLE  = ("Segoe UI", 13, "bold")
FONT_SMALL  = ("Segoe UI", 9)
FONT_BTN    = ("Segoe UI", 10, "bold")
FONT_BTN_SM = ("Segoe UI", 9)
FONT_MONO   = ("Cascadia Code", 10)
FONT_LOG    = ("Cascadia Code", 10)

# Window dimensions — fixed size, no manual resizing
W           = 480
H_SETUP     = 280    # height of the first-run setup screen
H_CONFIG    = 270    # height of the main config view
H_NOTICE    = 70     # added while the llama.cpp update notice shows
H_SETTINGS      = 460   # settings dialog, UVX collapsed
H_SETTINGS_UVX  = 550   # settings dialog, UVX expanded
H_ROLLBACK      = 44    # added to both while the roll back row shows
W_SETTINGS      = 460   # settings dialog width
H_RUNNING   = 750   # height of the running/log view


# ============================================================
# SECTION 2 — FILE PATHS
# All paths are resolved relative to this script's location.
# ============================================================

MODELS_DIR  = "Models"  # resolved at runtime relative to llama_dir
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
# llama-server's API key. Without one, any web page open in the browser can
# call the server on localhost (it allows every origin). Gitignored.
API_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")

# The agents MCP server (ADR 0007). The launcher starts it on 127.0.0.1:8002
# (agent_llm.AGENTS_PORT) as a fourth process; its log goes to the UVX pane.
AGENTS_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "server.py")

# Starts mcp-proxy with a Host check against DNS rebinding (ADR 0012).
MCP_GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_guard.py")

# llama-server's own OpenAI-compatible API. Agents connect here directly,
# bypassing the system prompt proxy on 8080.
AGENT_API_URL = "http://127.0.0.1:8081/v1"

# The model test harness. While the lock exists the proxy pauses Web UI chat; it
# holds a random token that lets the test's own chat requests through.
MODEL_TEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "model_test.py")
MODEL_TEST_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_test.lock")


# ============================================================
# SECTION 3 — CONFIG, PRESET & MODEL FILE HELPERS
# ============================================================

# Leaves -c and -ngl unset so llama-server's --fit picks the largest context
# that fits in free VRAM. --context-shift and --swa-full stay off (both are
# off by default): a full chat stops instead of silently dropping messages.
# No --tools: newer builds only offer file, shell and OS tools, and refuse to
# start with an unknown one. The time comes from the time MCP server (ADR 0011).
AUTO_PRESET = "Auto (fit to VRAM)"
AUTO_COMMAND = (
    'llama-server -m "{model}" --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 '
    '--webui-mcp-proxy --port 8081'
)

_DEFAULT_CONFIG = {
    "mcpServers": {
        "fetch":      {"command": "uvx", "args": ["mcp-server-fetch"]},
        "ddg-search": {"command": "uvx", "args": ["duckduckgo-mcp-server"]},
        "time":       {"command": "uvx", "args": ["mcp-server-time"]},
    },
    "system_prompt": (
        "The current date is provided at the top of every message. "
        "Treat it as ground truth — do not estimate or assume the date from training memory.\n\n"
        "You have real-time web access via two MCP tools: ddg-search and fetch, "
        "plus a time MCP tool for the exact current time.\n\n"
        "TOOL USAGE RULES:\n"
        "1. For any question involving facts, current events, people, software versions, prices, "
        "or anything that may have changed — use ddg-search BEFORE generating a response. "
        "Do not answer from training memory alone.\n"
        "2. Your training data is outdated. If search results conflict with your training knowledge, "
        "always trust the search results. Never override web results with what you think you know.\n"
        "3. If a search result requires more detail, use fetch to read the full page content.\n"
        "4. Use the time tool only when you need the exact current time.\n"
        "5. Do not explain your tool usage to the user. Execute tools silently and respond based on what you find."
    ),
    "llama_dir": "",
    "last_model": "",
    "open_browser": True,
    "presets": {
        AUTO_PRESET: {"command": AUTO_COMMAND, "models": []},
    },
}


def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        except IOError:
            pass
        return dict(_DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error reading config file: {e}")
        return dict(_DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except IOError as e:
        messagebox.showerror("Save Error", f"Could not save settings:\n{e}")


def load_presets(config: dict) -> dict:
    """Load and validate presets with enhanced migration logic."""
    presets = config.get("presets", {})
    
    # Validate and migrate presets
    migrated = {}
    for name, value in presets.items():
        if not isinstance(value, dict):
            # Handle legacy string-based presets
            migrated[name] = {
                "command": value,
                "models": []
            }
        else:
            # Validate modern dict-based presets
            if not isinstance(value.get("command"), str):
                raise ValueError(f"Invalid preset '{name}': 'command' must be a string")
                
            models = value.get("models", [])
            if not isinstance(models, list):
                raise ValueError(f"Invalid preset '{name}': 'models' must be a list")
                
            # Ensure all model entries are strings
            if not all(isinstance(model, str) for model in models):
                raise ValueError(f"Invalid preset '{name}': all models must be strings")
                
            migrated[name] = {
                "command": value["command"],
                "models": models
            }

    # The Auto preset always exists, so every model has a working default. Its
    # command always comes from code: a copy saved in config.json would
    # otherwise keep an outdated command forever.
    migrated.pop(AUTO_PRESET, None)
    return {AUTO_PRESET: {"command": AUTO_COMMAND, "models": []}, **migrated}


def resolve_uvx_command(cmd, config_file, guard_file=MCP_GUARD):
    # {config} and {guard} let the saved UVX command survive moving the launcher folder.
    return cmd.replace("{config}", config_file).replace("{guard}", guard_file)


# mcp-proxy is pinned: mcp_guard hooks how it starts uvicorn (ADR 0012).
UVX_COMMAND = ('uv run --no-project --with mcp-proxy==0.13.0 python "{guard}" '
               '--named-server-config "{config}" --port 8001 --stateless')
_OLD_UVX_COMMAND = 'uvx mcp-proxy --named-server-config "{config}" --port 8001 --stateless'


def migrate_uvx_command(config):
    # Saved configs keep their own command, so only the untouched old default is
    # swapped for the guarded one; an edited command is the user's and stays.
    if config.get("uvx_command", "").strip() != _OLD_UVX_COMMAND:
        return False
    config["uvx_command"] = UVX_COMMAND
    return True


def remove_stale_agents_entry(config):
    # The agents server used to run inside mcp-proxy from config.json's mcpServers.
    # The launcher starts it itself now (ADR 0007), so drop that old entry.
    return (config.get("mcpServers") or {}).pop("agents", None) is not None


def health_status(exit_codes, health, shown=()):
    # exit_codes: {process name: return code, or None while running}.
    # health: "ok", "loading" or "offline", from llama-server's /health.
    # shown: optional processes whose exit was already shown once; they no longer hide llama's status.
    # Returns (status text, level) where level is ok / warn / error / idle.
    for name, code in exit_codes.items():
        if code is not None and name not in shown:
            return f"● {name} exited (code {code})", "error"
    if health == "ok":
        return "● online", "ok"
    if health == "loading":
        return "● loading model", "warn"
    return "● offline", "idle"


def listening_pids(netstat_output, port):
    # PIDs listening on exactly this port in `netstat -ano` output.
    pids = set()
    for line in netstat_output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            pids.add(parts[4])
    return pids


def is_own_process(image_name, command_line, app_dir):
    # Ours: any llama-server, or anything started with a path in this folder
    # (proxy.py, and mcp-proxy via the config path).
    norm = lambda s: s.lower().replace("/", "\\")
    return (image_name.lower() == "llama-server.exe"
            or norm(app_dir) in norm(command_line))


def load_api_key(path):
    # Returns the saved API key, creating a random one on first run.
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    return key


def build_launch_command(template, model_path, mmproj, api_key_file):
    # Substitute the model path, attach the vision projector when there is one,
    # and require the API key unless the preset already sets its own.
    cmd = template.replace("{model}", model_path)
    if mmproj and "--mmproj" not in cmd and not re.search(r"(^|\s)-mm\s", cmd):
        cmd += f' --mmproj "{mmproj}"'
    if "--api-key" not in cmd:
        cmd += f' --api-key-file "{api_key_file}"'
    return cmd


def _is_extra_shard(filename):
    # Split models load from their first shard; hide -00002-of-0000N and later.
    m = re.search(r"-(\d{5})-of-\d{5}\.gguf$", filename, re.IGNORECASE)
    return bool(m) and m.group(1) != "00001"


def detect_models(llama_dir=""):
    # Returns {display name: (model path, mmproj path or None)}.
    # Models live directly in the folder, or in a subfolder next to an
    # mmproj*.gguf projector (llama-server's router layout), which enables
    # image input. Projector files are never listed as models.
    models_dir = llama_dir if llama_dir else MODELS_DIR
    found = {}

    def scan(folder, with_mmproj):
        try:
            files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".gguf"))
        except OSError:
            return
        mmproj = None
        if with_mmproj:
            mmproj = next((os.path.join(folder, f) for f in files
                           if f.lower().startswith("mmproj")), None)
        for f in files:
            if f.lower().startswith("mmproj") or _is_extra_shard(f):
                continue
            found.setdefault(f[:-5], (os.path.join(folder, f), mmproj))

    if not os.path.isdir(models_dir):
        return found
    scan(models_dir, with_mmproj=False)
    for d in sorted(os.listdir(models_dir)):
        sub = os.path.join(models_dir, d)
        if os.path.isdir(sub):
            scan(sub, with_mmproj=True)
    return found


def preset_matches_model(preset, model_filename):
    # A preset matches if any of its keywords appear in the model filename.
    # A preset with no keywords matches all models.
    keywords = preset.get("models", [])
    if not keywords:
        return True
    lower = model_filename.lower()
    return any(kw.lower() in lower for kw in keywords)


def model_test_command(python_exe):
    # python.exe even when the launcher runs under pythonw.exe, which has no console
    # streams, so the harness's lines reach the dialog; -u sends each one at once.
    folder, name = os.path.split(python_exe)
    if name.lower() == "pythonw.exe":
        python_exe = os.path.join(folder, "python.exe")
    return [python_exe, "-u", MODEL_TEST]


def write_test_lock(path):
    # Pauses Web UI chat; the returned token lets the test's own requests through the proxy.
    token = secrets.token_urlsafe(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def clear_test_lock(path):
    # Missing is fine. Never fatal: startup and Exit Processes clear it again.
    try:
        agent_jobs._retry(lambda: os.remove(path))  # the proxy may be reading it
    except OSError:
        pass


def test_temp_dirs():
    # The stress harness's throwaway folders (BASE and OUTSIDE in eval/stress_agents.py).
    temp = os.environ["TEMP"]
    return [os.path.join(temp, "agent-stress"), os.path.join(temp, "agent-stress-outside")]


def read_test_jobs(path):
    # The model test's job IDs (agents/paths.TEST_JOBS_FILE); missing or damaged gives [].
    try:
        with open(path, encoding="utf-8") as f:
            ids = json.load(f)
    except (OSError, ValueError):
        return []
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


# ============================================================
# SECTION 3c — LLAMA.CPP UPDATES
# ============================================================

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


# ============================================================
# SECTION 4 — THEME & SHARED UI HELPERS
# ============================================================

def theme_setting(config):
    # The saved Settings choice; anything missing or unknown is "system".
    theme = config.get("theme")
    return theme if theme in THEMES else "system"


def resolve_theme(setting, windows_light):
    # "light"/"dark" win; otherwise follow Windows. windows_light is None when the registry
    # can't be read, which counts as light (Windows' default).
    if setting in ("light", "dark"):
        return setting
    return "dark" if windows_light is False else "light"


def remap_colors(options, old, new):
    # options: {option: value} read from a widget. Returns the options whose value is a
    # colour of the old palette, each mapped to the same role's colour in the new one.
    roles = {colour: role for role, colour in old.items()}
    changed = {}
    for option, value in options.items():
        role = roles.get(str(value).lower())
        if role:
            changed[option] = new[role]
    return changed


_COLOUR_OPTIONS = ("bg", "fg", "activebackground", "activeforeground", "disabledforeground",
                   "highlightbackground", "highlightcolor", "insertbackground",
                   "selectbackground", "selectforeground", "selectcolor", "troughcolor")


def windows_light():
    # Windows' app theme: True light, False dark, None if it can't be read.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return None


def set_dark_title_bar(win, dark):
    # Dark or light Windows title bar (DWMWA_USE_IMMERSIVE_DARK_MODE). Cosmetic: never raises.
    try:
        # Borderless windows (the dropdown list) have no title bar, and the frame-changed
        # call below would drop Tk's pending move and leave them at the screen's corner.
        if win.overrideredirect():
            return
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # Windows repaints the frame only when told it changed (SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED).
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass


def apply_theme(root, name):
    # Switch the palette and recolour every existing widget, dialogs included. Widgets carry
    # no role: each colour option holding an old-palette colour gets that role's new colour.
    old = dict(_palette)
    set_palette(name)
    apply_ttk_style()
    stack = [root]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        try:
            keys = set(widget.keys())
            options = {o: widget.cget(o) for o in _COLOUR_OPTIONS if o in keys}
            changed = remap_colors(options, old, _palette)
            if changed:
                widget.configure(**changed)
        except tk.TclError:
            pass  # ttk widgets: apply_ttk_style covers them
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            set_dark_title_bar(widget, name == "dark")


def apply_ttk_style():
    # Style the ttk scrollbar from the current palette (apply_theme calls this again on a switch).
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Vertical.TScrollbar",
        background=BG_PANEL, troughcolor=BG_INPUT,
        arrowcolor=TEXT_MUT, bordercolor=BORDER,
        darkcolor=BG_PANEL, lightcolor=BG_PANEL,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER)])


def flat_btn(parent, text, command, color=None, fg=None, **kw):
    # Full-size flat button for primary actions. Colours default at call time (theme switches).
    color, fg = color or ACCENT, fg or BG
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=color, activeforeground=fg,
        disabledforeground=TEXT_MUT, font=FONT_BTN, relief="flat", bd=0,
        padx=14, pady=7, cursor="hand2", **kw
    )


def small_btn(parent, text, command, color=None, fg=None, **kw):
    # Compact ghost button for secondary actions: the parent's background until hovered.
    color, fg = color or parent.cget("bg"), fg or TEXT_SEC
    return tk.Button(
        parent, text=text, command=command,
        bg=color, fg=fg, activebackground=HOVER, activeforeground=TEXT_PRI,
        disabledforeground=TEXT_MUT, font=FONT_BTN_SM, relief="flat", bd=0,
        padx=10, pady=5, cursor="hand2", **kw
    )


def section_label(parent, text):
    # A quiet sentence-case heading in muted text, on the parent's background.
    bg = parent.cget("bg")
    frame = tk.Frame(parent, bg=bg)
    tk.Label(frame, text=text, font=FONT_SMALL,
             fg=TEXT_MUT, bg=bg).pack(side="left", pady=4)
    return frame


def card(parent, **kw):
    # A 1px bordered panel. Returns the outer border frame and inner content frame.
    outer = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=BG_PANEL, **kw)
    inner.pack(fill="both", expand=True)
    return outer, inner


# ============================================================
# SECTION 4b — TRAY IMAGE HELPER
# ============================================================

def _make_tray_image():
    # Create a 64x64 RGBA icon: dark background with an accent-colored circle.
    img = Image.new("RGBA", (64, 64), (26, 27, 38, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(122, 162, 247, 255))
    return img


# ============================================================
# SECTION 5 — DROPDOWN WIDGET
# A fully-themed dropdown that avoids Windows native rendering.
# See the header comment for a full explanation of why this
# exists instead of using ttk.Combobox.
# ============================================================

class _MenuProxy:
    # Stores the dropdown options in memory, mimicking the tk.Menu API.
    def __init__(self):
        self._items = []

    def delete(self, first, last):
        self._items.clear()

    def add_command(self, label="", command=None):
        self._items.append((label, command or (lambda: None)))


class DropdownButton(tk.Frame):
    # A custom dropdown: a Button + arrow Label that opens a borderless popup window.
    def __init__(self, parent, var):
        super().__init__(parent, bg=BORDER, padx=1, pady=1)
        self._var   = var
        self._popup = None
        self.menu   = _MenuProxy()

        self._arrow = tk.Label(self, text="▾", bg=BG_INPUT, fg=TEXT_MUT,
                               font=FONT_SMALL, padx=6)
        self._arrow.pack(side="right", fill="y")

        self._btn = tk.Button(
            self, textvariable=var,
            bg=BG_INPUT, fg=TEXT_PRI,
            activebackground=BG_PANEL, activeforeground=TEXT_PRI,
            font=FONT_SMALL, relief="flat", bd=0,
            highlightthickness=0, anchor="w", padx=8,
            cursor="hand2", command=self._toggle_popup,
        )
        self._btn.pack(side="left", fill="both", expand=True)
        self._arrow.bind("<Button-1>", lambda e: self._toggle_popup())

    def _toggle_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
            self._popup = None
            return
        self._show_popup()

    def _show_popup(self):
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        w = self.winfo_width()

        # overrideredirect strips all OS window chrome so we control the border fully
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.configure(bg=BORDER)

        inner = tk.Frame(popup, bg=BG_INPUT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        for label, cmd in self.menu._items:
            def on_select(c=cmd, p=popup):
                c()
                p.destroy()
                self._popup = None

            tk.Button(
                inner, text=label, anchor="w",
                bg=BG_INPUT, fg=TEXT_PRI,
                activebackground=ACCENT, activeforeground=BG,
                font=FONT_SMALL, relief="flat", bd=0,
                highlightthickness=0, padx=8, pady=4,
                cursor="hand2", command=on_select,
            ).pack(fill="x")

        popup.update_idletasks()
        h = inner.winfo_reqheight() + 2
        popup.geometry(f"{w}x{h}+{x}+{y}")

        # 50ms delay lets the click handler fire before the popup is destroyed on focus loss
        def on_focus_out(event):
            def check():
                if not popup.winfo_exists():
                    return
                focused = popup.focus_get()
                if focused is None or not str(focused).startswith(str(popup)):
                    popup.destroy()
                    self._popup = None
            popup.after(50, check)

        popup.bind("<FocusOut>", on_focus_out)
        popup.focus_set()
        self._popup = popup


# ============================================================
# SECTION 6 — PRESET EDITOR DIALOG
# A modal form for adding or editing a preset.
# ============================================================

class PresetDialog(tk.Toplevel):
    def __init__(self, parent, title, name="", command="", model_keywords=None, available_models=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("720x540")
        self.grab_set()

        self.result_name    = None
        self.result_command = None
        self.result_models  = None
        self._available_models = available_models or []

        section_label(self, "Preset name").pack(fill="x", padx=20, pady=(16, 2))
        self.name_var = tk.StringVar(value=name)
        tk.Entry(self, textvariable=self.name_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).pack(fill="x", padx=20, ipady=6)

        section_label(self, "Applies to models").pack(fill="x", padx=20, pady=(14, 2))
        tk.Label(self, text="Select models this preset applies to — leave none selected to match all",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 4))

        list_frame = tk.Frame(self, bg=BORDER)
        list_frame.pack(fill="x", padx=20)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.models_listbox = tk.Listbox(
            list_frame, selectmode=tk.MULTIPLE,
            bg=BG_INPUT, fg=TEXT_PRI,
            selectbackground=ACCENT, selectforeground=BG,
            font=FONT_MONO, relief="flat", bd=0,
            highlightthickness=0,
            activestyle="none",
            yscrollcommand=scrollbar.set,
            height=min(max(len(self._available_models), 1), 5),
        )
        scrollbar.config(command=self.models_listbox.yview)
        self.models_listbox.pack(side="left", fill="x", expand=True)
        if self._available_models:
            scrollbar.pack(side="right", fill="y")

        for i, model in enumerate(self._available_models):
            self.models_listbox.insert(tk.END, model)
            # Pre-select models that match any existing keyword (migration aid)
            if model_keywords:
                lower = model.lower()
                if any(kw.lower() in lower for kw in model_keywords):
                    self.models_listbox.selection_set(i)

        if not self._available_models:
            self.models_listbox.insert(tk.END, "(no models found — will match all)")
            self.models_listbox.config(fg=TEXT_MUT)

        section_label(self, "Command template: use {model} as the model path placeholder").pack(
            fill="x", padx=20, pady=(14, 2)
        )
        self.cmd_text = tk.Text(
            self, height=6, font=FONT_MONO,
            bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
            relief="flat", wrap="word",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self.cmd_text.pack(padx=20, fill="both", expand=True)
        self.cmd_text.insert("1.0", command)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(padx=20, pady=14, fill="x")
        flat_btn(btn_row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        small_btn(btn_row, "Cancel", self.destroy).pack(side="left")

    def _save(self):
        name = self.name_var.get().strip()
        cmd  = self.cmd_text.get("1.0", "end").strip()
        if not name:
            messagebox.showerror("Error", "Preset name cannot be empty.", parent=self)
            return
        if not cmd:
            messagebox.showerror("Error", "Command template cannot be empty.", parent=self)
            return

        selected = self.models_listbox.curselection()
        self.result_models = [self._available_models[i] for i in selected] if self._available_models else []

        self.result_name    = name
        self.result_command = cmd
        self.destroy()


# ============================================================
# SECTION 7 — SETTINGS DIALOG
# A small modal that lets the user change the llama.cpp folder
# path after initial setup. Accessible via the Settings button
# in the header of the home screen.
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, current_path, on_save,
                 uvx_command="", on_save_uvx=None,
                 open_browser=True, on_set_open_browser=None,
                 on_open_folders=None, previous_build=None, on_rollback=None,
                 theme="system", on_set_theme=None):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        # on_rollback is None while the servers run (Windows locks llama-server.exe).
        self._rollback_build = parse_build(previous_build or "") if on_rollback else None
        self._extra_h = H_ROLLBACK if self._rollback_build else 0
        self.geometry(f"{W_SETTINGS}x{H_SETTINGS + self._extra_h}")
        self.grab_set()

        self._on_save        = on_save
        self._on_rollback    = on_rollback
        self._on_save_uvx    = on_save_uvx
        self._on_set_open_browser = on_set_open_browser
        self._on_set_theme   = on_set_theme
        self._uvx_expanded   = False

        # ── Llama.cpp folder ─────────────────────────────────────
        section_label(self, "llama.cpp folder").pack(fill="x", padx=20, pady=(20, 6))
        tk.Label(self, text="The working directory used when launching llama-server.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 10))

        path_row = tk.Frame(self, bg=BG)
        path_row.pack(fill="x", padx=20)
        path_row.columnconfigure(0, weight=1)

        self._path_var = tk.StringVar(value=current_path)
        tk.Entry(path_row, textvariable=self._path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        small_btn(path_row, "Browse", self._browse).grid(row=0, column=1, padx=(8, 0))

        # ── Launch options ────────────────────────────────────────
        section_label(self, "Launch options").pack(fill="x", padx=20, pady=(20, 6))

        # Web UI row: label left, equal-width Yes/No buttons right
        web_row = tk.Frame(self, bg=BG)
        web_row.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(web_row, text="Web UI", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")

        btn_grp = tk.Frame(web_row, bg=BG)
        btn_grp.pack(side="right")
        self._yes_btn_dlg = tk.Button(
            btn_grp, text="Yes", width=5,
            font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
            command=lambda: self._set_open_browser_dlg(True),
        )
        self._yes_btn_dlg.pack(side="left", padx=(0, 3))
        self._no_btn_dlg = tk.Button(
            btn_grp, text="No", width=5,
            font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
            command=lambda: self._set_open_browser_dlg(False),
        )
        self._no_btn_dlg.pack(side="left")

        self._open_browser_dlg = open_browser
        self._apply_open_browser_style()

        tk.Label(self, text="Open the browser automatically when the server comes online.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 14))

        # Theme row: System follows Windows' light/dark setting, like the Web UI's choice.
        theme_row = tk.Frame(self, bg=BG)
        theme_row.pack(fill="x", padx=20, pady=(0, 14))
        tk.Label(theme_row, text="Theme", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
        theme_grp = tk.Frame(theme_row, bg=BG)
        theme_grp.pack(side="right")
        self._theme_btns = {}
        for i, t in enumerate(THEMES):
            b = tk.Button(theme_grp, text=t.capitalize(), width=6,
                          font=FONT_BTN_SM, relief="flat", bd=0, padx=6, pady=5, cursor="hand2",
                          command=lambda t=t: self._set_theme_dlg(t))
            b.pack(side="left", padx=(0, 3) if i < len(THEMES) - 1 else 0)
            self._theme_btns[t] = b
        self._theme_dlg = theme
        self._apply_theme_style()

        if on_open_folders:
            agents_row = tk.Frame(self, bg=BG)
            agents_row.pack(fill="x", padx=20, pady=(0, 14))
            tk.Label(agents_row, text="Agent folders", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
            small_btn(agents_row, "Manage…", lambda: on_open_folders(self)).pack(side="right")

        if self._rollback_build:
            rollback_row = tk.Frame(self, bg=BG)
            rollback_row.pack(fill="x", padx=20, pady=(0, 14))
            tk.Label(rollback_row, text="llama.cpp", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
            small_btn(rollback_row, f"Roll back to b{self._rollback_build}", self._rollback).pack(side="right")

        # UVX Settings: label + description + accordion toggle
        uvx_hdr = tk.Frame(self, bg=BG)
        uvx_hdr.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(uvx_hdr, text="UVX Settings", font=FONT_SMALL, fg=TEXT_PRI, bg=BG).pack(side="left")
        self._uvx_btn = small_btn(uvx_hdr, "▶ Show command", self._toggle_uvx_dlg)
        self._uvx_btn.pack(side="right")

        tk.Label(self, text="Command used to start the MCP proxy bridge on port 8001.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(padx=20, anchor="w", pady=(0, 6))

        self._uvx_text = tk.Text(
            self, height=4, font=FONT_MONO,
            bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
            relief="flat", wrap="word", padx=8, pady=6,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        self._uvx_text.insert("1.0", uvx_command)

        # ── Save / Cancel ─────────────────────────────────────────
        self._btn_row = tk.Frame(self, bg=BG)
        self._btn_row.pack(padx=20, pady=(10, 20), fill="x")
        flat_btn(self._btn_row, "Save", self._save, color=ACCENT, fg=BG).pack(side="left", padx=(0, 8))
        small_btn(self._btn_row, "Cancel", self.destroy).pack(side="left")

    def _set_open_browser_dlg(self, open_browser):
        self._open_browser_dlg = open_browser
        self._apply_open_browser_style()
        if self._on_set_open_browser:
            self._on_set_open_browser(open_browser)

    @staticmethod
    def _style_toggle(button, on):
        if on:
            button.config(bg=ACCENT, fg=BG, activebackground=ACCENT, activeforeground=BG)
        else:
            button.config(bg=BG_INPUT, fg=TEXT_SEC, activebackground=BORDER, activeforeground=TEXT_PRI)

    def _apply_open_browser_style(self):
        self._style_toggle(self._yes_btn_dlg, self._open_browser_dlg)
        self._style_toggle(self._no_btn_dlg, not self._open_browser_dlg)

    def _set_theme_dlg(self, theme):
        self._theme_dlg = theme
        if self._on_set_theme:
            self._on_set_theme(theme)  # recolours this dialog too
        self._apply_theme_style()

    def _apply_theme_style(self):
        for t, b in self._theme_btns.items():
            self._style_toggle(b, t == self._theme_dlg)

    def _toggle_uvx_dlg(self):
        self._uvx_expanded = not self._uvx_expanded
        if self._uvx_expanded:
            self._uvx_text.pack(fill="x", padx=20, pady=(0, 6), before=self._btn_row)
            self._uvx_btn.config(text="▼ Hide command")
            self.geometry(f"{W_SETTINGS}x{H_SETTINGS_UVX + self._extra_h}")
        else:
            self._uvx_text.pack_forget()
            self._uvx_btn.config(text="▶ Show command")
            self.geometry(f"{W_SETTINGS}x{H_SETTINGS + self._extra_h}")

    def _rollback(self):
        build = self._rollback_build
        if messagebox.askyesno(
                "Roll back llama.cpp",
                f"Reinstall llama.cpp b{build} through WinGet, then run the build check? "
                "This accepts the package's terms and uses the GPU for about 30 seconds.",
                parent=self):
            # Settings closes first, so the progress dialog takes the grab from the main window.
            self.destroy()
            self._on_rollback(build)

    def _browse(self):
        path = filedialog.askdirectory(title="Select llama.cpp folder")
        if path:
            self._path_var.set(path)

    def _save(self):
        path = self._path_var.get().strip()
        if not path:
            messagebox.showerror("Required", "Please enter a path.", parent=self)
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path",
                "That folder doesn't exist. Please choose a valid directory.", parent=self)
            return
        self._on_save(path)
        if self._on_save_uvx:
            self._on_save_uvx(self._uvx_text.get("1.0", "end").strip())
        self.destroy()


class FoldersDialog(tk.Toplevel):
    # Folders agents may read (and organise through approved plans).
    def __init__(self, parent, policy, error=None):
        super().__init__(parent)
        self.title("Agent folders")
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x320")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self._parent = parent
        self._policy = policy
        section_label(self, "Folders agents can read").pack(fill="x", padx=20, pady=(20, 6))
        note = f"Agents always write only to {policy.sandbox}. Local drives only."
        if error:
            note += f" The sandbox can't be created: {error}"
        tk.Label(self, text=note,
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG, wraplength=W_SETTINGS - 40,
                 justify="left").pack(padx=20, anchor="w")
        self._list = tk.Listbox(self, font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI, relief="flat",
                                highlightthickness=1, highlightbackground=BORDER, height=8)
        self._list.pack(fill="both", expand=True, padx=20, pady=10)
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(0, 20))
        flat_btn(row, "Add folder…", self._add).pack(side="left", padx=(0, 8))
        small_btn(row, "Remove", self._remove).pack(side="left")
        small_btn(row, "Close", self._close).pack(side="right")
        self._refresh()

    def _close(self):
        # Tk drops the grab when this window goes; hand it back to Settings.
        self.destroy()
        try:
            if self._parent.winfo_exists():
                self._parent.grab_set()
        except tk.TclError:
            pass

    def _refresh(self):
        self._list.delete(0, "end")
        try:
            folders = self._policy.read_folders()
        except (OSError, ValueError) as e:
            messagebox.showerror("Folder list unreadable", str(e), parent=self)
            return
        for f in folders:
            self._list.insert("end", f)

    def _add(self):
        path = filedialog.askdirectory(title="Let agents read this folder", parent=self)
        if not path:
            return
        try:
            self._policy.add_folder(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Not added", str(e), parent=self)
        self._refresh()

    def _remove(self):
        sel = self._list.curselection()
        if sel:
            try:
                self._policy.remove_folder(self._list.get(sel[0]))
            except (OSError, ValueError) as e:
                messagebox.showerror("Not removed", str(e), parent=self)
            self._refresh()


class UpdateDialog(tk.Toplevel):
    # Progress of a WinGet job (update or roll back). job(report) runs in a
    # background thread and returns (ok, message); its report lines reach the
    # log through after(0, ...). Close stays disabled until the job ends.
    def __init__(self, parent, title, job, on_done=None):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x360")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self._on_done = on_done
        self._working = True
        section_label(self, title).pack(fill="x", padx=20, pady=(20, 6))
        border = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=20)
        self._log = tk.Text(border, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, height=10,
                            relief="flat", state=tk.DISABLED, wrap="word")
        sb = ttk.Scrollbar(border, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._status = tk.Label(self, text="Working… this can take a few minutes.",
                                font=FONT_SMALL, fg=TEXT_SEC, bg=BG,
                                wraplength=W_SETTINGS - 40, justify="left")
        self._status.pack(padx=20, anchor="w", pady=(10, 0))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(10, 20))
        self._close_btn = small_btn(row, "Close", self._close, state=tk.DISABLED)
        self._close_btn.pack(side="right")
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def report(self, line):
        # Called from the job's thread.
        self.after(0, self._append, line)

    def _append(self, line):
        self._log.config(state=tk.NORMAL)
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.config(state=tk.DISABLED)

    def _work(self, job):
        try:
            ok, message = job(self.report)
        except Exception as e:  # Close must never stay disabled
            ok, message = False, str(e) or type(e).__name__
        self.after(0, self._finish, ok, message)

    def _finish(self, ok, message):
        self._working = False
        self._status.config(text=message, fg=TEXT_PRI)
        self._close_btn.config(state=tk.NORMAL)
        if self._on_done:
            self._on_done(ok)

    def _close(self):
        if not self._working:
            self.destroy()


class TestDialog(tk.Toplevel):
    # Progress of a model test. proc is the harness (eval/model_test.py); a reader
    # thread drains its output into the log through after(0, ...). No grab: Exit
    # Processes stays clickable and stops the test first (stop_now).
    def __init__(self, parent, proc):
        super().__init__(parent)
        self.title("Model test")
        self.configure(bg=BG)
        self.geometry(f"{W_SETTINGS}x420")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._proc = proc
        self._phase = "running"  # running / stopping / done
        self._report_path = None
        self._last = ""
        section_label(self, "Model test").pack(fill="x", padx=20, pady=(20, 6))
        border = tk.Frame(self, bg=BORDER, padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=20)
        self._log = tk.Text(border, font=FONT_LOG, bg=BG_INPUT, fg=TEXT_PRI, height=10,
                            relief="flat", state=tk.DISABLED, wrap="word")
        sb = ttk.Scrollbar(border, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True)
        self._status = tk.Label(self, text="Running… 15 to 60 minutes. Web UI chat is paused.",
                                font=FONT_SMALL, fg=TEXT_SEC, bg=BG,
                                wraplength=W_SETTINGS - 40, justify="left")
        self._status.pack(padx=20, anchor="w", pady=(10, 0))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=(10, 20))
        self._close_btn = small_btn(row, "Close", self._close, state=tk.DISABLED)
        self._close_btn.pack(side="right")
        self._stop_btn = small_btn(row, "Stop", self._stop, fg=DANGER)
        self._stop_btn.pack(side="right", padx=(0, 8))
        threading.Thread(target=self._read, daemon=True).start()

    def busy(self):
        return self._phase != "done"

    def report(self, line):
        # Called from the reader and stop threads.
        self.after(0, self._append, line)

    def _append(self, line):
        if line.strip():
            self._last = line
        if line.startswith("[report] "):
            self._report_path = line[len("[report] "):].strip()
        self._log.config(state=tk.NORMAL)
        self._log.insert("end", line + "\n")
        self._log.see("end")
        self._log.config(state=tk.DISABLED)

    def _read(self):
        # Keeps draining the pipe until EOF (see the launcher rule), then reports the exit code.
        try:
            for line in self._proc.stdout:
                self.report(line.rstrip("\r\n"))
        except Exception:
            pass
        self.after(0, self._ended, self._proc.wait())

    def _ended(self, code):
        if self._phase != "running":
            return  # Stop killed it; the stop sequence sets the status
        self._phase = "done"
        clear_test_lock(MODEL_TEST_LOCK)
        self._stop_btn.config(state=tk.DISABLED)
        self._close_btn.config(state=tk.NORMAL)
        if code == 0 and self._report_path:
            self._status.config(text="Done. The report is open in the browser.", fg=TEXT_PRI)
            webbrowser.open(pathlib.Path(self._report_path).as_uri())
        else:
            self._status.config(text=f"Failed (exit {code}): {self._last}", fg=DANGER)

    def _stop(self):
        # The question runs its own event loop, so the test may have ended meanwhile.
        if (self._phase == "running"
                and messagebox.askyesno("Stop the model test",
                                        "Stop the test? Nothing is added to the report.", parent=self)
                and self._phase == "running"):
            self._begin_stop()
            threading.Thread(target=self._stop_sequence, daemon=True).start()

    def stop_now(self):
        # Exit Processes: the same sequence without the question, on the UI thread,
        # so it ends before the agents server is killed.
        if self._phase == "running":
            self._begin_stop()
            self._stop_sequence()

    def _begin_stop(self):
        self._phase = "stopping"
        self._stop_btn.config(state=tk.DISABLED)
        self._status.config(text="Stopping…", fg=TEXT_SEC)

    def _stop_sequence(self):
        # In this order: the harness first, so it starts no more jobs; then its jobs (a
        # running one stops before its next model call); then its folders; the lock last.
        try:
            subprocess.run(f"taskkill /F /T /PID {self._proc.pid}", shell=True, capture_output=True)
            try:
                key = agent_llm.read_key(agent_llm.APPROVAL_KEY_FILE)
            except OSError:
                key = ""
            for job_id in read_test_jobs(agent_paths.TEST_JOBS_FILE):
                try:
                    _, body = _http(f"{agent_llm.AGENTS_BASE}/jobs/{job_id}/cancel", 5, b"",
                                    {"Authorization": f"Bearer {key}",
                                     "Host": f"127.0.0.1:{agent_llm.AGENTS_PORT}"})
                    self.report(f"{job_id}: {json.loads(body).get('message')}")
                except Exception as e:
                    self.report(f"{job_id}: not cancelled ({e})")
            for folder in test_temp_dirs():
                shutil.rmtree(folder, ignore_errors=True)
            clear_test_lock(MODEL_TEST_LOCK)
        finally:
            self.after(0, self._stopped)

    def _stopped(self):
        self._phase = "done"
        self._status.config(text="Stopped; nothing was added to the report.", fg=TEXT_PRI)
        self._close_btn.config(state=tk.NORMAL)

    def _close(self):
        # Closing the window while the test runs means Stop.
        if self._phase == "running":
            self._stop()
        elif self._phase == "done":
            self.destroy()


# ============================================================
# SECTION 8 — MAIN APPLICATION WINDOW
# CommandLauncherGUI manages the full window lifecycle:
#
#   _build_setup_screen()  — first-run path configuration
#   _confirm_setup()       — validates path, saves config, transitions to home
#   _build_home_screen()   — the main launcher UI
#
# On startup, the app checks config.json. If no llama.cpp path
# is saved, the setup screen is shown. Otherwise the home screen
# loads directly.
# ============================================================

class CommandLauncherGUI:
    def __init__(self, master):
        self.master = master

        # Load persisted config — contains the llama.cpp folder path
        self.config    = load_config()
        self.llama_dir = self.config.get("llama_dir", "")

        # The palette must be set before any widget is built; widgets read it when created.
        self._theme = theme_setting(self.config)
        set_palette(resolve_theme(self._theme, windows_light()))
        master.title("")
        master.configure(bg=BG)
        master.resizable(False, False)
        master.iconbitmap(default="")

        apply_ttk_style()
        # Every dialog gets the title bar of the current theme when it first shows.
        # Class bindings fire for the window itself, not for its children.
        for cls in ("Tk", "Toplevel"):
            master.bind_class(cls, "<Map>",
                              lambda e: set_dark_title_bar(e.widget, CURRENT_THEME == "dark"), add="+")
        self._watch_windows_theme()

        # Kill all child processes when the window is closed
        master.protocol("WM_DELETE_WINDOW", self._on_close)

        # A model test lock left by a crash would keep Web UI chat paused.
        clear_test_lock(MODEL_TEST_LOCK)

        # Route to setup or home based on whether a path is already configured
        if not self.llama_dir:
            self._build_setup_screen()
        else:
            self._build_home_screen()


    # ── Setup screen ─────────────────────────────────────────
    # Shown on first launch. Requires the user to provide the
    # llama.cpp folder path before proceeding to the home screen.

    def _build_setup_screen(self):
        self.master.geometry(f"{W}x{H_SETUP}")
        self.master.grid_columnconfigure(0, weight=1)
        self.master.grid_rowconfigure(1, weight=1)

        # Title row, on the window background (no header bar, like the Web UI)
        hdr = tk.Frame(self.master, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew")
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg=BG).pack(side="left", padx=32, pady=(20, 0))

        # Body — holds all setup content in a single frame for easy cleanup
        self._setup_frame = tk.Frame(self.master, bg=BG)
        self._setup_frame.grid(row=1, column=0, sticky="nsew", padx=32, pady=20)
        self._setup_frame.columnconfigure(0, weight=1)

        tk.Label(self._setup_frame,
                 text="Where is your llama.cpp folder?",
                 font=FONT_BTN, fg=TEXT_PRI, bg=BG).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        tk.Label(self._setup_frame,
                 text="This folder is used as the working directory when launching servers.",
                 font=FONT_SMALL, fg=TEXT_MUT, bg=BG).grid(
            row=1, column=0, sticky="w", pady=(0, 14))

        # Path entry row — text field + browse button side by side
        path_row = tk.Frame(self._setup_frame, bg=BG)
        path_row.grid(row=2, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)

        self._setup_path_var = tk.StringVar()
        tk.Entry(path_row, textvariable=self._setup_path_var,
                 font=FONT_MONO, bg=BG_INPUT, fg=TEXT_PRI,
                 insertbackground=TEXT_PRI, relief="flat",
                 highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT).grid(row=0, column=0, sticky="ew", ipady=6)
        small_btn(path_row, "Browse", self._browse_setup).grid(
            row=0, column=1, padx=(8, 0))

        flat_btn(self._setup_frame, "Confirm", self._confirm_setup,
                 color=ACCENT, fg=BG).grid(row=3, column=0, sticky="ew", pady=(16, 0))

    def _browse_setup(self):
        # Open a folder picker and put the chosen path into the setup entry field.
        path = filedialog.askdirectory(title="Select llama.cpp folder")
        if path:
            self._setup_path_var.set(path)

    def _confirm_setup(self):
        # Validate the entered path, save it to config.json, and transition to home.
        path = self._setup_path_var.get().strip()
        if not path:
            messagebox.showerror("Required",
                "Please enter the path to your llama.cpp folder.")
            return
        if not os.path.isdir(path):
            messagebox.showerror("Invalid Path",
                "That folder doesn't exist. Please choose a valid directory.")
            return

        self.llama_dir = path
        self.config["llama_dir"] = path
        save_config(self.config)

        # Clear the setup screen and build the home screen in its place
        for widget in self.master.winfo_children():
            widget.destroy()
        self._build_home_screen()


    # ── Home screen ──────────────────────────────────────────
    # The main launcher UI — model/preset selection, UVX command,
    # launch button, and running state (log + exit button).

    def _build_home_screen(self):
        self.master.geometry(f"{W}x{H_CONFIG}")
        self.master.grid_columnconfigure(0, weight=1)

        # Application state
        try:
            self.presets = load_presets(self.config)
        except ValueError as e:
            messagebox.showerror("Config Error",
                f"One or more presets in config.json are invalid:\n{e}\n\n"
                "Proceeding with an empty preset list.")
            self.presets = {}
        self._open_browser = self.config.get("open_browser", True)
        self._models       = {}
        if migrate_uvx_command(self.config):
            save_config(self.config)
        self.uvx_command   = self.config.get("uvx_command", UVX_COMMAND)
        self.api_key       = load_api_key(API_KEY_FILE)
        # Only the jobs panel sends this one, so llama API key holders can't approve plans (ADR 0009).
        self.approval_key  = load_api_key(agent_llm.APPROVAL_KEY_FILE)
        self.agent_policy  = agent_paths.Policy(agent_paths.default_sandbox())
        self._agents_error = None
        try:
            self.agent_policy.ensure_dirs()
        except OSError as e:
            # Agents are optional: an unusable sandbox must never stop the launcher.
            self._agents_error = str(e)
        self.llama_cmd     = ""
        self._llama_proc   = None
        self._uvx_proc     = None
        self._proxy_proc   = None
        self._agents_proc  = None
        self._polling      = False
        self._launch_time  = None
        self._test_dialog  = None

        self.master.columnconfigure(0, weight=1)

        # ── Row 0: Title row with Settings on the right, on the window background
        hdr = tk.Frame(self.master, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        tk.Label(hdr, text="Llama Launcher", font=FONT_TITLE,
                 fg=TEXT_PRI, bg=BG).pack(side="left")
        small_btn(hdr, "Settings", self._open_settings).pack(side="right")

        # ── Row 1: llama.cpp update notice (hidden until the startup check finds a newer build)
        self._notice, notice_card = card(self.master)
        self._notice_label = tk.Label(notice_card, font=FONT_SMALL, fg=TEXT_PRI, bg=BG_PANEL,
                                      wraplength=320, justify="left")
        self._notice_label.pack(side="left", padx=12, pady=8)
        self._update_btn = small_btn(notice_card, "Update", self._start_update, color=ACCENT, fg=BG)
        self._update_btn.pack(side="right", padx=12, pady=8)

        # ── Row 2: Model selector
        self.model_outer, model_card = card(self.master)
        self.model_outer.grid(row=2, column=0, padx=24, pady=(16, 8), sticky="ew")
        model_card.grid_columnconfigure(1, weight=1)

        section_label(model_card, "Model").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.model_var  = tk.StringVar()
        self.model_menu = DropdownButton(model_card, self.model_var)
        self.model_menu.grid(row=0, column=1, sticky="ew", pady=10)
        small_btn(model_card, "Refresh", self._refresh_models).grid(row=0, column=2, padx=(6, 12), pady=10)

        self._refresh_models(init=True)

        # ── Row 3: Preset selector
        self.preset_outer, preset_card = card(self.master)
        self.preset_outer.grid(row=3, column=0, padx=24, pady=(0, 8), sticky="ew")
        preset_card.grid_columnconfigure(1, weight=1)

        section_label(preset_card, "Preset").grid(row=0, column=0, sticky="w", padx=(12, 6), pady=10)
        self.preset_var  = tk.StringVar()
        self.preset_menu = DropdownButton(preset_card, self.preset_var)
        self.preset_menu.grid(row=0, column=1, sticky="ew", pady=10)

        preset_btns = tk.Frame(preset_card, bg=BG_PANEL)
        preset_btns.grid(row=0, column=2, padx=(6, 12), pady=10)
        small_btn(preset_btns, "+ Add",  self._add_preset).pack(side="left", padx=(0, 6))
        small_btn(preset_btns, "Edit",   self._edit_preset).pack(side="left", padx=(0, 6))
        small_btn(preset_btns, "Delete", self._delete_preset, fg=DANGER).pack(side="left")

        # ── Row 4: Launch Server button
        self.run_button = tk.Button(
            self.master, text="Launch Server",
            command=self.run_commands,
            bg=SUCCESS, fg=BG,
            activebackground=SUCCESS_HOVER, activeforeground=BG,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            pady=12, cursor="hand2",
        )
        self.run_button.grid(row=4, column=0,
                             sticky="ew", padx=24, pady=(8, 24))

        # ── Row 5: Status bar (hidden until running state)
        self.status_bar = tk.Frame(self.master, bg=BG_PANEL)

        # Left: health dot
        self._health_label = tk.Label(self.status_bar, text="● waiting",
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL)
        self._health_label.pack(side="left", padx=(16, 0), pady=8)

        # Middle: uptime label + elapsed counter
        tk.Label(self.status_bar, text="uptime",
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL).pack(side="left", padx=(20, 6), pady=8)
        self._elapsed_var = tk.StringVar(value="00:00:00")
        tk.Label(self.status_bar, textvariable=self._elapsed_var,
            font=FONT_MONO, fg=TEXT_SEC, bg=BG_PANEL).pack(side="left", pady=8)

        # Right: auto-scroll checkbox
        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.status_bar, text="Auto-scroll",
            variable=self._autoscroll_var,
            font=FONT_SMALL, fg=TEXT_MUT, bg=BG_PANEL,
            activebackground=BG_PANEL, activeforeground=TEXT_SEC,
            selectcolor=BG_INPUT, cursor="hand2").pack(side="right", padx=16, pady=8)

        # Below the status bar: the direct API address for agents (bypasses the
        # proxy, so no system prompt or date injection). Click to copy.
        self.api_bar = tk.Frame(self.master, bg=BG)
        api_row = tk.Frame(self.api_bar, bg=BG)
        api_row.pack(fill="x")
        tk.Label(api_row, text="Agent API", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left")
        self._api_label = tk.Label(api_row, text=AGENT_API_URL, font=FONT_MONO,
                                   fg=ACCENT, bg=BG, cursor="hand2")
        self._api_label.pack(side="left", padx=(8, 0))
        self._api_label.bind("<Button-1>", lambda e: self._copy_api_url())
        # The keys themselves are never shown, only copied. A second row: three pairs don't fit 480 px.
        key_row = tk.Frame(self.api_bar, bg=BG)
        key_row.pack(fill="x", pady=(2, 0))
        tk.Label(key_row, text="API key", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left")
        self._key_label = tk.Label(key_row, text="click to copy", font=FONT_MONO,
                                   fg=ACCENT, bg=BG, cursor="hand2")
        self._key_label.pack(side="left", padx=(8, 0))
        self._key_label.bind("<Button-1>", lambda e: self._copy_key(self.api_key, self._key_label))
        tk.Label(key_row, text="Approval key (Jobs panel)", font=FONT_SMALL,
                 fg=TEXT_MUT, bg=BG).pack(side="left", padx=(16, 0))
        self._approval_label = tk.Label(key_row, text="click to copy", font=FONT_MONO,
                                        fg=ACCENT, bg=BG, cursor="hand2")
        self._approval_label.pack(side="left", padx=(8, 0))
        self._approval_label.bind("<Button-1>", lambda e: self._copy_key(self.approval_key, self._approval_label))

        # ── Rows 7–8: Running state (hidden until Launch is pressed)
        # Two stacked log panes separated by a draggable sash.

        self._llama_label_var  = tk.StringVar(value="llama-server")
        self._uvx_label_var    = tk.StringVar(value="UVX Proxy")
        self._proxy_label_var  = tk.StringVar(value="System Prompt Proxy")

        # PanedWindow lets the user drag the divider to resize each log pane.
        self.log_pane = tk.PanedWindow(
            self.master, orient="vertical",
            bg=BORDER, sashwidth=5, sashpad=0,
            sashrelief="flat", bd=0, relief="flat",
        )

        def _make_log_pane(label_var):
            frame = tk.Frame(self.log_pane, bg=BG)
            hdr = tk.Frame(frame, bg=BG)
            hdr.pack(fill="x", pady=(4, 2))
            tk.Label(hdr, textvariable=label_var,
                     font=FONT_SMALL, fg=TEXT_MUT, bg=BG).pack(side="left", pady=2)
            border = tk.Frame(frame, bg=BORDER, padx=1, pady=1)
            border.pack(fill="both", expand=True)
            txt = tk.Text(border, font=FONT_LOG,
                          bg=BG_INPUT, fg=TEXT_PRI, insertbackground=TEXT_PRI,
                          relief="flat", state=tk.DISABLED, wrap="none")
            sb = ttk.Scrollbar(border, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            return frame, txt

        llama_frame,  self.llama_log  = _make_log_pane(self._llama_label_var)
        uvx_frame,    self.uvx_log    = _make_log_pane(self._uvx_label_var)
        proxy_frame,  self.proxy_log  = _make_log_pane(self._proxy_label_var)
        self.log_pane.add(llama_frame,  stretch="always", minsize=80)
        self.log_pane.add(uvx_frame,    stretch="always", minsize=80)
        self.log_pane.add(proxy_frame,  stretch="always", minsize=80)

        # ── Test this model + Exit buttons. Test is enabled only while the health poll says online.
        self.exit_row = tk.Frame(self.master, bg=BG)
        # Outline button: a Tk button on Windows draws no border of its own, so a 1px frame does.
        test_border = tk.Frame(self.exit_row, bg=BORDER, padx=1, pady=1)
        test_border.pack(side="left", padx=(0, 8))
        self.test_btn = tk.Button(
            test_border, text="Test this model",
            command=self._start_model_test, state=tk.DISABLED,
            bg=BG, fg=TEXT_PRI, disabledforeground=TEXT_MUT,
            activebackground=HOVER, activeforeground=TEXT_PRI,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            padx=14, pady=11, cursor="hand2",
        )
        self.test_btn.pack()
        self.exit_btn = tk.Button(
            self.exit_row, text="Exit Processes",
            command=self._exit_running_state,
            bg=DANGER, fg=BG,
            activebackground=DANGER_HOVER, activeforeground=BG,
            font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
            pady=12, cursor="hand2",
        )

        # When the model changes, re-filter the preset list.
        # When the preset changes, rebuild the launch command.
        self.model_var.trace_add("write",  lambda *_: self._on_model_changed())
        self.preset_var.trace_add("write", lambda *_: self._load_preset())

        self._refresh_preset_menu(reset=True)

        # Set up system tray icon if available
        self._setup_tray()

        threading.Thread(target=self._check_for_update, daemon=True).start()


    # ── Settings ─────────────────────────────────────────────

    def _open_settings(self):
        def on_save(new_path):
            self.llama_dir = new_path
            self.config["llama_dir"] = new_path
            save_config(self.config)

        def on_save_uvx(cmd):
            self.uvx_command = cmd
            self.config["uvx_command"] = cmd
            save_config(self.config)

        SettingsDialog(
            self.master, self.llama_dir, on_save,
            uvx_command=self.uvx_command,
            on_save_uvx=on_save_uvx,
            open_browser=self._open_browser,
            on_set_open_browser=self._set_open_browser,
            on_open_folders=lambda parent: FoldersDialog(parent, self.agent_policy, self._agents_error),
            previous_build=self.config.get("previous_build"),
            on_rollback=None if self.exit_btn.winfo_manager() else self._start_rollback,
            theme=self._theme, on_set_theme=self._set_theme,
        )


    # ── llama.cpp updates ─────────────────────────────────────

    def _check_for_update(self):
        # Background thread, once per start. WinGet or network errors show nothing.
        found = UpdateRunner(lambda line: None).check()
        if found:
            self.master.after(0, self._show_update_notice, *found)

    def _show_update_notice(self, installed, newest):
        self._update_builds = (installed, newest)
        self._notice_label.config(text=f"llama.cpp b{newest} is out (you have b{installed}). "
                                       "Updating accepts the package's terms.")
        self._notice.grid(row=1, column=0, padx=24, pady=(16, 0), sticky="ew")
        # Windows locks a running llama-server.exe, so no updates while running.
        running = bool(self.exit_btn.winfo_manager())
        self._update_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        if not running:
            self._resize_config()

    def _start_update(self):
        installed, newest = self._update_builds
        self.config["previous_build"] = f"b{installed}"
        save_config(self.config)
        model_path, mmproj = self._models.get(self.model_var.get(), (None, None))
        runners = []

        def job(report):
            runners.append(UpdateRunner(report))
            return runners[0].update(installed, newest, model_path, mmproj, API_KEY_FILE)
        UpdateDialog(self.master, f"Update to b{newest}", job,
                     on_done=lambda ok: self._update_done(ok, runners and runners[0].rolled_back))

    def _update_done(self, ok, rolled_back):
        if ok:
            self._notice.grid_remove()
            self._resize_config()
        elif rolled_back:
            # Back on the build previous_build names: nothing to roll back to. A failed
            # roll back keeps it, so Settings can retry from the broken build.
            self.config.pop("previous_build", None)
            save_config(self.config)

    def _start_rollback(self, build):
        model_path, mmproj = self._models.get(self.model_var.get(), (None, None))
        UpdateDialog(self.master, f"Roll back to b{build}",
                     lambda report: UpdateRunner(report).rollback_and_check(
                         build, model_path, mmproj, API_KEY_FILE),
                     on_done=self._rollback_done)

    def _rollback_done(self, ok):
        # Cleared so the startup notice offers the newer build again.
        if ok:
            self.config.pop("previous_build", None)
            save_config(self.config)


    # ── Window sizing ─────────────────────────────────────────

    def _resize_config(self):
        extra = H_NOTICE if self._notice.winfo_manager() else 0
        self.master.geometry(f"{W}x{H_CONFIG + extra}")

    def _set_theme(self, theme):
        # From Settings: applies at once, like the Web UI's own theme choice.
        self._theme = theme
        self.config["theme"] = theme
        save_config(self.config)
        name = resolve_theme(theme, windows_light())
        if name != CURRENT_THEME:
            apply_theme(self.master, name)

    def _watch_windows_theme(self):
        # In System mode, follow a change of Windows' setting. Runs on the UI thread (tkinter isn't thread-safe).
        if self._theme == "system":
            name = resolve_theme("system", windows_light())
            if name != CURRENT_THEME:
                apply_theme(self.master, name)
        self.master.after(3000, self._watch_windows_theme)

    def _set_open_browser(self, open_browser):
        self._open_browser = open_browser
        self.config["open_browser"] = open_browser
        save_config(self.config)

    def _copy_api_url(self):
        self.master.clipboard_clear()
        self.master.clipboard_append(AGENT_API_URL)
        self._api_label.config(text="copied")
        self.master.after(1200, lambda: self._api_label.config(text=AGENT_API_URL))

    def _copy_key(self, key, label):
        self.master.clipboard_clear()
        self.master.clipboard_append(key)
        label.config(text="copied")
        self.master.after(1200, lambda: label.config(text="click to copy"))


    # ── Two-state view switching ──────────────────────────────

    def _enter_running_state(self):
        # Hide config widgets and show the status bar, two log panes + exit button.
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid_remove()
        self._update_btn.config(state=tk.DISABLED)
        self.status_bar.grid(row=5, column=0,
                             sticky="ew", padx=24, pady=(16, 0))
        self.api_bar.grid(row=6, column=0, sticky="ew", padx=24, pady=(8, 0))
        self.master.grid_rowconfigure(7, weight=1)
        self.log_pane.grid(row=7, column=0,
                           sticky="nsew", padx=24, pady=(8, 8))
        self.exit_row.grid(row=8, column=0,
                           sticky="ew", padx=24, pady=(0, 24))
        # winfo_manager() of exit_btn tells the running state apart (Settings, update notice).
        self.exit_btn.pack(side="left", fill="x", expand=True)
        self.test_btn.config(state=tk.DISABLED)
        self.master.resizable(True, True)
        self.master.geometry(f"{W}x{H_RUNNING}")

    def _exit_running_state(self):
        # Kill both processes, then restore the config view.
        self._stop_health_poll()  # first, so the kills below aren't reported as an agents crash
        self._kill_all()
        self._stop_elapsed()
        self.master.resizable(False, False)
        self.master.grid_rowconfigure(7, weight=0)
        self.status_bar.grid_remove()
        self.api_bar.grid_remove()
        self.log_pane.grid_remove()
        self.exit_btn.pack_forget()
        self.exit_row.grid_remove()
        self.test_btn.config(state=tk.DISABLED)
        # Reset pane labels for the next launch
        self._llama_label_var.set("llama-server")
        self._uvx_label_var.set("UVX Proxy")
        self._proxy_label_var.set("System Prompt Proxy")
        for w in (self.model_outer, self.preset_outer, self.run_button):
            w.grid()
        self._update_btn.config(state=tk.NORMAL)
        self._resize_config()


    # ── Model & preset list management ───────────────────────

    def _refresh_models(self, init=False):
        self._models = detect_models(self.llama_dir)
        menu = self.model_menu.menu
        menu.delete(0, "end")
        if self._models:
            display = sorted(self._models, key=str.lower)
            for d in display:
                menu.add_command(label=d, command=lambda v=d: self.model_var.set(v))
            current = self.model_var.get()
            if init or not current or current not in display:
                last = self.config.get("last_model")
                if init and last and last in display:
                    self.model_var.set(last)
                else:
                    self.model_var.set(display[0])
        else:
            menu.add_command(label="(no .gguf models found)",
                             command=lambda: self.model_var.set("(no .gguf models found)"))
            self.model_var.set("(no .gguf models found)")

    def _on_model_changed(self):
        m = self.model_var.get()
        if m and not m.startswith("("):
            self.config["last_model"] = m
            save_config(self.config)
        self._refresh_preset_menu(reset=True)

    def _filtered_presets(self):
        model = self.model_var.get()
        if not model or model.startswith("("):
            return {}
        return {n: d for n, d in self.presets.items() if preset_matches_model(d, model)}

    def _refresh_preset_menu(self, reset=False):
        # reset=True (startup, model change) always selects Auto; other presets
        # are manual overrides and are not remembered between launches.
        filtered = self._filtered_presets()
        menu = self.preset_menu.menu
        menu.delete(0, "end")
        if filtered:
            for name in filtered:
                menu.add_command(label=name, command=lambda v=name: self.preset_var.set(v))
            current = self.preset_var.get()
            if reset or current not in filtered:
                self.preset_var.set(AUTO_PRESET if AUTO_PRESET in filtered else next(iter(filtered)))
            else:
                self._load_preset()
        else:
            menu.add_command(label="(no matching presets)",
                             command=lambda: self.preset_var.set("(no matching presets)"))
            self.preset_var.set("(no matching presets)")
            self.llama_cmd = ""

    def _load_preset(self):
        # Build the launch command by substituting {model} with the actual file
        # path, and attach the model's mmproj projector when it has one.
        name  = self.preset_var.get()
        entry = self._models.get(self.model_var.get())
        if not name or name not in self.presets:
            return
        if entry is None:
            self.llama_cmd = ""
            return
        model_path, mmproj = entry
        self.llama_cmd = build_launch_command(self.presets[name]["command"],
                                              model_path, mmproj, API_KEY_FILE)


    # ── Preset CRUD ───────────────────────────────────────────

    def _add_preset(self):
        models = sorted(detect_models(self.llama_dir), key=str.lower)
        dlg = PresetDialog(self.master, "Add Preset", available_models=models)
        self.master.wait_window(dlg)
        if dlg.result_name:
            if dlg.result_name in self.presets:
                messagebox.showerror("Duplicate",
                    f'A preset named "{dlg.result_name}" already exists.')
                return
            self.presets[dlg.result_name] = {
                "command": dlg.result_command, "models": dlg.result_models
            }
            self.config["presets"] = self.presets
            save_config(self.config)
            self._refresh_preset_menu()
            if dlg.result_name in self._filtered_presets():
                self.preset_var.set(dlg.result_name)

    def _edit_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            messagebox.showwarning("No Preset", "Please select a preset to edit.")
            return
        data   = self.presets[name]
        models = sorted(detect_models(self.llama_dir), key=str.lower)
        dlg    = PresetDialog(self.master, "Edit Preset", name=name,
                              command=data["command"],
                              model_keywords=data.get("models", []),
                              available_models=models)
        self.master.wait_window(dlg)
        if dlg.result_name:
            if dlg.result_name != name and dlg.result_name in self.presets:
                messagebox.showerror("Duplicate",
                    f'A preset named "{dlg.result_name}" already exists.')
                return
            del self.presets[name]
            self.presets[dlg.result_name] = {
                "command": dlg.result_command, "models": dlg.result_models
            }
            self.config["presets"] = self.presets
            save_config(self.config)
            self._refresh_preset_menu()
            if dlg.result_name in self._filtered_presets():
                self.preset_var.set(dlg.result_name)

    def _delete_preset(self):
        name = self.preset_var.get()
        if not name or name not in self.presets:
            messagebox.showwarning("No Preset", "Please select a preset to delete.")
            return
        if name == AUTO_PRESET:
            messagebox.showinfo("Auto Preset",
                "The Auto preset is the default for every model and can't be deleted.")
            return
        if not messagebox.askyesno("Confirm Delete", f'Delete preset "{name}"?'):
            return
        del self.presets[name]
        self.config["presets"] = self.presets
        save_config(self.config)
        self._refresh_preset_menu()


    # ── Server health polling ─────────────────────────────────

    def _start_health_poll(self):
        self._polling = True
        def poll_loop():
            browser_opened = False
            shown = set()  # the agents server is optional: its crash shows for one poll, then llama's status returns
            # Role names, looked up when the label updates, so a theme switch can't leave stale colours.
            colors = {"ok": "SUCCESS", "warn": "WARN", "error": "DANGER", "idle": "TEXT_MUT"}
            while self._polling:
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8080/health", timeout=2
                    ) as resp:
                        health = "ok" if json.loads(resp.read().decode()).get("status") == "ok" else "loading"
                except urllib.error.HTTPError as e:
                    # llama-server answers 503 while the model is still loading
                    health = "loading" if e.code == 503 else "offline"
                except Exception:
                    health = "offline"
                procs = {"llama-server": self._llama_proc, "UVX proxy": self._uvx_proc,
                         "prompt proxy": self._proxy_proc, "agents server": self._agents_proc}
                codes = {n: p.poll() for n, p in procs.items() if p is not None}
                text, level = health_status(codes, health, shown)
                agents_code = codes.get("agents server")
                if agents_code is not None and "agents server" not in shown and self._polling:
                    shown.add("agents server")
                    note = f"[agents] exited (code {agents_code}); agent jobs and the Jobs panel are off until the next launch"
                    self.master.after(0, lambda n=note: self._append_log(self.uvx_log, n))
                if level == "ok" and not browser_opened and self._open_browser:
                    browser_opened = True
                    self.master.after(0, lambda: webbrowser.open("http://127.0.0.1:8080/"))
                self.master.after(0, lambda c=colors[level], t=text: self._health_label.config(fg=_palette[c], text=t))
                self.master.after(0, lambda ok=level == "ok": self._set_test_btn(ok))
                time.sleep(3)
        threading.Thread(target=poll_loop, daemon=True).start()

    def _stop_health_poll(self):
        self._polling = False
        self._health_label.config(text="● waiting", fg=TEXT_MUT)


    # ── Model test ────────────────────────────────────────────

    def _set_test_btn(self, healthy):
        # Only while llama-server is online and no test is running. _polling drops
        # a poll result that arrives after Exit Processes.
        busy = self._test_dialog is not None and self._test_dialog.busy()
        self.test_btn.config(state=tk.NORMAL if healthy and self._polling and not busy else tk.DISABLED)

    def _start_model_test(self):
        if not messagebox.askyesno("Test this model",
                "15 to 60 minutes, depending on the model. Uses the GPU. Web UI chat is paused until it finishes or you stop it."):
            return
        self.test_btn.config(state=tk.DISABLED)
        try:
            write_test_lock(MODEL_TEST_LOCK)
            proc = subprocess.Popen(
                model_test_command(sys.executable),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as e:
            clear_test_lock(MODEL_TEST_LOCK)
            messagebox.showerror("Test this model", f"The test couldn't start:\n{e}")
            return
        self._test_dialog = TestDialog(self.master, proc)


    # ── Elapsed time ──────────────────────────────────────────

    def _start_elapsed(self):
        self._launch_time = time.time()
        self._tick_elapsed()

    def _tick_elapsed(self):
        if self._launch_time is None:
            return
        elapsed = int(time.time() - self._launch_time)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._elapsed_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self._elapsed_after_id = self.master.after(1000, self._tick_elapsed)

    def _stop_elapsed(self):
        self._launch_time = None
        if hasattr(self, "_elapsed_after_id"):
            self.master.after_cancel(self._elapsed_after_id)
        self._elapsed_var.set("00:00:00")


    # ── System tray ───────────────────────────────────────────

    def _setup_tray(self):
        if not _TRAY_AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._show_from_tray, default=True),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self._tray_icon = pystray.Icon(
            "Llama Launcher", _make_tray_image(), "Llama Launcher", menu
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _show_from_tray(self, icon=None, item=None):
        self.master.after(0, self.master.deiconify)

    def _hide_to_tray(self):
        self.master.withdraw()

    def _tray_exit(self, icon=None, item=None):
        self._tray_icon.stop()
        self.master.after(0, self._force_exit)

    def _force_exit(self):
        if hasattr(self, "_llama_proc"):
            self._kill_all()
        self.master.destroy()


    # ── Process management ────────────────────────────────────

    def _free_port(self, cmd, default_port, widget):
        # Free the port this command will use, but only from a leftover of our
        # own (see is_own_process). Anything else aborts the launch.
        m = re.search(r'--port\s+(\d+)', cmd)
        port = int(m.group(1)) if m else default_port
        netstat = subprocess.run("netstat -ano", shell=True, capture_output=True, text=True).stdout
        app_dir = os.path.dirname(os.path.abspath(__file__))
        for pid in listening_pids(netstat, port) - {"0"}:
            info = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; $p.Name; $p.CommandLine"],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.splitlines()
            name = info[0] if info else "unknown"
            if not is_own_process(name, " ".join(info[1:]), app_dir):
                raise RuntimeError(f"port {port} is in use by {name} (PID {pid}), "
                                   "which the launcher didn't start. Close it and launch again.")
            subprocess.run(f"taskkill /F /T /PID {pid}", shell=True, capture_output=True)
            self._append_log(widget, f"  Port {port} was in use — freed PID {pid} ({name})")

    def _kill_proc(self, proc, widget=None, prefix=""):
        # Kill a process and its full child tree (/T) — ensures GPU workers are also stopped.
        if proc is None:
            return
        try:
            subprocess.run(f"taskkill /F /T /PID {proc.pid}",
                           shell=True, capture_output=True)
            if widget:
                self._append_log(widget, f"{prefix}─── killed (PID {proc.pid}) ───")
        except Exception as e:
            if widget:
                self._append_log(widget, f"{prefix}─── kill failed: {e} ───")

    def _kill_all(self):
        # A running model test stops first, while the agents server can still cancel its jobs.
        if self._test_dialog is not None:
            try:
                self._test_dialog.stop_now()
            except Exception:
                pass
        self._kill_proc(self._llama_proc, getattr(self, "llama_log", None))
        self._llama_proc = None
        self._kill_proc(self._uvx_proc, getattr(self, "uvx_log", None))
        self._uvx_proc = None
        self._kill_proc(self._proxy_proc, getattr(self, "proxy_log", None))
        self._proxy_proc = None
        self._kill_proc(self._agents_proc, getattr(self, "uvx_log", None), "[agents] ")
        self._agents_proc = None
        # Jobs ran inside the agents server, which is gone now (so it can't race these writes).
        # A failure here (e.g. unreadable sandbox) must never stop the exit.
        try:
            agent_jobs.JobStore(self.agent_policy.jobs_dir).cancel_unfinished()
        except Exception:
            pass
        clear_test_lock(MODEL_TEST_LOCK)  # a leftover from a crash would keep Web UI chat paused

    def _on_close(self):
        # If servers are running and tray is available, minimize to tray instead of closing.
        running = (self._llama_proc, self._uvx_proc, self._proxy_proc, self._agents_proc)
        if any(p is not None for p in running) and _TRAY_AVAILABLE:
            self._hide_to_tray()
        else:
            if hasattr(self, "_llama_proc"):
                self._kill_all()
            self.master.destroy()


    # ── Log helpers ───────────────────────────────────────────

    def _append_log(self, widget, text):
        # Append one line to a log pane. Safe to call from any thread via .after().
        widget.config(state=tk.NORMAL)
        widget.insert("end", text + "\n")
        if self._autoscroll_var.get():
            widget.see("end")
        widget.config(state=tk.DISABLED)

    def _stream_output(self, proc, widget, prefix=""):
        # Read the process's stdout line-by-line in a background thread and
        # schedule each line onto the main thread so tkinter stays thread-safe.
        # This thread must keep draining the pipe: if it stopped, the pipe would
        # fill and the child would block on its next write. Output is decoded as
        # UTF-8 with errors="replace" so an odd byte can't kill it. prefix marks
        # lines from a process that shares a pane (the agents server).
        def reader():
            try:
                for line in proc.stdout:
                    line = prefix + line.rstrip("\r\n")
                    widget.after(0, lambda l=line: self._append_log(widget, l))
            except Exception:
                pass
            widget.after(0, lambda: self._append_log(widget, f"{prefix}─── process ended ───"))
        threading.Thread(target=reader, daemon=True).start()


    # ── Launch sequence ───────────────────────────────────────

    def _start_agents_server(self):
        # Optional (ADR 0007): any failure is logged and the launch carries on.
        # A crash after start shows once in the status bar as "agents server exited", then in the log.
        if self._agents_error:
            self._append_log(self.uvx_log, f"[agents] not started: {self._agents_error}")
            return
        port = agent_llm.AGENTS_PORT
        try:
            self._free_port(f"--port {port}", port, self.uvx_log)
            self._agents_proc = subprocess.Popen(
                ["uv", "run", "--script", AGENTS_SERVER, "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=os.path.dirname(os.path.abspath(__file__)),
                creationflags=subprocess.CREATE_NO_WINDOW,  # no shell, so hide uv's console
            )
        except Exception as e:
            self._agents_proc = None
            self._append_log(self.uvx_log, f"[agents] not started: {e}")
            return
        self._uvx_label_var.set(f"{self._uvx_label_var.get()}  ·  agents PID {self._agents_proc.pid}")
        self._append_log(self.uvx_log, f"[agents] launched on 127.0.0.1:{port} (PID {self._agents_proc.pid})")
        self._stream_output(self._agents_proc, self.uvx_log, "[agents] ")

    def run_commands(self):
        uvx_cmd = resolve_uvx_command(self.uvx_command.strip(), CONFIG_FILE)

        if not self.llama_cmd:
            messagebox.showerror("No Command",
                "No Llama command available. Please select a model and preset.")
            return
        if not uvx_cmd:
            messagebox.showerror("No Command",
                "UVX command is empty. Open Settings to fill it in.")
            return

        # Clear all log panes before entering the running state
        for w in (self.llama_log, self.uvx_log, self.proxy_log):
            w.config(state=tk.NORMAL)
            w.delete("1.0", "end")
            w.config(state=tk.DISABLED)
        self._enter_running_state()

        model_name = self.model_var.get()
        cwd = self.llama_dir if self.llama_dir else None

        try:
            if remove_stale_agents_entry(self.config):
                save_config(self.config)
            self._free_port(self.llama_cmd, 8081, self.llama_log)
            self._llama_proc = subprocess.Popen(
                self.llama_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
            )
            self._llama_label_var.set(f"llama-server  ·  {self.model_var.get()}  ·  PID {self._llama_proc.pid}")
            self._append_log(self.llama_log, f"Launched (PID {self._llama_proc.pid})")
            self._stream_output(self._llama_proc, self.llama_log)

            self._free_port(uvx_cmd, 8001, self.uvx_log)
            self._uvx_proc = subprocess.Popen(
                uvx_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
            )
            self._uvx_label_var.set(f"UVX Proxy  ·  PID {self._uvx_proc.pid}")
            self._append_log(self.uvx_log, f"Launched (PID {self._uvx_proc.pid})")
            self._stream_output(self._uvx_proc, self.uvx_log)

            self._start_agents_server()

            self._free_port("--port 8080", 8080, self.proxy_log)
            proxy_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.py")
            if not os.path.isfile(proxy_script):
                raise FileNotFoundError(f"proxy.py not found at {proxy_script}")
            self._proxy_proc = subprocess.Popen(
                [sys.executable, proxy_script, "8080", "8081"],
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._proxy_label_var.set(f"System Prompt Proxy  ·  PID {self._proxy_proc.pid}")
            self._append_log(self.proxy_log, f"Launched (PID {self._proxy_proc.pid})")
            self._stream_output(self._proxy_proc, self.proxy_log)

            self._start_health_poll()
            self._start_elapsed()


        except Exception as e:
            self._append_log(self.llama_log, f"LAUNCH FAILED: {e}")
            self._kill_all()
            self._exit_running_state()


# ============================================================
# ENTRY POINT
# Creates the root window, hands it to CommandLauncherGUI,
# and starts the tkinter event loop.
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app  = CommandLauncherGUI(root)
    root.mainloop()
