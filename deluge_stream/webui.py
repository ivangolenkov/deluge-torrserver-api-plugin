"""Initial authenticated Web UI integration, without data proxy or policy RPC."""

import hashlib
import json

import deluge.component as component
from deluge.configmanager import get_config_dir
from deluge.plugins.pluginbase import WebPluginBase
from deluge.ui.client import client
from deluge.ui.web.json_api import export
from twisted.internet import defer, reactor

from . import API_VERSION, __version__
from . import web_adapter as adapter
from .update_service import UpdateService
from .updates import ERRORS


class WebUI(WebPluginBase):
    scripts = []

    def __init__(self, plugin_name):
        adapter.initialize(self)

    def enable(self):
        self.scripts = [adapter.script_path()]
        self.enabled = True
        self.generation = object()
        self.updates = UpdateService(reactor, get_config_dir(), "web")
        self.update_state = {"state": "idle"}
        self.update_plan = None
        adapter.register(self)

    def disable(self):
        self.enabled = False
        if getattr(self, "updates", None) is not None:
            self.updates.close().addErrback(lambda _: None)
        try:
            adapter.deregister(self)
        finally:
            self.scripts = []
            adapter.release_script()

    def __del__(self):
        # Override the buggy base destructor; no global Deluge monkey patch.
        adapter.deregister(self)

    def _update_guard(self):
        generation, connection = self.generation, adapter.connection_token()

        def guard():
            if (
                not self.enabled
                or self.generation is not generation
                or not client.connected()
                or adapter.connection_token() is not connection
            ):
                raise ValueError("core_unavailable")

        guard()
        return guard

    def _update_failed(self, failure, generation):
        if self.generation is not generation or not self.enabled:
            return None
        code = getattr(failure.value, "message", str(failure.value))
        if code not in ERRORS:
            code = "update_invalid"
        self.update_state = {"state": "error", "error": code}
        return None

    @export
    @defer.inlineCallbacks
    def check_plugin_update(self):
        if self.update_state["state"] in (
            "checking",
            "preparing",
            "installing",
            "restarting",
        ):
            raise ValueError("update_busy")
        guard = self._update_guard()
        # Require admin on the current core before any web-side I/O.
        yield client.stream.get_config(API_VERSION)
        guard()
        if self.update_state["state"] in (
            "checking",
            "preparing",
            "installing",
            "restarting",
        ):
            raise ValueError("update_busy")
        self.update_state = {"state": "checking"}
        self.update_plan = None
        self._check_updates(guard).addErrback(self._update_failed, self.generation)
        return True

    @defer.inlineCallbacks
    def _check_updates(self, guard):
        core = yield client.stream.update_check(API_VERSION)
        guard()
        web = yield self.updates.run("check")
        guard()
        if core.get("version") != web.get("version"):
            raise ValueError("update_conflict")
        available = core["available"] or web["available"]
        if available:
            if (
                not core.get("token")
                or not web.get("token")
                or core["release"] != web["release"]
            ):
                raise ValueError("update_conflict")
            self.update_plan = (core, web, guard)
        self.update_state = {
            "state": "checked",
            "server": self._update_server(),
            "host_id": next(
                (
                    host[0]
                    for host in component.get("Web").hostlist.get_hosts_info()
                    if tuple(host[1:4]) == client.connection_info()
                ),
                None,
            ),
            "core_version": core["current"],
            "web_version": web["current"],
            "version": web["version"],
            "available": available,
            "url": web.get("url"),
        }

    @export
    @defer.inlineCallbacks
    def install_plugin_update(self, version):
        if (
            self.update_state["state"] != "checked"
            or not self.update_plan
            or version != self.update_state["version"]
        ):
            raise ValueError("update_conflict")
        core, web, guard = self.update_plan
        guard()
        yield client.stream.get_config(API_VERSION)
        guard()
        if self.update_state["state"] != "checked":
            raise ValueError("update_busy")
        self.update_state = {"state": "preparing", "version": version}
        self._install_update(core, web, guard).addErrback(
            self._update_failed, self.generation
        )
        return True

    @defer.inlineCallbacks
    def _install_update(self, core, web, guard):
        yield self.updates.run("prepare", web["token"])
        guard()
        yield client.stream.update_prepare(API_VERSION, core["token"])
        guard()
        self.update_state = {"state": "installing", "version": web["version"]}
        yield client.stream.update_commit(API_VERSION, core["token"])
        guard()
        yield self.updates.run("commit", web["token"])
        guard()
        self.update_state = {"state": "restarting", "version": web["version"]}
        yield client.stream.update_restart(API_VERSION, core["token"])
        guard()
        self.updates.restart(web["token"])

    @export
    def get_plugin_update_status(self):
        return dict(self.update_state)

    @staticmethod
    def _update_server():
        return hashlib.sha256(json.dumps(client.connection_info()).encode()).hexdigest()

    @export
    def get_web_status(self):
        """Web-session protected. Core calls remain on authenticated daemon RPC."""
        if not getattr(self, "enabled", False):
            return self._status(False, "web_disabled")
        if not client.connected():
            return self._status(False, "core_unavailable")
        generation, connection = self.generation, adapter.connection_token()
        d = defer.maybeDeferred(client.stream.get_diagnostics, API_VERSION)
        d.addTimeout(5, reactor)

        def received(info):
            if not self.enabled or self.generation is not generation:
                return self._status(False, "web_disabled")
            if not client.connected() or adapter.connection_token() is not connection:
                return self._status(False, "core_unavailable")
            if (
                not isinstance(info, dict)
                or type(info.get("api_version")) is not int
                or info["api_version"] != API_VERSION
                or info.get("plugin_version") != __version__
            ):
                return self._status(False, "incompatible_core")
            # Explicit whitelist, not an arbitrary core-response proxy.
            return self._status(
                True,
                "ready",
                update_server=self._update_server(),
                storage_active=info.get("storage_active") is True,
                torrserver_active=info.get("torrserver_active") is True,
                recovery_required=info.get("recovery_required") is True,
                http_error="bind_failed"
                if info.get("http_error") == "bind_failed"
                else None,
                streaming=self._streaming(info.get("streaming")),
            )

        d.addCallbacks(received, lambda _: self._status(False, "core_unavailable"))
        return d

    @staticmethod
    def _streaming(value):
        if not isinstance(value, dict):
            return {}
        result = {
            key: value[key]
            for key in (
                "sessions",
                "piece_jobs",
                "payload_bytes_reserved",
                "deadlines",
                "preloads",
            )
            if type(value.get(key)) is int and 0 <= value[key] <= 2**40
        }
        error = value.get("last_error", "")
        known = {
            "",
            "operation_conflict",
            "torrent_not_found",
            "metadata_pending",
            "file_skipped",
            "session_paused",
            "torrent_paused",
            "torrent_queued",
            "disk_full",
            "storage_error",
            "deadline_limit",
            "transfer_limit",
            "buffer_limit",
            "piece_read_error",
            "file_not_ready",
            "torrent_not_ready",
            "timeout",
            "stream_error",
            "cancel_error",
        }
        result["last_error"] = (
            error if isinstance(error, str) and error in known else "stream_error"
        )
        return result

    @staticmethod
    def _status(available, reason, **capabilities):
        return {
            "available": available,
            "reason": reason,
            "api_version": API_VERSION,
            "plugin_version": __version__,
            **capabilities,
        }
