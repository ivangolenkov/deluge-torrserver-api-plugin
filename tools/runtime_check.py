"""Run the runtime probe isolated from checkout imports, against the built egg."""

import argparse
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path

from version import egg_name

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["smoke", "integration"], required=True)
parser.add_argument("--dist", required=True)
parser.add_argument("--profile-root", default="")
args = parser.parse_args()
try:
    from deluge._libtorrent import lt as libtorrent

    versions = {
        name: importlib.metadata.version(name)
        for name in ("Deluge", "Twisted", "setuptools", "wheel")
    }
except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
    raise SystemExit(
        "Missing required Deluge/libtorrent runtime. Use tools/runtime.Dockerfile "
        "or prepare Linux per docs/compatibility.md; no successful skip. " + str(exc)
    ) from exc
if platform.system() != "Linux" or versions["Deluge"] != "2.2.0":
    raise SystemExit("Runtime probe requires Linux + Deluge 2.2.0")
versions.update(
    libtorrent=libtorrent.__version__,
    python=platform.python_version(),
    arch=platform.machine(),
    sqlite=sqlite3.sqlite_version,
)
print(json.dumps(versions, sort_keys=True), flush=True)
egg = Path(args.dist).resolve() / egg_name()
if not egg.is_file():
    raise SystemExit(f"Build the current Python artifact first: {egg}")
probe = "daemon_smoke.py" if args.mode == "smoke" else "probe.py"
subprocess.run(
    [
        sys.executable,
        "-I",
        str((Path("tests/runtime") / probe).resolve()),
        "--mode",
        args.mode,
        "--egg",
        str(egg),
        "--profile-root",
        args.profile_root,
    ],
    check=True,
    timeout=120,
)
if args.mode == "integration":
    for script in (
        "research.py",
        "guards.py",
        "cleanup.py",
        "policy_smoke.py",
        "policy_crash_probe.py",
        "retention.py",
        "retention_crash.py",
        "qos_resume_probe.py",
        "streaming.py",
        "streaming_lifecycle.py",
    ):
        subprocess.run(
            [
                sys.executable,
                "-I",
                str((Path("tests/runtime") / script).resolve()),
                "--egg",
                str(egg),
                "--profile-root",
                args.profile_root,
            ],
            check=True,
            timeout=300,
        )
else:
    subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path("tests/runtime/web_contract.py").resolve()),
            "--egg",
            str(egg),
        ],
        check=True,
        timeout=30,
    )
    subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path("tests/runtime/web_smoke.py").resolve()),
            "--egg",
            str(egg),
            "--profile-root",
            args.profile_root,
        ],
        check=True,
        timeout=180,
    )
