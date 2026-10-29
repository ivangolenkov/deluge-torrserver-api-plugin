"""Publish an already verified bundle via gh; never replace existing assets."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

try:
    from tools.release_bundle import VERSION, digest, verify
except ModuleNotFoundError:
    from release_bundle import VERSION, digest, verify


def asset_plan(local, existing, draft):
    """Reject foreign/different assets before any remote write."""
    if existing.keys() - local.keys():
        raise ValueError("unexpected remote assets; manual review required")
    for name, checksum in existing.items():
        if checksum != local[name]:
            raise ValueError(f"different published content: {name}; never overwrite")
    missing = local.keys() - existing.keys()
    if missing and not draft:
        raise ValueError("incomplete non-draft release; manual review required")
    return sorted(missing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise SystemExit("invalid repository")
    manifest = verify(args.dist, strict=True, tag=args.tag)

    def gh(*command):
        return subprocess.check_output(["gh", *command, "--repo", args.repo], text=True)

    # Listing distinguishes absence from auth/network failure (which must abort).
    releases = json.loads(gh("release", "list", "--limit", "1000", "--json", "tagName"))
    exists = any(release["tagName"] == args.tag for release in releases)
    local = {
        path.name: digest(path)
        for path in args.dist.iterdir()
        if path.name
        in {
            manifest["artifact"],
            "compatibility.json",
            "RELEASE-NOTES.md",
            "SHA256SUMS",
            "LICENSE",
        }
    }
    if not exists:
        gh(
            "release",
            "create",
            args.tag,
            "--draft",
            "--verify-tag",
            "--target",
            manifest["source"]["commit"],
            "--title",
            f"Stream {VERSION}",
            "--notes-file",
            str(args.dist / "RELEASE-NOTES.md"),
            *(["--prerelease"] if re.search(r"(?:a|b|rc)\d+$", VERSION) else []),
        )
    release = json.loads(
        gh("release", "view", args.tag, "--json", "isDraft,targetCommitish,assets")
    )
    if release["targetCommitish"] != manifest["source"]["commit"]:
        raise ValueError("remote release commit mismatch")
    names = [asset["name"] for asset in release["assets"]]
    if len(names) != len(set(names)) or set(names) - local.keys():
        raise ValueError("duplicate/foreign remote assets")
    existing = {}
    with tempfile.TemporaryDirectory(prefix="stream-release-") as directory:
        for name in names:
            gh("release", "download", args.tag, "--pattern", name, "--dir", directory)
            existing[name] = digest(Path(directory) / name)
    for name in asset_plan(local, existing, release["isDraft"]):
        gh("release", "upload", args.tag, str(args.dist / name))
    # Verify ALL uploaded bytes before changing draft status. A failure leaves a draft.
    with tempfile.TemporaryDirectory(prefix="stream-release-verify-") as directory:
        gh("release", "download", args.tag, "--dir", directory)
        downloaded = {path.name: digest(path) for path in Path(directory).iterdir()}
        if downloaded != local:
            raise ValueError("remote bundle verification failed; draft not finalized")
    if release["isDraft"]:
        gh("release", "edit", args.tag, "--draft=false")
    print("RELEASE: identical complete remote assets verified; no assets overwritten")


if __name__ == "__main__":
    main()
