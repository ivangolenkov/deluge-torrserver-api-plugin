"""Real core/web/Chromium update and exec; isolated GitHub response fixtures."""

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from deluge._libtorrent import lt
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from web_browser import RPC, free_port, until  # noqa: E402

TARGET = "9999.0.1"


def fixture_egg(source, destination, fixtures, version=None):
    # Only network acquisition is replaced. The installed validator, stage,
    # filesystem commit, RPC, shutdown, SQLite and exec code are exercised.
    injection = f"""
def fetch(url, limit, cancelled):
    filename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[1]
    name = "latest.json" if url == API else filename
    path = Path({str(fixtures)!r}) / name
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError("update_invalid")
    return data
"""
    with (
        zipfile.ZipFile(source) as original,
        zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as result,
    ):
        for name in original.namelist():
            if name.endswith(".pyc"):
                continue
            data = original.read(name)
            if name == "deluge_stream/updates.py":
                data += injection.encode()
            if version and name == "deluge_stream/_version.py":
                data = re.sub(
                    rb'__version__ = "[^"]+"',
                    f'__version__ = "{version}"'.encode(),
                    data,
                )
            if version and name == "EGG-INFO/PKG-INFO":
                data = re.sub(
                    rb"(?m)^Version: .+$", f"Version: {version}".encode(), data
                )
            result.writestr(name, data)


def test(egg, root, browser_path):
    fixtures = root / "fixtures"
    fixtures.mkdir()
    core, web = root / "core", root / "web"
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidate = fixtures / f"Stream-{TARGET}-py{minor}.egg"
    fixture_egg(egg, candidate, fixtures, TARGET)
    for profile in (core, web):
        (profile / "plugins").mkdir(parents=True)
        fixture_egg(egg, profile / "plugins" / Path(egg).name, fixtures)
    (core / "auth").write_text("admin:test-password:10\nreader:reader-password:5\n")
    with zipfile.ZipFile(egg) as archive:
        namespace = {}
        exec(archive.read("deluge_stream/runtime_profiles.py"), namespace)
        contract = namespace["RUNTIME_PROFILES"][minor]
    manifest = {
        "schema": 1,
        "version": TARGET,
        "artifact": candidate.name,
        "artifact_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "runtime_contract": contract,
    }
    manifest_path = fixtures / "compatibility.json"
    manifest_path.write_text(json.dumps(manifest))
    base = f"https://github.com/ivangolenkov/deluge-torrserver-api-plugin/releases/download/v{TARGET}/"
    release = {
        "id": 100,
        "tag_name": "v" + TARGET,
        "draft": False,
        "prerelease": False,
        "assets": [],
    }
    for path in (candidate, manifest_path):
        data = path.read_bytes()
        release["assets"].append(
            {
                "name": path.name,
                "state": "uploaded",
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "browser_download_url": base + path.name,
            }
        )
    (fixtures / "latest.json").write_text(json.dumps(release))
    core_port, web_port, http_port = free_port(), free_port(), free_port()
    processes, logs = [], []
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
                "info",
            ]
            command += ["-u", "127.0.0.1"] if executable == "deluged" else ["--no-ssl"]
            processes.append(
                subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            )
        rpc = RPC(f"http://127.0.0.1:{web_port}")

        def login():
            assert all(process.poll() is None for process in processes)
            try:
                return rpc.call("auth.login", "deluge")
            except OSError:
                return False

        until(login)
        host = rpc.call(
            "web.add_host", "127.0.0.1", core_port, "admin", "test-password"
        )[1]
        until(lambda: rpc.call("web.connect", host))
        rpc.call("web.set_config", {"default_daemon": host})
        assert rpc.call("core.enable_plugin", "Stream")
        until(lambda: rpc.call("stream.get_web_status")["available"])
        reader = rpc.call(
            "web.add_host", "127.0.0.1", core_port, "reader", "reader-password"
        )[1]
        rpc.call("web.disconnect")
        until(lambda: rpc.call("web.connect", reader))
        until(lambda: rpc.call("stream.get_web_status")["available"])
        for method, params in (
            ("stream.check_plugin_update", []),
            ("stream.update_check", [1]),
        ):
            try:
                rpc.call(method, *params)
            except RuntimeError:
                pass
            else:
                raise AssertionError("non-admin admitted to updater")
        rpc.call("web.disconnect")
        until(lambda: rpc.call("web.connect", host))
        until(lambda: rpc.call("stream.get_web_status")["available"])
        rpc.call(
            "stream.set_config",
            1,
            {
                "bind_host": "127.0.0.1",
                "bind_port": http_port,
                "max_temporary_torrents": 17,
                "torrent_owner": "admin",
            },
        )
        payload = b"test"
        downloads = core / "downloads"
        downloads.mkdir()
        (downloads / "fixture.bin").write_bytes(payload)
        raw = lt.bencode(
            {
                "info": {
                    "name": "fixture.bin",
                    "length": 4,
                    "piece length": 16384,
                    "pieces": hashlib.sha1(payload).digest(),
                }
            }
        )
        tid = rpc.call(
            "core.add_torrent_file",
            "fixture.torrent",
            base64.b64encode(raw).decode(),
            {
                "download_location": str(downloads),
                "add_paused": False,
                "auto_managed": False,
                "owner": "admin",
            },
        )
        until(
            lambda: rpc.call("core.get_torrent_status", tid, ["progress"])["progress"]
            == 100
        )
        rid = until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))[
            "record_id"
        ]
        rpc.call("stream.set_retention", 1, tid, rid, True)
        rpc.call("stream.set_published", 1, tid, rid, True)
        metadata = rpc.call("stream.get_metadata", 1, tid, rid)
        metadata = rpc.call(
            "stream.set_metadata",
            1,
            tid,
            rid,
            metadata["version"],
            {"title": "Before update", "data": "keep\r\nraw"},
        )
        before = rpc.call("stream.get_status", 1, [tid])[tid]
        pids = [process.pid for process in processes]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=browser_path,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(error.stack))
            page.goto(rpc.base)
            page.evaluate(
                """async () => { await fetch('/json', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({method:'auth.login',
                params:['deluge'], id:1})}); }"""
            )
            page.reload()
            page.get_by_text("No", exact=True).click()
            page.wait_for_function(
                "window.deluge && deluge.plugins && deluge.plugins.Stream "
                "&& deluge.plugins.Stream.page"
            )
            page.evaluate(
                "deluge.preferences.show(); deluge.preferences.selectPage('Stream');"
            )
            page.get_by_role("button", name="Проверить обновления", exact=True).click()
            page.wait_for_function(
                "deluge.plugins.Stream.page.availableVersion === '9999.0.1'"
            )
            assert "Доступна версия" in page.locator("body").inner_text()
            page.get_by_role("button", name="Установить обновление", exact=True).click()
            page.get_by_text("No", exact=True).click()
            assert not (core / "plugins" / candidate.name).exists()
            # A corrupt download fails before either process is changed/restarted.
            candidate_bytes = candidate.read_bytes()
            candidate.write_bytes(bytes([candidate_bytes[0] ^ 1]) + candidate_bytes[1:])
            page.get_by_role("button", name="Установить обновление", exact=True).click()
            page.get_by_text("Yes", exact=True).click()
            page.wait_for_function(
                "deluge.plugins.Stream.page.updateInfo.getValue()"
                ".indexOf('Контрольная сумма') >= 0"
            )
            assert not (core / "plugins" / candidate.name).exists()
            assert not (web / "plugins" / candidate.name).exists()
            assert all(process.poll() is None for process in processes)
            candidate.write_bytes(candidate_bytes)
            page.get_by_role("button", name="Проверить обновления", exact=True).click()
            page.wait_for_function(
                "!deluge.plugins.Stream.page.installUpdateButton.disabled"
            )
            page.get_by_role("button", name="Установить обновление", exact=True).click()
            page.get_by_text("Yes", exact=True).click()

            def updated():
                assert all(process.poll() is None for process in processes)
                try:
                    status = rpc.call("stream.get_web_status")
                    return status["available"] and status["plugin_version"] == TARGET
                except (OSError, RuntimeError):
                    return False

            until(updated, seconds=20)
            assert [process.pid for process in processes] == pids
            assert all(process.poll() is None for process in processes)
            after = until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))
            for key in ("record_id", "is_temporary", "published"):
                assert after[key] == before[key], (key, before, after)
            assert rpc.call("stream.get_metadata", 1, tid, rid) == metadata
            assert rpc.call("stream.get_config", 1)["max_temporary_torrents"] == 17
            assert not rpc.call("stream.get_diagnostics", 1)["recovery_required"]
            assert (downloads / "fixture.bin").read_bytes() == payload
            # Deluge may replace plugin objects on disconnect; pending state is
            # carried in this browser's sessionStorage across script reload.
            page.reload()
            page.wait_for_function(
                "window.deluge && deluge.plugins && deluge.plugins.Stream "
                "&& deluge.plugins.Stream.page"
            )
            page.wait_for_function(
                "deluge.plugins.Stream.page.updateInfo.getValue()"
                ".indexOf('Обновление завершено') >= 0",
                timeout=30000,
            )
            page.evaluate(
                "deluge.preferences.show(); deluge.preferences.selectPage('Stream');"
            )
            page.get_by_role("button", name="Проверить обновления", exact=True).click()
            page.wait_for_function(
                "deluge.plugins.Stream.page.updateInfo.getValue()"
                ".indexOf('Новых стабильных релизов нет') >= 0"
            )
            assert not errors, errors
            browser.close()
        for profile in (core, web):
            assert (
                profile / "plugins" / candidate.name
            ).read_bytes() == candidate.read_bytes()
            assert (profile / "plugins" / Path(egg).name).exists()
        print(
            "UPDATES: Chromium check/confirm/cancel/install; native core/web exec "
            "with same PIDs, selected new eggs, separate profiles, "
            "policy/metadata/payload preserved; fixture network only",
            flush=True,
        )
    except BaseException:
        if "rpc" in locals():
            for method in (
                "web.connected",
                "web.get_config",
                "stream.get_plugin_update_status",
                "stream.get_web_status",
            ):
                try:
                    print(method, rpc.call(method), flush=True)
                except Exception as exc:
                    print(method, str(exc), flush=True)
        for log in logs:
            log.seek(0)
            print(log.read(), flush=True)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--egg", required=True)
    parser.add_argument("--profile-root", default="")
    parser.add_argument("--browser", default="/usr/bin/chromium")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="stream-updates-", dir=args.profile_root or None
    ) as directory:
        test(args.egg, Path(directory), args.browser)


if __name__ == "__main__":
    main()
