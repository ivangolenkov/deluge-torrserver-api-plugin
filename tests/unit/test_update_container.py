from pathlib import Path
from runpy import run_path

import pytest

install = run_path(str(Path(__file__).resolve().parents[2] / "docker/entrypoint.py"))[
    "install_packaged_egg"
]


def test_recreation_keeps_newer_ui_update_and_original_backup(tmp_path):
    image = tmp_path / "Stream-1.0.0-py3.11.egg"
    image.write_bytes(b"image")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    install(image, plugins)
    updated = plugins / "Stream-1.1.0-py3.11.egg"
    updated.write_bytes(b"update")
    install(image, plugins)
    assert updated.read_bytes() == b"update"
    assert (plugins / image.name).read_bytes() == b"image"


def test_same_version_different_content_and_python_change_need_resolution(tmp_path):
    image = tmp_path / "Stream-1.0.0-py3.11.egg"
    image.write_bytes(b"image")
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / image.name).write_bytes(b"other build")
    with pytest.raises(ValueError, match="different content"):
        install(image, plugins)
    (plugins / "Stream-2.0.0-py3.14.egg").write_bytes(b"other runtime")
    with pytest.raises(ValueError, match="incompatible"):
        install(image, plugins)
