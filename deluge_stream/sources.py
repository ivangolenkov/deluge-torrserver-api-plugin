"""Bounded asynchronous HTTP source loading; filesystem preparation is worker-only."""

import base64
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import libtorrent as lt
from twisted.internet import defer, protocol
from twisted.web.client import Agent, ResponseDone
from twisted.web.http import PotentialDataLoss

from .adapter import hash_aliases


def parse_source(source):
    if isinstance(source, bytes):
        try:
            info = lt.torrent_info(lt.bdecode(source))
            if info.num_files() > 10000:
                raise ValueError("path_check_limit")
            storage = info.files()
            if (
                sum(
                    len(storage.file_path(index).encode())
                    for index in range(info.num_files())
                )
                > 2 * 1024**2
            ):
                raise ValueError("path_check_limit")
            for index in range(info.num_files()):
                name = storage.file_path(index)
                relative = PurePosixPath(name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in name
                    or storage.file_flags(index) & storage.flag_symlink
                ):
                    raise ValueError("unsafe_paths")
            aliases = tuple(hash_aliases(info).values())
        except (RuntimeError, ValueError) as exc:
            raise ValueError("invalid_torrent") from exc
        return source, aliases
    if not isinstance(source, str) or len(source) > 16384:
        raise ValueError("invalid_source")
    source = source.strip()
    if re.fullmatch("[A-Z2-7]{32}", source.upper()):
        source = base64.b32decode(source.upper()).hex()
    if re.fullmatch("[a-fA-F0-9]{40}", source):
        source = "magnet:?xt=urn:btih:" + source.lower()
    if source[:8].lower() == "magnet:?":
        source = "magnet:?" + source[8:]
    if not source.startswith("magnet:?"):
        raise ValueError("invalid_source")
    try:
        params = lt.parse_magnet_uri(source)
        if not params.info_hashes.has_v1():
            raise ValueError("v2_magnet_unsupported")
        # Deluge 2.2.0 accepts btih; raw .torrent supports the full v2 matrix.
        aliases = (str(params.info_hashes.v1),)
    except RuntimeError as exc:
        raise ValueError("invalid_source") from exc
    return source, aliases


def instance_directory(root):
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("unsafe_paths")
    for parent in (*reversed(root.parents), root):
        try:
            value = os.lstat(parent)
        except FileNotFoundError:
            parent.mkdir()
            value = os.lstat(parent)
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError("unsafe_paths")
    destination = root / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    return str(destination)


class Body(protocol.Protocol):
    def __init__(self, limit):
        self.limit, self.size, self.buffer = limit, 0, bytearray()
        self.done = defer.Deferred(lambda _: self.transport.abortConnection())

    def dataReceived(self, data):
        self.size += len(data)
        if self.done.called:
            return
        if self.size > self.limit:
            self.done.errback(ValueError("source_too_large"))
            self.transport.abortConnection()
            return
        self.buffer.extend(data)

    def connectionLost(self, reason):
        if self.done.called:
            self.buffer.clear()
            return
        if reason.check(ResponseDone, PotentialDataLoss):
            self.done.callback(bytes(self.buffer))
        else:
            self.done.errback(RuntimeError("source_error"))
        self.buffer.clear()


class Sources:
    def __init__(self, reactor, config):
        self.reactor, self.config = reactor, config
        self.agent = Agent(reactor)
        self.active = set()
        self.closed = False

    def load(self, source):
        if self.closed or len(self.active) >= 2:
            return defer.fail(RuntimeError("source_limit"))
        work = self._load(source)
        self.active.add(work)

        def finished(result):
            self.active.discard(work)
            return result

        work.addBoth(finished)
        work.addTimeout(self.config()["metadata_timeout_seconds"], self.reactor)
        return work

    @defer.inlineCallbacks
    def _load(self, url):
        for _ in range(6):
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
            ):
                raise ValueError("invalid_source")
            response = yield self.agent.request(b"GET", url.encode("ascii"))
            limit = min(self.config()["max_torrent_upload_mib"] * 1024**2, 16 * 1024**2)
            body = Body(limit)
            response.deliverBody(body)
            raw = yield body.done
            if 300 <= response.code < 400:
                location = response.headers.getRawHeaders(b"location", [])
                if not location or len(location[0]) > 16384:
                    raise ValueError("source_error")
                target = urljoin(url, location[0].decode("utf-8"))
                if target.startswith("magnet:?"):
                    return target
                url = target
                continue
            if response.code != 200:
                raise ValueError("source_error")
            if raw.strip().startswith(b"magnet:?"):
                return raw.strip().decode("utf-8")
            return raw
        raise ValueError("source_redirect_limit")

    def close(self):
        self.closed = True
        for work in tuple(self.active):
            work.cancel()
