"""Resolve release versions from a tag; never edit source or import Deluge."""

import argparse
import os
import re
import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV_VERSION = runpy.run_path(str(ROOT / "deluge_stream/_version.py"))["__version__"]


def valid_version(version):
    pattern = (
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?"
    )
    return re.fullmatch(pattern, version) is not None


def release_tag(root=ROOT, env=None):
    env = os.environ if env is None else env
    if "STREAM_RELEASE_TAG" in env:
        tag = env["STREAM_RELEASE_TAG"]  # An explicit empty value selects dev.
    elif env.get("GITHUB_REF_TYPE") == "tag":
        tag = env.get("GITHUB_REF_NAME", "")
        if not tag:
            raise ValueError("missing GitHub release tag")
    elif env.get("GITHUB_ACTIONS") == "true":
        tag = ""  # Branch/PR builds are dev, even on a previously tagged commit.
    elif (root / ".git").exists():
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={root.resolve()}",
                "tag",
                "--points-at",
                "HEAD",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        tags = [
            value
            for value in result.stdout.splitlines()
            if value.startswith("v") and valid_version(value[1:])
        ]
        if len(tags) > 1:
            raise ValueError("multiple release tags at HEAD; select STREAM_RELEASE_TAG")
        tag = tags[0] if tags else ""
    else:
        tag = ""  # Source snapshots without Git remain buildable as dev.
    if tag and (not tag.startswith("v") or not valid_version(tag[1:])):
        raise ValueError("release tag must be vX.Y.Z or vX.Y.Z{a,b,rc}N")
    return tag


RELEASE_TAG = release_tag()
VERSION = RELEASE_TAG[1:] if RELEASE_TAG else DEV_VERSION


def version_source():
    source = (ROOT / "deluge_stream/_version.py").read_text()
    return re.sub(
        r'^__version__ = ".*"$',
        f'__version__ = "{VERSION}"',
        source,
        count=1,
        flags=re.MULTILINE,
    ).encode()


def egg_name():
    if not valid_version(VERSION) and VERSION != DEV_VERSION:
        raise ValueError("invalid resolved version")
    return f"Stream-{VERSION}-py3.11.egg"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", action="store_true")
    args = parser.parse_args()
    print(RELEASE_TAG if args.tag else VERSION)
