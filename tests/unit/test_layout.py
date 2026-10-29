import pytest

from deluge_stream.layout import FileSpan, Layout


@pytest.fixture
def layout():
    return Layout(
        16,
        4,
        (
            FileSpan(0, "a", 0, 17),
            FileSpan(1, "pad", 17, 15, pad=True),
            FileSpan(2, "empty", 32, 0),
            FileSpan(3, "b", 32, 17),
        ),
        frozenset({0, 3}),
    )


def test_payload_mapping_does_not_shift_physical_offsets(layout):
    mapping = layout.payload()
    assert [file.index for file in mapping.values()] == [0, 2, 3]
    assert mapping[3].offset == 32
    assert list(layout.pieces(mapping[1])) == [0, 1]
    assert list(layout.pieces(mapping[2])) == []
    assert list(layout.pieces(mapping[3])) == [2, 3]


def test_priority_is_current_policy_not_stream_snapshot(layout):
    assert layout.priority(0, [4, 0, 0, 1]) == 4
    assert layout.priority(0, [1, 0, 0, 1]) == 1
    assert layout.priority(0, [1, 0, 0, 1], first_last=True) == 7
    assert layout.priority(0, [0, 0, 0, 1], first_last=True) == 0
    assert layout.priority(2, [4, 0, 0, 1], first_last=True) == 1


def test_shared_boundary_uses_max_priority():
    layout = Layout(
        16,
        2,
        (
            FileSpan(0, "a", 0, 17),
            FileSpan(1, "b", 17, 15),
        ),
        frozenset(),
    )
    assert layout.priority(1, [0, 6]) == 6
    assert layout.priority(1, [4, 1]) == 4


def test_bad_policy_or_piece_is_rejected(layout):
    with pytest.raises(ValueError):
        layout.priority(0, [4])
    with pytest.raises(ValueError):
        layout.priority(4, [4, 0, 0, 4])
