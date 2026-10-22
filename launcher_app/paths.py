"""File locations. Everything is relative to the repo root (ROOT), never to this package's folder."""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo root
MODELS_DIR  = "Models"  # resolved at runtime relative to llama_dir


CONFIG_FILE = os.path.join(ROOT, "config.json")


# llama-server's API key. Without one, any web page open in the browser can
# call the server on localhost (it allows every origin). Gitignored.
API_KEY_FILE = os.path.join(ROOT, "api_key.txt")


# The agents MCP server (ADR 0007). The launcher starts it on 127.0.0.1:8002
# (agent_llm.AGENTS_PORT) as a fourth process; its log goes to the UVX pane.
AGENTS_SERVER = os.path.join(ROOT, "agents", "server.py")


# Starts mcp-proxy with a Host check against DNS rebinding (ADR 0012).
MCP_GUARD = os.path.join(ROOT, "mcp_guard.py")


# llama-server's own OpenAI-compatible API. Agents connect here directly,
# bypassing the system prompt proxy on 8080.
AGENT_API_URL = "http://127.0.0.1:8081/v1"


# The model test harness. While the lock exists the proxy pauses Web UI chat; it
# holds a random token that lets the test's own chat requests through.
MODEL_TEST = os.path.join(ROOT, "eval", "model_test.py")


MODEL_TEST_LOCK = os.path.join(ROOT, "model_test.lock")


# The window, taskbar and tray icon. Drawn by dev/make_icon.py; rerun it after a palette change.
ICON_FILE = os.path.join(ROOT, "icon.ico")
