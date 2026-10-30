"""Installed native playback load; timings are local evidence, not HDD acceptance."""

import argparse
import base64
import importlib.util
import json
import logging
import math
import os
import platform
import resource
import socket
import tempfile
import time
import urllib.request
from pathlib import Path

import deluge.component as component
import libtorrent as lt
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "performance_helpers", Path(__file__).with_name("research.py")
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

BLOCK = bytes(range(256)) * 256
FILE_SIZE = 64 * 1024**2
RATE = 50_000_000 / 8


def fixture(root, piece_kib):
    directory = root / "downloads"
    directory.mkdir()
    with (directory / "load.bin").open("wb") as stream:
        for _ in range(FILE_SIZE // len(BLOCK)):
            stream.write(BLOCK)
    storage = lt.file_storage()
    storage.add_file("load.bin", FILE_SIZE)
    creator = lt.create_torrent(storage, piece_kib * 1024, lt.create_torrent.v1_only)
    lt.set_piece_hashes(creator, str(directory))
    return lt.bencode(creator.generate())


def p95(values):
    if not values:
        raise AssertionError("no timing samples")
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def playback(url, seconds):
    start = time.monotonic()
    total = 0
    first_bytes = []
    while time.monotonic() - start < seconds:
        opened = time.monotonic()
        with urllib.request.urlopen(url, timeout=10) as response:
            assert response.status == 200
            first = response.read(1)
            assert first == BLOCK[:1]
            first_bytes.append(time.monotonic() - opened)
            offset = 1
            total += 1
            while time.monotonic() - start < seconds:
                data = response.read(len(BLOCK))
                if not data:
                    assert offset == FILE_SIZE
                    break
                expected = (BLOCK + BLOCK)[offset % len(BLOCK) :][: len(data)]
                assert data == expected, "playback data corruption"
                offset += len(data)
                total += len(data)
                # Fixed-rate consumer, bounded chunk size; sleep only in this worker.
                delay = total / RATE - (time.monotonic() - start)
                if delay > 0:
                    time.sleep(min(delay, 0.1))
    return {
        "bytes": total,
        "seconds": time.monotonic() - start,
        "first_byte_seconds": first_bytes,
    }


def seek_cancel(url, cycles):
    timings = []
    for index in range(cycles):
        offset = (index * 104729) % (FILE_SIZE - 1024**2)
        request = urllib.request.Request(
            url, headers={"Range": f"bytes={offset}-{offset + 1024**2 - 1}"}
        )
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 206
            data = response.read(257)
            timings.append(time.monotonic() - start)
            assert data == (BLOCK + BLOCK)[offset % len(BLOCK) :][:257]
            # Deliberately close without consuming the rest of the Range response.
    return timings


def process_usage():
    return {
        "rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "fds": len(os.listdir("/proc/self/fd")),
        "threads": len(os.listdir("/proc/self/task")),
    }


class TransferLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        self.events.append(record.getMessage())
        self.events = self.events[-32:]


@defer.inlineCallbacks
def run(reactor, root, args):
    daemon, _, _ = yield native.start(reactor, root, args.egg)
    plugin = component.get("CorePluginManager").plugins["Stream"].plugin
    server, policy = plugin.server, plugin.policy
    transfer_log = TransferLog()
    logger = logging.getLogger("deluge_stream.streaming")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(transfer_log)
    started = time.monotonic()
    reactor_started = reactor.seconds()
    clock_drift = []
    clocks, payload, rss = [], [], []
    disk_blocks = []
    ticker = None
    sampler = None
    alerts = component.get("AlertManager")

    def stats(alert):
        if "disk.disk_blocks_in_use" in alert.values:
            disk_blocks.append(alert.values["disk.disk_blocks_in_use"])

    alerts.register_handler("session_stats_alert", stats)
    try:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        yield policy.set_config(
            {
                "bind_host": "127.0.0.1",
                "bind_port": port,
                "public_base_url": base,
                "torrent_owner": "localclient",
                "download_root": str(root / "downloads"),
                "stream_window_mib": 2,
                "initial_prebuffer_mib": 1,
            },
            ("localclient", True),
        )
        yield server.configure()
        raw = yield threads.deferToThread(fixture, root, args.piece_kib)
        tid = daemon.core.add_torrent_file(
            "load.torrent",
            base64.b64encode(raw),
            {
                "owner": "localclient",
                "download_location": str(root / "downloads"),
                "auto_managed": False,
                "add_paused": False,
            },
        )
        torrent = component.get("TorrentManager").torrents[tid]
        yield native.until(reactor, lambda: torrent.handle.status().is_seeding)
        yield native.until(reactor, lambda: tid in policy.rows)
        yield policy.mutate(
            tid, policy.rows[tid]["record_id"], ("localclient", True), published=True
        )
        url = base + f"/play/{tid}/1"
        # Warm thread pool, HTTP and native disk cache before descriptor baseline.
        yield threads.deferToThread(seek_cancel, url, 10)
        yield native.until(
            reactor,
            lambda: not server.streams.sessions
            and server.streams.diagnostics()["piece_jobs"] == 0
            and server.streams.diagnostics()["direct_jobs"] == 0,
        )
        yield task.deferLater(reactor, 0.5, lambda: None)
        before = yield threads.deferToThread(process_usage)
        previous = [time.monotonic()]

        def tick():
            now = time.monotonic()
            clocks.append(max(0, now - previous[0] - 0.02))
            clock_drift.append(reactor.seconds() - reactor_started - (now - started))
            previous[0] = now
            payload.append(server.streams.diagnostics()["payload_bytes_reserved"])

        @defer.inlineCallbacks
        def sample():
            rss.append((yield threads.deferToThread(process_usage)))
            daemon.core.session.post_session_stats()

        ticker = task.LoopingCall(tick)
        ticker.start(0.02, now=False)
        sampler = task.LoopingCall(sample)
        sampler.start(1, now=True)
        streams = yield defer.gatherResults(
            [
                threads.deferToThread(playback, url, args.seconds),
                threads.deferToThread(playback, url, args.seconds),
            ],
            consumeErrors=True,
        )
        seeks = yield threads.deferToThread(seek_cancel, url, args.cycles)
        yield native.until(
            reactor,
            lambda: not server.streams.sessions
            and server.streams.diagnostics()["piece_jobs"] == 0
            and server.streams.diagnostics()["direct_jobs"] == 0,
        )
        yield task.deferLater(reactor, 1, lambda: None)
        after = yield threads.deferToThread(process_usage)
        diag = server.streams.diagnostics()
        rates = [item["bytes"] * 8 / item["seconds"] / 1_000_000 for item in streams]
        first = seeks + [
            value for item in streams for value in item["first_byte_seconds"]
        ]
        result = {
            "scope": "isolated local ready-data native playback; not target HDD/NAS",
            "arch": platform.machine(),
            "python": platform.python_version(),
            "libtorrent": lt.__version__,
            "duration_seconds": args.seconds,
            "piece_kib": args.piece_kib,
            "seek_cancel_cycles": args.cycles,
            "streams_mbit_s": rates,
            "ready_first_byte_p95_seconds": p95(first),
            "reactor_lag_p95_seconds": p95(clocks),
            "reactor_samples": len(clocks),
            "reactor_clock_drift_max_seconds": max(map(abs, clock_drift), default=0),
            "payload_peak_bytes": max(payload),
            "before": before,
            "after": after,
            "rss_samples": rss,
            "native_disk_blocks_peak": max(disk_blocks, default=None),
            "native_memory_scope": (
                "disk.disk_blocks_in_use only, not total libtorrent memory"
            ),
            "final_streaming": diag,
            "full_duration": args.seconds >= 900 and args.cycles >= 1000,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        yield threads.deferToThread(
            args.output.write_text, json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        print(
            json.dumps(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"rss_samples", "final_streaming"}
                },
                sort_keys=True,
            )
        )
        assert min(rates) >= 49.5, rates  # report exact rates; 1% sampling tolerance
        assert p95(first) <= 1 and p95(clocks) <= 0.1
        assert max(payload) <= policy.config["payload_buffer_budget_mib"] * 1024**2
        assert diag["payload_bytes_reserved"] == 0
        assert diag["deadlines"] == 0
        assert after["fds"] <= before["fds"], (before, after)
    except BaseException as exc:
        failure = {
            "passed": False,
            "elapsed_seconds": time.monotonic() - started,
            "reactor_elapsed_seconds": reactor.seconds() - reactor_started,
            "reactor_clock_drift_max_seconds": max(map(abs, clock_drift), default=0),
            "error": str(exc),
            "final_streaming": server.streams.diagnostics(),
            "recent_transfers": transfer_log.events,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        yield threads.deferToThread(
            args.output.write_text, json.dumps(failure, indent=2) + "\n"
        )
        print(json.dumps(failure), flush=True)
        raise
    finally:
        logger.removeHandler(transfer_log)
        logger.setLevel(previous_level)
        if ticker and ticker.running:
            ticker.stop()
        if sampler and sampler.running:
            sampler.stop()
        alerts.deregister_handler(stats)
        yield native.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument(
        "--piece-kib", type=int, choices=[16, 64, 256, 1024], default=64
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600 or not 1 <= args.cycles <= 10000:
        parser.error("seconds must be 1..3600, cycles 1..10000")
    with tempfile.TemporaryDirectory(
        prefix="stream-load-", dir=args.profile_root or None
    ) as directory:
        task.react(run, [Path(directory), args])
