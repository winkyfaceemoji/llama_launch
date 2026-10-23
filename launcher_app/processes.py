"""Pure helpers for process control: health text, port parsing, own-process check, single instance, model test lock."""
from agents import jobs as agent_jobs
from ctypes import wintypes
import ctypes
import json
import os
import secrets
import threading
from .paths import MODEL_TEST


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


# One launcher per Windows session (ADR 0017): a named mutex marks the first one, and a
# named event lets a second one ask it to show its window. A private kernel32 so these
# argtypes don't touch ctypes.windll.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
INSTANCE_NAME = "LlamaLauncher"


def claim_instance(name=INSTANCE_NAME):
    # The first launcher gets its show-request event back. A second one signals that event
    # (so the first shows its window) and gets None. The event is made before the mutex,
    # so whoever finds the mutex also finds the event.
    event = _kernel32.CreateEventW(None, False, False, f"Local\\{name}.show")
    mutex = _kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return event  # the mutex stays open, and so held, until this process exits
    _kernel32.CloseHandle(mutex)
    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: the first may take the foreground
    _kernel32.SetEvent(event)
    _kernel32.CloseHandle(event)
    return None


def watch_show_requests(event, on_show):
    # on_show runs on this daemon thread, so it must hand UI work to Tk with after(0, ...).
    def loop():
        while _kernel32.WaitForSingleObject(event, 0xFFFFFFFF) == 0:  # INFINITE, WAIT_OBJECT_0
            on_show()
    threading.Thread(target=loop, daemon=True).start()


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
    # Missing is fine. Never fatal: startup and Stop Servers clear it again.
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
