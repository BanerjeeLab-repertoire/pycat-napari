"""The object a plugin's ``register(api)`` receives. **Every way a plugin extends PyCAT is a method here.**

Each method validates what it is given and STAGES it; the loader commits the plugin's contributions only once
``register`` has returned and the cross-checks in :meth:`PluginAPI._finalize` pass (a batch step's declared
operations exist, an adapter's batch step is this plugin's…). A plugin that fails part-way leaves nothing behind
but its entry in the Installed Plugins list, with the reason.

Names. Everything a plugin registers lives in its namespace, ``'<plugin>.<name>'``, where ``<plugin>`` is its
entry-point name: operation ids, batch steps, analysis methods, menu actions, feature cards. Methods take the
local ``name`` and return the full key, which is what a panel records (``self._record(key, params)``) and what a
saved batch config replays.

Headless. Nothing here imports Qt. A UI class or menu callback may be given as ``'module:attribute'`` so the
plugin's ``register`` module never imports Qt either and its batch steps load in a headless batch run.
"""
from __future__ import annotations

import inspect
import re

from pycat.plugins import registry as _reg

#: The plugin API version. A plugin declares the MAJOR it was written for as ``PYCAT_PLUGIN_API = 1`` in the
#: module that holds ``register``; PyCAT loads it only when the majors match. Additions bump the minor.
PLUGIN_API_VERSION = (1, 0)

_NAME = re.compile(r'^[a-z][a-z0-9_]*$')


class PluginError(Exception):
    """A plugin asked for something the API cannot do. The message says what and how to fix it."""


def _check_name(kind: str, name: str) -> str:
    name = str(name)
    if not _NAME.match(name):
        raise PluginError(f"{kind} name {name!r} must be lower-case letters, digits and '_', starting with a letter.")
    return name


def _check_ref(kind: str, obj) -> None:
    if isinstance(obj, str):
        if ':' not in obj:
            raise PluginError(f"{kind} {obj!r} must be a 'module:attribute' reference or the object itself.")
    elif not callable(obj):
        raise PluginError(f"{kind} {obj!r} is not callable.")


def resolve_ref(obj):
    """A ``'module:attribute'`` reference imported now; anything else returned unchanged."""
    if isinstance(obj, str):
        import importlib
        module, _, attr = obj.partition(':')
        target = importlib.import_module(module)
        for part in attr.split('.'):
            target = getattr(target, part)
        return target
    return obj


class PluginAPI:
    """Bound to one plugin. See the module docstring; each method documents its own contract."""

    def __init__(self, name: str, package: str = ''):
        self.name = _check_name('plugin', name)
        self.package = package or self.name   # the plugin's top-level package: ops declared in it are its own
        self._steps: dict = {}
        self._step_ops: dict = {}
        self._step_tables: dict = {}
        self._object_tables: list = []
        self._methods: dict = {}
        self._actions: dict = {}
        self._cards: dict = {}
        self._adapters: dict = {}
        self._kernels: list = []
        self._vocabulary: list = []      # (kind, value) this plugin added — undone if the plugin fails

    @property
    def version(self) -> tuple:
        """The API version of the running PyCAT, ``(major, minor)``. Gate a newer call on the minor."""
        return PLUGIN_API_VERSION

    def key(self, name: str) -> str:
        """The full key for a local ``name``: ``'<plugin>.<name>'``."""
        return f"{self.name}.{_check_name('local', name)}"

    # ── Vocabulary ──────────────────────────────────────────────────────────────────────────────────

    def add_target(self, name: str) -> str:
        """Add an object type to the layer-tag ``target`` vocabulary (``'bacterium'``, ``'host_cell'``), so the
        plugin's operations and layers can say what they are OF. Shared, not namespaced: a target means the
        same thing whoever measures it, so adding one that exists (PyCAT's or another plugin's) is a no-op.
        Call this BEFORE importing the modules whose ``@tags_layer`` decorators use the target."""
        from pycat.utils import layer_tags, tag_registry
        name = _check_name('target', name)
        if name not in layer_tags.CORE_VALUES['target']:
            layer_tags.CORE_VALUES['target'].add(name)
            tag_registry.TARGETS = tuple(sorted(layer_tags.CORE_VALUES['target']))
            _reg._VOCABULARY[('target', name)] = self.name
            self._vocabulary.append(('target', name))
        return name

    def add_requirement(self, name: str, reason: str) -> str:
        """Add a runnability requirement (``tag_registry.REQUIREMENTS``): ``reason`` completes the sentence
        "needs …" that a greyed-out operation shows. A no-op if the requirement already exists."""
        from pycat.utils import tag_registry
        name = _check_name('requirement', name)
        if not str(reason).strip():
            raise PluginError(f"requirement {name!r} needs a reason (it is what the user is shown).")
        if name not in tag_registry.REQUIREMENTS:
            tag_registry.REQUIREMENTS[name] = str(reason).strip()
            tag_registry.REQUIREMENT_NAMES = tuple(sorted(tag_registry.REQUIREMENTS))
            _reg._VOCABULARY[('requirement', name)] = self.name
            self._vocabulary.append(('requirement', name))
        return name

    # ── Operations (the catalog) ────────────────────────────────────────────────────────────────────

    def add_operation(self, name: str, function, *, role: str, summary: str, target=None, produces=None,
                      aliases: tuple = (), inputs: tuple = (), requirements: tuple = ()) -> str:
        """Register an operation for a function that is not decorated with ``@tags_layer`` (a measurement,
        typically: ``role='measurement'``). It joins the operation catalog the Navigator plans over, exactly
        like a decorated one. ``@tags_layer('<plugin>.<op>', ...)`` on the function is the usual route and
        needs no call here — any decorator that runs while the plugin loads is attributed to it."""
        from pycat.utils import tag_registry
        _check_ref('operation function', function)
        op = self.key(name)
        tag_registry.register_operation(op, role=role, summary=summary, target=target, produces=produces,
                                        aliases=aliases, inputs=inputs, requirements=requirements,
                                        module=self.package)
        fn = function
        registered_by = (fn.replace(':', '.') if isinstance(fn, str)
                         else f"{fn.__module__}.{fn.__qualname__}")
        tag_registry._OPERATIONS[op]['registered_by'] = registered_by
        return op

    def add_kernel(self, op: str, wraps: str, *, style: str = 'passthrough', fn=None, roles: tuple = ('image',),
                   optional=None, entity: str = 'image', first: bool = False, part_of=None) -> str:
        """Give operation ``op`` (a full id) an execution kernel, so ``OperationService.execute(op, inputs,
        params)`` runs it — the one call a panel, a batch step and the Navigator all make, so the three cannot
        compute differently. ``wraps`` is the ``'module:function'`` that does the science. ``style``:

        - ``'passthrough'``: ``wrapped(*inputs[roles], **optional_inputs, **params)`` (see ``kernel.ops``)
        - ``'named'``: ``wrapped(**inputs, **params)``
        - ``'custom'``: ``fn(inputs, params) -> AnalysisResult``, which you write
        """
        if style not in ('passthrough', 'named', 'custom'):
            raise PluginError(f"kernel style {style!r} must be 'passthrough', 'named' or 'custom'.")
        if style == 'custom' and not callable(fn):
            raise PluginError("a 'custom' kernel needs fn(inputs, params) -> AnalysisResult.")
        if ':' not in str(wraps):
            raise PluginError(f"wraps {wraps!r} must be 'module:function'.")
        self._kernels.append(dict(op=op, wraps=wraps, style=style, fn=fn, roles=tuple(roles),
                                  optional=optional, entity=entity, first=first, part_of=part_of))
        return op

    # ── Batch ───────────────────────────────────────────────────────────────────────────────────────

    def add_batch_step(self, name: str, fn, *, operations: tuple = (), result_table=None) -> str:
        """Register a batch replay step: ``fn(state, image_path, params, output_dir) -> None``, the same contract
        as PyCAT's own (``batch/steps``). A panel records ``(key, params)``; batch calls ``fn`` with those
        ``params`` for every file. ``state`` is per-file and shared by the steps (``state['data_instance']`` is
        the file's data repository). Raise ``pycat.utils.errors.StepNotImplemented`` to be reported as skipped.

        ``operations``: the operation ids the step invokes (its composition, for the Navigator).
        ``result_table``: ``(entity_type, repository_key)`` of the per-object table the step writes, so the run
        report carries it as a typed result. To also reach ``consolidated_long.csv``, write the table to
        ``output_dir / f'{image_path.stem}_{repository_key}.csv'`` and :meth:`add_object_table` the key."""
        key = self.key(name)
        if callable(fn):
            params = [p for p in inspect.signature(fn).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            if len(params) != 4:
                raise PluginError(f"batch step {key!r}: fn must take (state, image_path, params, output_dir).")
        else:
            raise PluginError(f"batch step {key!r}: fn is not callable.")
        self._steps[key] = fn
        if operations:
            self._step_ops[key] = tuple(operations)
        if result_table is not None:
            entity, table = result_table
            self._step_tables[key] = (str(entity), str(table))
        return key

    def add_object_table(self, repository_key: str) -> str:
        """Include a per-object ``'<type>_df'`` table in ``consolidated_long.csv`` (one row per object and
        measurement, across the whole batch). Shared, not namespaced: it is the key in the data repository."""
        key = str(repository_key)
        if not key.endswith('_df'):
            raise PluginError(f"object table {key!r} must be a '<type>_df' data-repository key.")
        if key not in self._object_tables:
            self._object_tables.append(key)
        return key

    def add_navigator_adapter(self, op: str, batch_step: str, params_from=None) -> str:
        """Let the Navigator RUN operation ``op`` when a plan includes it, through this plugin's ``batch_step``
        (the proven same-computation route). ``params_from(intent, ctx, state, reviewed) -> params``; omitted,
        the step gets ``{}``. Without an adapter a planned plugin op is reported "run from its panel"."""
        if params_from is not None and not callable(params_from):
            raise PluginError(f"navigator adapter for {op!r}: params_from is not callable.")
        self._adapters[op] = _reg.AdapterSpec(op, batch_step, params_from, self.name)
        return op

    # ── GUI ─────────────────────────────────────────────────────────────────────────────────────────

    def add_analysis_method(self, name: str, label: str, ui_class, *, pipeline: tuple = ()) -> str:
        """A full analysis method, opened from the Plugins menu the way PyCAT's own are: the dock is cleared,
        the data repository carried over, then ``ui_class(viewer, central_manager).setup_ui()``.

        ``pipeline``: ``((step_key, label), ...)`` for the workflow checklist; each step ticks when the panel
        records that key. The checklist is registered under the method's key, so ``setup_ui`` activates it with
        ``central_manager.workflow_checklist.activate(key)``. A saved session whose active method is
        ``ui_class``'s name reopens this method."""
        key = self.key(name)
        _check_ref('ui_class', ui_class)
        steps = tuple((str(s), str(lbl)) for s, lbl in pipeline)
        self._methods[key] = _reg.AnalysisMethod(key, str(label), ui_class, steps, self.name)
        return key

    def add_toolbox_tool(self, name: str, label: str, adder) -> str:
        """A standalone tool under the plugin's menu, with the toolbox calling convention:
        ``adder(ui_instance, layout=None, separate_widget=False)``. It is called with the live
        ``ToolboxFunctionsUI`` and ``separate_widget=True``; dock it with
        ``ui_instance._add_widget_to_layout_or_dock(widget, layout, separate_widget, dock_name)``."""
        key = self.key(name)
        _check_ref('toolbox adder', adder)
        self._actions[key] = _reg.MenuAction(key, str(label), adder, 'toolbox', self.name)
        return key

    def add_menu_action(self, name: str, label: str, callback) -> str:
        """Any other action under the plugin's menu: ``callback(central_manager)``. It is searchable in the
        command palette (Ctrl+Shift+P) like every PyCAT menu item."""
        key = self.key(name)
        _check_ref('menu callback', callback)
        self._actions[key] = _reg.MenuAction(key, str(label), callback, 'action', self.name)
        return key

    def add_feature_card(self, name: str, title: str, summary: str, *, category: str = 'Plugins', entry=None,
                         docs_anchor=None) -> str:
        """A card on the Home dock. ``entry(central_manager)`` opens the feature (or ``'module:function'``)."""
        key = self.key(name)
        if entry is not None:
            _check_ref('feature card entry', entry)
        self._cards[key] = _reg.FeatureCardSpec(key, str(title), str(summary), str(category), entry,
                                                docs_anchor, self.name)
        return key

    # ── Loader side ─────────────────────────────────────────────────────────────────────────────────

    def _finalize(self) -> None:
        """Cross-check what was staged now that ``register`` has finished. Raises :class:`PluginError`."""
        from pycat.utils.tag_registry import get_operation
        problems = []
        for step, ops in self._step_ops.items():
            problems += [f"batch step {step!r} declares unknown operation {op!r}"
                         for op in ops if get_operation(op) is None]
        for op, spec in self._adapters.items():
            if get_operation(op) is None:
                problems.append(f"navigator adapter for unknown operation {op!r}")
            if spec.batch_step not in self._steps:
                problems.append(f"navigator adapter {op!r} names {spec.batch_step!r}, not one of this plugin's "
                                f"batch steps")
        for k in self._kernels:
            if get_operation(k['op']) is None:
                problems.append(f"kernel for unknown operation {k['op']!r} (register the operation first)")
            elif _reg.plugin_of_operation(k['op']) != self.name:
                problems.append(f"kernel for {k['op']!r}: a plugin may give kernels only to its own operations")
        for method in self._methods.values():
            problems += [f"analysis method {method.key!r}: checklist step {s!r} is not a batch step of this "
                         f"plugin" for s, _ in method.pipeline if s.startswith(self.name + '.')
                         and s not in self._steps]
        if problems:
            raise PluginError('; '.join(problems))

    def _commit(self, record: '_reg.PluginRecord') -> None:
        """Publish the staged contributions to :mod:`pycat.plugins.registry` (and the kernels to
        ``OperationService``). Called only after :meth:`_finalize` passed."""
        for k in self._kernels:
            from pycat.kernel import operation_service as svc
            if k['style'] == 'passthrough':
                svc.passthrough(k['op'], k['wraps'], roles=k['roles'], optional=k['optional'],
                                entity=k['entity'], first=k['first'], part_of=k['part_of'])
            elif k['style'] == 'named':
                svc.named(k['op'], k['wraps'], entity=k['entity'], part_of=k['part_of'])
            else:
                svc.register_kernel(k['op'], k['fn'], wraps=k['wraps'], entity=k['entity'], part_of=k['part_of'])
            _reg._KERNELS[k['op']] = self.name
        _reg._BATCH_STEPS.update(self._steps)
        _reg._STEP_OPERATIONS.update(self._step_ops)
        _reg._STEP_TABLES.update(self._step_tables)
        _reg._OBJECT_TABLES.extend(t for t in self._object_tables if t not in _reg._OBJECT_TABLES)
        _reg._ANALYSIS_METHODS.update(self._methods)
        _reg._MENU_ACTIONS.update(self._actions)
        _reg._FEATURE_CARDS.update(self._cards)
        _reg._ADAPTERS.update(self._adapters)
        record.contributions = (
            [('operation', op) for op, owner in _reg._OPERATIONS.items() if owner == self.name]
            + [('kernel', k['op']) for k in self._kernels]
            + [('batch step', s) for s in self._steps]
            + [('object table', t) for t in self._object_tables]
            + [('analysis method', m.label) for m in self._methods.values()]
            + [('menu action', a.label) for a in self._actions.values()]
            + [('feature card', c.title) for c in self._cards.values()]
            + [('navigator adapter', op) for op in self._adapters]
            + [(kind, value) for kind, value in self._vocabulary])

    def _discard(self) -> None:
        """Undo what took effect before the plugin failed: its operations and its vocabulary additions."""
        from pycat.utils import layer_tags, tag_registry
        for op in [op for op, owner in _reg._OPERATIONS.items() if owner == self.name]:
            tag_registry._OPERATIONS.pop(op, None)
            _reg._OPERATIONS.pop(op, None)
        for kind, value in self._vocabulary:
            _reg._VOCABULARY.pop((kind, value), None)
            if kind == 'target':
                layer_tags.CORE_VALUES['target'].discard(value)
                tag_registry.TARGETS = tuple(sorted(layer_tags.CORE_VALUES['target']))
            else:
                tag_registry.REQUIREMENTS.pop(value, None)
                tag_registry.REQUIREMENT_NAMES = tuple(sorted(tag_registry.REQUIREMENTS))
        self._vocabulary.clear()
