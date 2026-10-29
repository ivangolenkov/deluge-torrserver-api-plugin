import os

import pytest

from deluge_stream.path_policy import validate_temporary_paths


def test_private_payload_and_missing_files_allowed(tmp_path):
    (tmp_path / "movie").write_bytes(b"keep")
    plan = (("a", str(tmp_path), ("movie", "not-yet-downloaded")),)
    assert validate_temporary_paths("a", plan)
    assert (tmp_path / "movie").read_bytes() == b"keep"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "dir/../../escape", "a\\b"])
def test_traversal_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="unsafe_paths"):
        validate_temporary_paths("a", (("a", str(tmp_path), (name,)),))


def test_shared_paths_and_shared_inodes_rejected(tmp_path):
    path = tmp_path / "movie"
    path.write_bytes(b"keep")
    with pytest.raises(ValueError, match="shared_paths"):
        validate_temporary_paths(
            "a", (("a", str(tmp_path), ("movie",)), ("b", str(tmp_path), ("movie",)))
        )
    os.link(path, tmp_path / "other")
    with pytest.raises(ValueError, match="shared_paths"):
        validate_temporary_paths("a", (("a", str(tmp_path), ("movie",)),))


def test_symlinks_rejected_without_deleting_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "movie").write_bytes(b"keep")
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_paths"):
        validate_temporary_paths("a", (("a", str(tmp_path / "link"), ("movie",)),))
    assert (target / "movie").read_bytes() == b"keep"
