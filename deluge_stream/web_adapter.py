"""Deluge Web 2.2.0 integration, separate from daemon-only imports."""

import deluge.component as component
from deluge.ui.client import client

from .resources import release_script as release_script
from .resources import script_path as script_path


def initialize(owner):
    # Modern WebPluginBase normalizes to "WebPlugin.stream", but the Web plugin
    # manager still looks up the metadata display name "WebPlugin.Stream".
    # Register only our component explicitly; do not patch Deluge's registries.
    component.Component.__init__(owner, "WebPlugin.Stream", owner.update_interval)


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
