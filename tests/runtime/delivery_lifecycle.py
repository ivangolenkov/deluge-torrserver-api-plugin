"""Installed core replacement, schema-5 fixture migration, disable and uninstall."""

import base64
import hashlib
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from deluge.ui.client import client
from twisted.internet import defer, task, threads

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_smoke import Peer, args, metainfo, status  # noqa: E402
from streaming import fetch  # noqa: E402


def schema_five_fixture(database):
    """Controlled old-schema fixture, NOT a claim that the old egg used schema 5."""
    with closing(sqlite3.connect(database)) as db, db:
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        assert schema in (6, 7)
        assert db.execute("SELECT COUNT(*) FROM source_adds").fetchone()[0] == 0
        db.execute("DROP TABLE source_adds")
        if schema == 7:
            db.execute("ALTER TABLE torrent_metadata DROP COLUMN version")
        db.execute("PRAGMA user_version=5")


def migrated(database):
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT COUNT(*) FROM source_adds").fetchone()[0] == 0


@defer.inlineCallbacks
def native_matches(reactor, tid, fields, expected):
    for _ in range(200):
        torrents = yield client.core.get_torrents_status({}, fields)
        current = torrents.get(tid, {})
        if current == expected:
            return
        yield task.deferLater(reactor, 0.05, lambda: None)
    raise AssertionError(("native catalogue/options did not settle", expected, current))


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="stream-delivery-", dir=args.profile_root or None
    ) as directory:
        root = Path(directory)
        profile, downloads = root / "profile", root / "downloads"
        (profile / "plugins").mkdir(parents=True)
        downloads.mkdir()
        previous = Path(args.previous_egg or args.egg).resolve()
        candidate = Path(args.egg).resolve()
        installed = profile / "plugins" / previous.name
        shutil.copy2(previous, installed)
        payload = downloads / "fixture.bin"
        payload.write_bytes(b"data")
        sentinel = downloads / "foreign.bin"
        sentinel.write_bytes(b"unrelated user data")
        peer = Peer(profile)
        (profile / "auth").write_text(f"admin:{peer.password}:10\n")
        (profile / "auth").chmod(0o600)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            base = f"http://127.0.0.1:{reserve.getsockname()[1]}"
        fields = [
            "file_priorities",
            "prioritize_first_last_pieces",
            "paused",
            "auto_managed",
            "download_location",
            "owner",
        ]
        try:
            yield peer.start(reactor)
            assert (yield client.core.enable_plugin("Stream"))
            yield client.stream.set_config(
                1,
                {
                    "torrent_owner": "admin",
                    "download_root": str(downloads),
                    "bind_host": "127.0.0.1",
                    "bind_port": int(base.rsplit(":", 1)[1]),
                    "public_base_url": base,
                    "max_temporary_torrents": 17,
                },
            )
            tid = yield client.core.add_torrent_file(
                "fixture.torrent",
                base64.b64encode(metainfo()),
                {
                    "owner": "admin",
                    "download_location": str(downloads),
                    "add_paused": False,
                    "auto_managed": False,
                },
            )
            for _ in range(200):
                native = yield client.core.get_torrent_status(tid, ["progress"])
                if native["progress"] == 100:
                    break
                yield task.deferLater(reactor, 0.05, lambda: None)
            assert native["progress"] == 100
            row = yield status(reactor, tid)
            rid = row["record_id"]
            yield client.stream.set_retention(1, tid, rid, True)
            yield client.stream.set_published(1, tid, rid, True)
            yield client.core.set_torrent_options(
                [tid], {"file_priorities": [6], "prioritize_first_last_pieces": True}
            )
            yield client.core.pause_torrent([tid])
            viewed = yield threads.deferToThread(
                fetch,
                base,
                "/viewed",
                {"action": "set", "hash": tid, "file_index": 1, "timecode": 23.5},
            )
            assert viewed[0] == 200
            before = yield status(reactor, tid)
            native_before = yield client.core.get_torrent_status(tid, fields)
            seed_queue_before = yield client.core.get_torrent_status(tid, ["queue"])
            native_before["paused"] = True
            yield native_matches(reactor, tid, fields, native_before)
            config = yield client.stream.get_config(1)
            yield peer.stop()
            # Offline fixture only; never a live profile or a user's database.
            yield threads.deferToThread(schema_five_fixture, profile / "stream.sqlite")
            installed.unlink()  # only this test-created egg, while daemon is stopped
            shutil.copy2(candidate, profile / "plugins")
            yield peer.start(reactor)
            after = yield status(reactor, tid)
            for key in ("record_id", "fifo_sequence", "is_temporary", "published"):
                assert before[key] == after[key], (key, before, after)
            assert not after["recovery_required"]
            assert (yield client.stream.get_config(1)) == config
            yield native_matches(reactor, tid, fields, native_before)
            seed_queue_after = yield client.core.get_torrent_status(tid, ["queue"])
            marks = yield threads.deferToThread(
                fetch, base, "/viewed", {"action": "list", "hash": tid}
            )
            assert json.loads(marks[2])[0]["timecode"] == 23.5
            yield threads.deferToThread(migrated, profile / "stream.sqlite")
            # Preservation of pause has been checked above. A restored native
            # torrent may still need resume-data checking: explicitly model a
            # USER resume here, never let the plugin enable missing payload.
            yield client.core.resume_torrent([tid])
            native_before["paused"] = False
            yield native_matches(reactor, tid, fields, native_before)
            for _ in range(200):
                result = yield threads.deferToThread(fetch, base, f"/play/{tid}/1")
                if result[0] == 200:
                    break
                # RPC catalogue/options can precede native resume checking.
                # Wait only for this explicit transient conflict; never resume
                # the torrent or hide a missing-data/storage/auth failure.
                assert result[0] == 409 and json.loads(result[2]) == {
                    "error": "operation_conflict"
                }, result
                yield task.deferLater(reactor, 0.05, lambda: None)
            assert result[0] == 200 and result[2] == b"data", result
            assert (yield client.core.disable_plugin("Stream"))
            yield native_matches(reactor, tid, fields, native_before)
            assert (
                payload.read_bytes() == b"data"
                and sentinel.read_bytes() == b"unrelated user data"
            )
            assert (yield client.core.enable_plugin("Stream"))
            reset = yield status(reactor, tid)
            assert reset["record_id"] != rid
            assert not reset["is_temporary"] and not reset["published"]
            assert (yield client.stream.get_config(1)) == config
            assert (yield client.core.disable_plugin("Stream"))
            yield peer.stop()
            (profile / "plugins" / candidate.name).unlink()
            yield peer.start(reactor)
            assert "Stream" not in (yield client.core.get_enabled_plugins())
            yield native_matches(reactor, tid, fields, native_before)
            assert (
                payload.read_bytes() == b"data"
                and sentinel.read_bytes() == b"unrelated user data"
            )
            print(
                json.dumps(
                    {
                        "delivery": (
                            "replacement/migration5-to7/disable/re-enable/"
                            "uninstall passed"
                        ),
                        "previous_sha256": hashlib.sha256(
                            previous.read_bytes()
                        ).hexdigest(),
                        "candidate_sha256": hashlib.sha256(
                            candidate.read_bytes()
                        ).hexdigest(),
                        "baseline": "provided artifact"
                        if args.previous_egg
                        else "same candidate redeployment",
                        "native_seed_queue_markers": [
                            seed_queue_before["queue"],
                            seed_queue_after["queue"],
                        ],
                        "scope": (
                            "isolated real core; schema5 is a controlled fixture, "
                            "not a released version"
                        ),
                    },
                    sort_keys=True,
                )
            )
        except BaseException:
            if peer.log:
                peer.log.flush()
            print((profile / "daemon.log").read_text(), flush=True)
            raise
        finally:
            yield peer.stop()


task.react(main)
