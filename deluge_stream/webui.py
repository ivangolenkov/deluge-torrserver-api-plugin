"""Initial authenticated Web UI integration, without data proxy or policy RPC."""

from deluge.plugins.pluginbase import WebPluginBase
from deluge.ui.client import client
from deluge.ui.web.json_api import export
from twisted.internet import defer, reactor

from . import API_VERSION, __version__
from . import web_adapter as adapter


class WebUI(WebPluginBase):
    scripts = [adapter.script_path()]

    def enable(self):
        self.enabled = True
        self.generation = object()
        adapter.register(self)

    def disable(self):
        self.enabled = False
        adapter.deregister(self)

    def __del__(self):
        # Override the buggy base destructor; no global Deluge monkey patch.
        adapter.deregister(self)

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
