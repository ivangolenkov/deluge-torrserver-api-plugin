"""Reactor coordinator: bounded DB work, native events and epoch-guarded policy."""

import uuid

from twisted.internet import defer, task

from .config import validate
from .db_worker import DatabaseWorker
from .path_policy import validate_temporary_paths
from .repository import MAX_TEXT, METADATA_FIELDS, Repository, valid_alias
from .retention import Retention
from .transfers import TransferRegistry


class PolicyService:
    def __init__(self, reactor, adapter_factory):
        self.reactor = reactor
        self.epoch = 0
        self.rows = {}
        self.ready = False
        self.closed = False
        self.quiescing = False
        self.error = None
        self.scheduled = None
        self.syncing = False
        self.next_retry = 0
        self.lock = defer.DeferredLock()
        self.operations = 0
        self.recovery_required = False
        self.recovery_token = str(uuid.uuid4())
        self.adapter = adapter_factory(self.changed)
        self.worker = DatabaseWorker(reactor, Repository(self.adapter.database_path()))
        self.retention = Retention(self)
        self.transfers = TransferRegistry(
            reactor, lambda: self.config, self.retention.wake
        )
        self.retention_recovered = False
        self.fields = {
            "stream_record_id": lambda tid: self.status_field(tid, "record_id", ""),
            "stream_is_temporary": lambda tid: bool(
                self.status_field(tid, "is_temporary", 0)
            ),
            "stream_published": lambda tid: bool(self.status_field(tid, "published", 0))
            and not self.recovery_required,
            "stream_fifo_sequence": lambda tid: self.status_field(
                tid, "fifo_sequence", None
            ),
            "stream_recovery_required": lambda tid: self.recovery_required,
            "stream_active_requests": lambda tid: self.transfer_status(tid)[
                "active_stream_requests"
            ],
            "stream_cleanup_protected": lambda tid: self.transfer_status(tid)[
                "cleanup_protected"
            ],
            "stream_waiting_pieces": lambda tid: self.streaming.status(
                self.rows.get(tid, {}).get("record_id", "")
            )["waiting_pieces"]
            if hasattr(self, "streaming")
            else 0,
        }

    @defer.inlineCallbacks
    def open(self):
        yield self.worker.call("initialize")
        self.recovery_required = yield self.worker.call("begin_run")
        self.config = yield self.worker.call("get_settings")
        self.adapter.register_status(self.fields)
        if self.adapter.session_ready:
            yield self._sync()

    def status_field(self, tid, name, default):
        return self.rows.get(tid, {}).get(name, default) if self.ready else default

    def changed(self):
        self.epoch += 1
        self.recovery_token = str(uuid.uuid4())
        self.ready = False
        if not self.closed and self.scheduled is None and not self.syncing:
            self.scheduled = self.reactor.callLater(0, self._scheduled_sync)

    def _scheduled_sync(self):
        self.scheduled = None
        if not self.closed:
            self._sync().addErrback(self._failed)

    def _failed(self, failure):
        self.ready = False
        self.error = "storage_error"
        self.next_retry = self.reactor.seconds() + 5
        return None

    @defer.inlineCallbacks
    def _sync(self):
        if (
            self.closed
            or self.syncing
            or not hasattr(self, "config")
            or not self.adapter.session_ready
        ):
            return
        self.syncing = True
        yield self.lock.acquire()
        try:
            while not self.closed:
                epoch = self.epoch
                catalogue = self.adapter.catalogue()
                rows = yield self.worker.call("reconcile", catalogue)
                if self.closed:
                    return
                if epoch == self.epoch and catalogue == self.adapter.catalogue():
                    self.adapter.fresh.clear()
                    self.rows = rows
                    self.transfers.reconcile(
                        {row["record_id"] for row in rows.values()}
                    )
                    if not self.retention_recovered:
                        yield self.retention.recover()
                        self.retention_recovered = True
                        if epoch != self.epoch or catalogue != self.adapter.catalogue():
                            continue
                    self.catalogue_stamp = tuple(entry[:4] for entry in catalogue)
                    self.ready = True
                    self.error = None
                    self.retention.wake()
                    return
                # Do not spin within one reactor turn when events keep arriving.
                yield task.deferLater(self.reactor, 0, lambda: None)
        finally:
            self.lock.release()
            self.syncing = False

    def update(self):
        if (
            not self.closed
            and not self.ready
            and self.error is not None
            and self.reactor.seconds() >= self.next_retry
            and not self.syncing
        ):
            self.next_retry = self.reactor.seconds() + 5
            self.changed()
        if not self.closed and self.ready:
            try:
                if (
                    tuple(entry[:4] for entry in self.adapter.catalogue())
                    != self.catalogue_stamp
                ):
                    self.changed()
            except Exception:
                self.ready = False
                self.error = "storage_error"

    def _record(self, tid, rid, actor, *, write=False):
        if self.closed or self.quiescing or not self.ready:
            raise RuntimeError(self.error or "operation_conflict")
        if not self.adapter.access(tid, actor, write=write):
            raise ValueError("torrent_not_found")
        row = self.rows.get(tid)
        # A stale caller does not make a valid live catalogue stale. In
        # particular, an old editor must not force reconciliation of a re-add.
        if row is not None and row["record_id"] != rid:
            raise ValueError("operation_conflict")
        live = next(
            (entry for entry in self.adapter.catalogue() if entry[0] == tid), None
        )
        if (
            row is None
            or live is None
            or row["instance_key"] != live[1]
            or row["owner"] != live[2]
        ):
            self.changed()
            raise ValueError("operation_conflict")
        return row

    def _still_current(self, epoch, catalogue, plan=None):
        try:
            return (
                not self.closed
                and not self.quiescing
                and epoch == self.epoch
                and catalogue == self.adapter.catalogue()
                and (plan is None or plan == self.adapter.deletion_catalogue())
            )
        except (KeyError, ValueError, RuntimeError):
            return False

    def get_status(self, tids, actor):
        if not isinstance(tids, tuple | list) or len(tids) > 128:
            raise ValueError("invalid_request")
        if not self.ready:
            raise RuntimeError(self.error or "operation_conflict")
        result = {}
        for tid in tids:
            if tid in self.rows and self.adapter.access(tid, actor):
                row = self._record(tid, self.rows[tid]["record_id"], actor)
                result[tid] = self._format(row)
        return result

    def _format(self, row):
        result = {
            key: row[key]
            for key in ("record_id", "is_temporary", "fifo_sequence", "revision")
        }
        result.update(
            published=bool(row["published"]) and not self.recovery_required,
            published_requested=bool(row["published"]),
            recovery_required=self.recovery_required,
            **self.transfers.status(row["record_id"]),
            cleanup_reason=self.retention.reason,
        )
        if hasattr(self, "streaming"):
            result.update(self.streaming.status(row["record_id"]))
        return result

    def mutate(
        self, tid, rid, actor, *, temporary=None, published=None, save_to_db=None
    ):
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        d = self.lock.run(
            self._mutate,
            tid,
            rid,
            actor,
            temporary=temporary,
            published=published,
            save_to_db=save_to_db,
        )
        return self._observe_operation(d)

    def publish_existing(self, alias, save_to_db, actor):
        valid_alias(alias)
        if type(save_to_db) is not bool:
            raise ValueError("invalid_request")
        matches = [
            entry[0]
            for entry in self.adapter.catalogue()
            if alias == entry[0] or alias in entry[3]
        ]
        if len(matches) != 1 or matches[0] not in self.rows:
            raise ValueError("torrent_not_found")
        tid = matches[0]
        return self.mutate(
            tid, self.rows[tid]["record_id"], actor, save_to_db=save_to_db
        )

    def metadata(self, tid, rid, actor, *, version=None, changes=None):
        # Share bounded admission and the policy lock with cleanup/lifecycle.
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        if (
            not isinstance(tid, str)
            or len(tid) > 64
            or not isinstance(rid, str)
            or len(rid) > 64
        ):
            return defer.fail(ValueError("invalid_request"))
        if changes is not None and (
            not isinstance(changes, dict)
            or changes.keys() - METADATA_FIELDS
            or any(
                not isinstance(value, str) or len(value) > MAX_TEXT
                for value in changes.values()
            )
        ):
            return defer.fail(ValueError("invalid_request"))
        self.operations += 1
        return self._observe_operation(
            self.lock.run(self._metadata, tid, rid, actor, version, changes)
        )

    @defer.inlineCallbacks
    def _metadata(self, tid, rid, actor, version, changes):
        # Reading client data also requires owner/admin, not shared access.
        self._record(tid, rid, actor, write=True)
        if rid in self.transfers.blocked:
            raise RuntimeError("operation_conflict")
        epoch, catalogue = self.epoch, self.adapter.catalogue()
        if changes is None:
            result = yield self.worker.call("metadata_snapshot", rid)
        else:
            result = yield self.worker.call("edit_metadata", rid, version, changes)
        self._record(tid, rid, actor, write=True)
        if not self._still_current(epoch, catalogue):
            raise RuntimeError("operation_conflict")
        return dict(result, record_id=rid)

    @defer.inlineCallbacks
    def admit_source(self, tid, rid, actor, directory, token, save, metadata):
        # Private primitive: only a newly created native torrent in the UUID
        # directory of a durable add intent can use metadata-pending admission.
        # Cleanup still requires its ordinary complete native-path preflight.
        yield self.lock.acquire()
        try:
            row = self._record(tid, rid, actor, write=True)
            if self.recovery_required or self.adapter.source_path(tid) != directory:
                raise RuntimeError("operation_conflict")
            updated = yield self.worker.call(
                "commit_source_add", token, rid, row["revision"], save, metadata
            )
            current = next(
                (entry for entry in self.adapter.catalogue() if entry[0] == tid), None
            )
            if (
                self.closed
                or current is None
                or current[1] != row["instance_key"]
                or current[2] != actor[0]
                or self.adapter.source_path(tid) != directory
            ):
                yield self.worker.call(
                    "set_policy", rid, temporary=False, published=False
                )
                self.changed()
                raise RuntimeError("operation_conflict")
            self.rows[tid] = updated
            self.retention.wake()
            return updated
        finally:
            self.lock.release()

    def _observe_operation(self, work):
        # RPC disconnect/cancellation must not cancel the coordinator midway
        # through a committed transaction and skip its cache/epoch checks.
        observer = defer.Deferred()

        def completed(value):
            self.operations -= 1
            if not observer.called:
                observer.callback(value)
            return None

        work.addBoth(completed)
        return observer

    @defer.inlineCallbacks
    def _mutate(self, tid, rid, actor, *, temporary, published, save_to_db):
        row = self._record(tid, rid, actor, write=True)
        if rid in self.transfers.blocked:
            raise RuntimeError("operation_conflict")
        if self.recovery_required and (published is True or save_to_db is not None):
            raise RuntimeError("recovery_required")
        epoch, catalogue = self.epoch, self.adapter.catalogue()
        plan = None
        if temporary is True:
            plan = self.adapter.deletion_catalogue()
            yield self.worker.submit(validate_temporary_paths, tid, plan)
            if not self._still_current(epoch, catalogue, plan):
                raise ValueError("operation_conflict")
        # Owner/admin is rechecked after the filesystem worker and before commit.
        self._record(tid, rid, actor, write=True)
        if save_to_db is None:
            updated = yield self.worker.call(
                "set_policy",
                rid,
                temporary=temporary,
                published=published,
                revision=row["revision"],
            )
        else:
            updated = yield self.worker.call(
                "repeat_add", rid, save_to_db=save_to_db, revision=row["revision"]
            )
        if not self._still_current(epoch, catalogue, plan):
            # Revoke a stale temporary admission;
            # never publish stale worker results to a replacement's cache.
            if temporary is True:
                yield self.worker.call(
                    "set_policy", rid, temporary=False, revision=updated["revision"]
                )
            if not self.closed:
                self.changed()
            raise ValueError("operation_conflict")
        self.rows[tid] = updated
        self.retention.wake()
        return self._format(updated)

    def confirm_recovery(self, token, actor):
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        return self._observe_operation(
            self.lock.run(self._confirm_recovery, token, actor)
        )

    @defer.inlineCallbacks
    def _confirm_recovery(self, token, actor):
        if not actor[0] or not actor[1]:
            raise ValueError("forbidden")
        if not self.ready or token != self.recovery_token:
            raise ValueError("operation_conflict")
        epoch, catalogue = self.epoch, self.adapter.catalogue()
        plan = self.adapter.deletion_catalogue()
        for tid, row in self.rows.items():
            if row["is_temporary"]:
                yield self.worker.submit(validate_temporary_paths, tid, plan)
        if not self._still_current(epoch, catalogue, plan):
            raise ValueError("operation_conflict")
        yield self.worker.call("confirm_recovery")
        if not self._still_current(epoch, catalogue, plan):
            self.recovery_required = True
            yield self.worker.call("require_recovery")
            raise ValueError("operation_conflict")
        self.recovery_required = False
        self.retention.wake()
        return True

    def set_config(self, changes, actor):
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        d = self.lock.run(self._set_config, changes, actor)
        return self._observe_operation(d)

    @defer.inlineCallbacks
    def _set_config(self, changes, actor):
        if not actor[1] or not actor[0]:
            raise ValueError("forbidden")
        target = validate(changes, self.config)
        if target["torrent_owner"] and not self.adapter.owner_exists(
            target["torrent_owner"]
        ):
            raise ValueError("invalid_owner")
        yield self.worker.call("set_settings", changes)
        if self.closed:
            raise RuntimeError("operation_conflict")
        self.config = target
        self.retention.wake()
        return dict(target)

    def transfer_status(self, tid):
        return self.transfers.status(self.rows.get(tid, {}).get("record_id", ""))

    def acquire_transfer(self, tid, rid, actor, kind, cancel=None):
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        return self._observe_operation(
            self.lock.run(self._acquire_transfer, tid, rid, actor, kind, cancel)
        )

    def _acquire_transfer(self, tid, rid, actor, kind, cancel):
        self._record(tid, rid, actor)
        return self.transfers.acquire(rid, kind, cancel)

    def activate_ticket(self, tid, rid, actor, lease, kind):
        def activate():
            self._record(tid, rid, actor)
            if lease.record_id != rid:
                raise ValueError("operation_conflict")
            return self.transfers.activate(lease, kind)

        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        return self._observe_operation(self.lock.run(activate))

    def resolve_removal(self, operation, action, actor):
        if self.operations >= 32 or self.closed:
            return defer.fail(RuntimeError("operation_conflict"))
        self.operations += 1
        return self._observe_operation(
            self.lock.run(self.retention.resolve, operation, action, actor)
        )

    @defer.inlineCallbacks
    def close(self, *, reset):
        self.closed = True
        self.ready = False
        self.retention.stop()
        self.transfers.close()
        if self.scheduled is not None and self.scheduled.active():
            self.scheduled.cancel()
        self.adapter.close(self.fields)
        # Drain all previously admitted policy work before revoking bindings.
        yield self.lock.acquire()
        try:
            if reset:
                yield self.worker.call("reset_bindings")
            elif hasattr(self, "config") and self.adapter.session_ready:
                # The native TorrentManager retains its catalogue during stop.
                # Flush outstanding add/remove observations before the clean mark.
                yield self.worker.call("reconcile", self.adapter.catalogue())
                yield self.worker.call("finish_run")
        finally:
            self.lock.release()
            yield self.worker.close()
            self.rows.clear()
