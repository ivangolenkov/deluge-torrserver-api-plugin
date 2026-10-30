"""Build a specified local historical source in temp; test current installed upgrade."""

import argparse
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

try:
    from tools.volume_snapshot import validate_members
except ModuleNotFoundError:
    from volume_snapshot import validate_members


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-ref", required=True)
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        [
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            args.previous_ref + "^{commit}",
        ],
        cwd=root,
        text=True,
    ).strip()
    version_helper = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit}:tools/version.py"],
            cwd=root,
            capture_output=True,
        ).returncode
        == 0
    )
    raw = subprocess.check_output(
        [
            "git",
            "archive",
            "--format=tar",
            commit,
            "setup.py",
            "deluge_stream",
            *(["tools/version.py"] if version_helper else []),
        ],
        cwd=root,
    )
    with tempfile.TemporaryDirectory(prefix="stream-historical-") as directory:
        source = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            members = archive.getmembers()
            validate_members(members, {"setup.py", "deluge_stream", "tools"})
            archive.extractall(source, members=members)
        subprocess.run(
            [sys.executable, "setup.py", "bdist_egg"],
            cwd=source,
            env=dict(os.environ, STREAM_RELEASE_TAG=""),
            check=True,
            timeout=90,
        )
        (previous,) = (source / "dist").glob("*.egg")
        print(
            f"HISTORICAL BASELINE: local commit {commit}; not a published release",
            flush=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-I",
                str(root / "tests/runtime/delivery_lifecycle.py"),
                "--egg",
                str(Path(args.egg).resolve()),
                "--previous-egg",
                str(previous),
                "--profile-root",
                args.profile_root,
            ],
            cwd=root,
            check=True,
            timeout=180,
        )


if __name__ == "__main__":
    main()
