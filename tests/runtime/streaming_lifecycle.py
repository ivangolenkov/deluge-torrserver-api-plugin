"""Actual daemon stop/start and SIGKILL with a live product playback request."""

import base64
import http.client
import json
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import libtorrent as lt
from deluge.ui.client import client
from twisted.internet import defer, task, threads

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy_smoke import Peer, args, metainfo, status  # noqa: E402
from streaming import fetch  # noqa: E402


def safe_fetch(*arguments):
    try:
        return fetch(*arguments)
    except (OSError, http.client.HTTPException):
        return None


def saved_resume(profile, tid):
    paths = list(profile.rglob("torrents.fastresume"))
    assert len(paths) == 1
    data = lt.bdecode(paths[0].read_bytes())
    assert tid.encode() in data
    params = lt.read_resume_data(data[tid.encode()])
    assert list(params.file_priorities) == [6]
    return paths[0].stat().st_size


@defer.inlineCallbacks
def live(reactor, tid):
    for _ in range(200):
        native = yield client.core.get_torrent_status(tid, ["state", "paused"])
        if native["state"] == "Downloading" and not native["paused"]:
            return
        yield task.deferLater(reactor, 0.02, lambda: None)
    raise AssertionError("native torrent did not become eligible")


@defer.inlineCallbacks
def active(reactor):
    for _ in range(200):
        info = yield client.stream.get_diagnostics(1)
        if info["streaming"]["sessions"] == info["streaming"]["piece_jobs"] == 1:
            assert info["streaming"]["deadlines"] == 1
            return
        yield task.deferLater(reactor, 0.02, lambda: None)
    raise AssertionError("product reader did not acquire native window")


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="stream-live-restart-", dir=args.profile_root or None
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
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            yield peer.start(reactor)
            assert (yield client.core.enable_plugin("Stream"))
            yield client.stream.set_config(
                1,
                {
                    "torrent_owner": "admin",
                    "bind_host": "127.0.0.1",
                    "bind_port": port,
                    "public_base_url": base,
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
            row = yield status(reactor, tid)
            rid = row["record_id"]
            yield client.stream.set_published(1, tid, rid, True)
            yield client.core.set_torrent_options(
                [tid], {"file_priorities": [6], "prioritize_first_last_pieces": True}
            )
            mark = yield threads.deferToThread(
                fetch,
                base,
                "/viewed",
                {"action": "set", "hash": tid, "file_index": 1, "timecode": 12.5},
            )
            assert mark[0] == 200
            yield live(reactor, tid)
            waiting = threads.deferToThread(safe_fetch, base, f"/play/{tid}/1")
            yield active(reactor)
            yield peer.stop()
            yield waiting
            size = yield threads.deferToThread(saved_resume, profile, tid)
            assert size > 0  # Real fsynced native fastresume, not a mocked snapshot.
            yield peer.start(reactor)
            restored = yield status(reactor, tid)
            assert restored["record_id"] == rid and restored["published"]
            assert (
                restored["active_stream_requests"] == 0
                and not restored["streaming_protected"]
            )
            info = yield client.stream.get_diagnostics(1)
            assert (
                info["streaming"]["sessions"]
                == info["streaming"]["piece_jobs"]
                == info["streaming"]["payload_bytes_reserved"]
                == 0
            )
            native = yield client.core.get_torrent_status(
                tid, ["file_priorities", "prioritize_first_last_pieces", "auto_managed"]
            )
            assert (
                list(native["file_priorities"]) == [6]
                and native["prioritize_first_last_pieces"]
            )
            assert not native["auto_managed"]
            marks = yield threads.deferToThread(
                fetch, base, "/viewed", {"action": "list", "hash": tid}
            )
            assert json.loads(marks[2])[0]["timecode"] == 12.5
            yield live(reactor, tid)
            waiting = threads.deferToThread(safe_fetch, base, f"/play/{tid}/1")
            yield active(reactor)
            yield peer.stop(crash=True)
            yield waiting
            yield peer.start(reactor)
            restored = yield status(reactor, tid)
            assert restored["recovery_required"] and not restored["published"]
            assert restored["active_stream_requests"] == 0
            info = yield client.stream.get_diagnostics(1)
            assert info["streaming"]["sessions"] == info["streaming"]["deadlines"] == 0
            native = yield client.core.get_torrent_status(
                tid, ["file_priorities", "prioritize_first_last_pieces"]
            )
            assert (
                list(native["file_priorities"]) == [6]
                and native["prioritize_first_last_pieces"]
            )
            yield client.stream.confirm_recovery(1, info["recovery_token"])
            assert (yield status(reactor, tid))["published"]
            print(
                "PRODUCT LIFECYCLE: live GET/native fastresume/normal restart/SIGKILL; "
                "HTTP state cleared, latest file priorities/first-last/queue "
                "and viewed preserved; no QoS replay"
            )
        except BaseException:
            if peer.log:
                peer.log.flush()
            print((profile / "daemon.log").read_text(), flush=True)
            raise
        finally:
            yield peer.stop()


task.react(main)
