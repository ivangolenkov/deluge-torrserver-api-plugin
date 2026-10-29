import pytest

from tools.publish_release import asset_plan


def test_publish_plan_missing_draft_assets_only():
    assert asset_plan({"egg": "a", "manifest": "b"}, {"egg": "a"}, True) == ["manifest"]


def test_identical_rerun_has_no_uploads():
    assert asset_plan({"egg": "a"}, {"egg": "a"}, False) == []


@pytest.mark.parametrize(
    "existing,draft", [({"egg": "b"}, True), ({"foreign": "a"}, True), ({}, False)]
)
def test_published_or_conflicting_assets_fail_closed(existing, draft):
    with pytest.raises(ValueError):
        asset_plan({"egg": "a"}, existing, draft)
