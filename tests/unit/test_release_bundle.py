import json
import shutil
import subprocess
import zipfile

import pytest

from tools import release_bundle as bundle
from tools.version import valid_version


def test_provenance_trusts_only_selected_checkout_without_changing_config(
    tmp_path, monkeypatch
):
    # Exercise Git's actual ownership guard, independent of the host's UID/config.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    monkeypatch.delenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", raising=False)
    root = tmp_path / "checkout with spaces"
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            "Isolated ownership fixture",
        ],
        cwd=root,
        check=True,
    )
    (root / "payload.txt").write_text("source fixture")
    config = (root / ".git/config").read_bytes()
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")

    def untrusted():
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
        )
        assert result.returncode != 0 and "dubious ownership" in result.stderr

    untrusted()
    source = bundle.provenance(root)
    assert len(source["commit"]) == 40
    assert len(source["tree_sha256"]) == 64
    assert source["dirty"]
    assert bundle.provenance(root) == source
    assert (root / ".git/config").read_bytes() == config
    untrusted()  # No persistent or wildcard trust leaked to later Git processes.


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
            "EGG-INFO/PKG-INFO",
            f"Name: Stream\nVersion: {bundle.VERSION}\n"
            f"Requires-Python: {bundle.python_requires()}\n",
        )
        archive.writestr(
            "EGG-INFO/entry_points.txt", "[deluge.plugin.core]\n[deluge.plugin.web]\n"
        )
        for source in (bundle.ROOT / "deluge_stream").rglob("*"):
            if source.is_file() and source.suffix in {".py", ".js"}:
                archive.writestr(
                    source.relative_to(bundle.ROOT).as_posix(),
                    bundle.version_source()
                    if source.name == "_version.py"
                    else source.read_bytes(),
                )
    bundle.create(dist, root)
    return root, dist


def test_release_without_license_is_allowed(delivery):
    root, dist = delivery
    manifest = bundle.verify(dist, strict=True, root=root)
    assert manifest["license_sha256"] is None
    assert not (dist / "LICENSE").exists()
    assert "LICENSE" not in (dist / "SHA256SUMS").read_text()


def test_release_without_license_still_requires_clean_source(delivery, monkeypatch):
    root, dist = delivery
    monkeypatch.setattr(
        bundle, "provenance", lambda root: {"commit": "a" * 40, "dirty": True}
    )
    bundle.create(dist, root)
    with pytest.raises(ValueError, match="clean source"):
        bundle.verify(dist, strict=True, root=root)


def test_release_without_license_still_checks_tag(delivery):
    root, dist = delivery
    with pytest.raises(ValueError, match="tag/version"):
        bundle.verify(dist, strict=True, tag="v9.9.9", root=root)


def test_licensed_clean_bundle_and_tag(delivery):
    root, dist = delivery
    (root / "LICENSE").write_text("Test license fixture; not a project license")
    bundle.create(dist, root)
    bundle.verify(dist, strict=True, root=root)
    with pytest.raises(ValueError, match="tag/version"):
        bundle.verify(dist, tag="v9.9.9", root=root)


@pytest.mark.parametrize("changed", ["source", "bundle"])
def test_optional_license_is_still_verified(delivery, changed):
    root, dist = delivery
    (root / "LICENSE").write_text("Test license fixture; not a project license")
    bundle.create(dist, root)
    ((root if changed == "source" else dist) / "LICENSE").write_text("Changed")
    with pytest.raises(ValueError, match="license mismatch|checksum mismatch"):
        bundle.verify(dist, strict=True, root=root)


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
    notes = (dist / "RELEASE-NOTES.md").read_text()
    assert "not a publication prerequisite" in notes
    assert "remain separate release gates" not in notes


def test_wrong_generated_runtime_version_is_rejected(delivery):
    _, dist = delivery
    artifact = dist / bundle.egg_name()
    with zipfile.ZipFile(artifact) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["deluge_stream/_version.py"] = b'__version__ = "99.99.99"\n'
    with zipfile.ZipFile(artifact, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="stale/different packaged source"):
        bundle.check_egg(artifact)


def test_wrong_python_minor_metadata_is_rejected(delivery):
    _, dist = delivery
    other = next(
        minor for minor in bundle.RUNTIME_PROFILES if minor != bundle.PYTHON_VERSION
    )
    with pytest.raises(ValueError, match="Python metadata mismatch"):
        bundle.check_egg(dist / bundle.egg_name(), other)


@pytest.fixture
def python_matrix(delivery, tmp_path):
    root, first = delivery
    second = tmp_path / "other-python"
    shutil.copytree(first, second)
    manifest = json.loads((second / "compatibility.json").read_text())
    other = next(
        minor
        for minor in bundle.RUNTIME_PROFILES
        if minor != manifest["runtime_contract"]["python"]
    )
    # Synthetic ZIP fixture only, not evidence that a renamed artifact installs.
    (second / manifest["artifact"]).rename(second / bundle.egg_name(other))
    manifest["artifact"] = bundle.egg_name(other)
    artifact = second / manifest["artifact"]
    with zipfile.ZipFile(artifact) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["EGG-INFO/PKG-INFO"] = (
        f"Name: Stream\nVersion: {bundle.VERSION}\n"
        f"Requires-Python: {bundle.python_requires(other)}\n"
    ).encode()
    with zipfile.ZipFile(artifact, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    manifest["artifact_sha256"] = bundle.digest(artifact)
    manifest["runtime_contract"] = bundle.RUNTIME_PROFILES[other]
    (second / "compatibility.json").write_text(json.dumps(manifest))
    names = [manifest["artifact"], "compatibility.json", "RELEASE-NOTES.md"]
    (second / "SHA256SUMS").write_text(
        "".join(f"{bundle.digest(second / name)}  {name}\n" for name in names)
    )
    return root, first, second, tmp_path / "combined"


def test_two_python_bundles_are_verified_and_combined(python_matrix):
    root, first, second, dist = python_matrix
    bundle.combine(dist, [first, second], root)
    manifest = bundle.verify(dist, strict=True, root=root)
    assert manifest["schema"] == 2
    assert {entry["artifact"] for entry in bundle.artifact_entries(manifest)} == {
        bundle.egg_name("3.11"),
        bundle.egg_name("3.14"),
    }


def test_combining_does_not_require_a_supported_host_python(python_matrix, monkeypatch):
    from tools import version

    root, first, second, dist = python_matrix
    # GitHub's verification host may use 3.12; only Docker smokes need 3.11/3.14.
    monkeypatch.setattr(version, "PYTHON_VERSION", "3.12")
    monkeypatch.setattr(bundle, "PYTHON_VERSION", "3.12")
    monkeypatch.setattr(bundle, "RUNTIME", None)
    bundle.combine(dist, [first, second], root)
    assert bundle.verify(dist, strict=True, root=root)["schema"] == 2


@pytest.mark.parametrize("duplicate", [False, True])
def test_incomplete_or_duplicate_python_matrix_is_rejected(python_matrix, duplicate):
    root, first, _, dist = python_matrix
    with pytest.raises(ValueError, match="Python artifact matrix"):
        bundle.combine(dist, [first, first] if duplicate else [first], root)


def test_tampered_python314_artifact_is_rejected(python_matrix):
    root, first, second, dist = python_matrix
    bundle.combine(dist, [first, second], root)
    with (dist / bundle.egg_name("3.14")).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        bundle.verify(dist, strict=True, root=root)
