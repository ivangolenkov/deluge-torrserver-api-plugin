from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py

from tools.version import VERSION, python_requires, python_tag, version_source

python_tag()  # Never build an artifact for an untested Python minor by accident.


class VersionedBuild(build_py):
    def run(self):
        super().run()
        # Only build output changes; the checked-out source must remain clean.
        (Path(self.build_lib) / "deluge_stream/_version.py").write_bytes(
            version_source()
        )


setup(
    name="Stream",
    version=VERSION,
    cmdclass={"build_py": VersionedBuild},
    description=(
        "Deluge streaming, temporary torrent retention and ready file/ZIP downloads"
    ),
    packages=find_packages(include=["deluge_stream", "deluge_stream.*"]),
    python_requires=python_requires(),
    package_data={"deluge_stream": ["data/*.js"]},
    entry_points={
        "deluge.plugin.core": ["Stream = deluge_stream:CorePlugin"],
        "deluge.plugin.web": ["Stream = deluge_stream:WebUIPlugin"],
    },
    zip_safe=True,
)
