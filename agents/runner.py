"""Agent loop, the three job kinds, and plan approval. Standard library only."""
import json
import os
import threading
import time
from urllib.parse import urlsplit

from . import plans

# Held while plan files move (apply, restore) and while the trash is emptied, so Empty trash
# can never delete files an apply or restore is moving, wherever that was started.
FILE_MOVES = threading.Lock()

MAX_CALLS = 40
MAX_SECONDS = 20 * 60
MAX_RESULT_CHARS = 6000
CONTEXT_CHARS = 40000
ELIDED = "[older tool result removed to save space]"
TRUNCATED = "\n[truncated]"


class Context:
    def __init__(self, policy, store, chat, mcp_call):
        self.policy, self.store, self.chat, self.mcp_call = policy, store, chat, mcp_call


def tool(name, description, params, required, fn):
    schema = {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": params, "required": required}}}
    return name, schema, fn


def _invoke(fns, name, raw_args):
    if name not in fns:
        return f"error: unknown tool {name!r}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
        return str(fns[name](**args))[:MAX_RESULT_CHARS]
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


def _trim(messages):
    """Keep the history inside CONTEXT_CHARS by dropping the oldest tool results (never the last 4 messages)."""
    total = sum(len(m.get("content") or "") for m in messages)
    for i in range(len(messages) - 4):
        if total <= CONTEXT_CHARS:
            break
        m = messages[i]
        if m.get("role") == "tool" and m.get("content") != ELIDED:
            total -= len(m.get("content") or "") - len(ELIDED)
            messages[i] = {**m, "content": ELIDED}


def _name_of(path):
    path = str(path or "").rstrip("\\/")
    return os.path.basename(path) or path


def step_text(name, args, result):
    """One line for the jobs panel describing a finished tool call."""
    args = args if isinstance(args, dict) else {}
    result = str(result or "")
    if name == "propose_plan":
        if result.startswith("error:"):
            lines = [l for l in result.splitlines()[1:] if l.strip()]
            return "Plan rejected: " + (lines[0] if lines else result[len("error:"):].strip())
        n = result.split("with ", 1)[-1].split(" ", 1)[0]
        return f"Proposed plan: {n} changes"
    if result.startswith("error:"):
        return "Failed: " + result[len("error:"):].strip()
    if name == "web_search":
        return f'Searched "{args.get("query", "")}"'
    if name == "fetch_url":
        try:  # never show user:password from the URL
            parts = urlsplit(str(args.get("url", "")))
            host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
            path = parts.path
        except ValueError:
            return "Read a web page"
        return "Read " + (host + path)[:80]
    if name == "list_dir":
        return "Looked in " + _name_of(args.get("path"))
    if name == "read_file":
        return "Read " + _name_of(args.get("path"))
    if name == "write_file":
        return "Wrote " + _name_of(args.get("path"))
    if name == "propose_steps":
        return result[:1].upper() + result[1:]
    return f"Used {name}"


def _parse_args(raw):
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def run_agent(chat, system, task, tools, max_calls=MAX_CALLS, max_seconds=MAX_SECONDS, clock=time.monotonic,
              on_step=None):
    schemas = [t[1] for t in tools]
    fns = {t[0]: t[2] for t in tools}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    start, used, turns, last_text = clock(), 0, 0, ""

    def finish(text, reason):
        text = text or last_text
        if not text.strip():
            reason = f"{reason}; the model gave no final answer" if reason else "the model gave no final answer"
        return text, reason

    while True:
        _trim(messages)
        try:
            reply = chat(messages, schemas)
        except (RuntimeError, OSError) as e:
            if turns == 0:
                raise
            return last_text, f"stopped: model error: {e}"
        turns += 1
        text = reply.get("content") or ""
        if text.strip():
            last_text = text
        requested = reply.get("tool_calls") or []
        if not requested:
            return finish(text, "")
        messages.append({"role": "assistant", "content": text, "tool_calls": requested})
        timed_out, limited = clock() - start >= max_seconds, False
        for c in requested:
            call_id = c.get("id", "") if isinstance(c, dict) else ""
            if timed_out or used >= max_calls:
                limited, result = True, "Limit reached."
            else:
                used += 1
                fn = c.get("function") if isinstance(c, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                    result = _invoke(fns, fn["name"], fn.get("arguments"))
                    if on_step:
                        try:
                            on_step(step_text(fn["name"], _parse_args(fn.get("arguments")), result))
                        except Exception:
                            pass  # progress lines are best-effort
                else:
                    result = "error: malformed tool call"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        if limited:
            reason = (f"stopped after {used} tool calls" if used >= max_calls
                      else f"stopped after {max_seconds // 60} minutes")
            messages.append({"role": "user", "content": "You are out of tool calls. Write your final answer now from what you have."})
            _trim(messages)
            try:
                return finish(chat(messages, None).get("content") or "", reason)
            except (RuntimeError, OSError) as e:
                return last_text, f"stopped: model error: {e}"


def _file_tools(policy, sandbox_only):
    def full_path(path):
        return path if os.path.isabs(path) else os.path.join(policy.sandbox, path)

    def readable(full):
        return policy.can_write(full) if sandbox_only else policy.can_read(full)

    def no_access(path):
        if sandbox_only:
            return f"error: you can only read files you wrote inside {policy.sandbox}"
        return f"error: no read access to {path}. Ask the user to add the folder."

    def list_dir(path):
        full = full_path(path)
        if not readable(full):
            return no_access(path)
        entries = sorted(os.scandir(full), key=lambda e: e.name.lower())[:500]
        lines = [f"[dir] {e.name}" if e.is_dir() else f"{e.name}  ({e.stat().st_size} bytes)" for e in entries]
        return "\n".join(lines) or "(empty)"

    def read_file(path):
        full = full_path(path)
        if not readable(full):
            return no_access(path)
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_RESULT_CHARS + 1)
        if len(text) > MAX_RESULT_CHARS:
            return text[:MAX_RESULT_CHARS - len(TRUNCATED)] + TRUNCATED
        return text

    def write_file(path, content):
        full = full_path(path)
        if not policy.can_write(full):
            return f"error: you can only write inside {policy.sandbox}"
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"wrote {full}"

    s = {"type": "string"}
    return [tool("list_dir", "List a folder.", {"path": s}, ["path"], list_dir),
            tool("read_file", f"Read a text file (first {MAX_RESULT_CHARS:,} characters).", {"path": s}, ["path"], read_file),
            tool("write_file", "Write a file inside the sandbox.", {"path": s, "content": s}, ["path", "content"], write_file)]


def file_tools(policy):
    """Read the user's listed folders (and the sandbox), write the sandbox. Never combined with web tools."""
    return _file_tools(policy, False)


def sandbox_tools(policy):
    """Read and write only inside the sandbox. These go with the web tools."""
    return _file_tools(policy, True)


def web_tools(mcp_call):
    s = {"type": "string"}
    return [tool("web_search", "Search the web.", {"query": s}, ["query"],
                 lambda query: mcp_call("ddg-search", "search", {"query": query, "max_results": 8})),
            tool("fetch_url", "Read a web page as text.", {"url": s}, ["url"],
                 lambda url: mcp_call("fetch", "fetch", {"url": url, "max_length": MAX_RESULT_CHARS}))]


RESEARCH_PROMPT = (
    "You are a careful research assistant. Use web_search and fetch_url to find primary sources "
    "(official docs, papers, first-party pages). Then write a Markdown report: a short summary first, "
    "then the findings, then a Sources list. Cite a source URL for every claim, and say where sources disagree.")

PLAN_PROMPT = (
    "You plan projects. Research as needed, then write a Markdown plan with numbered steps. You cannot look "
    "inside the user's own folders. Before your final answer, call propose_steps once with follow-up jobs "
    "worth running, each either "
    '{"kind": "research", "topic": "..."} or {"kind": "organize", "folder": "<absolute path>", "goal": "..."}. '
    "Name a folder in an organize step only if the goal text gives its path.")

ORGANIZE_PROMPT = (
    "You organise files inside {folder}. Look around with list_dir and read_file, then call propose_plan "
    "once with the full list of operations, using absolute paths:\n"
    '{{"op": "mkdir", "path": ...}}, {{"op": "move", "src": ..., "dst": ...}} (also for renames), '
    '{{"op": "remove", "path": ...}} (goes to a trash the user can restore).\n'
    "Nothing runs until the user approves. If propose_plan reports errors, fix them and call it again. "
    "Finish with a short summary of the plan.")

REVIEW_PROMPT = (
    "You review a file-organising plan before a person approves it. The goal was: {goal}\n"
    "List every operation that looks wrong, risky, or unrelated to the goal, especially removals, "
    "with its number and the reason. If everything looks right, say so in one line.")


def _steps(job, ctx):
    return lambda text: ctx.store.append_step(job["id"], text)


def run_research(job, ctx):
    tools = web_tools(ctx.mcp_call) + sandbox_tools(ctx.policy)
    text, stop = run_agent(ctx.chat, RESEARCH_PROMPT, job["args"]["topic"], tools, on_step=_steps(job, ctx))
    if stop:
        ctx.store.append_step(job["id"], f"Stopped: {stop}")
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.update(job["id"], state="done", note=stop)


def run_plan(job, ctx):
    saved = []

    def propose_steps(steps):
        good = [s for s in steps if isinstance(s, dict) and (
            (s.get("kind") == "research" and s.get("topic"))
            or (s.get("kind") == "organize" and s.get("folder") and s.get("goal")))]
        saved[:] = good
        return f"saved {len(good)} follow-up jobs"

    steps_param = {"steps": {"type": "array", "items": {"type": "object"}}}
    tools = web_tools(ctx.mcp_call) + sandbox_tools(ctx.policy) + [
        tool("propose_steps", "Save follow-up jobs for the user to queue.", steps_param, ["steps"], propose_steps)]
    text, stop = run_agent(ctx.chat, PLAN_PROMPT, job["args"]["goal"], tools, on_step=_steps(job, ctx))
    if stop:
        ctx.store.append_step(job["id"], f"Stopped: {stop}")
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.write_json(job["id"], "steps.json", saved)
    ctx.store.update(job["id"], state="done", note=stop)


def run_organize(job, ctx):
    folder, goal = job["args"]["folder"], job["args"]["goal"]
    proposed = []

    def propose_plan(operations):
        errors = plans.validate_plan(operations, ctx.policy, root=folder)
        if errors:
            proposed[:] = []  # an earlier plan must not survive under a summary of this one
            return "error: plan rejected:\n" + "\n".join(errors)
        proposed[:] = operations
        return f"plan saved with {len(operations)} operations"

    ops_param = {"operations": {"type": "array", "items": {"type": "object"}}}
    # No write_file: the model must not copy listed-folder contents into the sandbox, which web-enabled jobs read.
    tools = [t for t in file_tools(ctx.policy) if t[0] != "write_file"] + [
        tool("propose_plan", "Propose the full list of file operations.", ops_param, ["operations"], propose_plan)]
    text, stop = run_agent(ctx.chat, ORGANIZE_PROMPT.format(folder=folder), goal, tools, on_step=_steps(job, ctx))
    if stop:
        ctx.store.append_step(job["id"], f"Stopped: {stop}")
    ctx.store.write_text(job["id"], "result.md", text)
    if not proposed:
        ctx.store.update(job["id"], state="failed", note="no valid plan was proposed" + (f"; {stop}" if stop else ""))
        return
    ctx.store.write_json(job["id"], "plan.json", proposed)
    review = ctx.chat([{"role": "system", "content": REVIEW_PROMPT.format(goal=goal)},
                       {"role": "user", "content": json.dumps(proposed, indent=2)}], None)
    ctx.store.append_step(job["id"], "Reviewed the plan")
    ctx.store.write_text(job["id"], "review.md", review.get("content") or "")
    ctx.store.update(job["id"], state="awaiting_approval", note=stop)


KINDS = {"research": run_research, "plan": run_plan, "organize": run_organize}


def run_job(job, ctx):
    KINDS[job["kind"]](job, ctx)


def approve(job_id, ctx):
    try:
        job = ctx.store.transition(job_id, ("awaiting_approval",), "applying")
    except (KeyError, ValueError) as e:
        return f"Not applied: {e}"
    try:
        ops = ctx.store.read_json(job_id, "plan.json")
        errors = plans.validate_plan(ops, ctx.policy, root=job["args"]["folder"])
    except Exception as e:
        ctx.store.update(job_id, state="awaiting_approval", note=f"could not start applying: {type(e).__name__}: {e}")
        return f"Not applied; nothing was changed: {type(e).__name__}: {e}"
    if errors:
        ctx.store.update(job_id, state="awaiting_approval", note="files changed since the plan was made")
        return "Not applied; the plan no longer matches the files:\n" + "\n".join(errors)
    try:
        with FILE_MOVES:
            # The log is saved after every operation, so a crash still leaves something Restore can use.
            log, error = plans.apply_plan(ops, job_id, ctx.policy,
                                          on_step=lambda done: ctx.store.write_json(job_id, "applied.json", done))
            ctx.store.write_json(job_id, "applied.json", log)
    except Exception as e:
        ctx.store.update(job_id, state="failed", note=f"apply failed: {type(e).__name__}: {e}")
        return f"Apply failed: {type(e).__name__}: {e}. Restore undoes the completed steps."
    if error:
        ctx.store.update(job_id, state="failed", note=f"partly applied: {error}")
        return f"Partly applied ({len(log)} of {len(ops)}): {error}. Restore undoes the completed steps."
    ctx.store.update(job_id, state="applied")
    return f"Applied {len(log)} operations."


def reject(job_id, ctx):
    try:
        ctx.store.transition(job_id, ("awaiting_approval",), "rejected")
    except (KeyError, ValueError) as e:
        return f"Not rejected: {e}"
    return "Rejected; nothing was changed."


def undo(job_id, ctx):
    try:
        if not ctx.store.has(job_id, "applied.json"):
            return "Nothing to restore: this job never changed any files."
        ctx.store.transition(job_id, ("applied", "failed"), "restoring")
    except (KeyError, ValueError) as e:
        return f"Not restored: {e}"
    try:
        with FILE_MOVES:
            skipped = plans.restore(ctx.store.read_json(job_id, "applied.json"))
    except Exception as e:
        ctx.store.update(job_id, state="failed", note=f"restore failed: {type(e).__name__}: {e}; try Restore again")
        return f"Restore failed: {type(e).__name__}: {e}. You can try Restore again."
    ctx.store.update(job_id, state="restored", note=f"{len(skipped)} skipped" if skipped else "")
    return "Restored." + ("\nSkipped:\n" + "\n".join(skipped) if skipped else "")


def queue_steps(job_id, ctx, submit):
    try:
        job = ctx.store.get(job_id)
        if job.get("steps_queued"):
            return "Those steps were already queued."
        steps = ctx.store.read_json(job_id, "steps.json") if ctx.store.has(job_id, "steps.json") else []
        if not steps:
            return "This job has no follow-up steps."
        ctx.store.transition(job_id, ("done",), "done", steps_queued=True)
    except (KeyError, ValueError) as e:
        return f"Not queued: {e}"
    for s in steps:
        args = {k: v for k, v in s.items() if k != "kind"}
        submit(ctx.store.create(s["kind"], args)["id"])
    return f"Queued {len(steps)} jobs."


def describe(job):
    args = ", ".join(f"{k}={v}" for k, v in job["args"].items())
    note = f" ({job['note']})" if job.get("note") else ""
    return f"{job['id']}  {job['kind']}  {job['state']}{note}  [{args}]"
