"""Install egg into a fresh deluged subprocess; exercise actual authenticated RPC."""

import argparse
import secrets
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from twisted.internet import defer, task, threads

parser = argparse.ArgumentParser()
parser.add_argument("--mode")
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
args = parser.parse_args()


@defer.inlineCallbacks
def main(reactor):
    from deluge.ui.client import client

    with tempfile.TemporaryDirectory(
        prefix="deluge-stream-daemon-", dir=args.profile_root or None
    ) as directory:
        profile = Path(directory)
        (profile / "plugins").mkdir()
        shutil.copy2(args.egg, profile / "plugins")
        password = secrets.token_hex(32)
        auth = profile / "auth"
        auth.write_text(f"localclient:{password}:10\n")
        auth.chmod(0o600)
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        with (profile / "daemon.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    shutil.which("deluged"),
                    "-d",
                    "-c",
                    str(profile),
                    "-p",
                    str(port),
                    "-u",
                    "127.0.0.1",
                    "-i",
                    "127.0.0.1",
                    "-L",
                    "error",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError("deluged exited during startup")
                    try:
                        yield client.connect("127.0.0.1", port, "localclient", password)
                    except Exception:
                        yield task.deferLater(reactor, 0.1, lambda: None)
                    else:
                        break
                else:
                    raise AssertionError("cannot connect to isolated deluged")
                assert (yield client.daemon.get_version()) == "2.2.0"
                assert "Stream" in (yield client.core.get_available_plugins())
                for _ in range(20):
                    assert (yield client.core.enable_plugin("Stream"))
                    info = yield client.stream.probe_info()
                    assert info["port"] > 0 and len(info["token"]) == 64
                    assert (yield client.core.disable_plugin("Stream"))
                    methods = yield client.daemon.get_method_list()
                    assert "stream.probe_info" not in methods
                    with socket.socket() as connection:
                        assert connection.connect_ex(("127.0.0.1", info["port"])) != 0
                assert "Stream" not in (yield client.core.get_enabled_plugins())
                print(
                    "DAEMON SMOKE: actual deluged process + authenticated RPC; "
                    "egg installed; 20 enable/disable cycles; listeners/RPC removed",
                    flush=True,
                )
                yield client.disconnect()
                process.terminate()
                code = yield threads.deferToThread(process.wait, 15)
                assert code == 0, code
            except BaseException:
                log.seek(0)
                print(log.read(), flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    yield threads.deferToThread(process.wait)


task.react(main)
