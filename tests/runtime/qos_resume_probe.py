"""Phase-3 research gate: immediate priority repair vs native deadline completion.

No production QoS or public API is enabled. Uses the installed egg, native
Deluge session, and one extra local fixture seed session only.
"""

import argparse
import base64
import importlib.util
import json
import tempfile
from pathlib import Path

import libtorrent as lt
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "qos_research_helpers", Path(__file__).with_name("research.py")
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def fixture(root):
    path = root / "seed"
    path.mkdir()
    payload = b"".join(bytes([i + 1]) * 65536 for i in range(64))
    (path / "payload.bin").write_bytes(payload)
    storage = lt.file_storage()
    storage.add_file("payload.bin", len(payload))
    creator = lt.create_torrent(storage, 65536, lt.create_torrent.v1_only)
    lt.set_piece_hashes(creator, str(path))
    return path, lt.bencode(creator.generate())


@defer.inlineCallbacks
def run(reactor, root, egg):
    daemon, _, _ = yield helpers.start(reactor, root, egg)
    settings = {
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "dht_bootstrap_nodes": "",
    }
    seed = None
    torrent = None
    target = None
    try:
        path, raw = yield threads.deferToThread(fixture, root)
        seed = lt.session(settings)
        seed_handle = seed.add_torrent(
            {"ti": lt.torrent_info(raw), "save_path": str(path)}
        )
        yield helpers.until(reactor, lambda: seed_handle.status().is_seeding)
        seed_handle.set_upload_limit(64 * 1024)
        daemon.core.session.apply_settings(settings)
        destination = root / "download"
        destination.mkdir()
        tid = daemon.core.add_torrent_file(
            "fixture.torrent",
            base64.b64encode(raw),
            {
                "download_location": str(destination),
                "add_paused": True,
                "auto_managed": False,
            },
        )
        torrent = helpers.component.get("TorrentManager").torrents[tid]
        handle = torrent.handle
        target = handle.torrent_file().num_pieces() - 1
        baseline = handle.piece_priority(target)
        assert baseline == 4
        handle.set_piece_deadline(target, 0)
        # Synchronous native getter fences processing of the preceding command.
        deadline_priority = handle.piece_priority(target)
        assert deadline_priority == 7
        handle.piece_priority(target, baseline)
        repaired = yield helpers.saved(reactor, torrent)
        repaired_priorities = list(repaired.piece_priorities) or [4] * (target + 1)
        assert repaired_priorities[target] == baseline
        # The repair does not remove the time-critical entry. Native completion
        # changes its priority again, without another plugin command.
        handle.resume()
        yield helpers.until(reactor, lambda: not handle.status().paused)
        handle.connect_peer(("127.0.0.1", seed.listen_port()))
        yield helpers.until(reactor, lambda: handle.have_piece(target))
        handle.pause()
        assert not handle.status().is_seeding
        completed = yield helpers.saved(reactor, torrent)
        after = list(completed.piece_priorities)[target]
        assert after == 1, after
        assert list(completed.file_priorities) == [4]
        print(
            json.dumps(
                {
                    "experiment": "deadline_immediate_repair_then_native_completion",
                    "base_file_priority": 4,
                    "deadline_priority": deadline_priority,
                    "resume_after_immediate_repair": repaired_priorities[target],
                    "resume_after_native_completion": after,
                    "instant_repair_is_persistence_safe": False,
                    "scope": "native local seed, not product QoS",
                }
            ),
            flush=True,
        )
    finally:
        if torrent is not None and target is not None:
            torrent.handle.reset_piece_deadline(target)
            torrent.handle.piece_priority(target, 4)
        if seed is not None:
            seed.pause()
        yield helpers.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-qos-resume-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), str(Path(args.egg).resolve())])
