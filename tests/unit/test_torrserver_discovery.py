from types import SimpleNamespace

import pytest
from twisted.web.test.requesthelper import DummyRequest

from deluge_stream.torrserver import API


@pytest.mark.parametrize("path", [b"/", b"/echo"])
@pytest.mark.parametrize("method", [b"GET", b"HEAD", b"OPTIONS"])
def test_lampa_discovery_paths(path, method):
    server = SimpleNamespace(
        closed=False,
        active_requests=set(),
        policy=object(),
        torrents=SimpleNamespace(actor=lambda: ("localclient", False)),
    )
    request = DummyRequest([])
    request.path = path
    request.method = method
    result = API(server).render(request)
    assert request.responseCode in (None, 200)
    assert request.responseHeaders.getRawHeaders(b"Access-Control-Allow-Origin") == [
        b"*"
    ]
    if method == b"OPTIONS":
        assert result == b""
    else:
        assert request.finished
        assert b"".join(request.written) == (
            b"MatriX.147.1" if method == b"GET" else b""
        )
        assert request.responseHeaders.getRawHeaders(b"Content-Length") == [b"12"]
    assert not server.active_requests
