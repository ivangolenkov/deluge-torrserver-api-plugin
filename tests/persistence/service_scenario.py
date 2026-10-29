"""Real SQLite/worker/reactor; fake native catalogue for deterministic barriers."""

import sys
import threading
from pathlib import Path

from twisted.internet import defer, task

from deluge_stream.lifecycle import PolicyService
from deluge_stream.repository import Repository

TID = "a" * 40


class Adapter:
    session_ready = True

    def __init__(self, root, changed):
        self.root = root
        self.changed = changed
        self.key = "key-1"
        self.fresh = set()

    def database_path(self):
        return self.root / "policy.sqlite"

    def catalogue(self):
        return ((TID, self.key, "alice", (), TID in self.fresh),)

    def payload_plan(self):
        return ((TID, str(self.root), ("movie",)),)

    deletion_catalogue = payload_plan

    def access(self, tid, actor, *, write=False):
        return tid == TID and (actor[1] or actor[0] == "alice")

    def owner_exists(self, owner):
        return owner == "alice"

    def register_status(self, fields):
        self.fields = fields

    def close(self, fields):
        self.fields = {}


class Barrier(Repository):
    def __init__(self, path):
        super().__init__(path)
        self.block = False
        self.committed = threading.Event()
        self.release = threading.Event()

    def set_policy(self, *args, **kwargs):
        result = super().set_policy(*args, **kwargs)
        if self.block:
            self.block = False
            self.committed.set()
            if not self.release.wait(5):
                raise TimeoutError("barrier was not released")
        return result


@defer.inlineCallbacks
def wait(reactor, condition):
    for _ in range(500):
        if condition():
            return
        yield task.deferLater(reactor, 0.005, lambda: None)
    raise AssertionError("service condition timed out")


@defer.inlineCallbacks
def main(reactor, directory):
    root = Path(directory)
    service = PolicyService(reactor, lambda changed: Adapter(root, changed))
    yield service.open()
    owner = ("alice", False)
    rid = service.rows[TID]["record_id"]
    try:
        first = yield service.mutate(TID, rid, owner, temporary=True)
        assert first["fifo_sequence"] == 1
        assert (yield service.publish_existing(TID, False, owner))["fifo_sequence"] == 1
        assert not (yield service.publish_existing(TID, True, owner))["is_temporary"]
        assert not (yield service.publish_existing(TID, False, owner))["is_temporary"]

        # Cancellation must not skip post-commit cache publication/admission drain.
        barrier = Barrier(service.worker.repository.path)
        service.worker.repository = barrier
        barrier.block = True
        pending = service.mutate(TID, rid, owner, temporary=True)
        yield wait(reactor, barrier.committed.is_set)
        pending.cancel()
        try:
            yield pending
        except defer.CancelledError:
            pass
        assert service.operations == 1
        barrier.release.set()
        yield wait(reactor, lambda: service.operations == 0)
        assert service.get_status([TID], owner)[TID]["is_temporary"]

        yield service.mutate(TID, rid, owner, temporary=False)
        barrier.committed.clear()
        barrier.release.clear()
        barrier.block = True
        stale = service.mutate(TID, rid, owner, temporary=True)
        yield wait(reactor, barrier.committed.is_set)
        service.adapter.key = "replacement-key"
        service.adapter.fresh.add(TID)
        service.changed()
        barrier.release.set()
        try:
            yield stale
        except ValueError as exc:
            assert str(exc) == "operation_conflict"
        else:
            raise AssertionError("stale result was accepted")
        yield wait(reactor, lambda: service.ready)
        row = service.get_status([TID], owner)[TID]
        assert row["record_id"] != rid and not row["is_temporary"]
    finally:
        if isinstance(service.worker.repository, Barrier):
            service.worker.repository.release.set()
        yield service.close(reset=False)
    print(
        "POLICY SERVICE: real commit/cancel/re-add "
        "barriers and bounded admission passed"
    )


task.react(main, [sys.argv[1]])
