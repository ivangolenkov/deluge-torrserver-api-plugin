"""Optional native-identity experiment, not a product identity provider.

Run in the pinned Linux container with --network none. Creates one disposable
Deluge profile and no public torrent. Tests whether different Torrent objects
can have identical native persisted TorrentState and second-resolution added_time.
No plugin, metadata witness, payload deletion or user profile is involved.
"""

import base64
import hashlib
import json
import tempfile
from pathlib import Path

import deluge.component as component
import deluge.configmanager
from deluge._libtorrent import lt
from deluge.core.daemon import Daemon
from twisted.internet import defer, task


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(prefix="stream-identity-probe-") as directory:
        root = Path(directory)
        profile = root / "profile"
        profile.mkdir()
        destination = root / "downloads"
        destination.mkdir()
        sentinel = destination / "unrelated.txt"
        sentinel.write_bytes(b"keep unrelated payload")
        raw = lt.bencode(
            {
                "info": {
                    "name": "fixture.bin",
                    "length": 4,
                    "piece length": 16384,
                    "pieces": hashlib.sha1(b"data").digest(),
                }
            }
        )
        deluge.configmanager.set_config_dir(str(profile))
        daemon = Daemon(standalone=True)
        daemon.start()
        yield component.start()
        manager = component.get("TorrentManager")
        daemon.core.session.apply_settings(
            {
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
                "listen_interfaces": "127.0.0.1:0",
            }
        )
        observations = []
        try:
            for _ in range(8):
                tid = daemon.core.add_torrent_file(
                    "fixture.torrent",
                    base64.b64encode(raw),
                    {
                        "download_location": str(destination),
                        "add_paused": True,
                        "auto_managed": False,
                    },
                )
                first = manager.torrents[tid]
                before = manager.create_state().torrents[0].__dict__.copy()
                first_added = first.handle.status().added_time
                assert daemon.core.remove_torrent(tid, False)
                assert tid not in manager.torrents
                tid2 = daemon.core.add_torrent_file(
                    "fixture.torrent",
                    base64.b64encode(raw),
                    {
                        "download_location": str(destination),
                        "add_paused": True,
                        "auto_managed": False,
                    },
                )
                second = manager.torrents[tid2]
                after = manager.create_state().torrents[0].__dict__.copy()
                second_added = second.handle.status().added_time
                observation = {
                    "same_infohash": tid == tid2,
                    "different_live_object": first is not second,
                    "same_persisted_torrent_state": before == after,
                    "same_native_added_time": first_added == second_added,
                    "native_added_time": int(second_added),
                }
                observations.append(observation)
                if all(
                    observation[key]
                    for key in (
                        "same_infohash",
                        "different_live_object",
                        "same_persisted_torrent_state",
                        "same_native_added_time",
                    )
                ):
                    break
                assert daemon.core.remove_torrent(tid2, False)
                yield task.deferLater(reactor, 0.05, lambda: None)
            else:
                raise AssertionError("no native-identity collision observed in 8 pairs")
            assert sentinel.read_bytes() == b"keep unrelated payload"
            print(
                json.dumps(
                    {
                        "scope": "native identity collision, not reconciliation",
                        "deluge": "2.2.0",
                        "libtorrent": lt.__version__,
                        "pairs": observations,
                        "payload_deletion": False,
                        "add_torrent_params_identity_fields": [
                            name
                            for name in dir(lt.add_torrent_params())
                            if any(term in name for term in ("uuid", "user", "added"))
                        ],
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        finally:
            yield component.shutdown()


if __name__ == "__main__":
    task.react(main)
