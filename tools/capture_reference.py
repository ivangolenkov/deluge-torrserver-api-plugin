"""Optional isolated TorrServer wire research, never part of plugin runtime.

Run inside the phase-0 Linux container with --network none. The only torrent
and tracker are generated locally. No user profile or public swarm is used.
The reference executable is supplied explicitly; this tool downloads nothing.
"""

import argparse
import hashlib
import http.client
import json
import platform
import socket
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "MatriX.147.1"
BINARY_SHA256 = "f73ad19eaf7c78f753fd7a6e7c4da7849e77e79a4c39ea56fe73ce43ee7136c4"
LAMPA_COMMIT = "86f3afa5997793242cc2a081da96efb0934ae71f"
LAMPA_SHA256 = "b4a1384a5ebd220caa49451bdbcf5f6efc18d4bdc11777d456c0d988a9cbae75"
LIMIT = 2 << 20
HEADERS = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "transfermode.dlna.org",
    "contentfeatures.dlna.org",
    "getcontentfeatures.dlna.org",
    "range",
    "origin",
    "access-control-request-method",
    "access-control-request-headers",
}


def summarize(body, content_type):
    if not body:
        return {"kind": "empty", "bytes": 0}
    if "json" in content_type:
        try:
            return {"kind": "json", "value": json.loads(body)}
        except (ValueError, UnicodeDecodeError):
            pass
    if content_type.startswith("text/") or "mpegurl" in content_type:
        return {"kind": "text", "value": body.decode("utf-8")}
    return {
        "kind": "binary",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def headers(values):
    return {key.lower(): value for key, value in values if key.lower() in HEADERS}


class Stand:
    def __init__(self, binary, root, lampa=None):
        from deluge._libtorrent import lt

        self.lt = lt
        self.binary = Path(binary).resolve(strict=True)
        if hashlib.sha256(self.binary.read_bytes()).hexdigest() != BINARY_SHA256:
            raise ValueError(
                "reference binary SHA256 does not match the pinned release"
            )
        self.root = Path(root)
        (self.root / "db").mkdir()
        self.lampa = Path(lampa).resolve(strict=True) if lampa else None
        if (
            self.lampa
            and hashlib.sha256((self.lampa / "app.min.js").read_bytes()).hexdigest()
            != LAMPA_SHA256
        ):
            raise ValueError("Lampa app bundle differs from the pinned commit")
        self.cases = []
        self.lock = threading.Lock()
        self.process = None
        self.source = "scripted-reference-probe"
        self.label = "startup"
        self.finished = threading.Event()
        self.payload = bytes(range(256)) * (4 << 12)
        (self.root / "fixture.mp4").write_bytes(self.payload)
        fs = lt.file_storage()
        fs.add_file("fixture.mp4", len(self.payload))
        torrent = lt.create_torrent(fs, 65536, lt.create_torrent.v1_only)
        torrent.add_tracker("http://127.0.0.1:18091/announce")
        lt.set_piece_hashes(torrent, str(self.root))
        self.metainfo = bytes(lt.bencode(torrent.generate()))
        info = lt.torrent_info(self.metainfo)
        self.hash = str(info.info_hash())
        self.seed = lt.session(
            {
                "listen_interfaces": "127.0.0.1:0",
                "enable_dht": False,
                "enable_lsd": False,
                "enable_upnp": False,
                "enable_natpmp": False,
            }
        )
        self.seed_handle = self.seed.add_torrent(
            {
                "ti": info,
                "save_path": str(self.root),
                "flags": lt.torrent_flags.seed_mode,
            }
        )
        deadline = time.monotonic() + 10
        while not self.seed.listen_port() or not self.seed_handle.status().is_seeding:
            if time.monotonic() > deadline:
                raise TimeoutError("local seed did not become ready")
            time.sleep(0.05)
        stand = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/fixture.torrent":
                    return self.local(stand.metainfo, "application/x-bittorrent")
                if path == "/announce":
                    peer = socket.inet_aton("127.0.0.1") + struct.pack(
                        "!H", stand.seed.listen_port()
                    )
                    return self.local(
                        bytes(
                            lt.bencode(
                                {
                                    "interval": 1,
                                    "complete": 1,
                                    "incomplete": 0,
                                    "peers": peer,
                                }
                            )
                        ),
                        "text/plain",
                    )
                if path.startswith("/lampa/"):
                    name = path.removeprefix("/lampa/")
                    allowed = {
                        "index.html",
                        "app.min.js",
                        "assembly.json",
                        "css/app.css",
                        "vender/jquery/jquery.js",
                        "vender/notify/notify.js",
                        "vender/scrollbar/jquery.scrollbar.js",
                        "vender/scrollbar/jquery.scrollbar.css",
                        "vender/navigator/navigator.js",
                        "vender/keypad/keypad.js",
                        "vender/keypad/style.css",
                        "webos/webOSTV.js",
                    }
                    if stand.lampa and name in allowed:
                        content_type = (
                            "text/html"
                            if name.endswith(".html")
                            else (
                                "application/json"
                                if name.endswith(".json")
                                else "text/css"
                                if name.endswith(".css")
                                else "application/javascript"
                            )
                        )
                        return self.local(
                            (stand.lampa / name).read_bytes(), content_type
                        )
                    self.send_error(404)
                    return
                self.proxy()

            def local(self, body, content_type):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path == "/__step":
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size < 1024:
                        self.send_error(400)
                        return
                    step = json.loads(self.rfile.read(size))
                    if step.get("requester") not in {
                        "lampa-module-browser",
                        "browser-scripted-fetch",
                    } or not isinstance(step.get("name"), str):
                        self.send_error(400)
                        return
                    stand.source, stand.label = step["requester"], step["name"]
                    self.local(b"ok", "text/plain")
                    return
                if self.path == "/__finish":
                    self.local(b"finished", "text/plain")
                    stand.finished.set()
                    return
                self.proxy()

            def do_HEAD(self):
                self.proxy()

            def do_OPTIONS(self):
                self.proxy()

            def proxy(self):
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    length < 0
                    or length > LIMIT
                    or self.headers.get("Transfer-Encoding")
                ):
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                conn = http.client.HTTPConnection("127.0.0.1", 18090, timeout=30)
                try:
                    conn.request(
                        self.command,
                        self.path,
                        body=body or None,
                        headers=dict(self.headers),
                    )
                    response = conn.getresponse()
                    data = response.read(LIMIT + 1)
                    if len(data) > LIMIT:
                        raise ValueError("response exceeds research capture limit")
                    entry = {
                        "name": stand.label,
                        "requester": stand.source,
                        "request": {
                            "method": self.command,
                            "path": self.path,
                            "headers": headers(self.headers.items()),
                            "body": summarize(body, "application/json"),
                        },
                        "response": {
                            "status": response.status,
                            "headers": headers(response.getheaders()),
                            "body": summarize(
                                data, response.getheader("Content-Type", "")
                            ),
                        },
                    }
                    with stand.lock:
                        if len(stand.cases) >= 1024:
                            raise ValueError("research capture case limit exceeded")
                        stand.cases.append(entry)
                    self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in {
                            "transfer-encoding",
                            "connection",
                            "content-length",
                        }:
                            self.send_header(key, value)
                    self.send_header(
                        "Content-Length",
                        str(
                            response.getheader("Content-Length", 0)
                            if self.command == "HEAD"
                            else len(data)
                        ),
                    )
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                finally:
                    conn.close()

        self.http = ThreadingHTTPServer(("0.0.0.0", 18091), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def start(self):
        self.log = (self.root / "reference.log").open("ab")
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "--ip",
                "127.0.0.1",
                "--port",
                "18090",
                "--torrentaddr",
                "127.0.0.1:18093",
                "--path",
                str(self.root / "db"),
            ],
            stdout=self.log,
            stderr=self.log,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError((self.root / "reference.log").read_text()[-6000:])
            try:
                with socket.create_connection(("127.0.0.1", 18090), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise TimeoutError("reference did not bind its loopback port")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if hasattr(self, "log"):
            self.log.close()

    def close(self):
        self.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.seed.pause()

    def request(self, name, path, body=None, method=None, extra=None, status=200):
        self.label = name
        conn = http.client.HTTPConnection("127.0.0.1", 18091, timeout=40)
        try:
            conn.request(
                method or ("POST" if body is not None else "GET"),
                path,
                body=json.dumps(body) if body is not None else None,
                headers={"Content-Type": "application/json", **(extra or {})},
            )
            response = conn.getresponse()
            data = response.read(LIMIT + 1)
            if response.status != status:
                raise AssertionError((name, response.status, data[:1000]))
            return (
                json.loads(data)
                if data and "json" in response.getheader("Content-Type", "")
                else data
            )
        finally:
            conn.close()

    def probe(self):
        assert self.request("echo", "/echo").decode() == VERSION
        self.request("settings-get", "/settings", {"action": "get"})
        self.request("list-empty", "/torrents", {"action": "list"})
        self.request(
            "get-missing", "/torrents", {"action": "get", "hash": "0" * 40}, status=404
        )
        self.request("unknown-action", "/torrents", {"action": "unknown"}, status=400)
        self.request(
            "cors",
            "/torrents",
            method="OPTIONS",
            status=204,
            extra={
                "Origin": "http://example.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        torrent = self.request(
            "add-local",
            "/torrents",
            {
                "action": "add",
                "link": "http://127.0.0.1:18091/fixture.torrent",
                "title": "Fixture",
                "data": "{}",
                "save_to_db": True,
            },
            extra={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        assert torrent["hash"] == self.hash
        deadline = time.monotonic() + 30
        while True:
            torrent = self.request(
                "get-files",
                "/torrents",
                {
                    "action": "get",
                    "hash": self.hash,
                },
            )
            if torrent.get("file_stats"):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("reference metadata not ready")
            time.sleep(0.1)
        index = torrent["file_stats"][0]["id"]
        stream = f"/stream/fixture.mp4?link={self.hash}&index={index}"
        self.request("stat", stream + "&stat")
        self.request("preload", stream + "&preload")
        self.request("cache", "/cache", {"action": "get", "hash": self.hash})
        for name, value, expected in [
            ("range-first", "bytes=0-0", self.payload[:1]),
            ("range-seek", "bytes=65530-65550", self.payload[65530:65551]),
            ("range-suffix", "bytes=-17", self.payload[-17:]),
        ]:
            actual = self.request(
                name,
                stream + "&play",
                extra={
                    "Range": value,
                    "getcontentFeatures.dlna.org": "1",
                },
                status=206,
            )
            assert actual == expected
        self.request("head", stream + "&play", method="HEAD")
        self.request("playlist", f"/playlist?hash={self.hash}")
        self.request(
            "viewed-before-set", "/viewed", {"action": "list", "hash": self.hash}
        )
        self.request(
            "viewed-set",
            "/viewed",
            {
                "action": "set",
                "hash": self.hash,
                "file_index": index,
                "timecode": 12.5,
            },
        )
        viewed = self.request(
            "viewed-list",
            "/viewed",
            {
                "action": "list",
                "hash": self.hash,
            },
        )
        assert viewed and viewed[0]["file_index"] == index
        self.stop()
        self.start()
        assert (
            self.request(
                "viewed-after-restart",
                "/viewed",
                {
                    "action": "list",
                    "hash": self.hash,
                },
            )
            == viewed
        )
        self.request(
            "viewed-rem",
            "/viewed",
            {
                "action": "rem",
                "hash": self.hash,
                "file_index": index,
            },
        )
        assert self.request(
            "viewed-after-rem",
            "/viewed",
            {
                "action": "list",
                "hash": self.hash,
            },
        ) in ([], None)

    def save(self, output):
        data = {
            "provenance": {
                "kind": "reference-runtime-capture",
                "reference": VERSION,
                "reference_commit": "d84f41968780744d4b8643f7a73b22a84d2c3779",
                "binary_sha256": BINARY_SHA256,
                "platform": "linux/arm64",
                "python": platform.python_version(),
                "client_captured": any(
                    c["requester"] == "lampa-module-browser" for c in self.cases
                ),
                "num_verified": False,
                "plugin_verified": False,
                "full_ui_playback_verified": False,
                "fixture": {
                    "hash": self.hash,
                    "bytes": len(self.payload),
                    "sha256": hashlib.sha256(self.payload).hexdigest(),
                    "media_decodable": False,
                    "public_swarm": False,
                },
                "lampa": {
                    "commit": LAMPA_COMMIT,
                    "version": "3.4.4",
                    "bundle_sha256": LAMPA_SHA256,
                }
                if self.lampa
                else None,
            },
            "cases": self.cases,
        }
        with Path(output).open("x") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--lampa-root")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error("output already exists; choose a new capture path")
    with tempfile.TemporaryDirectory(prefix="torrserver-reference-") as root:
        stand = Stand(args.binary, root, args.lampa_root)
        try:
            stand.start()
            stand.probe()
            if args.serve:
                stand.source = "lampa-module-browser"
                stand.label = "browser-module-call"
                print(json.dumps({"ready": True, "hash": stand.hash}), flush=True)
                if not stand.finished.wait(timeout=600):
                    raise TimeoutError(
                        "browser capture not finished within 600 seconds"
                    )
            stand.save(args.output)
            print(f"Captured {len(stand.cases)} exchanges to {args.output}", flush=True)
        except Exception:
            print((stand.root / "reference.log").read_text()[-6000:], flush=True)
            raise
        finally:
            stand.close()


if __name__ == "__main__":
    main()
