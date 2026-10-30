"""Installed ready-file disk reads; native proof, cancellation and drain barriers."""

import base64
import http.client
import importlib.util
import io
import socket
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import deluge.component as component
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "direct_test_helpers", Path(__file__).with_name("streaming.py")
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
native = helpers.native


def fetch(*args, **kwargs):
    try:
        return helpers.fetch(*args, **kwargs)
    except (OSError, http.client.HTTPException):
        return None


@defer.inlineCallbacks
def run(reactor, root, egg):
    daemon, _, _ = yield native.start(reactor, root, egg)
    plugin = component.get("CorePluginManager").plugins["Stream"].plugin
    server, policy = plugin.server, plugin.policy
    from deluge_stream import direct_reader

    original_block = direct_reader.read_block
    release, entered = threading.Event(), threading.Event()
    try:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        actor = ("localclient", True)
        yield policy.set_config(
            {
                "bind_host": "127.0.0.1",
                "bind_port": port,
                "public_base_url": base,
                "torrent_owner": "localclient",
                "download_root": str(root),
            },
            actor,
        )
        yield server.configure()
        raw = yield threads.deferToThread(native.fixture, root, "v1")
        tid = daemon.core.add_torrent_file(
            "ready.torrent",
            base64.b64encode(raw),
            {
                "owner": "localclient",
                "download_location": str(root / "v1"),
                "auto_managed": False,
                "add_paused": False,
            },
        )
        torrent = component.get("TorrentManager").torrents[tid]
        yield native.until(
            reactor, lambda: torrent.handle.status().is_seeding and tid in policy.rows
        )
        rid = policy.rows[tid]["record_id"]
        yield policy.mutate(tid, rid, actor, published=True)
        torrent.handle.pause()
        daemon.core.set_torrent_options([tid], {"file_priorities": [0, 0, 0]})
        yield native.until(reactor, lambda: torrent.handle.status().paused)
        before = (
            list(torrent.options["file_priorities"]),
            torrent.handle.status().paused,
        )
        reads = []

        def no_native(*_):
            raise AssertionError("verified whole file unexpectedly used read_piece")

        server.streams.adapter.read = no_native

        def recorded(*arguments):
            reads.append((arguments[-2], arguments[-1]))
            return original_block(*arguments)

        direct_reader.read_block = recorded
        layout = server.streams.adapter.open(tid, 1)[1]
        for file_id, file in layout.payload().items():
            expected = (root / "v1" / file.path).read_bytes()
            response = yield threads.deferToThread(
                fetch, base, f"/play/{tid}/{file_id}"
            )
            assert response[0] == 200 and response[2] == expected
            if file.size:
                response = yield threads.deferToThread(
                    fetch,
                    base,
                    f"/play/{tid}/{file_id}",
                    headers={"Range": "bytes=-31"},
                )
                assert response[0] == 206 and response[2] == expected[-31:]
        assert reads and all(0 < count <= 256 * 1024 for _, count in reads)
        assert not server.streams.jobs and not server.streams.owned
        issued = yield server.downloads.create(tid, rid, actor, "zip")
        response = yield threads.deferToThread(
            fetch, base, urlsplit(issued["url"]).path
        )
        assert response[0] == 200
        with zipfile.ZipFile(io.BytesIO(response[2])) as archive:
            assert archive.testzip() is None
            for file in layout.payload().values():
                assert archive.read(file.path) == (root / "v1" / file.path).read_bytes()
        assert before == (
            list(torrent.options["file_priorities"]),
            torrent.handle.status().paused,
        )
        # Mutating the SAME inode is outside the content-integrity contract.
        file = layout.payload()[1]
        path = root / "v1" / file.path
        yield threads.deferToThread(path.write_bytes, b"z" * file.size)
        response = yield threads.deferToThread(fetch, base, f"/play/{tid}/1")
        assert response[0] == 200 and response[2] == b"z" * file.size
        yield threads.deferToThread(path.write_bytes, b"a" * file.size)

        def blocked(*arguments):
            entered.set()
            if not release.wait(15):
                raise RuntimeError("storage_error")
            return original_block(*arguments)

        direct_reader.read_block = blocked
        waiting = threads.deferToThread(fetch, base, f"/play/{tid}/1")
        yield native.until(reactor, entered.is_set)
        assert server.streams.direct.pending == 1 and server.streams.reserved > 0
        daemon.core.force_recheck([tid])
        yield native.until(reactor, lambda: not server.streams.sessions)
        assert server.streams.direct.pending == 1 and server.streams.reserved > 0
        closed = server.close()
        assert not closed.called, (
            "close returned before the issued filesystem job drained"
        )
        release.set()
        yield closed
        result = yield waiting
        assert result is None or result[0] != 200
        assert server.streams.direct.pending == server.streams.reserved == 0
        print(
            "DIRECT READ: native proof + safe worker reads, "
            "paused/skipped playback/Range/ZIP; no read_piece, allowed content edit, "
            "recheck cancellation holds quota and close drains"
        )
    finally:
        release.set()
        direct_reader.read_block = original_block
        yield native.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-direct-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), str(Path(args.egg).resolve())])
