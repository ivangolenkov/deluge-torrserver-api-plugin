"""Make's Docker CI runner. No publishing, user-profile installation or secrets."""

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from tools.version import RELEASE_TAG
except ModuleNotFoundError:
    from version import RELEASE_TAG


def commands(root, platform, image, container=None, python="3.11"):
    return (
        (
            "image",
            [
                "docker",
                "build",
                "--platform",
                platform,
                "-f",
                "tools/runtime314.Dockerfile"
                if python == "3.14"
                else "tools/runtime.Dockerfile",
                "-t",
                image,
                ".",
            ],
        ),
        (
            "check",
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                platform,
                "--network",
                "none",
                "-e",
                f"STREAM_RELEASE_TAG={RELEASE_TAG}",
                *(["--name", container] if container else []),
                "-v",
                f"{root}:/work",
                image,
                "make",
                "check",
                "VENV=/opt/venv",
            ],
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform", choices=["linux/amd64", "linux/arm64"], default="linux/amd64"
    )
    parser.add_argument("--image", default="deluge-stream-check:local")
    parser.add_argument("--python", choices=["3.11", "3.14"], default="3.11")
    args = parser.parse_args()
    if not args.image or args.image.startswith("-"):
        parser.error("invalid Docker image tag")
    root = Path(__file__).resolve().parents[1]
    logs = root / "build" / "ci"
    logs.mkdir(parents=True, exist_ok=True)
    container = f"deluge-stream-ci-{uuid.uuid4().hex}"
    for name, command in commands(
        root, args.platform, args.image, container, args.python
    ):
        path = logs / f"{name}.log"
        print(f"CI {name}: output in {path.relative_to(root)}", flush=True)
        try:
            with path.open("w") as log:
                result = subprocess.run(
                    command, cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=900
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(path.read_text(), end="", flush=True)
            print(f"CI {name} failed: {exc}", file=sys.stderr)
            if name == "check":
                cleanup(container, path)
            return 1
        print(path.read_text(), end="", flush=True)
        if result.returncode:
            if name == "check":
                cleanup(container, path)
            return result.returncode if result.returncode > 0 else 1
    return 0


def cleanup(container, path):
    """Remove only this invocation's uniquely named container, never broad prune."""
    try:
        with path.open("a") as log:
            subprocess.run(
                ["docker", "rm", "--force", container],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        print(f"Could not remove own CI container: {container}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
