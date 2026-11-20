Writing a PyCAT Plugin
======================

A plugin adds workflows, methods and algorithms to PyCAT from a separately installed package, kept in its own
repository and released on its own schedule. PyCAT finds it at startup; nothing in PyCAT is edited.

A plugin can add:

* **operations** to the operation catalog the Navigator plans over, and to the layer-tag vocabulary;
* **execution kernels**, so a panel, a batch step and the Navigator run an operation the same way;
* **batch steps**, so what a panel records replays over a folder of files;
* **per-object tables** in ``consolidated_long.csv``;
* **analysis methods** (full panels with a workflow checklist), **toolbox tools** and **menu actions** under the
  **✦ Plugins** menu, and **cards** on the Home dock;
* **targets** (``bacterium``) and **runnability requirements** to the controlled vocabularies.

The API is ``pycat.plugins``; every call is a method of :class:`pycat.plugins.api.PluginAPI`, documented there.


The package
-----------

A plugin is a normal pip package that declares an entry point in the ``pycat.plugins`` group. The entry-point
name is the plugin's **namespace**; everything it registers is named ``<namespace>.<name>``.

.. code-block:: toml

   # pyproject.toml
   [project]
   name = "wadsworthlab-bacteria-host"
   dependencies = ["pycat-napari"]   # pin to the first release that ships pycat.plugins

   [project.entry-points."pycat.plugins"]
   bacteria_host = "wadsworthlab_bacteria_host:register"

.. code-block:: python

   # wadsworthlab_bacteria_host/__init__.py
   PYCAT_PLUGIN_API = 1          # the plugin API major this was written for

   def register(api):
       api.add_target('bacterium')                        # vocabulary first ...
       from wadsworthlab_bacteria_host import ops         # ... then the modules whose decorators use it

       api.add_kernel('bacteria_host.count', 'wadsworthlab_bacteria_host.ops:count_bacteria', style='named')
       step = api.add_batch_step('count', ops.replay_count, operations=('bacteria_host.count',),
                                 result_table=('bacterium', 'bacteria_df'))
       api.add_object_table('bacteria_df')
       api.add_navigator_adapter('bacteria_host.count', step)
       api.add_analysis_method('adhesion', 'Bacterial adhesion',
                               'wadsworthlab_bacteria_host.panel:AdhesionUI',
                               pipeline=(('open_image', 'Open image'), (step, 'Count bacteria')))

Install it into the PyCAT environment (``pip install -e .`` while developing) and restart PyCAT. **✦ Plugins ▸
Installed plugins…** lists every plugin found, what each added, and why any did not load.


Operations
----------

Decorate the function that does the science, exactly as PyCAT's own toolbox does. The decorator runs when
``register`` imports the module, and because the module is in the plugin's own package the operation is
attributed to the plugin. (Import operation modules from ``register``, not later: an operation first declared
after loading is not the plugin's.)

.. code-block:: python

   # ops.py — no Qt here
   from pycat.utils.tag_registry import tags_layer
   from pycat.kernel.operation_service import OperationService

   @tags_layer('bacteria_host.count', role='measurement', target='bacterium',
               summary='Count bacteria adhering to host cells', inputs=('labels',))
   def count_bacteria(labels):
       return {'n_bacteria': int(labels.max())}

   def replay_count(state, image_path, params, output_dir):
       from pycat.plugins.batch import resolve_layer
       labels = resolve_layer(state, params['labels_layer'])
       record = OperationService.produce('bacteria_host.count', {'labels': labels})
       ...

A function that is not decorated (a measurement written elsewhere) is registered with ``api.add_operation``.
Operation ids must start with ``<namespace>.``; a decorator outside it stops the plugin loading, with the reason.

Plugin operations join the live vocabulary, so the Navigator plans over them and layers they produce are tagged.
They are **not** written to PyCAT's committed ``operation_catalog.json``: that file and its regeneration guard
describe PyCAT alone, whatever is installed.

Roles are not extensible (the planner maps each role to a data representation). Targets and requirements are:
``api.add_target`` and ``api.add_requirement`` add a value if it does not exist, and share it if it does.


Batch steps
-----------

A batch step has PyCAT's own contract, ``fn(state, image_path, params, output_dir) -> None``. A panel records
``self._record('bacteria_host.count', params)``; batch calls the step with those ``params`` for every file.
``state['data_instance']`` is the file's data repository. Raise ``pycat.utils.errors.StepNotImplemented`` to be
reported as skipped.

``pycat.plugins.batch`` holds the stable helpers a step needs: ``resolve_layer(state, recorded_name)`` finds the
array for a layer name the panel recorded, the way PyCAT's own steps do, and
``write_object_table(state, df, 'bacteria_df', image_path, output_dir)`` stores a per-object table and writes it
where ``consolidated_long.csv`` reads it (declare the key once with ``api.add_object_table``). Import nothing
underscored from ``pycat.batch``; those internals may change.


Panels
------

An analysis method's ``ui_class`` follows PyCAT's panel contract: ``__init__(viewer, central_manager)`` and
``setup_ui()``, which builds the dock (``viewer.window.add_dock_widget``) and activates its checklist with
``central_manager.workflow_checklist.activate('bacteria_host.adhesion')``. ``toolbox/frap_ui.py`` is a compact
model. Give the class as ``'module:Class'`` so the ``register`` module, and therefore a headless batch run,
never imports Qt; it is imported when the menu item is clicked. A saved session whose active method is the
class's name reopens it.


Rules PyCAT applies
-------------------

* **Isolation.** A plugin that fails to import, declares another API major, or raises in ``register`` is listed
  with the reason and skipped. What it registered before failing is removed. Other plugins still load.
* **Validation.** Calls check their arguments, and once ``register`` returns PyCAT cross-checks the whole
  (a batch step's operations exist, an adapter names one of the plugin's steps, a kernel belongs to one of the
  plugin's operations). Any problem fails the plugin with all the problems named.
* **Versioning.** ``PYCAT_PLUGIN_API`` must equal the running PyCAT's API major (``api.version``). Additions
  bump the minor; a plugin can test ``api.version >= (1, 1)`` before using one.
* **Opting out.** ``PYCAT_DISABLE_PLUGINS=1`` loads no plugins. PyCAT's test suite sets it.


Testing a plugin
----------------

``pycat.plugins.loader.load_plugins(entry_points=[...])`` loads exactly the entry points given (the env switch
and the once-per-process latch do not apply) and returns their records; ``record.status == 'loaded'`` or
``record.error`` says why not. PyCAT's ``tests/test_plugins.py`` is a worked example.
