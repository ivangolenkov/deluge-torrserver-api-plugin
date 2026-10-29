"""Real Deluge + SQLite commit/crash experiments for retained QoS ownership.

Still a phase-0 candidate, not the Core streaming controller. Startup audit runs
before admitting new QoS. Only explicitly owned pieces of a witnessed instance
may be repaired; acknowledgement is not deletion of their durable coverage.
"""

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import deluge.component as component
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "cleanup_guard_helpers", Path(__file__).with_name("guards.py")
)
guards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)
helpers = guards.helpers

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
parser.add_argument("--root")
parser.add_argument("--worker", choices=["prepare", "finalize", "audit"])
parser.add_argument("--case", default="after")


def ledger(root):
    from deluge_stream.recovery_ledger import RecoveryLedger

    return RecoveryLedger(root / "coverage.sqlite", max_instances=4, max_owned=64)


def read_ownership(store):
    # This fixture has at most three rows. The actual store supports bounded
    # keyset pages and never needs a full torrent's ownership in one buffer.
    rows = []
    cursor = None
    while page := store.scan(after=cursor, limit=2):
        rows.extend(page)
        cursor = (page[-1].record_id, page[-1].piece)
    return rows


@defer.inlineCallbacks
def enroll(reactor, root, torrent, pieces):
    from deluge_stream.witness import create_witness

    witness = yield threads.deferToThread(
        create_witness,
        guards.metadata(root, torrent.torrent_id),
        root / "profile" / "coverage-witnesses",
    )
    count = torrent.handle.torrent_file().num_pieces()
    store = ledger(root)
    rid = yield threads.deferToThread(
        store.register, torrent.torrent_id, witness, count
    )
    epochs = yield threads.deferToThread(store.arm, rid, pieces)
    return rid, epochs


@defer.inlineCallbacks
def save_policy_only(reactor):
    manager = component.get("TorrentManager")
    yield helpers.until(reactor, lambda: not manager.is_saving_state)
    yield manager.save_state()


@defer.inlineCallbacks
def prepare(reactor, args):
    root = Path(args.root)
    daemon, _, snapshot = yield helpers.start(reactor, root, args.egg)
    raw = yield threads.deferToThread(helpers.fixture, root, "v1")
    torrent = helpers.add(daemon, root, raw, "v1")
    yield guards.set_policy(reactor, daemon, torrent, snapshot, [1, 0, 6])
    daemon.core.set_torrent_options(
        [torrent.torrent_id],
        {
            "prioritize_first_last_pieces": True,
        },
    )
    layout = snapshot(torrent.handle.torrent_file())
    yield helpers.until(
        reactor,
        lambda: torrent.handle.get_piece_priorities()
        == helpers.policy(layout, torrent),
    )
    yield enroll(reactor, root, torrent, [0, 2])
    for piece in (0, 2):
        torrent.handle.set_piece_deadline(piece, 0)
    yield helpers.until(reactor, lambda: torrent.handle.piece_priority(2) == 7)
    yield guards.save(reactor, torrent)
    os.kill(os.getpid(), signal.SIGKILL)


@defer.inlineCallbacks
def repair(reactor, root, torrent, snapshot, rows):
    from deluge_stream.recovery_guard import admit_result
    from deluge_stream.witness import witness_matches

    manager = component.get("TorrentManager")
    captured = guards.stamp(torrent)
    row = rows[0]
    valid = yield threads.deferToThread(
        witness_matches,
        row.witness,
        guards.metadata(root, row.torrent_id),
        root / "profile" / "coverage-witnesses",
    )
    current = manager.torrents.get(row.torrent_id)
    reason = admit_result(
        torrent, current, captured, guards.stamp(current) if current else None, valid
    )
    if reason != "ready":
        return reason, 0
    layout = snapshot(torrent.handle.torrent_file())
    assert layout.piece_count == row.piece_count
    desired = helpers.policy(layout, torrent)
    owned = {row.piece for row in rows}
    before = torrent.handle.get_piece_priorities()
    writes = 0
    for piece in sorted(owned):
        if torrent.handle.piece_priority(piece) != desired[piece]:
            torrent.handle.reset_piece_deadline(piece)
            torrent.handle.piece_priority(piece, desired[piece])
            writes += 1
    yield helpers.until(
        reactor,
        lambda: all(
            torrent.handle.piece_priority(piece) == desired[piece] for piece in owned
        ),
    )
    after = torrent.handle.get_piece_priorities()
    assert all(
        after[p] == before[p] for p in range(layout.piece_count) if p not in owned
    )
    assert guards.stamp(torrent) == captured
    yield guards.save(reactor, torrent)
    return "restored", writes


@defer.inlineCallbacks
def finalize(reactor, args):
    root = Path(args.root)
    daemon, _, snapshot = yield helpers.start(reactor, root, args.egg)
    manager = component.get("TorrentManager")
    yield helpers.until(reactor, lambda: len(manager.torrents) == 1)
    torrent = next(iter(manager.torrents.values()))
    assert guards.stamp(torrent).first_last
    store = ledger(root)
    rows = yield threads.deferToThread(read_ownership, store)
    assert len(rows) == 2 and all(row.pending for row in rows)
    result, _ = yield repair(reactor, root, torrent, snapshot, rows)
    assert result == "restored"
    rid = rows[0].record_id
    epochs = {row.piece: row.epoch for row in rows}
    captured = guards.stamp(torrent)
    if args.case == "epoch":
        newer = yield threads.deferToThread(store.arm, rid, [0, 2])
        assert newer == {0: 2, 2: 2}
        torrent.handle.set_piece_deadline(2, 0)
        yield helpers.until(reactor, lambda: torrent.handle.piece_priority(2) == 7)
        yield guards.save(reactor, torrent)
        accepted = yield threads.deferToThread(store.acknowledge, rid, epochs)
        assert accepted == ()
        assert torrent.handle.piece_priority(2) == 7
        current = yield threads.deferToThread(read_ownership, store)
        assert all(row.pending and row.epoch == 2 for row in current)
        os.kill(os.getpid(), signal.SIGKILL)

    entered, release, committed = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def before_commit():
        entered.set()
        if not release.wait(10):
            raise RuntimeError("commit fault barrier timed out")
        if args.case == "rollback":
            raise RuntimeError("injected transaction rollback")

    def after_commit():
        committed.set()
        if args.case == "after" and not release_after.wait(10):
            raise RuntimeError("post-commit fault barrier timed out")

    release_after = threading.Event()
    acknowledgement = threads.deferToThread(
        store.acknowledge,
        rid,
        epochs,
        before_commit=before_commit,
        after_commit=after_commit,
    )
    try:
        yield helpers.until(reactor, entered.is_set)
        if args.case == "readd":
            # Deluge can remove/re-add while the separate SQLite writer is held.
            assert daemon.core.remove_torrent(torrent.torrent_id, False)
            yield helpers.until(reactor, lambda: not torrent.handle.is_valid())
            raw = yield threads.deferToThread(helpers.fixture, root, "v1")
            new = helpers.add(daemon, root, raw, "v1")
            yield guards.set_policy(reactor, daemon, new, snapshot, [2, 0, 3])
        else:
            # Change only first/last, retaining file priorities. The pinned Deluge
            # runtime can leave an old explicit piece vector in resume-data.
            daemon.core.set_torrent_options(
                [torrent.torrent_id],
                {
                    "prioritize_first_last_pieces": False,
                },
            )
        yield save_policy_only(reactor)
        if args.case == "before":
            os.kill(os.getpid(), signal.SIGKILL)
        release.set()
        if args.case == "after":
            yield helpers.until(reactor, committed.is_set)
            os.kill(os.getpid(), signal.SIGKILL)
        if args.case == "rollback":
            try:
                yield acknowledgement
            except RuntimeError as error:
                assert "injected transaction rollback" in str(error)
            else:
                raise AssertionError("rollback fault did not abort the commit")
            os.kill(os.getpid(), signal.SIGKILL)
        accepted = yield acknowledgement
        assert accepted == (0, 2)
        if args.case == "readd":
            assert manager.torrents[torrent.torrent_id] is new and new is not torrent
            # Old completion acknowledges only old UUID. New QoS is admitted only
            # after its own coverage is committed; the old callback does no repair.
            yield enroll(reactor, root, new, [2])
            new.handle.set_piece_deadline(2, 0)
            yield helpers.until(reactor, lambda: new.handle.piece_priority(2) == 7)
            yield guards.save(reactor, new)
            current = yield threads.deferToThread(read_ownership, store)
            assert len(current) == 3 and sum(row.pending for row in current) == 1
            os.kill(os.getpid(), signal.SIGKILL)
        assert args.case == "live" and guards.stamp(torrent) != captured
        # Bounded, explicit retry after the commit; rows exist even though pending=0.
        current = yield threads.deferToThread(read_ownership, store)
        assert len(current) == 2 and not any(row.pending for row in current)
        result, writes = yield repair(reactor, root, torrent, snapshot, current)
        assert result == "restored" and torrent.handle.piece_priority(0) == 1
        print(
            json.dumps(
                {
                    "cleanup_live": "policy changed during real SQLite commit",
                    "coverage_retained": len(current),
                    "retry_writes": writes,
                }
            ),
            flush=True,
        )
    finally:
        release.set()
        release_after.set()
    yield helpers.stop()


@defer.inlineCallbacks
def audit(reactor, args):
    root = Path(args.root)
    _, _, snapshot = yield helpers.start(reactor, root, args.egg)
    manager = component.get("TorrentManager")
    try:
        yield helpers.until(reactor, lambda: len(manager.torrents) == 1)
        torrent = next(iter(manager.torrents.values()))
        expected_priorities = [2, 0, 3] if args.case == "readd" else [1, 0, 6]
        assert torrent.options["file_priorities"] == expected_priorities
        assert guards.stamp(torrent).first_last == (args.case == "epoch")
        store = ledger(root)
        rows = yield threads.deferToThread(read_ownership, store)
        assert len(rows) == (3 if args.case == "readd" else 2)
        pending_before = sum(row.pending for row in rows)
        if args.case in ("before", "rollback", "epoch"):
            assert pending_before == 2
        elif args.case in ("after", "live"):
            assert pending_before == 0
        else:
            assert pending_before == 1
        owned_before = torrent.handle.get_piece_priorities()
        results = []
        for rid in sorted({row.record_id for row in rows}):
            group = [row for row in rows if row.record_id == rid]
            result, writes = yield repair(reactor, root, torrent, snapshot, group)
            results.append((result, writes))
            if result == "restored":
                yield threads.deferToThread(
                    store.acknowledge, rid, {row.piece: row.epoch for row in group}
                )
            else:
                assert result == "identity_unproven" and args.case == "readd"
                assert writes == 0
        after = yield threads.deferToThread(read_ownership, store)
        assert len(after) == len(
            rows
        )  # Acknowledgement NEVER removes startup coverage.
        assert not any(row.pending for row in after)
        if args.case == "epoch":
            assert torrent.handle.piece_priority(2) == 1
            assert all(row.epoch == 2 for row in after)
        elif args.case == "readd":
            assert torrent.handle.piece_priority(2) == 2
        else:
            assert torrent.handle.piece_priority(0) == 1
        # In particular, after-commit recovery must not skip acknowledged rows.
        if args.case == "after":
            assert owned_before[0] == 7, (
                "control no longer reproduces stale resume vector"
            )
        print(
            json.dumps(
                {
                    "cleanup_cut": args.case,
                    "pending_before": pending_before,
                    "coverage_after": len(after),
                    "audit": results,
                }
            ),
            flush=True,
        )
    finally:
        yield helpers.stop()


def main():
    args = parser.parse_args()
    if args.worker:
        task.react(
            lambda reactor: {"prepare": prepare, "finalize": finalize, "audit": audit}[
                args.worker
            ](reactor, args)
        )
        return
    with tempfile.TemporaryDirectory(
        prefix="deluge-stream-cleanup-", dir=args.profile_root or None
    ) as directory:
        common = [
            sys.executable,
            "-I",
            str(Path(__file__).resolve()),
            "--egg",
            str(Path(args.egg).resolve()),
        ]

        def run(root, worker, case, expected=0):
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

        for case in ("before", "after", "rollback", "live", "epoch", "readd"):
            root = Path(directory) / case
            run(root, "prepare", case, -signal.SIGKILL)
            run(root, "finalize", case, 0 if case == "live" else -signal.SIGKILL)
            run(root, "audit", case)


if __name__ == "__main__":
    main()
