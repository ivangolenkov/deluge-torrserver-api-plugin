"""Version-specific Deluge catalogue/auth/lifecycle access, reactor only."""

import platform
import sqlite3
import sys
import uuid
from pathlib import Path

import deluge.component as component
import libtorrent as lt
import twisted
from deluge.common import get_version
from deluge.configmanager import get_config_dir

from .adapter import hash_aliases


def check_runtime():
    if (
        platform.system() != "Linux"
        or sys.version_info[:2] != (3, 11)
        or get_version() != "2.2.0"
        or lt.__version__ != "2.0.8.0"
        or twisted.__version__ != "24.11.0"
        or sqlite3.sqlite_version_info < (3, 40, 1)
    ):
        raise RuntimeError("incompatible_runtime")


class PolicyAdapter:
    EVENTS = (
        "TorrentAddedEvent",
        "TorrentRemovedEvent",
        "SessionStartedEvent",
        "TorrentFileRenamedEvent",
        "TorrentFolderRenamedEvent",
    )

    def __init__(self, changed):
        self.manager = component.get("TorrentManager")
        self.events = component.get("EventManager")
        self.plugins = component.get("CorePluginManager")
        self.rpc = component.get("RPCServer")
        self.changed = changed
        self.keys = {}
        self.fresh = set()
        self.session_ready = (
            self.manager._component_state == "Started"
            and not self.manager.torrents_loading
        )
        self.handlers = dict(
            zip(
                self.EVENTS,
                (self._added, self._removed, self._session, self._paths, self._paths),
                strict=True,
            )
        )
        for name, handler in self.handlers.items():
            self.events.register_event_handler(name, handler)

    @staticmethod
    def database_path():
        return Path(get_config_dir()) / "stream.sqlite"

    def daemon_stopping(self):
        return self.plugins._component_state == "Stopping"

    def actor(self):
        # Capture before yielding: RPCServer's current session is reactor-local.
        return self.rpc.get_session_user(), self.rpc.get_session_auth_level() >= 10

    @staticmethod
    def owner_exists(owner):
        # AuthManager's cached accounts, no auth-file I/O on reactor.
        return owner in component.get("AuthManager")._AuthManager__auth

    def _added(self, tid, from_state):
        if not from_state:
            self.fresh.add(tid)
        self.changed()

    def _removed(self, tid):
        # Read live catalogue, not a hash-only delayed removal. A replay must
        # not retire a replacement already present under that hash.
        if tid not in self.manager.torrents:
            self.keys.pop(tid, None)
        self.changed()

    def _session(self):
        self.session_ready = True
        self.changed()

    def _paths(self, *args):
        self.changed()

    def catalogue(self):
        if len(self.manager.torrents) > 4096:
            raise RuntimeError("catalogue_limit")
        result = []
        for tid, torrent in tuple(self.manager.torrents.items()):
            previous = self.keys.get(tid)
            if previous is None or previous[0] is not torrent:
                if previous is not None:
                    self.fresh.add(tid)
                self.keys[tid] = (torrent, str(uuid.uuid4()))
            key = self.keys[tid][1]
            info = torrent.handle.torrent_file()
            aliases = tuple(hash_aliases(info).values()) if info is not None else ()
            result.append(
                (tid, key, torrent.options["owner"], aliases, tid in self.fresh)
            )
        return tuple(sorted(result))

    def access(self, tid, actor, *, write=False):
        torrent = self.manager.torrents.get(tid)
        if torrent is None:
            return False
        user, admin = actor
        return bool(user) and (
            admin
            or torrent.options["owner"] == user
            or (not write and torrent.options["shared"])
        )

    def payload_plan(self):
        result = []
        total = 0
        for tid, torrent in tuple(self.manager.torrents.items()):
            info = torrent.handle.torrent_file()
            if info is None:
                raise RuntimeError("metadata_pending")
            if torrent.moving_storage_dest_path or torrent.forcing_recheck:
                raise RuntimeError("operation_conflict")
            files = info.files()
            total += files.num_files()
            if total > 10000:
                raise RuntimeError("path_check_limit")
            names = tuple(
                files.file_path(i)
                for i in range(files.num_files())
                if not files.file_flags(i) & files.flag_pad_file
            )
            result.append((tid, torrent.handle.save_path(), names))
        return tuple(sorted(result))

    def register_status(self, fields):
        for name, function in fields.items():
            self.plugins.register_status_field(name, function)

    def close(self, fields=()):
        for name, handler in self.handlers.items():
            self.events.deregister_event_handler(name, handler)
        for name in fields:
            self.plugins.deregister_status_field(name)
        self.keys.clear()
