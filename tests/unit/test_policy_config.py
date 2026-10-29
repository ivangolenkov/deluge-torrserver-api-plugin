import pytest

from deluge_stream.config import DEFAULTS, validate


def test_config_is_complete_and_no_partial_update():
    before = dict(DEFAULTS)
    assert validate({"max_temporary_torrents": 8})["max_temporary_torrents"] == 8
    assert DEFAULTS == before
    with pytest.raises(ValueError):
        validate({"max_temporary_torrents": 9, "bind_port": False}, before)
    assert before == DEFAULTS


@pytest.mark.parametrize(
    "change",
    [
        {"max_temporary_torrents": 0},
        {"max_temporary_torrents": True},
        {"cleanup_retry_seconds": 1},
        {"bind_port": 65536},
        {"bind_host": "unresolved.invalid"},
        {"download_root": "relative"},
        {"public_base_url": "file:///tmp/a"},
        {"public_base_url": "http://user:pw@host"},
        {"public_base_url": "http://host:99999"},
        {"public_base_url": "http://host?q=secret"},
        {"torrent_owner": "\x00"},
        {"ttl": 10},
        {"http_enabled": 1},
    ],
)
def test_invalid_config(change):
    with pytest.raises(ValueError):
        validate(change)


def test_base_url_download_root_and_owner_do_not_require_io():
    value = validate(
        {
            "public_base_url": "https://example.invalid/stream",
            "download_root": "/downloads/stream",
            "torrent_owner": "alice",
        }
    )
    assert value["public_base_url"] == "https://example.invalid/stream"
