"""Installed product HTTP, incomplete local payload, pause/skip and two readers."""

import argparse
import base64
import importlib.util
import json
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import deluge.component as component
from deluge._libtorrent import lt
from jsonschema import Draft202012Validator
from twisted.internet import defer, task, threads
from twisted.web.resource import Resource
from twisted.web.server import Site

spec = importlib.util.spec_from_file_location(
    "stream_probe_helpers", Path(__file__).with_name("qos_resume_probe.py")
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
native = helpers.helpers
reference = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures/torrserver-reference/schema.json"
    ).read_text()
)


def validate(kind, value):
    Draft202012Validator(
        {
            "$schema": reference["$schema"],
            "$defs": reference["$defs"],
            "$ref": "#/$defs/" + kind,
        }
    ).validate(value)


def fetch(base, path, data=None, headers=None, method=None):
    body = (
        data
        if isinstance(data, bytes)
        else json.dumps(data).encode()
        if data is not None
        else None
    )
    request = urllib.request.Request(
        base + path, data=body, headers=headers or {}, method=method
    )
    try:
        response = urllib.request.urlopen(request, timeout=40)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, dict(response.headers), response.read()


class Source(Resource):
    isLeaf = True

    def __init__(self, raw):
        super().__init__()
        self.raw = raw

    def render_GET(self, request):
        if request.path == b"/redirect":
            request.setResponseCode(302)
            request.setHeader(b"Location", b"/fixture.torrent")
            return b""
        if request.path == b"/magnet":
            return (
                "magnet:?xt=urn:btih:" + str(lt.torrent_info(self.raw).info_hashes().v1)
            ).encode()
        return self.raw


@defer.inlineCallbacks
def run(reactor, root, egg):
    daemon, _, _ = yield native.start(reactor, root, egg)
    plugin = component.get("CorePluginManager").plugins["Stream"].plugin
    server, policy = plugin.server, plugin.policy
    seed = None
    source_listener = None
    try:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        config = {
            "bind_host": "127.0.0.1",
            "bind_port": port,
            "torrent_owner": "localclient",
            "public_base_url": f"http://127.0.0.1:{port}",
            "download_root": str(root / "downloads"),
            "stream_window_mib": 1,
            "initial_prebuffer_mib": 1,
            "max_temporary_torrents": 20,
            "debounce_seconds": 1,
        }
        yield policy.set_config(config, ("localclient", True))
        yield server.configure()
        assert server.error is None
        base = f"http://127.0.0.1:{port}"

        def request(path, data=None, **kwargs):
            return threads.deferToThread(fetch, base, path, data, **kwargs)

        assert (yield request("/echo"))[2] == b"MatriX.147.1"
        assert (yield request("/echo", method="OPTIONS"))[1][
            "Access-Control-Allow-Origin"
        ] == "*"
        seed_path, raw = yield threads.deferToThread(helpers.fixture, root)
        source_listener = reactor.listenTCP(0, Site(Source(raw)), interface="127.0.0.1")
        source_url = f"http://127.0.0.1:{source_listener.getHost().port}/redirect"
        reply = yield request(
            "/torrents", {"action": "add", "link": source_url, "title": "fixture"}
        )
        assert reply[0] == 200, reply
        dto = json.loads(reply[2])
        validate("Torrent", dto)
        tid = dto["hash"]
        assert dto["stat"] == 3 and dto["file_stats"][0]["id"] == 1
        assert dto["save_to_db"] is False
        torrent = component.get("TorrentManager").torrents[tid]
        assert torrent.options["owner"] == "localclient"
        assert Path(torrent.handle.save_path()).parent == root / "downloads"
        assert policy.rows[tid]["published"] and policy.rows[tid]["is_temporary"]
        original_check = server.streams.adapter.check

        def admission_conflict(session):
            if session.tid == tid:
                raise RuntimeError("operation_conflict")
            return original_check(session)

        server.streams.adapter.check = admission_conflict
        try:
            for _ in range(40):
                failed = yield request(f"/play/{tid}/1")
                assert failed[0] == 409 and json.loads(failed[2]) == {
                    "error": "operation_conflict"
                }, failed
            yield native.until(reactor, lambda: not server.active_requests)
            assert not server.streams.sessions
        finally:
            server.streams.adapter.check = original_check
        original_rid, fifo = (
            policy.rows[tid]["record_id"],
            policy.rows[tid]["fifo_sequence"],
        )
        repeated = yield request("/torrents", {"action": "add", "link": tid})
        assert repeated[0] == 200 and policy.rows[tid]["fifo_sequence"] == fifo
        assert policy.rows[tid]["record_id"] == original_rid
        text_magnet = yield request(
            "/torrents",
            {"action": "add", "link": source_url.rsplit("/", 1)[0] + "/magnet"},
        )
        assert text_magnet[0] == 200 and json.loads(text_magnet[2])["hash"] == tid
        assert policy.rows[tid]["fifo_sequence"] == fifo
        bad = yield request("/torrents", {"action": "unknown"})
        assert bad[0] == 400 and json.loads(bad[2])["error"] == "unknown_action"
        settings = yield request("/settings", {"action": "get"})
        values = json.loads(settings[2])
        assert values["CacheSize"] == 1024**2 and values["UseDisk"] is True
        assert (yield request("/settings", {"action": "set", "CacheSize": 1}))[0] == 200
        assert policy.config["stream_window_mib"] == 1
        stream = f"/stream/fixture.mp4?link={tid}&index=1&play"
        # Explicit fixture user action, not something the product is allowed to do.
        daemon.core.set_torrent_options([tid], {"auto_managed": False})
        torrent.handle.pause()
        yield native.until(reactor, lambda: torrent.handle.status().paused)
        head = yield request(stream, method="HEAD", headers={"Range": "bytes=0-0"})
        assert (
            head[0] == 200
            and head[2] == b""
            and head[1]["Content-Length"] == str(4 * 1024**2)
        )
        assert not server.streams.sessions and not server.streams.owned
        blocked = yield request(stream, headers={"Range": "bytes=0-0"})
        assert (
            blocked[0] == 409 and json.loads(blocked[2])["error"] == "torrent_paused"
        ), blocked
        assert torrent.handle.status().paused
        daemon.core.set_torrent_options([tid], {"file_priorities": [0]})
        yield native.until(reactor, lambda: torrent.handle.get_file_priorities() == [0])
        skipped = yield request(stream, headers={"Range": "bytes=0-0"})
        assert skipped[0] == 409 and json.loads(skipped[2])["error"] == "file_skipped"
        daemon.core.set_torrent_options([tid], {"file_priorities": [4]})
        playlist = yield request(f"/playlist?hash={tid}")
        assert (
            playlist[0] == 200
            and base.encode() in playlist[2]
            and b"index=1" in playlist[2]
        )
        invalid = yield request(stream, headers={"Range": "bytes=999999999-"})
        assert invalid[0] == 416 and invalid[1]["Content-Range"] == "bytes */4194304"
        viewed = {"action": "set", "hash": tid, "file_index": 1, "timecode": 12.5}
        assert (yield request("/viewed", viewed))[0] == 200
        marks = yield request("/viewed", {"action": "list", "hash": tid})
        assert json.loads(marks[2])[0]["timecode"] == 12.5
        cache = yield request("/cache", {"action": "get", "hash": tid})
        validate("Cache", json.loads(cache[2]))
        assert json.loads(cache[2])["Filled"] == 0
        seed = lt.session(
            {
                "listen_interfaces": "127.0.0.1:0",
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
            }
        )
        seed_handle = seed.add_torrent(
            {"ti": lt.torrent_info(raw), "save_path": str(seed_path)}
        )
        yield native.until(reactor, lambda: seed_handle.status().is_seeding)
        seed_handle.set_upload_limit(512 * 1024)
        # Deliberate fixture-user bandwidth setting and an unrelated background
        # torrent: product QoS must not change the global limit or stop it.
        background_data = lt.bdecode(raw)
        background_data[b"info"][b"name"] = b"background.bin"
        background_raw = lt.bencode(background_data)
        yield threads.deferToThread(
            shutil.copyfile, seed_path / "payload.bin", seed_path / "background.bin"
        )
        background_seed = seed.add_torrent(
            {"ti": lt.torrent_info(background_raw), "save_path": str(seed_path)}
        )
        yield native.until(reactor, lambda: background_seed.status().is_seeding)
        background_seed.set_upload_limit(512 * 1024)
        background_dir = root / "background"
        yield threads.deferToThread(background_dir.mkdir)
        background_id = daemon.core.add_torrent_file(
            "background.torrent",
            base64.b64encode(background_raw),
            {
                "download_location": str(background_dir),
                "add_paused": False,
                "auto_managed": False,
            },
        )
        background = component.get("TorrentManager").torrents[background_id]
        daemon.core.session.apply_settings({"download_rate_limit": 768 * 1024})
        background.handle.connect_peer(("127.0.0.1", seed.listen_port()))
        yield native.until(
            reactor, lambda: policy.ready and background_id in policy.rows
        )
        hidden = yield request("/torrents", {"action": "get", "hash": background_id})
        assert hidden[0] == 404
        torrent.handle.resume()
        yield native.until(reactor, lambda: not torrent.handle.status().paused)
        torrent.handle.connect_peer(("127.0.0.1", seed.listen_port()))
        assert torrent.handle.status().progress < 1
        a = request(stream, headers={"Range": "bytes=0-2097151"})
        b = request(f"/play/{tid}/1", headers={"Range": "bytes=3145728-"})
        yield native.until(reactor, lambda: len(server.streams.sessions) == 2)
        assert server.streams.owned
        assert policy.transfer_status(tid)["active_stream_requests"] == 2
        daemon.core.set_torrent_options([tid], {"prioritize_first_last_pieces": True})
        rem_active = yield request("/torrents", {"action": "rem", "hash": tid})
        assert rem_active[0] == 409
        owned_initial = set(server.streams.owned)
        yield native.until(
            reactor, lambda: any(16 <= key[2] < 32 for key in server.streams.owned)
        )
        assert set(server.streams.owned) != owned_initial
        replies = yield defer.DeferredList([a, b], fireOnOneErrback=True)
        payload = yield threads.deferToThread((seed_path / "payload.bin").read_bytes)
        assert replies[0][1][0] == replies[1][1][0] == 206
        assert replies[0][1][2] == payload[:2097152]
        assert replies[1][1][2] == payload[3145728:]
        yield native.until(reactor, lambda: not server.streams.sessions)
        assert server.streams.reserved == 0 and not server.streams.owned
        assert torrent.options["file_priorities"] == [4]
        assert torrent.options["prioritize_first_last_pieces"] is True
        assert daemon.core.session.get_settings()["download_rate_limit"] == 768 * 1024
        assert background.handle.status().total_done > 0
        assert background.options["file_priorities"] == [4]
        assert not background.handle.status().paused
        # Make both reader paths deterministic instead of depending on whether
        # the background download completed before a slow HTTP request arrived.
        yield native.until(reactor, lambda: torrent.handle.status().is_seeding)
        original_direct = server.streams.adapter.supports_direct
        for direct in (False, True):
            # Fixture-only switch: ready native reads remain covered even though
            # production prefers direct reads for a fully verified file.
            server.streams.adapter.supports_direct = direct
            slow = socket.socket()
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)

            def connect_slow(slow=slow):
                slow.connect(("127.0.0.1", port))
                slow.sendall(
                    (
                        f"GET {stream} HTTP/1.1\r\nHost: localhost\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                )

            try:
                yield threads.deferToThread(connect_slow)
                yield native.until(reactor, lambda: bool(server.streams.sessions))
                assert len(server.streams.sessions) == 1
                session = next(iter(server.streams.sessions.values()))
                producer = session.cancel.__self__
                yield native.until(reactor, lambda producer=producer: producer.paused)
                assert (session.direct_identity is not None) == direct
                block = (
                    server.streams.direct.BLOCK_SIZE
                    if direct
                    else session.layout.piece_length
                )
                # One chunk (two conservative copies) plus bounded HTTP writes;
                # this is deliberately stricter than the global payload budget.
                limit = 2 * block + server.streams.HTTP_OVERHEAD
                peak = 0
                for _ in range(10):
                    reserved = server.streams.reserved
                    peak = max(peak, reserved)
                    assert 0 <= reserved <= limit, (direct, reserved, limit)
                    assert len(server.streams.jobs) <= 1
                    assert (
                        server.streams.direct.pending if server.streams.direct else 0
                    ) <= 1
                    if producer.chunk is not None:
                        assert len(producer.chunk.data) <= block
                    yield task.deferLater(reactor, 0.02, lambda: None)
                print(
                    f"BACKPRESSURE: reader={'direct' if direct else 'native'} "
                    f"reservation_peak={peak} single_chunk_limit={limit}"
                )
            finally:
                slow.close()
                server.streams.adapter.supports_direct = original_direct
            yield native.until(
                reactor,
                lambda: not server.streams.sessions
                and server.streams.reserved == 0
                and not server.streams.jobs
                and not (server.streams.direct and server.streams.direct.pending),
            )
            assert not server.streams.owned
        # Ready bytes must remain readable under torrent AND session pause.
        torrent.handle.pause()
        yield native.until(reactor, lambda: torrent.handle.status().paused)
        daemon.core.session.pause()
        ready = yield request(stream, headers={"Range": "bytes=17-65547"})
        assert ready[0] == 206 and ready[2] == payload[17:65548], ready[:2]
        daemon.core.set_torrent_options([tid], {"file_priorities": [0]})
        yield native.until(reactor, lambda: torrent.handle.get_file_priorities() == [0])
        ready = yield request(stream, headers={"Range": "bytes=-17"})
        assert ready[0] == 206 and ready[2] == payload[-17:], ready[:2]
        assert torrent.handle.get_file_priorities() == [0]
        assert torrent.handle.status().paused and daemon.core.session.is_paused()
        # Stat/cache/HEAD leave no new playback leases and don't refresh debounce.
        before = policy.transfers.debounce.get(original_rid)
        assert (yield request(stream + "&stat"))[0] == 200
        assert (yield request(stream, method="HEAD"))[0] == 200
        assert policy.transfers.debounce.get(original_rid) == before
        assert not server.streams.sessions and server.streams.reserved == 0
        assert (yield request("/torrents", {"action": "wipe"}))[0] == 200
        assert tid in component.get("TorrentManager").torrents
        daemon.core.session.resume()
        for variant in ("v1", "v2", "hybrid"):
            fixture_root = root / ("fixture-" + variant)
            metadata = yield threads.deferToThread(
                native.fixture, fixture_root, variant
            )
            boundary = b"stream-native-boundary"
            body = (
                b"--" + boundary + b'\r\nContent-Disposition: form-data; name="file"; '
                b'filename="fixture.torrent"\r\n'
                b"Content-Type: application/x-bittorrent\r\n\r\n"
                + metadata
                + b"\r\n--"
                + boundary
                + b"--\r\n"
            )
            uploaded = yield request(
                "/torrent/upload",
                body,
                headers={
                    "Content-Type": "multipart/form-data; boundary=" + boundary.decode()
                },
            )
            assert uploaded[0] == 200, uploaded
            info = json.loads(uploaded[2])
            validate("Torrent", info)
            assert info["save_to_db"] is True
            assert sorted(file["length"] for file in info["file_stats"]) == [
                0,
                65537,
                100003,
            ]
            assert [file["id"] for file in info["file_stats"]] == [1, 2, 3]
            vt = component.get("TorrentManager").torrents[info["hash"]]
            daemon.core.set_torrent_options([info["hash"]], {"auto_managed": False})
            vt.handle.resume()
            yield native.until(
                reactor, lambda vt=vt: vt.handle.status().state not in (1, 7)
            )
            sh = seed.add_torrent(
                {
                    "ti": lt.torrent_info(metadata),
                    "save_path": str(fixture_root / variant),
                }
            )
            yield native.until(reactor, lambda sh=sh: sh.status().is_seeding)
            b_id = next(
                file["id"]
                for file in info["file_stats"]
                if file["path"].endswith("b.bin")
            )
            empty_id = next(
                file["id"] for file in info["file_stats"] if file["length"] == 0
            )
            prefix = f"/stream?link={info['hash']}&index={b_id}"
            preload_a = yield request(prefix + "&preload")
            preload_b = yield request(prefix + "&preload")
            assert (
                preload_a[0] == preload_b[0] == 200
                and preload_a[2] == preload_b[2] == b""
            )
            assert len(server.streams.preloads) == 1
            assert policy.transfer_status(info["hash"])["active_stream_requests"] == 0
            if variant == "v1":
                pa = request(
                    f"/play/{info['hash']}/1", headers={"Range": "bytes=65536-65536"}
                )
                pb = request(prefix + "&play", headers={"Range": "bytes=0-16"})
                yield native.until(
                    reactor,
                    lambda: sum(
                        session.kind == "playback"
                        for session in server.streams.sessions.values()
                    )
                    == 2,
                )
                assert len(server.streams.jobs) == 1
                assert len(next(iter(server.streams.jobs.values()))["waiters"]) == 2
                vt.handle.connect_peer(("127.0.0.1", seed.listen_port()))
                assert (yield pa)[2] == b"a"
                part = yield pb
            else:
                vt.handle.connect_peer(("127.0.0.1", seed.listen_port()))
                part = yield request(prefix + "&play", headers={"Range": "bytes=0-16"})
            assert part[0] == 206 and part[2] == b"b" * 17, part
            yield native.until(reactor, lambda: not server.streams.preloads)
            whole = yield request(prefix + "&play")
            assert whole[0] == 200 and whole[2] == b"b" * 100003
            empty = yield request(f"/play/{info['hash']}/{empty_id}")
            assert empty[0] == 200 and empty[2] == b""
            if variant == "hybrid":
                alias = str(lt.torrent_info(metadata).info_hashes().v1)
                repeated = yield request(
                    "/torrents", {"action": "add", "link": alias, "save_to_db": False}
                )
                assert (
                    repeated[0] == 200 and json.loads(repeated[2])["save_to_db"] is True
                )
            file_path = Path(vt.handle.save_path()) / next(
                file["path"] for file in info["file_stats"] if file["id"] == b_id
            )
            yield native.until(
                reactor,
                lambda tid=info["hash"]: not policy.transfer_status(tid)[
                    "cleanup_protected"
                ],
            )
            removed = yield request(
                "/torrents", {"action": "rem", "hash": info["hash"]}
            )
            assert removed[0] == 200 and file_path.exists()
            assert file_path.read_bytes() == b"b" * 100003
        pending_hash = "d" * 40
        pending = yield request("/torrents", {"action": "add", "link": pending_hash})
        assert pending[0] == 200, pending
        pending_dto = json.loads(pending[2])
        assert pending_dto["metadata_pending"] and pending_dto["file_stats"] == []
        assert policy.rows[pending_hash]["is_temporary"]
        pending_stat = yield request(f"/stream?link={pending_hash}&index=1&stat")
        assert pending_stat[0] == 200 and json.loads(pending_stat[2])["stat"] == 1
        missing_metadata = yield request(f"/play/{pending_hash}/1")
        assert missing_metadata[0] == 409
        assert json.loads(missing_metadata[2])["error"] == "metadata_pending"
        assert (yield request("/torrents", {"action": "rem", "hash": pending_hash}))[
            0
        ] == 200
        print(
            "PRODUCT STREAMING: URL add/repeat, publication/owner, numeric DTO, "
            "playlist/viewed/cache/HEAD/Range, two incomplete readers/window movement, "
            "ready torrent+session pause+skip, zero leaked buffers/deadlines passed"
        )
    finally:
        if seed is not None:
            seed.pause()
        if source_listener is not None:
            yield source_listener.stopListening()
        daemon.core.session.resume()
        yield native.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-product-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), str(Path(args.egg).resolve())])
