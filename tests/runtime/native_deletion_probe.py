"""Research: native rename/delete ordering, in a disposable Deluge profile only.

This does not enable FIFO. It demonstrates why a metadata path snapshot is not
an authorization barrier for native asynchronous disk jobs.
"""

import argparse
import base64
import json
import shutil
import tempfile
from pathlib import Path

import deluge.component as component
import deluge.configmanager
from deluge._libtorrent import lt
from deluge.core.daemon import Daemon
from twisted.internet import defer, task


@defer.inlineCallbacks
def run(reactor, root, egg):
    profile = root / "profile"
    (profile / "plugins").mkdir(parents=True)
    shutil.copy2(egg, profile / "plugins")
    deluge.configmanager.set_config_dir(str(profile))
    daemon = Daemon(standalone=True)
    daemon.start()
    yield component.start()
    plugins = component.get("CorePluginManager")
    assert (yield plugins.enable_plugin("Stream"))
    alerts = component.get("AlertManager")
    daemon.core.session.apply_settings(
        {
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "listen_interfaces": "127.0.0.1:0",
        }
    )
    try:
        destination = root / "download"
        destination.mkdir()
        source = destination / "original.bin"
        source.write_bytes(b"original payload")
        storage = lt.file_storage()
        storage.add_file(source.name, source.stat().st_size)
        creator = lt.create_torrent(storage, 16384, lt.create_torrent.v1_only)
        lt.set_piece_hashes(creator, str(destination))
        raw = lt.bencode(creator.generate())
        tid = daemon.core.add_torrent_file(
            "fixture.torrent",
            base64.b64encode(raw),
            {
                "download_location": str(destination),
                "add_paused": True,
                "auto_managed": False,
            },
        )
        torrent = component.get("TorrentManager").torrents[tid]
        torrent.handle.resume()
        # Wait for native initial file checking, not for a race-inducing sleep.
        for _ in range(200):
            if torrent.handle.status().state not in (1, 7):
                break
            yield task.deferLater(reactor, 0.05, lambda: None)
        else:
            raise AssertionError("native checking timed out")
        torrent.handle.pause()
        deleted = defer.Deferred()
        renamed = []

        def renamed_alert(alert):
            if alert.handle == torrent.handle:
                renamed.append(alert.new_name())

        def deleted_alert(alert):
            if alert.handle == torrent.handle and not deleted.called:
                deleted.callback("deleted")

        alerts.register_handler("file_renamed_alert", renamed_alert)
        alerts.register_handler("torrent_deleted_alert", deleted_alert)
        try:
            # Queue rename and immediately snapshot native metadata and delete.
            # No reactor yield. libtorrent's own thread may still advance;
            # this is an ordering probe, not a deterministic disk-worker fence.
            target = "renamed.bin"
            torrent.handle.rename_file(0, target)
            snapshot = torrent.handle.torrent_file().files().file_path(0)
            assert snapshot == "original.bin", snapshot
            assert daemon.core.remove_torrent(tid, True)
            outcome = yield deleted.addTimeout(15, reactor)
            assert renamed == [target], renamed
            assert not source.exists()
            assert not (destination / target).exists()
            print(
                json.dumps(
                    {
                        "snapshot": snapshot,
                        "native_rename": renamed,
                        "delete_result": outcome,
                        "renamed_path_absent": True,
                        "conclusion": "deletion is not confined to snapshot paths",
                    }
                ),
                flush=True,
            )
        finally:
            alerts.deregister_handler(renamed_alert)
            alerts.deregister_handler(deleted_alert)
    finally:
        yield plugins.disable_plugin("Stream")
        yield component.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-native-deletion-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), str(Path(args.egg).resolve())])
