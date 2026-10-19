"""Agent loop, the three job kinds, and plan approval. Standard library only."""
import json
import os
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from . import plans

# Held while plan files move (apply, restore) and while the trash is emptied, so Empty trash
# can never delete files an apply or restore is moving, wherever that was started.
FILE_MOVES = threading.Lock()

MAX_CALLS = 40
MAX_SECONDS = 20 * 60
MAX_RESULT_CHARS = 6000
CONTEXT_CHARS = 40000
ELIDED = "[older tool result removed to save space]"
ELIDED_ARGS = json.dumps({"removed": "older tool call arguments removed to save space"})  # still valid JSON
TRUNCATED = "\n[truncated]"
MAX_CHECKS = 2  # times a final answer can be sent back before it is accepted as is
CANCELLED = "cancelled"  # run_agent's stop reason after JobStore.request_cancel
URL = re.compile(r"""https?://[^\s<>()\[\]{}"'`|]+""", re.IGNORECASE)
# Fenced blocks (an unclosed one runs to the end) and inline code: commands and config, not citations.
CODE = re.compile(r"(```|~~~).*?(?:\1|\Z)|`[^`\n]*`", re.DOTALL)


class Context:
    def __init__(self, policy, store, chat, mcp_call, n_ctx=lambda: None):
        self.policy, self.store, self.chat, self.mcp_call = policy, store, chat, mcp_call
        self.n_ctx = n_ctx  # the loaded model's context in tokens, or None


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


def context_chars(n_ctx):
    """The history budget: half the server's context at 3 characters a token (the rest is left for the reply),
    never above CONTEXT_CHARS. --fit can pick a context far smaller than CONTEXT_CHARS assumes."""
    return min(CONTEXT_CHARS, n_ctx * 3 // 2) if n_ctx else CONTEXT_CHARS


def _args(call):
    fn = call.get("function") if isinstance(call, dict) else None
    args = fn.get("arguments") if isinstance(fn, dict) else None
    return args if isinstance(args, str) else ""


def _size(m):
    return len(m.get("content") or "") + sum(len(_args(c)) for c in m.get("tool_calls") or [])


def _without_args(call):
    if len(_args(call)) <= len(ELIDED_ARGS):
        return call
    return {**call, "function": {**call["function"], "arguments": ELIDED_ARGS}}


def _trim(messages, limit=CONTEXT_CHARS):
    """Keep the history inside limit by dropping the oldest tool results and tool-call arguments (a plan batch
    is all arguments), never the last 4 messages."""
    total = sum(_size(m) for m in messages)
    for i in range(len(messages) - 4):
        if total <= limit:
            break
        m = messages[i]
        if m.get("role") == "tool" and m.get("content") != ELIDED:
            short = {**m, "content": ELIDED}
        elif m.get("tool_calls"):
            short = {**m, "tool_calls": [_without_args(c) for c in m["tool_calls"]]}
        else:
            continue
        total -= _size(m) - _size(short)
        messages[i] = short


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
        m = re.match(r"added (\d+) operations; the plan now has (\d+)", result)
        return f"Added {m[1]} changes to the plan ({m[2]} in all)" if m else "Proposed plan changes"
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
              on_step=None, check=None, max_checks=MAX_CHECKS, cancelled=lambda: False, context_chars=CONTEXT_CHARS):
    """check(final_text) returns None to accept a final answer, or a message that sends it back (max_checks times).
    cancelled() is asked before each model call; when true the loop stops with stop "cancelled"."""
    schemas = [t[1] for t in tools]
    fns = {t[0]: t[2] for t in tools}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    start, used, turns, checks, last_text = clock(), 0, 0, 0, ""

    def finish(text, reason):
        text = text or last_text
        if not text.strip():
            reason = f"{reason}; the model gave no final answer" if reason else "the model gave no final answer"
        return text, reason

    while True:
        if cancelled():
            return last_text, CANCELLED
        _trim(messages, context_chars)
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
            objection = check(text) if check and checks < max_checks else None
            if not objection:
                return finish(text, "")
            checks += 1
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": objection}]
            continue
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
            if cancelled():
                return last_text, CANCELLED
            messages.append({"role": "user", "content": "You are out of tool calls. Write your final answer now from what you have."})
            _trim(messages, context_chars)
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


def web_tools(mcp_call, on_fetch=None):
    """on_fetch(url) runs after each page that was read without an error."""
    def fetch_url(url):
        text = mcp_call("fetch", "fetch", {"url": url, "max_length": MAX_RESULT_CHARS})
        if on_fetch:
            on_fetch(url)
        return text

    s = {"type": "string"}
    return [tool("web_search", "Search the web.", {"query": s}, ["query"],
                 lambda query: mcp_call("ddg-search", "search", {"query": query, "max_results": 8})),
            tool("fetch_url", "Read a web page as text.", {"url": s}, ["url"], fetch_url)]


def _same_page(url):
    """A URL reduced so a citation matches the page that was fetched: no trailing punctuation, slash or fragment."""
    parts = urlsplit(url.rstrip(".,;:!?*_"))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def _outside_code(match):
    # A span that is only a link (URL: `https://...`) is a citation written as code; a URL in a command isn't.
    span = match.group(0)
    if span.startswith("`") and not span.startswith("```") and URL.fullmatch(span.strip("` ")):
        return span.strip("` ")
    return " "


def cited_urls(text):
    return list(dict.fromkeys(_same_page(u) for u in URL.findall(CODE.sub(_outside_code, text or ""))))


RESEARCH_PROMPT = (
    "You are a careful research assistant. Use web_search to find primary sources (official docs, papers, "
    "first-party pages), then read them with fetch_url. Search results are only leads: never report what a "
    "snippet says. Then write a Markdown report: a short summary first, then the findings, then a Sources list. "
    "Cite a URL you read with fetch_url for every claim, and say where sources disagree.")

PLAN_PROMPT = (
    "You plan projects. Research as needed, then write a Markdown plan with numbered steps. Cite only pages you "
    "read with fetch_url; search results are only leads. You cannot look "
    "inside the user's own folders. Before your final answer, call propose_steps once with follow-up jobs "
    "worth running, each either "
    '{"kind": "research", "topic": "..."} or {"kind": "organize", "folder": "<absolute path>", "goal": "..."}. '
    "Name a folder in an organize step only if the goal text gives its path.")

ORGANIZE_PROMPT = (
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

# Operations per propose_plan call that the prompt asks for. A whole 200-file plan in one reply ran past
# llm.chat's 900 s timeout (O4, 2026-10-16), and a small --fit context leaves little room for a long reply.
PLAN_BATCH = 25

REVIEW_PROMPT = (
    "You review a file-organising plan before a person approves it. The goal was: {goal}\n"
    "First, list every remove operation by number and say whether the goal explicitly asked to remove that "
    "file. A remove the goal didn't ask for is a problem. Removing most or all files is always worth a "
    "warning, even when asked, so say plainly how many files would be removed. Then list any other operation "
    "that looks wrong, risky, or unrelated to the goal, with its number and the reason. Only if there are no "
    "removes and nothing else is wrong, say in one line that the plan looks right.")


def _today():
    return datetime.now().strftime("%A, %B %d, %Y")


def _dated(prompt):
    """Web jobs call llama-server directly, so they miss the proxy's date line and guess the year of dated pages.
    Date only, as in the proxy (ADR 0004)."""
    return f"Current date: {_today()}\n\n{prompt}"


def _steps(job, ctx):
    return lambda text: ctx.store.append_step(job["id"], text)


def _cancelled(job, ctx):
    return lambda: bool(ctx.store.get(job["id"]).get("cancel_requested"))


def _source_check(job, ctx, must_read):
    """A small model writes from search snippets and cites pages it never opened, so a web job's answer may cite
    only pages fetch_url read; enforced here, not just asked for in the prompt. Returns on_fetch, check, finish."""
    read = set()

    def unread(text):
        return [u for u in cited_urls(text) if u not in read]

    def check(text):
        missing = unread(text)
        if not missing and (read or not must_read):
            return None
        ctx.store.append_step(job["id"], "Sent back to read its sources")
        if missing:
            return ("Your answer cites pages you have not read: " + ", ".join(missing[:10]) + ". Search results "
                    "are only leads. Read the pages you rely on with fetch_url, then rewrite the answer citing only "
                    "pages you read, and drop any claim they don't confirm.")
        return ("You have not read any page yet. Search results are only leads: read the most relevant sources "
                "with fetch_url, then write the report from what they say.")

    def finish(text, stop):
        """The text to save and the job note."""
        if stop:
            ctx.store.append_step(job["id"], f"Stopped: {stop}")
        notes = [stop] if stop else []
        missing = unread(text)
        if missing:
            notes.append("cites pages it never read: " + ", ".join(missing[:10]))
            text = f"> Warning: this cites pages the agent never read: {', '.join(missing)}\n\n{text}"
        elif must_read and not read:
            notes.append("read no sources")
        return text, "; ".join(notes)

    return (lambda url: read.add(_same_page(url))), check, finish


def run_research(job, ctx):
    on_fetch, check, finish = _source_check(job, ctx, must_read=True)
    tools = web_tools(ctx.mcp_call, on_fetch) + sandbox_tools(ctx.policy)
    text, stop = run_agent(ctx.chat, _dated(RESEARCH_PROMPT), job["args"]["topic"], tools, on_step=_steps(job, ctx),
                           check=check, cancelled=_cancelled(job, ctx), context_chars=context_chars(ctx.n_ctx()))
    text, note = finish(text, stop)
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.update(job["id"], state="done", note=note)


def run_plan(job, ctx):
    saved = []

    def propose_steps(steps):
        good = [s for s in steps if isinstance(s, dict) and (
            (s.get("kind") == "research" and s.get("topic"))
            or (s.get("kind") == "organize" and s.get("folder") and s.get("goal")))]
        if not good:  # a later empty call wiped valid steps (Ministral, 2026-10-16)
            return f"error: no valid follow-up jobs in that list; the {len(saved)} saved earlier are kept"
        saved[:] = good
        return f"saved {len(good)} follow-up jobs"

    # A plan needn't read anything, but any page it cites must have been read.
    on_fetch, check, finish = _source_check(job, ctx, must_read=False)
    steps_param = {"steps": {"type": "array", "items": {"type": "object"}}}
    tools = web_tools(ctx.mcp_call, on_fetch) + sandbox_tools(ctx.policy) + [
        tool("propose_steps", "Save follow-up jobs for the user to queue.", steps_param, ["steps"], propose_steps)]
    text, stop = run_agent(ctx.chat, _dated(PLAN_PROMPT), job["args"]["goal"], tools, on_step=_steps(job, ctx),
                           check=check, cancelled=_cancelled(job, ctx), context_chars=context_chars(ctx.n_ctx()))
    text, note = finish(text, stop)
    ctx.store.write_text(job["id"], "result.md", text)
    ctx.store.write_json(job["id"], "steps.json", saved)
    ctx.store.update(job["id"], state="done", note=note)


def run_organize(job, ctx):
    folder, goal = job["args"]["folder"], job["args"]["goal"]
    saved, proposed = [], []  # saved: the accepted batches; proposed: the plan kept if the job ends now

    def propose_plan(operations):
        # Each call adds a batch; the whole plan so far is expanded and validated together.
        ops = plans.expand_folder_destinations(saved + operations) if isinstance(operations, list) else operations
        errors = plans.validate_plan(ops, ctx.policy, root=folder)
        if errors:
            proposed[:] = []  # earlier batches must not survive under a summary written after this one
            return (f"error: batch rejected, not added. The plan still has {len(saved)} operations from earlier "
                    "batches; numbers below count those first:\n" + "\n".join(errors))
        added = len(ops) - len(saved)
        saved[:] = ops
        proposed[:] = ops
        return f"added {added} operations; the plan now has {len(ops)}"

    ops_param = {"operations": {"type": "array", "items": {"type": "object"}}}
    # No write_file: the model must not copy listed-folder contents into the sandbox, which web-enabled jobs read.
    tools = [t for t in file_tools(ctx.policy) if t[0] != "write_file"] + [
        tool("propose_plan", f"Add a batch of file operations (at most {PLAN_BATCH}) to the plan.", ops_param,
             ["operations"], propose_plan)]
    text, stop = run_agent(ctx.chat, ORGANIZE_PROMPT.format(folder=folder, batch=PLAN_BATCH), goal, tools, on_step=_steps(job, ctx),
                           cancelled=_cancelled(job, ctx), context_chars=context_chars(ctx.n_ctx()))
    if stop:
        ctx.store.append_step(job["id"], f"Stopped: {stop}")
    ctx.store.write_text(job["id"], "result.md", text)
    if stop == CANCELLED:
        return  # no review call; the worker marks it cancelled
    if not proposed:
        ctx.store.update(job["id"], state="failed", note="no valid plan was proposed" + (f"; {stop}" if stop else ""))
        return
    ctx.store.write_json(job["id"], "plan.json", proposed)
    from . import panel_api  # panel_api imports this module
    try:
        warnings = panel_api.removal_warnings(proposed, folder)
    except OSError:
        warnings = []
    # Saved once: the panel polls every 2 s and must not re-walk a big folder each time.
    ctx.store.write_json(job["id"], "warnings.json", warnings)
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
