"""Create and verify local delivery metadata; never publish or infer acceptance."""

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from email.parser import BytesParser
from pathlib import Path

try:
    from tools.version import ROOT, VERSION, egg_name
except ModuleNotFoundError:
    from version import ROOT, VERSION, egg_name

RUNTIME = {
    "os": "Linux",
    "architectures": ["x86_64", "aarch64"],
    "python": "3.11",
    "Deluge": "2.2.0",
    "libtorrent": "2.0.8.0",
    "Twisted": "24.11.0",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def provenance(root):
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()

    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
    ).split(b"\0")
    tree = hashlib.sha256()
    for name in sorted(set(paths) - {b""}):
        path = root / name.decode("utf-8")
        tree.update(name + b"\0")
        if path.is_symlink():
            raise ValueError(f"source symlink not supported: {path}")
        if path.is_file():
            tree.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
            tree.update(bytes.fromhex(digest(path)))
        else:
            tree.update(b"missing\0")
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=all")),
        "tree_sha256": tree.hexdigest(),
    }


def check_egg(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive entries")
        if any(n.startswith("/") or ".." in n.split("/") for n in names):
            raise ValueError("unsafe archive path")
        if archive.testzip() is not None:
            raise ValueError("corrupt artifact")
        source_files = {
            source.relative_to(ROOT).as_posix(): source
            for source in (ROOT / "deluge_stream").rglob("*")
            if source.is_file() and source.suffix in {".py", ".js"}
        }
        packaged = {name for name in names if name.startswith("deluge_stream/")}
        if packaged != source_files.keys():
            raise ValueError("package member set differs from current source")
        for name, source in source_files.items():
            if archive.read(name) != source.read_bytes():
                raise ValueError(f"stale/different packaged source: {name}")
        info = BytesParser().parsebytes(archive.read("EGG-INFO/PKG-INFO"))
        if info["Name"] != "Stream" or info["Version"] != VERSION:
            raise ValueError("artifact version/name mismatch")
        entries = archive.read("EGG-INFO/entry_points.txt").decode()
        for entry in ("deluge.plugin.core", "deluge.plugin.web"):
            if f"[{entry}]" not in entries:
                raise ValueError("missing Deluge entry point")
        for name in (
            "deluge_stream/data/stream.js",
            "deluge_stream/core.py",
            "deluge_stream/webui.py",
            "deluge_stream/_version.py",
        ):
            if not archive.read(name):
                raise ValueError(f"empty resource: {name}")
        # Compare the packaged version source without executing archive code.
        if (
            archive.read("deluge_stream/_version.py")
            != (ROOT / "deluge_stream/_version.py").read_bytes()
        ):
            raise ValueError("packaged version source mismatch")


def create(dist, root=ROOT):
    artifact = dist / egg_name()
    check_egg(artifact)
    license_path = root / "LICENSE"
    licensed = license_path.is_file() and bool(license_path.read_text().strip())
    if licensed:
        (dist / "LICENSE").write_bytes(license_path.read_bytes())
    elif (dist / "LICENSE").exists():
        raise ValueError("stale LICENSE in dist; use a fresh output directory")
    manifest = {
        "schema": 1,
        "version": VERSION,
        "artifact": artifact.name,
        "artifact_sha256": digest(artifact),
        "runtime_contract": RUNTIME,
        "source": provenance(root),
        "license_sha256": digest(dist / "LICENSE") if licensed else None,
        "acceptance": "Not certified by this manifest; see phase reports",
    }
    (dist / "compatibility.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    notes = (
        f"# Stream {VERSION}: local delivery candidate\n\n"
        "Install the same Python 3.11 egg in core and separate Web UI profiles.\n"
        "Runtime contract: Linux, Deluge 2.2.0, libtorrent 2.0.8.0, Twisted 24.11.0.\n"
        "Unverified Lampa/Num and target HDD acceptance is documented, "
        "not a publication prerequisite.\n"
        "Back up the complete stopped Deluge profile and downloads before updating.\n"
        "Explicit plugin disable resets instance policy/publication/metadata/viewed.\n"
        "This candidate is not an announcement of publication or release readiness.\n"
    )
    (dist / "RELEASE-NOTES.md").write_text(notes)
    files = [artifact.name, "compatibility.json", "RELEASE-NOTES.md"]
    if licensed:
        files.append("LICENSE")
    (dist / "SHA256SUMS").write_text(
        "".join(f"{digest(dist / name)}  {name}\n" for name in sorted(files))
    )
    return manifest


def verify(dist, strict=False, tag=None, root=ROOT):
    manifest = json.loads((dist / "compatibility.json").read_text())
    if manifest["schema"] != 1 or manifest["version"] != VERSION:
        raise ValueError("manifest version/schema mismatch")
    if manifest["artifact"] != egg_name() or manifest["runtime_contract"] != RUNTIME:
        raise ValueError("manifest artifact/runtime mismatch")
    expected = {egg_name(), "compatibility.json", "RELEASE-NOTES.md"}
    if manifest["license_sha256"] is not None:
        expected.add("LICENSE")
    seen = set()
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match:
            raise ValueError("invalid checksum entry")
        checksum, name = match.groups()
        if name not in expected or name in seen:
            raise ValueError("unexpected/duplicate checksum entry")
        seen.add(name)
        if (dist / name).is_symlink() or digest(dist / name) != checksum:
            raise ValueError(f"checksum mismatch: {name}")
    if seen != expected:
        raise ValueError("incomplete checksum set")
    if digest(dist / egg_name()) != manifest["artifact_sha256"]:
        raise ValueError("manifest artifact checksum mismatch")
    check_egg(dist / egg_name())
    if manifest["source"] != provenance(root):
        raise ValueError("source provenance changed; recreate bundle")
    if manifest["license_sha256"] is not None and (
        digest(dist / "LICENSE") != manifest["license_sha256"]
        or (dist / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes()
    ):
        raise ValueError("license mismatch")
    if tag is not None and tag != f"v{VERSION}":
        raise ValueError("tag/version mismatch")
    if strict:
        if manifest["source"]["dirty"]:
            raise ValueError("release requires a clean source checkout")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        if args.create:
            create(args.dist)
        verify(args.dist, strict=args.strict, tag=args.tag)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"BUNDLE FAILED: {exc}") from exc
    print("BUNDLE: verified; no publication; smoke is a separate make prerequisite")


if __name__ == "__main__":
    main()
