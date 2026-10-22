"""config.json: defaults, loading, saving, prompts, the UVX command."""
from agents import prompts as agent_prompts
from tkinter import messagebox
import json
import os
import secrets
import shutil
from .paths import CONFIG_FILE, MCP_GUARD
from .selection import AUTO_COMMAND, AUTO_PRESET


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


# The prompts the Prompts dialog shows: (key, tab name). "chat" is config["system_prompt"], which the proxy sends
# with Web UI chat; the rest are agent job kinds (agents/prompts.py), kept in config["agent_prompts"].
PROMPT_TABS = [("chat", "Chat"), ("research", "Research"), ("plan", "Plan"),
               ("organize", "Organize"), ("review", "Review")]


def shipped_prompts() -> dict:
    """What Reset to default restores for each prompt."""
    return dict(agent_prompts.SHIPPED, chat=_DEFAULT_CONFIG["system_prompt"])


def set_prompts(config: dict, texts: dict):
    """Store the dialog's texts in config. An agent prompt that is blank or the shipped text is dropped, so a
    later improvement to the shipped prompt reaches it. Returns a reason and changes nothing if any agent prompt
    can't be filled in by the runner."""
    for key, name in PROMPT_TABS:
        problem = agent_prompts.check(key, texts[key]) if key in texts and texts[key].strip() else None
        if problem:
            return f"{name}: {problem}"
    edits = dict(config.get("agent_prompts") or {})
    for key, text in texts.items():
        if key == "chat":
            config["system_prompt"] = text
        elif not text.strip() or text == agent_prompts.SHIPPED[key]:
            edits.pop(key, None)
        else:
            edits[key] = text
    if edits:
        config["agent_prompts"] = edits
    else:
        config.pop("agent_prompts", None)
    return None


def backup_config(path: str = CONFIG_FILE) -> None:
    """Copy config.json to config.json.bak before the prompts are saved: config.json isn't in git, so this is
    the only way back from a bad edit. One backup, replaced each time."""
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")


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
