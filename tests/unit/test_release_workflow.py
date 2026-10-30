"""Static wiring checks; these do not simulate a hosted GitHub Actions run."""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"


def test_publish_has_no_external_acceptance_approval():
    source = WORKFLOW.read_text()
    publish = source.split("\n  publish:\n", 1)[1]
    assert "environment:" not in publish
    assert "RELEASE_ACCEPTANCE_APPROVED" not in source
    assert "APPROVED" not in publish


def test_publish_still_requires_checked_artifacts_and_tag_push():
    source = WORKFLOW.read_text()
    assert "uses: ./.github/workflows/checks.yml" in source
    assert "needs: checks" in source
    assert "make release-verify" in source
    publish = source.split("\n  publish:\n", 1)[1]
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in publish
    assert "needs: verify" in publish
    assert "name: verified-release" in publish
    assert "python3 tools/publish_release.py" in publish
