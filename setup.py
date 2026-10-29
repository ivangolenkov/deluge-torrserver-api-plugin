from setuptools import find_packages, setup

setup(
    name="Stream",
    version="0.0.1",
    description="Deluge Stream phase-0 research prototype (not TorrServer yet)",
    packages=find_packages(include=["deluge_stream", "deluge_stream.*"]),
    python_requires=">=3.11",
    entry_points={"deluge.plugin.core": ["Stream = deluge_stream:CorePlugin"]},
    zip_safe=True,
)
