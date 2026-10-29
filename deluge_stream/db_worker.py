"""Single-thread SQLite dispatcher with bounded admission and drain-on-close.

Calls/cancellation/close run on the reactor. Cancelling a running Deferred
discards its result, not its transaction: a committed policy cannot be undone
by a disconnected RPC caller. Capacity is held until actual work completes.
"""

from twisted.internet import defer, threads
from twisted.python.threadpool import ThreadPool


class WorkerUnavailable(RuntimeError):
    pass


class DatabaseWorker:
    def __init__(self, reactor, repository, *, capacity=32):
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("invalid DB worker capacity")
        self.reactor = reactor
        self.repository = repository
        self.capacity = capacity
        self.pending = 0
        self.closed = False
        self._closing = None
        self._stopping = False
        self._pool = ThreadPool(minthreads=0, maxthreads=1, name="stream-sqlite")
        self._pool.start()

    def call(self, method, *args, **kwargs):
        if self.closed or self.pending >= self.capacity:
            return defer.fail(WorkerUnavailable("database worker unavailable"))
        if method.startswith("_"):
            return defer.fail(ValueError("private database method"))
        operation = getattr(self.repository, method, None)
        if not callable(operation):
            return defer.fail(ValueError("unknown database method"))
        return self.submit(operation, *args, **kwargs)

    def submit(self, operation, *args, **kwargs):
        """Internal I/O admission; never expose arbitrary callables over RPC."""
        if self.closed or self.pending >= self.capacity:
            return defer.fail(WorkerUnavailable("database worker unavailable"))
        state = {"cancelled": False}
        result = defer.Deferred(lambda _: state.update(cancelled=True))
        self.pending += 1

        def run():
            # Cancellation before execution skips queued side effects. Reactor
            # cancellation after this point cannot interrupt a SQLite commit.
            if state["cancelled"]:
                return None
            return operation(*args, **kwargs)

        work = threads.deferToThreadPool(self.reactor, self._pool, run)

        def complete(value):
            self.pending -= 1
            if not result.called:
                result.callback(value)
            if self.closed and not self.pending:
                self._stop()
            # Consume worker failures even when the RPC Deferred was cancelled.
            return None

        work.addBoth(complete)
        return result

    def close(self):
        if self._closing is None:
            self.closed = True
            self._closing = defer.Deferred()
            if not self.pending:
                self._stop()
        # Separate observers: cancellation of one must not cancel drain/join.
        observer = defer.Deferred()

        def notify(value):
            if not observer.called:
                observer.callback(value)
            return value

        self._closing.addBoth(notify)
        return observer

    def _stop(self):
        if self._stopping:
            return
        self._stopping = True
        # Joining a worker on the reactor is blocking even after its last result
        # was delivered. Do the join in Twisted's shared worker pool instead.
        joined = threads.deferToThread(self._pool.stop)
        joined.chainDeferred(self._closing)
