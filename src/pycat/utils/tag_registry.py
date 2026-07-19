"""
The controlled vocabulary of PyCAT operations. **One name per operation, enforced.**

Why a registry, and not just strings
------------------------------------
A tag system whose vocabulary grows at the call site **will** produce degenerate tags. Someone
writes ``'clahe'``, someone else writes ``'CLAHE'``, a third writes ``'contrast_limited_ahe'``, and
the tag becomes unqueryable — which is the one thing it exists to be.

So the vocabulary is a **registry**, and it has three properties:

1. **An operation declares its own tag**, on the function that performs it. The tag travels with
   the code, not with the 116 call sites that produce layers — so **it cannot be forgotten**, and
   it cannot drift out of sync with the functions that actually exist.

2. **A duplicate name is an ImportError, not a silent collision.** Non-degeneracy is structural.

3. **The vocabulary is discoverable.** ``list_operations()`` returns everything that exists, with
   what it does and what it produces — which is what the UX curation will be built on.

The four keys
-------------
A single flat string cannot answer the questions the tag system exists to answer without becoming
a sentence. Four keys can:

===========  =============================================================================
key          answers
===========  =============================================================================
``role``     *What KIND of layer is this?* — raw, preprocessed, mask, labels, overlay...
``op``       *What was DONE to it?* — clahe, log, otsu, watershed, cellpose...
``target``   *What is it OF?* — condensate, cell, nucleus, punctum, bead, fibril...
``parent``   *Where did it come FROM?* — carried as a lineage EDGE (see ``layer_tags``)
===========  =============================================================================

``op`` is the one that must be controlled, because it is the one with a hundred values.
"""

from __future__ import annotations

import functools

from pycat.utils.general_utils import debug_log


# ── THE ROLES ARE layer_tags.CORE_VALUES['role']. There is ONE vocabulary. ─────────────────
#
# A first version of this module invented its OWN role set — 'raw', 'preprocessed', ... — and
# ``layer_tags`` rejected every tag written with it. **I built the exact degeneracy this module
# exists to prevent, into the thing that prevents it.**
#
# The roles are imported, not redeclared. If a role is missing, it is added in ONE place
# (``layer_tags.CORE_VALUES``) and everything downstream sees it.
#
# Note that **'raw' vs 'derived' is PROVENANCE, not role** — ``layer_tags`` already carries that
# distinction on its own key, and duplicating it here is what produced the collision.
from pycat.utils.layer_tags import CORE_VALUES as _CORE_VALUES

ROLES = tuple(sorted(_CORE_VALUES['role']))
TARGETS = tuple(sorted(_CORE_VALUES['target']))


# ── THE RUNNABILITY REQUIREMENTS (OperationSpec increment 5) ────────────────────────────────
#
# `inputs` (increment 2) says which LAYERS an operation consumes; `requirements` says what the
# DATA/ENVIRONMENT must provide for it to be runnable at all — a precondition beyond any layer. Each
# maps to a **human-readable reason**, because the point of declaring it is that the UI can grey the
# operation out and SAY WHY ("needs a 3D z-stack") instead of letting the user click it and hit an
# error. A controlled vocabulary, for the same reason `op` is: a free-string requirement is one the UI
# cannot render and nothing can check.
REQUIREMENTS: dict[str, str] = {
    'z_stack':      'a 3D z-stack',
    'time_axis':    'a time axis (a stack of frames over time)',
    'pixel_size':   'a calibrated pixel size (microns per pixel)',
    'two_channels': 'two image channels',
    'gpu':          'a CUDA-capable GPU',
}
REQUIREMENT_NAMES = tuple(sorted(REQUIREMENTS))


_OPERATIONS: dict[str, dict] = {}


class TagCollision(ImportError):
    """**Two operations claimed the same tag.** That is a bug, not a warning.

    The whole value of the vocabulary is that a tag means ONE thing. If ``'watershed'`` is
    registered by two different functions, a query for it returns a mixture — and the tag system
    is worse than no tag system, because it looks like it works.
    """


def register_operation(op: str, *, role: str, summary: str,
                       target: str | None = None, produces: str | None = None,
                       aliases: tuple = (), inputs: tuple = (),
                       requirements: tuple = ()):
    """Declare an operation in the vocabulary. **Raises on a duplicate.**

    Parameters
    ----------
    op : the tag. **Human-readable and short** — ``'clahe'``, ``'log'``, ``'otsu'``. This is what
        appears in the UI and in a query, so it is a *name*, not a description.
    role : what the operation PRODUCES (one of ``ROLES``).
    summary : one line, for the user. This is what the tag inspector shows.
    target : what it operates on, if it is specific (one of ``TARGETS``).
    produces : the role of the OUTPUT, if different from ``role``.
    aliases : other names that mean the same thing, so a query can find it either way. **An alias
        cannot be a registered op** — that would be the collision this class exists to prevent.
    inputs : the layer role(s)/target(s) this operation CONSUMES — the edges that turn the flat
        vocabulary into a graph (``op_a.produces -> op_b.inputs``). Values are drawn from the SAME
        ``ROLES`` / ``TARGETS`` vocabularies, never a third one. **Optional:** an operation that
        declares nothing is a *root* — it loads or creates a layer from a file, not from another
        layer. Absent is honest; a guessed input is drift with extra steps, so declare only what is
        unambiguous (OperationSpec increment 2).
    requirements : what the DATA/ENVIRONMENT must provide for this op to be runnable, beyond any layer
        — values from the controlled ``REQUIREMENTS`` vocabulary (``z_stack``, ``time_axis``, …). It
        exists so a consumer can gate the op with a STATED REASON ("needs a 3D z-stack") instead of
        letting it fail at run time. Optional; declare only unambiguous preconditions (increment 5).
    """
    op = str(op).strip().lower()

    if not op:
        raise ValueError("an operation tag cannot be empty")
    # The sweep uses a few convenience names that map onto the real roles. Mapping them here,
    # ONCE, is better than either (a) inventing a second vocabulary or (b) rewriting 63
    # decorators to say 'image' when they mean 'a filtered image'.
    role = {'preprocessed': 'image', 'measurement': 'result', 'raw': 'image'}.get(role, role)

    if role not in ROLES:
        raise ValueError(
            f"role '{role}' is not one of {ROLES}.\n\n"
            f"**These are layer_tags.CORE_VALUES['role'] — there is ONE vocabulary.** If a new "
            f"kind of layer genuinely exists, add it THERE and everything downstream sees it. "
            f"Inventing a second set here is what produced a version of this module whose every "
            f"tag was rejected by the validator.")
    if target is not None and target not in TARGETS:
        raise ValueError(f"target '{target}' is not one of {TARGETS}")

    # An input must be a REGISTERED role or target — the same refusal an unregistered tag gets. A
    # free string here is exactly the degeneracy the vocabulary exists to prevent, one layer deeper.
    inputs = tuple(str(i).strip().lower() for i in inputs)
    for value in inputs:
        if value not in ROLES and value not in TARGETS:
            raise ValueError(
                f"input '{value}' (declared by '{op}') is not a registered role {ROLES} or "
                f"target {TARGETS}. `inputs` draws on the SAME vocabulary as `role`/`target` — "
                f"there is no third one. A guessed input is worse than none; leave it undeclared.")

    # A requirement must be in the controlled REQUIREMENTS vocabulary — else the UI has no reason to
    # show and nothing can check it, the same degeneracy an unregistered tag would be.
    requirements = tuple(str(r).strip().lower() for r in requirements)
    for value in requirements:
        if value not in REQUIREMENTS:
            raise ValueError(
                f"requirement '{value}' (declared by '{op}') is not one of "
                f"{REQUIREMENT_NAMES}. A requirement must be in the controlled vocabulary so a "
                f"consumer can render its reason and gate on it; add it to REQUIREMENTS (with its "
                f"human-readable reason) if it is genuinely new, don't free-string it here.")

    if op in _OPERATIONS:
        existing = _OPERATIONS[op]
        raise TagCollision(
            f"**The operation tag '{op}' is already registered** by "
            f"{existing['registered_by']}, and {summary!r} is trying to claim it too.\n\n"
            f"A tag must mean ONE thing. If these are genuinely the same operation, they should "
            f"share an implementation. If they are different, they need different names — "
            f"'{op}_2d' and '{op}_3d', or whatever actually distinguishes them.\n\n"
            f"**A silent collision would be worse than this error**: a query for '{op}' would "
            f"return a mixture of two operations, and the tag system would look like it works.")

    for alias in aliases:
        alias = str(alias).strip().lower()
        if alias in _OPERATIONS:
            raise TagCollision(
                f"the alias '{alias}' (for '{op}') is already a registered OPERATION")

    _OPERATIONS[op] = dict(
        op=op, role=role, summary=summary, target=target,
        produces=produces or role,
        aliases=tuple(str(a).strip().lower() for a in aliases),
        inputs=inputs,
        requirements=requirements,
        registered_by=None,      # filled in by the decorator
    )
    return op


def tags_layer(op: str, *, role: str, summary: str,
               target: str | None = None, produces: str | None = None,
               aliases: tuple = (), inputs: tuple = (), requirements: tuple = ()):
    """Decorator: **an operation declares its own tag, on the function that performs it.**

    ::

        @tags_layer('clahe', role='preprocessed',
                    summary='Contrast-limited adaptive histogram equalisation')
        def apply_clahe(image, ...):
            ...

    The tag then travels **with the code**, not with the call sites — so a new caller cannot
    forget it, and a new function that forgets the decorator is **catchable by a test**
    (``test_every_transform_declares_a_tag``).

    The decorator does not touch the return value. It records the operation in the registry and
    stamps the function with ``__pycat_op__``, which is what the layer-creation path reads to tag
    the output. That separation matters: **a function that returns an array should keep returning
    an array**, and the tagging happens where the layer is made.
    """
    def _decorate(fn):
        register_operation(op, role=role, summary=summary, target=target,
                           produces=produces, aliases=aliases, inputs=inputs,
                           requirements=requirements)
        _OPERATIONS[op.strip().lower()]['registered_by'] = (
            f"{fn.__module__}.{fn.__qualname__}")

        fn.__pycat_op__ = op.strip().lower()

        @functools.wraps(fn)
        def _wrapped(*args, **kwargs):
            return fn(*args, **kwargs)

        _wrapped.__pycat_op__ = op.strip().lower()
        return _wrapped

    return _decorate


def list_operations():
    """Every operation in the vocabulary. **This is what the UX curation is built on.**"""
    return {k: dict(v) for k, v in sorted(_OPERATIONS.items())}


def get_operation(op):
    """Look up an operation by its tag or by any of its aliases."""
    op = str(op).strip().lower()
    if op in _OPERATIONS:
        return dict(_OPERATIONS[op])
    for entry in _OPERATIONS.values():
        if op in entry['aliases']:
            return dict(entry)
    return None


def operation_of(fn):
    """The tag a function declares, or None. Used by the layer-creation path."""
    return getattr(fn, '__pycat_op__', None)


def _reset_registry_for_tests():
    """**Tests only.** The registry is process-global by design — it is a vocabulary."""
    _OPERATIONS.clear()


# ── Getting the tag onto the LAYER, and onto the PLOT ─────────────────────────────────────

def tag_from_operation(layer, fn_or_op, *, source_layer=None, target=None, **extra):
    """**Stamp a layer with the operation that produced it.** This is where the tag lands.

    The decorator records *what a function is*; this puts it *on the thing the function made*.
    They are deliberately separate: **a function that returns an array should keep returning an
    array**, and a UI that builds a layer from that array is the only place that knows the layer
    exists.

    ::

        result = apply_clahe(image)
        layer = viewer.add_image(result, name='CLAHE')
        tag_from_operation(layer, apply_clahe, source_layer=raw_layer)

    ``source_layer`` records the **lineage edge**, so the layer knows what it came from — which is
    what makes "show me the raw image behind this mask" answerable.
    """
    from pycat.utils.layer_tags import tag_layer, mark_derived

    op = fn_or_op if isinstance(fn_or_op, str) else operation_of(fn_or_op)
    if not op:
        debug_log('tag_registry: nothing to tag with', ValueError(repr(fn_or_op)))
        return layer

    entry = get_operation(op)
    if entry is None:
        # A tag that is not in the registry is exactly the degeneracy this module exists to
        # prevent. Refuse it loudly rather than write it and let it rot in the data.
        raise KeyError(
            f"'{op}' is not a registered operation. **A tag that is not in the vocabulary is a "
            f"degenerate tag** — it cannot be queried, and nothing else will ever match it. "
            f"Register it with @tags_layer on the function that performs it.")

    tag_layer(layer, 'op', entry['op'], source='pipeline')
    tag_layer(layer, 'role', entry['produces'], source='pipeline')

    if target or entry.get('target'):
        tag_layer(layer, 'target', target or entry['target'], source='pipeline')

    for key, value in extra.items():
        tag_layer(layer, key, value, source='pipeline')

    if source_layer is not None:
        mark_derived(layer, source_layer, via=entry['op'])

    return layer


def tags_for_plot(source_layers, *, plot_of=None, **extra):
    """**A plot is a view of tagged data, and it should say so.**

    Gable: *"a plot that is generated should probably have tags if possible."*

    A figure is not a napari layer, so it cannot carry layer tags — but it can carry the **same
    dictionary**, and that is what makes **brushing** possible: a point in an MSD plot knows which
    ``track_id`` it came from, which ``op`` produced the tracks, and which layer holds them. Click
    the point, and the identity is already there to look the object up with.

    Returns a plain dict, to be attached to the figure (``fig._pycat_tags``) and carried into any
    export. **The identity plumbing has to exist before the interaction can be built** — this is
    that plumbing.
    """
    from pycat.utils.layer_tags import get_tags, layer_tag_id

    layers = source_layers if isinstance(source_layers, (list, tuple)) else [source_layers]

    sources = []
    for layer in layers:
        if layer is None:
            continue
        try:
            sources.append(dict(
                layer_id=layer_tag_id(layer),
                layer_name=getattr(layer, 'name', None),
                tags=get_tags(layer),
            ))
        except Exception as exc:
            debug_log('tag_registry: could not read the tags off a plot source', exc)

    return dict(role='plot', plot_of=plot_of, sources=sources, **extra)


def attach_plot_tags(figure, source_layers, *, plot_of=None, **extra):
    """Attach the plot tags to a matplotlib figure, where a brushing handler can find them."""
    tags = tags_for_plot(source_layers, plot_of=plot_of, **extra)
    try:
        figure._pycat_tags = tags
    except Exception as exc:
        debug_log('tag_registry: could not attach tags to the figure', exc)
    return tags

# ── Operations that live only in the UI ───────────────────────────────────────────────────
#
# Not every operation is a toolbox function. **CLAHE is a UI action** — ``_add_run_clahe`` calls
# ``skimage.exposure.equalize_adapthist`` directly, and a sweep of ``*_tools.py`` misses it
# entirely. So does every napari-native action: a user drawing a shape, merging two label layers,
# expanding labels.
#
# These are registered here, in one place, because **an operation with no home is an operation
# that produces an untagged layer** — and Gable's requirement is that *everything* that makes,
# merges or changes a layer tags it.

def _register_ui_operations():
    """The operations that have no toolbox function to decorate. **Registered explicitly.**"""

    _UI_OPS = [
        # ---- filtering (ui_filtering_mixin) --------------------------------------------------
        # A row may carry a 6th element, `inputs` — the role(s)/target(s) it consumes (the graph
        # edges). Most UI ops leave it off (roots, or their input role is not yet unambiguous);
        # CLAHE is a plain image→image filter, so it declares `('image',)`.
        ('clahe', 'preprocessed', 'Contrast-limited adaptive histogram equalisation', None,
         ('equalize_adapthist',), ('image',)),
        ('im2bw', 'mask', 'Binarise at a threshold', None, ('binarize',)),
        ('best_slice', 'preprocessed', 'Select the best-focused slice of a stack', None, ()),
        ('morph_gaussian', 'preprocessed', 'Morphological Gaussian filter', None, ()),

        # ---- labels & masks (ui_labels_mixin) ------------------------------------------------
        #
        # **These are the MERGES.** A user combining two masks is changing a layer, and the
        # result must say how it was made — otherwise "what is this mask?" has no answer.
        ('labels_to_mask', 'mask', 'Convert a labels layer to a binary mask', None, ()),
        ('label_mask', 'labels', 'Label a binary mask into distinct objects', None, ()),
        ('expand_labels', 'labels', 'Expand labels outward by a distance', None, ()),
        ('mask_merge', 'mask', 'Logical merge of two masks (AND/OR/XOR/subtract)', None,
         ('mask_logic_merge',)),
        ('multi_merge', 'mask', 'Merge several mask layers at once', None, ()),
        ('two_layer_merge', 'labels', 'Advanced merge of two label layers', None, ()),
        ('relabel', 'labels', 'Renumber or increment label values', None, ()),

        # ---- segmentation (ui_segmentation_mixin) --------------------------------------------
        ('stardist', 'labels', 'StarDist star-convex object segmentation', 'cell', ()),
        ('rf_classifier', 'labels', 'Trained random-forest pixel classifier', None, ()),

        # ---- napari-native user actions ------------------------------------------------------
        #
        # A user drawing an ROI, painting a label, or picking points IS changing the data, and the
        # layer that results is as real as any computed one. **An untagged hand-drawn ROI is
        # indistinguishable from a computed mask**, which is exactly the confusion the tag system
        # exists to remove.
        ('hand_drawn', 'annotation', 'Drawn by the user in napari (shapes/points)', None,
         ('user_drawn',)),
        ('hand_painted', 'labels', 'Painted by the user in the napari labels editor', None, ()),
        ('cropped', 'image', 'Cropped to a region', None, ()),
    ]

    for row in _UI_OPS:
        op, role, summary, target, aliases = row[:5]
        inputs = row[5] if len(row) > 5 else ()
        requirements = row[6] if len(row) > 6 else ()
        try:
            register_operation(op, role=role, summary=summary, target=target,
                               aliases=aliases, inputs=inputs, requirements=requirements)
            _OPERATIONS[op]['registered_by'] = 'pycat.utils.tag_registry (UI operation)'
        except TagCollision:
            # Already registered by a toolbox function -- that is fine and correct.
            pass


_register_ui_operations()
