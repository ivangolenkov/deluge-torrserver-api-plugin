import shutil
import subprocess
import sys
from pathlib import Path


def cleaner(root):
    tools = root / "tools"
    tools.mkdir()
    target = tools / "clean.py"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "tools/clean.py", target)
    return target


def test_clean_removes_only_fixed_outputs(tmp_path):
    script = cleaner(tmp_path)
    for name in ("build", "dist", ".pytest_cache", ".ruff_cache", "Stream.egg-info"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "generated").touch()
    for name in ("downloads", "deluge-profile", ".venv", "custom-dist"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "keep.sqlite").touch()
    subprocess.run([sys.executable, str(script)], check=True)
    assert not (tmp_path / "build").exists()
    assert not (tmp_path / "dist").exists()
    for name in ("downloads", "deluge-profile", ".venv", "custom-dist"):
        assert (tmp_path / name / "keep.sqlite").is_file()


def test_clean_refuses_symlink(tmp_path):
    script = cleaner(tmp_path)
    protected = tmp_path / "downloads"
    protected.mkdir()
    (protected / "keep").touch()
    (tmp_path / "build").symlink_to(protected, target_is_directory=True)
    result = subprocess.run([sys.executable, str(script)], capture_output=True)
    assert result.returncode != 0
    assert (protected / "keep").is_file()
