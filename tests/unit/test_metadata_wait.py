from types import SimpleNamespace

import pytest
from twisted.internet import defer
from twisted.internet.task import Clock
from twisted.web.test.requesthelper import DummyRequest

from deluge_stream.torrents import Torrents
from deluge_stream.torrserver import API


@pytest.fixture
def waiting():
    clock = Clock()
    row = {"torrent_id": "a" * 40, "record_id": "original"}
    state = {"row": row, "native": object(), "info": None, "owner": "localclient"}
    torrents = object.__new__(Torrents)
    torrents.reactor = clock
    torrents.closed = False
    torrents.metadata_waits = set()
    torrents.active = set()
    torrents.sources = SimpleNamespace(close=lambda: None)
    torrents.policy = SimpleNamespace(
        closed=False,
        config={
            "metadata_timeout_seconds": 3,
            "public_base_url": "http://example.test",
        },
        adapter=SimpleNamespace(
            native_status=lambda _: (state["native"], state["info"], None)
        ),
    )
    torrents.actor = lambda: (state["owner"], False)

    def find(_):
        if state["row"] is None:
            raise ValueError("torrent_not_found")
        return state["row"]

    torrents.find = find
    return torrents, clock, row, state


def test_wait_returns_when_native_metadata_arrives(waiting):
    torrents, clock, row, state = waiting
    results = []
    work = torrents.wait_metadata(row)
    work.addCallback(results.append)
    assert torrents.metadata_waits and not work.called
    state["info"] = object()
    clock.advance(0.1)
    assert results == [True]
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


def test_full_wait_times_out_and_releases_timer(waiting):
    torrents, clock, row, _ = waiting
    failures = []
    torrents.wait_metadata(row).addErrback(failures.append)
    for _ in range(31):
        clock.advance(0.1)
    assert failures[0].check(defer.TimeoutError)
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


def test_file_list_short_poll_returns_pending_before_lampa_timeout(waiting):
    torrents, clock, row, _ = waiting
    results = []
    torrents.wait_metadata(row, poll=True).addCallback(results.append)
    for _ in range(11):
        clock.advance(0.1)
    assert results == [False] and clock.seconds() < 2
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


@pytest.mark.parametrize("change", ["remove", "readd", "native", "owner", "closed"])
def test_pending_wait_cannot_attach_to_replacement_or_revoked_owner(waiting, change):
    torrents, clock, row, state = waiting
    failures = []
    torrents.wait_metadata(row).addErrback(failures.append)
    if change == "remove":
        state["row"] = None
    elif change == "readd":
        state["row"] = {**row, "record_id": "replacement"}
    elif change == "native":
        state["native"] = object()
    elif change == "owner":
        state["owner"] = "another"
    else:
        torrents.policy.closed = True
    state["info"] = object()
    clock.advance(0.1)
    assert failures and failures[0].check(ValueError, RuntimeError)
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


def test_plugin_close_cancels_wait_without_leaving_timer(waiting):
    torrents, clock, row, _ = waiting
    failures = []
    torrents.wait_metadata(row).addErrback(failures.append)
    assert torrents.close().called
    assert failures[0].check(defer.CancelledError)
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


def test_metadata_wait_admission_is_bounded(waiting):
    torrents, clock, row, _ = waiting
    failures = []
    for _ in range(33):
        torrents.wait_metadata(row).addErrback(failures.append)
    assert len(torrents.metadata_waits) == 32
    assert str(failures[0].value) == "source_limit"
    torrents.close()
    assert not torrents.metadata_waits and not clock.getDelayedCalls()


def test_http_disconnect_cancels_metadata_wait(waiting):
    torrents, clock, _, _ = waiting
    server = SimpleNamespace(
        policy=torrents.policy, torrents=torrents, closed=False, active_requests=set()
    )
    request = DummyRequest([])
    request.method = b"GET"
    request.path = b"/playlist"
    request.uri = b"/playlist?hash=" + b"a" * 40
    request.args = {b"hash": [b"a" * 40]}
    disconnected = defer.Deferred()
    request.notifyFinish = lambda: disconnected
    API(server).render(request)
    assert len(torrents.metadata_waits) == 1 and server.active_requests
    request._disconnected = True
    disconnected.errback(RuntimeError("connection lost"))
    assert not torrents.metadata_waits and not clock.getDelayedCalls()
    assert not server.active_requests and not request.written


def test_pending_dto_omits_truthy_empty_file_list(waiting):
    torrents, _, row, state = waiting
    row.update(created_at=1, is_temporary=True)
    torrents.policy.worker = SimpleNamespace(call=lambda *args: defer.succeed({}))
    torrents._ready = lambda: defer.succeed(None)
    status = SimpleNamespace(
        name="waiting",
        download_payload_rate=0,
        upload_payload_rate=0,
        num_peers=0,
        num_seeds=0,
    )
    torrents.policy.adapter.native_status = lambda _: (state["native"], None, status)
    results = []
    torrents.dto(row).addCallback(results.append)
    assert results[0]["metadata_pending"] and results[0]["stat"] == 1
    assert "file_stats" not in results[0]
