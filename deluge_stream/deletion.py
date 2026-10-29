"""Worker-only preflight/recovery checks. Never deletes any filesystem path.

Not a native filesystem barrier: deletion uses the agreed Deluge guarantees.
Pending external rename/move may change paths after the snapshot.
"""

import os
import stat
from pathlib import Path

from .path_policy import validate_temporary_paths


def build_deletion_plan(tid, catalogue):
    """Catalogue includes *all* native paths (pads and partfile too).

    libtorrent 2.0.8 delete_files attempts every file_storage path, including
    pads, then its reserved .<best-infohash>.parts file and empty subdirectories.
    A payload-only plan is NOT sufficient to authorize native remove_data=True.
    """
    validate_temporary_paths(tid, catalogue)
    selected = next((entry for entry in catalogue if entry[0] == tid), None)
    if selected is None:
        raise ValueError("torrent_not_found")
    _, root, names = selected
    paths = []
    parents = {}
    for name in names:
        path = Path(root) / name
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            status = None
        identity = [status.st_dev, status.st_ino] if status else None
        paths.append({"path": str(path), "identity": identity})
        for parent in path.parents:
            if str(parent) in parents:
                break
            try:
                value = os.lstat(parent)
            except FileNotFoundError:
                parents[str(parent)] = None
                continue
            if not stat.S_ISDIR(value.st_mode):
                raise ValueError("unsafe_paths")
            parents[str(parent)] = [value.st_dev, value.st_ino]
    return {"torrent_id": tid, "root": root, "files": paths, "parents": parents}


def verify_plan_unchanged(plan, catalogue):
    current = build_deletion_plan(plan["torrent_id"], catalogue)
    if current != plan:
        raise ValueError("paths_changed")
    return True


def verify_absent(plan):
    """Verification only, including after restart with no native handle.

    Never follows a saved path to unlink an orphan. Remaining/replaced files or
    symlinked parents require manual resolution. Unrelated directory contents
    are deliberately not part of the expected removal result.
    """
    for item in plan["files"]:
        path = Path(item["path"])
        for parent in path.parents:
            try:
                status = os.lstat(parent)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(status.st_mode):
                return False
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        return False
    return True
