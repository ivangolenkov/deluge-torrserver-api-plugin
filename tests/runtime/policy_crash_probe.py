"""Fault injection: native re-add committed while plugin DB is locked.

This checks the cross-store crash confirmation gate, not automatic identity proof.
Runs against the installed egg in a completely disposable daemon profile.
"""

import base64
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from deluge.ui.client import client
from twisted.internet import defer, task

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_smoke import Peer, args, metainfo, status  # noqa: E402


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="stream-crash-window-", dir=args.profile_root or None
    ) as directory:
        root = Path(directory)
        profile = root / "profile"
        (profile / "plugins").mkdir(parents=True)
        shutil.copy2(args.egg, profile / "plugins")
        downloads = root / "downloads"
        downloads.mkdir()
        peer = Peer(profile)
        (profile / "auth").write_text(f"admin:{peer.password}:10\n")
        (profile / "auth").chmod(0o600)
        database = profile / "stream.sqlite"
        raw = metainfo()
        options = {
            "download_location": str(downloads),
            "add_paused": True,
            "auto_managed": False,
        }
        try:
            yield peer.start(reactor)
            assert (yield client.core.enable_plugin("Stream"))
            tid = yield client.core.add_torrent_file(
                "fixture.torrent", base64.b64encode(raw), options
            )
            old = yield status(reactor, tid)
            rid = old["record_id"]
            yield client.stream.set_retention(1, tid, rid, True)
            yield client.stream.set_published(1, tid, rid, True)
            print("POLICY PROBE: initial policy committed; stopping daemon", flush=True)
            yield peer.stop()
            yield peer.start(reactor)
            assert (yield status(reactor, tid))["record_id"] == rid
            print(
                "POLICY PROBE: native restart restored policy; locking SQLite",
                flush=True,
            )
            lock = sqlite3.connect(database, timeout=5)
            try:
                lock.execute("BEGIN IMMEDIATE")
                assert (yield client.core.remove_torrent(tid, False))
                yield task.deferLater(reactor, 0.2, lambda: None)
                replacement = yield client.core.add_torrent_file(
                    "fixture.torrent", base64.b64encode(raw), options
                )
                assert replacement == tid
                # Native catalogue/state work can proceed, while plugin worker
                # cannot commit a replacement/tombstone through the held lock.
                yield task.deferLater(reactor, 0.3, lambda: None)
                yield peer.stop(crash=True)
                print(
                    "POLICY PROBE: killed after native re-add while DB was locked",
                    flush=True,
                )
            finally:
                lock.rollback()
                lock.close()
            yield peer.start(reactor)
            print(
                "POLICY PROBE: restarted native IDs",
                (yield client.core.get_session_state()),
                flush=True,
            )
            restored = yield status(reactor, tid)
            assert restored["recovery_required"]
            assert not restored["published"]
            print(
                json.dumps(
                    {
                        "experiment": "native_readd_before_plugin_commit_then_SIGKILL",
                        "same_record_id": restored["record_id"] == rid,
                        "temporary_after_restart": bool(restored["is_temporary"]),
                        "published_after_restart": bool(restored["published"]),
                        "recovery_required": restored["recovery_required"],
                        "tested_crash_case_blocked": True,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        except BaseException as exc:
            if peer.log:
                peer.log.flush()
            print((profile / "daemon.log").read_text(), flush=True)
            print("POLICY PROBE failure:", repr(exc), flush=True)
            raise
        finally:
            yield peer.stop()


task.react(main)
