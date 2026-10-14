"""Validate, apply and undo organise plans. Standard library only."""
import os
import re
import shutil
import stat

from .paths import _real, is_inside

OPS = {"mkdir": ("path",), "move": ("src", "dst"), "remove": ("path",)}
JOB_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _within(path, root):
    # Both arguments are already _real() keys.
    return path == root or path.startswith(root.rstrip("\\") + "\\")


def _shape_error(op):
    if not isinstance(op, dict) or op.get("op") not in OPS:
        return f"unknown operation {op!r}; use mkdir, move or remove"
    fields = OPS[op["op"]]
    if any(not isinstance(op.get(k), str) or not op.get(k) for k in fields):
        return f"{op['op']} needs {', '.join(fields)}"
    return None


def validate_plan(ops, policy):
    if not isinstance(ops, list) or not ops:
        return ["the plan has no operations"]
    errors, created = [], set()
    touched = []  # (operation number, is mkdir, real path) of earlier operations
    for i, op in enumerate(ops, 1):
        shape = _shape_error(op)
        if shape:
            errors.append(f"{i}: {shape}")
            continue
        fields = OPS[op["op"]]
        bad = [op[k] for k in fields if not os.path.isabs(op[k]) or not policy.can_change(op[k])]
        if bad:
            errors.append(f"{i}: not allowed (use absolute paths inside the readable folders): {', '.join(bad)}")
            continue
        # No operation may depend on another, except through mkdir parents.
        overlap = None
        mine = [(k, _real(op[k])) for k in fields]
        for j, was_mkdir, q in touched:
            for k, p in mine:
                if not (_within(p, q) or _within(q, p)):
                    continue
                parent_ok = was_mkdir and p != q and _within(p, q) and (op["op"] == "mkdir" or k == "dst")
                if not parent_ok:
                    overlap = f"{i}: overlaps operation {j} ({op[k]}); each operation must touch its own paths"
                    break
            if overlap:
                break
        touched.extend((i, op["op"] == "mkdir", p) for _, p in mine)
        if overlap:
            errors.append(overlap)
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


def trash_path(policy, job_id, path, op_number=None):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError(f"bad job id: {job_id!r}")
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise ValueError(f"only local drive paths can be trashed: {path}")
    parts = [policy.trash_dir, job_id]
    if op_number is not None:
        parts.append(str(op_number))
    return os.path.join(*parts, drive.rstrip(":"), rest.lstrip("\\/"))


def _stop_reason(op, policy):
    """Why this operation must not run right now (it is re-checked just before running), else None."""
    shape = _shape_error(op)
    if shape:
        return shape
    for k in OPS[op["op"]]:
        if not os.path.isabs(op[k]) or not policy.can_change(op[k]):
            return f"not allowed: {op[k]}"
    if op["op"] == "mkdir":
        if os.path.lexists(op["path"]):
            return f"already exists: {op['path']}"
    elif op["op"] == "move":
        if not os.path.lexists(op["src"]):
            return f"source missing: {op['src']}"
        if os.path.lexists(op["dst"]):
            return f"destination already exists: {op['dst']}"
        if not os.path.isdir(os.path.dirname(op["dst"])):
            return f"destination folder missing: {os.path.dirname(op['dst'])}"
    elif not os.path.lexists(op["path"]):
        return f"nothing to remove at {op['path']}"
    return None


def apply_plan(ops, job_id, policy):
    log = []
    for i, op in enumerate(ops, 1):
        reason = _stop_reason(op, policy)
        if reason:
            return log, f"step {i} stopped, nothing changed by it: {reason}"
        src = to = None
        try:
            if op["op"] == "mkdir":
                os.makedirs(op["path"])
                log.append({"op": "mkdir", "path": op["path"]})
            elif op["op"] == "move":
                src, to = op["src"], op["dst"]
                shutil.move(src, to)
                log.append({"op": "move", "from": src, "to": to})
            else:
                src = op["path"]
                to = trash_path(policy, job_id, src, i)
                os.makedirs(os.path.dirname(to), exist_ok=True)
                shutil.move(src, to)
                log.append({"op": "remove", "from": src, "to": to})
        except (OSError, ValueError) as e:
            msg = f"step {i} failed: {e}"
            if src and to and os.path.lexists(src) and os.path.lexists(to):
                msg += f". A copy was left at {to}; the original is still at {src}. Nothing was deleted"
            return log, msg
    return log, None


def restore(log):
    skipped = []
    for entry in reversed(log):
        try:
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
        except OSError as e:
            skipped.append(f"could not restore {entry.get('from', entry.get('path'))}: {e}")
    return skipped


def _make_writable(path):
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                try:
                    os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _delete(path):
    remove = shutil.rmtree if os.path.isdir(path) and not os.path.islink(path) else os.unlink
    try:
        remove(path)
    except PermissionError:
        _make_writable(path)
        remove(path)


def empty_trash(policy):
    if not os.path.isdir(policy.trash_dir):
        return 0
    count = 0
    for name in os.listdir(policy.trash_dir):
        _delete(os.path.join(policy.trash_dir, name))
        count += 1
    return count
