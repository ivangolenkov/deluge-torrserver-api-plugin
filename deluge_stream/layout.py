"""Immutable phase-0 metadata model, using libtorrent offsets, not UI sizes.

No libtorrent imports here. The adapter builds the snapshot on the reactor.
Priority reconstruction is a research candidate, not a live QoS controller.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FileSpan:
    index: int
    path: str
    offset: int
    size: int
    pad: bool = False


@dataclass(frozen=True)
class Layout:
    piece_length: int
    piece_count: int
    files: tuple[FileSpan, ...]
    first_last: frozenset[int]

    def payload(self):
        """Explicit external (1-based) -> internal mapping, including empty files."""
        return {
            index: file
            for index, file in enumerate(
                (file for file in self.files if not file.pad), 1
            )
        }

    def pieces(self, file):
        if file.size == 0:
            return range(0)
        return range(
            file.offset // self.piece_length,
            (file.offset + file.size - 1) // self.piece_length + 1,
        )

    def priority(self, piece, file_priorities, first_last=False):
        """Current Deluge file policy, restricted to one touched piece."""
        if len(file_priorities) != len(self.files):
            raise ValueError("file policy does not match metadata")
        if not 0 <= piece < self.piece_count:
            raise ValueError("piece outside metadata")
        start = piece * self.piece_length
        stop = start + self.piece_length
        priority = max(
            (
                file_priorities[file.index]
                for file in self.files
                if not file.pad
                and file.size
                and file.offset < stop
                and file.offset + file.size > start
            ),
            default=0,
        )
        if priority and first_last and piece in self.first_last:
            return 7
        return priority
