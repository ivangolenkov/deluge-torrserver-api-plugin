from types import SimpleNamespace

import pytest
from twisted.internet import defer
from twisted.python.failure import Failure

from deluge_stream.lifecycle import PolicyService


def service():
    result = PolicyService.__new__(PolicyService)
    result.operations = 0
    result.closed = result.quiescing = False
    result.ready = True
    result.epoch = 0
    result.error = None
    result.lock = defer.DeferredLock()
    result.transfers = SimpleNamespace(blocked=set())
    result.rows = {"tid": {"record_id": "rid", "instance_key": "key", "owner": "alice"}}
    result.adapter = SimpleNamespace(
        catalogue=lambda: (("tid", "key", "alice"),),
        access=lambda tid, actor, **kw: actor[1] or actor[0] == "alice",
    )
    result.changed = lambda: setattr(result, "epoch", result.epoch + 1)
    pending = defer.Deferred()
    result.worker = SimpleNamespace(call=lambda *a: pending)
    return result, pending


def collect(work):
    values = []
    work.addBoth(values.append)
    return values


@pytest.mark.parametrize("actor", [("alice", False), ("admin", True)])
def test_owner_admin_snapshot_and_bounded_operation(actor):
    coordinator, pending = service()
    values = collect(coordinator.metadata("tid", "rid", actor))
    assert not values and coordinator.operations == 1
    pending.callback({"fields": {}, "version": "initial"})
    assert values == [{"fields": {}, "version": "initial", "record_id": "rid"}]
    assert coordinator.operations == 0


def test_shared_reader_is_not_metadata_reader():
    coordinator, _ = service()
    values = collect(coordinator.metadata("tid", "rid", ("bob", False)))
    assert isinstance(values[0], Failure)
    assert str(values[0].value) == "torrent_not_found"
    assert coordinator.operations == 0


def test_stale_editor_does_not_invalidate_replacement_catalogue():
    coordinator, _ = service()
    values = collect(coordinator.metadata("tid", "old-rid", ("alice", False)))
    assert str(values[0].value) == "operation_conflict"
    assert coordinator.ready and coordinator.epoch == 0
    assert coordinator.operations == 0 and not coordinator.lock.locked


@pytest.mark.parametrize("event", ["epoch", "owner", "closed", "quiescing"])
def test_late_result_not_reported_as_success(event):
    coordinator, pending = service()
    values = collect(coordinator.metadata("tid", "rid", ("alice", False)))
    if event == "epoch":
        coordinator.epoch += 1
    elif event == "owner":
        coordinator.adapter.access = lambda *a, **kw: False
    else:
        setattr(coordinator, event, True)
    pending.callback({"fields": {}, "version": "initial"})
    assert isinstance(values[0], Failure)
    assert coordinator.operations == 0


def test_cancel_observer_does_not_release_lock_before_worker_finishes():
    coordinator, pending = service()
    work = coordinator.metadata("tid", "rid", ("alice", False), version="v", changes={})
    values = collect(work)
    work.cancel()
    assert isinstance(values[0].value, defer.CancelledError)
    assert coordinator.operations == 1 and coordinator.lock.locked
    pending.callback({"fields": {}, "version": "new"})
    assert coordinator.operations == 0 and not coordinator.lock.locked


def test_queue_limit_and_early_shape_bound():
    coordinator, _ = service()
    coordinator.operations = 32
    values = collect(coordinator.metadata("tid", "rid", ("alice", False)))
    assert str(values[0].value) == "operation_conflict"
    coordinator.operations = 0
    for changes in ({"data": "x" * 65537}, {"arbitrary": "x"}, {"data": 1}):
        values = collect(
            coordinator.metadata("tid", "rid", ("alice", False), changes=changes)
        )
        assert str(values[0].value) == "invalid_request"
    assert coordinator.operations == 0
