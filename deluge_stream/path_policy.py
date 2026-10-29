"""Conservative ownership admission, worker only. Never opens/deletes payload."""

import os
import stat
from pathlib import Path, PurePosixPath


def validate_temporary_paths(tid, plan):
    files = {}
    inodes = {}
    selected = []
    for owner, root, names in plan:
        if not Path(root).is_absolute():
            raise ValueError("unsafe_paths")
        for name in names:
            relative = PurePosixPath(name)
            if (
                not name
                or relative.is_absolute()
                or ".." in relative.parts
                or "\\" in name
            ):
                raise ValueError("unsafe_paths")
            path = Path(root) / name
            # Reject symlinks anywhere, even when they point inside the root.
            current = Path(path.anchor)
            for part in path.parts[1:]:
                if part == "..":
                    raise ValueError("unsafe_paths")
                current /= part
                try:
                    status = os.lstat(current)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(status.st_mode):
                    raise ValueError("unsafe_paths")
            canonical = str(path)
            if canonical in files:
                raise ValueError("shared_paths")
            files[canonical] = owner
            try:
                status = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                status = None
            if status is not None:
                if not stat.S_ISREG(status.st_mode):
                    raise ValueError("unsafe_paths")
                inode = (status.st_dev, status.st_ino)
                if inode in inodes or status.st_nlink > 1:
                    raise ValueError("shared_paths")
                inodes[inode] = owner
            if owner == tid:
                selected.append(path)
    if not selected:
        raise ValueError("metadata_pending")
    ordered = sorted(files)
    for left, right in zip(ordered, ordered[1:], strict=False):
        if right.startswith(left + os.sep):
            raise ValueError("shared_paths")
    return True
