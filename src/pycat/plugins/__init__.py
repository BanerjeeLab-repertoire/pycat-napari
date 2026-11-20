"""PyCAT's plugin API: extend PyCAT from a separately installed package.

A plugin declares an entry point in the ``pycat.plugins`` group, pointing at ``register(api)``::

    # pyproject.toml of the plugin
    [project.entry-points."pycat.plugins"]
    my_lab = "my_lab_pycat:register"

    # my_lab_pycat/__init__.py
    PYCAT_PLUGIN_API = 1

    def register(api):
        api.add_target('bacterium')
        from my_lab_pycat import ops                   # @tags_layer('my_lab.count', ...) decorators run here
        step = api.add_batch_step('count', ops.replay_count, operations=('my_lab.count',))
        api.add_analysis_method('adhesion', 'Bacterial adhesion', 'my_lab_pycat.panel:AdhesionUI',
                                pipeline=((step, 'Count adherent bacteria'),))

Everything :class:`PluginAPI` offers is documented on its methods; the guide is
``docs/source/development/plugins.rst``.
"""
from pycat.plugins.api import PLUGIN_API_VERSION, PluginAPI, PluginError
from pycat.plugins.loader import ENTRY_POINT_GROUP, load_plugins
from pycat.plugins.registry import plugins

__all__ = ['ENTRY_POINT_GROUP', 'PLUGIN_API_VERSION', 'PluginAPI', 'PluginError', 'load_plugins', 'plugins']
