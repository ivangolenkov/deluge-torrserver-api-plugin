"""Bounded LAN TorrServer HTTP surface, backed by the existing Deluge session."""

import io
import json
import mimetypes
from email.parser import BytesParser
from email.policy import default
from urllib.parse import quote, unquote_to_bytes, urlencode

from twisted.internet import defer
from twisted.web.http import HTTPChannel, parse_qs
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET, Request, Site

from .downloads import Downloads
from .ranges import InvalidRange, UnsatisfiableRange, resolve_range
from .stream_adapter import NativeStreams
from .streaming import Streams
from .torrents import Torrents

ERRORS = {
    "torrent_not_found": 404,
    "invalid_file_id": 400,
    "invalid_request": 400,
    "invalid_source": 400,
    "invalid_torrent": 400,
    "v2_magnet_unsupported": 400,
    "unknown_action": 400,
    "unsafe_paths": 409,
    "shared_paths": 409,
    "operation_conflict": 409,
    "metadata_pending": 409,
    "recovery_required": 409,
    "torrent_paused": 409,
    "session_paused": 409,
    "torrent_queued": 409,
    "file_skipped": 409,
    "disk_full": 507,
    "storage_error": 503,
    "source_error": 502,
    "source_redirect_limit": 400,
    "source_too_large": 413,
    "transfer_limit": 429,
    "source_limit": 429,
    "buffer_limit": 503,
    "deadline_limit": 503,
    "owner_not_configured": 503,
    "public_url_required": 503,
    "path_check_limit": 413,
    "disabled": 503,
    "piece_read_error": 503,
    "response_limit": 413,
    "ticket_invalid": 403,
    "file_not_ready": 409,
    "torrent_not_ready": 409,
}


def error_code(error):
    if isinstance(error, defer.TimeoutError | defer.CancelledError):
        return "timeout", 504
    code = str(error)
    if isinstance(error, ValueError) and code not in ERRORS:
        return "invalid_request", 400
    return (code, ERRORS[code]) if code in ERRORS else ("storage_error", 503)


def multipart(raw, content_type):
    # Parse bounded memory in the worker, never use client filename as a path.
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n"
    )
    boundary = message.get_boundary()
    if not boundary or len(boundary) > 200:
        raise ValueError("invalid_request")
    delimiter = b"--" + boundary.encode("ascii")
    if raw.count(b"\r\n" + delimiter) > 9 or not raw.startswith(delimiter + b"\r\n"):
        raise ValueError("invalid_request")
    parts = raw.split(b"\r\n" + delimiter)
    if not parts[-1].startswith(b"--"):
        raise ValueError("invalid_request")
    parts[0] = parts[0][len(delimiter) :]
    source, metadata = None, {}
    if len(parts) > 9:
        raise ValueError("invalid_request")
    for segment in parts[:-1]:
        headers, separator, value = segment.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not separator or len(headers) > 16384:
            raise ValueError("invalid_request")
        part = BytesParser(policy=default).parsebytes(headers + b"\r\n\r\n")
        if part.get_content_maintype() == "multipart" or part.get(
            "Content-Transfer-Encoding", "binary"
        ).lower() not in {"binary", "8bit", "7bit"}:
            raise ValueError("invalid_request")
        name = part.get_param("name", header="content-disposition")
        if name == "file":
            if source is not None:
                raise ValueError("invalid_request")
            source = value
        elif name == "poster":
            metadata["poster"] = value.decode("utf-8")
    if source is None:
        raise ValueError("invalid_request")
    return source, metadata


class LimitedRequest(Request):
    def requestReceived(self, command, path, version):
        # Do not invoke http.Request's automatic form/multipart decoder on the
        # reactor. This API accepts raw bounded JSON and worker-parsed multipart.
        self.method, self.uri, self.clientproto = command, path, version
        self.path, _, query = path.partition(b"?")
        self.args = parse_qs(query, 1)
        self.content.seek(0)
        self.process()

    def gotLength(self, length):
        self.content = io.BytesIO()
        self.received = 0
        self.rejected = False
        if length is not None and length > self.channel.factory.body_limit():
            self._reject()

    def _reject(self):
        if not self.rejected:
            self.rejected = True
            self.transport.write(
                b"HTTP/1.1 413 Content Too Large\r\nConnection: close\r\n"
                b"Access-Control-Allow-Origin: *\r\nContent-Length: 0\r\n\r\n"
            )
            self.transport.loseConnection()

    def handleContentChunk(self, data):
        if self.rejected:
            return
        site = self.channel.factory
        if (
            self.received + len(data) > site.body_limit()
            or site.input_bytes + len(data) > 32 * 1024**2
        ):
            self._reject()
            return
        self.received += len(data)
        site.input_bytes += len(data)
        self.content.write(data)

    def process(self):
        if not getattr(self, "rejected", False):
            super().process()

    def _release_input(self):
        if getattr(self, "received", 0):
            self.channel.factory.input_bytes -= self.received
            self.received = 0

    def finish(self):
        self._release_input()
        return super().finish()

    def connectionLost(self, reason):
        self._release_input()
        return super().connectionLost(reason)


class LimitedChannel(HTTPChannel):
    def dataReceived(self, data):
        if getattr(self, "_handlingRequest", False):
            queued = getattr(self, "_dataBuffer", ())
            size = len(getattr(self, "_buffer", b"")) + sum(map(len, queued))
            if len(queued) >= 256 or size + len(data) > 1024**2:
                self.transport.abortConnection()
                return
        return super().dataReceived(data)

    def connectionMade(self):
        super().connectionMade()
        self.factory.connections.add(self)
        if len(self.factory.connections) > 32:
            self.transport.abortConnection()

    def connectionLost(self, reason):
        self.factory.connections.discard(self)
        return super().connectionLost(reason)


class TorrServer:
    def __init__(self, reactor, policy):
        self.reactor, self.policy = reactor, policy
        self.streams = Streams(reactor, policy, NativeStreams(policy))
        self.torrents = Torrents(reactor, policy, self.streams)
        self.downloads = Downloads(self)
        self.listener = None
        self.site = None
        self.signature = None
        self.error = None
        self.configure_lock = defer.DeferredLock()
        self.active_requests = set()
        self.closed = False

    @defer.inlineCallbacks
    def start(self):
        yield self.policy.worker.submit(mimetypes.init)
        yield self.policy.worker.call("recover_source_adds")
        yield self.configure()

    def configure(self):
        return self.configure_lock.run(self._configure)

    @defer.inlineCallbacks
    def _configure(self):
        config = self.policy.config
        target = (
            config["http_enabled"],
            config["bind_host"],
            config["bind_port"],
            config["torrent_owner"],
        )
        if (target == self.signature and self.error is None) or self.closed:
            return
        for request in tuple(self.active_requests):
            request.transport.abortConnection()
        if self.site is not None:
            for channel in tuple(self.site.connections):
                channel.transport.abortConnection()
            self.site = None
        if self.listener is not None:
            listener, self.listener = self.listener, None
            yield listener.stopListening()
        self.signature = target
        self.error = None
        if not target[0]:
            return
        site = Site(API(self), requestFactory=LimitedRequest)
        self.site = site
        site.protocol = LimitedChannel
        site.connections = set()
        site.input_bytes = 0
        site.body_limit = lambda: min(
            self.policy.config["max_torrent_upload_mib"] * 1024**2, 16 * 1024**2
        )
        site.log = lambda _: None
        site.timeOut = self.policy.config["client_idle_timeout_seconds"]
        try:
            self.listener = self.reactor.listenTCP(target[2], site, interface=target[1])
        except Exception:
            self.error = "bind_failed"

    @defer.inlineCallbacks
    def close(self):
        self.closed = True
        self.downloads.close()
        disk_drained = self.streams.close()
        for request in tuple(self.active_requests):
            request.transport.abortConnection()
        if self.site is not None:
            for channel in tuple(self.site.connections):
                channel.transport.abortConnection()
            self.site = None
        yield self.torrents.close()
        yield disk_drained
        yield self.configure_lock.acquire()
        try:
            if self.listener is not None:
                listener, self.listener = self.listener, None
                yield listener.stopListening()
        finally:
            self.configure_lock.release()


class API(Resource):
    isLeaf = True

    def __init__(self, server):
        super().__init__()
        self.server = server

    def render(self, request):
        request.setHeader(b"Access-Control-Allow-Origin", b"*")
        request.setHeader(b"Access-Control-Allow-Methods", b"GET, HEAD, POST, OPTIONS")
        request.setHeader(
            b"Access-Control-Allow-Headers",
            b"Range, Content-Type, transferMode.dlna.org, getcontentFeatures.dlna.org",
        )
        request.setHeader(
            b"Access-Control-Expose-Headers",
            b"Content-Length, Content-Range, Accept-Ranges, contentFeatures.dlna.org",
        )
        if request.method == b"OPTIONS":
            return b""
        if request.method not in (b"GET", b"HEAD", b"POST"):
            request.setResponseCode(405)
            return b""
        if self.server.closed or len(self.server.active_requests) >= 32:
            request.setResponseCode(503)
            return b""
        self.server.active_requests.add(request)
        work = None

        def finished(result):
            self.server.active_requests.discard(request)
            if work is not None and not work.called:
                work.cancel()
            return None

        request.notifyFinish().addBoth(finished)
        # Admission/producer failure may finish synchronously inside dispatch.
        # Twisted does not replay notifyFinish for an already finished request.
        work = defer.maybeDeferred(self._dispatch, request)
        if (
            request.finished or getattr(request, "_disconnected", False)
        ) and not work.called:
            work.cancel()

        def success(result):
            if (
                result is NOT_DONE_YET
                or request.finished
                or getattr(request, "_disconnected", False)
            ):
                return
            content_type, body = result
            if len(body) > 8 * 1024**2:
                raise ValueError("response_limit")
            request.setHeader(b"Content-Type", content_type)
            request.setHeader(b"Content-Length", str(len(body)).encode())
            if request.method != b"HEAD":
                request.write(body)
            request.finish()

        def failed(failure):
            if not request.finished and not getattr(request, "_disconnected", False):
                code, status = error_code(failure.value)
                request.setResponseCode(status)
                success((b"application/json", json.dumps({"error": code}).encode()))
            return None

        work.addCallback(success)
        work.addErrback(failed)
        return NOT_DONE_YET

    @staticmethod
    def _json(value):
        return b"application/json; charset=utf-8", json.dumps(
            value, ensure_ascii=False, allow_nan=False
        ).encode()

    @defer.inlineCallbacks
    def _dispatch(self, request):
        s, torrents = self.server.policy, self.server.torrents
        route = request.path.decode("utf-8")
        if route.startswith("/download/"):
            from .download_http import serve

            return (yield serve(self.server, request, route.removeprefix("/download/")))
        actor = torrents.actor()
        # Lampa's settings availability check requests the base URL, whereas
        # its playback handshake uses /echo. Neither exposes torrent data.
        if route in ("/", "/echo") and request.method in (b"GET", b"HEAD"):
            return b"text/plain", b"MatriX.147.1"
        if route in ("/torrents", "/cache", "/settings", "/viewed", "/torrent/upload"):
            if request.method != b"POST":
                request.setResponseCode(405)
                return b"text/plain", b""
            limit = (
                s.config[
                    "max_torrent_upload_mib"
                    if route == "/torrent/upload"
                    else "max_json_body_mib"
                ]
                * 1024**2
            )
            raw = request.content.read(min(limit, 16 * 1024**2) + 1)
            if len(raw) > limit:
                raise ValueError("source_too_large")
            if route == "/torrent/upload":
                source, metadata = yield s.worker.submit(
                    multipart, raw, request.getHeader("Content-Type") or ""
                )
                return self._json((yield torrents.add(source, True, metadata)))
            try:
                data = yield s.worker.submit(json.loads, raw)
                if not isinstance(data, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid_request") from None
            action = data.get("action", "get" if route == "/settings" else "")
            if not isinstance(action, str):
                raise ValueError("invalid_request")
            if route == "/settings":
                if action not in {"", "get", "set", "def"}:
                    raise ValueError("unknown_action")
                return self._json(
                    {
                        "CacheSize": s.config["stream_window_mib"] * 1024**2,
                        "ReaderReadAHead": 100,
                        "PreloadCache": min(
                            100,
                            s.config["initial_prebuffer_mib"]
                            * 100
                            // s.config["stream_window_mib"],
                        ),
                        "TrackTimecode": True,
                        "UseDisk": True,
                        "RemoveCacheOnDrop": False,
                        "TorrentDisconnectTimeout": s.config["debounce_seconds"],
                        "ResponsiveMode": True,
                        "FriendlyName": "Deluge Stream",
                    }
                )
            if route == "/torrents":
                if action not in {"add", "get", "list", "set", "rem", "drop", "wipe"}:
                    raise ValueError("unknown_action")
                if action in {"drop", "wipe"}:
                    return self._json({"status": "ok"})
                if action == "list":
                    result = []
                    encoded_size = 2
                    for row in torrents.published():
                        item = yield torrents.dto(row)
                        encoded_size += len(self._json(item)[1]) + 1
                        if encoded_size > 8 * 1024**2:
                            raise ValueError("response_limit")
                        result.append(item)
                    return self._json(result)
                if action == "add":
                    metadata = {
                        key: data[key]
                        for key in ("title", "poster", "category", "data")
                        if key in data
                    }
                    if any(
                        not isinstance(value, str) or len(value.encode()) > 65536
                        for value in metadata.values()
                    ):
                        raise ValueError("invalid_request")
                    return self._json(
                        (
                            yield torrents.add(
                                data.get("link"),
                                data.get("save_to_db", False),
                                metadata,
                            )
                        )
                    )
                row = torrents.find(data.get("hash"))
                if action == "get":
                    return self._json((yield torrents.dto(row)))
                if action == "set":
                    yield torrents.metadata(
                        data.get("hash"),
                        {
                            key: data[key]
                            for key in ("title", "poster", "category", "data")
                            if key in data
                        },
                    )
                    return self._json(
                        (yield torrents.dto(torrents.find(data.get("hash"))))
                    )
                if action == "rem":
                    yield torrents.rem(data.get("hash"))
                    return self._json({"status": "ok"})
                raise ValueError("unknown_action")
            if route == "/cache" and action != "get":
                raise ValueError("unknown_action")
            if route == "/viewed" and action not in {"set", "list", "rem"}:
                raise ValueError("unknown_action")
            row = torrents.find(
                data.get("hash"), write=route == "/viewed" and action != "list"
            )
            if route == "/cache":
                if action != "get":
                    raise ValueError("unknown_action")
                file_id = data.get("index", 1)
                offset = data.get("offset", 0)
                capacity, filled, pieces = torrents.filled(row, file_id, offset)
                layout = torrents.mapping(row)
                external_ids = {
                    file.index: index for index, file in layout.payload().items()
                }
                readers = [
                    {
                        "Id": session.token,
                        "Pos": session.position - session.file.offset,
                        "FileIndex": external_ids[session.file.index],
                    }
                    for session in self.server.streams.sessions.values()
                    if session.rid == row["record_id"] and session.kind == "playback"
                ]
                return self._json(
                    {
                        "Hash": row["torrent_id"],
                        "Capacity": capacity,
                        "Filled": filled,
                        "PiecesLength": layout.piece_length,
                        "PiecesCount": layout.piece_count,
                        "WindowPiecesCount": len(pieces),
                        "Offset": offset,
                        "Pieces": pieces,
                        "Readers": readers,
                        "Torrent": (yield torrents.dto(row, file_id)),
                    }
                )
            if action not in {"set", "list", "rem"}:
                raise ValueError("unknown_action")
            if action == "list":
                result, after = [], 0
                while True:
                    page = yield s.worker.call(
                        "list_viewed", row["record_id"], after=after
                    )
                    result.extend(
                        {
                            "hash": row["torrent_id"],
                            "file_index": mark["file_id"],
                            "timecode": mark["timecode"],
                        }
                        for mark in page
                    )
                    if len(page) < 128:
                        break
                    after = page[-1]["file_id"]
                    if len(result) > 10000:
                        raise RuntimeError("path_check_limit")
                if torrents.find(row["torrent_id"])["record_id"] != row["record_id"]:
                    raise RuntimeError("operation_conflict")
                return self._json(result)
            file_id = data.get("file_index")
            if (
                type(file_id) is not int
                or file_id not in torrents.mapping(row).payload()
            ):
                raise ValueError("invalid_file_id")
            if action == "set":
                yield s.worker.call(
                    "set_viewed", row["record_id"], file_id, data.get("timecode", 0)
                )
            else:
                yield s.worker.call("remove_viewed", row["record_id"], file_id)
            if (
                torrents.find(row["torrent_id"], write=True)["record_id"]
                != row["record_id"]
            ):
                raise RuntimeError("operation_conflict")
            return self._json({"status": "ok"})
        if request.method not in (b"GET", b"HEAD"):
            raise ValueError("invalid_request")

        def arg(name, default=None):
            values = request.args.get(name.encode())
            return values[0].decode("utf-8") if values else default

        alias, raw_id = arg("link", arg("hash")), arg("index", "1")
        if route.startswith("/play/"):
            parts = route.split("/")
            if len(parts) != 4:
                raise ValueError("invalid_request")
            alias, raw_id = parts[2:]
        elif (
            route != "/playlist"
            and route != "/stream"
            and not route.startswith("/stream/")
        ):
            request.setResponseCode(404)
            return b"text/plain", b""
        row = torrents.find(alias)
        flags = {
            unquote_to_bytes(part.split(b"=", 1)[0])
            for part in request.uri.partition(b"?")[2].split(b"&")
        }
        if route == "/playlist" or b"m3u" in flags:
            base = s.config["public_base_url"].rstrip("/")
            if not base:
                raise RuntimeError("public_url_required")
            lines = ["#EXTM3U"]
            for i, file in torrents.mapping(row).payload().items():
                title = file.path.replace("\r", " ").replace("\n", " ")
                lines += [
                    f"#EXTINF:-1,{title}",
                    base
                    + "/stream/"
                    + quote(file.path, safe="")
                    + "?"
                    + urlencode({"link": row["torrent_id"], "index": i, "play": ""}),
                ]
            return b"audio/x-mpegurl", ("\n".join(lines) + "\n").encode()
        try:
            file_id = int(raw_id)
        except ValueError:
            raise ValueError("invalid_file_id") from None
        if (b"stat" in flags or b"preload" in flags) and not (
            request.method == b"GET" and b"preload" in flags
        ):
            _, info, _ = s.adapter.native_status(row["torrent_id"])
            if info is None:
                return self._json((yield torrents.dto(row, file_id)))
        layout = torrents.mapping(row)
        file = layout.payload().get(file_id)
        if file is None:
            raise ValueError("invalid_file_id")
        if request.method == b"GET" and b"preload" in flags:
            yield self.server.streams.preload(
                row["torrent_id"], row["record_id"], actor, file_id
            )
            return b"text/plain", b""
        if b"stat" in flags or b"preload" in flags:
            return self._json((yield torrents.dto(row, file_id)))
        try:
            start, stop, partial = resolve_range(
                request.getHeader("Range") if request.method == b"GET" else None,
                file.size,
            )
        except UnsatisfiableRange:
            request.setResponseCode(416)
            request.setHeader(b"Content-Range", f"bytes */{file.size}".encode())
            return b"text/plain", b""
        except InvalidRange:
            raise ValueError("invalid_request") from None
        request.setResponseCode(206 if partial else 200)
        request.setHeader(
            b"Content-Type",
            (mimetypes.guess_type(file.path)[0] or "application/octet-stream").encode(),
        )
        request.setHeader(b"Accept-Ranges", b"bytes")
        request.setHeader(b"Content-Length", str(stop - start).encode())
        request.setHeader(b"transferMode.dlna.org", b"Streaming")
        if request.getHeader("getcontentFeatures.dlna.org"):
            request.setHeader(
                b"contentFeatures.dlna.org",
                b"DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000",
            )
        if partial:
            request.setHeader(
                b"Content-Range", f"bytes {start}-{stop - 1}/{file.size}".encode()
            )
        if request.method == b"HEAD" or start == stop:
            request.finish()
            return NOT_DONE_YET
        producer = Playback(self.server, request, row, actor, file_id, start, stop)
        producer.start()
        return NOT_DONE_YET


class Playback:
    def __init__(self, server, request, row, actor, file_id, start, stop):
        self.server, self.request, self.row, self.actor = server, request, row, actor
        self.file_id, self.start_at, self.stop_at = file_id, start, stop
        self.session = None
        self.chunk = None
        self.waiting = None
        self.scheduled = None
        self.closed = False
        self.paused = False
        self.end_reason = None

    def start(self):
        self.request.notifyFinish().addBoth(lambda _: self.stopProducing())
        d = self.server.streams.open(
            self.row["torrent_id"],
            self.row["record_id"],
            self.actor,
            self.file_id,
            self.start_at,
            self.stop_at,
            cancel=self.failed,
        )
        self.waiting = d

        def admitted(session):
            self.waiting = None
            if self.closed:
                self.server.streams.stop(session)
                return
            self.session = session
            self.request.registerProducer(self, True)
            self.pump()

        d.addCallback(admitted)
        d.addErrback(self.failed)

    def pauseProducing(self):
        self.paused = True

    def resumeProducing(self):
        self.paused = False
        if self.scheduled is None:
            self.pump()

    def pump(self):
        self.scheduled = None
        if self.closed or self.paused or self.waiting is not None:
            return
        try:
            session = self.session
            self.server.streams.adapter.check(session)
            if self.chunk is not None:
                offset = session.position - self.chunk.start
                count = min(
                    65536,
                    len(self.chunk.data) - offset,
                    session.stop - session.position,
                )
                if count <= 0:
                    raise RuntimeError("piece_read_error")
                self.request.write(self.chunk.data[offset : offset + count])
                self.server.streams.advance(session, count)
                if (
                    offset + count == len(self.chunk.data)
                    or session.position == session.stop
                ):
                    self.chunk.release()
                    self.chunk = None
                if session.position == session.stop:
                    self.stopProducing()
                    self.request.finish()
                    return
                self.scheduled = self.server.reactor.callLater(0, self.pump)
            else:
                d = self.server.streams.read_next(session)
                self.waiting = d

                def ready(chunk):
                    self.waiting = None
                    if self.closed:
                        chunk.release()
                        return
                    self.chunk = chunk
                    self.pump()

                d.addCallback(ready)
                d.addErrback(self.failed)
        except (RuntimeError, ValueError, KeyError) as exc:
            self.failed(exc)

    def failed(self, failure):
        if self.closed:
            return None
        error = getattr(failure, "value", failure)
        code, status = error_code(error)
        self.end_reason = code
        started = self.request.startedWriting
        self.stopProducing()
        if not getattr(self.request, "_disconnected", False):
            if started:
                self.request.transport.abortConnection()
            else:
                body = json.dumps({"error": code}).encode()
                self.request.setResponseCode(status)
                self.request.responseHeaders.removeHeader(b"Content-Range")
                self.request.setHeader(b"Content-Type", b"application/json")
                self.request.setHeader(b"Content-Length", str(len(body)).encode())
                self.request.write(body)
                self.request.finish()
        return None

    def stopProducing(self):
        if self.closed:
            return None
        self.closed = True
        if self.scheduled is not None and self.scheduled.active():
            self.scheduled.cancel()
        if self.waiting is not None and not self.waiting.called:
            self.waiting.cancel()
        if self.chunk is not None:
            self.chunk.release()
            self.chunk = None
        if self.session is not None:
            reason = self.end_reason or (
                "cancelled" if self.session.position < self.session.stop else None
            )
            self.server.streams.stop(self.session, reason)
        self.request.unregisterProducer()
        return None
