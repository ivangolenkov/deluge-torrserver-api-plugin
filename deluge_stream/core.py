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

from . import API_VERSION, __version__
from .adapter import PieceAdapter, deregister_rpc
from .lifecycle import PolicyService
from .policy_adapter import PolicyAdapter, check_runtime
from .ranges import InvalidRange, UnsatisfiableRange, resolve_range
from .torrserver import TorrServer

log = logging.getLogger(__name__)


class Core(CorePluginBase):
    @defer.inlineCallbacks
    def enable(self):
        self._closing = None
        self._close_done = False
        self._close_result = None
        self._close_waiters = []
        self._daemon_stopping = False
        self.policy = None
        self.listener = None
        self.adapter = None
        self.server = None
        self._shutdown_id = reactor.addSystemEventTrigger(
            "before", "shutdown", self._shutdown
        )
        self.token = secrets.token_hex(32)
        self.active = None
        try:
            check_runtime()
            self.policy = PolicyService(reactor, PolicyAdapter)
            yield self.policy.open()
            if self._closing is not None:
                raise RuntimeError("operation_conflict")
            self.adapter = PieceAdapter(reactor)
            site = Site(Probe(self))
            site.log = lambda _: None  # No query strings/tokens in access logs.
            self.listener = reactor.listenTCP(0, site, interface="127.0.0.1")
            self.server = TorrServer(reactor, self.policy)
            self.policy.streaming = self.server.streams
            self.policy.research_active = lambda: self.active is not None
            yield self.server.start()
        except Exception:
            yield self._close(reset=False)
            raise

    def disable(self):
        reset = not (
            self._daemon_stopping
            or (self.policy is not None and self.policy.adapter.daemon_stopping())
        )
        return self._close(reset=reset)

    def stop(self):
        # Component shutdown is not a user's explicit plugin disable.
        self._daemon_stopping = True
        return self._close(reset=False)

    def _shutdown(self):
        # Deluge 2.2.0's PluginManager.stop doesn't await disable_plugins(), and
        # auto-enabled plugin Components can still be Stopped. Our own shutdown
        # trigger must retain the reactor until SQLite drain/join completes.
        self._daemon_stopping = True
        return self._close(reset=False)

    def _close(self, *, reset):
        observer = defer.Deferred()
        if self._close_done:
            observer.callback(self._close_result)
            return observer
        self._close_waiters.append(observer)
        if self._closing is None:
            # Deluge can call disable() and component.stop() concurrently during
            # daemon shutdown. Each must receive its own Deferred chain.
            self._closing = True
            self._do_close(reset=reset).addBoth(self._closed)
        return observer

    def _closed(self, result):
        self._close_done = True
        self._close_result = result
        if self._shutdown_id is not None:
            try:
                reactor.removeSystemEventTrigger(self._shutdown_id)
            except ValueError:
                pass
            self._shutdown_id = None
        waiters, self._close_waiters = self._close_waiters, []
        for waiter in waiters:
            if not waiter.called:
                waiter.callback(result)
        return None

    @defer.inlineCallbacks
    def _do_close(self, *, reset):
        if self.policy is not None:
            self.policy.quiescing = True
            self.policy.retention.stop()
        if self.active is not None:
            request = self.active.request
            self.active.stopProducing()
            request.transport.abortConnection()
        if self.server is not None:
            yield self.server.close()
        if self.adapter is not None:
            self.adapter.close()
        self.token = None
        deregister_rpc(self)
        try:
            if self.listener is not None:
                yield self.listener.stopListening()
        finally:
            if self.policy is not None:
                yield self.policy.close(reset=reset)

    def update(self):
        if self.policy is not None:
            self.policy.update()

    def __del__(self):
        try:
            deregister_rpc(self)
        except KeyError:
            pass

    @staticmethod
    def _version(api_version):
        if type(api_version) is not int or api_version != API_VERSION:
            raise ValueError("incompatible_api")

    @staticmethod
    def _public(work):
        def failed(failure):
            code = str(failure.value)
            if code not in {
                "invalid_config",
                "invalid_owner",
                "invalid_request",
                "forbidden",
                "operation_conflict",
                "recovery_required",
                "torrent_not_found",
                "metadata_pending",
                "unsafe_paths",
                "shared_paths",
                "catalogue_limit",
                "path_check_limit",
                "transfer_limit",
                "file_not_ready",
                "torrent_not_ready",
                "public_url_required",
                "invalid_file_id",
                "ticket_invalid",
            }:
                code = "storage_error"
            raise RuntimeError(code) from None

        return work.addErrback(failed)

    @export(5)
    def get_status(self, api_version, torrent_ids):
        self._version(api_version)
        return self.policy.get_status(torrent_ids, self.policy.adapter.actor())

    @export(5)
    def set_retention(self, api_version, torrent_id, record_id, temporary):
        self._version(api_version)
        if type(temporary) is not bool:
            raise ValueError("invalid_request")
        return self._public(
            self.policy.mutate(
                torrent_id, record_id, self.policy.adapter.actor(), temporary=temporary
            )
        )

    @export(5)
    def set_published(self, api_version, torrent_id, record_id, published):
        self._version(api_version)
        if type(published) is not bool:
            raise ValueError("invalid_request")
        return self._public(
            self.policy.mutate(
                torrent_id, record_id, self.policy.adapter.actor(), published=published
            )
        )

    @export(5)
    def publish_existing(self, api_version, hash_alias, save_to_db):
        """Existing-add policy primitive. Does not create a native torrent."""
        self._version(api_version)
        return self._public(
            self.policy.publish_existing(
                hash_alias, save_to_db, self.policy.adapter.actor()
            )
        )

    @export(10)
    def get_config(self, api_version):
        self._version(api_version)
        if not self.policy.adapter.actor()[1]:
            raise ValueError("forbidden")
        return dict(self.policy.config)

    @export(5)
    def create_download_ticket(
        self, api_version, torrent_id, record_id, kind, file_id=1
    ):
        self._version(api_version)
        return self._public(
            self.server.downloads.create(
                torrent_id, record_id, self.policy.adapter.actor(), kind, file_id
            )
        )

    @export(5)
    def get_download_info(
        self, api_version, torrent_id, record_id, offset=0, native_index=None
    ):
        self._version(api_version)
        return self._public(
            self.server.downloads.info(
                torrent_id, record_id, self.policy.adapter.actor(), offset, native_index
            )
        )

    @export(10)
    def set_config(self, api_version, changes):
        self._version(api_version)
        return self._public(self._configure(changes, self.policy.adapter.actor()))

    @defer.inlineCallbacks
    def _configure(self, changes, actor):
        result = yield self.policy.set_config(changes, actor)
        if self.server is not None:
            yield self.server.configure()
        return result

    @export(10)
    def confirm_recovery(self, api_version, catalogue_token):
        self._version(api_version)
        return self._public(
            self.policy.confirm_recovery(catalogue_token, self.policy.adapter.actor())
        )

    @export(10)
    def get_cleanup(self, api_version):
        self._version(api_version)
        if not self.policy.adapter.actor()[1]:
            raise ValueError("forbidden")
        return self.policy.retention.status()

    @export(10)
    def resolve_removal(self, api_version, operation_id, action):
        self._version(api_version)
        return self._public(
            self.policy.resolve_removal(
                operation_id, action, self.policy.adapter.actor()
            )
        )

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

    @export(5)
    def get_diagnostics(self, api_version):
        """Authenticated capability handshake; no torrents, paths or probe key."""
        if type(api_version) is not int or api_version != API_VERSION:
            raise ValueError("incompatible_api")
        return {
            "api_version": API_VERSION,
            "plugin_version": __version__,
            "deluge_version": get_version(),
            "python_version": sys.version.split()[0],
            "storage_active": self.policy is not None and self.policy.ready,
            "torrserver_active": bool(
                self.server
                and self.server.listener
                and self.policy.config["torrent_owner"]
            ),
            "policy_rpc_available": self.policy is not None and self.policy.ready,
            "error": self.policy.error if self.policy is not None else "storage_error",
            "recovery_required": self.policy.recovery_required if self.policy else True,
            "recovery_token": (
                self.policy.recovery_token
                if self.policy and self.policy.adapter.actor()[1]
                else None
            ),
            "streaming": self.server.streams.diagnostics() if self.server else {},
            "downloads": {
                "tickets": len(self.server.downloads.tickets),
                "preflights": self.server.downloads.preflights,
                "metadata_bytes_reserved": self.server.downloads.metadata_reserved,
            }
            if self.server
            else {},
            "http_error": self.server.error if self.server else None,
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
        if request.method != b"HEAD" and self.core.server is not None:
            streams = self.core.server.streams
            budget = self.core.policy.config["payload_buffer_budget_mib"] * 1024**2
            if (
                streams.sessions
                or streams.reserved
                or piece_length * 2 + streams.HTTP_OVERHEAD > budget
            ):
                request.setResponseCode(503)
                return b"probe buffer unavailable"
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
        if not self.core.policy.ready or torrent_id not in self.core.policy.rows:
            request.setResponseCode(503)
            request.setHeader(b"Content-Length", b"0")
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
        request.notifyFinish().addBoth(lambda _: producer.stopProducing())
        rid = self.core.policy.rows.get(torrent_id, {}).get("record_id", "")
        admission = self.core.policy.acquire_transfer(
            torrent_id,
            rid,
            ("research-probe", True),
            "playback",
            lambda reason: producer._failed(reason),
        )

        def admitted(lease):
            if producer.closed:
                lease.release()
                return
            producer.lease = lease
            current = self.core.adapter.file_spec(torrent_id, file_id)[0]
            if current is not torrent:
                raise RuntimeError("operation_conflict")
            request.registerProducer(producer, True)
            producer.resumeProducing()

        admission.addCallback(admitted)
        admission.addErrback(producer._failed)
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
        self.lease = None

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
            if self.lease is not None:
                self.lease.progress(count)
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
        if self.lease is not None:
            self.lease.release()
        if self.scheduled is not None and self.scheduled.active():
            self.scheduled.cancel()
        if self.waiting is not None:
            self.waiting.cancel()
        self.buffer = None
        self.request.unregisterProducer()
        if self.core.active is self:
            self.core.active = None
