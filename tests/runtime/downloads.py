"""Installed core artifact, native readiness and direct file/ZIP/ticket HTTP."""

import argparse
import base64
import hashlib
import http.client
import io
import json
import socket
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import deluge.component as component
from deluge._libtorrent import lt
from deluge.core.authmanager import Account
from twisted.internet import defer, task, threads

sys.path.insert(0, str(Path(__file__).resolve().parent))
from streaming import fetch, helpers, native  # noqa: E402


@defer.inlineCallbacks
def run(reactor, root, egg):
    daemon, _, _ = yield native.start(reactor, root, egg)
    plugin = component.get("CorePluginManager").plugins["Stream"].plugin
    policy, server = plugin.policy, plugin.server
    actor = ("localclient", True)
    try:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        yield policy.set_config(
            {"bind_host": "127.0.0.1", "bind_port": port, "public_base_url": base},
            actor,
        )
        yield server.configure()
        seed_path, raw = yield threads.deferToThread(helpers.fixture, root)
        tid = daemon.core.add_torrent_file(
            "fixture.torrent",
            base64.b64encode(raw),
            {
                "download_location": str(seed_path),
                "add_paused": False,
                "auto_managed": False,
            },
        )
        torrent = component.get("TorrentManager").torrents[tid]
        yield native.until(
            reactor,
            lambda: policy.ready
            and tid in policy.rows
            and torrent.handle.status().is_seeding,
        )
        rid = policy.rows[tid]["record_id"]
        assert not policy.rows[tid]["published"] and not policy.config["torrent_owner"]
        torrent.handle.pause()
        daemon.core.set_torrent_options([tid], {"file_priorities": [0]})
        yield native.until(reactor, lambda: torrent.handle.status().paused)
        payload = yield threads.deferToThread((seed_path / "payload.bin").read_bytes)

        def request(path, **kwargs):
            return threads.deferToThread(fetch, base, path, **kwargs)

        def ticket(kind="file"):
            return server.downloads.create(tid, rid, actor, kind)

        issued = yield ticket()
        path = urlsplit(issued["url"]).path
        assert policy.transfer_status(tid)["ticket_reservations"] == 1
        head = yield request(path, method="HEAD")
        assert (
            head[0] == 200
            and head[2] == b""
            and head[1]["Content-Length"] == str(len(payload))
        )
        assert policy.transfer_status(tid)["ticket_reservations"] == 1
        response = yield request(path, headers={"Range": "bytes=17-65547"})
        assert response[0] == 206 and response[2] == payload[17:65548], response[:2]
        assert "filename*=UTF-8''" in response[1]["Content-Disposition"]
        response = yield request(path, headers={"Range": "bytes=-31"})
        assert response[0] == 206 and response[2] == payload[-31:]
        assert policy.transfer_status(tid)["active_stream_requests"] == 0
        assert not server.streams.owned
        # Native read failure: JSON before file body, abort (never successful
        # end-of-archive) after ZIP headers have already been transmitted.
        adapter = server.streams.adapter
        original_alert_data = adapter.alert_data
        from deluge_stream import direct_reader

        original_block = direct_reader.read_block

        def disk_full(*_):
            raise RuntimeError("disk_full")

        adapter.alert_data = disk_full
        direct_reader.read_block = disk_full
        try:
            failed_file = yield request(path)
            assert (
                failed_file[0] == 507
                and json.loads(failed_file[2])["error"] == "disk_full"
            )
            failed_zip_ticket = yield ticket("zip")
            try:
                yield request(urlsplit(failed_zip_ticket["url"]).path)
            except (http.client.HTTPException, ConnectionResetError):
                pass
            else:
                raise AssertionError("failed ZIP was finished successfully")
        finally:
            adapter.alert_data = original_alert_data
            direct_reader.read_block = original_block
        issued_zip = yield ticket("zip")
        response = yield request(
            urlsplit(issued_zip["url"]).path, headers={"Range": "bytes=0-1"}
        )
        assert response[0] == 200 and response[1]["Accept-Ranges"] == "none"
        with zipfile.ZipFile(io.BytesIO(response[2])) as archive:
            assert archive.namelist() == ["payload.bin"]
            assert archive.read("payload.bin") == payload and archive.testzip() is None
        invalid = yield request("/download/not-a-ticket")
        assert invalid[0] == 403 and json.loads(invalid[2])["error"] == "ticket_invalid"
        # Multi-file v1/v2/hybrid: padding excluded, empty files retained, skip
        # cannot hide an incomplete payload file from ZIP readiness.
        for variant in ("v1", "v2", "hybrid"):
            folder = root / ("multi-" + variant)
            metadata = yield threads.deferToThread(native.fixture, folder, variant)
            mid = daemon.core.add_torrent_file(
                variant + ".torrent",
                base64.b64encode(metadata),
                {
                    "download_location": str(folder / variant),
                    "add_paused": False,
                    "auto_managed": False,
                },
            )
            mt = component.get("TorrentManager").torrents[mid]
            yield native.until(
                reactor,
                lambda mid=mid, mt=mt: policy.ready
                and mid in policy.rows
                and mt.handle.status().is_seeding,
            )
            mrid = policy.rows[mid]["record_id"]
            mt.handle.pause()
            daemon.core.set_torrent_options(
                [mid], {"file_priorities": [0] * mt.handle.torrent_file().num_files()}
            )
            zipped = yield server.downloads.create(mid, mrid, actor, "zip")
            response = yield request(urlsplit(zipped["url"]).path)
            assert response[0] == 200, response[:2]
            with zipfile.ZipFile(io.BytesIO(response[2])) as archive:
                files = [info for info in archive.infolist() if not info.is_dir()]
                assert sorted(info.file_size for info in files) == [0, 65537, 100003]
                assert archive.testzip() is None
                for info in files:
                    expected = (
                        b"a" * 65537
                        if info.filename.endswith("a.bin")
                        else b"b" * 100003
                        if info.filename.endswith("b.bin")
                        else b""
                    )
                    assert archive.read(info) == expected
            details = yield server.downloads.info(mid, mrid, actor)
            assert details["zip_ready"] and len(details["files"]) == 3
            empty_id = next(file["id"] for file in details["files"] if not file["size"])
            zero = yield server.downloads.create(mid, mrid, actor, "file", empty_id)
            empty_response = yield request(urlsplit(zero["url"]).path)
            assert empty_response[0] == 200 and empty_response[2] == b""

        # Account revocation invalidates a capability already returned to RPC.
        accounts = component.get("AuthManager")._AuthManager__auth
        accounts["viewer"] = Account("viewer", "isolated-viewer-password", 5)
        try:
            try:
                yield server.downloads.create(tid, rid, ("viewer", False), "file")
            except ValueError as exc:
                assert str(exc) == "torrent_not_found"
            else:
                raise AssertionError("unrelated account received a download ticket")
            daemon.core.set_torrent_options([tid], {"shared": True})
            shared = yield server.downloads.create(tid, rid, ("viewer", False), "file")
            shared_reply = yield request(
                urlsplit(shared["url"]).path, headers={"Range": "bytes=0-16"}
            )
            assert shared_reply[0] == 206 and shared_reply[2] == payload[:17]
            daemon.core.set_torrent_options([tid], {"shared": False})
            denied = yield request(urlsplit(shared["url"]).path)
            assert denied[0] in (403, 404)
        finally:
            accounts.pop("viewer", None)
            daemon.core.set_torrent_options([tid], {"shared": False})
        revoked = yield ticket()
        account = accounts.pop("localclient")
        try:
            denied = yield request(urlsplit(revoked["url"]).path)
            assert denied[0] == 403
        finally:
            accounts["localclient"] = account

        # A replacement with the same hash never inherits the old ticket.
        stale = yield ticket()
        interrupted = socket.socket()
        interrupted.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)

        def interruptible():
            interrupted.connect(("127.0.0.1", port))
            interrupted.sendall(
                (
                    f"GET {urlsplit(stale['url']).path} HTTP/1.1\r\n"
                    "Host: localhost\r\nConnection: close\r\n\r\n"
                ).encode()
            )

        try:
            yield threads.deferToThread(interruptible)
            yield native.until(
                reactor, lambda: policy.transfer_status(tid)["file_downloads"] == 1
            )
            assert daemon.core.remove_torrent(tid, False)
            yield native.until(reactor, lambda: not server.streams.sessions)
        finally:
            interrupted.close()
        yield native.until(reactor, lambda: policy.ready and tid not in policy.rows)
        replaced = daemon.core.add_torrent_file(
            "fixture.torrent",
            base64.b64encode(raw),
            {
                "download_location": str(seed_path),
                "add_paused": False,
                "auto_managed": False,
            },
        )
        assert replaced == tid
        torrent = component.get("TorrentManager").torrents[tid]
        yield native.until(
            reactor,
            lambda: policy.ready
            and tid in policy.rows
            and torrent.handle.status().is_seeding,
        )
        assert policy.rows[tid]["record_id"] != rid
        stale_response = yield request(urlsplit(stale["url"]).path)
        assert stale_response[0] == 409
        rid = policy.rows[tid]["record_id"]
        torrent.handle.pause()
        daemon.core.set_torrent_options([tid], {"file_priorities": [0]})
        # Slow real sockets exercise HTTP backpressure, active lease protection,
        # ticket TTL without interrupting active GET, and disconnect cleanup.
        yield policy.set_config({"download_ticket_start_ttl_seconds": 1}, actor)
        for kind, counter in (("file", "file_downloads"), ("zip", "zip_downloads")):
            slow_ticket = yield ticket(kind)
            slow = socket.socket()
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)

            def connect(slow=slow, slow_ticket=slow_ticket):
                slow.connect(("127.0.0.1", port))
                slow.sendall(
                    (
                        f"GET {urlsplit(slow_ticket['url']).path} HTTP/1.1\r\n"
                        "Host: localhost\r\nConnection: close\r\n\r\n"
                    ).encode()
                )

            try:
                yield threads.deferToThread(connect)
                yield native.until(
                    reactor,
                    lambda counter=counter: policy.transfer_status(tid)[counter] == 1,
                )
                yield task.deferLater(reactor, 1.1, lambda: None)
                assert policy.transfer_status(tid)[counter] == 1
                assert policy.transfer_status(tid)["active_stream_requests"] == 0
                assert server.streams.reserved <= 1024**2
                if kind == "file":
                    daemon.core.force_recheck([tid])
                    yield native.until(reactor, lambda: not server.streams.sessions)
                    yield native.until(
                        reactor,
                        lambda: not torrent.forcing_recheck
                        and all(
                            torrent.handle.have_piece(piece)
                            for piece in range(
                                torrent.handle.torrent_file().num_pieces()
                            )
                        ),
                    )
                    torrent.handle.pause()
                else:
                    destination = root / "moved-download"
                    yield threads.deferToThread(destination.mkdir)
                    daemon.core.move_storage([tid], str(destination))
                    yield native.until(reactor, lambda: not server.streams.sessions)
                    yield native.until(
                        reactor,
                        lambda destination=destination: (
                            not torrent.handle.status().moving_storage
                            and torrent.handle.save_path() == str(destination)
                        ),
                    )
                    seed_path = destination
            finally:
                slow.close()
            yield native.until(
                reactor,
                lambda: not server.streams.sessions and server.streams.reserved == 0,
            )
        # Native readiness, never size/sparse/100% wanted progress.
        data = lt.bdecode(raw)
        data[b"info"][b"name"] = b"missing.bin"
        missing = daemon.core.add_torrent_file(
            "missing.torrent",
            base64.b64encode(lt.bencode(data)),
            {
                "download_location": str(seed_path),
                "add_paused": True,
                "file_priorities": [0],
            },
        )
        yield native.until(reactor, lambda: policy.ready and missing in policy.rows)
        for kind, code in (("file", "file_not_ready"), ("zip", "torrent_not_ready")):
            try:
                yield server.downloads.create(
                    missing, policy.rows[missing]["record_id"], actor, kind
                )
            except RuntimeError as exc:
                assert str(exc) == code
            else:
                raise AssertionError("missing payload admitted")
        # 100% wanted progress and a full-sized sparse skipped file are not
        # evidence that all ZIP payload bytes have passed native verification.
        mixed_root = root / "mixed-root"

        def mixed_fixture():
            (mixed_root / "mixed").mkdir(parents=True)
            (mixed_root / "mixed/a.bin").write_bytes(b"a" * 16384)
            with (mixed_root / "mixed/b.bin").open("wb") as output:
                output.truncate(16384)
            return lt.bencode(
                {
                    "info": {
                        "name": "mixed",
                        "piece length": 16384,
                        "files": [
                            {"path": ["a.bin"], "length": 16384},
                            {"path": ["b.bin"], "length": 16384},
                        ],
                        "pieces": hashlib.sha1(b"a" * 16384).digest()
                        + hashlib.sha1(b"b" * 16384).digest(),
                    }
                }
            )

        mixed_raw = yield threads.deferToThread(mixed_fixture)
        mixed = daemon.core.add_torrent_file(
            "mixed.torrent",
            base64.b64encode(mixed_raw),
            {
                "download_location": str(mixed_root),
                "file_priorities": [4, 0],
                "add_paused": False,
                "auto_managed": False,
            },
        )
        mt = component.get("TorrentManager").torrents[mixed]
        yield native.until(
            reactor,
            lambda: policy.ready
            and mixed in policy.rows
            and mt.handle.status().is_finished,
        )
        mixed_rid = policy.rows[mixed]["record_id"]
        ready_file = yield server.downloads.create(mixed, mixed_rid, actor, "file", 1)
        assert (yield request(urlsplit(ready_file["url"]).path))[2] == b"a" * 16384
        try:
            yield server.downloads.create(mixed, mixed_rid, actor, "zip")
        except RuntimeError as exc:
            assert str(exc) == "torrent_not_ready"
        else:
            raise AssertionError("wanted progress admitted skipped sparse ZIP")
        yield policy.set_config({"download_ticket_start_ttl_seconds": 1}, actor)
        expiring = yield ticket()
        yield task.deferLater(reactor, 1.1, lambda: None)
        expired = yield request(urlsplit(expiring["url"]).path)
        assert expired[0] == 403
        assert not server.streams.sessions and server.streams.reserved == 0
        assert policy.transfer_status(missing)["ticket_reservations"] == 0
        assert torrent.handle.status().paused and torrent.options[
            "file_priorities"
        ] == [0]
        # Real FIFO race: reservation -> simultaneous file/ZIP; releasing one
        # cannot delete a protected oldest or skip ahead to the newer torrent.
        yield policy.set_config({"download_ticket_start_ttl_seconds": 10}, actor)
        yield policy.mutate(tid, rid, actor, temporary=True)
        reservations = [(yield ticket("file")), (yield ticket("zip"))]
        yield policy.set_config({"max_temporary_torrents": 1}, actor)
        yield policy.mutate(
            missing, policy.rows[missing]["record_id"], actor, temporary=True
        )
        yield native.until(
            reactor, lambda: policy.retention.reason == "protected_oldest"
        )
        sockets = []
        try:
            for reserved in reservations:
                connection = socket.socket()
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)

                def begin(connection=connection, reserved=reserved):
                    connection.connect(("127.0.0.1", port))
                    connection.sendall(
                        (
                            f"GET {urlsplit(reserved['url']).path} HTTP/1.1\r\n"
                            "Host: localhost\r\nConnection: close\r\n\r\n"
                        ).encode()
                    )

                sockets.append(connection)
                yield threads.deferToThread(begin)
            yield native.until(
                reactor,
                lambda: policy.transfer_status(tid)["file_downloads"]
                == policy.transfer_status(tid)["zip_downloads"]
                == 1,
            )
            assert tid in policy.rows and missing in policy.rows
            yield policy.mutate(tid, rid, actor, temporary=False)
            assert not policy.rows[tid]["is_temporary"]
            assert tid in component.get("TorrentManager").torrents
            # Restore a deterministic oldest/newer order for the release test.
            yield policy.mutate(
                missing, policy.rows[missing]["record_id"], actor, temporary=False
            )
            yield policy.mutate(tid, rid, actor, temporary=True)
            yield policy.mutate(
                missing, policy.rows[missing]["record_id"], actor, temporary=True
            )
            sockets[0].close()
            yield native.until(
                reactor, lambda: policy.transfer_status(tid)["file_downloads"] == 0
            )
            assert policy.transfer_status(tid)["zip_downloads"] == 1
            assert tid in policy.rows and missing in policy.rows
            sockets[1].close()
            yield native.until(reactor, lambda: policy.ready and tid not in policy.rows)
            assert missing in policy.rows
            assert not (yield threads.deferToThread((seed_path / "payload.bin").exists))
        finally:
            for connection in sockets:
                connection.close()
        print(
            "DOWNLOADS: native-ready paused/skipped unpublished file; "
            "Range/HEAD/reuse/expiry; ZIP STORE+CRC; missing file+ZIP rejected; "
            "no playback/QoS; FIFO reservation/file+ZIP/release retry"
        )
    finally:
        yield native.stop()


@defer.inlineCallbacks
def main(reactor, args):
    with tempfile.TemporaryDirectory(
        prefix="stream-downloads-", dir=args.profile_root or None
    ) as directory:
        yield run(reactor, Path(directory), args.egg)


parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
task.react(main, [parser.parse_args()])
