"""Logic behind the jobs panel's routes on the agents server. Standard library only."""
import os

from . import plans, runner

MOVING = ("applying", "restoring")
# Never cut from the list: a plan waiting for approval is only approvable here, and live jobs are being watched.
ALWAYS_LISTED = ("awaiting_approval", "queued", "running", "applying", "restoring")


def _title(job):
    args = job.get("args") or {}
    if job.get("kind") == "organize":
        folder = str(args.get("folder", "")).rstrip("\\/")
        return f"{args.get('goal', '')} — {os.path.basename(folder) or folder}"
    return str(args.get("topic") or args.get("goal") or job.get("kind", ""))


def _count_files(path):
    # Files at or under path, never following links or junctions (a removed link is one item).
    if os.path.isfile(path) or plans._is_link(path):
        return 1
    n = 0
    for d, dirs, files in os.walk(path):
        dirs[:] = [x for x in dirs if not plans._is_link(os.path.join(d, x))]
        n += len(files)
    return n


def removal_warnings(plan, folder):
    """Fixed-rule warnings for an organise plan; the model's review is not enough on its own.

    Returns [{"text", "level"}] with level "warn" or "danger" (more than half the folder's files).
    """
    removes = [op["path"] for op in plan or [] if isinstance(op, dict) and op.get("op") == "remove" and isinstance(op.get("path"), str)]
    if not removes:
        return []
    removed = sum(max(1, _count_files(p)) for p in removes)  # an empty folder is still one item
    noun = "item" if removed == 1 else "items"
    warnings = [{"level": "warn",
                 "text": f"Removes {removed} {noun}. They go to the agent trash and can be restored until you empty it."}]
    total = _count_files(folder) if folder and os.path.isdir(folder) else 0
    if total and removed * 2 > total:
        warnings.append({"level": "danger",
                         "text": f"This removes more than half of the files in the folder ({removed} of {total})."})
    return warnings


def jobs_payload(store, limit=50):
    out = []
    newest_first = store.list()
    pinned = [j["id"] for j in newest_first if j.get("state") in ALWAYS_LISTED]
    others = [j["id"] for j in newest_first if j.get("state") not in ALWAYS_LISTED]
    keep = set(pinned) | set(others[:max(0, limit - len(pinned))])
    for job in (j for j in newest_first if j["id"] in keep):
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
            try:
                if store.has(jid, "warnings.json"):  # saved at proposal
                    item["warnings"] = store.read_json(jid, "warnings.json")
                else:  # jobs proposed before warnings were saved
                    item["warnings"] = removal_warnings(item["plan"], (job.get("args") or {}).get("folder"))
            except (OSError, ValueError):
                item["warnings"] = []
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
