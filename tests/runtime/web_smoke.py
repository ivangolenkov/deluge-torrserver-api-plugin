"""Two actual processes, separate profiles, installed egg and authenticated JSON."""

import argparse
import http.cookiejar
import json
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from twisted.internet import defer, task, threads

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
parser.add_argument("--profile-root", default="")
args = parser.parse_args()


def free_port():
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        return reserve.getsockname()[1]


class JsonClient:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, *params):
        request = urllib.request.Request(
            self.url + "/json",
            data=json.dumps(
                {
                    "method": method,
                    "params": list(params),
                    "id": 1,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)

    def script(self):
        with self.opener.open(self.url + "/js/stream/stream.js", timeout=5) as response:
            return response.read()


@defer.inlineCallbacks
def main(reactor):
    with tempfile.TemporaryDirectory(
        prefix="stream-core-web-", dir=args.profile_root or None
    ) as directory:
        root = Path(directory)
        core, web = root / "core", root / "web"
        for profile in (core, web):
            (profile / "plugins").mkdir(parents=True)
            shutil.copy2(args.egg, profile / "plugins")
        password = secrets.token_hex(32)
        (core / "auth").write_text(f"streamtest:{password}:10\n")
        (core / "auth").chmod(0o600)
        core_port, web_port = free_port(), free_port()
        processes = []
        logs = []
        try:
            for executable, profile, port in (
                ("deluged", core, core_port),
                ("deluge-web", web, web_port),
            ):
                log = (profile / "process.log").open("w+")
                logs.append(log)
                command = [
                    shutil.which(executable),
                    "-d",
                    "-c",
                    str(profile),
                    "-p",
                    str(port),
                    "-i",
                    "127.0.0.1",
                    "-L",
                    "error",
                ]
                if executable == "deluged":
                    command += ["-u", "127.0.0.1"]
                else:
                    command += ["--no-ssl"]
                processes.append(
                    subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                )
            browser = JsonClient(web_port)

            def request(method, *params):
                return threads.deferToThread(browser.call, method, *params)

            for _ in range(100):
                assert all(process.poll() is None for process in processes), (
                    "server exited"
                )
                try:
                    login = yield request("auth.login", "deluge")
                    if login.get("result") is True:
                        break
                except OSError:
                    pass
                yield task.deferLater(reactor, 0.1, lambda: None)
            else:
                raise AssertionError("cannot login to isolated deluge-web")
            added = yield request(
                "web.add_host", "127.0.0.1", core_port, "streamtest", password
            )
            assert added["result"][0] is True, added
            for _ in range(100):
                connected = yield request("web.connect", added["result"][1])
                if connected.get("result") and not connected.get("error"):
                    break
                yield task.deferLater(reactor, 0.1, lambda: None)
            else:
                raise AssertionError("web cannot connect to isolated daemon")
            for _ in range(20):
                enabled = yield request("core.enable_plugin", "Stream")
                assert enabled.get("result") is True, enabled
                for _ in range(100):
                    status = yield request("stream.get_web_status")
                    result = status.get("result")
                    if isinstance(result, dict) and result.get("available") is True:
                        break
                    yield task.deferLater(reactor, 0.05, lambda: None)
                else:
                    raise AssertionError(f"web handshake unavailable: {status}")
                assert result["api_version"] == 1 and result["reason"] == "ready"
                assert result["storage_active"] is True
                assert result["torrserver_active"] is False
                assert "token" not in result and "port" not in result
                configured = yield request(
                    "stream.set_config", 1, {"max_temporary_torrents": 4}
                )
                assert configured["result"]["max_temporary_torrents"] == 4
                assert (yield request("stream.get_config", 1))["result"][
                    "max_temporary_torrents"
                ] == 4
                script = yield threads.deferToThread(browser.script)
                assert b"Deluge.registerPlugin('Stream'" in script
                denied = yield threads.deferToThread(
                    JsonClient(web_port).call, "stream.get_web_status"
                )
                assert denied.get("error") and not denied.get("result"), denied
                bad_version = yield request("stream.get_diagnostics", 999)
                assert bad_version.get("error"), bad_version
                assert (yield request("core.disable_plugin", "Stream"))[
                    "result"
                ] is True
                for _ in range(100):
                    removed = yield request("stream.get_web_status")
                    if removed.get("error"):
                        break
                    yield task.deferLater(reactor, 0.05, lambda: None)
                else:
                    raise AssertionError("web RPC remained registered after disable")
                assert (yield request("web.connected"))["result"] is True
            assert (core / "stream.sqlite").exists()
            print(
                "CORE/WEB SMOKE: separate profiles/processes; authenticated handshake, "
                "packaged script, anonymous rejection, version mismatch and "
                "20 enable/disable cycles passed",
                flush=True,
            )
        except BaseException:
            if "browser" in locals():
                try:
                    diagnostic = yield threads.deferToThread(
                        browser.call, "web.get_plugin_resources", "Stream"
                    )
                    print("WEB RESOURCE DIAGNOSTIC:", diagnostic, flush=True)
                except Exception:
                    pass  # Preserve the original test failure.
            for log in logs:
                log.seek(0)
                print(log.read(), flush=True)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try:
                        yield threads.deferToThread(process.wait, 15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        yield threads.deferToThread(process.wait)
            for log in logs:
                log.close()


task.react(main)
