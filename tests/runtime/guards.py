"""Phase-0 identity/worker-result/queue probes; never a live QoS controller.

Hardlinks refer ONLY to Deluge state/*.torrent in disposable test profiles.
They are an experimental fail-closed witness, not a portable instance identity.
"""

import argparse
import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import deluge.component as component
from deluge.event import TorrentRemovedEvent
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "phase0_research_helpers", Path(__file__).with_name("research.py")
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
parser.add_argument(
    "--worker", choices=["prepare", "recover", "replace", "queue", "reader"]
)
parser.add_argument("--case", default="normal")
parser.add_argument("--root")


def database(root, operation, record=None):
    with closing(sqlite3.connect(root / "guards.sqlite", timeout=5)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute(
            "CREATE TABLE IF NOT EXISTS generations "
            "(record_id TEXT PRIMARY KEY, tid TEXT, witness TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS touched "
            "(record_id TEXT, piece INTEGER, PRIMARY KEY(record_id, piece))"
        )
        if operation == "mark":
            rid, tid, witness, piece = record
            db.execute(
                "INSERT INTO generations VALUES (?, ?, ?)",
                (rid, tid, json.dumps(witness)),
            )
            db.execute("INSERT INTO touched VALUES (?, ?)", (rid, piece))
        elif operation == "clear":
            db.execute("DELETE FROM touched WHERE record_id = ?", (record,))
        elif operation == "read":
            return db.execute(
                "SELECT g.record_id, g.tid, g.witness, t.piece FROM generations g "
                "JOIN touched t ON g.record_id = t.record_id ORDER BY g.record_id"
            ).fetchall()


def metadata(root, tid):
    return root / "profile" / "state" / (tid + ".torrent")


def stamp(torrent):
    from deluge_stream.recovery_guard import PolicyStamp

    return PolicyStamp(
        tuple(torrent.options["file_priorities"]),
        torrent.options["prioritize_first_last_pieces"],
    )


@defer.inlineCallbacks
def set_policy(reactor, daemon, torrent, snapshot, priorities):
    # Separate native operations with an application barrier. This is a research
    # control, NOT a patch of Deluge's asynchronous priority implementation.
    daemon.core.set_torrent_options(
        [torrent.torrent_id],
        {
            "prioritize_first_last_pieces": False,
            "file_priorities": priorities,
        },
    )
    layout = snapshot(torrent.handle.torrent_file())
    yield helpers.until(
        reactor,
        lambda: torrent.handle.get_piece_priorities()
        == helpers.policy(layout, torrent),
    )


@defer.inlineCallbacks
def mark(reactor, root, torrent, piece):
    from deluge_stream.witness import create_witness

    witness = yield threads.deferToThread(
        create_witness,
        metadata(root, torrent.torrent_id),
        root / "profile" / "witnesses",
    )
    yield threads.deferToThread(
        database,
        root,
        "mark",
        (
            witness.token,
            torrent.torrent_id,
            asdict(witness),
            piece,
        ),
    )
    return witness.token


@defer.inlineCallbacks
def save(reactor, torrent):
    manager = component.get("TorrentManager")
    yield helpers.until(reactor, lambda: not manager.is_saving_state)
    yield manager.save_state()
    result = yield manager.save_resume_data([torrent.torrent_id], flush_disk_cache=True)
    assert result, "Deluge did not confirm saving resume-data"


@defer.inlineCallbacks
def prepare(reactor, args):
    root = Path(args.root)
    daemon, _, snapshot = yield helpers.start(reactor, root, args.egg)
    raw = yield threads.deferToThread(helpers.fixture, root, "v1")
    torrent = helpers.add(daemon, root, raw, "v1")
    yield set_policy(reactor, daemon, torrent, snapshot, [1, 0, 6])
    yield mark(reactor, root, torrent, 2)
    torrent.handle.set_piece_deadline(2, 0)
    yield helpers.until(reactor, lambda: torrent.handle.piece_priority(2) == 7)
    yield save(reactor, torrent)
    os.kill(os.getpid(), signal.SIGKILL)


@defer.inlineCallbacks
def replace_instance(reactor, root, daemon, snapshot, old, unlink_failure=False):
    # Simulates ordinary Deluge use while the Stream plugin is disabled.
    plugins = component.get("CorePluginManager")
    if "Stream" in plugins.get_enabled_plugins():
        assert (yield plugins.disable_plugin("Stream"))
    original_remove = os.remove

    def controlled_remove(path, *args, **kwargs):
        if Path(path) == metadata(root, old.torrent_id):
            raise PermissionError("injected metadata unlink failure")
        return original_remove(path, *args, **kwargs)

    # Fault injection only in this disposable test process, never in the plugin.
    if unlink_failure:
        with patch("os.remove", controlled_remove):
            assert daemon.core.remove_torrent(old.torrent_id, False)
    else:
        assert daemon.core.remove_torrent(old.torrent_id, False)
    yield helpers.until(reactor, lambda: not old.handle.is_valid())
    raw = yield threads.deferToThread(helpers.fixture, root, "v1")
    new = helpers.add(daemon, root, raw, "v1")
    assert new.torrent_id == old.torrent_id and new is not old
    yield set_policy(reactor, daemon, new, snapshot, [2, 0, 3])
    rid = yield mark(reactor, root, new, 2)
    new.handle.set_piece_deadline(2, 0)
    yield helpers.until(reactor, lambda: new.handle.piece_priority(2) == 7)
    yield save(reactor, new)
    return new, rid


@defer.inlineCallbacks
def replace(reactor, args):
    root = Path(args.root)
    daemon, _, snapshot = yield helpers.start(reactor, root, args.egg)
    manager = component.get("TorrentManager")
    try:
        yield helpers.until(reactor, lambda: len(manager.torrents) == 1)
        old = next(iter(manager.torrents.values()))
        yield replace_instance(
            reactor,
            root,
            daemon,
            snapshot,
            old,
            unlink_failure=args.case == "unlink-failed",
        )
    finally:
        yield helpers.stop()


@defer.inlineCallbacks
def recover_one(reactor, root, daemon, snapshot, row, fault=None):
    from deluge_stream.recovery_guard import admit_result
    from deluge_stream.witness import MetadataWitness, witness_matches

    rid, tid, raw_witness, piece = row
    manager = component.get("TorrentManager")
    torrent = manager.torrents.get(tid)
    if torrent is None:
        return "instance_absent", 0
    captured = stamp(torrent)
    witness = MetadataWitness(**json.loads(raw_witness))
    valid = yield threads.deferToThread(
        witness_matches, witness, metadata(root, tid), root / "profile" / "witnesses"
    )
    # Deterministic barrier: hold an actual worker result, then interleave a
    # real Core operation before publishing it back to the recovery candidate.
    if fault == "policy":
        yield set_policy(reactor, daemon, torrent, snapshot, [2, 0, 3])
    elif fault == "readd":
        new, _ = yield replace_instance(reactor, root, daemon, snapshot, torrent)
        assert new.handle.piece_priority(piece) == 7
    current = manager.torrents.get(tid)
    reason = admit_result(
        torrent, current, captured, stamp(current) if current else None, valid
    )
    if reason != "ready":
        return reason, 0
    layout = snapshot(torrent.handle.torrent_file())
    desired = helpers.policy(layout, torrent)[piece]
    torrent.handle.reset_piece_deadline(piece)
    torrent.handle.piece_priority(piece, desired)
    yield helpers.until(
        reactor, lambda: torrent.handle.piece_priority(piece) == desired
    )
    assert stamp(torrent) == captured
    yield save(reactor, torrent)
    # Only the quiet controlled branch clears its own record. Product cleanup
    # still needs coordination against policy changes during this DB commit.
    assert stamp(torrent) == captured
    yield threads.deferToThread(database, root, "clear", rid)
    return "restored", 1


@defer.inlineCallbacks
def recover(reactor, args):
    root = Path(args.root)
    daemon, _, snapshot = yield helpers.start(reactor, root, args.egg)
    manager = component.get("TorrentManager")
    try:
        yield helpers.until(reactor, lambda: len(manager.torrents) == 1)
        loaded = next(iter(manager.torrents.values()))
        expected_loaded = (
            [2, 0, 3] if args.case in ("offline", "unlink-failed") else [1, 0, 6]
        )
        assert loaded.options["file_priorities"] == expected_loaded
        rows = yield threads.deferToThread(database, root, "read")
        assert rows
        outcomes = []
        for row in rows:
            fault = {"policy": "policy", "race": "readd"}.get(args.case)
            result, writes = yield recover_one(
                reactor, root, daemon, snapshot, row, fault
            )
            outcomes.append((result, writes))
            if args.case == "policy":
                assert (result, writes) == ("policy_changed", 0)
                assert (yield threads.deferToThread(database, root, "read")) == rows
                retried = yield recover_one(reactor, root, daemon, snapshot, row)
                assert retried == ("restored", 1)
                outcomes.append(retried)
            elif args.case == "race":
                assert (result, writes) == ("instance_changed", 0)
                current = next(iter(manager.torrents.values()))
                assert current.handle.piece_priority(2) == 7
        remaining = yield threads.deferToThread(database, root, "read")
        if args.case in ("normal", "policy"):
            assert not remaining
        elif args.case in ("offline", "unlink-failed"):
            assert sorted(outcomes) == [("identity_unproven", 0), ("restored", 1)]
            assert len(remaining) == 1  # Retain ambiguous old record for diagnosis.
        elif args.case == "race":
            assert len(remaining) == 2  # Old and new record must both survive.
        print(
            json.dumps(
                {
                    "guard_case": args.case,
                    "outcomes": outcomes,
                    "remaining_markers": len(remaining),
                }
            ),
            flush=True,
        )
    finally:
        yield helpers.stop()


@defer.inlineCallbacks
def queue(reactor, args):
    root = Path(args.root)
    daemon, _, _ = yield helpers.start(reactor, root, args.egg)
    try:
        raw = yield threads.deferToThread(helpers.fixture, root, "v1")
        torrent = helpers.add(daemon, root, raw, "v1")
        handle = torrent.handle
        adapter = component.get("CorePluginManager").plugins["Stream"].plugin.adapter
        # Only the test administrator changes global limits, never the plugin.
        daemon.core.set_config({"max_active_downloading": 0, "max_active_limit": 0})
        daemon.core.session.apply_settings(
            {"auto_manage_interval": 1, "active_downloads": 0, "active_limit": 0}
        )
        daemon.core.set_torrent_options([torrent.torrent_id], {"auto_managed": True})
        daemon.core.resume_torrents([torrent.torrent_id])
        yield helpers.until(
            reactor, lambda: handle.status().paused and handle.status().auto_managed
        )
        baseline = handle.get_piece_priorities()
        try:
            yield adapter.read(torrent.torrent_id, 2, torrent)
        except RuntimeError as error:
            assert "queued" in str(error)
        else:
            raise AssertionError("reader bypassed the native queue")
        assert adapter.pending is None
        assert handle.status().paused and handle.status().auto_managed
        assert daemon.core.session.get_settings()["active_downloads"] == 0
        daemon.core.set_torrent_options([torrent.torrent_id], {"auto_managed": False})
        daemon.core.resume_torrents([torrent.torrent_id])
        yield helpers.until(reactor, lambda: not handle.status().paused)
        waiting = adapter.read(torrent.torrent_id, 2, torrent)
        assert adapter.pending is not None
        daemon.core.session.pause()
        started = reactor.seconds()
        try:
            yield waiting.addTimeout(1, reactor)
        except RuntimeError as error:
            assert "session paused" in str(error)
        else:
            raise AssertionError("session pause did not terminate missing-piece wait")
        assert reactor.seconds() - started < 1
        assert daemon.core.session.is_paused()
        assert adapter.pending is None
        assert handle.get_piece_priorities() == baseline
        daemon.core.session.resume()
        yield helpers.until(reactor, lambda: not handle.status().paused)
        waiting = adapter.read(torrent.torrent_id, 2, torrent)
        assert adapter.pending is not None
        daemon.core.set_torrent_options(
            [torrent.torrent_id],
            {
                "file_priorities": [0, 0, 0],
            },
        )
        try:
            yield waiting.addTimeout(1, reactor)
        except RuntimeError as error:
            assert "skipped" in str(error)
        else:
            raise AssertionError("changed user file selection was ignored")
        assert adapter.pending is None
        assert handle.get_file_priorities() == [0, 0, 0]
        assert daemon.core.session.get_settings()["active_downloads"] == 0
        print(
            json.dumps(
                {
                    "queue": "native queue not bypassed",
                    "session_pause": "wait cancelled; pause/priorities preserved",
                    "user_skip": "pending wait released; latest file selection kept",
                }
            ),
            flush=True,
        )
    finally:
        yield helpers.stop()


@defer.inlineCallbacks
def reader(reactor, args):
    root = Path(args.root)
    daemon, _, _ = yield helpers.start(reactor, root, args.egg)
    try:
        raw = yield threads.deferToThread(helpers.fixture, root, "v1")
        old = helpers.add(daemon, root, raw, "v1")
        adapter = component.get("CorePluginManager").plugins["Stream"].plugin.adapter
        daemon.core.resume_torrents([old.torrent_id])
        yield helpers.until(reactor, lambda: not old.handle.status().paused)
        old_read = adapter.read(old.torrent_id, 2, old)
        old_read.addErrback(lambda _: None)
        assert daemon.core.remove_torrent(old.torrent_id, False)
        old_read.cancel()  # Client disconnect is idempotent after real removal.
        new = helpers.add(daemon, root, raw, "v1")
        assert new is not old and new.torrent_id == old.torrent_id
        daemon.core.resume_torrents([new.torrent_id])
        assert not new.handle.status().paused
        new_read = adapter.read(new.torrent_id, 2, new)
        new_read.addErrback(lambda _: None)
        assert not new_read.called
        # Native EventManager delivery is synchronous. Explicitly replay a stale
        # callback through it as fault injection, not as a claim of a native race.
        component.get("EventManager").emit(TorrentRemovedEvent(old.torrent_id))
        yield task.deferLater(reactor, 0.2, lambda: None)
        assert not new_read.called, "old removal event cancelled the replacement reader"
        assert adapter.pending is not None and adapter.pending[1] is new
        new_read.cancel()
        assert adapter.pending is None
        print(
            json.dumps({"reader_readd": "stale event replay leaves new wait alive"}),
            flush=True,
        )
    finally:
        yield helpers.stop()


def main():
    args = parser.parse_args()
    if args.worker:
        task.react(
            lambda reactor: {
                "prepare": prepare,
                "recover": recover,
                "replace": replace,
                "queue": queue,
                "reader": reader,
            }[args.worker](reactor, args)
        )
        return
    with tempfile.TemporaryDirectory(
        prefix="deluge-stream-guards-", dir=args.profile_root or None
    ) as directory:
        common = [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--egg",
            str(Path(args.egg).resolve()),
        ]

        def run(root, worker, case="normal", expected=0):
            result = subprocess.run(
                common + ["--root", str(root), "--worker", worker, "--case", case],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != expected:
                print(result.stdout + result.stderr, flush=True)
                raise AssertionError((worker, case, result.returncode, expected))
            print(result.stdout, end="", flush=True)

        root = Path(directory)
        for case in ("normal", "offline", "unlink-failed", "race", "policy"):
            location = root / case
            run(location, "prepare", expected=-signal.SIGKILL)
            if case in ("offline", "unlink-failed"):
                run(location, "replace", case)
            run(location, "recover", case)
        run(root / "queue", "queue")
        run(root / "reader", "reader")


if __name__ == "__main__":
    main()
