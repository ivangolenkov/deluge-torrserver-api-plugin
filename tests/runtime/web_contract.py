"""Isolated egg import; fault injection of remote replies, not a real RPC test."""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from twisted.internet import defer, task

parser = argparse.ArgumentParser()
parser.add_argument("--egg", required=True)
args = parser.parse_args()
sys.path.insert(0, args.egg)


@defer.inlineCallbacks
def main(reactor):
    from deluge_stream import API_VERSION, __version__
    from deluge_stream.webui import WebUI

    web = object.__new__(WebUI)
    remote = SimpleNamespace(connected=lambda: False)
    connection = object()
    with (
        patch("deluge_stream.webui.client", remote),
        patch("deluge_stream.webui.adapter.register"),
        patch("deluge_stream.webui.adapter.deregister"),
        patch("deluge_stream.webui.adapter.connection_token", lambda: connection),
    ):
        web.enable()
        assert web.get_web_status()["reason"] == "core_unavailable"
        remote.connected = lambda: True
        replies = []
        remote.stream = SimpleNamespace(get_diagnostics=lambda _: replies.pop(0))
        valid = {
            "api_version": API_VERSION,
            "plugin_version": __version__,
            "storage_active": False,
            "token": "must-not-proxy",
        }
        for value in (
            None,
            {**valid, "api_version": True},
            {**valid, "api_version": 999},
            {**valid, "plugin_version": "0.0.0"},
        ):
            replies.append(value)
            assert (yield web.get_web_status())["reason"] == "incompatible_core"
        replies.append(valid)
        result = yield web.get_web_status()
        assert result["available"] is True and "token" not in result
        replies.append(defer.fail(RuntimeError("secret remote details")))
        result = yield web.get_web_status()
        assert result["reason"] == "core_unavailable" and "secret" not in str(result)
        late = defer.Deferred()
        replies.append(late)
        waiting = web.get_web_status()
        web.disable()
        web.enable()
        late.callback(valid)
        assert (yield waiting)["reason"] == "web_disabled"
        late = defer.Deferred()
        replies.append(late)
        waiting = web.get_web_status()
        connection = object()
        late.callback(valid)
        assert (yield waiting)["reason"] == "core_unavailable"
        web.disable()
        assert web.get_web_status()["reason"] == "web_disabled"
    assert Path(WebUI.scripts[0]).is_file()
    print(
        "WEB CONTRACT: offline/error/version, stale enable/connection "
        "and reply whitelist passed"
    )


task.react(main)
