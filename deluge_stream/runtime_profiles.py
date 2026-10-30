"""Explicit native stacks; pure data/helpers, no Deluge import or I/O."""

RUNTIME_PROFILES = {
    "3.11": {
        "os": "Linux",
        "architectures": ["x86_64", "aarch64"],
        "python": "3.11",
        "Deluge": "2.2.0",
        "libtorrent": "2.0.8.0",
        "Twisted": "24.11.0",
    },
    "3.14": {
        "os": "Linux",
        "architectures": ["x86_64"],
        "python": "3.14",
        "Deluge": "2.2.0",
        "libtorrent": "2.0.14.0",
        "Twisted": "26.10.0",
    },
}


def supported_runtime(system, python, deluge, libtorrent, twisted, sqlite):
    profile = RUNTIME_PROFILES.get(python)
    return bool(
        profile
        and system == profile["os"]
        and deluge == profile["Deluge"]
        and libtorrent == profile["libtorrent"]
        and twisted == profile["Twisted"]
        and sqlite >= (3, 40, 1)
    )
