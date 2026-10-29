from types import SimpleNamespace

from twisted.internet import defer
from twisted.internet.task import Clock

from deluge_stream.retention import Retention


def test_retry_is_bounded_throttled_and_shutdown_cancels_timer():
    clock = Clock()
    service = SimpleNamespace(
        reactor=clock, closed=False, config={"cleanup_retry_seconds": 5}
    )
    retention = Retention(service)
    calls = []

    def run():
        calls.append(clock.seconds())
        retention.reason = "unsafe_paths"
        return defer.succeed(None)

    retention._run = run
    retention.wake()
    clock.advance(0)
    clock.advance(4)
    assert calls == [0]
    clock.advance(1)
    clock.advance(5)
    clock.advance(100)
    assert calls == [0, 5, 10]
    retention.wake()
    clock.advance(0)
    assert calls[-1] == 110
    service.closed = True
    retention.stop()
    assert not clock.getDelayedCalls()


def test_manual_pending_and_storage_error_do_not_retry_native_removal():
    clock = Clock()
    failures = []
    service = SimpleNamespace(
        reactor=clock,
        closed=False,
        config={"cleanup_retry_seconds": 5},
        _failed=failures.append,
    )
    retention = Retention(service)
    retention.pending = [{"stage": "manual"}]
    retention._run = lambda: defer.fail(RuntimeError("database unavailable"))
    retention.wake()
    clock.advance(0)
    assert len(failures) == 1
    assert retention.reason == "storage_error"
    assert not clock.getDelayedCalls()
