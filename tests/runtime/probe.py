"""Real Deluge components + installed egg, no checkout imports or public swarm."""

import argparse
import base64
import json
import shutil
import socket
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import deluge.component as component
import deluge.configmanager
import libtorrent as lt
from deluge.core.daemon import Daemon
from twisted.internet import defer, task, threads

parser = argparse.ArgumentParser()
parser.add_argument("--mode", required=True)
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
args = parser.parse_args()


@defer.inlineCallbacks
def until(reactor, predicate, seconds=15):
    for _ in range(int(seconds / 0.05)):
        if predicate():
            return
        yield task.deferLater(reactor, 0.05, lambda: None)
    raise AssertionError("runtime condition timed out")


def fetch(port, token, torrent_id, range_value=None, method="GET"):
    headers = {"Authorization": f"Bearer {token}"}
    if range_value:
        headers["Range"] = range_value
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/probe?torrent={torrent_id}&file=0",
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


@defer.inlineCallbacks
def resume_snapshot(reactor, handle):
    result = defer.Deferred()
    alerts = component.get("AlertManager")

    def received(alert):
        if alert.handle == handle and not result.called:
            # Copy while the Deluge AlertManager owns the callback.
            result.callback(bytes(lt.write_resume_data_buf(alert.params)))

    alerts.register_handler("save_resume_data_alert", received)
    try:
        handle.save_resume_data()
        raw = yield result.addTimeout(10, reactor)
        params = lt.read_resume_data(raw)
        # This fixture has exactly one wanted file; omitted piece vector means 4.
        return list(params.file_priorities), (
            list(params.piece_priorities) or [4] * handle.torrent_file().num_pieces()
        )
    finally:
        alerts.deregister_handler(received)


@defer.inlineCallbacks
def integration(reactor, daemon, plugin, root):
    seed_path = root / "seed"
    seed_path.mkdir()
    # Nonzero, deterministic, nonrepeating per-piece payload.
    payload = b"".join(bytes([i % 251 + 1]) * 65536 for i in range(64))
    (seed_path / "payload.bin").write_bytes(payload)
    storage = lt.file_storage()
    storage.add_file("payload.bin", len(payload))
    creator = lt.create_torrent(storage, 65536, lt.create_torrent.v1_only)
    lt.set_piece_hashes(creator, str(seed_path))
    metadata = creator.generate()
    torrent_info = lt.torrent_info(metadata)
    settings = {
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "dht_bootstrap_nodes": "",
    }
    # Second session belongs ONLY to the local fixture seed, never the plugin.
    seed = lt.session(settings)
    seed_handle = seed.add_torrent({"ti": torrent_info, "save_path": str(seed_path)})
    yield until(reactor, lambda: seed_handle.status().is_seeding)
    seed_handle.set_upload_limit(256 * 1024)
    daemon.core.session.apply_settings(settings)
    dest = root / "download"
    dest.mkdir()
    torrent_id = daemon.core.add_torrent_file(
        "fixture.torrent",
        base64.b64encode(lt.bencode(metadata)),
        {"download_location": str(dest), "add_paused": False, "auto_managed": False},
    )
    assert torrent_id
    torrent = component.get("TorrentManager").torrents[torrent_id]
    handle = torrent.handle
    handle.set_flags(lt.torrent_flags.sequential_download)
    yield until(reactor, lambda: not handle.status().paused)
    handle.connect_peer(("127.0.0.1", seed.listen_port()))
    data = plugin.probe_info()
    port, token = data["port"], data["token"]
    before = handle.status().progress
    assert before < 1, before
    status, headers, body = yield threads.deferToThread(
        fetch, port, token, torrent_id, "bytes=17-100016"
    )
    assert status == 206, (status, body[:100])
    assert headers["Content-Range"] == f"bytes 17-100016/{len(payload)}"
    assert body == payload[17:100017]
    assert handle.status().progress < 1, "fixture must remain incomplete"
    # Let AlertManager run additional cycles: copied bytes must remain unchanged.
    yield task.deferLater(reactor, 0.25, lambda: None)
    assert body == payload[17:100017]
    for range_value, expected_status, expected in (
        ("bytes=0-0", 206, payload[:1]),
        ("bytes=4194304-", 416, b""),
        ("bytes=nonsense", 400, b"invalid probe request"),
    ):
        result = yield threads.deferToThread(
            fetch, port, token, torrent_id, range_value
        )
        assert result[0] == expected_status, result[0]
        assert result[2] == expected
    result = yield threads.deferToThread(
        fetch, port, token, torrent_id, "bytes=0-0", "HEAD"
    )
    assert result[0] == 200 and result[2] == b""
    assert result[1]["Content-Length"] == str(len(payload))
    result = yield threads.deferToThread(fetch, port, "wrong", torrent_id)
    assert result[0] == 403

    seed_handle.pause()
    # No seed for a deliberately absent last piece: exercise async wait + cancel.
    missing = [i for i in range(torrent_info.num_pieces()) if not handle.have_piece(i)]
    assert missing
    start = missing[-1] * torrent_info.piece_length()
    baseline_pieces = handle.get_piece_priorities()
    resume_before = yield resume_snapshot(reactor, handle)
    # Technical experiment, isolated from HTTP reader; not a safe product QoS.
    handle.set_piece_deadline(missing[-1], 0)
    resume_during = yield resume_snapshot(reactor, handle)
    handle.reset_piece_deadline(missing[-1])
    resume_after_reset = yield resume_snapshot(reactor, handle)
    print(
        json.dumps(
            {
                "resume_priority_before": resume_before[1][missing[-1]],
                "resume_priority_during_deadline": resume_during[1][missing[-1]],
                "resume_priority_after_reset": resume_after_reset[1][missing[-1]],
                "live_piece_priority": handle.piece_priority(missing[-1]),
            }
        ),
        flush=True,
    )
    # Pin the observed risk so a passing experiment cannot imply safe QoS.
    assert handle.get_piece_priorities() != baseline_pieces
    assert resume_during != resume_before
    assert resume_after_reset != resume_before
    # Controlled experiment cleanup, no concurrent user changes in this fixture.
    handle.piece_priority(missing[-1], baseline_pieces[missing[-1]])
    yield until(reactor, lambda: handle.get_piece_priorities() == baseline_pieces)
    assert (yield resume_snapshot(reactor, handle)) == resume_before
    conn = socket.socket()
    conn.setblocking(False)
    yield threads.deferToThread(connect_and_send, conn, port, token, torrent_id, start)
    yield until(reactor, lambda: plugin.adapter.pending is not None)
    ticks = []
    clock = task.LoopingCall(lambda: ticks.append(reactor.seconds()))
    clock.start(0.02)
    yield task.deferLater(reactor, 0.2, lambda: None)
    clock.stop()
    assert len(ticks) >= 5, "piece wait blocked reactor"
    conn.close()
    yield until(reactor, lambda: plugin.active is None)
    assert plugin.adapter.pending is None
    assert handle.get_piece_priorities() == baseline_pieces
    resume_after = yield resume_snapshot(reactor, handle)
    assert resume_before == resume_after
    # No file-priority mutations in the research reader.
    assert handle.get_file_priorities() == [4]
    incomplete_after = handle.status().progress
    seed.remove_torrent(seed_handle)
    daemon.core.pause_torrents([torrent_id])
    yield until(reactor, lambda: handle.status().paused)
    try:
        yield plugin.adapter.read(torrent_id, missing[-1], torrent)
    except RuntimeError as error:
        assert "paused" in str(error)
    else:
        raise AssertionError("missing piece must not unpause the torrent")
    ready = yield plugin.adapter.read(torrent_id, 0, torrent)
    assert ready == payload[: torrent_info.piece_length()]
    assert handle.status().paused

    checked = defer.Deferred()

    def on_checked(alert):
        if alert.handle == handle and not checked.called:
            checked.callback(None)

    alerts = component.get("AlertManager")
    alerts.register_handler("torrent_checked_alert", on_checked)
    try:
        daemon.core.force_recheck([torrent_id])
        yield checked.addTimeout(15, reactor)
    finally:
        alerts.deregister_handler(on_checked)
    assert handle.status().paused
    moved = root / "moved"
    yield threads.deferToThread(moved.mkdir)
    daemon.core.move_storage([torrent_id], str(moved))
    yield until(reactor, lambda: handle.status().save_path == str(moved))
    ready = yield plugin.adapter.read(torrent_id, 0, torrent)
    assert ready == payload[: torrent_info.piece_length()]
    assert handle.status().paused

    daemon.core.resume_torrents([torrent_id])
    yield until(reactor, lambda: not handle.status().paused)
    pending = plugin.adapter.read(torrent_id, missing[-1], torrent)
    assert plugin.adapter.pending is not None
    assert daemon.core.remove_torrent(torrent_id, False)
    try:
        yield pending
    except (defer.CancelledError, RuntimeError):
        pass
    else:
        raise AssertionError("removed instance must cancel its piece wait")
    yield until(reactor, lambda: not handle.is_valid())
    replacement_id = daemon.core.add_torrent_file(
        "fixture.torrent",
        base64.b64encode(lt.bencode(metadata)),
        {"download_location": str(moved), "add_paused": True, "auto_managed": False},
    )
    assert replacement_id == torrent_id
    replacement = component.get("TorrentManager").torrents[torrent_id]
    assert replacement is not torrent
    try:
        yield plugin.adapter.read(torrent_id, 0, torrent)
    except RuntimeError as error:
        assert "instance changed" in str(error)
    else:
        raise AssertionError("old producer must not read the replacement instance")
    assert plugin.adapter.pending is None
    print(
        json.dumps(
            {
                "range_bytes": len(body),
                "reactor_ticks_while_waiting": len(ticks),
                "incomplete_before": before,
                "incomplete_after": incomplete_after,
                "copied_alert_buffer": "stable across later AlertManager cycles",
                "cancel": "reader released; HTTP probe never changes priorities",
                "deadline_risk": "priority 4 -> deadline 7 -> reset 1, saved in resume",
                "qos_product_ready": False,
                "lifecycle": "pause/ready read/recheck/move/remove/re-add checked",
            }
        ),
        flush=True,
    )


def connect_and_send(conn, port, token, torrent_id, start):
    conn.setblocking(True)
    conn.settimeout(5)
    conn.connect(("127.0.0.1", port))
    conn.sendall(
        (
            f"GET /probe?torrent={torrent_id}&file=0 HTTP/1.1\r\n"
            f"Host: 127.0.0.1\r\nAuthorization: Bearer {token}\r\n"
            f"Range: bytes={start}-\r\n\r\n"
        ).encode()
    )


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="deluge-stream-phase0-", dir=args.profile_root or None
    ) as directory:
        root = Path(directory)
        profile = root / "profile"
        profile.mkdir()
        plugins = profile / "plugins"
        plugins.mkdir()
        shutil.copy2(args.egg, plugins)
        deluge.configmanager.set_config_dir(str(profile))
        daemon = Daemon(standalone=True)
        daemon.start()
        yield component.start()
        manager = component.get("CorePluginManager")
        try:
            assert "Stream" in manager.get_available_plugins()
            baseline = len(component.get("AlertManager").handlers["read_piece"])
            for _ in range(20):
                assert (yield manager.enable_plugin("Stream"))
                plugin = manager.plugins["Stream"].plugin
                assert (
                    str(plugins) in plugin.__class__.__module__
                    or str(plugins)
                    in __import__(
                        plugin.__class__.__module__, fromlist=["__file__"]
                    ).__file__
                )
                assert "stream.probe_info" in daemon.rpcserver.get_method_list()
                assert plugin.probe_info()["port"] > 0
                assert (yield manager.disable_plugin("Stream"))
                assert "stream.probe_info" not in daemon.rpcserver.get_method_list()
                assert (
                    len(component.get("AlertManager").handlers["read_piece"])
                    == baseline
                )
                assert not plugin.listener.connected
            print(
                "SMOKE: installed egg; 20 enable/disable cycles; RPC registered; "
                "no read handlers/listeners leaked",
                flush=True,
            )
            if args.mode == "integration":
                assert (yield manager.enable_plugin("Stream"))
                plugin = manager.plugins["Stream"].plugin
                yield integration(reactor, daemon, plugin, root)
                assert (yield manager.disable_plugin("Stream"))
        finally:
            yield component.shutdown()


task.react(main)
