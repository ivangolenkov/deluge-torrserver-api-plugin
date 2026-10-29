import runpy
from pathlib import Path

from setuptools import find_packages, setup

version = runpy.run_path(str(Path(__file__).parent / "deluge_stream/_version.py"))

setup(
    name="Stream",
    version=version["__version__"],
    description=(
        "Deluge streaming, temporary torrent retention and ready file/ZIP downloads"
    ),
    packages=find_packages(include=["deluge_stream", "deluge_stream.*"]),
    python_requires=">=3.11,<3.12",
    package_data={"deluge_stream": ["data/*.js"]},
    entry_points={
        "deluge.plugin.core": ["Stream = deluge_stream:CorePlugin"],
        "deluge.plugin.web": ["Stream = deluge_stream:WebUIPlugin"],
    },
    zip_safe=True,
)
