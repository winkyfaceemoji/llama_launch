# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.3,<3"]
# ///
"""Agents MCP server (ADR 0006). Run by mcp-proxy with `uv run --script`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from agents import jobs, llm, paths, runner  # noqa: E402

policy = paths.Policy(paths.default_sandbox())
policy.ensure_dirs()
store = jobs.JobStore(policy.jobs_dir)
store.cancel_unfinished()  # anything left from a previous run was killed with it
ctx = runner.Context(policy, store, llm.chat, llm.mcp_call)
worker = jobs.Worker(store, lambda job: runner.run_job(job, ctx))

server = MCPServer("agents", instructions=(
    "Background jobs on the user's PC. Tools return a job ID at once; check progress with job_status "
    "and read output with job_result. organize only proposes a plan; the user approves it."))


def _start(kind, args):
    job = store.create(kind, args)
    worker.submit(job["id"])
    return f"Started {kind} job {job['id']}. Check it with job_status; the user can also watch the launcher's Jobs pane."


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
            parts.append(f"--- {name} ---\n{store.read_text(job_id, name)}")
    return "\n\n".join(parts)


@server.tool()
def apply(job_id: str) -> str:
    """Apply an approved organise plan exactly as proposed."""
    return runner.approve(job_id, ctx)


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
    except ValueError as e:
        return f"Not added: {e}"


if __name__ == "__main__":
    server.run()
