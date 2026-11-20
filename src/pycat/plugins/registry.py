"""What the loaded plugins registered. **Stdlib only** — imported by the batch registry, the consolidated
table, the op catalog and the tag registry, all of which must stay importable in the minimal (``-m core``) lane.

PyCAT reads the plugin contributions from here at the point of use (``plugin_batch_steps()``,
``object_tables()``, ``plugin_adapters()``…), so a subsystem never holds a stale copy taken before the plugins
loaded. Writes go through :class:`pycat.plugins.api.PluginAPI`, which validates them; nothing here validates.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Callable, Optional

#: ``(plugin name, top-level package)`` of the plugin whose import / ``register(api)`` is running, so a
#: ``@tags_layer`` decorator in that package is attributed to the plugin (``tag_registry.register_operation``).
_CURRENT_PLUGIN: contextvars.ContextVar = contextvars.ContextVar('pycat_current_plugin', default=None)


def current_plugin() -> Optional[str]:
    """The name of the plugin being loaded right now, or ``None`` (PyCAT's own code)."""
    current = _CURRENT_PLUGIN.get()
    return current[0] if current else None


@dataclass
class PluginRecord:
    """One discovered plugin and what happened when PyCAT loaded it."""
    name: str                       # the entry-point name (the plugin's namespace)
    target: str                     # 'module:function' the entry point names
    distribution: str = ''          # the pip distribution that provides it
    version: str = ''
    status: str = 'pending'         # 'loaded' | 'failed' | 'incompatible' | 'pending'
    error: str = ''                 # why it failed, in words a user can act on
    contributions: list = field(default_factory=list)   # ('kind', 'key') pairs, for the Installed Plugins view


@dataclass(frozen=True)
class AnalysisMethod:
    key: str                        # '<plugin>.<method>'
    label: str                      # menu label
    ui_class: object                # a class, or 'module:Class' resolved when the menu item is clicked
    pipeline: tuple = ()            # ((step_key, label), ...) for the workflow checklist; () = none
    plugin: str = ''


@dataclass(frozen=True)
class MenuAction:
    key: str
    label: str
    callback: object                # a callable, or 'module:function'
    kind: str = 'action'            # 'action' | 'toolbox' (an adder taking (ui_instance, layout, separate_widget))
    plugin: str = ''


@dataclass(frozen=True)
class FeatureCardSpec:
    key: str
    title: str
    summary: str
    category: str
    entry: object = None
    docs_anchor: Optional[str] = None
    plugin: str = ''


@dataclass(frozen=True)
class AdapterSpec:
    plan_step: str                  # the op id the Navigator plans
    batch_step: str                 # the plugin batch step that computes it
    params_from: Optional[Callable] = None   # (intent, ctx, state, reviewed) -> params; None = {}
    plugin: str = ''


_PLUGINS: dict = {}                 # name -> PluginRecord, in load order
_BATCH_STEPS: dict = {}             # step name -> fn(state, image_path, params, output_dir)
_STEP_OPERATIONS: dict = {}         # step name -> (op id, ...)
_STEP_TABLES: dict = {}             # step name -> (entity_type, data-repository key)
_OBJECT_TABLES: list = []           # extra per-object '<type>_df' keys for consolidated_long.csv
_ANALYSIS_METHODS: dict = {}        # key -> AnalysisMethod
_MENU_ACTIONS: dict = {}            # key -> MenuAction
_FEATURE_CARDS: dict = {}           # key -> FeatureCardSpec
_ADAPTERS: dict = {}                # plan step -> AdapterSpec
_OPERATIONS: dict = {}              # op id -> plugin name (ops registered while a plugin loaded)
_VOCABULARY: dict = {}              # (kind, value) -> plugin name that first added it
_KERNELS: dict = {}                 # op id -> plugin name


def plugins() -> list:
    """Every discovered plugin's :class:`PluginRecord`, in load order (loaded, failed and incompatible)."""
    return list(_PLUGINS.values())


def plugin_batch_steps() -> dict:
    return dict(_BATCH_STEPS)


def plugin_step_operations() -> dict:
    return dict(_STEP_OPERATIONS)


def plugin_step_tables() -> dict:
    return dict(_STEP_TABLES)


def plugin_object_tables() -> tuple:
    return tuple(_OBJECT_TABLES)


def analysis_methods() -> list:
    return list(_ANALYSIS_METHODS.values())


def menu_actions() -> list:
    return list(_MENU_ACTIONS.values())


def feature_cards() -> list:
    return list(_FEATURE_CARDS.values())


def plugin_adapters() -> dict:
    return dict(_ADAPTERS)


def plugin_operations() -> dict:
    """op id -> the plugin that registered it."""
    return dict(_OPERATIONS)


def plugin_of_operation(op: str) -> Optional[str]:
    return _OPERATIONS.get(op)


def claim_operation(op: str, module: Optional[str]) -> None:
    """Called by ``tag_registry.register_operation`` before it stores ``op``: attribute the op to the plugin
    loading now when ``module`` (the declaring code's module) is in that plugin's package. PyCAT modules (or
    any other library) that a plugin's import happens to import first stay PyCAT's.

    A plugin's op ids live in its namespace (``'<plugin>.<op>'``) so two plugins, or a plugin and a later PyCAT
    release, cannot claim the same name; anything else is refused here."""
    current = _CURRENT_PLUGIN.get()
    if current is None or not module or module.split('.')[0] != current[1]:
        return
    name = current[0]
    if not op.startswith(name + '.') or op == name + '.':
        raise ValueError(f"plugin '{name}' registered the operation '{op}'; a plugin's operation ids must be "
                         f"'{name}.<operation>' (e.g. '{name}.{op.split('.')[-1] or 'measure'}').")
    _OPERATIONS[op] = name


def _reset_for_tests() -> None:
    """**Tests only.** Forget every plugin and contribution (the vocabulary additions are undone by the API)."""
    for table in (_PLUGINS, _BATCH_STEPS, _STEP_OPERATIONS, _STEP_TABLES, _ANALYSIS_METHODS, _MENU_ACTIONS,
                  _FEATURE_CARDS, _ADAPTERS, _OPERATIONS, _VOCABULARY, _KERNELS):
        table.clear()
    _OBJECT_TABLES.clear()
