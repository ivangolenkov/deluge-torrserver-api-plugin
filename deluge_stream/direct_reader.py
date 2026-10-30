"""Bounded worker-only reads of native-verified files, with safe descriptor opens."""

import errno
import os
import stat
from pathlib import PurePosixPath

from twisted.internet import defer, threads
from twisted.python.failure import Failure
from twisted.python.threadpool import ThreadPool


def open_payload(root, file):
    root, relative = PurePosixPath(root), PurePosixPath(file.path)
    if (
        not root.is_absolute()
        or relative.is_absolute()
        or ".." in (*root.parts, *relative.parts)
        or not relative.parts
        or "\\" in file.path
        or len(root.parts) + len(relative.parts) > 128
    ):
        raise ValueError("unsafe_paths")
    descriptor = None
    try:
        directory_flags = (
            getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        descriptor = os.open("/", directory_flags)
        parts = (*root.parts[1:], *relative.parts)
        for part in parts[:-1]:
            following = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        following = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
        )
        os.close(descriptor)
        descriptor = following
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("storage_error")
        result, descriptor = descriptor, None
        return result, (info.st_dev, info.st_ino)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError("unsafe_paths") from None
        raise RuntimeError(
            "disk_full" if exc.errno == errno.ENOSPC else "storage_error"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def identity(root, file):
    descriptor, value = open_payload(root, file)
    os.close(descriptor)
    return value


def read_block(root, file, expected, position, count):
    descriptor, value = open_payload(root, file)
    try:
        if value != expected:
            raise RuntimeError("operation_conflict")
        if not 0 <= position < position + count <= file.size:
            raise ValueError("invalid_range")
        try:
            data = os.pread(descriptor, count, position)
        except OSError as exc:
            raise RuntimeError(
                "disk_full" if exc.errno == errno.ENOSPC else "storage_error"
            ) from None
        if len(data) != count:
            raise RuntimeError(
                "storage_error"
            )  # Never complete a truncated response/ZIP.
        return data
    finally:
        os.close(descriptor)


class DirectReader:
    BLOCK_SIZE = 256 * 1024
    CAPACITY = 16

    def __init__(self, engine):
        self.engine = engine
        self.pending = 0
        self.closed = False
        self.drained = None
        self.stopping = False
        self.pool = ThreadPool(minthreads=0, maxthreads=2, name="stream-files")
        self.pool.start()

    def submit(self, session, operation, *args, charge=0, start=None):
        if self.closed or self.pending >= self.CAPACITY:
            return defer.fail(RuntimeError("transfer_limit"))
        budget = self.engine.policy.config["payload_buffer_budget_mib"] * 1024**2
        if self.engine.reserved + charge > budget:
            return defer.fail(RuntimeError("buffer_limit"))
        state = {"cancelled": False}
        result = defer.Deferred(lambda _: state.update(cancelled=True))
        file = session.file
        self.pending += 1
        self.engine.reserved += charge

        def run():
            return None if state["cancelled"] else operation(*args)

        def complete(value):
            self.pending -= 1
            if not result.called and not isinstance(value, Failure):
                try:
                    self.engine.adapter.check(session)
                    if session.file != file:
                        raise RuntimeError("operation_conflict")
                except Exception:
                    value = Failure()
            if result.called or isinstance(value, Failure):
                self.engine.reserved -= charge
                if not result.called:
                    result.errback(value)
            elif start is None:
                result.callback(value)
            else:
                from .streaming import Chunk

                result.callback(Chunk(self.engine, value, charge, start=start))
            if self.closed and not self.pending:
                self._stop()
            return None

        threads.deferToThreadPool(self.engine.reactor, self.pool, run).addBoth(complete)
        return result

    def close(self):
        if self.drained is None:
            self.closed = True
            self.drained = defer.Deferred()
            if not self.pending:
                self._stop()
        observer = defer.Deferred()

        def notify(value):
            if not observer.called:
                observer.callback(value)
            return value

        self.drained.addBoth(notify)
        return observer

    def _stop(self):
        if not self.stopping:
            self.stopping = True
            threads.deferToThread(self.pool.stop).chainDeferred(self.drained)
