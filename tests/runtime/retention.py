"""Installed artifact: native FIFO deletion, all lease kinds and partial failure."""

import argparse
import base64
import importlib.util
import tempfile
from pathlib import Path

import deluge.component as component
import libtorrent as lt
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "research_helpers", Path(__file__).with_name("research.py")
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
ADMIN = ("native-test", True)


def fill(torrent):
    root = Path(torrent.handle.save_path())
    files = torrent.handle.torrent_file().files()
    paths = [root / files.file_path(i) for i in range(files.num_files())]
    paths.append(root / f".{torrent.torrent_id}.parts")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"native-delete-fixture")
    return paths


@defer.inlineCallbacks
def add(reactor, daemon, root, service, variant, label):
    raw = yield threads.deferToThread(helpers.fixture, root / label, variant)
    # Make every fixture a distinct torrent while retaining the metadata shape.
    data = lt.bdecode(raw)
    data[b"info"][b"name"] = label.encode()
    raw = lt.bencode(data)
    torrent = helpers.add(daemon, root, raw, label)
    paths = yield threads.deferToThread(fill, torrent)
    yield helpers.until(
        reactor, lambda: service.ready and torrent.torrent_id in service.rows
    )
    return torrent, paths


@defer.inlineCallbacks
def temporary(service, torrent):
    tid = torrent.torrent_id
    return (
        yield service.mutate(tid, service.rows[tid]["record_id"], ADMIN, temporary=True)
    )


@defer.inlineCallbacks
def run(reactor, root, egg):
    daemon, _, _ = yield helpers.start(reactor, root, egg)
    plugin = component.get("CorePluginManager").plugins["Stream"].plugin
    s = plugin.policy
    try:
        yield s.set_config(
            {"max_temporary_torrents": 100, "debounce_seconds": 1}, ADMIN
        )
        a, apaths = yield add(reactor, daemon, root, s, "v1", "first")
        b, bpaths = yield add(reactor, daemon, root, s, "v2", "second")
        c, cpaths = yield add(reactor, daemon, root, s, "hybrid", "third")
        ar = yield temporary(s, a)
        yield temporary(s, b)
        yield temporary(s, c)
        sentinel = root / "download-first" / "unrelated.txt"
        yield threads.deferToThread(sentinel.write_bytes, b"keep")
        # Every kind protects the native oldest without skipping to newer rows.
        leases = []
        for kind in ("playback", "preload", "file", "zip", "ticket"):
            leases.append(
                (yield s.acquire_transfer(a.torrent_id, ar["record_id"], ADMIN, kind))
            )
        yield s.set_config({"max_temporary_torrents": 2}, ADMIN)
        yield helpers.until(reactor, lambda: s.retention.reason == "protected_oldest")
        assert a.torrent_id in component.get("TorrentManager").torrents
        assert b.torrent_id in component.get("TorrentManager").torrents
        assert s.transfer_status(a.torrent_id)["active_stream_requests"] == 1
        ticket = leases[-1]
        yield s.activate_ticket(a.torrent_id, ar["record_id"], ADMIN, ticket, "file")
        for lease in leases:
            lease.release()
        yield helpers.until(
            reactor,
            lambda: s.ready
            and a.torrent_id not in s.rows
            and s.retention.running is None,
        )
        assert all(not path.exists() for path in apaths)
        assert sentinel.read_bytes() == b"keep"
        assert all(path.exists() for path in bpaths + cpaths)
        yield s.set_config({"max_temporary_torrents": 1}, ADMIN)
        yield helpers.until(
            reactor,
            lambda: s.ready
            and b.torrent_id not in s.rows
            and s.retention.running is None,
        )
        assert all(
            not path.exists() for path in bpaths
        )  # v2 pads and partfile included
        # A known shared payload blocks temporary admission.
        other_raw = lt.bdecode(
            (yield threads.deferToThread(helpers.fixture, root / "shared", "v1"))
        )
        other_raw[b"info"][b"name"] = b"third"
        duplicate = helpers.add(daemon, root, lt.bencode(other_raw), "overlap")
        # Move destination is separate; native shared path is created explicitly.
        duplicate.handle.move_storage(c.handle.save_path())
        yield helpers.until(
            reactor, lambda: duplicate.handle.save_path() == c.handle.save_path()
        )
        yield helpers.until(reactor, lambda: s.ready and duplicate.torrent_id in s.rows)
        try:
            yield temporary(s, duplicate)
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError("shared native paths admitted")
        assert daemon.core.remove_torrent(duplicate.torrent_id, False)
        yield helpers.until(
            reactor, lambda: s.ready and duplicate.torrent_id not in s.rows
        )
        d, _ = yield add(reactor, daemon, root, s, "v1", "fourth")
        yield temporary(s, d)
        yield helpers.until(
            reactor,
            lambda: s.ready
            and c.torrent_id not in s.rows
            and s.retention.running is None,
        )
        assert all(
            not path.exists() for path in cpaths
        )  # hybrid pads and best-hash partfile
        assert s.retention.pending == []
        # Native partial failure: libtorrent deletes known files but cannot rmdir
        # a nonempty subdirectory containing a foreign sentinel. It must remain.
        extra = Path(d.handle.save_path()) / "fourth" / "foreign.txt"
        yield threads.deferToThread(extra.write_bytes, b"foreign")
        e, _ = yield add(reactor, daemon, root, s, "v1", "fifth")
        yield temporary(s, e)
        yield helpers.until(reactor, lambda: bool(s.retention.pending))
        assert s.retention.pending[0]["stage"] == "manual"
        assert extra.read_bytes() == b"foreign"
        assert e.torrent_id in component.get("TorrentManager").torrents
        operation = s.retention.pending[0]["operation_id"]
        yield s.resolve_removal(operation, "verify", ADMIN)
        # Verify only touched journal, not foreign filesystem contents.
        assert extra.read_bytes() == b"foreign"
        assert sentinel.read_bytes() == b"keep"
        # External native removal is not blocked by leases. Its old release and
        # retirement callback must not affect a re-added instance under that hash.
        eid = e.torrent_id
        old_rid = s.rows[eid]["record_id"]
        cancelled = []
        old_lease = yield s.acquire_transfer(
            eid, old_rid, ADMIN, "file", cancelled.append
        )
        raw = lt.bencode(lt.create_torrent(e.handle.torrent_file()).generate())
        destination = e.handle.save_path()
        assert daemon.core.remove_torrent(eid, False)
        new_tid = daemon.core.add_torrent_file(
            "readd.torrent",
            base64.b64encode(raw),
            {
                "download_location": destination,
                "add_paused": True,
                "auto_managed": False,
            },
        )
        assert new_tid == eid
        yield helpers.until(
            reactor,
            lambda: s.ready and eid in s.rows and s.rows[eid]["record_id"] != old_rid,
        )
        new_lease = yield s.acquire_transfer(
            eid, s.rows[eid]["record_id"], ADMIN, "file"
        )
        assert cancelled == ["retired"] and not old_lease.release()
        assert not new_lease.closed and s.transfer_status(eid)["file_downloads"] == 1
        new_lease.release()
        print(
            "NATIVE RETENTION: FIFO/5 protections/ticket/debounce; v1/v2/hybrid "
            "payload+pad+partfile; shared paths; "
            "native partial failure/manual verify passed"
        )
    finally:
        yield helpers.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-retention-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), str(Path(args.egg).resolve())])
