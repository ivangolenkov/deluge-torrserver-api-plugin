"""One admitted update job per process; blocking work stays in a worker."""

import threading

from twisted.internet import defer, threads

from . import restart
from .updates import ERRORS, UpdateStore, runtime


class UpdateService:
    def __init__(self, reactor, profile, role):
        self.reactor = reactor
        self.role = role
        self.cancelled = threading.Event()
        self.store = UpdateStore(profile, runtime(role), self.cancelled)
        self.busy = False
        self.restarting = False
        self.closing = None
        self.command = None

    def run(self, method, *args):
        if self.busy or self.restarting:
            raise ValueError("update_busy")
        if self.cancelled.is_set():
            raise ValueError("update_cancelled")
        self.busy = True

        def work():
            try:
                # Filesystem launch checks belong in the worker as well.
                self.command = restart.command(self.role, self.store.profile)
                return getattr(self.store, method)(*args)
            except Exception as exc:
                code = str(exc)
                if code not in ERRORS:
                    code = (
                        "update_storage"
                        if isinstance(exc, OSError)
                        else "update_invalid"
                    )
                raise ValueError(code) from None
            finally:
                if self.cancelled.is_set():
                    self.store.clear()

        def finished(result):
            self.busy = False
            if self.closing is not None and not self.closing.called:
                self.closing.callback(None)
            return result

        result = threads.deferToThread(work)
        result.addBoth(finished)
        return result

    def restart(self, token):
        if self.busy or self.restarting:
            raise ValueError("update_busy")
        if not self.store.require(token).get("installed"):
            raise ValueError("update_conflict")
        restart.schedule(self.reactor, self.role, self.command)
        self.restarting = True
        return True

    def close(self):
        self.cancelled.set()
        if self.busy:
            if self.closing is None:
                self.closing = defer.Deferred()
            return self.closing
        return threads.deferToThread(self.store.clear)
