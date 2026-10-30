import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import ci_check
from tools.ci_check import commands


def test_ci_uses_same_make_checks_without_network_or_publication():
    image, check = commands(Path("/checkout with spaces"), "linux/amd64", "test:local")
    assert image[1][-1] == "."
    assert image[1][:2] == ["docker", "build"]
    assert check[1][-4:] == ["test:local", "make", "check", "VENV=/opt/venv"]
    assert "/checkout with spaces:/work" in check[1]
    assert check[1][check[1].index("--network") + 1] == "none"
    assert f"STREAM_RELEASE_TAG={ci_check.RELEASE_TAG}" in check[1]
    assert not any(
        word in {"push", "publish", "release"}
        for _, command in (image, check)
        for word in command
    )


def test_workflow_guards_and_pins():
    # Static guard only; YAML syntax/action semantics are validated separately.
    text = (
        Path(__file__).resolve().parents[2] / ".github/workflows/checks.yml"
    ).read_text()
    for event in ("pull_request:", "workflow_dispatch:", "workflow_call:"):
        assert event in text
    assert "branches: [main]" in text
    assert "contents: read" in text and "persist-credentials: false" in text
    assert "if: always()" in text and "run: make ci-check" in text
    assert len(re.findall(r"uses: actions/[\w-]+@[0-9a-f]{40}", text)) == 2
    assert "secrets." not in text and "contents: write" not in text
    for expected in (
        "ubuntu-24.04-arm",
        "linux/arm64",
        "make docker-build test-docker",
    ):
        assert expected in text


def test_release_workflow_cannot_publish_from_manual_or_pr_runs():
    text = (
        Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    ).read_text()
    assert "uses: ./.github/workflows/checks.yml" in text
    assert "github.event_name == 'push' && github.ref_type == 'tag'" in text
    assert "make release-verify" in text and "no rebuild" in text
    assert "--clobber" not in text
    assert text.count("contents: write") == 1


@pytest.mark.parametrize("result", [0, 7, "timeout", "missing_docker"])
def test_ci_preserves_logs_and_never_masks_failure(tmp_path, monkeypatch, result):
    monkeypatch.setattr(ci_check, "__file__", str(tmp_path / "tools/ci_check.py"))
    monkeypatch.setattr(sys, "argv", ["ci_check.py"])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write("recorded output\n")
        if result == "timeout":
            raise subprocess.TimeoutExpired(command, 900)
        if result == "missing_docker":
            raise FileNotFoundError("docker")
        return SimpleNamespace(returncode=result)

    monkeypatch.setattr(ci_check.subprocess, "run", run)
    assert ci_check.main() == (result if isinstance(result, int) else 1)
    assert len(calls) == (2 if result == 0 else 1)
    assert (tmp_path / "build/ci/image.log").read_text() == "recorded output\n"
    if result == 0:
        assert (tmp_path / "build/ci/check.log").read_text() == "recorded output\n"


def test_failed_check_removes_only_its_own_named_container(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_check, "__file__", str(tmp_path / "tools/ci_check.py"))
    monkeypatch.setattr(sys, "argv", ["ci_check.py"])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=23 if len(calls) == 2 else 0)

    monkeypatch.setattr(ci_check.subprocess, "run", run)
    assert ci_check.main() == 23
    assert calls[2][:3] == ["docker", "rm", "--force"]
    assert calls[2][3] == calls[1][calls[1].index("--name") + 1]
    assert calls[2][3].startswith("deluge-stream-ci-")
