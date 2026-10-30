"""Static Web UI resource usable from unpacked plugins and legacy zipped eggs."""

from contextlib import ExitStack
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

# Hold at most one extracted script while enabled. TemporaryDirectory also has
# its own finalizer; do not register module callbacks that survive plugin unload.
_resources = ExitStack()


@lru_cache(maxsize=1)
def script_path():
    resource = files("deluge_stream") / "data/stream.js"
    if isinstance(resource, Path):
        return str(resource)
    # as_file() gives a random basename; Deluge uses that basename in the URL.
    directory = _resources.enter_context(TemporaryDirectory(prefix="stream-web-"))
    path = Path(directory) / "stream.js"
    path.write_bytes(resource.read_bytes())
    return str(path)


def release_script():
    script_path.cache_clear()
    _resources.close()
