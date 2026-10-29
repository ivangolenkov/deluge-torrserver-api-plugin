"""Ephemeral bounded download capabilities; no payload or password in RPC."""

import os
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode

from twisted.internet import defer, task


def archive_names(files, maximum):
    if len(files) > min(maximum, 100000):
        raise ValueError("path_check_limit")
    result, seen, directories, size = [], set(), set(), 0
    for file in files:
        name = unicodedata.normalize("NFC", file.path.replace("\\", "/"))
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or ":" in name.split("/")[0]
            or any(ord(char) < 32 for char in name)
            or len(name.encode()) > 4096
            or len(path.parts) > 32
        ):
            raise ValueError("unsafe_paths")
        if name in seen:
            raise ValueError("unsafe_paths")
        seen.add(name)
        size += len(name.encode()) + 256
        for parent in path.parents:
            directory = str(parent)
            if directory != "." and directory not in directories:
                directories.add(directory)
                size += len(directory.encode()) + 256
        if len(seen) + len(directories) > min(maximum, 100000):
            raise ValueError("path_check_limit")
        if size > 32 * 1024**2:
            raise ValueError("path_check_limit")
        result.append(name)
    ordered = sorted(seen)
    if any(b.startswith(a + "/") for a, b in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("unsafe_paths")
    return tuple(result)


def metadata_charge(layout, files):
    # Include retained paths, framing records and derived directory names.
    return (len(layout.files) + len(files)) * 256 + sum(
        len(file.path.encode()) * (4 + 2 * len(PurePosixPath(file.path).parts))
        for file in layout.files
    )


def fingerprints(root, files):
    """Worker-only: reject symlink components and record external modification."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("unsafe_paths")
    result = []
    for file in files:
        path = root / file.path
        current = Path(path.anchor)
        try:
            for part in path.parts[1:]:
                if part == "..":
                    raise ValueError("unsafe_paths")
                current /= part
                try:
                    value = os.lstat(current)
                except FileNotFoundError:
                    if file.size == 0:
                        value = None
                        break
                    raise
                if stat.S_ISLNK(value.st_mode):
                    raise ValueError("unsafe_paths")
            if value is None:
                result.append(None)
            else:
                if not stat.S_ISREG(value.st_mode) or value.st_size < file.size:
                    raise RuntimeError("storage_error")
                result.append(
                    (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
                )
        except OSError:
            raise RuntimeError("storage_error") from None
    return tuple(result)


@dataclass
class Ticket:
    token: str
    tid: str
    rid: str
    actor: tuple
    kind: str
    file_id: int
    native: object
    layout: object
    files: tuple
    names: tuple
    root: str
    identities: tuple
    reservation: object
    expires: float
    timer: object = None
    active: bool = False
    uses: int = 0
    metadata_charge: int = 0
    retired: bool = False


class Downloads:
    CAPACITY = 128
    MAX_USES = 16
    METADATA_BUDGET = 64 * 1024**2

    def __init__(self, server):
        self.server = server
        self.policy, self.reactor = server.policy, server.reactor
        self.adapter = server.streams.adapter
        self.tickets = {}
        self.closed = False
        self.preflights = 0
        self.metadata_reserved = 0

    def _bounded(self, method, *args):
        if self.closed or self.preflights >= 4:
            return defer.fail(RuntimeError("transfer_limit"))
        self.preflights += 1
        work = defer.maybeDeferred(method, *args)
        work.addTimeout(
            min(60, self.policy.config["metadata_timeout_seconds"]), self.reactor
        )

        def done(result):
            self.preflights -= 1
            return result

        return work.addBoth(done)

    def info(self, tid, rid, actor, offset=0, native_index=None):
        if (
            type(offset) is not int
            or offset < 0
            or (native_index is not None and type(native_index) is not int)
        ):
            return defer.fail(ValueError("invalid_request"))
        return self._bounded(self._info, tid, rid, actor, offset, native_index)

    @defer.inlineCallbacks
    def _info(self, tid, rid, actor, offset, native_index):
        if not self.policy.adapter.download_access(tid, actor):
            raise ValueError("torrent_not_found")
        row = self.policy._record(tid, rid, actor)
        native, layout, _, _ = yield self.adapter.download_open(tid, 1, self.reactor)
        base = self.policy.config["public_base_url"].rstrip("/")
        published = False
        if row["published"] and base:
            try:
                self.server.torrents.find(tid)
                published = True
            except (RuntimeError, ValueError):
                pass
        result, checks, zip_ready = [], 0, True
        for file_id, file in layout.payload().items():
            ready = True
            for piece in layout.pieces(file):
                have = native.handle.have_piece(piece)
                checks += 1
                if checks % 256 == 0:
                    yield task.deferLater(self.reactor, 0, lambda: None)
                    self.policy._record(tid, rid, actor)
                if not have:
                    ready = False
                    break
            if file_id % 256 == 0:
                yield task.deferLater(self.reactor, 0, lambda: None)
                self.policy._record(tid, rid, actor)
            zip_ready = zip_ready and ready
            if not (
                file.index == native_index
                if native_index is not None
                else offset < file_id <= offset + 128
            ):
                continue
            result.append(
                {
                    "id": file_id,
                    "index": file.index,
                    "path": file.path,
                    "size": file.size,
                    "ready": ready,
                    "play_url": base
                    + "/stream?"
                    + urlencode({"link": tid, "index": file_id, "play": ""})
                    if published
                    else None,
                }
            )
        self.policy._record(tid, rid, actor)
        if self.adapter.manager.torrents.get(tid) is not native:
            raise RuntimeError("operation_conflict")
        if not self.policy.adapter.download_access(tid, actor):
            raise ValueError("torrent_not_found")
        total = len(layout.payload())
        return {
            "files": result,
            "zip_ready": zip_ready,
            "total": total,
            "next_offset": offset + 128 if offset + 128 < total else None,
        }

    def ready(self, tid, rid, actor, kind, file_id):
        return self._bounded(self._ready, tid, rid, actor, kind, file_id)

    @defer.inlineCallbacks
    def _ready(self, tid, rid, actor, kind, file_id):
        if not self.policy.adapter.download_access(tid, actor):
            raise ValueError("torrent_not_found")
        self.policy._record(tid, rid, actor)
        native, layout, file, root = yield self.adapter.download_open(
            tid, file_id, self.reactor
        )
        files = tuple(layout.payload().values()) if kind == "zip" else (file,)
        names = yield self.policy.worker.submit(
            archive_names, files, self.policy.config["max_zip_entries"]
        )
        # Yield bounded batches; never walk a huge torrent on one reactor turn.
        checks = 0
        for item in files:
            for piece in layout.pieces(item):
                if not native.handle.have_piece(piece):
                    raise RuntimeError(
                        "torrent_not_ready" if kind == "zip" else "file_not_ready"
                    )
                checks += 1
                if checks % 256 == 0:
                    yield task.deferLater(self.reactor, 0, lambda: None)
                    self.policy._record(tid, rid, actor)
        identities = yield self.policy.worker.submit(fingerprints, root, files)
        self.policy._record(tid, rid, actor)
        current, mapping, _, location = yield self.adapter.download_open(
            tid, file_id, self.reactor
        )
        self.policy._record(tid, rid, actor)
        if not self.policy.adapter.download_access(tid, actor):
            raise ValueError("torrent_not_found")
        if current is not native or mapping != layout or location != root:
            raise RuntimeError("operation_conflict")
        status = native.handle.status()
        if native.forcing_recheck or status.moving_storage or status.state in (1, 7):
            raise RuntimeError("operation_conflict")
        return native, layout, files, names, root, identities

    @defer.inlineCallbacks
    def create(self, tid, rid, actor, kind, file_id=1):
        if self.closed or len(self.tickets) >= self.CAPACITY:
            raise RuntimeError("transfer_limit")
        if kind not in {"file", "zip"} or type(file_id) is not int or file_id < 1:
            raise ValueError("invalid_request")
        if kind == "zip":
            file_id = 1
        base = self.policy.config["public_base_url"].rstrip("/")
        if not base or self.server.listener is None or self.server.error:
            raise RuntimeError("public_url_required")
        # Reservation precedes worker/yield points; cleanup cannot race preflight.
        lease = yield self.policy.acquire_transfer(tid, rid, actor, "ticket")
        try:
            values = yield self.ready(tid, rid, actor, kind, file_id)
            if lease.closed or self.closed or len(self.tickets) >= self.CAPACITY:
                raise RuntimeError("operation_conflict")
            token = secrets.token_urlsafe(32)
            ttl = self.policy.config["download_ticket_start_ttl_seconds"]
            ticket = Ticket(
                token,
                tid,
                rid,
                actor,
                kind,
                file_id,
                *values,
                lease,
                self.reactor.seconds() + ttl,
            )
            ticket.metadata_charge = yield self.policy.worker.submit(
                metadata_charge, ticket.layout, ticket.files
            )
            if lease.closed or self.closed or len(self.tickets) >= self.CAPACITY:
                raise RuntimeError("operation_conflict")
            self.policy._record(tid, rid, actor)
            if not self.policy.adapter.download_access(tid, actor):
                raise ValueError("torrent_not_found")
            if self.metadata_reserved + ticket.metadata_charge > self.METADATA_BUDGET:
                raise RuntimeError("transfer_limit")
            ttl = self.policy.config["download_ticket_start_ttl_seconds"]
            lease.issue()
            ticket.expires = self.reactor.seconds() + ttl
            self.metadata_reserved += ticket.metadata_charge
            self.tickets[token] = ticket
            ticket.timer = self.reactor.callLater(ttl, self.expire, token)
            return {"url": base + "/download/" + quote(token), "expires_in": ttl}
        except BaseException:
            lease.release()
            raise

    def get(self, token):
        ticket = self.tickets.get(token)
        if self.closed or ticket is None or self.reactor.seconds() >= ticket.expires:
            raise ValueError("ticket_invalid")
        self.policy._record(ticket.tid, ticket.rid, ticket.actor)
        if not self.policy.adapter.download_access(ticket.tid, ticket.actor):
            raise ValueError("ticket_invalid")
        return ticket

    @defer.inlineCallbacks
    def validate(self, ticket):
        values = yield self.ready(
            ticket.tid, ticket.rid, ticket.actor, ticket.kind, ticket.file_id
        )
        if values != (
            ticket.native,
            ticket.layout,
            ticket.files,
            ticket.names,
            ticket.root,
            ticket.identities,
        ):
            raise RuntimeError("operation_conflict")

    @defer.inlineCallbacks
    def start(self, ticket, start, stop, cancel):
        if ticket.active or ticket.uses >= self.MAX_USES:
            raise RuntimeError("transfer_limit")
        ticket.active = True
        session = None
        try:
            yield self.validate(ticket)
            self.get(ticket.token)
            lease = ticket.reservation
            if lease.closed:
                lease = yield self.policy.acquire_transfer(
                    ticket.tid, ticket.rid, ticket.actor, "ticket"
                )
                ticket.reservation = lease
            session = yield self.server.streams.open(
                ticket.tid,
                ticket.rid,
                ticket.actor,
                ticket.file_id,
                start,
                stop,
                kind=ticket.kind,
                public=False,
                reservation=lease,
                cancel=cancel,
                opened=(ticket.native, ticket.layout, ticket.files[0], ticket.root),
            )
            if session.native is not ticket.native:
                raise RuntimeError("operation_conflict")
            ticket.uses += 1
            return session
        except BaseException:
            if session is not None:
                self.server.streams.stop(session)
            ticket.reservation.release()
            self.finished(ticket)
            raise

    def expire(self, token):
        ticket = self.tickets.pop(token, None)
        if ticket is not None:
            ticket.retired = True
            if ticket.timer.active():
                ticket.timer.cancel()
            if not ticket.active:
                ticket.reservation.release()
                self.finished(ticket)

    def finished(self, ticket):
        ticket.active = False
        if ticket.retired:
            self.metadata_reserved -= ticket.metadata_charge
            ticket.metadata_charge = 0

    def close(self):
        self.closed = True
        for token in tuple(self.tickets):
            self.expire(token)
