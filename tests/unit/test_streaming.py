from types import SimpleNamespace

import pytest
from twisted.internet import defer
from twisted.internet.task import Clock
from twisted.python.failure import Failure

from deluge_stream.config import DEFAULTS
from deluge_stream.layout import FileSpan, Layout
from deluge_stream.streaming import Chunk, Streams, window
from deluge_stream.transfers import TransferRegistry


class Alerts:
    def register_handler(self, name, callback):
        self.callback = callback

    def deregister_handler(self, callback):
        assert self.callback == callback
        self.callback = None


class Adapter:
    def __init__(self):
        self.alerts = Alerts()
        self.native = object()
        self.ready = set()
        self.reason = None
        self.reads = []
        self.deadlines = {}
        self.file = FileSpan(0, "movie.mp4", 0, 128)
        self.layout = Layout(32, 4, (self.file,), frozenset())

    def register(self, handler):
        self.alerts.register_handler("read_piece_alert", handler)

    def unregister(self, handler):
        self.alerts.deregister_handler(handler)

    @staticmethod
    def alert_piece(alert):
        return alert.piece

    @staticmethod
    def alert_data(alert):
        return bytes(alert.buffer) if alert.error.value() == 0 else None

    def open(self, tid, file_id):
        return self.native, self.layout, self.file, "/fixture"

    def check(self, session):
        if session.closed or session.native is not self.native:
            raise RuntimeError("operation_conflict")

    def missing_reason(self, session):
        return self.reason

    def have(self, session, piece):
        return piece in self.ready

    def piece_size(self, session, piece):
        return 32

    def read(self, session, piece):
        self.reads.append(piece)

    def deadline(self, native, piece, milliseconds):
        self.deadlines[piece] = milliseconds

    def reset(self, tid, native, piece):
        if native is self.native:
            self.deadlines.pop(piece, None)

    @staticmethod
    def matches(native, alert):
        return native is alert.native

    def emit(self, piece, data=b"x" * 32):
        self.alerts.callback(
            SimpleNamespace(
                native=self.native,
                piece=piece,
                buffer=data,
                error=SimpleNamespace(value=lambda: 0),
            )
        )


def result(d):
    values = []
    d.addBoth(values.append)
    assert values, "Deferred unexpectedly pending"
    if isinstance(values[0], Failure):
        values[0].raiseException()
    return values[0]


@pytest.fixture
def engine():
    clock = Clock()
    config = dict(DEFAULTS)
    config["piece_wait_timeout_seconds"] = 1
    transfers = TransferRegistry(clock, lambda: config, lambda: None)
    policy = SimpleNamespace(config=config)
    policy.acquire_transfer = lambda tid, rid, actor, kind, cancel: defer.succeed(
        transfers.acquire(rid, kind, cancel)
    )
    engine = Streams(clock, policy, Adapter())
    yield engine
    engine.close()
    transfers.close()
    assert not engine.jobs
    assert engine.reserved == 0
    assert not clock.getDelayedCalls()


def open_session(engine, start=0, stop=128, **kwargs):
    return result(
        engine.open("a" * 40, "record", ("alice", False), 1, start, stop, **kwargs)
    )


def test_window_is_byte_bounded_and_uses_native_offsets():
    file = FileSpan(1, "b", 31, 66)
    assert list(window(file, 31, 32, 32, 100)) == [0]
    assert list(window(file, 31, 97, 32, 33)) == [0, 1]
    assert not window(file, 97, 97, 32, 100)


def test_ready_range_in_incomplete_file_keeps_native_reader(engine):
    engine.adapter.supports_direct = True
    engine.adapter.ready.add(0)
    session = open_session(engine, stop=16)
    assert session.direct_identity is None and engine.direct is None
    waiting = engine.read_next(session)
    with pytest.raises(RuntimeError, match="operation_conflict"):
        result(engine.read_next(session))
    assert engine.adapter.reads == [0]
    engine.adapter.emit(0)
    chunk = result(waiting)
    assert chunk.start == 0 and chunk.data == b"x" * 32
    assert not session.reading
    chunk.release()


def test_native_read_next_cancellation_releases_single_read_guard(engine):
    session = open_session(engine)
    waiting = engine.read_next(session)
    waiting.cancel()
    with pytest.raises(defer.CancelledError):
        result(waiting)
    assert not session.reading


@pytest.mark.parametrize("outcome", ["ready", "lost", "timeout"])
def test_whole_file_direct_selection_and_live_readiness(engine, outcome):
    engine.adapter.supports_direct = True
    engine.adapter.ready.update(range(4))
    pending = defer.Deferred()

    def submit(session, operation, *args, charge=0, start=None):
        if start is None:
            return defer.succeed((1, 2))
        assert args[-2:] == (0, 128)
        engine.reserved += charge
        pending.addCallback(lambda data: Chunk(engine, data, charge, start=start))
        return pending

    engine.direct = SimpleNamespace(
        submit=submit, BLOCK_SIZE=256 * 1024, close=lambda: defer.succeed(None)
    )
    session = open_session(engine)
    assert session.direct_identity == (1, 2) and not engine.owned
    waiting = engine.read_next(session)
    if outcome == "timeout":
        engine.reactor.advance(1.1)
        with pytest.raises(defer.TimeoutError):
            result(waiting)
        # The real DirectReader retains this charge until the worker drains.
        engine.reserved = 0
    else:
        if outcome == "lost":
            engine.adapter.ready.remove(0)
        pending.callback(b"x" * 128)
        if outcome == "lost":
            with pytest.raises(RuntimeError, match="file_not_ready"):
                result(waiting)
        else:
            chunk = result(waiting)
            assert chunk.start == 0 and chunk.data == b"x" * 128
            chunk.release()
    assert not session.reading and not engine.adapter.reads


@pytest.mark.parametrize(
    "reason", ["torrent_paused", "session_paused", "file_skipped", "torrent_queued"]
)
def test_ready_piece_can_be_read_when_missing_data_is_blocked(engine, reason):
    engine.adapter.reason = reason
    engine.adapter.ready.add(0)
    session = open_session(engine)
    assert engine.adapter.deadlines == {}
    d = engine.read(session, 0)
    assert engine.adapter.reads == [0]
    engine.adapter.emit(0)
    chunk = result(d)
    assert chunk.data == b"x" * 32
    chunk.release()
    with pytest.raises(RuntimeError, match=reason):
        result(engine.read(session, 1))


def test_two_clients_share_native_read_but_have_independent_release(engine):
    engine.adapter.ready.add(0)
    a, b = open_session(engine), open_session(engine)
    da, db = engine.read(a, 0), engine.read(b, 0)
    assert engine.adapter.reads == [0]
    assert engine.reserved == 2 * (64 + engine.HTTP_OVERHEAD)
    engine.adapter.emit(0)
    ca, cb = result(da), result(db)
    assert ca.data is cb.data
    ca.release()
    ca.release()
    assert engine.reserved == 64 + engine.HTTP_OVERHEAD
    engine.stop(a)
    assert not b.closed
    cb.release()


def test_deadline_union_survives_one_reader_closing(engine):
    a = open_session(engine, 0, 65)
    b = open_session(engine, 64, 128)
    assert set(engine.adapter.deadlines) == {0, 1, 2, 3}
    engine.stop(a)
    assert set(engine.adapter.deadlines) == {2, 3}
    engine.stop(b)
    assert not engine.adapter.deadlines


def test_wait_cancel_and_timeout_release_budget(engine):
    session = open_session(engine)
    pending = engine.read(session, 0)
    pending.cancel()
    with pytest.raises(defer.CancelledError):
        result(pending)
    assert engine.reserved == 0 and not engine.jobs
    pending = engine.read(session, 0)
    engine.reactor.advance(1)
    with pytest.raises(defer.TimeoutError):
        result(pending)
    assert engine.reserved == 0 and not engine.jobs


def test_replacement_and_stale_alert_cannot_deliver_old_data(engine):
    cancelled = []
    session = open_session(engine, cancel=cancelled.append)
    pending = engine.read(session, 0)
    engine.adapter.native = object()
    engine.reactor.advance(0.1)
    assert session.closed and cancelled == ["operation_conflict"]
    with pytest.raises(defer.CancelledError):
        result(pending)
    assert not engine.jobs


@pytest.mark.parametrize("replacement", [False, True])
def test_issued_cancel_retains_native_quota_until_alert(engine, replacement):
    engine.adapter.ready.add(0)
    session = open_session(engine)
    pending = engine.read(session, 0)
    charge = engine.reserved
    pending.cancel()
    with pytest.raises(defer.CancelledError):
        result(pending)
    assert engine.reserved == charge and len(engine.jobs) == 1
    engine.MAX_JOBS = 1
    with pytest.raises(RuntimeError, match="transfer_limit"):
        result(engine.read(session, 1))
    if replacement:
        other = open_session(engine)
        waiting = engine.read(other, 0)
        assert engine.adapter.reads == [0]
        engine.adapter.emit(0)
        chunk = result(waiting)
        assert engine.reserved == charge
        chunk.release()
    else:
        engine.adapter.alert_data = lambda alert: pytest.fail("orphan copied")
        engine.adapter.emit(0)
    assert engine.reserved == 0 and not engine.jobs


def test_close_clears_orphan_native_accounting(engine):
    engine.adapter.ready.add(0)
    pending = engine.read(open_session(engine), 0)
    engine.close()
    with pytest.raises(defer.CancelledError):
        result(pending)
    assert engine.reserved == 0 and not engine.jobs


def test_short_native_read_is_not_served_as_valid_bytes(engine):
    engine.adapter.ready.add(0)
    session = open_session(engine)
    pending = engine.read(session, 0)
    engine.adapter.emit(0, b"short")
    with pytest.raises(RuntimeError, match="piece_read_error"):
        result(pending)


def test_native_storage_failure_releases_all_read_waiters(engine):
    engine.adapter.ready.add(0)
    pending = [engine.read(open_session(engine), 0) for _ in range(2)]

    def failed(alert):
        raise RuntimeError("disk_full")

    engine.adapter.alert_data = failed
    engine.adapter.emit(0)
    for work in pending:
        with pytest.raises(RuntimeError, match="disk_full"):
            result(work)
    assert not engine.jobs and engine.reserved == 0


def test_preload_is_coalesced_and_not_playback(engine):
    assert result(engine.preload("a" * 40, "record", ("alice", False), 1)) is None
    assert result(engine.preload("a" * 40, "record", ("alice", False), 1)) is None
    assert len(engine.sessions) == len(engine.preloads) == 1
    assert next(iter(engine.sessions.values())).kind == "preload"
    for piece in range(4):
        engine.adapter.ready.add(piece)
        engine.reactor.advance(0.1)
        engine.adapter.emit(piece)
    assert not engine.sessions and not engine.preloads
    assert engine.adapter.reads == []  # Preload is disk-backed, no RAM payload reads.


def test_limits_reject_without_reservation_leak(engine):
    engine.MAX_DEADLINES = 1
    with pytest.raises(RuntimeError):
        session = open_session(engine)
        if session.closed:
            raise RuntimeError("deadline_limit")
    assert not engine.sessions
    assert engine.reserved == 0


@pytest.mark.parametrize("kind", ["file", "zip"])
def test_download_never_schedules_missing_pieces(engine, kind):
    session = open_session(engine, kind=kind, public=False)
    assert not engine.owned and not engine.adapter.deadlines
    with pytest.raises(RuntimeError, match="file_not_ready"):
        result(engine.read(session, 0))
    assert not engine.jobs and engine.reserved == 0


def test_native_invalidation_cancels_only_matching_instance(engine):
    session = open_session(engine)
    work = engine.read(session, 0)
    engine._invalidated(SimpleNamespace(native=object()))
    assert not session.closed and not work.called
    engine._invalidated(SimpleNamespace(native=engine.adapter.native))
    assert session.closed
    with pytest.raises(defer.CancelledError):
        result(work)
