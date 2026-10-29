"""Reactor FIFO coordinator; native disk completion is awaited outside policy lock."""

from twisted.internet import defer

from .deletion import build_deletion_plan, verify_absent


class Retention:
    RETRIES = 3
    DELETE_TIMEOUT = 30

    def __init__(self, service):
        self.service = service
        self.timer = None
        self.running = None
        self.attempts = 0
        self.reason = ""
        self.pending = []

    def wake(self):
        self.attempts = 0
        self.schedule(0)

    def schedule(self, delay):
        if (
            self.service.closed
            or getattr(self.service, "quiescing", False)
            or self.running is not None
            or self.timer is not None
        ):
            return
        self.timer = self.service.reactor.callLater(delay, self._start)

    def _start(self):
        self.timer = None
        # Sentinel avoids synchronous-completion assignment races with Clock/fakes.
        self.running = True
        work = self._run()
        self.running = work
        work.addBoth(self._finished)

    def _finished(self, result):
        from twisted.python.failure import Failure

        self.running = None
        if isinstance(result, Failure):
            # A DB error disables the entire policy service, not just this pass.
            self.service._failed(result)
            self.reason = "storage_error"
        if not self.service.closed and self.reason and not self.pending:
            self.attempts += 1
            if self.attempts < self.RETRIES:
                self.schedule(self.service.config["cleanup_retry_seconds"])
        return None

    @defer.inlineCallbacks
    def recover(self):
        s = self.service
        self.pending = yield s.worker.call("pending_removals")
        for entry in self.pending:
            stage = entry["stage"]
            if stage == "manual":
                continue
            if stage == "prepared":
                yield s.worker.call("removal_state", entry["operation_id"], "aborted")
                continue
            # No native deletion replay after restart. Verify only, never unlink.
            live = any(row[0] == entry["torrent_id"] for row in s.adapter.catalogue())
            absent = not live and (yield s.worker.submit(verify_absent, entry["plan"]))
            yield s.worker.call(
                "removal_state",
                entry["operation_id"],
                "completed" if absent else "manual",
                "" if absent else "interrupted",
            )
        self.pending = yield s.worker.call("pending_removals")
        for entry in self.pending:
            s.transfers.blocked.add(entry["record_id"])
        if self.pending:
            self.reason = "manual_resolution_required"

    def _candidate(self):
        s = self.service
        if s.closed or s.quiescing or not s.ready or s.error:
            self.reason = "operation_conflict"
            return None
        if s.recovery_required:
            self.reason = "recovery_required"
            return None
        if self.pending:
            self.reason = "manual_resolution_required"
            return None
        rows = sorted(
            (row for row in s.rows.values() if row["is_temporary"]),
            key=lambda row: row["fifo_sequence"],
        )
        if len(rows) <= s.config["max_temporary_torrents"]:
            self.reason = ""
            return None
        row = rows[0]
        if s.transfers.status(row["record_id"])["cleanup_protected"]:
            self.reason = "protected_oldest"
            return None
        return row

    @defer.inlineCallbacks
    def _prepare(self):
        s = self.service
        row = self._candidate()
        if row is None:
            return None
        tid, rid = row["torrent_id"], row["record_id"]
        epoch, catalogue = s.epoch, s.adapter.catalogue()
        operation = None
        try:
            native_paths = s.adapter.deletion_catalogue()
            plan = yield s.worker.submit(build_deletion_plan, tid, native_paths)
            if (
                not s._still_current(epoch, catalogue)
                or native_paths != s.adapter.deletion_catalogue()
                or self._candidate() != row
            ):
                raise ValueError("operation_conflict")
        except (ValueError, RuntimeError) as exc:
            self.reason = (
                str(exc)
                if str(exc)
                in {
                    "unsafe_paths",
                    "shared_paths",
                    "metadata_pending",
                    "path_check_limit",
                }
                else "operation_conflict"
            )
            return None
        # Serialized with pin/config/admission. No native call before durable intent.
        operation = yield s.worker.call(
            "prepare_removal",
            rid,
            row["revision"],
            row["fifo_sequence"],
            plan,
        )
        s.transfers.blocked.add(rid)
        yield s.worker.call("removal_state", operation, "submitting")
        if (
            not s._still_current(epoch, catalogue)
            or native_paths != s.adapter.deletion_catalogue()
            or self._candidate() != row
        ):
            yield s.worker.call(
                "removal_state", operation, "aborted", "operation_conflict"
            )
            s.transfers.blocked.discard(rid)
            self.reason = "operation_conflict"
            return None
        waiter = s.adapter.watch_deletion(
            tid, row["instance_key"], s.reactor, self.DELETE_TIMEOUT
        )
        try:
            accepted = s.adapter.remove_with_data(tid, row["instance_key"])
        except Exception:
            accepted = False
        if not accepted:
            # Native may have accepted before Deluge encountered an exception.
            # Without a positive completion we never guess that nothing happened.
            if not waiter.called:
                waiter.cancel()
            waiter.addErrback(lambda _: None)
            yield s.worker.call(
                "removal_state", operation, "manual", "native_submission_failed"
            )
            self.pending = yield s.worker.call("pending_removals")
            self.reason = "native_submission_failed"
            return None
        yield s.worker.call("removal_state", operation, "submitted")
        self.reason = "deleting"
        return operation, row, waiter

    @defer.inlineCallbacks
    def _run(self):
        s = self.service
        while not s.closed:
            context = yield s.lock.run(self._prepare)
            if context is None:
                return
            operation, row, waiter = context
            result = yield waiter
            yield s.lock.acquire()
            try:
                same_live = any(
                    item[0] == row["torrent_id"] and item[1] == row["instance_key"]
                    for item in s.adapter.catalogue()
                )
                success = result == "deleted" and not same_live
                yield s.worker.call(
                    "removal_state",
                    operation,
                    "completed" if success else "manual",
                    "" if success else result,
                )
                self.pending = yield s.worker.call("pending_removals")
                if success:
                    s.transfers.blocked.discard(row["record_id"])
                    self.reason = ""
                else:
                    self.reason = "manual_resolution_required"
                    return
            finally:
                s.lock.release()
            if not s.closed:
                yield s._sync()

    def status(self):
        return {
            "reason": self.reason,
            "attempts": self.attempts,
            "pending": [
                {
                    key: entry[key]
                    for key in (
                        "operation_id",
                        "record_id",
                        "torrent_id",
                        "stage",
                        "error",
                    )
                }
                for entry in self.pending
            ],
        }

    @defer.inlineCallbacks
    def resolve(self, operation, action, actor):
        s = self.service
        if not actor[0] or not actor[1]:
            raise ValueError("forbidden")
        if action not in {"verify", "acknowledge"} or s.closed:
            raise ValueError("invalid_request")
        entry = next(
            (item for item in self.pending if item["operation_id"] == operation), None
        )
        if entry is None or entry["stage"] != "manual":
            raise ValueError("operation_conflict")
        if action == "verify":
            if any(row[0] == entry["torrent_id"] for row in s.adapter.catalogue()):
                raise ValueError("operation_conflict")
            if not (yield s.worker.submit(verify_absent, entry["plan"])):
                raise ValueError("operation_conflict")
        yield s.worker.call(
            "removal_state", operation, "completed" if action == "verify" else "aborted"
        )
        s.transfers.blocked.discard(entry["record_id"])
        self.pending = yield s.worker.call("pending_removals")
        self.reason = ""
        self.wake()
        return True

    def stop(self):
        if self.timer is not None and self.timer.active():
            self.timer.cancel()
        self.timer = None
