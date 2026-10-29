"""Exercise product Compose in an isolated project; delete only its test volumes."""

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path


class RPC:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}/json"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, *params):
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"method": method, "params": params, "id": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=5) as response:
            result = json.load(response)
        if result["error"]:
            raise RuntimeError(result["error"])
        return result["result"]


def until(function):
    deadline = time.monotonic() + 60
    last = None
    while time.monotonic() < deadline:
        try:
            result = function()
            if result:
                return result
        except (OSError, RuntimeError) as exc:
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"Compose readiness timeout: {last}")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    project = "stream-test-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="stream-compose-") as temporary:
        password = Path(temporary) / "rpc-password"
        password.write_text(secrets.token_hex(32))
        password.chmod(0o644)  # compose bind-secret must be readable by the chosen UID
        web_port, http_port, peer_port = free_port(), free_port(), free_port()
        env = dict(
            os.environ,
            STREAM_IMAGE=args.image,
            DOCKER_DEFAULT_PLATFORM=args.platform,
            STREAM_RPC_PASSWORD_PATH=str(password),
            STREAM_WEB_PORT=str(web_port),
            STREAM_HTTP_PORT=str(http_port),
            STREAM_PEER_PORT=str(peer_port),
            STREAM_BIND_IP="127.0.0.1",
            PUID="1000",
            PGID="1000",
        )

        def compose(*command, **kwargs):
            return subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project,
                    "-f",
                    str(root / "compose.yml"),
                    *command,
                ],
                env=env,
                cwd=root,
                check=True,
                timeout=180,
                **kwargs,
            )

        def connect():
            rpc = RPC(web_port)
            until(lambda: rpc.call("auth.login", "deluge"))
            hosts = rpc.call("web.get_hosts")
            until(lambda: rpc.call("web.connect", hosts[0][0]))
            until(lambda: rpc.call("stream.get_web_status")["available"])
            return rpc

        try:
            compose("up", "-d", "--no-build")
            rpc = connect()
            rpc.call(
                "stream.set_config",
                1,
                {
                    "torrent_owner": "stream",
                    "download_root": "/downloads",
                    "public_base_url": f"http://127.0.0.1:{http_port}",
                },
            )
            payload = b"compose-persistence-fixture"
            compose(
                "exec",
                "-T",
                "--user",
                "1000:1000",
                "core",
                "python",
                "-c",
                "from pathlib import Path; "
                f"Path('/downloads/persist.bin').write_bytes({payload!r})",
            )
            raw = (
                b"d4:infod6:lengthi"
                + str(len(payload)).encode()
                + b"e4:name11:persist.bin12:piece lengthi16384e6:pieces20:"
                + hashlib.sha1(payload).digest()
                + b"ee"
            )
            tid = rpc.call(
                "core.add_torrent_file",
                "fixture.torrent",
                base64.b64encode(raw).decode(),
                {
                    "download_location": "/downloads",
                    "owner": "stream",
                    "add_paused": False,
                    "auto_managed": False,
                },
            )
            assert tid
            until(
                lambda: rpc.call("core.get_torrent_status", tid, ["progress"])[
                    "progress"
                ]
                == 100
            )
            status = until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))
            rid = status["record_id"]
            rpc.call("stream.set_retention", 1, tid, rid, True)
            rpc.call("stream.set_published", 1, tid, rid, True)
            before = rpc.call("stream.get_status", 1, [tid])[tid]
            compose("down")  # no -v: actual persistent data survives
            compose("up", "-d", "--no-build")
            rpc = connect()
            after = until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))
            for key in ("record_id", "is_temporary", "published"):
                assert after[key] == before[key], (key, before, after)
            config = rpc.call("stream.get_config", 1)
            assert config["download_root"] == "/downloads"
            ticket = rpc.call("stream.create_download_ticket", 1, tid, rid, "file", 1)
            with urllib.request.urlopen(ticket["url"], timeout=10) as response:
                assert response.read() == payload
            compose(
                "exec",
                "-T",
                "web",
                "python",
                "-c",
                "from pathlib import Path; assert not Path('/downloads').exists()",
            )
            print(
                "COMPOSE: non-root core/web, separate payload FS, recreation preserves "
                "instance/temporary/publication/settings/payload; "
                "direct ticket bytes passed"
            )
        except BaseException:
            compose("logs", "--no-color")
            raise
        finally:
            compose("down", "--volumes")  # only the random test project's volumes


if __name__ == "__main__":
    main()
