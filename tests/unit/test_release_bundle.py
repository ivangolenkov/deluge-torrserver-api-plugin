import json
import zipfile

import pytest

from tools import release_bundle as bundle
from tools.version import valid_version


@pytest.mark.parametrize("version", ["0.0.1", "1.2.3rc1", "1.0.0a0", "1.0.0b1"])
def test_supported_version_format(version):
    assert valid_version(version)


@pytest.mark.parametrize(
    "version", ["v1.0.0", "1.0", "01.0.0", "1.0.0-rc1", "1.0.0+dirty"]
)
def test_unsupported_version_format(version):
    assert not valid_version(version)


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setattr(
        bundle, "provenance", lambda root: {"commit": "a" * 40, "dirty": False}
    )
    with zipfile.ZipFile(dist / bundle.egg_name(), "w") as archive:
        archive.writestr(
            "EGG-INFO/PKG-INFO", f"Name: Stream\nVersion: {bundle.VERSION}\n"
        )
        archive.writestr(
            "EGG-INFO/entry_points.txt", "[deluge.plugin.core]\n[deluge.plugin.web]\n"
        )
        for source in (bundle.ROOT / "deluge_stream").rglob("*"):
            if source.is_file() and source.suffix in {".py", ".js"}:
                archive.writestr(
                    source.relative_to(bundle.ROOT).as_posix(), source.read_bytes()
                )
    bundle.create(dist, root)
    return root, dist


def test_candidate_without_license_is_not_release_ready(delivery):
    root, dist = delivery
    assert bundle.verify(dist, root=root)["license_sha256"] is None
    with pytest.raises(ValueError, match="LICENSE"):
        bundle.verify(dist, strict=True, root=root)


def test_licensed_clean_bundle_and_tag(delivery):
    root, dist = delivery
    (root / "LICENSE").write_text("Test license fixture; not a project license")
    bundle.create(dist, root)
    bundle.verify(dist, strict=True, tag=f"v{bundle.VERSION}", root=root)
    with pytest.raises(ValueError, match="tag/version"):
        bundle.verify(dist, tag="v9.9.9", root=root)


@pytest.mark.parametrize(
    "name", ["RELEASE-NOTES.md", "compatibility.json", bundle.egg_name()]
)
def test_changed_file_rejected(delivery, name):
    root, dist = delivery
    with (dist / name).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="checksum"):
        bundle.verify(dist, root=root)


@pytest.mark.parametrize("entry", ["../outside", "/outside", "RELEASE-NOTES.md"])
def test_unsafe_or_duplicate_checksum_rejected(delivery, entry):
    root, dist = delivery
    with (dist / "SHA256SUMS").open("a") as stream:
        stream.write("a" * 64 + "  " + entry + "\n")
    with pytest.raises(ValueError):
        bundle.verify(dist, root=root)


def test_missing_checksum_rejected(delivery):
    root, dist = delivery
    sums = dist / "SHA256SUMS"
    sums.write_text("\n".join(sums.read_text().splitlines()[1:]) + "\n")
    with pytest.raises(ValueError, match="incomplete"):
        bundle.verify(dist, root=root)


def test_source_changed_rejected(delivery, monkeypatch):
    root, dist = delivery
    monkeypatch.setattr(
        bundle, "provenance", lambda root: {"commit": "b" * 40, "dirty": False}
    )
    with pytest.raises(ValueError, match="provenance"):
        bundle.verify(dist, root=root)


def test_manifest_is_not_acceptance_claim(delivery):
    _, dist = delivery
    manifest = json.loads((dist / "compatibility.json").read_text())
    assert "Not certified" in manifest["acceptance"]
