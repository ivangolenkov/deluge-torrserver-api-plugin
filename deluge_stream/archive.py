"""Incremental STORE/ZIP64 framing; payload and central directory stay separate."""

import struct
import zlib


class Archive:
    def __init__(self):
        self.offset = 0
        self.records = []
        self.current = None

    def _emit(self, data):
        self.offset += len(data)
        return data

    def begin(self, name, size):
        if self.current is not None:
            raise ValueError("entry_open")
        name = name.encode("utf-8")
        if len(name) > 65535 or size < 0:
            raise ValueError("invalid_entry")
        self.current = [name, size, self.offset, 0, 0]
        extra = struct.pack("<HHQQ", 1, 16, size, size)
        return self._emit(
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                45,
                0x808,
                0,
                0,
                33,
                0,
                0xFFFFFFFF,
                0xFFFFFFFF,
                len(name),
                len(extra),
            )
            + name
            + extra
        )

    def data(self, data):
        if self.current is None or self.current[4] + len(data) > self.current[1]:
            raise ValueError("invalid_entry_size")
        self.current[3] = zlib.crc32(data, self.current[3])
        self.current[4] += len(data)
        return self._emit(data)

    def end(self):
        if self.current is None or self.current[4] != self.current[1]:
            raise ValueError("invalid_entry_size")
        name, size, offset, crc, _ = self.current
        self.records.append((name, size, offset, crc))
        self.current = None
        return self._emit(struct.pack("<IIQQ", 0x08074B50, crc, size, size))

    def finish(self):
        if self.current is not None:
            raise ValueError("entry_open")
        start = self.offset
        for name, size, offset, crc in self.records:
            extra = struct.pack("<HHQQQ", 1, 24, size, size, offset)
            yield self._emit(
                struct.pack(
                    "<IHHHHHHIIIHHHHHII",
                    0x02014B50,
                    0x32D,
                    45,
                    0x808,
                    0,
                    0,
                    33,
                    crc,
                    0xFFFFFFFF,
                    0xFFFFFFFF,
                    len(name),
                    len(extra),
                    0,
                    0,
                    0,
                    (0o40755 if name.endswith(b"/") else 0o100644) << 16,
                    0xFFFFFFFF,
                )
                + name
                + extra
            )
        central_size = self.offset - start
        end_offset = self.offset
        count = len(self.records)
        yield self._emit(
            struct.pack(
                "<IQHHIIQQQQ",
                0x06064B50,
                44,
                45,
                45,
                0,
                0,
                count,
                count,
                central_size,
                start,
            )
            + struct.pack("<IIQI", 0x07064B50, 0, end_offset, 1)
            + struct.pack(
                "<IHHHHIIH",
                0x06054B50,
                0,
                0,
                min(count, 65535),
                min(count, 65535),
                min(central_size, 0xFFFFFFFF),
                min(start, 0xFFFFFFFF),
                0,
            )
        )
