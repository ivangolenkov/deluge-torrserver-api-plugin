"""Real Chromium + installed web/core artifacts, authenticated native UI actions."""

import argparse
import base64
import hashlib
import http.cookiejar
import io
import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import libtorrent as lt
from playwright.sync_api import sync_playwright


def free_port():
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        return reserve.getsockname()[1]


class RPC:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, *params):
        request = urllib.request.Request(
            self.base + "/json",
            json.dumps({"method": method, "params": list(params), "id": 1}).encode(),
            {"Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=10) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result["result"]


def until(check, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            value = check()
        except RuntimeError:
            value = False
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("browser runtime condition timed out")


def test(egg, root, executable):
    core, web = root / "core", root / "web"
    for profile in (core, web):
        (profile / "plugins").mkdir(parents=True)
        shutil.copy2(egg, profile / "plugins")
    (core / "auth").write_text("streamtest:isolated-test-password:10\n")
    (core / "auth").chmod(0o600)
    core_port, web_port, http_port = free_port(), free_port(), free_port()
    base = f"http://127.0.0.1:{web_port}"
    http = f"http://127.0.0.1:{http_port}"
    processes, logs = [], []
    try:
        for program, profile, port in (
            ("deluged", core, core_port),
            ("deluge-web", web, web_port),
        ):
            log = (profile / "process.log").open("w+")
            logs.append(log)
            command = [
                shutil.which(program),
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
            command += ["-u", "127.0.0.1"] if program == "deluged" else ["--no-ssl"]
            processes.append(
                subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            )
        rpc = RPC(base)

        def login():
            assert all(process.poll() is None for process in processes)
            try:
                return rpc.call("auth.login", "deluge")
            except OSError:
                return False

        until(login)
        host = rpc.call(
            "web.add_host",
            "127.0.0.1",
            core_port,
            "streamtest",
            "isolated-test-password",
        )[1]
        until(lambda: rpc.call("web.connect", host))
        rpc.call("web.set_config", {"default_daemon": host})
        assert rpc.call("core.enable_plugin", "Stream")
        until(lambda: rpc.call("stream.get_web_status")["available"])
        rpc.call(
            "stream.set_config",
            1,
            {
                "bind_host": "127.0.0.1",
                "bind_port": http_port,
                "public_base_url": http,
                "torrent_owner": "streamtest",
            },
        )
        payload = (b"browser-byte-fixture" * 220000)[: 4 * 1024**2]
        downloads = core / "downloads"
        downloads.mkdir()
        (downloads / "файл.bin").write_bytes(payload)
        raw = lt.bencode(
            {
                "info": {
                    "name": "файл.bin",
                    "length": len(payload),
                    "piece length": 16384,
                    "pieces": b"".join(
                        hashlib.sha1(payload[offset : offset + 16384]).digest()
                        for offset in range(0, len(payload), 16384)
                    ),
                }
            }
        )
        tid = rpc.call(
            "core.add_torrent_file",
            "fixture.torrent",
            base64.b64encode(raw).decode(),
            {
                "download_location": str(downloads),
                "owner": "streamtest",
                "add_paused": False,
                "auto_managed": False,
            },
        )
        until(
            lambda: rpc.call("core.get_torrent_status", tid, ["progress"])["progress"]
            == 100
        )
        rid = until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))[
            "record_id"
        ]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(error.stack))
            page.goto(base)
            page.evaluate("""async () => {
                await fetch('/json', {method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({method:'auth.login',
                        params:['deluge'], id:1})});
            }""")
            page.reload()
            page.get_by_text("No", exact=True).click()
            page.wait_for_function(
                "window.deluge && deluge.plugins && deluge.plugins.Stream "
                "&& deluge.plugins.Stream.panel",
                timeout=20000,
            )
            try:
                page.wait_for_function(
                    "id => !!deluge.torrents.store.getById(id)", arg=tid
                )
            except Exception:
                print("BROWSER ERRORS", errors, page.locator("body").inner_text())
                raise
            page.evaluate(
                "id => {deluge.torrents.getSelectionModel().selectRecords("
                "[deluge.torrents.store.getById(id)]); "
                "deluge.details.setActiveTab(deluge.plugins.Stream.panel);}",
                tid,
            )
            page.wait_for_function(
                "deluge.plugins.Stream.panel.row && "
                "deluge.plugins.Stream.panel.files.store.getCount() === 1"
            )
            assert "Постоянный" in page.locator("body").inner_text()
            page.evaluate(
                "deluge.plugins.Stream.panel.files.getSelectionModel().selectRow(0)"
            )
            try:
                with page.expect_download() as download:
                    page.get_by_text("Скачать файл", exact=True).click()
            except Exception:
                print("DOWNLOAD UI ERRORS", errors, page.locator("body").inner_text())
                raise
            result = download.value
            assert result.url.startswith(http + "/download/")
            assert Path(result.path()).read_bytes() == payload
            assert result.suggested_filename == "файл.bin"
            with page.expect_download() as download:
                page.get_by_text("Скачать ZIP", exact=True).click()
            with zipfile.ZipFile(
                io.BytesIO(Path(download.value.path()).read_bytes())
            ) as archive:
                assert archive.read("файл.bin") == payload and archive.testzip() is None
            page.evaluate(
                "deluge.details.setActiveTab(deluge.plugins.Stream.nativeFiles)"
            )
            page.wait_for_function(
                "deluge.plugins.Stream.nativeFiles.getRootNode().hasChildNodes()"
            )
            page.locator(".x-tree-node-leaf").get_by_text("файл.bin", exact=True).click(
                button="right"
            )
            with page.expect_download() as download:
                page.get_by_text("Скачать файл через Stream", exact=True).click()
            assert Path(download.value.path()).read_bytes() == payload
            assert rpc.call("core.get_torrent_status", tid, ["file_priorities"])[
                "file_priorities"
            ] == [4]
            page.evaluate("deluge.details.setActiveTab(deluge.plugins.Stream.panel)")
            sockets = []
            try:
                for kind in ("file", "zip"):
                    link = rpc.call("stream.create_download_ticket", 1, tid, rid, kind)[
                        "url"
                    ]
                    connection = socket.socket()
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                    connection.connect(("127.0.0.1", http_port))
                    connection.sendall(
                        (
                            "GET "
                            + link.removeprefix(http)
                            + " HTTP/1.1\r\nHost: localhost\r\n"
                            "Connection: close\r\n\r\n"
                        ).encode()
                    )
                    sockets.append(connection)
                page.wait_for_function(
                    "deluge.plugins.Stream.panel.row.file_downloads === 1 && "
                    "deluge.plugins.Stream.panel.row.zip_downloads === 1"
                )
                assert "Стриминг" not in page.evaluate(
                    "deluge.plugins.Stream.panel.info.getValue()"
                )
                sockets[0].close()
                page.wait_for_function(
                    "deluge.plugins.Stream.panel.row.file_downloads === 0 && "
                    "deluge.plugins.Stream.panel.row.zip_downloads === 1"
                )
            finally:
                for connection in sockets:
                    connection.close()
            page.wait_for_function(
                "deluge.plugins.Stream.panel.row.zip_downloads === 0"
            )
            page.evaluate(
                "deluge.menus.torrent.showAt([50,50]); "
                "deluge.plugins.Stream.menu.menu.showAt([180,50]);"
            )
            page.get_by_text("Опубликовать в TorrServer", exact=True).click()
            until(lambda: rpc.call("stream.get_status", 1, [tid])[tid]["published"])
            page.wait_for_function("deluge.plugins.Stream.panel.row.published")
            page.get_by_role("button", name="OK", exact=True).click()
            page.evaluate("deluge.plugins.Stream.mutate('set_retention', true)")
            until(lambda: rpc.call("stream.get_status", 1, [tid])[tid]["is_temporary"])
            page.get_by_role("button", name="OK", exact=True).click()
            page.evaluate("deluge.plugins.Stream.mutate('set_retention', false)")
            until(
                lambda: not rpc.call("stream.get_status", 1, [tid])[tid]["is_temporary"]
            )
            page.get_by_role("button", name="OK", exact=True).click()
            page.evaluate(
                "deluge.preferences.show(); deluge.preferences.selectPage('Stream');"
            )
            page.wait_for_function("!!deluge.plugins.Stream.page.original")
            page.evaluate(
                "deluge.plugins.Stream.page.fields.max_temporary_torrents.setValue(7); "
                "deluge.plugins.Stream.page.onApply();"
            )
            until(
                lambda: rpc.call("stream.get_config", 1)["max_temporary_torrents"] == 7
            )
            page.evaluate(
                "deluge.plugins.Stream.page.fields.bind_port.setValue(0); "
                "deluge.plugins.Stream.page.onApply();"
            )
            page.wait_for_function(
                "deluge.plugins.Stream.page.info.getValue()"
                ".indexOf('Некорректные') >= 0"
            )
            assert rpc.call("stream.get_config", 1)["bind_port"] == http_port
            page.evaluate(
                "port => {deluge.plugins.Stream.page.fields.bind_port.setValue(port); "
                "deluge.plugins.Stream.page.onApply();}",
                web_port,
            )
            page.wait_for_function(
                "deluge.plugins.Stream.page.info.getValue().indexOf('порт занят') >= 0"
            )
            assert rpc.call("stream.get_diagnostics", 1)["http_error"] == "bind_failed"
            page.evaluate(
                "port => {deluge.plugins.Stream.page.fields.bind_port.setValue(port); "
                "deluge.plugins.Stream.page.onApply();}",
                http_port,
            )
            until(lambda: rpc.call("stream.get_diagnostics", 1)["http_error"] is None)
            page.evaluate("deluge.preferences.hide()")
            incomplete_info = lt.bdecode(raw)
            incomplete_info[b"info"][b"name"] = b"incomplete.bin"
            incomplete = rpc.call(
                "core.add_torrent_file",
                "missing.torrent",
                base64.b64encode(lt.bencode(incomplete_info)).decode(),
                {
                    "download_location": str(downloads),
                    "add_paused": True,
                    "auto_managed": False,
                },
            )
            page.wait_for_function(
                "id => !!deluge.torrents.store.getById(id)", arg=incomplete
            )
            page.evaluate(
                "id => {deluge.torrents.getSelectionModel().selectRecords("
                "[deluge.torrents.store.getById(id)]); "
                "deluge.details.setActiveTab(deluge.plugins.Stream.panel);}",
                incomplete,
            )
            page.wait_for_function(
                "id => deluge.plugins.Stream.panel.torrentId === id && "
                "deluge.plugins.Stream.panel.row && "
                "deluge.plugins.Stream.panel.files.store.getCount() === 1 && "
                "!deluge.plugins.Stream.panel.files.store.getAt(0).get('ready')",
                arg=incomplete,
            )
            page.evaluate(
                "deluge.plugins.Stream.panel.files.getSelectionModel().selectRow(0)"
            )
            page.get_by_text("Скачать файл", exact=True).click()
            page.get_by_text(
                "Файл не загружен полностью. Догрузка не запускается.", exact=True
            ).wait_for()
            page.get_by_role("button", name="OK", exact=True).click()
            page.get_by_text("Скачать ZIP", exact=True).click()
            page.get_by_text(
                "ZIP недоступен: не готовы все файлы, включая пропущенные.", exact=True
            ).wait_for()
            page.get_by_role("button", name="OK", exact=True).click()
            assert (
                rpc.call("stream.get_status", 1, [incomplete])[incomplete][
                    "ticket_reservations"
                ]
                == 0
            )
            rpc.call("core.disable_plugin", "Stream")
            page.wait_for_function(
                "!deluge.plugins.Stream || !deluge.plugins.Stream.alive"
            )
            assert rpc.call("web.connected")
            assert (
                rpc.call("core.get_torrent_status", tid, ["name"])["name"] == "файл.bin"
            )
            assert not errors, errors
            print(
                "BROWSER:",
                browser.version,
                "real installed core/web; file Unicode+ZIP CRC; publication/settings; "
                "disable leaves native UI/RPC working",
                flush=True,
            )
            browser.close()
        assert not (web / "downloads").exists()
        assert rid
    except BaseException:
        for log in logs:
            log.seek(0)
            print(log.read(), flush=True)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(15)
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
    if not Path(args.browser).is_file():
        raise SystemExit(
            "Chromium is required: install chromium or use tools/runtime.Dockerfile"
        )
    with tempfile.TemporaryDirectory(
        prefix="stream-browser-", dir=args.profile_root or None
    ) as directory:
        test(args.egg, Path(directory), args.browser)


if __name__ == "__main__":
    main()
