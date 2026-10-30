import tarfile

import pytest

from tools.volume_snapshot import validate_members


def entry(name, kind=tarfile.REGTYPE):
    value = tarfile.TarInfo(name)
    value.type = kind
    return value


def test_snapshot_accepts_only_expected_regular_tree():
    validate_members(
        [
            entry("config", tarfile.DIRTYPE),
            entry("config/stream.sqlite"),
            entry("downloads/payload.bin"),
        ],
        {"config", "downloads"},
    )


@pytest.mark.parametrize(
    "name", ["/config/auth", "config/../outside", "foreign/file", ""]
)
def test_snapshot_rejects_unsafe_names(name):
    with pytest.raises(ValueError, match="unsafe"):
        validate_members([entry(name)], {"config"})


@pytest.mark.parametrize(
    "kind",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.FIFOTYPE,
    ],
)
def test_snapshot_rejects_links_and_special_files(kind):
    with pytest.raises(ValueError, match="unsafe"):
        validate_members([entry("config/link", kind)], {"config"})


def test_snapshot_rejects_duplicate_targets():
    with pytest.raises(ValueError, match="unsafe"):
        validate_members([entry("config/auth"), entry("config/auth")], {"config"})
