"""Discover and load PyCAT plugins. Headless: no Qt, so a batch run loads a plugin's steps too.

A plugin is any installed distribution that declares an entry point in the ``pycat.plugins`` group::

    [project.entry-points."pycat.plugins"]
    bacteria_host = "wadsworthlab_bacteria_host:register"

The entry-point NAME is the plugin's namespace; the target is ``register(api)``, and the module that holds it
declares ``PYCAT_PLUGIN_API = 1`` (the API major it was written for).

Loading is once per process and idempotent (:func:`load_plugins`). One plugin that fails to import, declares an
incompatible API, or raises in ``register`` is recorded with its reason and skipped; it never stops PyCAT or
another plugin. ``PYCAT_DISABLE_PLUGINS=1`` loads none (the test suite sets it, so an installed plugin cannot
change what PyCAT's own tests see).
"""
from __future__ import annotations

import os
import traceback

from pycat.plugins import registry as _reg
from pycat.plugins.api import PLUGIN_API_VERSION, PluginAPI, PluginError

ENTRY_POINT_GROUP = 'pycat.plugins'
_LOADED = False


def plugins_disabled() -> bool:
    return os.environ.get('PYCAT_DISABLE_PLUGINS', '').strip().lower() in ('1', 'true', 'yes')


def discover_entry_points() -> list:
    """The installed ``pycat.plugins`` entry points, sorted by name (a stable load order)."""
    from importlib.metadata import entry_points
    return sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name)


def _distribution_of(ep) -> tuple:
    dist = getattr(ep, 'dist', None)
    if dist is None:
        return '', ''
    return (dist.metadata.get('Name', '') or '', dist.version or '')


def load_plugin(ep) -> '_reg.PluginRecord':
    """Load one entry point and record the outcome. Never raises for a plugin's own failure."""
    dist, version = _distribution_of(ep)
    record = _reg.PluginRecord(name=ep.name, target=ep.value, distribution=dist, version=version)
    if ep.name in _reg._PLUGINS:
        record.status, record.error = 'failed', (f"another installed plugin already uses the name {ep.name!r} "
                                                 f"({_reg._PLUGINS[ep.name].distribution or 'unknown'})")
        _reg._PLUGINS[f"{ep.name} ({dist or ep.value})"] = record
        return record
    _reg._PLUGINS[ep.name] = record
    package = ep.value.partition(':')[0].split('.')[0]
    try:
        api = PluginAPI(ep.name, package)
    except PluginError as exc:
        record.status, record.error = 'failed', str(exc)
        return record
    if package == 'pycat':
        record.status, record.error = 'failed', "a plugin's entry point must name its own package, not pycat"
        return record
    token = _reg._CURRENT_PLUGIN.set((ep.name, package))
    try:
        register = ep.load()          # imports the plugin; its @tags_layer decorators run, attributed to it
        _check_api_version(register)
        register(api)
        api._finalize()
    except _Incompatible as exc:
        api._discard()
        record.status, record.error = 'incompatible', str(exc)
    except Exception as exc:          # broad-ok: optional_probe — a plugin's own failure is recorded and skipped, never PyCAT's
        api._discard()
        record.status = 'failed'
        record.error = f"{type(exc).__name__}: {exc}"
        record.contributions = [('traceback', traceback.format_exc())]
    else:
        api._commit(record)
        record.status = 'loaded'
    finally:
        _reg._CURRENT_PLUGIN.reset(token)
    return record


class _Incompatible(Exception):
    pass


def _check_api_version(register) -> None:
    import sys
    module = sys.modules.get(getattr(register, '__module__', ''), None)
    declared = getattr(module, 'PYCAT_PLUGIN_API', None)
    if declared is None:
        raise _Incompatible(f"its module does not declare PYCAT_PLUGIN_API (this PyCAT provides plugin API "
                            f"{PLUGIN_API_VERSION[0]}; add 'PYCAT_PLUGIN_API = {PLUGIN_API_VERSION[0]}' beside "
                            f"register)")
    if int(declared) != PLUGIN_API_VERSION[0]:
        raise _Incompatible(f"it was written for plugin API {declared}; this PyCAT provides API "
                            f"{PLUGIN_API_VERSION[0]}.{PLUGIN_API_VERSION[1]}")


def load_plugins(entry_points=None) -> list:
    """Load every plugin once per process; later calls return the same records. ``entry_points`` (tests)
    loads exactly those, ignoring ``PYCAT_DISABLE_PLUGINS`` and the once-only latch."""
    global _LOADED
    if entry_points is not None:
        return [load_plugin(ep) for ep in entry_points]
    if _LOADED:
        return _reg.plugins()
    _LOADED = True
    if plugins_disabled():
        return []
    try:
        eps = discover_entry_points()
    except Exception as exc:          # broad-ok: optional_probe — broken package metadata must not stop PyCAT starting
        from pycat.utils.general_utils import debug_log
        debug_log('plugins: entry-point discovery failed', exc)
        return []
    for ep in eps:
        record = load_plugin(ep)
        if record.status == 'loaded':
            print(f"[PyCAT] Plugin '{record.name}' loaded ({record.distribution} {record.version}).".rstrip())
        else:
            print(f"[PyCAT] Plugin '{record.name}' NOT loaded ({record.status}): {record.error}")
    return _reg.plugins()


def plugin_batch_steps() -> dict:
    """Every loaded plugin's batch steps (loading the plugins first if nothing has yet). What
    ``batch_step_registry.register_all_steps`` adds to PyCAT's own."""
    load_plugins()
    return _reg.plugin_batch_steps()


def _reset_for_tests() -> None:
    """**Tests only.** Unload everything: the registry, the plugins' operations, vocabulary and kernels, and the
    plugins' own modules (so the next load re-imports them and their ``@tags_layer`` decorators run again)."""
    global _LOADED
    import sys
    from pycat.utils import layer_tags, tag_registry
    packages = {r.target.partition(':')[0].split('.')[0] for r in _reg.plugins()} - {'pycat', ''}
    for mod in [m for m in sys.modules if m.split('.')[0] in packages]:
        sys.modules.pop(mod, None)
    for op in list(_reg._OPERATIONS):
        tag_registry._OPERATIONS.pop(op, None)
    for (kind, value) in list(_reg._VOCABULARY):
        if kind == 'target':
            layer_tags.CORE_VALUES['target'].discard(value)
        else:
            tag_registry.REQUIREMENTS.pop(value, None)
    tag_registry.TARGETS = tuple(sorted(layer_tags.CORE_VALUES['target']))
    tag_registry.REQUIREMENT_NAMES = tuple(sorted(tag_registry.REQUIREMENTS))
    if _reg._KERNELS:
        from pycat.kernel import operation_service as svc
        for op in _reg._KERNELS:
            svc._KERNELS.pop(op, None)
    _reg._reset_for_tests()
    _LOADED = False
