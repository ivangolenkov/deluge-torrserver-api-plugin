"""Compare two clean-directory builds, including installed package resources."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    from tools.release_bundle import ROOT, check_egg, digest, egg_name
except ModuleNotFoundError:
    from release_bundle import ROOT, check_egg, digest, egg_name


def main():
    with tempfile.TemporaryDirectory(prefix="stream-packaging-") as directory:
        eggs = []
        for name in ("first", "second"):
            source = Path(directory) / name
            source.mkdir()
            shutil.copy2(ROOT / "setup.py", source)
            shutil.copytree(
                ROOT / "deluge_stream",
                source / "deluge_stream",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            subprocess.run(
                [sys.executable, "setup.py", "bdist_egg"],
                cwd=source,
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=60,
            )
            artifact = source / "dist" / egg_name()
            check_egg(artifact)
            eggs.append(artifact)
            if name == "first":
                time.sleep(2.1)  # do not accidentally hide ZIP timestamp differences
        with zipfile.ZipFile(eggs[0]) as left, zipfile.ZipFile(eggs[1]) as right:
            if set(left.namelist()) != set(right.namelist()) or any(
                left.read(name) != right.read(name) for name in left.namelist()
            ):
                raise SystemExit(
                    "PACKAGING FAILED: member contents differ between builds"
                )
        print(
            json.dumps(
                {
                    "build_sha256": [digest(egg) for egg in eggs],
                    "member_contents_identical": True,
                    "byte_identical": digest(eggs[0]) == digest(eggs[1]),
                    "scope": "two clean directories, current source snapshot; "
                    "ZIP timestamps are not normalized",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
