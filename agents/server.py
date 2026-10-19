# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3"]
# ///
"""Agents MCP server (ADR 0007). The launcher starts it with `uv run --script agents/server.py`.

Serves MCP at http://127.0.0.1:8002/mcp with DNS-rebinding protection, plus key-checked
routes for the jobs panel in the Web UI (job list, approvals, Empty trash). Never run a second copy while the launcher is
running: starting up cancels unfinished jobs, including ones the live server is applying.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anyio.to_thread  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from agents import jobs, llm, panel_api, paths, runner  # noqa: E402

PORT = llm.AGENTS_PORT  # --port replaces it; the approval routes check Host against it

policy = paths.Policy(paths.default_sandbox())
try:
    policy.ensure_dirs()
    store = jobs.JobStore(policy.jobs_dir)
    store.cancel_unfinished()  # anything left from a previous run was killed with it
except Exception as e:
    sys.exit(f"agents server can't start: {type(e).__name__}: {e}")
ctx = runner.Context(policy, store, llm.chat, llm.mcp_call, llm.n_ctx)
worker = jobs.Worker(store, lambda job: runner.run_job(job, ctx))

MAX_FILE_CHARS = 8000  # per file in job_result; keeps big plans out of the chat model's context

server = MCPServer("agents", instructions=(
    "Background jobs on the user's PC. Tools return a job ID at once; check progress with job_status "
    "and read output with job_result. organize only proposes a plan; the user reviews and approves it "
    "in the jobs panel in the Web UI, never in chat."))


def _start(kind, args):
    job = store.create(kind, args)
    worker.submit(job["id"])
    return f"Started {kind} job {job['id']}. Check it with job_status; the user can also watch the jobs panel in the Web UI."


@server.tool()
def research(topic: str) -> str:
    """Research a topic on the web and write a cited Markdown report."""
    return _start("research", {"topic": topic})


@server.tool()
def plan(goal: str) -> str:
    """Write a step-by-step plan for a goal, with follow-up jobs the user can queue."""
    return _start("plan", {"goal": goal})


@server.tool()
def organize(folder: str, goal: str) -> str:
    """Propose a plan to organise a local folder. Nothing changes until the user approves it."""
    if not policy.can_read(folder):
        return f"No read access to {folder}. Call request_folder_access first."
    if not os.path.isdir(folder):
        return f"{folder} is not a folder."
    return _start("organize", {"folder": os.path.abspath(folder), "goal": goal})


@server.tool()
def job_status(job_id: str = "") -> str:
    """Status of one job, or the 10 most recent jobs if no ID is given."""
    try:
        found = [store.get(job_id)] if job_id else store.list()[:10]
    except KeyError:
        return f"No job {job_id}."
    return "\n".join(runner.describe(j) for j in found) or "No jobs yet."


@server.tool()
def job_result(job_id: str) -> str:
    """The job's report, plan document, or proposed file plan with its review."""
    try:
        job = store.get(job_id)
    except KeyError:
        return f"No job {job_id}."
    parts = [runner.describe(job)]
    for name in ("result.md", "plan.json", "review.md", "steps.json"):
        if store.has(job_id, name):
            text = store.read_text(job_id, name)
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + f"\n[truncated — full file: {store.path(job_id, name)}]"
            parts.append(f"--- {name} ---\n{text}")
    return "\n\n".join(parts)


@server.tool()
def reject(job_id: str) -> str:
    """Reject an organise plan; nothing changes."""
    return runner.reject(job_id, ctx)


@server.tool()
def restore(job_id: str) -> str:
    """Undo an applied organise plan, putting moved and removed files back."""
    return runner.undo(job_id, ctx)


@server.tool()
def queue_steps(job_id: str) -> str:
    """Queue the follow-up jobs a plan job proposed."""
    return runner.queue_steps(job_id, ctx, worker.submit)


@server.tool()
def request_folder_access(path: str) -> str:
    """Ask to let agents read a local folder. The user's approval of this call grants it."""
    try:
        return f"Agents can now read {policy.add_folder(path)}."
    except (ValueError, OSError) as e:
        return f"Not added: {e}"


# Approval routes for the jobs panel, which shows the full plan. Applying is not an MCP tool:
# a chat prompt shows only the job ID, so approving there would approve an unseen plan.
ACTIONS = {
    "apply": lambda job_id: runner.approve(job_id, ctx),
    "reject": lambda job_id: runner.reject(job_id, ctx),
    "restore": lambda job_id: runner.undo(job_id, ctx),
    "queue": lambda job_id: runner.queue_steps(job_id, ctx, worker.submit),
    # Launcher only (the model test's Stop): not an MCP tool, and the proxy doesn't forward it from the panel.
    "cancel": lambda job_id: store.request_cancel(job_id),
}


def _key():
    try:
        return llm.read_key(llm.APPROVAL_KEY_FILE)
    except OSError:
        return ""  # no key file: check_request refuses everything


@server.custom_route("/jobs/{job_id}/{action}", methods=["POST"])
async def job_action(request):
    if not llm.check_request(request.headers, PORT, _key()):
        return JSONResponse({"ok": False, "message": "Forbidden."}, status_code=403)
    fn = ACTIONS.get(request.path_params["action"])
    if fn is None:
        return JSONResponse({"ok": False, "message": "Unknown action."}, status_code=404)
    message = await anyio.to_thread.run_sync(fn, request.path_params["job_id"])
    return JSONResponse({"ok": True, "message": message})


@server.custom_route("/jobs", methods=["GET"])
async def jobs_list(request):
    if not llm.check_request(request.headers, PORT, _key()):
        return JSONResponse({"ok": False, "message": "Forbidden."}, status_code=403)
    data = await anyio.to_thread.run_sync(panel_api.jobs_payload, store)
    return JSONResponse({"ok": True, "jobs": data})


@server.custom_route("/trash/empty", methods=["POST"])
async def trash_empty(request):
    if not llm.check_request(request.headers, PORT, _key()):
        return JSONResponse({"ok": False, "message": "Forbidden."}, status_code=403)
    return JSONResponse(await anyio.to_thread.run_sync(panel_api.empty_trash, ctx))


def main():
    global PORT
    parser = argparse.ArgumentParser(description="Agents MCP server (the launcher starts it).")
    parser.add_argument("--port", type=int, default=llm.AGENTS_PORT)
    PORT = parser.parse_args().port
    # Explicit, so a change in mcp's defaults can't silently drop the rebinding check.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"])
    # Stateless JSON replies, as mcp-proxy --stateless served the Web UI before.
    server.run("streamable-http", host="127.0.0.1", port=PORT, streamable_http_path="/mcp",
               json_response=True, stateless_http=True, transport_security=security)


if __name__ == "__main__":
    main()
