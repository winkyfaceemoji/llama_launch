"""Validate, apply and undo organise plans. Standard library only."""
import os
import shutil

from .paths import is_inside

OPS = {"mkdir": ("path",), "move": ("src", "dst"), "remove": ("path",)}


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def validate_plan(ops, policy):
    if not isinstance(ops, list) or not ops:
        return ["the plan has no operations"]
    errors, created = [], set()
    for i, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            errors.append(f"{i}: unknown operation {op!r}; use mkdir, move or remove")
            continue
        fields = OPS[op["op"]]
        if any(not isinstance(op.get(k), str) or not op.get(k) for k in fields):
            errors.append(f"{i}: {op['op']} needs {', '.join(fields)}")
            continue
        bad = [op[k] for k in fields if not os.path.isabs(op[k]) or not policy.can_change(op[k])]
        if bad:
            errors.append(f"{i}: not allowed (use absolute paths inside the readable folders): {', '.join(bad)}")
            continue
        if op["op"] == "mkdir":
            if os.path.exists(op["path"]):
                errors.append(f"{i}: already exists: {op['path']}")
            created.add(_key(op["path"]))
        elif op["op"] == "move":
            src, dst = op["src"], op["dst"]
            parent = os.path.dirname(dst)
            if not os.path.exists(src):
                errors.append(f"{i}: source missing: {src}")
            elif os.path.exists(dst):
                errors.append(f"{i}: destination already exists: {dst}")
            elif not (os.path.isdir(parent) or _key(parent) in created):
                errors.append(f"{i}: destination folder missing (add a mkdir first): {parent}")
            elif is_inside(dst, src):
                errors.append(f"{i}: can't move a folder into itself: {src}")
        elif not os.path.exists(op["path"]):
            errors.append(f"{i}: nothing to remove at {op['path']}")
    return errors


def trash_path(policy, job_id, path):
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return os.path.join(policy.trash_dir, job_id, drive.rstrip(":"), rest.lstrip("\\/"))


def apply_plan(ops, job_id, policy):
    log = []
    for i, op in enumerate(ops, 1):
        try:
            if op["op"] == "mkdir":
                os.makedirs(op["path"])
                log.append({"op": "mkdir", "path": op["path"]})
            elif op["op"] == "move":
                shutil.move(op["src"], op["dst"])
                log.append({"op": "move", "from": op["src"], "to": op["dst"]})
            else:
                to = trash_path(policy, job_id, op["path"])
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.move(op["path"], to)
                log.append({"op": "remove", "from": op["path"], "to": to})
        except OSError as e:
            return log, f"step {i} failed: {e}"
    return log, None


def restore(log):
    skipped = []
    for entry in reversed(log):
        if entry["op"] == "mkdir":
            try:
                os.rmdir(entry["path"])
            except OSError:
                skipped.append(f"kept folder (not empty or gone): {entry['path']}")
        elif os.path.exists(entry["from"]):
            skipped.append(f"something new is already at {entry['from']}")
        elif not os.path.exists(entry["to"]):
            skipped.append(f"no longer there (trash emptied?): {entry['to']}")
        else:
            os.makedirs(os.path.dirname(entry["from"]), exist_ok=True)
            shutil.move(entry["to"], entry["from"])
    return skipped


def empty_trash(policy):
    count = 0
    for name in os.listdir(policy.trash_dir):
        shutil.rmtree(os.path.join(policy.trash_dir, name))
        count += 1
    return count
