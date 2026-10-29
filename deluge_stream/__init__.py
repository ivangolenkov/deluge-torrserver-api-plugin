"""Deluge entry point; pure helpers do not require importing Deluge."""


def CorePlugin(plugin_name):
    from deluge.plugins.init import PluginInitBase

    from .core import Core

    class Entry(PluginInitBase):
        _plugin_cls = Core

    return Entry(plugin_name)
