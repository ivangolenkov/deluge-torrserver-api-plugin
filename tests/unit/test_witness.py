import errno
import os

import pytest

from deluge_stream.witness import create_witness, witness_matches


def test_link_pins_original_metadata_after_same_hash_replacement(tmp_path):
    source = tmp_path / "hash.torrent"
    source.write_bytes(b"same metadata")
    directory = tmp_path / "witnesses"
    witness = create_witness(source, directory)
    assert witness_matches(witness, source, directory)
    source.unlink()
    source.write_bytes(b"same metadata")
    assert not witness_matches(witness, source, directory)
    assert (directory / witness.token).read_bytes() == b"same metadata"
    assert source.read_bytes() == b"same metadata"


def test_in_place_metadata_edit_is_not_trusted(tmp_path):
    source = tmp_path / "hash.torrent"
    source.write_bytes(b"metadata")
    directory = tmp_path / "witnesses"
    witness = create_witness(source, directory)
    source.write_bytes(b"changed length")
    assert not witness_matches(witness, source, directory)


def test_reconstructed_witness_survives_reopen(tmp_path):
    source = tmp_path / "hash.torrent"
    source.write_bytes(b"metadata")
    directory = tmp_path / "witnesses"
    witness = create_witness(source, directory)
    assert witness_matches(type(witness)(**vars(witness)), source, directory)
    (directory / witness.token).unlink()
    assert not witness_matches(witness, source, directory)


def test_symlink_metadata_is_rejected_without_opening_target(tmp_path):
    protected = tmp_path / "protected"
    protected.write_bytes(b"keep")
    source = tmp_path / "hash.torrent"
    source.symlink_to(protected)
    with pytest.raises(OSError):
        create_witness(source, tmp_path / "witnesses")
    assert protected.read_bytes() == b"keep"


def test_fifo_metadata_is_rejected_without_blocking(tmp_path):
    source = tmp_path / "hash.torrent"
    os.mkfifo(source)
    with pytest.raises(ValueError):
        create_witness(source, tmp_path / "witnesses")


def test_symlink_reference_directory_is_rejected(tmp_path):
    source = tmp_path / "hash.torrent"
    source.write_bytes(b"metadata")
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = tmp_path / "witnesses"
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        create_witness(source, directory)
    assert list(outside.iterdir()) == []


def test_unsupported_hardlinks_fail_without_fallback(tmp_path, monkeypatch):
    source = tmp_path / "hash.torrent"
    source.write_bytes(b"keep")
    directory = tmp_path / "witnesses"

    def unavailable(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", unavailable)
    with pytest.raises(OSError, match="cross-device"):
        create_witness(source, directory)
    assert source.read_bytes() == b"keep"
    assert list(directory.iterdir()) == []
