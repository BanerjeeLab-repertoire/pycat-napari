"""
**Every layer gets tagged, and no call site can forget.**

The problem
-----------
There are **116 ``viewer.add_*`` call sites** in PyCAT, and **2 of them tagged anything.**

Hand-editing 114 call sites is the fragile approach: it is a one-off sweep that decays the moment
someone adds the 117th. **And the 117th is exactly the one that will be forgotten**, because
nobody adding a layer is thinking about the tag system.

So the interception happens **once, at the viewer**. Every ``add_image``, ``add_labels``,
``add_points``, ``add_shapes``, ``add_tracks`` is wrapped, and the tag is applied to whatever comes
out. **A new call site is tagged automatically, because it does not know it is being tagged.**

What can be known, and what cannot
----------------------------------
The tag has three possible sources, and they are **not equally reliable**:

1. **The caller.** If a function on the stack carries ``__pycat_op__`` (i.e. it is decorated with
   ``@tags_layer``), that IS the operation — *definitionally*, with no inference.

   **But it usually will not fire.** Most UI code calls the transform and *then* adds the layer::

       result = apply_clahe(img)      # the decorated function has already RETURNED
       viewer.add_image(result, ...)  # the stack no longer contains it

2. **The layer name.** PyCAT names layers descriptively — ``'CLAHE'``, ``'Labeled Cell Mask'``.
   Only ~10 names are literals in the source; **the rest are built at runtime**, so this is a
   heuristic and it is treated as one: the tag is written with ``source='inferred'``.

3. **The data.** ``add_labels`` produces labels; an integer array is a mask or labels; a float
   array is an image. **The ROLE is always inferable, and it is inferable with certainty.**

So the guarantee is deliberately asymmetric:

* **``role`` is ALWAYS set**, from the layer type and the data. This is what makes *"where is the
  mask?"* answerable for **every** layer in the viewer.
* **``op`` is set when it is KNOWN**, and left absent when it is not. **An absent tag is honest; a
  guessed one is a lie that will be queried as truth.**

A layer with a role and no op is still queryable. A layer with nothing is invisible.
"""

from __future__ import annotations

import inspect
import re

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.utils.layer_tags import tag_layer, get_tag
from pycat.utils.tag_registry import get_operation


# ── Which layer types map to which role ───────────────────────────────────────────────────
# ── These are the EXISTING layer_tags roles, not a new vocabulary ───────────────────────────
#
# A first version of this hook invented its own role set ('raw', 'preprocessed', ...) and the
# existing validator rejected every tag it wrote. **That is the degeneracy the tag system exists
# to prevent, and I built it into the tagger.**
#
# 'raw' vs 'derived' is what **provenance** already carries. The ROLE is what the layer IS.
_TYPE_ROLE = {
    'image': 'image',
    'labels': 'labels',
    'points': 'overlay',
    'shapes': 'overlay',
    'tracks': 'overlay',
    'vectors': 'overlay',
    'surface': 'overlay',
}

# ── Name fragments that identify an operation, when the stack cannot ───────────────────────
#
# A HEURISTIC, and treated as one: written with source='inferred', never as fact. It exists
# because most layer names are built at runtime and cannot be harvested from the source.
_NAME_HINTS = (
    ('clahe', 'clahe'),
    ('laplac', 'log'), ('log filter', 'log'), (' log', 'log'),
    ('difference of gauss', 'dog'), ('dog', 'dog'),
    ('rolling ball', 'rolling_ball'),
    ('bandpass', 'bandpass'),
    ('bilateral', 'bilateral'),
    ('gabor', 'gabor'),
    ('wbns', 'wbns'),
    ('watershed', 'watershed'),
    ('cellpose', 'cellpose'),
    ('otsu', 'multi_otsu'),
    ('invert', 'invert'),
    ('threshold', 'local_threshold'),
)


def _role_from(layer_type, data, viewer):
    """The role, from the layer type and the data. **This is the part that is always knowable.**"""
    role = _TYPE_ROLE.get(layer_type, 'overlay')

    if layer_type == 'labels':
        # A boolean or 0/1 integer array is a MASK; anything with more values is LABELS.
        try:
            arr = np.asarray(data)
            if arr.dtype == bool or (np.issubdtype(arr.dtype, np.integer)
                                     and int(arr.max(initial=0)) <= 1):
                role = 'mask'
        except Exception as exc:
            debug_log('layer_tag_hook: could not tell a mask from labels', exc)

    return role


def _op_from_stack(max_depth=15):
    """An operation from a decorated function on the call stack. **Definitional, not inferred.**"""
    try:
        frame = inspect.currentframe()
        for _ in range(max_depth):
            frame = frame.f_back
            if frame is None:
                return None
            name = frame.f_code.co_name
            candidate = frame.f_globals.get(name)
            op = getattr(candidate, '__pycat_op__', None)
            if op:
                return op
    except Exception as exc:
        debug_log('layer_tag_hook: the stack walk failed', exc)
    return None


def _op_from_name(name):
    """An operation guessed from the layer name. **A heuristic, and tagged as one.**"""
    if not name:
        return None
    lowered = re.sub(r'[_\-]+', ' ', str(name)).lower()
    for fragment, op in _NAME_HINTS:
        if fragment in lowered:
            return op
    return None


def install(viewer):
    """Wrap every ``add_*`` on this viewer so that **every layer it makes is tagged.**

    Idempotent: installing twice does nothing the second time.

    Returns the viewer, so it can be chained.
    """
    if getattr(viewer, '_pycat_tag_hook_installed', False):
        return viewer

    # ── napari's Viewer is a PYDANTIC MODEL. `setattr` on it is REJECTED. ────────
    #
    # This used ``setattr(viewer, 'add_image', ...)`` — and pydantic's ``__setattr__`` permits only
    # **declared fields.** ``add_image`` is a *method on the class*, not a field on the instance, so
    # the assignment raised::
    #
    #     ValidationError: 1 validation error for Viewer
    #     add_image
    #       Object has no attribute 'add_image'
    #
    # ***And the whole layer-tagging system was silently dead.*** ``run_pycat`` wraps this install
    # in ``except Exception: debug_log(...)``, so PyCAT started with **no tag hook at all** — and
    # the only sign was a traceback in the terminal that read like a napari bug.
    #
    # ``object.__setattr__`` bypasses pydantic's validation and writes straight to the instance
    # ``__dict__``. Python then finds the **instance** attribute before the class method — which is
    # exactly the interception this hook needs.
    #
    # *(The ``_pycat_tag_hook_installed`` flag is not a declared field either — so it had the same
    # problem, and it is why a retry could never have helped.)*
    for layer_type in ('image', 'labels', 'points', 'shapes', 'tracks', 'vectors', 'surface'):
        method_name = f'add_{layer_type}'
        original = getattr(viewer, method_name, None)
        if original is None:
            continue

        object.__setattr__(viewer, method_name, _wrap(viewer, original, layer_type))

    object.__setattr__(viewer, '_pycat_tag_hook_installed', True)
    return viewer


def _wrap(viewer, original, layer_type):
    def _add(*args, **kwargs):
        layer = original(*args, **kwargs)

        try:
            data = kwargs.get('data', args[0] if args else None)
            name = kwargs.get('name', getattr(layer, 'name', None))

            # ── The ROLE is always set. This is the guarantee. ──────────────────
            role = _role_from(layer_type, data, viewer)
            tag_layer(layer, 'role', role, source='inferred')
            tag_layer(layer, 'layer_type', layer_type, source='inferred')

            # ── raw vs derived is PROVENANCE, not role ───────────────────────────
            #
            # The FIRST image into an empty viewer is the acquisition; everything after it was
            # made by PyCAT. That distinction is what makes "show me the original" answerable,
            # and it belongs in the key that already exists for it.
            if layer_type == 'image':
                try:
                    first = not any(get_tag(l, 'role') == 'image' for l in viewer.layers
                                    if l is not layer)
                    tag_layer(layer, 'provenance', 'raw' if first else 'derived',
                              source='inferred')
                except Exception as exc:
                    debug_log('layer_tag_hook: could not set provenance', exc)

            # ── The OP is set only when it is KNOWN ──────────────────────────────
            #
            # From the stack: definitional. From the name: a guess, and marked as one.
            # **An absent tag is honest; a guessed one is a lie that will be queried as truth.**
            op = _op_from_stack()
            if op and get_operation(op):
                tag_layer(layer, 'op', op, source='derived')     # definitional, not a guess
            else:
                op = _op_from_name(name)
                if op and get_operation(op):
                    tag_layer(layer, 'op', op, source='inferred')
                else:
                    op = None

            # ── The TARGET comes with the operation, and was being dropped ───────
            #
            # The registry already knows that `cellpose` produces CELLS and `bead_detect`
            # produces BEADS — it is declared on the decorator. The hook was tagging the `op` and
            # **throwing the target away**, so a step asking for "the cell labels"
            # (role=labels, target=cell) found NOTHING, even with a Cellpose layer sitting right
            # there.
            #
            # The information existed. It just was not being carried the last inch.
            if op:
                entry = get_operation(op)
                if entry and entry.get('target'):
                    tag_layer(layer, 'target', entry['target'], source='derived')

        except Exception as exc:
            # A tagging failure must NEVER stop a layer being added. The user's data comes first.
            debug_log(f'layer_tag_hook: could not tag a new {layer_type} layer', exc)

        return layer

    _add.__name__ = f'add_{layer_type}'
    _add.__doc__ = (original.__doc__ or '') + (
        "\n\n**PyCAT: this layer is automatically tagged** (see "
        "``pycat.utils.layer_tag_hook``). The ``role`` is always set; the ``op`` is set when it "
        "can be known.")
    return _add
