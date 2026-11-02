import io
import json
import threading
import zipfile

import pytest

from deluge_stream import restart
from deluge_stream.updates import (
    API,
    UpdateStore,
    safe_url,
    select,
    sha,
    validate_egg,
    version_key,
)


def fixture(tmp_path, current="1.0.0"):
    runtime = {
        "os": "Linux",
        "architecture": "x86_64",
        "python": "3.11",
        "Deluge": "2.2.0",
        "Twisted": "24.11.0",
        "libtorrent": "2.0.8.0",
    }
    version = "1.1.0"
    name = "Stream-1.1.0-py3.11.egg"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("EGG-INFO/PKG-INFO", "Name: Stream\nVersion: 1.1.0\n")
        archive.writestr(
            "EGG-INFO/entry_points.txt", "[deluge.plugin.core]\n[deluge.plugin.web]\n"
        )
        archive.writestr("deluge_stream/_version.py", '__version__ = "1.1.0"\n')
    package = output.getvalue()
    contract = {key: value for key, value in runtime.items() if key != "architecture"}
    contract["architectures"] = ["x86_64", "aarch64"]
    manifest = {
        "schema": 2,
        "version": version,
        "artifacts": [
            {
                "artifact": name,
                "artifact_sha256": sha(package),
                "runtime_contract": contract,
            }
        ],
    }
    manifest_data = json.dumps(manifest).encode()
    base = "https://github.com/ivangolenkov/deluge-torrserver-api-plugin/releases/download/v1.1.0/"
    assets = []
    files = {}
    for filename, data in [(name, package), ("compatibility.json", manifest_data)]:
        url = base + filename
        assets.append(
            {
                "name": filename,
                "state": "uploaded",
                "size": len(data),
                "digest": "sha256:" + sha(data),
                "browser_download_url": url,
            }
        )
        files[url] = data
    release = {
        "id": 7,
        "tag_name": "v1.1.0",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }
    files[API] = json.dumps(release).encode()
    (tmp_path / "plugins").mkdir()
    store = UpdateStore(
        tmp_path, runtime, threading.Event(), lambda url, *_: files[url], current
    )
    return store, files, release, manifest


def test_prepare_commit_preserve_old_package_and_never_overwrite(tmp_path):
    store, _, _, _ = fixture(tmp_path)
    old = tmp_path / "plugins" / "Stream-1.0.0-py3.11.egg"
    old.write_bytes(b"old loaded code")
    plan = store.check()
    assert plan["available"]
    assert store.prepare(plan["token"])
    assert len(list(old.parent.glob("*.egg"))) == 1
    assert store.commit(plan["token"])["restart_required"]
    assert old.read_bytes() == b"old loaded code"
    assert store.commit(plan["token"])["installed"] == "1.1.0"
    destination = old.parent / "Stream-1.1.0-py3.11.egg"
    destination.unlink()  # Fault injection, never overwrite an existing candidate.
    destination.write_bytes(b"different")
    with pytest.raises(ValueError, match="update_conflict"):
        store.commit(plan["token"])
    assert destination.read_bytes() == b"different"
    store.clear()
    assert not list(old.parent.glob(".stream-update-*"))


def test_partial_update_can_repair_same_version_without_downgrade(tmp_path):
    store, files, _, _ = fixture(tmp_path, "1.1.0")
    same = store.check()
    assert not same["available"] and same["token"]
    assert store.prepare(same["token"])
    store.current = "2.0.0"
    assert not store.check()["available"] and store.plan is None
    files[API] = None
    assert store.check()["version"] is None


def test_bad_checksum_or_cancel_never_installs(tmp_path):
    store, files, release, _ = fixture(tmp_path)
    token = store.check()["token"]
    url = release["assets"][0]["browser_download_url"]
    files[url] = bytes([files[url][0] ^ 1]) + files[url][1:]
    with pytest.raises(ValueError, match="update_checksum"):
        store.prepare(token)
    assert not list((tmp_path / "plugins").iterdir())
    store.cancelled.set()
    with pytest.raises(ValueError, match="update_cancelled"):
        store.prepare(token)


def test_stale_token_and_corrupt_staging_are_rejected(tmp_path):
    store, _, _, _ = fixture(tmp_path)
    token = store.check()["token"]
    newer = store.check()["token"]
    with pytest.raises(ValueError, match="update_conflict"):
        store.prepare(token)
    store.prepare(newer)
    from pathlib import Path

    next(Path(store.staged.name).iterdir()).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="update_checksum"):
        store.commit(newer)
    assert not list((tmp_path / "plugins").glob("*.egg"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("python", "3.14"),
        ("architecture", "armv7l"),
        ("Twisted", "99"),
        ("libtorrent", "1.2"),
    ],
)
def test_incompatible_runtime_has_no_install_plan(tmp_path, field, value):
    store, _, _, _ = fixture(tmp_path)
    store.runtime[field] = value
    with pytest.raises(ValueError, match="update_incompatible"):
        store.check()
    assert store.plan is None


def test_web_without_libtorrent_and_duplicate_contract(tmp_path):
    store, _, release, manifest = fixture(tmp_path)
    store.runtime.pop("libtorrent")
    assert select(manifest, release, store.runtime)
    manifest["artifacts"] *= 2
    with pytest.raises(ValueError, match="update_incompatible"):
        select(manifest, release, store.runtime)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "file:///etc/passwd",
        "https://github.com.evil/a",
        "https://github.com@evil/a",
        "https://github.com:444/a",
        "https://localhost/a",
    ],
)
def test_untrusted_redirects(url):
    with pytest.raises(ValueError, match="update_invalid"):
        safe_url(url)


def test_versions_are_numeric_and_reject_arbitrary_tags():
    assert version_key("1.10.0") > version_key("1.9.9")
    assert version_key("1.0.0") > version_key("1.0.0rc9") > version_key("0.0.0.dev0")
    for value in ("latest", "01.2.3", "../../1.0.0", "1.0"):
        with pytest.raises(ValueError):
            version_key(value)


def test_unsafe_zip_and_wrong_metadata(tmp_path):
    path = tmp_path / "bad.egg"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(ValueError, match="update_invalid"):
        validate_egg(path, "1.0.0", "3.11")


def test_restart_reuses_interpreter_and_entrypoint_without_fork(monkeypatch, tmp_path):
    script = tmp_path / "deluged"
    script.touch()
    monkeypatch.setattr(restart.sys, "platform", "linux")
    monkeypatch.setattr(
        restart.sys, "argv", [str(script), "-c", "/profile", "-p", "12345"]
    )
    argv = restart.command("core")
    assert argv[1:] == [
        str(script),
        "-c",
        "/profile",
        "-p",
        "12345",
        "--do-not-daemonize",
    ]
    with pytest.raises(ValueError, match="restart_unsupported"):
        restart.command("web")


def test_restart_is_armed_only_after_response_delay(monkeypatch, tmp_path):
    from twisted.internet.task import Clock

    script = tmp_path / "deluge-web"
    script.touch()
    monkeypatch.setattr(restart.sys, "platform", "linux")
    monkeypatch.setattr(restart.sys, "argv", [str(script), "-d"])
    calls = []
    clock = Clock()
    clock.stop = lambda: calls.append("stop")
    monkeypatch.setattr(restart.atexit, "register", lambda *args: calls.append(args))
    restart.schedule(clock, "web")
    assert not calls
    clock.advance(1)
    assert calls[0][0] is restart.os.execv and calls[1] == "stop"


def test_network_body_limit_cancel_and_total_deadline(monkeypatch):
    from deluge_stream import updates

    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read1(self, size):
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            return Body()

    monkeypatch.setattr(updates.urllib.request, "build_opener", lambda *_: Opener())
    cancel = threading.Event()
    with pytest.raises(ValueError, match="update_invalid"):
        updates.fetch(API, 16, cancel)
    cancel.set()
    with pytest.raises(ValueError, match="update_cancelled"):
        updates.fetch(API, 16, cancel)
    cancel.clear()
    clock = iter([0, 61])
    monkeypatch.setattr(updates.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="update_network"):
        updates.fetch(API, 16, cancel)


@pytest.mark.parametrize(
    "status,code",
    [
        (404, None),
        (403, "update_rate_limit"),
        (429, "update_rate_limit"),
        (500, "update_network"),
    ],
)
def test_github_missing_release_rate_limit_and_failure(monkeypatch, status, code):
    from deluge_stream import updates

    class Opener:
        def open(self, *_args, **_kwargs):
            raise updates.urllib.error.HTTPError(API, status, "fixture", {}, None)

    monkeypatch.setattr(updates.urllib.request, "build_opener", lambda *_: Opener())
    if code:
        with pytest.raises(ValueError, match=code):
            updates.fetch(API, 16, threading.Event())
    else:
        assert updates.fetch(API, 16, threading.Event()) is None
