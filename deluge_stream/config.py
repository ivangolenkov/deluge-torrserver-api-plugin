"""Pure, atomic configuration validation. HTTP fields are staged for phase 3."""

import ipaddress
from pathlib import PurePosixPath
from urllib.parse import urlsplit

DEFAULTS = {
    "http_enabled": True,
    "bind_host": "0.0.0.0",
    "bind_port": 8090,
    "public_base_url": "",
    "torrent_owner": "",
    "download_root": "",
    "max_temporary_torrents": 3,
    "stream_window_mib": 20,
    "initial_prebuffer_mib": 20,
    "tail_prebuffer_pieces": 4,
    "debounce_seconds": 10,
    "max_playback_requests": 8,
    "max_preload_jobs": 2,
    "max_file_downloads": 4,
    "max_zip_downloads": 1,
    "payload_buffer_budget_mib": 64,
    "metadata_timeout_seconds": 60,
    "piece_wait_timeout_seconds": 60,
    "client_idle_timeout_seconds": 60,
    "preload_timeout_seconds": 60,
    "download_ticket_start_ttl_seconds": 60,
    "max_pending_download_tickets": 32,
    "cleanup_retry_seconds": 5,
    "max_torrent_upload_mib": 16,
    "max_json_body_mib": 1,
    "max_zip_entries": 100000,
}


def validate(changes, current=None):
    if not isinstance(changes, dict) or changes.keys() - DEFAULTS.keys():
        raise ValueError("invalid_config")
    result = dict(DEFAULTS if current is None else current)
    result.update(changes)
    for key, default in DEFAULTS.items():
        value = result[key]
        if type(default) is bool:
            if type(value) is not bool:
                raise ValueError("invalid_config")
        elif type(default) is int:
            upper = 65535 if key == "bind_port" else 1_000_000
            lower = 5 if key == "cleanup_retry_seconds" else 1
            if key == "tail_prebuffer_pieces":
                lower = 0
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("invalid_config")
        elif (
            not isinstance(value, str)
            or len(value.encode()) > 4096
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("invalid_config")
    try:
        ipaddress.ip_address(result["bind_host"])
    except ValueError as exc:
        raise ValueError("invalid_config") from exc
    if (
        result["download_root"]
        and not PurePosixPath(result["download_root"]).is_absolute()
    ):
        raise ValueError("invalid_config")
    if len(result["torrent_owner"].encode()) > 256:
        raise ValueError("invalid_config")
    if ".." in PurePosixPath(result["download_root"]).parts:
        raise ValueError("invalid_config")
    if result["public_base_url"]:
        parsed = urlsplit(result["public_base_url"])
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid_config")
        try:
            if parsed.port == 0:
                raise ValueError("invalid_config")
        except ValueError as exc:
            raise ValueError("invalid_config") from exc
    return result
