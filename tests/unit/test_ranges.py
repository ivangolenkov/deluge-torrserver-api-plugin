import pytest

from deluge_stream.ranges import InvalidRange, UnsatisfiableRange, resolve_range


@pytest.mark.parametrize(
    "header,size,expected",
    [
        (None, 10, (0, 10, False)),
        (None, 0, (0, 0, False)),
        ("bytes=0-0", 10, (0, 1, True)),
        ("bytes=2-", 10, (2, 10, True)),
        ("bytes=-3", 10, (7, 10, True)),
        ("bytes=-99", 10, (0, 10, True)),
        ("bytes=8-99", 10, (8, 10, True)),
        ("bytes=0-1,3-4", 10, (0, 10, False)),
        ("items=0-1", 10, (0, 10, False)),
        ("bytes=4294967296-", 2**33, (2**32, 2**33, True)),
    ],
)
def test_ranges(header, size, expected):
    assert resolve_range(header, size) == expected


@pytest.mark.parametrize(
    "header", ["bytes=", "bytes=a-b", "bytes=1", "bytes=--1", "bytes=0-1,no", "bytes"]
)
def test_invalid(header):
    with pytest.raises(InvalidRange):
        resolve_range(header, 10)


@pytest.mark.parametrize(
    "header,size",
    [("bytes=10-", 10), ("bytes=8-2", 10), ("bytes=-0", 10), ("bytes=0-", 0)],
)
def test_unsatisfiable(header, size):
    with pytest.raises(UnsatisfiableRange):
        resolve_range(header, size)


def test_negative_size():
    with pytest.raises(ValueError):
        resolve_range(None, -1)
