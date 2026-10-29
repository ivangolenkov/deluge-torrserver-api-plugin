"""Deluge Web 2.2.0 integration, separate from daemon-only imports."""

import deluge.component as component
from deluge.ui.client import client
from pkg_resources import resource_filename


def script_path():
    return resource_filename("deluge_stream", "data/stream.js")


def connection_token():
    return client._daemon_proxy


def register(owner):
    component.get("JSON").register_object(owner, "stream")


def deregister(owner):
    # JSON.deregister_object in Deluge 2.2.0 deletes from its live dict iterator,
    # just like the core RPC registry. Detach only our own bound methods.
    try:
        methods = component.get("JSON")._local_methods
    except KeyError:
        return
    for name, method in list(methods.items()):
        if getattr(method, "__self__", None) is owner:
            del methods[name]
