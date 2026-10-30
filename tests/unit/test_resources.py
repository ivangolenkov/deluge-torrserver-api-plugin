import zipfile
from contextlib import ExitStack
from pathlib import Path

import pytest

from deluge_stream import resources


@pytest.mark.parametrize("zipped", [False, True])
def test_script_resource_is_cached_without_pkg_resources(tmp_path, monkeypatch, zipped):
    payload = b"// fixture script"
    with ExitStack() as stack:
        if zipped:
            archive_path = tmp_path / "fixture.egg"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("data/stream.js", payload)
            archive = stack.enter_context(zipfile.ZipFile(archive_path))
            root = zipfile.Path(archive)
        else:
            root = tmp_path
            (root / "data").mkdir()
            (root / "data/stream.js").write_bytes(payload)
        monkeypatch.setattr(resources, "files", lambda _: root)
        monkeypatch.setattr(resources, "_resources", stack.enter_context(ExitStack()))
        resources.script_path.cache_clear()
        try:
            path = Path(resources.script_path())
            assert path.name == "stream.js"
            assert path.read_bytes() == payload
            assert resources.script_path() == str(path)
            if zipped:
                resources.release_script()
                assert not path.exists()
                path = Path(resources.script_path())
                assert path.name == "stream.js" and path.read_bytes() == payload
        finally:
            resources.script_path.cache_clear()
    if zipped:
        assert not path.exists()
