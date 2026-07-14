"""
**napari's Viewer is a pydantic model, and the tag hook was patching it with ``setattr``.**

That is rejected::

    ValidationError: 1 validation error for Viewer
    add_image
      Object has no attribute 'add_image'

Pydantic's ``__setattr__`` permits only **declared fields.** ``add_image`` is a *method on the
class*, not a field on the instance — so assigning to it on the instance is not allowed.

***And the whole layer-tagging system was silently dead.***

``run_pycat`` wraps the install in ``except Exception: debug_log(...)``, so PyCAT started with **no
tag hook at all**. Every layer went untagged. The tag registry, the resolver, the binding table, the
Tag Inspector, the autopopulation groundwork — **all of it inert**, and the only sign was a
traceback in the terminal that read like a napari bug.

*Gable saw it, asked about it, and it took a full pass through the loader before anyone looked at
it.*

The fix
-------
``object.__setattr__`` bypasses pydantic's validation and writes straight to the instance
``__dict__``. Python's attribute lookup then finds the **instance** attribute *before* the class
method — which is exactly the interception the hook needs.

*(The ``_pycat_tag_hook_installed`` flag is not a declared field either, so it had the same problem
— which is why a retry could never have helped.)*

Why this test exists
--------------------
**A hook that fails to install and is caught by a bare ``except`` is indistinguishable from a hook
that works.** The system it feeds — tags, resolution, autopopulation — degrades to *nothing
happens*, which is exactly what a feature not being used looks like.

So the install is tested against a Viewer that **rejects ``setattr`` the way the real one does.**
"""

import types

import pytest


class _PydanticLikeViewer:
    """A Viewer that refuses ``setattr`` — as napari's pydantic model does."""

    def __init__(self):
        object.__setattr__(self, 'layers', [])

    def __setattr__(self, name, value):
        raise ValueError(
            f"1 validation error for Viewer\n{name}\n  Object has no attribute '{name}'")

    def _make(self, kind, **kwargs):
        layer = types.SimpleNamespace(name=kwargs.get('name', kind), metadata={})
        self.layers.append(layer)
        return layer

    def add_image(self, data, **kwargs):
        return self._make('image', **kwargs)

    def add_labels(self, data, **kwargs):
        return self._make('labels', **kwargs)

    def add_points(self, data, **kwargs):
        return self._make('points', **kwargs)

    def add_shapes(self, data, **kwargs):
        return self._make('shapes', **kwargs)

    def add_tracks(self, data, **kwargs):
        return self._make('tracks', **kwargs)

    def add_vectors(self, data, **kwargs):
        return self._make('vectors', **kwargs)

    def add_surface(self, data, **kwargs):
        return self._make('surface', **kwargs)


@pytest.mark.core
def test_a_plain_setattr_on_this_viewer_IS_rejected():
    """**The premise.** If this passes, the test below proves nothing."""
    viewer = _PydanticLikeViewer()

    with pytest.raises(ValueError, match="Object has no attribute"):
        setattr(viewer, 'add_image', lambda *a, **k: None)


@pytest.mark.core
def test_the_tag_hook_INSTALLS_on_a_pydantic_viewer():
    """**It did not.** And ``run_pycat`` swallowed the failure into ``debug_log``, so PyCAT ran
    with the entire layer-tagging system inert."""
    hook = pytest.importorskip("pycat.utils.layer_tag_hook")

    viewer = _PydanticLikeViewer()

    # Must not raise.
    hook.install(viewer)

    assert viewer.__dict__.get('_pycat_tag_hook_installed') is True, (
        "the hook reported no error but did not mark itself installed"
    )
    assert callable(viewer.__dict__.get('add_image')), (
        "`add_image` was not wrapped. `setattr` on a pydantic model is rejected — use "
        "`object.__setattr__`, which writes straight to the instance __dict__."
    )


@pytest.mark.core
def test_the_wrapped_add_image_STILL_WORKS():
    """**A hook that breaks the thing it wraps is worse than no hook.**"""
    hook = pytest.importorskip("pycat.utils.layer_tag_hook")

    viewer = _PydanticLikeViewer()
    hook.install(viewer)

    layer = viewer.add_image([[1, 2], [3, 4]], name='a test layer')

    assert layer is not None
    assert layer.name == 'a test layer'
    assert len(viewer.layers) == 1


@pytest.mark.core
def test_the_hook_uses_object_setattr_and_NOT_plain_setattr():
    """A plain ``setattr`` cannot work here, and a future edit must not reintroduce one."""
    import ast
    import pathlib

    source_path = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "utils"
                   / "layer_tag_hook.py")
    tree = ast.parse(source_path.read_text(encoding='utf-8', errors='ignore'))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'id', None) != 'setattr':
            continue

        # setattr(viewer, ...) is the fatal one.
        first = node.args[0] if node.args else None
        target = getattr(first, 'id', '')

        assert 'viewer' not in target.lower(), (
            f"line {node.lineno}: `setattr(viewer, ...)` is REJECTED by pydantic — napari's Viewer "
            f"is a pydantic model, and this silently kills the entire tag system. Use "
            f"`object.__setattr__`."
        )
