"""Logic behind the jobs panel's routes on the agents server. Standard library only."""
import os

from . import plans, runner

MOVING = ("applying", "restoring")


def _title(job):
    args = job.get("args") or {}
    if job.get("kind") == "organize":
        folder = str(args.get("folder", "")).rstrip("\\/")
        return f"{args.get('goal', '')} — {os.path.basename(folder) or folder}"
    return str(args.get("topic") or args.get("goal") or job.get("kind", ""))


def jobs_payload(store, limit=50):
    out = []
    for job in store.list()[:limit]:
        jid, state = job["id"], job.get("state")
        count, steps = store.read_steps(jid, last=5)
        item = {k: job.get(k) for k in ("id", "kind", "state", "note", "created", "updated")}
        item.update(title=_title(job), step_count=count, steps=steps, queue_count=0,
                    can_restore=state in ("applied", "failed") and store.has(jid, "applied.json"))
        if job.get("kind") == "plan" and state == "done" and not job.get("steps_queued") and store.has(jid, "steps.json"):
            try:
                item["queue_count"] = len(store.read_json(jid, "steps.json"))
            except (OSError, ValueError):
                pass
        if state == "awaiting_approval":
            try:
                item["plan"] = store.read_json(jid, "plan.json")
            except (OSError, ValueError):
                item["plan"] = None
            try:
                item["review"] = store.read_text(jid, "review.md") if store.has(jid, "review.md") else ""
            except OSError:
                item["review"] = ""
        out.append(item)
    return out


def trash_refusal(jobs):
    moving = [f"{j['id']} ({j['state']})" for j in jobs if j.get("state") in MOVING]
    if moving:
        return "These jobs are still moving files: " + ", ".join(moving) + ". Wait for them to finish."
    return None


def empty_trash(ctx):
    if not runner.FILE_MOVES.acquire(blocking=False):
        return {"ok": False, "message": "Files are moving right now. Try again when that finishes."}
    try:
        refusal = trash_refusal(ctx.store.list())
        if refusal:
            return {"ok": False, "message": refusal}
        n = plans.empty_trash(ctx.policy)
        return {"ok": True, "message": f"Trash emptied ({n} items)."}
    except OSError as e:
        return {"ok": False, "message": f"Couldn't empty the trash: {e}"}
    finally:
        runner.FILE_MOVES.release()
