"""Read version metadata without importing Deluge or the source package."""

import re
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = runpy.run_path(str(ROOT / "deluge_stream/_version.py"))["__version__"]


def valid_version(version):
    pattern = (
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
        r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?"
    )
    return re.fullmatch(pattern, version) is not None


def egg_name():
    if not valid_version(VERSION):
        raise ValueError("version must be X.Y.Z or X.Y.Z{a,b,rc}N")
    return f"Stream-{VERSION}-py3.11.egg"


if __name__ == "__main__":
    print(VERSION)
