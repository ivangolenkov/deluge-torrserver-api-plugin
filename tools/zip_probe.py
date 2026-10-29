"""ZIP64 writer experiment, not the future HTTP/archive implementation.

Virtual sink stores headers and zero-run lengths only, never a full 4 GiB ZIP.
The standard zipfile reader then validates the streamed output and its CRC.
"""

import bisect
import io
import json
import zipfile


class VirtualArchive:
    def __init__(self):
        self.starts = []
        self.parts = []
        self.lengths = []
        self.size = 0
        self.position = 0
        self.readable_mode = False
        self.retained = 0
        self.max_write = 0

    def tell(self):
        return self.position if self.readable_mode else self.size

    def seek(self, offset, whence=0):
        if not self.readable_mode:
            raise io.UnsupportedOperation("nonseekable writer")
        self.position = (
            offset
            if whence == 0
            else self.position + offset
            if whence == 1
            else self.size + offset
        )
        return self.position

    def write(self, data):
        if not data:
            return 0
        self.max_write = max(self.max_write, len(data))
        zero_run = data.count(0) == len(data)
        if zero_run and self.parts and self.parts[-1] is None:
            self.lengths[-1] += len(data)
        else:
            self.starts.append(self.size)
            self.parts.append(None if zero_run else bytes(data))
            self.lengths.append(len(data))
            if not zero_run:
                self.retained += len(data)
        self.size += len(data)
        return len(data)

    def flush(self):
        pass

    def seekable(self):
        return self.readable_mode

    def read(self, count=-1):
        count = self.size - self.position if count < 0 else count
        stop = min(self.position + count, self.size)
        chunks = []
        while self.position < stop:
            index = bisect.bisect_right(self.starts, self.position) - 1
            offset = self.position - self.starts[index]
            length = min(self.lengths[index] - offset, stop - self.position)
            part = self.parts[index]
            chunks.append(
                bytes(length) if part is None else part[offset : offset + length]
            )
            self.position += length
        return b"".join(chunks)


def probe():
    sink = VirtualArchive()
    chunk = bytes(1024 * 1024)
    large_size = 2**32 + 123
    entries = 65536
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED) as archive:
        with archive.open("каталог/large.bin", "w", force_zip64=True) as entry:
            remaining = large_size
            while remaining:
                block = chunk[: min(len(chunk), remaining)]
                entry.write(block)
                remaining -= len(block)
        for index in range(entries):
            archive.writestr(f"empty/{index}.bin", b"")
    assert sink.size > 2**32
    assert sink.retained < 16 * 1024 * 1024
    assert sink.max_write <= len(chunk)
    sink.readable_mode = True
    with zipfile.ZipFile(sink) as archive:
        assert len(archive.infolist()) == entries + 1
        assert archive.getinfo("каталог/large.bin").file_size == large_size
        total = 0
        with archive.open("каталог/large.bin") as entry:
            while block := entry.read(len(chunk)):
                assert block.count(0) == len(block)
                total += len(block)
        assert total == large_size  # zipfile verifies CRC at EOF.
        assert archive.read("empty/65535.bin") == b""

    class CancelledSink(VirtualArchive):
        def write(self, data):
            if self.size + len(data) > 2 * len(chunk):
                raise BrokenPipeError("simulated client cancel")
            return super().write(data)

    cancelled = CancelledSink()
    reads = 0
    try:
        with zipfile.ZipFile(cancelled, "w") as archive:
            with archive.open("cancel.bin", "w", force_zip64=True) as entry:
                for _ in range(4096):
                    reads += 1
                    entry.write(chunk)
    except BrokenPipeError:
        pass
    else:
        raise AssertionError("cancel did not stop writer")
    assert reads <= 3
    print(
        json.dumps(
            {
                "zip64_file_bytes": large_size,
                "entries": entries + 1,
                "virtual_archive_bytes": sink.size,
                "retained_header_bytes": sink.retained,
                "max_write_bytes": sink.max_write,
                "crc": "verified by zipfile reader",
                "cancel_source_reads": reads,
            }
        )
    )


if __name__ == "__main__":
    probe()
