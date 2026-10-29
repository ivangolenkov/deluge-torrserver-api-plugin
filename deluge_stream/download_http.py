"""Direct core HTTP downloads: no Web UI proxy, no scheduling missing data."""

import json
import mimetypes
from pathlib import PurePosixPath
from urllib.parse import quote

from twisted.internet import defer
from twisted.web.server import NOT_DONE_YET

from .archive import Archive
from .downloads import fingerprints
from .ranges import InvalidRange, UnsatisfiableRange, resolve_range

VERIFY = object()


def disposition(name):
    name = PurePosixPath(name.replace("\\", "/")).name
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)
    name = name or "download"
    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in '\\"' else "_" for char in name
    )
    return (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
    ).encode()


@defer.inlineCallbacks
def serve(server, request, token):
    if request.method not in (b"GET", b"HEAD"):
        request.setResponseCode(405)
        return b"text/plain", b""
    ticket = server.downloads.get(token)
    yield server.downloads.validate(ticket)
    server.downloads.get(token)
    size = ticket.files[0].size
    if ticket.kind == "file":
        try:
            start, stop, partial = resolve_range(
                request.getHeader("Range") if request.method == b"GET" else None, size
            )
        except UnsatisfiableRange:
            request.setResponseCode(416)
            request.setHeader(b"Content-Range", f"bytes */{size}".encode())
            return b"text/plain", b""
        except InvalidRange:
            raise ValueError("invalid_request") from None
        request.setResponseCode(206 if partial else 200)
        request.setHeader(b"Accept-Ranges", b"bytes")
        request.setHeader(b"Content-Length", str(stop - start).encode())
        request.setHeader(b"Content-Disposition", disposition(ticket.files[0].path))
        request.setHeader(
            b"Content-Type",
            (
                mimetypes.guess_type(ticket.files[0].path)[0]
                or "application/octet-stream"
            ).encode(),
        )
        if partial:
            request.setHeader(
                b"Content-Range", f"bytes {start}-{stop - 1}/{size}".encode()
            )
    else:
        start, stop = 0, size
        request.setHeader(b"Accept-Ranges", b"none")
        request.setHeader(b"Content-Type", b"application/zip")
        info = ticket.native.handle.torrent_file()
        request.setHeader(b"Content-Disposition", disposition(info.name() + ".zip"))
    request.setHeader(b"Cache-Control", b"no-store")
    request.setHeader(b"Referrer-Policy", b"no-referrer")
    request.setHeader(b"X-Content-Type-Options", b"nosniff")
    if request.method == b"HEAD":
        request.finish()
        return NOT_DONE_YET
    producer = Download(server, request, ticket)
    yield producer.start(start, stop)
    return NOT_DONE_YET


class Download:
    def __init__(self, server, request, ticket):
        self.server, self.request, self.ticket = server, request, ticket
        self.session = None
        self.chunk = None
        self.waiting = None
        self.scheduled = None
        self.paused = False
        self.closed = False
        self.completed = False
        self.end_reason = None
        self.frames = None
        self.file_number = 0
        self.header_charge = 0

    @defer.inlineCallbacks
    def start(self, start, stop):
        self.request.notifyFinish().addBoth(lambda _: self.stopProducing())
        session = yield self.server.downloads.start(
            self.ticket, start, stop, self.failed
        )
        if self.closed:
            self.server.streams.stop(session)
            self.server.downloads.finished(self.ticket)
            return
        self.session = session
        charge = self.server.streams.HTTP_OVERHEAD
        if (
            self.server.streams.reserved + charge
            > self.server.policy.config["payload_buffer_budget_mib"] * 1024**2
        ):
            self.failed(RuntimeError("buffer_limit"))
            return
        self.header_charge = charge
        self.server.streams.reserved += charge
        self.frames = self._frames()
        self.request.registerProducer(self, True)
        self.pump()

    def _frames(self):
        session = self.session
        if self.ticket.kind == "file":
            while session.position < session.stop:
                yield None
            return
        archive = Archive()
        directories = set()
        for number, (file, name) in enumerate(
            zip(self.ticket.files, self.ticket.names, strict=True)
        ):
            self.file_number = number
            session.file = file
            session.position, session.stop = file.offset, file.offset + file.size
            for parent in reversed(PurePosixPath(name).parents):
                if str(parent) != "." and str(parent) not in directories:
                    directories.add(str(parent))
                    yield archive.begin(str(parent) + "/", 0)
                    yield archive.end()
            yield archive.begin(name, file.size)
            self.archive = archive
            while session.position < session.stop:
                yield None
            yield archive.end()
        yield VERIFY
        yield from archive.finish()

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
                offset = session.position % session.layout.piece_length
                count = min(
                    65536,
                    len(self.chunk.data) - offset,
                    session.stop - session.position,
                )
                if count <= 0:
                    raise RuntimeError("piece_read_error")
                block = self.chunk.data[offset : offset + count]
                if self.ticket.kind == "zip":
                    block = self.archive.data(block)
                self.request.write(block)
                self.server.streams.advance(session, count)
                if (
                    session.position % session.layout.piece_length == 0
                    or session.position == session.stop
                ):
                    self.chunk.release()
                    self.chunk = None
            else:
                try:
                    frame = next(self.frames)
                except StopIteration:
                    self.completed = True
                    self.stopProducing()
                    self.request.finish()
                    return
                if frame is VERIFY:
                    self.waiting = self._verify_all()
                    self.waiting.addCallback(self._verified)
                    self.waiting.addErrback(self.failed)
                    return
                if frame is not None:
                    self.request.write(frame)
                    session.lease.progress(len(frame))
                else:
                    d = self._read()
                    self.waiting = d

                    def ready(chunk):
                        self.waiting = None
                        if self.closed:
                            chunk.release()
                        else:
                            self.chunk = chunk
                            self.pump()

                    d.addCallback(ready)
                    d.addErrback(self.failed)
                    return
            if not self.closed:
                self.scheduled = self.server.reactor.callLater(0, self.pump)
        except Exception as exc:
            self.failed(exc)

    @defer.inlineCallbacks
    def _read(self):
        file = self.session.file
        identities = yield self.server.policy.worker.submit(
            fingerprints, self.ticket.root, (file,)
        )
        if identities != (self.ticket.identities[self.file_number],) or self.closed:
            raise RuntimeError("operation_conflict")
        chunk = yield self.server.streams.read(
            self.session, self.session.position // self.session.layout.piece_length
        )
        try:
            identities = yield self.server.policy.worker.submit(
                fingerprints, self.ticket.root, (file,)
            )
            if identities != (self.ticket.identities[self.file_number],) or self.closed:
                raise RuntimeError("operation_conflict")
        except BaseException:
            chunk.release()
            raise
        return chunk

    @defer.inlineCallbacks
    def _verify_all(self):
        identities = yield self.server.policy.worker.submit(
            fingerprints, self.ticket.root, self.ticket.files
        )
        if identities != self.ticket.identities or self.closed:
            raise RuntimeError("operation_conflict")

    def _verified(self, result):
        self.waiting = None
        self.pump()

    def failed(self, failure):
        from .torrserver import error_code

        if self.closed:
            return None
        code, status = error_code(getattr(failure, "value", failure))
        self.end_reason = code
        self.server.streams.last_error = code
        self.stopProducing()
        if not getattr(self.request, "_disconnected", False):
            if self.request.startedWriting:
                self.request.transport.abortConnection()
            else:
                body = json.dumps({"error": code}).encode()
                self.request.setResponseCode(status)
                self.request.responseHeaders.removeHeader(b"Content-Range")
                self.request.responseHeaders.removeHeader(b"Content-Disposition")
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
        if self.frames is not None:
            self.frames.close()
            self.frames = None
        self.archive = None
        self.server.streams.reserved -= self.header_charge
        self.header_charge = 0
        if self.session is not None:
            self.server.streams.stop(
                self.session,
                None if self.completed else self.end_reason or "cancelled",
            )
            self.server.downloads.finished(self.ticket)
        self.request.unregisterProducer()
        return None
