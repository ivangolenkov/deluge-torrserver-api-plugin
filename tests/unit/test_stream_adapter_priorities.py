from types import SimpleNamespace

import pytest

from deluge_stream.stream_adapter import NativeStreams


@pytest.mark.parametrize(
    "cached,native,metadata,expected",
    [
        ([], [4], True, None),
        ([], [0], True, "file_skipped"),
        ([], [], False, "metadata_pending"),
        ([], [], True, "metadata_pending"),
        ([0], [4], True, "file_skipped"),
        ([4], [0], True, None),
    ],
)
def test_missing_reason_reads_empty_magnet_cache_without_setting_priorities(
    cached, native, metadata, expected
):
    reads = []
    original = list(cached)

    def native_priorities():
        reads.append(True)
        return native

    status = SimpleNamespace(
        has_metadata=metadata, errc=SimpleNamespace(value=lambda: 0), paused=False
    )
    handle = SimpleNamespace(
        status=lambda: status, get_file_priorities=native_priorities
    )
    torrent = SimpleNamespace(handle=handle, options={"file_priorities": cached})
    adapter = object.__new__(NativeStreams)
    adapter.manager = SimpleNamespace(session=SimpleNamespace(is_paused=lambda: False))
    session = SimpleNamespace(native=torrent, file=SimpleNamespace(index=0))
    assert adapter.missing_reason(session) == expected
    assert bool(reads) == (not cached and metadata)
    assert torrent.options["file_priorities"] is cached
    assert cached == original
