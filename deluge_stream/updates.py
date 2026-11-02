"""Bounded GitHub release preparation; all methods run outside the reactor."""

import hashlib
import json
import os
import platform
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path

from . import __version__

REPOSITORY = "ivangolenkov/deluge-torrserver-api-plugin"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_JSON = 2 * 1024 * 1024
MAX_EGG = 16 * 1024 * 1024
HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com"}
ERRORS = {
    "update_busy",
    "update_cancelled",
    "update_network",
    "update_rate_limit",
    "update_invalid",
    "update_incompatible",
    "update_checksum",
    "update_conflict",
    "update_storage",
    "restart_unsupported",
    "forbidden",
    "core_unavailable",
}


def version_key(value):
    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(a|b|rc)(\d+))?", value
    )
    if value == "0.0.0.dev0":
        return (0, 0, 0, -1, 0)
    if not match:
        raise ValueError("update_invalid")
    major, minor, patch, suffix, number = match.groups()
    return (
        int(major),
        int(minor),
        int(patch),
        {"a": 0, "b": 1, "rc": 2, None: 3}[suffix],
        int(number or 0),
    )


def safe_url(url):
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in HOSTS
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise ValueError("update_invalid")
    return url


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


def fetch(url, limit, cancelled):
    request = urllib.request.Request(
        safe_url(url),
        headers={
            "User-Agent": f"Deluge-Stream/{__version__}",
            "Accept": "application/vnd.github+json"
            if url == API
            else "application/octet-stream",
        },
    )
    deadline = time.monotonic() + 60
    try:
        with urllib.request.build_opener(Redirects()).open(
            request, timeout=10
        ) as response:
            chunks, size = [], 0
            while True:
                if cancelled.is_set():
                    raise ValueError("update_cancelled")
                if time.monotonic() > deadline:
                    raise ValueError("update_network")
                chunk = response.read1(min(65536, limit + 1 - size))
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError("update_invalid")
    except urllib.error.HTTPError as exc:
        if url == API and exc.code == 404:
            return None
        raise ValueError(
            "update_rate_limit" if exc.code in (403, 429) else "update_network"
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ValueError("update_network") from exc


def sha(data):
    return hashlib.sha256(data).hexdigest()


def asset(release, name):
    matches = [item for item in release["assets"] if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError("update_invalid")
    item = matches[0]
    expected = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/{name}"
    if (
        item.get("browser_download_url") != expected
        or item.get("state") != "uploaded"
        or type(item.get("size")) is not int
        or not 0 < item["size"] <= MAX_EGG
    ):
        raise ValueError("update_invalid")
    return item


def download(item, limit, cancelled, getter):
    data = getter(item["browser_download_url"], limit, cancelled)
    if data is None or len(data) != item["size"]:
        raise ValueError("update_invalid")
    # GitHub's digest authenticates integrity within the trusted HTTPS source;
    # it is not an independent publisher signature.
    if item.get("digest") != "sha256:" + sha(data):
        raise ValueError("update_checksum")
    return data


def select(manifest, release, runtime):
    version = release["tag_name"][1:]
    if manifest.get("version") != version:
        raise ValueError("update_invalid")
    if manifest.get("schema") == 1:
        entries = [manifest]
    elif manifest.get("schema") == 2:
        entries = manifest["artifacts"]
    else:
        raise ValueError("update_invalid")
    matches = []
    for entry in entries:
        contract = entry["runtime_contract"]
        if all(
            contract.get(key) == value
            for key, value in runtime.items()
            if key != "architecture"
        ) and runtime["architecture"] in contract.get("architectures", []):
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError("update_incompatible")
    selected = matches[0]
    name = f"Stream-{version}-py{runtime['python']}.egg"
    if selected["artifact"] != name or not re.fullmatch(
        "[0-9a-f]{64}", selected["artifact_sha256"]
    ):
        raise ValueError("update_invalid")
    return selected


def validate_egg(path, version, python):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if (
            len(names) != len(set(names))
            or len(names) > 4096
            or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024
        ):
            raise ValueError("update_invalid")
        for name in names:
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise ValueError("update_invalid")
        metadata = BytesParser().parsebytes(archive.read("EGG-INFO/PKG-INFO"))
        if (
            metadata.get("Name") != "Stream"
            or metadata.get("Version") != version
            or archive.read("deluge_stream/_version.py")
            .decode()
            .find(f'__version__ = "{version}"')
            < 0
        ):
            raise ValueError("update_invalid")
        points = archive.read("EGG-INFO/entry_points.txt").decode()
        if "[deluge.plugin.core]" not in points or "[deluge.plugin.web]" not in points:
            raise ValueError("update_invalid")
        if archive.testzip() is not None:
            raise ValueError("update_checksum")


def runtime(role):
    from deluge.common import get_version
    from twisted import __version__ as twisted_version

    result = {
        "os": platform.system(),
        "architecture": platform.machine(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "Deluge": get_version(),
        "Twisted": twisted_version,
    }
    if role == "core":
        from deluge._libtorrent import lt

        result["libtorrent"] = lt.__version__
    return result


class UpdateStore:
    def __init__(
        self, profile, runtime_info, cancelled, getter=None, current=__version__
    ):
        self.profile = Path(profile)
        self.runtime = runtime_info
        self.cancelled = cancelled
        self.getter = getter or fetch
        self.current = current
        self.plan = None
        self.staged = None

    def clear(self):
        if self.staged is not None:
            self.staged.cleanup()
            self.staged = None
        self.plan = None

    def check(self):
        self.clear()
        data = self.getter(API, MAX_JSON, self.cancelled)
        status = {"current": self.current, "available": False, "version": None}
        if data is None:
            return status
        release = json.loads(data)
        tag = release["tag_name"]
        if (
            release.get("draft") is not False
            or release.get("prerelease") is not False
            or type(release.get("id")) is not int
            or not isinstance(tag, str)
            or not tag.startswith("v")
            or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
        ):
            raise ValueError("update_invalid")
        version = tag[1:]
        status.update(
            version=version, url=f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
        )
        if version_key(version) < version_key(self.current):
            return status
        manifest_data = download(
            asset(release, "compatibility.json"), MAX_JSON, self.cancelled, self.getter
        )
        selected = select(json.loads(manifest_data), release, self.runtime)
        package = asset(release, selected["artifact"])
        self.plan = {
            "release": release["id"],
            "version": version,
            "asset": package,
            "sha256": selected["artifact_sha256"],
            "token": secrets.token_hex(24),
        }
        return {
            **status,
            "available": version_key(version) > version_key(self.current),
            "release": release["id"],
            "token": self.plan["token"],
        }

    def require(self, token):
        if not self.plan or not isinstance(token, str) or token != self.plan["token"]:
            raise ValueError("update_conflict")
        if self.cancelled.is_set():
            raise ValueError("update_cancelled")
        return self.plan

    def prepare(self, token):
        plan = self.require(token)
        if self.staged is not None:
            return True
        data = download(plan["asset"], MAX_EGG, self.cancelled, self.getter)
        if sha(data) != plan["sha256"]:
            raise ValueError("update_checksum")
        plugins = self.profile / "plugins"
        if plugins.is_symlink() or not plugins.is_dir():
            raise ValueError("update_storage")
        staged = tempfile.TemporaryDirectory(prefix=".stream-update-", dir=plugins)
        try:
            path = Path(staged.name) / plan["asset"]["name"]
            with path.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            validate_egg(path, plan["version"], self.runtime["python"])
            self.require(token)
            self.staged = staged
        except BaseException:
            staged.cleanup()
            raise
        return True

    def commit(self, token):
        plan = self.require(token)
        if self.staged is None:
            raise ValueError("update_conflict")
        source = Path(self.staged.name) / plan["asset"]["name"]
        if sha(source.read_bytes()) != plan["sha256"]:
            raise ValueError("update_checksum")
        destination = self.profile / "plugins" / source.name
        try:
            # Atomic, no overwrite. Keep the running egg for lazy imports/recovery.
            os.link(source, destination)
        except FileExistsError:
            if (
                destination.is_symlink()
                or not destination.is_file()
                or sha(destination.read_bytes()) != plan["sha256"]
            ):
                raise ValueError("update_conflict") from None
        descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        plan["installed"] = True
        return {"installed": plan["version"], "restart_required": True}
