"""Test-only offline volume snapshot; run only against disposable Compose volumes."""

import argparse
import inspect
import tarfile
from pathlib import Path, PurePosixPath


def validate_members(members, roots):
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or member.name != path.as_posix()
            or path.parts[0] not in roots
            or member.name in seen
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError(f"unsafe snapshot member: {member.name}")
        seen.add(member.name)


def snapshot(mode, archive, roots):
    if mode == "backup":
        if archive.exists():
            raise ValueError("snapshot already exists; never overwrite")
        with tarfile.open(archive, "x") as stream:
            for name in sorted(roots):
                source = Path("/") / name
                for path in (source, *source.rglob("*")):
                    if path.is_symlink() or not (path.is_dir() or path.is_file()):
                        raise ValueError(f"unsafe snapshot source: {path}")
                stream.add(source, arcname=name)
        archive.chmod(0o600)
        return
    if any(any((Path("/") / name).iterdir()) for name in roots):
        raise ValueError("restore requires empty disposable target volumes")
    with tarfile.open(archive, "r") as stream:
        members = stream.getmembers()
        validate_members(members, roots)
        # Only this harness's own offline archive, validated above. Numeric UID/GID
        # must survive restore; the 'data' filter would discard ownership.
        options = (
            {"filter": "fully_trusted"}
            if "filter" in inspect.signature(stream.extractall).parameters
            else {}
        )
        stream.extractall("/", members=members, numeric_owner=True, **options)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["backup", "restore"])
    parser.add_argument("--role", required=True, choices=["core", "web"])
    args = parser.parse_args()
    roots = {"config", "downloads"} if args.role == "core" else {"config"}
    snapshot(args.mode, Path(f"/backup/{args.role}.tar"), roots)


if __name__ == "__main__":
    main()
