import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools import version


@pytest.mark.parametrize("tag", ["v0.1.3", "v2.0.0rc1", "v1.2.3a0", "v1.2.3b2"])
def test_explicit_tag_is_the_only_release_version(tmp_path, tag):
    assert version.release_tag(tmp_path, {"STREAM_RELEASE_TAG": tag}) == tag
    assert (
        version.release_tag(
            tmp_path, {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag}
        )
        == tag
    )


@pytest.mark.parametrize(
    "tag", ["0.1.3", "v01.2.3", "v1.2", "v1.2.3.dev0", "v1.2.3;bad"]
)
def test_invalid_release_tags_fail_instead_of_building_dev(tmp_path, tag):
    with pytest.raises(ValueError, match="release tag"):
        version.release_tag(tmp_path, {"STREAM_RELEASE_TAG": tag})


def test_snapshots_and_branch_jobs_are_dev(tmp_path):
    assert version.release_tag(tmp_path, {}) == ""
    assert version.release_tag(tmp_path, {"GITHUB_ACTIONS": "true"}) == ""
    assert version.release_tag(tmp_path, {"STREAM_RELEASE_TAG": ""}) == ""
    with pytest.raises(ValueError, match="missing GitHub"):
        version.release_tag(tmp_path, {"GITHUB_REF_TYPE": "tag"})


def test_local_exact_tag_and_ambiguity(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="notes\nv0.1.3\n")

    monkeypatch.setattr(version.subprocess, "run", run)
    assert version.release_tag(tmp_path, {}) == "v0.1.3"
    assert f"safe.directory={tmp_path.resolve()}" in calls[0]
    assert version.release_tag(tmp_path, {"STREAM_RELEASE_TAG": ""}) == ""
    assert len(calls) == 1
    monkeypatch.setattr(
        version.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="v0.1.3\nv0.1.4\n"
        ),
    )
    with pytest.raises(ValueError, match="multiple release tags"):
        version.release_tag(tmp_path, {})


def test_build_injects_tag_without_modifying_source_and_can_return_to_dev(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copy2(root / "setup.py", tmp_path)
    shutil.copytree(root / "deluge_stream", tmp_path / "deluge_stream")
    (tmp_path / "tools").mkdir()
    shutil.copy2(root / "tools/version.py", tmp_path / "tools/version.py")
    shutil.copy2(root / "tools/release_bundle.py", tmp_path / "tools/release_bundle.py")
    shutil.copy2(root / ".gitignore", tmp_path / ".gitignore")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    env.pop("GIT_TEST_ASSUME_DIFFERENT_OWNER", None)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, env=env, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "Isolated version fixture",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    source = tmp_path / "deluge_stream/_version.py"
    original = source.read_bytes()
    # Reuse the same build directory to catch stale generated _version.py.
    for tag, expected in (
        ("v0.1.3", "0.1.3"),
        ("v2.0.0rc1", "2.0.0rc1"),
        ("", version.DEV_VERSION),
    ):
        result = subprocess.run(
            [sys.executable, "setup.py", "bdist_egg"],
            cwd=tmp_path,
            env=dict(env, STREAM_RELEASE_TAG=tag),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        with zipfile.ZipFile(
            tmp_path / f"dist/Stream-{expected}-py{version.PYTHON_VERSION}.egg"
        ) as egg:
            assert f"Version: {expected}\n" in egg.read("EGG-INFO/PKG-INFO").decode()
            values = {}
            exec(egg.read("deluge_stream/_version.py"), values)
            assert values["__version__"] == expected and values["API_VERSION"] == 1
        assert source.read_bytes() == original
        artifact = tmp_path / f"dist/Stream-{expected}-py{version.PYTHON_VERSION}.egg"
        checked_bytes = artifact.read_bytes()
        verified = subprocess.run(
            [
                sys.executable,
                "tools/release_bundle.py",
                "--create",
                "--strict",
                *(["--tag", tag] if tag else []),
            ],
            cwd=tmp_path,
            env=dict(env, STREAM_RELEASE_TAG=tag),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert artifact.read_bytes() == checked_bytes
        assert not subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=tmp_path, env=env
        ).strip()
