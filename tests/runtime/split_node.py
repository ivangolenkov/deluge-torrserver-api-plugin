"""A core node or a Web+browser node; payload exists only in the core container."""

import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import libtorrent as lt
from playwright.sync_api import sync_playwright

sys.path.insert(0, "/checks")
from web_browser import RPC, until  # noqa: E402

payload = b"separate-filesystem-fixture" * 128
raw = lt.bencode(
    {
        "info": {
            "name": "split.bin",
            "length": len(payload),
            "piece length": 16384,
            "pieces": hashlib.sha1(payload).digest(),
        }
    }
)


def profile(path):
    (path / "plugins").mkdir(parents=True)
    shutil.copy2("/Stream.egg", path / "plugins/Stream-0.0.1-py3.11.egg")


if sys.argv[1] == "core":
    root = Path("/isolated-core")
    profile(root)
    (root / "auth").write_text("split:isolated-test-password:10\n")
    (root / "auth").chmod(0o600)
    (root / "downloads").mkdir()
    (root / "downloads/split.bin").write_bytes(payload)
    os.execv(
        shutil.which("deluged"),
        [
            "deluged",
            "-d",
            "-c",
            str(root),
            "-p",
            "58846",
            "-i",
            "0.0.0.0",
            "-u",
            "0.0.0.0",
            "-L",
            "error",
        ],
    )
else:
    root = Path("/isolated-web")
    profile(root)
    assert not Path("/isolated-core").exists()
    process = subprocess.Popen(
        [
            shutil.which("deluge-web"),
            "-d",
            "-c",
            str(root),
            "-p",
            "8112",
            "-i",
            "127.0.0.1",
            "--no-ssl",
            "-L",
            "error",
        ]
    )
    try:
        rpc = RPC("http://127.0.0.1:8112")
        for _ in range(100):
            try:
                if rpc.call("auth.login", "deluge"):
                    break
            except OSError:
                time.sleep(0.1)
        host = rpc.call(
            "web.add_host", "core", 58846, "split", "isolated-test-password"
        )[1]
        until(lambda: rpc.call("web.connect", host))
        rpc.call("web.set_config", {"default_daemon": host})
        assert rpc.call("core.enable_plugin", "Stream")
        until(lambda: rpc.call("stream.get_web_status")["available"])
        rpc.call(
            "stream.set_config",
            1,
            {
                "bind_host": "0.0.0.0",
                "bind_port": 8090,
                "public_base_url": "http://core:8090",
            },
        )
        tid = rpc.call(
            "core.add_torrent_file",
            "split.torrent",
            base64.b64encode(raw).decode(),
            {
                "download_location": "/isolated-core/downloads",
                "owner": "split",
                "add_paused": False,
                "auto_managed": False,
            },
        )
        until(
            lambda: rpc.call("core.get_torrent_status", tid, ["progress"])["progress"]
            == 100
        )
        until(lambda: rpc.call("stream.get_status", 1, [tid]).get(tid))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path="/usr/bin/chromium",
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(accept_downloads=True)
            page.goto("http://127.0.0.1:8112")
            page.evaluate("""async () => {
                await fetch('/json', {method:'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({method:'auth.login',
                        params:['deluge'],id:1})});
            }""")
            page.reload()
            page.get_by_text("No", exact=True).click()
            page.wait_for_function(
                "id => window.deluge && deluge.plugins.Stream && "
                "deluge.torrents.store.getById(id)",
                arg=tid,
            )
            page.evaluate(
                "id => {deluge.torrents.getSelectionModel().selectRecords("
                "[deluge.torrents.store.getById(id)]); "
                "deluge.details.setActiveTab(deluge.plugins.Stream.panel);}",
                tid,
            )
            page.wait_for_function(
                "deluge.plugins.Stream.panel.files.store.getCount() === 1"
            )
            page.evaluate(
                "deluge.plugins.Stream.panel.files.getSelectionModel().selectRow(0)"
            )
            for label, zipped in (("Скачать файл", False), ("Скачать ZIP", True)):
                with page.expect_download() as pending:
                    page.get_by_text(label, exact=True).click()
                download = pending.value
                assert download.url.startswith("http://core:8090/download/")
                data = Path(download.path()).read_bytes()
                if zipped:
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        assert archive.read("split.bin") == payload
                else:
                    assert data == payload
            browser.close()
        assert not Path("/isolated-core").exists()
        assert not (root / "downloads").exists()
        print(
            "SPLIT WEB: two containers, no shared payload filesystem; "
            "actual Chromium direct file/ZIP via core URL passed",
            flush=True,
        )
    finally:
        process.terminate()
        process.wait(15)
