"""Version-specific native streaming access. Reactor only; no payload writes."""

from pathlib import PurePosixPath

from twisted.internet import defer, task

from .adapter import snapshot_layout
from .layout import FileSpan, Layout


class NativeStreams:
    supports_direct = True

    def __init__(self, policy):
        self.policy = policy
        self.manager = policy.adapter.manager
        self.alerts = policy.adapter.alerts
        self.invalidate = None

    def register(self, handler):
        self.alerts.register_handler("read_piece_alert", handler)
        self.alerts.register_handler("state_changed_alert", self._state_changed)
        self.alerts.register_handler("storage_moved_alert", self._paths_changed)
        self.alerts.register_handler("file_renamed_alert", self._paths_changed)

    def unregister(self, handler):
        self.alerts.deregister_handler(handler)
        self.alerts.deregister_handler(self._state_changed)
        self.alerts.deregister_handler(self._paths_changed)
        self.invalidate = None

    def _state_changed(self, alert):
        if alert.state in (1, 7) and self.invalidate is not None:
            self.invalidate(alert)

    def _paths_changed(self, alert):
        if self.invalidate is not None:
            self.invalidate(alert)

    @staticmethod
    def alert_piece(alert):
        return alert.piece

    @staticmethod
    def alert_data(alert):
        error = alert.error.value()
        if error:
            raise RuntimeError("disk_full" if error == 28 else "storage_error")
        return bytes(alert.buffer)

    def open(self, tid, file_id):
        torrent = self.manager.torrents.get(tid)
        if torrent is None:
            raise ValueError("torrent_not_found")
        info = torrent.handle.torrent_file()
        if info is None:
            raise RuntimeError("metadata_pending")
        if info.num_files() > 10000:
            raise ValueError("path_check_limit")
        layout = snapshot_layout(info, compute_edges=False)
        if sum(len(file.path.encode()) for file in layout.files) > 2 * 1024**2:
            raise ValueError("path_check_limit")
        file = layout.payload().get(file_id)
        if file is None:
            raise ValueError("invalid_file_id")
        path = PurePosixPath(file.path)
        if path.is_absolute() or ".." in path.parts or "\\" in file.path:
            raise ValueError("unsafe_paths")
        if info.files().file_flags(file.index) & info.files().flag_symlink:
            raise ValueError("unsafe_paths")
        return torrent, layout, file, torrent.handle.save_path()

    @defer.inlineCallbacks
    def download_open(self, tid, file_id, reactor):
        torrent = self.manager.torrents.get(tid)
        if torrent is None:
            raise ValueError("torrent_not_found")
        info = torrent.handle.torrent_file()
        if info is None:
            raise RuntimeError("metadata_pending")
        storage = info.files()
        if storage.num_files() > 100000:
            raise ValueError("path_check_limit")
        files, path_bytes = [], 0
        for index in range(storage.num_files()):
            path = storage.file_path(index)
            path_bytes += len(path.encode())
            if path_bytes > 2 * 1024**2:
                raise ValueError("path_check_limit")
            flags = storage.file_flags(index)
            if flags & storage.flag_symlink:
                raise ValueError("unsafe_paths")
            files.append(
                FileSpan(
                    index,
                    path,
                    storage.file_offset(index),
                    storage.file_size(index),
                    bool(flags & storage.flag_pad_file),
                )
            )
            if index % 256 == 255:
                yield task.deferLater(reactor, 0, lambda: None)
        layout = Layout(
            info.piece_length(), info.num_pieces(), tuple(files), frozenset()
        )
        file = layout.payload().get(file_id)
        if file is None:
            raise ValueError("invalid_file_id")
        if self.manager.torrents.get(tid) is not torrent:
            raise RuntimeError("operation_conflict")
        return torrent, layout, file, torrent.handle.save_path()

    def check(self, session):
        s = self.policy
        torrent = self.manager.torrents.get(session.tid)
        row = s.rows.get(session.tid)
        if (
            session.closed
            or s.closed
            or torrent is not session.native
            or row is None
            or row["record_id"] != session.rid
            or not s.adapter.access(session.tid, session.actor)
        ):
            raise RuntimeError("operation_conflict")
        if session.public and (
            s.recovery_required
            or not row["published"]
            or session.actor[0] != s.config["torrent_owner"]
            or not s.adapter.access(session.tid, session.actor)
        ):
            raise RuntimeError("torrent_not_found")
        if not session.public and not s.adapter.download_access(
            session.tid, session.actor
        ):
            raise RuntimeError("torrent_not_found")
        handle = torrent.handle
        status = handle.status()
        if status.errc.value():
            raise RuntimeError(
                "disk_full" if status.errc.value() == 28 else "storage_error"
            )
        if (
            status.moving_storage
            or torrent.forcing_recheck
            or status.state in (1, 7)
            or handle.save_path() != session.path
            or handle.torrent_file().files().file_path(session.file.index)
            != session.file.path
        ):
            raise RuntimeError("operation_conflict")

    def missing_reason(self, session):
        torrent = session.native
        if len(torrent.options["file_priorities"]) <= session.file.index:
            return "metadata_pending"
        if torrent.options["file_priorities"][session.file.index] == 0:
            return "file_skipped"
        status = torrent.handle.status()
        if status.errc.value():
            return "disk_full" if status.errc.value() == 28 else "storage_error"
        if self.manager.session.is_paused():
            return "session_paused"
        if status.paused:
            return "torrent_queued" if status.auto_managed else "torrent_paused"
        return None

    @staticmethod
    def have(session, piece):
        return session.native.handle.have_piece(piece)

    @staticmethod
    def read(session, piece):
        session.native.handle.read_piece(piece)

    @staticmethod
    def piece_size(session, piece):
        return session.native.handle.torrent_file().piece_size(piece)

    @staticmethod
    def matches(native, alert):
        return native.handle == alert.handle

    @staticmethod
    def deadline(native, piece, milliseconds):
        native.handle.set_piece_deadline(piece, milliseconds)

    def reset(self, tid, native, piece):
        if self.manager.torrents.get(tid) is native:
            try:
                native.handle.reset_piece_deadline(piece)
            except RuntimeError:
                pass  # Native removal may already have invalidated the handle.
