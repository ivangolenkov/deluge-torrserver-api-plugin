from types import SimpleNamespace

import pytest
from twisted.internet import defer
from twisted.internet.task import Clock
from twisted.python.failure import Failure

from deluge_stream.config import DEFAULTS
from deluge_stream.downloads import Downloads
from deluge_stream.layout import FileSpan, Layout
from deluge_stream.transfers import TransferRegistry


def result(work):
    values = []
    work.addBoth(values.append)
    assert values
    if isinstance(values[0], Failure):
        values[0].raiseException()
    return values[0]


@pytest.fixture
def manager():
    clock = Clock()
    config = dict(DEFAULTS, public_base_url="http://127.0.0.1:8090")
    registry = TransferRegistry(clock, lambda: config, lambda: None)
    policy = SimpleNamespace(config=config)
    policy.worker = SimpleNamespace(
        submit=lambda function, *args: defer.maybeDeferred(function, *args)
    )
    policy.acquire_transfer = lambda tid, rid, actor, kind: defer.succeed(
        registry.acquire(rid, kind)
    )
    policy._record = lambda *args: {}
    policy.adapter = SimpleNamespace(download_access=lambda *args: True)
    server = SimpleNamespace(
        policy=policy,
        reactor=clock,
        streams=SimpleNamespace(adapter=None),
        listener=object(),
        error=None,
    )
    manager = Downloads(server)
    file = FileSpan(0, "file", 0, 4)
    manager._ready = lambda *args: defer.succeed(
        (
            object(),
            Layout(4, 1, (file,), frozenset()),
            (file,),
            ("file",),
            "/fixture",
            ((1, 2, 4, 0),),
        )
    )
    yield manager, registry
    manager.close()
    registry.close()
    assert manager.metadata_reserved == 0
    assert not clock.getDelayedCalls()


def create(manager):
    reply = result(manager.create("a" * 40, "rid", ("alice", False), "file"))
    return manager.get(reply["url"].rsplit("/", 1)[1])


def test_unused_ticket_expiry_frees_reservation_and_metadata(manager):
    downloads, registry = manager
    ticket = create(downloads)
    assert downloads.metadata_reserved > 0
    assert registry.status("rid")["ticket_reservations"] == 1
    downloads.reactor.advance(60)
    with pytest.raises(ValueError, match="ticket_invalid"):
        downloads.get(ticket.token)
    assert not registry.leases and downloads.metadata_reserved == 0


def test_active_ticket_expiry_does_not_interrupt_transfer_or_free_metadata(manager):
    downloads, registry = manager
    ticket = create(downloads)
    ticket.active = True
    registry.activate(ticket.reservation, "file")
    charge = downloads.metadata_reserved
    downloads.reactor.advance(59)
    ticket.reservation.progress(1)
    downloads.reactor.advance(2)
    assert ticket.retired and downloads.metadata_reserved == charge
    assert not ticket.reservation.closed
    ticket.reservation.release()
    downloads.finished(ticket)
    assert downloads.metadata_reserved == 0


def test_metadata_quota_rejects_without_leaking_protection(manager):
    downloads, registry = manager
    downloads.METADATA_BUDGET = 1
    with pytest.raises(RuntimeError, match="transfer_limit"):
        create(downloads)
    assert not downloads.tickets and not registry.leases


def test_cancelled_preflight_releases_reservation_and_job_slot(manager):
    downloads, registry = manager
    downloads._ready = lambda *args: defer.Deferred()
    work = downloads.create("a" * 40, "rid", ("alice", False), "file")
    assert downloads.preflights == 1 and registry.leases
    work.cancel()
    with pytest.raises(defer.CancelledError):
        result(work)
    assert downloads.preflights == 0 and not registry.leases


def test_ticket_ttl_and_fifo_reservation_start_together_after_preflight(manager):
    downloads, registry = manager
    values = result(downloads._ready())
    pending = defer.Deferred()
    downloads._ready = lambda *args: pending
    work = downloads.create("a" * 40, "rid", ("alice", False), "file")
    downloads.reactor.advance(50)
    pending.callback(values)
    reply = result(work)
    ticket = downloads.get(reply["url"].rsplit("/", 1)[1])
    assert ticket.expires == 110
    with pytest.raises(ValueError, match="invalid_reservation"):
        ticket.reservation.issue()
    downloads.reactor.advance(11)
    assert downloads.get(ticket.token) is ticket
    assert registry.status("rid")["ticket_reservations"] == 1
