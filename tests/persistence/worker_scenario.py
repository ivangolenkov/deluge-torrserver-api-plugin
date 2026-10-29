"""Executed in a fresh process: real reactor, thread pool and SQLite lock."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from twisted.internet import defer, task

from deluge_stream.db_worker import DatabaseWorker, WorkerUnavailable
from deluge_stream.repository import Repository


class CommitBarrierRepository(Repository):
    def __init__(self, path):
        super().__init__(path)
        self.committed = threading.Event()
        self.release = threading.Event()

    def set_settings(self, changes):
        super().set_settings(changes)
        self.committed.set()
        if not self.release.wait(5):
            raise TimeoutError("commit barrier was not released")


@defer.inlineCallbacks
def scenario(reactor, root):
    worker = DatabaseWorker(
        reactor, Repository(Path(root) / "worker.sqlite"), capacity=2
    )
    yield worker.call("initialize")
    row = yield worker.call("register", "a" * 40, "instance-1", "alice")
    rid = row["record_id"]
    ticks = []
    ticker = task.LoopingCall(lambda: ticks.append(1))
    ticker.clock = reactor
    ticker.start(0.005)
    try:
        # A real external writer holds SQLite's lock while reactor keeps ticking.
        with closing(sqlite3.connect(worker.repository.path)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            first = worker.call("set_policy", rid, temporary=True)
            cancelled = worker.call("set_viewed", rid, 1, 12.5)
            cancelled.cancel()
            try:
                yield cancelled
            except defer.CancelledError:
                pass
            else:
                raise AssertionError("queued cancellation did not fire")
            # Cancellation retains capacity until the actual queued job is drained.
            try:
                yield worker.call("get", rid)
            except WorkerUnavailable:
                pass
            else:
                raise AssertionError("worker admission was unbounded")
            yield task.deferLater(reactor, 0.08, lambda: None)
            assert len(ticks) >= 3 and not first.called
            lock.rollback()
        changed = yield first
        assert changed["is_temporary"] == 1
        # close drains even when an observer cancels its own wait.
        cancelled_close = worker.close()
        cancelled_close.cancel()
        try:
            yield cancelled_close
        except defer.CancelledError:
            pass
        yield worker.close()
        assert worker.pending == 0
        assert not any(
            t.name.startswith("PoolThread-stream-sqlite") for t in threading.enumerate()
        )
        # Verify SQLite data through a newly started worker, not on reactor.
        second = DatabaseWorker(reactor, Repository(worker.repository.path))
        try:
            assert (yield second.call("list_viewed", rid)) == []
            assert (yield second.call("get", rid))["is_temporary"] == 1
            try:
                yield second.call("set_viewed", rid, 0, 0)
            except ValueError:
                pass
            else:
                raise AssertionError("worker swallowed an operation failure")
        finally:
            yield second.close()
        barrier = CommitBarrierRepository(worker.repository.path)
        third = DatabaseWorker(reactor, barrier)
        try:
            committed = third.call("set_settings", {"max_temporary_torrents": 9})
            while not barrier.committed.is_set():
                yield task.deferLater(reactor, 0.005, lambda: None)
            committed.cancel()
            try:
                yield committed
            except defer.CancelledError:
                pass
            else:
                raise AssertionError("running cancellation did not fire")
            drained = third.close()
            assert third.pending == 1 and not drained.called
            barrier.release.set()
            yield drained
        finally:
            barrier.release.set()
            yield third.close()
        fourth = DatabaseWorker(reactor, Repository(worker.repository.path))
        try:
            assert (yield fourth.call("get_settings"))["max_temporary_torrents"] == 9
        finally:
            yield fourth.close()
        try:
            yield worker.call("get", rid)
        except WorkerUnavailable:
            pass
        else:
            raise AssertionError("closed worker accepted an operation")
    finally:
        ticker.stop()
        yield worker.close()
    print(
        "DB WORKER: bounded queue, cancel, WAL lock, reactor ticks, drain/join passed"
    )


if __name__ == "__main__":
    import sys

    task.react(scenario, [sys.argv[1]])
