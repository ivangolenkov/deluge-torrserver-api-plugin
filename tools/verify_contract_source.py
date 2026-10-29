"""Read-only verification of the optional sibling emulation reference.

Not required by make check: bundled schemas/examples must work without another
checkout, Go, a torrent engine or public network. This verifies source analysis,
not client compatibility and not execution of the Go handlers.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/torrserver-emulation"


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    schema = json.loads((FIXTURES / "schema.json").read_text())
    origin = manifest["provenance"]
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if commit != origin["commit"]:
        raise ValueError("reference commit differs from the recorded snapshot")
    for name, expected in origin["files_sha256"].items():
        path = (root / name).resolve(strict=True)
        if root not in path.parents:
            raise ValueError("reference path escapes the source root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"reference file changed: {name}")
    source = (root / "internal/delivery/http/torrserver/handlers.go").read_text()
    methods = {"Get": "GET", "Head": "HEAD", "Post": "POST"}
    actual = sorted(
        (methods[method], path, handler)
        for method, path, handler in re.findall(
            r'^\s*r\.(Get|Head|Post)\("([^"]+)", h\.(\w+)\)', source, re.MULTILINE
        )
    )
    expected = sorted(
        (route["method"], route["path"], route["handler"])
        for route in manifest["routes"]
    )
    if actual != expected:
        raise ValueError("recorded emulation routes do not match the source")
    primitive = {
        "string": "string",
        "bool": "boolean",
        "int": "integer",
        "int64": "integer",
        "float64": "number",
        "models.TorrentState": "string",
        "models.ErrorReason": "string",
        "[]torrentFileStat": "array",
    }
    structs = {
        "TorrentsReq": "TorrentsRequest",
        "torrentResponse": "TorrentStatus",
        "torrentFileStat": "FileStat",
        "cacheRequest": "CacheRequest",
    }
    for go_name, schema_name in structs.items():
        match = re.search(
            rf"type {go_name} struct \{{(.*?)^\}}", source, re.MULTILINE | re.DOTALL
        )
        if match is None:
            raise ValueError(f"missing emulation DTO: {go_name}")
        fields = re.findall(
            r'^\s*\w+\s+(\S+)\s+`json:"([^",]+)(,omitempty)?"`', match[1], re.MULTILINE
        )
        definition = schema["$defs"][schema_name]
        if set(definition["properties"]) != {name for _, name, _ in fields}:
            raise ValueError(f"field set changed: {go_name}")
        for kind, name, _ in fields:
            if definition["properties"][name].get("type") != primitive[kind]:
                raise ValueError(f"wire field type changed: {go_name}.{name}")
        if go_name in ("torrentResponse", "torrentFileStat"):
            required = {name for _, name, omit in fields if not omit}
            if set(definition["required"]) != required:
                raise ValueError(f"omitempty mismatch: {go_name}")
    return len(actual), len(structs), len(origin["files_sha256"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    result = verify(parser.parse_args().source_root)
    print(
        f"Source snapshot verified: {result[0]} routes, {result[1]} DTOs, "
        f"{result[2]} SHA256 files; no runtime/client verification claimed"
    )
