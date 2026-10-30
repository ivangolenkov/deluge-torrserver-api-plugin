"""Installed artifact, real daemon restart/crash, policy RPC and authorization."""

import argparse
import base64
import hashlib
import logging
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

import libtorrent as lt
from deluge.ui.client import client
from twisted.internet import defer, task, threads

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
parser.add_argument("--previous-egg", default="")
args = parser.parse_args()
logging.getLogger("deluge.ui.client").setLevel(logging.CRITICAL)


def db_rows(path, sql, params=()):
    with closing(sqlite3.connect(path, timeout=5)) as db:
        return db.execute(sql, params).fetchall()


class Peer:
    def __init__(self, profile):
        self.profile = profile
        self.password = secrets.token_hex(32)
        self.process = None
        self.log = None
        self.port = None

    @defer.inlineCallbacks
    def start(self, reactor):
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.port = reserve.getsockname()[1]
        self.log = (self.profile / "daemon.log").open("a+")
        self.process = subprocess.Popen(
            [
                shutil.which("deluged"),
                "-d",
                "-c",
                str(self.profile),
                "-p",
                str(self.port),
                "-i",
                "127.0.0.1",
                "-u",
                "127.0.0.1",
                "-L",
                "error",
            ],
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        yield self.login(reactor, "admin")

    @defer.inlineCallbacks
    def login(self, reactor, user):
        if client.connected():
            yield client.disconnect()
        for _ in range(150):
            assert self.process.poll() is None, "daemon exited"
            try:
                yield client.connect("127.0.0.1", self.port, user, self.password)
                return
            except Exception:
                yield task.deferLater(reactor, 0.05, lambda: None)
        raise AssertionError("cannot authenticate isolated daemon")

    @defer.inlineCallbacks
    def stop(self, *, crash=False):
        if client.connected():
            yield client.disconnect()
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGKILL if crash else signal.SIGTERM)
            try:
                code = yield threads.deferToThread(self.process.wait, 20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                yield threads.deferToThread(self.process.wait)
                raise
            assert code == (-signal.SIGKILL if crash else 0), code
        if self.log:
            self.log.close()
            self.log = None


@defer.inlineCallbacks
def status(reactor, tid):
    for _ in range(200):
        try:
            value = yield client.stream.get_status(1, [tid])
            if tid in value:
                return value[tid]
        except Exception:
            pass
        yield task.deferLater(reactor, 0.05, lambda: None)
    raise AssertionError("policy catalogue did not become ready")


@defer.inlineCallbacks
def denied(operation):
    try:
        yield operation
    except Exception:
        return
    raise AssertionError("operation should have been rejected")


def metainfo(payload=b"data"):
    return lt.bencode(
        {
            "info": {
                "name": "fixture.bin",
                "length": len(payload),
                "piece length": 16384,
                "pieces": hashlib.sha1(payload).digest(),
            }
        }
    )


def hybrid_fixture(root):
    (root / "hybrid.bin").write_bytes(b"data")
    storage = lt.file_storage()
    storage.add_file("hybrid.bin", 4)
    creator = lt.create_torrent(storage, 16384)
    lt.set_piece_hashes(creator, str(root))
    raw = lt.bencode(creator.generate())
    info = lt.torrent_info(raw)
    return raw, str(info.info_hashes().v2)


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="stream-policy-", dir=args.profile_root or None
    ) as directory:
        root = Path(directory)
        profile = root / "profile"
        (profile / "plugins").mkdir(parents=True)
        shutil.copy2(args.egg, profile / "plugins")
        downloads = root / "downloads"
        downloads.mkdir()
        sentinel = downloads / "unrelated.bin"
        sentinel.write_bytes(b"keep")
        peer = Peer(profile)
        (profile / "auth").write_text(
            "".join(
                f"{name}:{peer.password}:{level}\n"
                for name, level in (("admin", 10), ("alice", 5), ("bob", 5))
            )
        )
        (profile / "auth").chmod(0o600)
        database = profile / "stream.sqlite"
        raw = metainfo()
        try:
            yield peer.start(reactor)
            assert (yield client.core.enable_plugin("Stream"))
            options = {
                "download_location": str(downloads),
                "add_paused": True,
                "auto_managed": False,
                "owner": "alice",
                "shared": True,
            }
            tid = yield client.core.add_torrent_file(
                "fixture.torrent", base64.b64encode(raw), options
            )
            first = yield status(reactor, tid)
            rid = first["record_id"]
            assert first["is_temporary"] == first["published"] == 0
            native_before = yield client.core.get_torrent_status(
                tid, ["file_priorities", "paused", "download_location"]
            )
            changed = yield client.stream.set_retention(1, tid, rid, True)
            fifo = changed["fifo_sequence"]
            assert fifo > 0
            assert (yield client.stream.set_retention(1, tid, rid, True))[
                "fifo_sequence"
            ] == fifo
            yield client.stream.set_published(1, tid, rid, True)
            repeated = yield client.stream.publish_existing(1, tid, False)
            assert repeated["record_id"] == rid and repeated["fifo_sequence"] == fifo
            assert not (yield client.stream.publish_existing(1, tid, True))[
                "is_temporary"
            ]
            assert not (yield client.stream.publish_existing(1, tid, False))[
                "is_temporary"
            ]
            changed = yield client.stream.set_retention(1, tid, rid, True)
            assert changed["fifo_sequence"] > fifo
            fifo = changed["fifo_sequence"]
            native_after = yield client.core.get_torrent_status(
                tid, ["file_priorities", "paused", "download_location"]
            )
            assert native_before == native_after
            fields = yield client.core.get_torrent_status(
                tid, ["stream_is_temporary", "stream_record_id", "stream_published"]
            )
            assert fields["stream_is_temporary"] and fields["stream_record_id"] == rid
            assert fields["stream_published"]
            config = yield client.stream.set_config(
                1, {"max_temporary_torrents": 2, "torrent_owner": "alice"}
            )
            assert config["max_temporary_torrents"] == 2
            yield denied(client.stream.set_config(1, {"max_temporary_torrents": 0}))
            yield denied(client.stream.set_config(1, {"torrent_owner": "missing"}))
            assert (yield client.stream.get_config(1))["max_temporary_torrents"] == 2

            yield peer.login(reactor, "bob")
            assert (yield status(reactor, tid))["record_id"] == rid  # shared read
            yield denied(client.stream.set_retention(1, tid, rid, False))
            yield denied(client.stream.set_published(1, tid, rid, False))
            yield denied(client.stream.publish_existing(1, tid, False))
            yield denied(client.stream.get_config(1))
            yield peer.login(reactor, "alice")
            yield client.stream.set_retention(1, tid, rid, False)
            next_row = yield client.stream.set_retention(1, tid, rid, True)
            assert next_row["fifo_sequence"] > fifo
            fifo = next_row["fifo_sequence"]
            yield peer.login(reactor, "admin")

            # Native graceful shutdown must preserve temporary/public/FIFO.
            yield peer.stop()
            assert (
                yield threads.deferToThread(
                    db_rows,
                    database,
                    "SELECT is_temporary,published,fifo_sequence FROM torrent_policy",
                )
            ) == [(1, 1, fifo)]
            yield peer.start(reactor)
            assert "Stream" in (yield client.core.get_enabled_plugins()), (
                "native enabled setting lost"
            )
            restored = yield status(reactor, tid)
            assert restored["record_id"] == rid and restored["fifo_sequence"] == fifo
            assert restored["is_temporary"] == 1 and restored["published"]

            yield peer.stop(crash=True)
            yield peer.start(reactor)
            assert "Stream" in (yield client.core.get_enabled_plugins())
            restored = yield status(reactor, tid)
            assert restored["record_id"] == rid and restored["fifo_sequence"] == fifo
            assert restored["is_temporary"] == 1 and restored["recovery_required"]
            assert restored["published_requested"] and not restored["published"]
            yield denied(client.stream.confirm_recovery(1, "stale-token"))
            info = yield client.stream.get_diagnostics(1)
            assert (yield client.stream.confirm_recovery(1, info["recovery_token"]))
            assert (yield status(reactor, tid))["published"]

            hybrid_raw, alias = yield threads.deferToThread(hybrid_fixture, root)
            hybrid_id = yield client.core.add_torrent_file(
                "hybrid.torrent", base64.b64encode(hybrid_raw), options
            )
            hybrid_row = yield status(reactor, hybrid_id)
            before_ids = yield client.core.get_session_state()
            alias_row = yield client.stream.publish_existing(1, alias, False)
            assert alias_row["record_id"] == hybrid_row["record_id"]
            assert not alias_row["is_temporary"] and alias_row["published"]
            assert (yield client.core.get_session_state()) == before_ids
            assert (yield client.core.remove_torrent(hybrid_id, False))
            yield status(reactor, tid)

            # New live instance must not inherit old policy; stale record denied.
            assert (yield client.core.remove_torrent(tid, False))
            tid2 = yield client.core.add_torrent_file(
                "fixture.torrent", base64.b64encode(raw), options
            )
            assert tid2 == tid
            new = yield status(reactor, tid)
            assert (
                new["record_id"] != rid
                and not new["is_temporary"]
                and not new["published"]
            )
            yield denied(client.stream.set_retention(1, tid, rid, True))
            rid = new["record_id"]

            # An overlapping native torrent prevents enabling temporary status.
            overlap = yield client.core.add_torrent_file(
                "other.torrent", base64.b64encode(metainfo(b"other")), options
            )
            yield status(reactor, overlap)
            yield denied(client.stream.set_retention(1, tid, rid, True))
            assert not (yield status(reactor, tid))["is_temporary"]
            assert (yield client.core.remove_torrent(overlap, False))
            yield status(reactor, tid)
            yield client.stream.set_retention(1, tid, rid, True)
            yield client.stream.set_published(1, tid, rid, True)

            assert (yield client.core.disable_plugin("Stream"))
            assert (
                yield threads.deferToThread(
                    db_rows, database, "SELECT count(*) FROM torrent_policy"
                )
            ) == [(0,)]
            assert (yield client.core.enable_plugin("Stream"))
            reset = yield status(reactor, tid)
            assert (
                reset["record_id"] != rid
                and not reset["is_temporary"]
                and not reset["published"]
            )
            assert (yield client.stream.get_config(1))["max_temporary_torrents"] == 2
            assert sentinel.read_bytes() == b"keep"
            # A corrupted product DB must fail plugin enable, not kill Deluge
            # or replace the file with an empty success-shaped database.
            yield peer.stop()
            database.write_bytes(b"deliberately corrupted test database")
            yield peer.start(reactor)
            assert "Stream" not in (yield client.core.get_enabled_plugins())
            assert not (yield client.core.enable_plugin("Stream"))
            assert (yield client.daemon.get_version()) == "2.2.0"
            assert database.read_bytes() == b"deliberately corrupted test database"
            print(
                "POLICY SMOKE: owner/admin/shared; FIFO and publication; real daemon "
                "restart + SIGKILL; live delete/re-add; shared paths; "
                "explicit disable reset; corrupted DB fail-closed passed",
                flush=True,
            )
        except BaseException:
            if peer.log:
                peer.log.flush()
            print((profile / "daemon.log").read_text(), flush=True)
            raise
        finally:
            yield peer.stop()


if __name__ == "__main__":
    task.react(main)
