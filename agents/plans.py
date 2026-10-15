"""Validate, apply and undo organise plans. Standard library only."""
import os
import re
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


_BAD_CHARS = set('*?"<>|')
_DEVICE_NAMES = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                 | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)})


def _drive(path):
    return os.path.splitdrive(os.path.abspath(path))[0].upper()


def _path_problem(op, policy):
    """Why a plan path is not a plain local Windows path, or crosses drives, else None (no disk access)."""
    for k in OPS[op["op"]]:
        path = op[k]
        if not re.match(r"[A-Za-z]:[\\/]", path):
            return f"not a plain drive path (start with a drive letter, as in C:\\folder\\file): {path}"
        rest = path[2:]
        if any(ord(c) < 32 for c in rest):
            return f"not a plain path (control characters are not allowed): {path}"
        if ":" in rest or _BAD_CHARS & set(rest):
            return f"not a plain path (no : * ? \" < > | after the drive letter): {path}"
        parts = [part for part in re.split(r"[\\/]", rest) if part]
        if any(part.endswith((".", " ")) for part in parts):
            return f"not a plain path (a name ends in a dot or space): {path}"
        if any(part.split(".")[0].upper() in _DEVICE_NAMES for part in parts):
            return f"not a plain path (reserved Windows device name): {path}"
    if op["op"] == "move" and _drive(op["src"]) != _drive(op["dst"]):
        return "moves across drives aren't supported"
    if op["op"] == "remove" and _drive(op["path"]) != _drive(policy.trash_dir):
        return ("removing files on another drive isn't supported; move them to the sandbox's drive "
                "first or delete them yourself")
    return None


def expand_folder_destinations(ops):
    """Rewrite moves whose dst names a folder (made earlier in the plan, or existing) to folder\\<name>.

    Small models write "move a.pdf into PDFs" as dst=...\\PDFs, which validation must refuse
    (it would overlap the mkdir, or land on an existing folder). The saved plan, which the user
    approves, gets the explicit file path instead.
    """
    if not isinstance(ops, list):
        return ops
    made, out = set(), []
    for op in ops:
        if isinstance(op, dict) and op.get("op") == "mkdir" and isinstance(op.get("path"), str):
            made.add(_key(op["path"].rstrip("\\/")))
        if (isinstance(op, dict) and op.get("op") == "move"
                and isinstance(op.get("src"), str) and isinstance(op.get("dst"), str)):
            dst = op["dst"].rstrip("\\/")
            src = op["src"].rstrip("\\/")
            if dst and _key(dst) != _key(src) and (_key(dst) in made or os.path.isdir(dst)):
                op = {**op, "dst": os.path.join(dst, os.path.basename(src))}
        out.append(op)
    return out


def validate_plan(ops, policy, root=None):
    # root: the job's folder; every path must be strictly inside it.
    if not isinstance(ops, list) or not ops:
        return ["the plan has no operations"]
    errors, created = [], set()
    touched = []  # (operation number, is mkdir, real path) of earlier operations
    for i, op in enumerate(ops, 1):
        shape = _shape_error(op)
        if shape:
            errors.append(f"{i}: {shape}")
            continue
        problem = _path_problem(op, policy)
        if problem:
            errors.append(f"{i}: {problem}")
            continue
        fields = OPS[op["op"]]
        bad = [op[k] for k in fields if not os.path.isabs(op[k]) or not policy.can_change(op[k])]
        if bad:
            errors.append(f"{i}: not allowed (use absolute paths inside the readable folders): {', '.join(bad)}")
            continue
        if root is not None:
            outside = [op[k] for k in fields if not is_inside(op[k], root) or _real(op[k]) == _real(root)]
            if outside:
                errors.append(f"{i}: outside the job's folder {root}: {', '.join(outside)}")
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
                    overlap = (f"{i}: overlaps operation {j} ({op[k]}); each operation must touch its own paths. "
                               r"To put a file in a folder, give dst as its full new path, <folder>\<file name>; "
                               "never move or remove the same path twice")
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
    problem = _path_problem(op, policy)
    if problem:
        return problem
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


def apply_plan(ops, job_id, policy, on_step=None):
    """Run the plan; never raises. on_step(log) is called after each completed operation."""
    log = []
    for i, op in enumerate(ops, 1):
        try:
            reason = _stop_reason(op, policy)
            if reason:
                return log, f"step {i} stopped, nothing changed by it: {reason}"
            try:
                if op["op"] == "mkdir":
                    os.makedirs(op["path"])
                    log.append({"op": "mkdir", "path": op["path"]})
                elif op["op"] == "move":
                    os.rename(op["src"], op["dst"])
                    log.append({"op": "move", "from": op["src"], "to": op["dst"]})
                else:
                    to = trash_path(policy, job_id, op["path"], i)
                    os.makedirs(os.path.dirname(to), exist_ok=True)
                    os.rename(op["path"], to)
                    log.append({"op": "remove", "from": op["path"], "to": to})
            except (OSError, ValueError) as e:
                return log, f"step {i} failed, nothing changed by it: {e}"
            if on_step:
                on_step(log)
        except Exception as e:
            return log, f"step {i} failed: {type(e).__name__}: {e}"
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
                os.rename(entry["to"], entry["from"])
        except OSError as e:
            skipped.append(f"could not restore {entry.get('from', entry.get('path'))}: {e}")
    return skipped


def _is_link(path):
    # Any reparse point (junction, symlink, mount point), on every Python version:
    # os.path.isjunction only exists from 3.12 and islink is False for junctions.
    attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    return os.path.islink(path) or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _retry_writable(func, path):
    try:
        func(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)  # only the exact path that failed
        func(path)


def _delete(path):
    """Delete a file, folder tree or link without ever following a link."""
    if _is_link(path):
        try:
            os.rmdir(path)  # a junction or folder symlink goes as a link; its target is left alone
        except OSError:
            os.unlink(path)
        return
    if os.path.isdir(path):
        with os.scandir(path) as entries:
            children = [e.path for e in entries]
        for child in children:
            _delete(child)
        _retry_writable(os.rmdir, path)
    else:
        _retry_writable(os.unlink, path)


def empty_trash(policy):
    if not os.path.isdir(policy.trash_dir):
        return 0
    count = 0
    for name in os.listdir(policy.trash_dir):
        _delete(os.path.join(policy.trash_dir, name))
        count += 1
    return count
