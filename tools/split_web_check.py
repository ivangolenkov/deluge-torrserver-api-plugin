"""Docker-only extended UI test, with an internal network and no shared payload."""

import argparse
import subprocess
import uuid
from pathlib import Path

try:
    from tools.version import egg_name
except ModuleNotFoundError:
    from version import egg_name


def run(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, timeout=180, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--python", choices=["3.11", "3.14"], default="3.11")
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    egg = args.dist.resolve() / egg_name(args.python)
    if not egg.is_file():
        raise SystemExit(
            f"Run make build using Python {args.python} before test-web-split: {egg}"
        )
    name = "stream-split-" + uuid.uuid4().hex
    core, web = name + "-core", name + "-web"
    shared = [
        "--platform",
        args.platform,
        "--network",
        name,
        "-v",
        f"{egg}:/Stream.egg:ro",
        "-v",
        f"{root / 'tests/runtime'}:/checks:ro",
    ]
    run("network", "create", "--internal", name, stdout=subprocess.DEVNULL)
    try:
        run(
            "run",
            "-d",
            "--name",
            core,
            "--network-alias",
            "core",
            *shared,
            args.image,
            "python",
            "/checks/split_node.py",
            "core",
            stdout=subprocess.DEVNULL,
        )
        run(
            "run",
            "--rm",
            "--name",
            web,
            *shared,
            args.image,
            "python",
            "/checks/split_node.py",
            "web",
        )
    except BaseException:
        subprocess.run(["docker", "logs", core], check=False, timeout=15)
        raise
    finally:
        for node in (web, core):
            subprocess.run(
                ["docker", "stop", "--time", "15", node],
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["docker", "rm", node],
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        run("network", "rm", name, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
