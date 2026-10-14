"""Where agents may read and write. Standard library only (ADR 0006)."""
import ctypes
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDERS_FILE = os.path.join(APP_DIR, "agent_folders.json")

_DRIVE_REMOVABLE, _DRIVE_FIXED = 2, 3


def default_sandbox():
    return os.path.join(os.path.expanduser("~"), "Documents", "agent-sandbox")


def _real(path):
    # realpath resolves junctions and symlinks, so a link can't escape a root.
    return os.path.normcase(os.path.realpath(path))


def is_inside(path, root):
    p, r = _real(path), _real(root)
    return p == r or p.startswith(r.rstrip("\\") + "\\")


def _has_colon(path):
    # A ':' after the drive prefix names an NTFS alternate data stream ("jobs:evil").
    return ":" in os.path.splitdrive(os.path.abspath(path))[1]


def is_local(path):
    # Check for UNC before realpath: resolving a dead share can hang.
    if os.path.abspath(path).startswith("\\\\"):
        return False
    real = os.path.realpath(path)
    if real.startswith("\\\\"):
        return False
    drive = os.path.splitdrive(real)[0]
    if not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") in (_DRIVE_REMOVABLE, _DRIVE_FIXED)


class Policy:
    def __init__(self, sandbox, folders_file=FOLDERS_FILE):
        self.sandbox = os.path.abspath(sandbox)
        self.jobs_dir = os.path.join(self.sandbox, "jobs")
        self.trash_dir = os.path.join(self.sandbox, "trash")
        self.folders_file = folders_file

    def ensure_dirs(self):
        for d in (self.sandbox, self.jobs_dir, self.trash_dir):
            os.makedirs(d, exist_ok=True)

    def read_folders(self):
        try:
            with open(self.folders_file, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []

    def _save(self, folders):
        tmp = self.folders_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(folders, f, indent=2)
        os.replace(tmp, self.folders_file)

    def add_folder(self, path):
        full = os.path.abspath(path)
        if not is_local(full):
            raise ValueError(f"only local drives are allowed: {full}")
        if not os.path.isdir(full):
            raise ValueError(f"not a folder: {full}")
        folders = self.read_folders()
        if not any(_real(f) == _real(full) for f in folders):
            folders.append(full)
            self._save(folders)
        return full

    def remove_folder(self, path):
        self._save([f for f in self.read_folders() if _real(f) != _real(path)])

    def _system(self, path):
        return is_inside(path, self.jobs_dir) or is_inside(path, self.trash_dir)

    def can_read(self, path):
        if _has_colon(path) or not is_local(path):
            return False
        return ((is_inside(path, self.sandbox) or any(is_inside(path, f) for f in self.read_folders()))
                and not is_inside(path, APP_DIR))

    def can_write(self, path):
        return (not _has_colon(path) and is_inside(path, self.sandbox) and not self._system(path))

    def can_change(self, path):
        # What an approved plan may touch: strictly inside a listed folder (not
        # a listed folder itself, or one that contains another), never the sandbox or the launcher's own folder
        # (config, key, folder list), and never a folder that contains either.
        folders = self.read_folders()
        return (not _has_colon(path)
                and is_local(path)
                and any(is_inside(path, f) and _real(path) != _real(f) for f in folders)
                and not any(is_inside(f, path) for f in folders)
                and not is_inside(path, self.sandbox)
                and not is_inside(path, APP_DIR)
                and not is_inside(APP_DIR, path)
                and not is_inside(self.sandbox, path))
