import pytest
from twisted.internet.task import Clock

from deluge_stream.config import DEFAULTS
from deluge_stream.transfers import TransferRegistry


@pytest.fixture
def registry():
    return TransferRegistry(Clock(), lambda: DEFAULTS, lambda: None)


@pytest.mark.parametrize("kind", ["playback", "preload", "file", "zip", "ticket"])
def test_each_kind_protects_and_release_is_once(registry, kind):
    lease = registry.acquire("old", kind)
    assert registry.status("old")["cleanup_protected"]
    assert not registry.status("replacement")["cleanup_protected"]
    assert lease.release()
    assert not lease.release()
    registry.clock.advance(DEFAULTS["debounce_seconds"])
    assert not registry.status("old")["cleanup_protected"]
    assert not registry.clock.getDelayedCalls()


@pytest.mark.parametrize("kind", ["playback", "preload", "file", "zip", "ticket"])
def test_expiry_cancels_once_and_cannot_renew_retired_lease(registry, kind):
    cancelled = []
    lease = registry.acquire("old", kind, cancelled.append)
    registry.clock.advance(61)
    assert cancelled == ["expired"]
    assert not lease.release()
    if kind != "ticket":
        assert not lease.progress(1)
    assert not registry.leases


def test_progress_renews_only_on_actual_bytes(registry):
    lease = registry.acquire("old", "file")
    registry.clock.advance(59)
    assert lease.progress(1)
    registry.clock.advance(59)
    assert not lease.closed
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            lease.progress(value)
    registry.clock.advance(1)
    assert lease.closed


def test_ticket_to_file_is_atomic_when_limit_reached(registry):
    files = [registry.acquire("other", "file") for _ in range(4)]
    ticket = registry.acquire("old", "ticket")
    with pytest.raises(RuntimeError, match="transfer_limit"):
        registry.activate(ticket, "file")
    assert ticket.kind == "ticket"
    assert not ticket.closed
    files[0].release()
    assert registry.activate(ticket, "file") is ticket
    assert registry.status("old")["file_downloads"] == 1
    assert registry.status("old")["ticket_reservations"] == 0


def test_ticket_has_fixed_start_deadline(registry):
    ticket = registry.acquire("old", "ticket")
    with pytest.raises(ValueError):
        ticket.progress(1)
    registry.clock.advance(60)
    with pytest.raises(ValueError):
        registry.activate(ticket, "zip")


def test_last_playback_only_and_stale_debounce_callback(registry):
    a = registry.acquire("old", "playback")
    b = registry.acquire("old", "playback")
    a.release()
    assert "old" not in registry.debounce
    b.release()
    version = registry.debounce["old"][0]
    c = registry.acquire("old", "playback")
    c.release()
    registry._debounced("old", version)
    assert registry.status("old")["streaming_protected"]
    registry.clock.advance(10)
    assert not registry.status("old")["streaming_protected"]


def test_retirement_and_shutdown_do_not_leak_timers(registry):
    cancelled = []
    old = registry.acquire("old", "playback", cancelled.append)
    new = registry.acquire("replacement", "playback", cancelled.append)
    registry.reconcile({"replacement"})
    assert old.closed and not new.closed
    assert cancelled == ["retired"]
    assert not registry.status("old")["cleanup_protected"]
    registry.close()
    assert cancelled == ["retired", "shutdown"]
    assert not registry.clock.getDelayedCalls()
    with pytest.raises(RuntimeError):
        registry.acquire("replacement", "file")


def test_blocked_record_cannot_acquire_or_activate(registry):
    ticket = registry.acquire("old", "ticket")
    registry.blocked.add("old")
    with pytest.raises(RuntimeError):
        registry.activate(ticket, "file")
    with pytest.raises(RuntimeError):
        registry.acquire("old", "playback")


def test_callback_failure_does_not_leak_protection(registry):
    def fail(reason):
        raise RuntimeError(reason)

    lease = registry.acquire("old", "file", fail)
    registry.retire("old")
    assert lease.closed and not registry.leases
    assert not registry.clock.getDelayedCalls()


def test_kind_and_global_limits(registry):
    with pytest.raises(ValueError):
        registry.acquire("old", "stats")
    registry.acquire("old", "zip")
    with pytest.raises(RuntimeError, match="transfer_limit"):
        registry.acquire("new", "zip")
    registry.CAPACITY = 1
    with pytest.raises(RuntimeError, match="transfer_limit"):
        registry.acquire("new", "file")
