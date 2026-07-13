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


# ── The roles a layer can have ────────────────────────────────────────────────────────────
#
# Small and closed. A layer that does not fit one of these is a design question, not a new role.
ROLES = (
    'raw',              # straight off the microscope, untouched
    'preprocessed',     # an image that has been filtered/corrected but is still an IMAGE
    'mask',             # binary
    'labels',           # integer-labelled objects
    'overlay',          # points / shapes / tracks drawn ON something
    'measurement',      # a derived quantity rendered as a layer (a map, a field)
    'reference',        # a dark frame, a flat field, a PSF
)

# ── What a layer can be OF ────────────────────────────────────────────────────────────────
TARGETS = (
    'condensate', 'cell', 'nucleus', 'punctum', 'bead', 'fibril', 'chromatin',
    'droplet', 'aggregate', 'background', 'field',
)


_OPERATIONS: dict[str, dict] = {}


class TagCollision(ImportError):
    """**Two operations claimed the same tag.** That is a bug, not a warning.

    The whole value of the vocabulary is that a tag means ONE thing. If ``'watershed'`` is
    registered by two different functions, a query for it returns a mixture — and the tag system
    is worse than no tag system, because it looks like it works.
    """


def register_operation(op: str, *, role: str, summary: str,
                       target: str | None = None, produces: str | None = None,
                       aliases: tuple = ()):
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
    """
    op = str(op).strip().lower()

    if not op:
        raise ValueError("an operation tag cannot be empty")
    if role not in ROLES:
        raise ValueError(f"role '{role}' is not one of {ROLES}")
    if target is not None and target not in TARGETS:
        raise ValueError(f"target '{target}' is not one of {TARGETS}")

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
        registered_by=None,      # filled in by the decorator
    )
    return op


def tags_layer(op: str, *, role: str, summary: str,
               target: str | None = None, produces: str | None = None,
               aliases: tuple = ()):
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
                           produces=produces, aliases=aliases)
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
