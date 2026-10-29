import io
import zipfile

import pytest

from deluge_stream.archive import Archive
from deluge_stream.downloads import archive_names, fingerprints
from deluge_stream.layout import FileSpan


def test_streaming_store_zip64_is_readable_by_independent_zipfile():
    writer = Archive()
    output = io.BytesIO()
    for name, data in (
        ("каталог/", b""),
        ("каталог/a.bin", b"test" * 100),
        ("empty", b""),
    ):
        output.write(writer.begin(name, len(data)))
        output.write(writer.data(data))
        output.write(writer.end())
    for block in writer.finish():
        output.write(block)
    output.seek(0)
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["каталог/", "каталог/a.bin", "empty"]
        assert archive.read("каталог/a.bin") == b"test" * 100
        assert archive.testzip() is None
        assert archive.getinfo("каталог/a.bin").compress_type == zipfile.ZIP_STORED


@pytest.mark.parametrize(
    "names",
    [
        ("../escape",),
        ("/absolute",),
        ("C:/file",),
        ("a", "a/b"),
        ("a/b", "a\\b"),
        ("é", "e\u0301"),
    ],
)
def test_zip_names_reject_ambiguous_or_unsafe_paths(names):
    with pytest.raises(ValueError, match="unsafe_paths"):
        archive_names(
            tuple(FileSpan(i, name, 0, 0) for i, name in enumerate(names)), 100
        )


def test_payload_fingerprint_detects_replace_and_symlink(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data")
    files = (FileSpan(0, "file", 0, 4),)
    before = fingerprints(str(tmp_path), files)
    path.rename(tmp_path / "old")
    path.write_bytes(b"data")
    assert fingerprints(str(tmp_path), files) != before
    path.unlink()
    path.symlink_to(tmp_path / "old")
    with pytest.raises(ValueError, match="unsafe_paths"):
        fingerprints(str(tmp_path), files)


def test_empty_native_file_does_not_require_a_physical_parent_directory(tmp_path):
    files = (FileSpan(0, "absent/subdirectory/empty", 0, 0),)
    assert fingerprints(str(tmp_path), files) == (None,)
