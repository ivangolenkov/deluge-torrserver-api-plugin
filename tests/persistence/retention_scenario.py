"""Deterministic fake-native barriers around real reactor/SQLite coordinator."""

import sqlite3
import sys
from pathlib import Path

from twisted.internet import defer, task

from deluge_stream.lifecycle import PolicyService

ACTOR = ("alice", True)
A, B, C = (character * 40 for character in "abc")


class Adapter:
    session_ready = True

    def __init__(self, root, changed):
        self.root, self.changed = root, changed
        self.live = {A: "old-a", B: "old-b", C: "old-c"}
        self.fresh = set()
        self.calls = []
        self.waiter = None

    def database_path(self):
        return self.root / "policy.sqlite"

    def catalogue(self):
        return tuple(
            (tid, key, "alice", (), tid in self.fresh)
            for tid, key in sorted(self.live.items())
        )

    def deletion_catalogue(self):
        return tuple((tid, str(self.root), (tid,)) for tid in sorted(self.live))

    def access(self, tid, actor, *, write=False):
        return bool(actor[0]) and tid in self.live

    def register_status(self, fields):
        pass

    def owner_exists(self, owner):
        return owner == "alice"

    def watch_deletion(self, tid, key, reactor, timeout):
        assert self.waiter is None
        self.waiter = defer.Deferred(lambda d: d.callback("timeout"))
        return self.waiter

    def remove_with_data(self, tid, key):
        assert self.live[tid] == key
        self.calls.append(tid)
        del self.live[tid]
        self.changed()
        return True

    def finish(self, result="deleted"):
        waiter, self.waiter = self.waiter, None
        waiter.callback(result)

    def close(self, fields):
        if self.waiter is not None:
            self.finish("timeout")


@defer.inlineCallbacks
def until(reactor, predicate):
    for _ in range(1000):
        if predicate():
            return
        yield task.deferLater(reactor, 0.005, lambda: None)
    raise AssertionError("coordinator barrier timed out")


@defer.inlineCallbacks
def setup(reactor, root):
    root.mkdir()
    s = PolicyService(reactor, lambda changed: Adapter(root, changed))
    yield s.open()
    yield s.set_config({"max_temporary_torrents": 100, "debounce_seconds": 1}, ACTOR)
    for tid in (A, B, C):
        yield s.mutate(tid, s.rows[tid]["record_id"], ACTOR, temporary=True)
    return s


@defer.inlineCallbacks
def protection(reactor, root, kind):
    s = yield setup(reactor, root)
    try:
        rid = s.rows[A]["record_id"]
        lease = yield s.acquire_transfer(A, rid, ACTOR, kind)
        yield s.set_config({"max_temporary_torrents": 1}, ACTOR)
        yield until(reactor, lambda: s.retention.reason == "protected_oldest")
        assert s.adapter.calls == []
        if kind == "ticket":
            yield s.activate_ticket(A, rid, ACTOR, lease, "file")
        lease.release()
        yield until(reactor, lambda: s.adapter.calls == [A])
        # Matching completion is a controlled barrier, not a timing sleep.
        assert s.adapter.waiter is not None
        yield until(reactor, lambda: s.ready)
        s.adapter.finish()
        yield until(reactor, lambda: s.adapter.calls == [A, B])
        s.adapter.finish()
        yield until(reactor, lambda: s.retention.running is None)
        assert set(s.adapter.live) == {C}
    finally:
        yield s.close(reset=False)


@defer.inlineCallbacks
def pin_before_cleanup(reactor, root):
    s = yield setup(reactor, root)
    try:
        rid = s.rows[A]["record_id"]
        # Queue pin before cleanup acquires the same lock.
        yield s.lock.acquire()
        pin = s.mutate(A, rid, ACTOR, temporary=False)
        s.config["max_temporary_torrents"] = 1
        s.retention.wake()
        s.lock.release()
        assert not (yield pin)["is_temporary"]
        yield until(reactor, lambda: s.adapter.calls == [B])
        s.adapter.finish()
        yield until(reactor, lambda: s.retention.running is None)
        assert A in s.adapter.live
    finally:
        yield s.close(reset=False)


@defer.inlineCallbacks
def cleanup_before_admission(reactor, root):
    s = yield setup(reactor, root)
    try:
        rid = s.rows[A]["record_id"]
        s.retention.stop()
        s.config["max_temporary_torrents"] = 2
        yield s.lock.acquire()
        s.retention.wake()
        # Zero-delay turn puts cleanup into the lock queue before our requests.
        yield task.deferLater(reactor, 0, lambda: None)
        pin = s.mutate(A, rid, ACTOR, temporary=False)
        admission = s.acquire_transfer(A, rid, ACTOR, "file")
        s.lock.release()
        for result in (pin, admission):
            try:
                yield result
            except (RuntimeError, ValueError):
                pass
            else:
                raise AssertionError("admission/pin succeeded after native deletion")
        assert s.adapter.calls == [A]
        s.adapter.finish()
        yield until(reactor, lambda: s.retention.running is None)
    finally:
        yield s.close(reset=False)


@defer.inlineCallbacks
def readd_and_failure(reactor, root, outcome):
    s = yield setup(reactor, root)
    try:
        old = s.rows[A]["record_id"]
        yield s.set_config({"max_temporary_torrents": 1}, ACTOR)
        yield until(reactor, lambda: s.adapter.calls == [A])
        # Native disk completion withheld while policy lock is available.
        s.adapter.live[A] = "new-a"
        s.adapter.fresh.add(A)
        s.changed()
        yield until(reactor, lambda: s.ready)
        new = s.rows[A]["record_id"]
        assert new != old
        lease = yield s.acquire_transfer(A, new, ACTOR, "file")
        try:
            yield s.mutate(A, old, ACTOR, temporary=False)
        except ValueError:
            pass
        else:
            raise AssertionError("old pin succeeded")
        s.adapter.finish(outcome)
        yield until(reactor, lambda: bool(s.retention.pending))
        assert s.adapter.calls == [A]  # no skip to B after failure
        assert s.transfers.status(new)["cleanup_protected"]
        op = s.retention.pending[0]["operation_id"]
        yield s.resolve_removal(op, "acknowledge", ACTOR)
        yield until(reactor, lambda: s.adapter.calls == [A, B])
        s.adapter.finish()
        yield until(reactor, lambda: s.retention.running is None)
        assert s.rows[A]["record_id"] == new
        assert not lease.closed
        lease.release()
    finally:
        yield s.close(reset=False)


@defer.inlineCallbacks
def restart_intent(reactor, root):
    s = yield setup(reactor, root)
    row = s.rows[A]
    plan = {"files": [{"path": str(root / "orphan")}], "parents": {}}
    (root / "orphan").write_bytes(b"keep")
    op = yield s.worker.call(
        "prepare_removal", row["record_id"], row["revision"], row["fifo_sequence"], plan
    )
    yield s.worker.call("removal_state", op, "submitting")
    yield s.close(reset=False)
    restored = PolicyService(reactor, lambda changed: Adapter(root, changed))
    try:
        yield restored.open()
        assert restored.retention.pending[0]["stage"] == "manual"
        assert (root / "orphan").read_bytes() == b"keep"
        assert restored.adapter.calls == []
        yield restored.resolve_removal(op, "acknowledge", ACTOR)
    finally:
        yield restored.close(reset=False)


@defer.inlineCallbacks
def disk_full(reactor, root):
    s = yield setup(reactor, root)
    try:

        def full(*args):
            raise sqlite3.OperationalError("database or disk is full")

        s.worker.repository.prepare_removal = full
        yield s.set_config({"max_temporary_torrents": 3}, ACTOR)
        yield task.deferLater(reactor, 0, lambda: None)
        yield until(reactor, lambda: s.retention.running is None)
        assert s.adapter.calls == [] and s.error is None
        yield s.set_config({"max_temporary_torrents": 1}, ACTOR)
        yield until(reactor, lambda: s.error == "storage_error")
        assert s.adapter.calls == []  # no native mutation without durable intent
    finally:
        yield s.close(reset=False)


@defer.inlineCallbacks
def main(reactor, directory):
    root = Path(directory)
    for kind in ("playback", "preload", "file", "zip", "ticket"):
        yield protection(reactor, root / kind, kind)
    yield pin_before_cleanup(reactor, root / "pin")
    yield cleanup_before_admission(reactor, root / "cleanup-first")
    for outcome in ("delete_failed", "timeout"):
        yield readd_and_failure(reactor, root / outcome, outcome)
    yield restart_intent(reactor, root / "restart")
    yield disk_full(reactor, root / "disk-full")
    print("RETENTION: five protections/FIFO/pin/re-add/failure/restart barriers passed")


task.react(main, [sys.argv[1]])
