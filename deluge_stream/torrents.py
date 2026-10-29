"""TorrServer operations over the authorized policy/native catalogue, not an engine."""

from pathlib import PurePosixPath

from twisted.internet import defer, task

from .adapter import snapshot_layout
from .repository import METADATA_FIELDS, encode
from .sources import Sources, instance_directory, parse_source


class Torrents:
    def __init__(self, reactor, policy, streams):
        self.reactor, self.policy, self.streams = reactor, policy, streams
        self.sources = Sources(reactor, lambda: policy.config)
        self.add_lock = defer.DeferredLock()
        self.adding = 0
        self.active = set()
        self.closed = False

    def actor(self):
        owner = self.policy.config["torrent_owner"]
        if not owner or not self.policy.adapter.owner_exists(owner):
            raise RuntimeError("owner_not_configured")
        return owner, False

    def find(self, alias, *, write=False):
        if not isinstance(alias, str) or len(alias) not in (40, 64):
            raise ValueError("torrent_not_found")
        s = self.policy
        actor = self.actor()
        if not s.ready or s.closed:
            raise RuntimeError("operation_conflict")
        if s.recovery_required:
            raise RuntimeError("recovery_required")
        for tid, _, _, aliases, _ in s.adapter.catalogue():
            row = s.rows.get(tid)
            if (
                alias.lower() not in (tid, *aliases)
                or row is None
                or not row["published"]
            ):
                continue
            s._record(tid, row["record_id"], actor, write=write)
            if row["record_id"] in s.transfers.blocked:
                raise RuntimeError("operation_conflict")
            return row
        raise ValueError("torrent_not_found")

    def published(self):
        self.actor()
        if not self.policy.ready:
            raise RuntimeError("operation_conflict")
        if self.policy.recovery_required:
            return []
        return [
            row
            for tid, row in self.policy.rows.items()
            if row["published"]
            and self.policy.adapter.access(tid, self.actor())
            and row["record_id"] not in self.policy.transfers.blocked
        ]

    def mapping(self, row):
        _, info, _ = self.policy.adapter.native_status(row["torrent_id"])
        if info is None:
            raise RuntimeError("metadata_pending")
        if info.num_files() > 10000:
            raise ValueError("path_check_limit")
        layout = snapshot_layout(info, compute_edges=False)
        if sum(len(file.path.encode()) for file in layout.files) > 2 * 1024**2:
            raise ValueError("path_check_limit")
        for file in layout.payload().values():
            if (
                PurePosixPath(file.path).is_absolute()
                or ".." in PurePosixPath(file.path).parts
                or "\\" in file.path
            ):
                raise ValueError("unsafe_paths")
        return layout

    @defer.inlineCallbacks
    def _ready(self, tid=None):
        for _ in range(200):
            if self.closed or self.policy.closed:
                raise RuntimeError("operation_conflict")
            if self.policy.ready and (tid is None or tid in self.policy.rows):
                return
            yield self.policy._sync()
            if self.policy.ready and (tid is None or tid in self.policy.rows):
                return
            yield task.deferLater(self.reactor, 0.01, lambda: None)
        raise RuntimeError("operation_conflict")

    def filled(self, row, file_id, position=0):
        if type(file_id) is not int:
            raise ValueError("invalid_file_id")
        layout = self.mapping(row)
        file = layout.payload().get(file_id)
        if file is None:
            raise ValueError("invalid_file_id")
        if type(position) is not int or not 0 <= position <= file.size:
            raise ValueError("invalid_request")
        start = file.offset + position
        stop = min(
            file.offset + file.size,
            start + self.policy.config["initial_prebuffer_mib"] * 1024**2,
        )
        first, last = (
            start // layout.piece_length,
            (stop - 1) // layout.piece_length + 1,
        )
        if last - first > self.streams.MAX_DEADLINES:
            raise RuntimeError("deadline_limit")
        pieces, filled, contiguous = {}, 0, True
        for piece in range(first, last) if stop > start else ():
            size = min(stop, (piece + 1) * layout.piece_length) - max(
                start, piece * layout.piece_length
            )
            ready, priority, piece_size = self.policy.adapter.piece_status(
                row["torrent_id"], piece
            )
            pieces[str(piece)] = {
                "Id": piece,
                "Length": piece_size,
                "Size": size if ready else 0,
                "Completed": ready,
                "Priority": priority,
            }
            if contiguous and ready:
                filled += size
            else:
                contiguous = False
        return stop - start, filled, pieces

    @defer.inlineCallbacks
    def dto(self, row, file_id=1):
        metadata = yield self.policy.worker.call("get_metadata", row["record_id"])
        yield self._ready()
        current = self.find(row["torrent_id"])
        if current["record_id"] != row["record_id"]:
            raise RuntimeError("operation_conflict")
        _, info, status = self.policy.adapter.native_status(row["torrent_id"])
        files = []
        loaded_size = 0
        preload_size, filled = 0, 0
        if info is not None:
            layout = self.mapping(row)
            files = [
                {"id": i, "path": file.path, "length": file.size, "size": file.size}
                for i, file in layout.payload().items()
            ]
            progress = self.policy.adapter.native_progress(row["torrent_id"])
            loaded_size = sum(
                progress[file.index] for file in layout.payload().values()
            )
            if file_id in layout.payload():
                preload_size, filled, _ = self.filled(row, file_id)
        result = {
            "hash": row["torrent_id"],
            "title": metadata.get("title", status.name),
            "name": status.name,
            "poster": metadata.get("poster", ""),
            "category": metadata.get("category", ""),
            "data": metadata.get("data", ""),
            "timestamp": row["created_at"],
            "stat": 3 if info else 1,
            "stat_string": "Torrent working" if info else "Torrent added",
            "torrent_size": sum(file["length"] for file in files),
            "loaded_size": loaded_size,
            "preload_size": preload_size,
            "preloaded_bytes": filled,
            "download_speed": status.download_payload_rate,
            "upload_speed": status.upload_payload_rate,
            "active_peers": status.num_peers,
            "total_peers": status.num_peers,
            "connected_seeders": status.num_seeds,
            "file_stats": files,
            "metadata_pending": info is None,
            "save_to_db": not bool(row["is_temporary"]),
        }
        return result

    def add(self, source, save, metadata):
        if self.closed or self.adding >= 4:
            return defer.fail(RuntimeError("source_limit"))
        self.adding += 1
        work = self._add(source, save, metadata)
        self.active.add(work)
        observer = defer.Deferred()

        def done(result):
            self.adding -= 1
            self.active.discard(work)
            if not observer.called:
                observer.callback(result)
            return None

        work.addBoth(done)
        return observer

    @defer.inlineCallbacks
    def _add(self, source, save, metadata):
        if type(save) is not bool:
            raise ValueError("invalid_request")
        actor = self.actor()
        if (
            not isinstance(metadata, dict)
            or metadata.keys() - METADATA_FIELDS
            or any(not isinstance(value, str) for value in metadata.values())
        ):
            raise ValueError("invalid_request")
        encode(metadata)
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            source = yield self.sources.load(source)
        source, aliases = yield self.policy.worker.submit(parse_source, source)
        return (
            yield self.add_lock.run(
                self._commit_add, source, aliases, save, metadata, actor
            )
        )

    @defer.inlineCallbacks
    def _commit_add(self, source, aliases, save, metadata, actor):
        s = self.policy
        if actor != self.actor():
            raise RuntimeError("operation_conflict")
        if self.closed or s.closed or not s.ready or s.recovery_required:
            raise RuntimeError("operation_conflict")
        existing = next(
            (
                entry[0]
                for entry in s.adapter.catalogue()
                if set(aliases) & {entry[0], *entry[3]}
            ),
            None,
        )
        if existing is not None:
            row = s.rows[existing]
            yield s.mutate(existing, row["record_id"], actor, save_to_db=save)
            yield self.metadata(existing, metadata)
            return (yield self.dto(self.find(existing)))
        directory = yield s.worker.submit(
            instance_directory,
            s.config["download_root"] or s.adapter.default_download_root(),
        )
        token = yield s.worker.call("begin_source_add", actor[0], aliases[0], directory)
        try:
            if s.closed or actor != self.actor() or s.recovery_required:
                raise RuntimeError("operation_conflict")
            tid = s.adapter.add_source(source, actor[0], directory)
            if not tid:
                raise RuntimeError("add_failed")
            # Reconcile is outside the policy lock: native add emits events.
            yield self._ready(tid)
            row = s._record(tid, s.rows[tid]["record_id"], actor, write=True)
            if self.closed or actor != self.actor():
                raise RuntimeError("operation_conflict")
            yield s.admit_source(
                tid, row["record_id"], actor, directory, token, save, metadata
            )
            yield s.worker.call("finish_source_add", token, "done")
            yield self._ready(tid)
            return (yield self.dto(self.find(tid)))
        except BaseException:
            yield s.worker.call("finish_source_add", token, "failed")
            raise

    def metadata(self, alias, changes):
        return self.policy.lock.run(self._metadata, alias, changes)

    @defer.inlineCallbacks
    def _metadata(self, alias, changes):
        row = self.find(alias, write=True)
        yield self.policy.worker.call("set_metadata", row["record_id"], changes)
        if self.find(alias, write=True)["record_id"] != row["record_id"]:
            raise RuntimeError("operation_conflict")

    def rem(self, alias):
        def remove():
            row = self.find(alias, write=True)
            if self.policy.transfers.status(row["record_id"])["cleanup_protected"]:
                raise RuntimeError("operation_conflict")
            if not self.policy.adapter.remove_record(row["torrent_id"]):
                raise RuntimeError("operation_conflict")

        return self.policy.lock.run(remove)

    @defer.inlineCallbacks
    def close(self):
        self.closed = True
        self.sources.close()
        yield defer.DeferredList(tuple(self.active), consumeErrors=True)
