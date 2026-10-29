"""Delete only fixed, project-generated outputs; never configurable user paths."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for name in ("build", "dist", "Stream.egg-info", ".pytest_cache", ".ruff_cache"):
    path = root / name
    if path.is_symlink():
        raise SystemExit(f"Refusing symlink: {path}")
    if path.exists():
        shutil.rmtree(path)
