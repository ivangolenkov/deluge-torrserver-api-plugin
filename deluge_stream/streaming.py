"""Bounded multi-client pieces, aggregated deadlines, preload and cancellation.

Native piece priority side effects are deliberately left to Deluge/libtorrent.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field

from twisted.internet import defer, task

log = logging.getLogger(__name__)


def safe_reason(error):
    value = str(error)
    return value if re.fullmatch("[a-z_]{1,64}", value) else "stream_error"


def window(file, position, stop, piece_length, ahead):
    if not file.size or position >= stop:
        return range(0)
    return range(
        position // piece_length, (min(stop, position + ahead) - 1) // piece_length + 1
    )


@dataclass(eq=False)
class Session:
    tid: str
    rid: str
    native: object
    layout: object
    file: object
    path: str
    actor: tuple
    public: bool
    kind: str
    position: int
    stop: int
    cancel: object
    token: str = field(default_factory=lambda: str(uuid.uuid4()))
    lease: object = None
    closed: bool = False
    tails: tuple = ()
    pending: object = None
    ready_wait: object = None
    started: float = 0
    sent: int = 0
    first_byte: object = None


class Chunk:
    def __init__(self, engine, data, charge):
        self.engine, self.data, self.charge = engine, data, charge

    def release(self):
        if self.charge:
            self.engine.reserved -= self.charge
            self.charge = 0
            self.data = b""


class Streams:
    HTTP_OVERHEAD = 128 * 1024  # Two 64-KiB writes around transport high-water.
    MAX_JOBS = 16
    MAX_DEADLINES = 4096
    MAX_SESSIONS = 128

    def __init__(self, reactor, policy, adapter):
        self.reactor, self.policy, self.adapter = reactor, policy, adapter
        self.sessions = {}
        self.jobs = {}
        self.owned = {}
        self.preloads = {}
        self.reserved = 0
        self.timer = None
        self.closed = False
        self.last_error = ""
        self.lag = 0
        self.due = reactor.seconds()
        adapter.invalidate = self._invalidated
        adapter.register(self._read_alert)

    def _invalidated(self, alert):
        # Event/alert delivery is synchronous. Match the native handle, never
        # cancel a replacement simply because it has the same torrent hash.
        for session in tuple(self.sessions.values()):
            if self.adapter.matches(session.native, alert):
                self.stop(session, "operation_conflict")

    @defer.inlineCallbacks
    def open(
        self,
        tid,
        rid,
        actor,
        file_id,
        start,
        stop,
        kind="playback",
        cancel=None,
        public=True,
        reservation=None,
        opened=None,
    ):
        if self.closed or len(self.sessions) >= self.MAX_SESSIONS:
            raise RuntimeError("transfer_limit")
        if getattr(self.policy, "research_active", lambda: False)():
            raise RuntimeError("transfer_limit")
        native, layout, file, path = opened or self.adapter.open(tid, file_id)
        if not 0 <= start <= stop <= file.size:
            raise ValueError("invalid_range")
        if (
            layout.piece_length * 2 + self.HTTP_OVERHEAD
            > self.policy.config["payload_buffer_budget_mib"] * 1024**2
        ):
            raise RuntimeError("buffer_limit")
        session = Session(
            tid,
            rid,
            native,
            layout,
            file,
            path,
            actor,
            public,
            kind,
            file.offset + start,
            file.offset + stop,
            cancel,
        )
        session.started = self.reactor.seconds()
        if reservation is None:
            lease = yield self.policy.acquire_transfer(
                tid, rid, actor, kind, lambda reason: self.stop(session, reason)
            )
        else:
            lease = yield self.policy.activate_ticket(
                tid, rid, actor, reservation, kind
            )
            lease.cancel = lambda reason: self.stop(session, reason)
        session.lease = lease
        try:
            if (
                self.closed
                or session.closed
                or getattr(self.policy, "research_active", lambda: False)()
            ):
                raise RuntimeError("operation_conflict")
            self.adapter.check(session)
            self.sessions[session.token] = session
            self._deadlines()
            if session.closed:
                raise RuntimeError(self.last_error or "operation_conflict")
            log.info(
                "transfer_start hash=%s record=%s file=%s kind=%s start=%s stop=%s",
                tid,
                rid,
                file_id,
                kind,
                start,
                stop,
            )
            self._schedule()
            return session
        except BaseException:
            self.stop(session)
            raise

    def _schedule(self):
        if self.timer is None and not self.closed and (self.sessions or self.jobs):
            self.due = self.reactor.seconds() + 0.1
            self.timer = self.reactor.callLater(0.1, self._tick)

    def _tick(self):
        self.timer = None
        self.lag = max(0, self.reactor.seconds() - self.due)
        for session in tuple(self.sessions.values()):
            try:
                self.adapter.check(session)
            except (RuntimeError, ValueError, KeyError):
                self.stop(session, "operation_conflict")
        self._deadlines()
        for key, job in tuple(self.jobs.items()):
            if job["issued"]:
                continue
            for waiter in tuple(job["waiters"]):
                session = waiter[0]
                try:
                    self.adapter.check(session)
                    if not self.adapter.have(session, key[2]):
                        reason = self.adapter.missing_reason(session)
                        if reason:
                            raise RuntimeError(reason)
                        continue
                    job["issued"] = True
                    self.adapter.read(session, key[2])
                    break
                except (RuntimeError, ValueError, KeyError) as exc:
                    self._fail_waiter(key, waiter, str(exc))
        self._schedule()

    def _deadlines(self):
        wanted = {}
        for session in tuple(self.sessions.values()):
            try:
                self.adapter.check(session)
                if session.kind in {"file", "zip"}:
                    continue  # Downloads never schedule missing payload.
                if self.adapter.missing_reason(session):
                    continue
                ahead = self.policy.config["stream_window_mib"] * 1024**2
                pieces = window(
                    session.file,
                    session.position,
                    session.stop,
                    session.layout.piece_length,
                    ahead,
                )
                if len(pieces) + len(session.tails) > self.MAX_DEADLINES:
                    raise RuntimeError("deadline_limit")
                for index, piece in enumerate((*pieces, *session.tails)):
                    if self.adapter.have(session, piece):
                        continue
                    key = (session.tid, id(session.native), piece)
                    deadline = min(index * 50, 5000)
                    old = wanted.get(key)
                    if old is None or deadline < old[1]:
                        wanted[key] = (session.native, deadline)
                    if len(wanted) > self.MAX_DEADLINES:
                        raise RuntimeError("deadline_limit")
            except (RuntimeError, ValueError, KeyError) as exc:
                self.last_error = safe_reason(exc)
                # No global reset: exclude this session on its next operation.
                self.stop(session, str(exc), sync=False)
                return self._deadlines()
        for key, (native, _) in tuple(self.owned.items()):
            if key not in wanted:
                self.adapter.reset(key[0], native, key[2])
        for key, (native, deadline) in wanted.items():
            if self.owned.get(key) != (native, deadline):
                self.adapter.deadline(native, key[2], deadline)
        self.owned = wanted

    def read(self, session, piece):
        if session.closed or self.closed or session.pending is not None:
            return defer.fail(RuntimeError("operation_conflict"))
        try:
            self.adapter.check(session)
            if not self.adapter.have(session, piece):
                if session.kind in {"file", "zip"}:
                    raise RuntimeError("file_not_ready")
                reason = self.adapter.missing_reason(session)
                if reason:
                    raise RuntimeError(reason)
            key = (session.tid, id(session.native), piece)
            count = self.adapter.piece_size(session, piece)
            # Charge two copies per client conservatively (native alert copy +
            # retained bytes/HTTP slice), even when clients share a native read.
            charge = count * 2 + self.HTTP_OVERHEAD
            budget = self.policy.config["payload_buffer_budget_mib"] * 1024**2
            if count <= 0 or self.reserved + charge > budget:
                raise RuntimeError("buffer_limit")
            if key not in self.jobs and len(self.jobs) >= self.MAX_JOBS:
                raise RuntimeError("transfer_limit")
            job = self.jobs.setdefault(
                key,
                {
                    "native": session.native,
                    "waiters": [],
                    "issued": False,
                    "orphan_charge": 0,
                },
            )
            d = defer.Deferred(lambda _: self._cancel(key, session))
            self.reserved += charge
            waiter = (session, d, charge)
            job["waiters"].append(waiter)
            session.pending = (key, waiter)
            d.addTimeout(self.policy.config["piece_wait_timeout_seconds"], self.reactor)
            self._tick_now(key, session)
            self._schedule()
            return d
        except (RuntimeError, ValueError, KeyError) as exc:
            return defer.fail(exc)

    def _tick_now(self, key, session):
        job = self.jobs.get(key)
        if job and not job["issued"] and self.adapter.have(session, key[2]):
            job["issued"] = True
            self.adapter.read(session, key[2])

    def _cancel(self, key, session):
        job = self.jobs.get(key)
        if job:
            for waiter in tuple(job["waiters"]):
                if waiter[0] is session:
                    job["waiters"].remove(waiter)
                    if job["issued"]:
                        # Native read_piece cannot be cancelled. Retain its
                        # quota until the matching alert, even after disconnect.
                        job["orphan_charge"] += waiter[2]
                    else:
                        self.reserved -= waiter[2]
            if not job["waiters"] and not job["issued"]:
                self.jobs.pop(key, None)
        session.pending = None

    def _fail_waiter(self, key, waiter, reason):
        self._cancel(key, waiter[0])
        if not waiter[1].called:
            waiter[1].errback(RuntimeError(reason))

    def _read_alert(self, alert):
        piece = self.adapter.alert_piece(alert)
        for key, job in tuple(self.jobs.items()):
            if (
                key[2] != piece
                or not self.adapter.matches(job["native"], alert)
                or not job["issued"]
            ):
                continue
            self.jobs.pop(key, None)
            self.reserved -= job["orphan_charge"]
            if not job["waiters"]:
                continue  # Do not copy an orphaned native payload into Python.
            read_error = "piece_read_error"
            try:
                data = self.adapter.alert_data(alert)
            except RuntimeError as exc:
                data = None
                read_error = safe_reason(exc)
            for session, d, charge in tuple(job["waiters"]):
                session.pending = None
                try:
                    self.adapter.check(session)
                    if data is None or len(data) * 2 + self.HTTP_OVERHEAD != charge:
                        raise RuntimeError(read_error)
                    if not self.adapter.have(session, key[2]):
                        raise RuntimeError("piece_not_ready")
                except (RuntimeError, ValueError, KeyError) as exc:
                    self.reserved -= charge
                    if not d.called:
                        d.errback(exc)
                else:
                    if not d.called:
                        d.callback(Chunk(self, data, charge))
                    else:
                        self.reserved -= charge

    def advance(self, session, count):
        if session.closed:
            raise RuntimeError("operation_conflict")
        self.adapter.check(session)
        if session.first_byte is None:
            session.first_byte = self.reactor.seconds() - session.started
        session.sent += count
        session.position += count
        session.lease.progress(count)
        self._deadlines()

    def stop(self, session, reason=None, *, sync=True):
        if session.closed:
            return
        session.closed = True
        log.info(
            "transfer_end hash=%s record=%s kind=%s bytes_sent=%s "
            "duration=%.3f first_byte=%s reason=%s",
            session.tid,
            session.rid,
            session.kind,
            session.sent,
            self.reactor.seconds() - session.started,
            session.first_byte,
            safe_reason(reason) if reason else "complete",
        )
        self.sessions.pop(session.token, None)
        if session.ready_wait is not None and not session.ready_wait.called:
            session.ready_wait.cancel()
        if session.pending is not None:
            _, waiter = session.pending
            waiter[1].cancel()
        if session.lease is not None:
            session.lease.release()
        if reason and session.cancel is not None:
            callback, session.cancel = session.cancel, None
            try:
                callback(reason)
            except Exception:
                self.last_error = "cancel_error"
        if sync and not self.closed:
            self._deadlines()

    @defer.inlineCallbacks
    def preload(self, tid, rid, actor, file_id):
        key = (rid, file_id)
        if key in self.preloads:
            return
        # Reserve before yielding admission so concurrent preload coalesces.
        self.preloads[key] = None
        session = None
        try:
            _, layout, file, _ = self.adapter.open(tid, file_id)
            stop = min(file.size, self.policy.config["initial_prebuffer_mib"] * 1024**2)
            session = yield self.open(tid, rid, actor, file_id, 0, stop, "preload")
            self.preloads[key] = session
            if file.path.lower().endswith((".mp4", ".mov", ".m4v")):
                pieces = layout.pieces(file)
                if (
                    min(len(pieces), self.policy.config["tail_prebuffer_pieces"])
                    > self.MAX_DEADLINES
                ):
                    raise RuntimeError("deadline_limit")
                session.tails = tuple(
                    pieces[
                        max(
                            0, len(pieces) - self.policy.config["tail_prebuffer_pieces"]
                        ) :
                    ]
                )
            self._deadlines()
            if session.closed:
                raise RuntimeError(self.last_error or "operation_conflict")
            reason = self.adapter.missing_reason(session)
            if reason:
                needed = (
                    *window(
                        file, session.position, session.stop, layout.piece_length, stop
                    ),
                    *session.tails,
                )
                if any(not self.adapter.have(session, piece) for piece in needed):
                    raise RuntimeError(reason)
            work = self._preload_run(session)
            work.addBoth(lambda result: self._preload_finished(key, session, result))
        except BaseException:
            self.preloads.pop(key, None)
            if session is not None:
                self.stop(session)
            raise

    @defer.inlineCallbacks
    def _preload_run(self, session):
        needed = (
            tuple(
                window(
                    session.file,
                    session.position,
                    session.stop,
                    session.layout.piece_length,
                    session.stop - session.position,
                )
            )
            + session.tails
        )
        for piece in dict.fromkeys(needed):
            if session.closed:
                raise RuntimeError("operation_conflict")
            while not self.adapter.have(session, piece):
                if session.closed:
                    raise RuntimeError("operation_conflict")
                self.adapter.check(session)
                reason = self.adapter.missing_reason(session)
                if reason:
                    raise RuntimeError(reason)
                session.ready_wait = task.deferLater(self.reactor, 0.1, lambda: None)
                yield session.ready_wait
            self.adapter.check(session)
            # Preload lifetime is absolute: no byte-progress renewal.
            session.position = min(
                session.stop, (piece + 1) * session.layout.piece_length
            )
            self._deadlines()

    def _preload_finished(self, key, session, result):
        from twisted.python.failure import Failure

        if isinstance(result, Failure):
            self.last_error = safe_reason(result.value)
        self.preloads.pop(key, None)
        self.stop(session)
        return None

    def diagnostics(self):
        return {
            "sessions": len(self.sessions),
            "piece_jobs": len(self.jobs),
            "payload_bytes_reserved": self.reserved,
            "deadlines": len(self.owned),
            "preloads": len(self.preloads),
            "last_error": self.last_error,
            "reactor_lag_seconds": self.lag,
        }

    def status(self, rid):
        return {
            "waiting_pieces": sum(
                session.pending is not None
                for session in self.sessions.values()
                if session.rid == rid
            )
        }

    def close(self):
        if self.closed:
            return
        self.closed = True
        for session in tuple(self.sessions.values()):
            self.stop(session, "disabled", sync=False)
        for job in self.jobs.values():
            self.reserved -= job["orphan_charge"]
        self.jobs.clear()
        for key, (native, _) in tuple(self.owned.items()):
            self.adapter.reset(key[0], native, key[2])
        self.owned.clear()
        if self.timer is not None and self.timer.active():
            self.timer.cancel()
        self.timer = None
        self.adapter.unregister(self._read_alert)
