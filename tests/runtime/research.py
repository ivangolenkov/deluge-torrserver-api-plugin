"""Phase-0 metadata and crash experiments, never enabled in the HTTP plugin.

The parent starts fresh Python/Deluge processes against disposable profiles.
The research journal has no product lifecycle/identity implementation.
"""

import argparse
import base64
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import deluge.component as component
import deluge.configmanager
import libtorrent as lt
from deluge.core.daemon import Daemon
from deluge.error import AddTorrentError
from twisted.internet import defer, task, threads

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
parser.add_argument("--worker", choices=["matrix", "dirty", "recover"])
parser.add_argument("--root")
parser.add_argument("--cut", default="resume")


def fixture(root, variant):
    seed = root / variant
    pack = seed / "pack"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "a.bin").write_bytes(b"a" * 65537)
    (pack / "empty").touch()
    (pack / "b.bin").write_bytes(b"b" * 100003)
    storage = lt.file_storage()
    for path in ("pack/a.bin", "pack/empty", "pack/b.bin"):
        storage.add_file(path, (seed / path).stat().st_size)
    flags = {
        "v1": lt.create_torrent.v1_only,
        "v2": lt.create_torrent.v2_only,
        "hybrid": 0,
    }[variant]
    creator = lt.create_torrent(storage, 16384, flags)
    lt.set_piece_hashes(creator, str(seed))
    return lt.bencode(creator.generate())


def journal(root, operation, torrent_id=None, pieces=()):
    """One worker connection, WAL + FULL commit before libtorrent mutations."""
    with closing(sqlite3.connect(root / "research.sqlite", timeout=5)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute(
            "CREATE TABLE IF NOT EXISTS touched (tid TEXT, piece INTEGER, "
            "PRIMARY KEY (tid, piece))"
        )
        if operation == "mark":
            db.executemany(
                "INSERT OR IGNORE INTO touched VALUES (?, ?)",
                [(torrent_id, piece) for piece in pieces],
            )
        elif operation == "clear":
            db.execute("DELETE FROM touched")
        elif operation == "read":
            return db.execute(
                "SELECT tid, piece FROM touched ORDER BY piece"
            ).fetchall()


@defer.inlineCallbacks
def until(reactor, predicate):
    for _ in range(300):
        if predicate():
            return
        yield task.deferLater(reactor, 0.05, lambda: None)
    raise AssertionError("research condition timed out")


@defer.inlineCallbacks
def start(reactor, root, egg=None):
    profile = root / "profile"
    (profile / "plugins").mkdir(parents=True, exist_ok=True)
    shutil.copy2(egg or args.egg, profile / "plugins")
    deluge.configmanager.set_config_dir(str(profile))
    daemon = Daemon(standalone=True)
    daemon.start()
    yield component.start()
    manager = component.get("CorePluginManager")
    assert (yield manager.enable_plugin("Stream"))
    # Only the installed egg supplies the pure model and Deluge adapter.
    from deluge_stream.adapter import hash_aliases, snapshot_layout

    daemon.core.session.apply_settings(
        {
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "listen_interfaces": "127.0.0.1:0",
        }
    )
    return daemon, hash_aliases, snapshot_layout


def add(daemon, root, raw, label):
    destination = root / ("download-" + label)
    destination.mkdir(exist_ok=True)
    tid = daemon.core.add_torrent_file(
        label + ".torrent",
        base64.b64encode(raw),
        {
            "download_location": str(destination),
            "add_paused": True,
            "auto_managed": False,
        },
    )
    assert tid
    return component.get("TorrentManager").torrents[tid]


@defer.inlineCallbacks
def saved(reactor, torrent):
    result = defer.Deferred()
    alerts = component.get("AlertManager")

    def got(alert):
        if alert.handle == torrent.handle and not result.called:
            result.callback(bytes(lt.write_resume_data_buf(alert.params)))

    alerts.register_handler("save_resume_data_alert", got)
    try:
        torrent.handle.save_resume_data()
        raw = yield result.addTimeout(10, reactor)
        return lt.read_resume_data(raw)
    finally:
        alerts.deregister_handler(got)


def policy(layout, torrent):
    priorities = torrent.options["file_priorities"]
    first_last = torrent.options["prioritize_first_last_pieces"]
    return [
        layout.priority(piece, priorities, first_last)
        for piece in range(layout.piece_count)
    ]


@defer.inlineCallbacks
def stop():
    manager = component.get("CorePluginManager")
    if "Stream" in manager.get_enabled_plugins():
        assert (yield manager.disable_plugin("Stream"))
    yield component.shutdown()


@defer.inlineCallbacks
def matrix(reactor, root):
    daemon, aliases, snapshot = yield start(reactor, root)
    results = []
    try:
        for variant in ("v1", "v2", "hybrid"):
            raw = yield threads.deferToThread(fixture, root, variant)
            torrent = add(daemon, root, raw, variant)
            layout = snapshot(torrent.handle.torrent_file())
            mapping = layout.payload()
            assert len(mapping) == 3
            assert sorted(file.size for file in mapping.values()) == [0, 65537, 100003]
            expected_aliases = (
                {"v1"}
                if variant == "v1"
                else ({"v2"} if variant == "v2" else {"v1", "v2"})
            )
            hashes = aliases(torrent.handle.torrent_file())
            assert set(hashes) == expected_aliases
            if "v2" in hashes:
                assert torrent.torrent_id == hashes["v2"][:40]
            else:
                assert torrent.torrent_id == hashes["v1"]
            by_name = {file.path.rsplit("/", 1)[-1]: file for file in mapping.values()}
            a, empty, b = (by_name[name] for name in ("a.bin", "empty", "b.bin"))
            shared = set(layout.pieces(a)) & set(layout.pieces(b))
            assert bool(shared) == (variant == "v1")
            assert not list(layout.pieces(empty))
            # Compare each metadata-derived intersection with libtorrent map_block.
            info = torrent.handle.torrent_file()
            for piece in range(info.num_pieces()):
                block_files = {
                    block.file_index
                    for block in info.map_block(piece, 0, info.piece_size(piece))
                    if block.size
                }
                model_files = {
                    file.index for file in layout.files if piece in layout.pieces(file)
                }
                assert block_files == model_files, (variant, piece)
            for first_last in (False, True, False):
                priorities = [
                    0 if file.pad else (1 if file.index == a.index else 6)
                    for file in layout.files
                ]
                priorities[b.index] = 0 if first_last else 6
                daemon.core.set_torrent_options(
                    [torrent.torrent_id],
                    {
                        "file_priorities": priorities,
                        "prioritize_first_last_pieces": False,
                    },
                )
                baseline = policy(layout, torrent)
                yield until(
                    reactor,
                    lambda torrent=torrent,
                    baseline=baseline: torrent.handle.get_piece_priorities()
                    == baseline,
                )
                daemon.core.set_torrent_options(
                    [torrent.torrent_id],
                    {
                        "prioritize_first_last_pieces": first_last,
                    },
                )
                expected = policy(layout, torrent)
                try:
                    yield until(
                        reactor,
                        lambda torrent=torrent,
                        expected=expected: torrent.handle.get_piece_priorities()
                        == expected,
                    )
                except AssertionError:
                    raise AssertionError(
                        (
                            variant,
                            first_last,
                            expected,
                            torrent.handle.get_piece_priorities(),
                            torrent.options,
                        )
                    ) from None
            duplicate_count = len(component.get("TorrentManager").torrents)
            if variant == "hybrid":
                try:
                    yield defer.maybeDeferred(
                        daemon.core.add_torrent_magnet,
                        "magnet:?xt=urn:btmh:1220" + hashes["v2"],
                        {"download_location": torrent.options["download_location"]},
                    )
                except AddTorrentError:
                    duplicate = {"v2_magnet": "rejected by Deluge 2.2.0 validation"}
                else:
                    raise AssertionError("revisit v2 magnet compatibility assumption")
                try:
                    yield defer.maybeDeferred(
                        daemon.core.add_torrent_magnet,
                        "magnet:?xt=urn:btih:" + hashes["v1"],
                        {"download_location": torrent.options["download_location"]},
                    )
                except AddTorrentError as error:
                    assert "torrent already exists" in str(error)
                    duplicate["v1_alias_result"] = (
                        "duplicate rejected, not idempotent success"
                    )
                else:
                    raise AssertionError("revisit hybrid duplicate-add assumption")
                assert len(component.get("TorrentManager").torrents) == duplicate_count
            else:
                duplicate = None
            results.append(
                {
                    "variant": variant,
                    "deluge_id": torrent.torrent_id,
                    "aliases": hashes,
                    "files": [
                        {
                            "id": external,
                            "index": file.index,
                            "offset": file.offset,
                            "size": file.size,
                            "pieces": list(layout.pieces(file)),
                        }
                        for external, file in mapping.items()
                    ],
                    "pad_indices": [file.index for file in layout.files if file.pad],
                    "shared_pieces": sorted(shared),
                    "hybrid_duplicate_result": duplicate,
                    "policy_comparisons": 3,
                }
            )
        print(json.dumps({"metadata_matrix": results}), flush=True)
    finally:
        yield stop()


@defer.inlineCallbacks
def dirty(reactor, root):
    daemon, _, snapshot = yield start(reactor, root)
    raw = yield threads.deferToThread(fixture, root, "v1")
    torrent = add(daemon, root, raw, "v1")
    layout = snapshot(torrent.handle.torrent_file())
    tid = torrent.torrent_id
    manager = component.get("TorrentManager")
    yield manager.save_state()
    yield manager.save_resume_data([tid], flush_disk_cache=True)
    touched = [0, 2, layout.piece_count - 1]
    yield threads.deferToThread(journal, root, "mark", tid, touched)
    if args.cut == "journal":
        os.kill(os.getpid(), signal.SIGKILL)
    for piece in touched:
        torrent.handle.set_piece_deadline(piece, 0)
    yield until(
        reactor, lambda: all(torrent.handle.piece_priority(p) == 7 for p in touched)
    )
    # A real user changes file selection and first/last while QoS owns pieces.
    user_priorities = [1, 0, 0] if args.cut == "skipped" else [1, 0, 6]
    daemon.core.set_torrent_options([tid], {"file_priorities": user_priorities})
    yield until(
        reactor,
        lambda: torrent.handle.get_piece_priorities() == policy(layout, torrent),
    )
    daemon.core.set_torrent_options([tid], {"prioritize_first_last_pieces": True})
    yield until(
        reactor,
        lambda: torrent.handle.get_piece_priorities() == policy(layout, torrent),
    )
    yield manager.save_state()
    # Reapplying the deadline overlaps Deluge's latest user priorities.
    for piece in touched:
        if policy(layout, torrent)[piece]:
            torrent.handle.set_piece_deadline(piece, 0)
    yield until(
        reactor,
        lambda: all(
            torrent.handle.piece_priority(p) == (7 if policy(layout, torrent)[p] else 0)
            for p in touched
        ),
    )
    # Deliberately persist poisoned resume, as autosave would do before a crash.
    params = yield saved(reactor, torrent)
    assert list(params.piece_priorities) != policy(layout, torrent)
    yield manager.save_resume_data([tid], flush_disk_cache=True)
    if args.cut in ("memory", "restored", "cleared", "graceful"):
        for piece in touched:
            torrent.handle.reset_piece_deadline(piece)
            torrent.handle.piece_priority(piece, policy(layout, torrent)[piece])
        yield until(
            reactor,
            lambda: torrent.handle.get_piece_priorities() == policy(layout, torrent),
        )
        if args.cut == "memory":
            os.kill(os.getpid(), signal.SIGKILL)
        # Keep marker until clean resume is durably saved.
        yield manager.save_resume_data([tid], flush_disk_cache=True)
        if args.cut in ("cleared", "graceful"):
            yield threads.deferToThread(journal, root, "clear")
        if args.cut == "graceful":
            yield stop()
            return
    os.kill(os.getpid(), signal.SIGKILL)


@defer.inlineCallbacks
def recover(reactor, root):
    _, _, snapshot = yield start(reactor, root)
    manager = component.get("TorrentManager")
    try:
        yield until(reactor, lambda: len(manager.torrents) == 1)
        torrent = next(iter(manager.torrents.values()))
        layout = snapshot(torrent.handle.torrent_file())
        expected = policy(layout, torrent)
        before = torrent.handle.get_piece_priorities()
        records = yield threads.deferToThread(journal, root, "read")
        assert bool(records) == (args.cut not in ("cleared", "graceful"))
        for tid, piece in records:
            # This isolated profile never deletes/re-adds. Product recovery MUST
            # additionally prove the durable instance identity before touching it.
            assert tid == torrent.torrent_id
            torrent.handle.piece_priority(piece, expected[piece])
        yield until(reactor, lambda: torrent.handle.get_piece_priorities() == expected)
        params = yield saved(reactor, torrent)
        actual = list(params.piece_priorities) or [4] * layout.piece_count
        assert actual == expected
        yield manager.save_resume_data([torrent.torrent_id], flush_disk_cache=True)
        yield threads.deferToThread(journal, root, "clear")
        assert not (yield threads.deferToThread(journal, root, "read"))
        assert torrent.options["prioritize_first_last_pieces"] == (
            args.cut != "journal"
        )
        print(
            json.dumps(
                {
                    "recovery_cut": args.cut,
                    "current_file_priorities": torrent.options["file_priorities"],
                    "clean_resume": True,
                    "before_recovery_matches_policy": before == expected,
                    "scope": "known instance in isolated research profile",
                }
            ),
            flush=True,
        )
    finally:
        yield stop()


def main():
    global args
    args = parser.parse_args()
    if args.worker:
        function = {"matrix": matrix, "dirty": dirty, "recover": recover}[args.worker]
        task.react(lambda reactor: function(reactor, Path(args.root)))
    else:
        with tempfile.TemporaryDirectory(
            prefix="deluge-stream-research-", dir=args.profile_root or None
        ) as directory:
            root = Path(directory)
            common = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--egg",
                str(Path(args.egg).resolve()),
            ]

            def run_worker(arguments, expected=0):
                result = subprocess.run(
                    common + arguments,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != expected:
                    print(result.stdout + result.stderr, flush=True)
                    raise AssertionError((arguments, result.returncode, expected))
                if result.stdout:
                    print(result.stdout, end="", flush=True)

            run_worker(["--worker", "matrix", "--root", str(root / "matrix")])
            for cut in (
                "journal",
                "resume",
                "skipped",
                "memory",
                "restored",
                "cleared",
                "graceful",
            ):
                location = root / cut
                run_worker(
                    ["--worker", "dirty", "--root", str(location), "--cut", cut],
                    expected=0 if cut == "graceful" else -signal.SIGKILL,
                )
                run_worker(
                    ["--worker", "recover", "--root", str(location), "--cut", cut]
                )


if __name__ == "__main__":
    main()
