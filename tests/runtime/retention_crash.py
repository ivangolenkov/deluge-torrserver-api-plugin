"""SIGKILL at committed removal stages; native Deluge restore, no orphan unlink."""

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import deluge.component as component
from twisted.internet import defer, task, threads

spec = importlib.util.spec_from_file_location(
    "native_retention_helpers", Path(__file__).with_name("retention.py")
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
helpers = native.helpers


@defer.inlineCallbacks
def dirty(reactor, root, egg, cut):
    root.mkdir()
    daemon, _, _ = yield helpers.start(reactor, root, egg)
    s = component.get("CorePluginManager").plugins["Stream"].plugin.policy
    yield s.set_config({"max_temporary_torrents": 100}, native.ADMIN)
    a, paths = yield native.add(reactor, daemon, root, s, "v1", "first")
    b, _ = yield native.add(reactor, daemon, root, s, "v2", "second")
    row = yield native.temporary(s, a)
    yield native.temporary(s, b)
    sentinel = root / "download-first" / "sentinel"
    yield threads.deferToThread(sentinel.write_bytes, b"keep")
    state = {
        "tid": a.torrent_id,
        "record_id": row["record_id"],
        "fifo": row["fifo_sequence"],
        "paths": [str(p) for p in paths],
    }
    yield threads.deferToThread((root / "state.json").write_text, json.dumps(state))
    repo = s.worker.repository
    if cut == "prepared":
        original = repo.prepare_removal

        def prepare(*args):
            original(*args)
            os.kill(os.getpid(), signal.SIGKILL)

        repo.prepare_removal = prepare
    else:
        original_state = repo.removal_state

        def removal_state(operation, stage, error=""):
            original_state(operation, stage, error)
            if stage == cut:
                os.kill(os.getpid(), signal.SIGKILL)

        repo.removal_state = removal_state
    yield s.set_config({"max_temporary_torrents": 1}, native.ADMIN)
    yield task.deferLater(reactor, 20, lambda: None)
    raise AssertionError("crash cut was not reached")


@defer.inlineCallbacks
def recover(reactor, root, egg, cut):
    state = json.loads((root / "state.json").read_text())
    yield helpers.start(reactor, root, egg)
    s = component.get("CorePluginManager").plugins["Stream"].plugin.policy
    try:
        yield helpers.until(reactor, lambda: s.ready and s.retention.running is None)
        assert s.recovery_required
        assert (root / "download-first" / "sentinel").read_bytes() == b"keep"
        if cut in {"prepared", "submitting"}:
            row = s.rows[state["tid"]]
            assert row["record_id"] == state["record_id"]
            assert row["is_temporary"] and row["fifo_sequence"] == state["fifo"]
            assert all(Path(path).exists() for path in state["paths"])
        pending = s.retention.pending
        if cut == "prepared":
            assert pending == []  # no native submission, safe journal abort
        elif cut == "submitting":
            assert len(pending) == 1 and pending[0]["stage"] == "manual"
        else:
            assert not pending or pending[0]["stage"] == "manual"
        # Gate stays persisted; merely inspecting/reconciling does not confirm it.
        assert s.recovery_required
        print(
            json.dumps(
                {
                    "native_crash_cut": cut,
                    "recovery_required": True,
                    "pending": [item["stage"] for item in pending],
                    "foreign_sentinel": "preserved",
                    "orphan_unlink": False,
                }
            )
        )
    finally:
        yield helpers.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    parser.add_argument("--worker", choices=["dirty", "recover"])
    parser.add_argument("--root")
    parser.add_argument("--cut")
    args = parser.parse_args()
    if args.worker:
        task.react(
            dirty if args.worker == "dirty" else recover,
            [Path(args.root), args.egg, args.cut],
        )
    else:
        with tempfile.TemporaryDirectory(
            prefix="stream-retention-crash-", dir=args.profile_root or None
        ) as directory:
            common = [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--egg",
                str(Path(args.egg).resolve()),
            ]
            for cut in ("prepared", "submitting", "submitted"):
                root = str(Path(directory) / cut)
                for worker, code in (("dirty", -signal.SIGKILL), ("recover", 0)):
                    result = subprocess.run(
                        common + ["--worker", worker, "--root", root, "--cut", cut],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    assert result.returncode == code, result.stdout + result.stderr
                    if worker == "recover":
                        print(result.stdout, end="", flush=True)
