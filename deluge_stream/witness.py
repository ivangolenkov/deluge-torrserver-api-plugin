"""Research candidate: a fail-closed witness of Deluge's metadata-file lifetime.

Only call these filesystem functions in a worker, with trusted profile paths.
They never link, open or remove payload files. This is NOT product identity or
backup reconciliation: unsupported filesystems/changed metadata are rejected.
"""

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetadataWitness:
    token: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def fingerprint(status):
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def open_metadata(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("metadata is not a regular file")
    return descriptor


def create_witness(metadata, directory):
    """Pin the inode so removal/re-add cannot immediately reuse its number."""
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("witness directory is a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = str(uuid.uuid4())
    reference = directory / token
    with os.fdopen(open_metadata(metadata), "rb") as source:
        original = os.fstat(source.fileno())
        linked = False
        try:
            os.link(metadata, reference, follow_symlinks=False)
            linked = True
            with os.fdopen(open_metadata(reference), "rb") as pinned:
                status = os.fstat(pinned.fileno())
                if (status.st_dev, status.st_ino) != (original.st_dev, original.st_ino):
                    raise ValueError("metadata changed while creating witness")
                os.fsync(pinned.fileno())
            directory_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            witness = MetadataWitness(token, *fingerprint(status))
            if not witness_matches(witness, metadata, directory):
                raise ValueError("metadata changed while committing witness")
            return witness
        except BaseException:
            if linked:
                reference.unlink()  # Only the UUID link just created by this call.
            raise


def witness_matches(witness, metadata, directory):
    """False means ambiguous/stale: do not repair priorities or delete a marker."""
    try:
        if str(uuid.UUID(witness.token)) != witness.token:
            return False
        if Path(directory).is_symlink():
            return False
        with os.fdopen(open_metadata(Path(directory) / witness.token), "rb") as pinned:
            with os.fdopen(open_metadata(metadata), "rb") as source:
                expected = (
                    witness.device,
                    witness.inode,
                    witness.size,
                    witness.mtime_ns,
                    witness.ctime_ns,
                )
                return (
                    fingerprint(os.fstat(pinned.fileno())) == expected
                    and fingerprint(os.fstat(source.fileno())) == expected
                )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
