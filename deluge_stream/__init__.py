"""Deluge entry point; pure helpers do not require importing Deluge."""

__version__ = "0.0.1"
API_VERSION = 1


def CorePlugin(plugin_name):
    from deluge.plugins.init import PluginInitBase

    from .core import Core

    class Entry(PluginInitBase):
        _plugin_cls = Core

    return Entry(plugin_name)


def WebUIPlugin(plugin_name):
    from deluge.plugins.init import PluginInitBase

    from .webui import WebUI

    class Entry(PluginInitBase):
        _plugin_cls = WebUI

    return Entry(plugin_name)
