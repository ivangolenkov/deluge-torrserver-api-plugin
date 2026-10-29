"""All access to Deluge internals stays in this adapter (reactor thread)."""

import deluge.component as component
from twisted.internet import defer

from .layout import FileSpan, Layout


def snapshot_layout(info, *, compute_edges=True):
    storage = info.files()
    files = []
    first_last = set()
    for index in range(storage.num_files()):
        size = storage.file_size(index)
        files.append(
            FileSpan(
                index,
                storage.file_path(index),
                storage.file_offset(index),
                size,
                bool(storage.file_flags(index) & storage.flag_pad_file),
            )
        )
        if not compute_edges:
            continue
        # Same ranges as Deluge 2.2.0 first/last mode, including metadata pads.
        edge = int(0.02 * size)
        first_last.update(
            range(
                info.map_file(index, 0, 0).piece,
                info.map_file(index, edge, 0).piece + 1,
            )
        )
        first_last.update(
            range(
                info.map_file(index, size - edge, 0).piece,
                info.map_file(index, max(size - 1, 0), 0).piece + 1,
            )
        )
    return Layout(
        info.piece_length(),
        info.num_pieces(),
        tuple(files),
        frozenset(piece for piece in first_last if 0 <= piece < info.num_pieces()),
    )


def hash_aliases(info):
    hashes = info.info_hashes()
    aliases = {}
    if hashes.has_v1():
        aliases["v1"] = str(hashes.v1)
    if hashes.has_v2():
        aliases["v2"] = str(hashes.v2)
    return aliases


def deregister_rpc(owner):
    """2.2.0 deregister_object mutates a live dict iterator; detach owned methods.

    This does not replace/patch the Deluge method. Keep this version-specific
    access here, and never remove another plugin's exports.
    """
    methods = component.get("RPCServer").factory.methods
    for key, method in list(methods.items()):
        if getattr(method, "__self__", None) is owner:
            del methods[key]


class PieceAdapter:
    MAX_PIECE = 4 * 1024 * 1024

    def __init__(self, reactor):
        self.reactor = reactor
        self.manager = component.get("TorrentManager")
        self.alerts = component.get("AlertManager")
        self.events = component.get("EventManager")
        self.pending = None
        self.closed = False
        self.alerts.register_handler("read_piece_alert", self._on_read)
        self.events.register_event_handler("TorrentRemovedEvent", self._on_remove)

    def torrent(self, torrent_id):
        return self.manager.torrents[torrent_id]

    def file_spec(self, torrent_id, file_id):
        torrent = self.torrent(torrent_id)
        info = torrent.handle.torrent_file()
        files = info.files()
        if not 0 <= file_id < files.num_files() or (
            files.file_flags(file_id) & files.flag_pad_file
        ):
            raise ValueError("invalid internal file index")
        return (
            torrent,
            files.file_size(file_id),
            files.file_offset(file_id),
            info.piece_length(),
        )

    def read(self, torrent_id, piece, expected):
        if self.closed or self.pending is not None:
            return defer.fail(RuntimeError("reader unavailable"))
        torrent = self.torrent(torrent_id)
        if torrent is not expected:
            return defer.fail(RuntimeError("torrent instance changed"))
        info = torrent.handle.torrent_file()
        if info.piece_size(piece) > self.MAX_PIECE:
            return defer.fail(RuntimeError("piece exceeds prototype budget"))
        if not torrent.handle.have_piece(piece):
            reason = self._blocked_reason(torrent, piece)
            if reason:
                return defer.fail(RuntimeError(reason))
        d = defer.Deferred(lambda _: self._clear())
        self.pending = (torrent_id, torrent, piece, d)
        # Deliberately no QoS here: the phase-0 experiment found that deadlines
        # leave persistent piece priorities on 2.0.8. Wait for normal downloading.
        self._poll()
        d.addTimeout(15, self.reactor)
        return d

    def _poll(self):
        if self.pending is None:
            return
        torrent_id, torrent, piece, d = self.pending
        if self.manager.torrents.get(torrent_id) is not torrent:
            self._clear()
            d.errback(RuntimeError("torrent instance changed"))
        elif torrent.handle.have_piece(piece):
            torrent.handle.read_piece(piece)
        else:
            reason = self._blocked_reason(torrent, piece)
            if reason:
                self._clear()
                d.errback(RuntimeError(reason))
            else:
                self.poll = self.reactor.callLater(0.05, self._poll)

    def _blocked_reason(self, torrent, piece):
        if self.manager.session.is_paused():
            return "session paused"
        status = torrent.handle.status()
        if status.paused:
            return "torrent queued" if status.auto_managed else "torrent paused"
        if torrent.handle.piece_priority(piece) == 0:
            return "piece skipped"
        return None

    def _clear(self):
        self.pending = None
        poll = getattr(self, "poll", None)
        if poll is not None and poll.active():
            poll.cancel()

    def _on_read(self, alert):
        if self.pending is None:
            return
        torrent_id, torrent, piece, d = self.pending
        if alert.handle != torrent.handle or alert.piece != piece:
            return
        current = self.manager.torrents.get(torrent_id) is torrent
        # Copy during the AlertManager callback; never retain alert-owned buffers.
        data = bytes(alert.buffer) if alert.error.value() == 0 else None
        self._clear()
        if not current or data is None:
            d.errback(RuntimeError("stale instance or piece read error"))
        else:
            d.callback(data)

    def _on_remove(self, torrent_id):
        # EventManager 2.2.0 is synchronous, but its events carry only a hash.
        # Refuse stale/replayed callbacks too, rather than cancelling a new reader.
        if (
            self.pending
            and self.pending[0] == torrent_id
            and self.manager.torrents.get(torrent_id) is not self.pending[1]
        ):
            self.pending[3].cancel()

    def close(self):
        self.closed = True
        if self.pending is not None:
            self.pending[3].cancel()
        self.alerts.deregister_handler(self._on_read)
        self.events.deregister_event_handler("TorrentRemovedEvent", self._on_remove)
