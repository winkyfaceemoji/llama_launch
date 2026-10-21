"""The agent jobs' system prompts: the shipped ones, and the user's edits from config.json. Standard library only."""
import json
import string

RESEARCH = (
    "You are a careful research assistant. Use web_search to find primary sources (official docs, papers, "
    "first-party pages), then read them with fetch_url. Search results are only leads: never report what a "
    "snippet says. Then write a Markdown report: a short summary first, then the findings, then a Sources list. "
    "Cite a URL you read with fetch_url for every claim, and say where sources disagree.")

PLAN = (
    "You plan projects. Research as needed, then write a Markdown plan with numbered steps. Cite only pages you "
    "read with fetch_url; search results are only leads. You cannot look "
    "inside the user's own folders. Before your final answer, call propose_steps once with follow-up jobs "
    "worth running, each either "
    '{"kind": "research", "topic": "..."} or {"kind": "organize", "folder": "<absolute path>", "goal": "..."}. '
    "Name a folder in an organize step only if the goal text gives its path.")

ORGANIZE = (
    "You organise files inside {folder}. Look around with list_dir and read_file, then call propose_plan "
    "with the operations, using absolute paths:\n"
    '{{"op": "mkdir", "path": ...}}, {{"op": "move", "src": ..., "dst": ...}} (also for renames), '
    '{{"op": "remove", "path": ...}} (goes to a trash the user can restore).\n'
    "A move's dst is the item's full new path, not just the folder. Example for {folder}\\a.pdf:\n"
    '{{"op": "mkdir", "path": "{folder}\\PDFs"}}, '
    '{{"op": "move", "src": "{folder}\\a.pdf", "dst": "{folder}\\PDFs\\a.pdf"}}\n'
    "Send at most {batch} operations per propose_plan call; each call adds to the plan, so call it again for "
    "the rest. Nothing runs until the user approves. If propose_plan reports errors, fix that batch and send it "
    "again. When every operation is in, finish with a short summary of the plan.")

REVIEW = (
    "You review a file-organising plan before a person approves it. The goal was: {goal}\n"
    "First, list every remove operation by number and say whether the goal explicitly asked to remove that "
    "file. A remove the goal didn't ask for is a problem. Removing most or all files is always worth a "
    "warning, even when asked, so say plainly how many files would be removed. Then list any other operation "
    "that looks wrong, risky, or unrelated to the goal, with its number and the reason. Only if there are no "
    "removes and nothing else is wrong, say in one line that the plan looks right.")

SHIPPED = {"research": RESEARCH, "plan": PLAN, "organize": ORGANIZE, "review": REVIEW}

# The kinds the runner fills in with str.format, and the placeholders each must use. Research and plan aren't
# formatted, so any text is fine there.
PLACEHOLDERS = {"organize": ("folder", "batch"), "review": ("goal",)}
_BRACES = "write literal braces as {{ and }}"
_SAMPLE = {"folder": "C:\\Folder", "batch": 25, "goal": "Sort these files."}  # same types the runner passes


def check(kind, text):
    """None if the runner can fill this prompt in, else a one-line reason to show before saving."""
    wanted = PLACEHOLDERS.get(kind)
    if wanted is None:
        return None
    try:
        used = {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}
    except ValueError:
        return f"Unmatched brace: {_BRACES}."
    unknown = sorted(used - set(wanted))
    if unknown:
        return f"Unknown placeholder {{{unknown[0]}}}: use only {', '.join('{' + w + '}' for w in wanted)}, and {_BRACES}."
    missing = [w for w in wanted if w not in used]
    if missing:
        return f"Missing {', '.join('{' + m + '}' for m in missing)}."
    try:
        text.format(**{w: _SAMPLE[w] for w in wanted})
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
        return f"The runner can't fill this in: {e}."
    return None


def _resolve(config):
    edits = config.get("agent_prompts") if isinstance(config, dict) else None
    edits = edits if isinstance(edits, dict) else {}
    got, notes = {}, []
    for kind, shipped in SHIPPED.items():
        text = edits.get(kind)
        problem = None
        if text and not isinstance(text, str):
            problem = "not text"
        elif text:
            problem = check(kind, text)
        if problem:
            notes.append(f"{kind} prompt in config.json can't be used ({problem}); using the shipped one")
        got[kind] = text if text and not problem else shipped
    return got, notes


def effective(config):
    """Each kind's prompt: the user's edit from config["agent_prompts"], else the shipped one. An edit the
    runner couldn't fill in (a hand edit skips the dialog's check) counts as no edit."""
    return _resolve(config)[0]


def load(path):
    """effective() for the config file at path, plus a note per edit that was skipped. A missing or damaged
    file means the shipped prompts."""
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return dict(SHIPPED), []
    return _resolve(config)
