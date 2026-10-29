from setuptools import find_packages, setup

setup(
    name="Stream",
    version="0.0.1",
    description="Deluge Stream core/web scaffold (not TorrServer yet)",
    packages=find_packages(include=["deluge_stream", "deluge_stream.*"]),
    python_requires=">=3.11",
    package_data={"deluge_stream": ["data/*.js"]},
    entry_points={
        "deluge.plugin.core": ["Stream = deluge_stream:CorePlugin"],
        "deluge.plugin.web": ["Stream = deluge_stream:WebUIPlugin"],
    },
    zip_safe=True,
)
