"""Loopback-only phase-0 piece reader, deliberately not a product HTTP API."""

import logging
import secrets
import sys

from deluge.common import get_version
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase
from twisted.internet import defer, reactor
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET, Site

from .adapter import PieceAdapter, deregister_rpc
from .ranges import InvalidRange, UnsatisfiableRange, resolve_range

log = logging.getLogger(__name__)


class Core(CorePluginBase):
    def enable(self):
        self.token = secrets.token_hex(32)
        self.adapter = PieceAdapter(reactor)
        self.active = None
        try:
            site = Site(Probe(self))
            site.log = lambda _: None  # No query strings/tokens in access logs.
            self.listener = reactor.listenTCP(0, site, interface="127.0.0.1")
        except Exception:
            self.adapter.close()
            raise

    def disable(self):
        if self.active is not None:
            request = self.active.request
            self.active.stopProducing()
            request.transport.abortConnection()
        self.adapter.close()
        self.token = None
        deregister_rpc(self)
        return self.listener.stopListening()

    @export(10)
    def probe_info(self):
        """Admin-only, ephemeral test endpoint, not a user download link."""
        return {
            "port": self.listener.getHost().port,
            "token": self.token,
            "deluge": get_version(),
            "python": sys.version.split()[0],
            "active": self.active is not None,
            "waiting": self.adapter.pending is not None,
        }


class Probe(Resource):
    isLeaf = True

    def __init__(self, core):
        super().__init__()
        self.core = core

    def render(self, request):
        if self.core.token is None:
            request.setResponseCode(503)
            return b"probe disabled"
        if request.method not in (b"GET", b"HEAD"):
            request.setResponseCode(405)
            return b"method not allowed"
        if request.getHeader("Authorization") != f"Bearer {self.core.token}":
            request.setResponseCode(403)
            return b"forbidden"
        try:
            torrent_id = request.args[b"torrent"][0].decode("ascii")
            file_id = int(request.args[b"file"][0])
            torrent, size, offset, piece_length = self.core.adapter.file_spec(
                torrent_id, file_id
            )
            start, stop, partial = resolve_range(
                request.getHeader("Range") if request.method == b"GET" else None, size
            )
        except UnsatisfiableRange:
            request.setResponseCode(416)
            request.setHeader(b"Content-Range", f"bytes */{size}".encode())
            return b""
        except (
            InvalidRange,
            ValueError,
            KeyError,
            IndexError,
            AttributeError,
            RuntimeError,
        ):
            request.setResponseCode(400)
            return b"invalid probe request"
        if self.core.active is not None and request.method != b"HEAD":
            request.setResponseCode(429)
            return b"prototype allows one reader"
        request.setHeader(b"Content-Type", b"application/octet-stream")
        request.setHeader(b"Accept-Ranges", b"bytes")
        request.setHeader(b"Content-Length", str(stop - start).encode())
        if partial:
            request.setResponseCode(206)
            request.setHeader(
                b"Content-Range", f"bytes {start}-{stop - 1}/{size}".encode()
            )
        if request.method == b"HEAD" or start == stop:
            return b""
        producer = PieceProducer(
            self.core,
            request,
            torrent_id,
            torrent,
            offset + start,
            offset + stop,
            piece_length,
        )
        self.core.active = producer
        request.registerProducer(producer, True)
        request.notifyFinish().addBoth(lambda _: producer.stopProducing())
        producer.resumeProducing()
        return NOT_DONE_YET


class PieceProducer:
    def __init__(self, core, request, torrent_id, torrent, start, stop, piece_length):
        self.core = core
        self.request = request
        self.torrent_id = torrent_id
        self.torrent = torrent
        self.position = start
        self.stop = stop
        self.piece_length = piece_length
        self.waiting = None
        self.buffer = None
        self.paused = False
        self.closed = False
        self.scheduled = None

    def pauseProducing(self):
        self.paused = True

    def resumeProducing(self):
        self.paused = False
        self._pump()

    def _pump(self):
        if self.closed or self.paused or self.waiting is not None:
            return
        if self.buffer is not None:
            data, self.buffer = self.buffer, None
            offset = self.position % self.piece_length
            count = min(len(data) - offset, self.stop - self.position)
            if count <= 0:
                self._failed(None)
                return
            self.position += count
            self.request.write(data[offset : offset + count])
        if self.position == self.stop:
            self.stopProducing()
            self.request.finish()
        elif not self.paused and not self.closed:
            # Defer even an immediate result: don't recurse on cached pieces.
            self.scheduled = reactor.callLater(0, self._read)

    def _read(self):
        if self.closed or self.paused or self.waiting is not None:
            return
        d = defer.maybeDeferred(
            self.core.adapter.read,
            self.torrent_id,
            self.position // self.piece_length,
            self.torrent,
        )
        self.waiting = d
        d.addCallback(self._ready)
        d.addErrback(self._failed)

    def _ready(self, data):
        self.waiting = None
        if not self.closed:
            self.buffer = data
            self._pump()

    def _failed(self, failure):
        self.waiting = None
        if not self.closed:
            log.warning("Research piece transfer failed: %s", failure)
            self.stopProducing()
            self.request.transport.abortConnection()

    def stopProducing(self):
        if self.closed:
            return
        self.closed = True
        if self.scheduled is not None and self.scheduled.active():
            self.scheduled.cancel()
        if self.waiting is not None:
            self.waiting.cancel()
        self.buffer = None
        self.request.unregisterProducer()
        if self.core.active is self:
            self.core.active = None
