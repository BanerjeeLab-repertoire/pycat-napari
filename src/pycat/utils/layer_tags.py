"""
PyCAT Layer Tagging System
==========================
A structured, evidence-backed tagging layer that describes *what each layer is*
and *how layers relate*, so downstream autopopulation can query typed facts
instead of matching freeform names.

Motivation
----------
Historically, step autopopulation guessed a layer's role from its NAME (string
matching: "does this contain 'DAPI'", "does this look like a mask"). Names are
freeform and inconsistent, so that logic is fragile. This module replaces the
guess with typed, inspectable tags assigned at load time from real evidence
(metadata, dimensionality, pixel statistics, provenance) plus explicit
relationships between layers (a mask *belongs-to* an image; an upscaled image is
*derived-from* the original and *supersedes* it).

Design (three cleanly separable concerns — this module is only the first)
------------------------------------------------------------------------
  1. TAG ENGINE (this module): assign / store / query tags + lineage. Stable.
  2. RESOLVER (later): a generic resolve(query) -> layer over these tags.
  3. PER-STEP BINDINGS (later, external JSON): which query each method field uses
     to autopopulate — the volatile layer the user curates per method, kept out
     of code so re-pointing autopopulation never touches engine logic.

Storage model
-------------
The CANONICAL store is the napari layer's own ``layer.metadata['pycat_tags']``
dict, so tags travel with the layer and survive layer operations, and the whole
system is removable by deleting one namespaced key. A lightweight per-session
INDEX (keyed by a stable per-layer id) is a cache for fast queries, rebuilt from
layer metadata on demand and never authoritative.

Records
-------
Tag  : dict(key, value, source, confidence)
    key        controlled-core vocabulary (see CORE_KEYS) or a free 'user:'-
               prefixed key; free tags are permitted alongside the controlled core.
    value      controlled per core key where defined (see CORE_VALUES), else free.
    source     'from_metadata' | 'inferred' | 'derived' | 'user_set'
    confidence 0..1 (metadata/user_set high; inference lower).
Edge : dict(relation, target, via)
    relation   'belongs_to' | 'derived_from' | 'supersedes' | 'pairs_with'
    target     the target layer's tag-id (see layer_tag_id()).
    via        the operation that created the relation (e.g. 'upscale').

Anti-black-box
--------------
Every tag records WHERE it came from and a confidence, so the inspector can show
*why* a tag is set and the user can override it (an override is stored with
source='user_set' and locks the value against re-inference).
"""

from __future__ import annotations

METADATA_KEY = 'pycat_tags'

# ── Controlled core vocabulary ───────────────────────────────────────────────
# Core keys carry defined semantics; values are validated where a controlled set
# exists. Free tags are allowed under a 'user:' key prefix (permissive extras).
CORE_KEYS = {
    'role',          # what the layer IS in the workflow
    'representation',# HOW the data is represented -- intensity_field / binary_mask /
                     # instance_labels / coordinates / trajectories / probability_map /
                     # measurement_table / model_fit / geometry (distinct from 'role', which is the
                     # layer's job in the workflow; a resolver needs "instance labels, not a binary
                     # mask" and that is a representation question, not a role question)
    'state',         # WHERE the data is in the workflow -- raw / corrected / enhanced / segmented /
                     # refined / tracked / measured / fitted / validated. Ordered: a resolver uses
                     # the order to prefer the most-processed version (hand-refined over raw labels)
    'op',            # WHICH OPERATION produced it -- see pycat.utils.tag_registry
    'target',        # what the layer is OF: condensate / cell / nucleus / punctum / bead...
    'layer_type',    # the napari type it was added as (image/labels/points/shapes/tracks)
    'dimensionality',# 2d / 2d+t / z-stack / multi-position
    'axis_order',    # WHICH AXIS IS WHICH: 'YX' / 'TYX' / 'ZYX' / 'TZYX'. `dimensionality` says
                     # what KIND of stack it is; this says where each axis LIVES, which is what
                     # anything indexing or scaling the array needs. A (N, Y, X) movie and a
                     # (N, Y, X) z-stack are the same array — only this tag distinguishes them, and
                     # without it a shared Z scale cannot know whether axis 0 is depth or time.
    'stack_axis',    # 'T' / 'Z' — the user's ANSWER for an undeclared multipage TIFF (source
                     # 'user_set'). Kept distinct from `axis_order`: this records that a human was
                     # asked and what they said; `axis_order` is the resulting layout, which is also
                     # set for files that declared their axes and were never in doubt.
    'modality',      # fluorescence / brightfield
    'channel',       # fluorophore / stain identity (free value from metadata)
    'spectral_bucket',   # coarse emission band: blue/green/red/far_red/unknown. The honest
                         # DAPI-vs-GFP discriminator when channel names collide (both "Fluorescence").
    'scale',         # calibrated / uncalibrated
    'provenance',    # raw / derived / segmentation / pycat-generated
    'purpose',       # what an annotation/drawing layer is FOR (open vocabulary)
    'quality_status',    # QC verdict written ONTO the assessed layer: pass / warn / fail
    'analysis_ready_for',# open vocab: what analysis this layer is fit for after QC (e.g. tracking)
}

CORE_VALUES = {
    # ── 'labels', 'overlay' and 'reference' are ADDITIONS (1.5.493) ─────────────
    #
    # The original set could not distinguish a MASK (binary) from LABELS (many objects), and had
    # nowhere to put a points/shapes/tracks overlay or a dark-frame reference. All three are real
    # kinds of layer, and a layer whose kind cannot be expressed is a layer that cannot be found.
    #
    # NOTE: 'raw' and 'preprocessed' deliberately do NOT go here — that is what 'provenance'
    # already carries ('raw' / 'derived'). **A second vocabulary for the same idea is exactly the
    # degeneracy the tag system exists to prevent**, and a first version of the auto-tagging hook
    # tried to invent one before this was noticed.
    'role': {'image', 'mask', 'labels', 'bead_stack', 'host_mask', 'roi',
             'annotation', 'result', 'overlay', 'reference'},
    'target': {'condensate', 'cell', 'nucleus', 'punctum', 'bead', 'fibril',
               'chromatin', 'droplet', 'aggregate', 'background', 'field'},
    'layer_type': {'image', 'labels', 'points', 'shapes', 'tracks', 'vectors', 'surface'},
    # 'representation' — HOW the data is encoded. Separate from 'role' on purpose: a resolver that
    # needs instance labels (not a binary mask) asks a representation question. A small compatibility
    # lattice lives in representation_satisfies() below.
    'representation': {'intensity_field', 'binary_mask', 'instance_labels', 'coordinates',
                       'trajectories', 'probability_map', 'measurement_table', 'model_fit',
                       'geometry'},
    # 'state' — WHERE in the workflow. ORDERED (see STATE_ORDER below); the order is what lets a
    # resolver prefer the most-processed candidate.
    'state': {'raw', 'corrected', 'enhanced', 'segmented', 'refined', 'tracked', 'measured',
              'fitted', 'validated'},
    # 'quality_status' — the QC verdict, written onto the assessed layer by the QC step.
    'quality_status': {'pass', 'warn', 'fail'},
    # 'op' has its values validated against the OPERATION REGISTRY, not a set here -- see
    # tag_registry.get_operation(). A tag that is not a registered operation is REFUSED.
    'dimensionality': {'2d', '2d+t', 'z-stack', 'multi-position'},
    # 'axis_order' — the layout of the array actually handed to napari. Channels are split into
    # separate layers on load and positions into separate scenes, so C and P never appear here.
    'axis_order': {'YX', 'TYX', 'ZYX', 'TZYX'},
    # 'stack_axis' — the answer to "is this multipage TIFF T or Z?". '?' is the honest value for
    # "the user was asked and the answer did not survive", which beats guessing one.
    'stack_axis': {'T', 'Z', '?'},
    'modality': {'fluorescence', 'brightfield'},
    'spectral_bucket': {'blue', 'green', 'red', 'far_red', 'transmitted', 'unknown'},
    'scale': {'calibrated', 'uncalibrated'},
    'provenance': {'raw', 'derived', 'segmentation', 'pycat-generated',
                   'user-created'},
    # 'channel' has free values (fluorophore names vary by microscope).
    # 'purpose' is intentionally NOT here — it uses SUGGESTED_VALUES (open
    # vocabulary) so users can coin their own purposes for exploration.
}

# Open-vocabulary keys: values are SUGGESTED (for discovery / UI dropdowns and to
# keep common purposes consistent) but ANY value is accepted, unlike CORE_VALUES
# which reject unknown values.
SUGGESTED_VALUES = {
    'purpose': {
        'cell_diameter', 'object_diameter',   # measurement lines
        'roi_measure', 'roi_background',       # region annotations
        'roi_exclude', 'roi_include',
        'line_profile',                        # intensity line-scan
        'scratch',                             # freeform / exploratory
    },
    # 'analysis_ready_for' — what a QC-passed layer is fit to feed. Open vocabulary: workflows coin
    # their own downstream names; these are the common ones for discovery/UI.
    'analysis_ready_for': {
        'segmentation', 'tracking', 'colocalization', 'intensity_measurement',
        'partition_coefficient', 'msd_viscosity', 'frap', 'quantification',
    },
}

# 'pipeline' is a first-class source: a tag written by the pipeline auto-tagger
# (tag_registry.py) records the ACTUAL operation that produced the layer — it is definitional, not a
# guess. It was previously absent here, so tag_layer() silently rewrote it to 'inferred' and dropped
# its confidence to 0.6, mislabelling every pipeline-produced tag as a low-confidence inference. It
# is distinguished from 'derived' on purpose: 'pipeline' means "a recorded PyCAT pipeline step made
# this" (stronger provenance) vs "some derivation made this". See docs/audits/codebase_audit_2026-07-15.md (A1).
VALID_SOURCES = {'from_metadata', 'inferred', 'derived', 'user_set', 'pipeline'}
VALID_RELATIONS = {'belongs_to', 'derived_from', 'supersedes', 'pairs_with',
                   # measurement / tracking / registration lineage (audit A5): a tracks layer
                   # `tracks` its detections; a table is `measured_from` a labels layer; a channel
                   # is `registered_to` another; a dark/flat frame is a `reference_for` a raw image.
                   # These specifically enable the VPT/MSD plot<->layer brushing and coloc linking.
                   'registered_to', 'measured_from', 'tracks', 'reference_for'}

# Confidence defaults by source (callers may override per tag).
DEFAULT_CONFIDENCE = {
    'from_metadata': 1.0,
    'user_set': 1.0,
    'pipeline': 0.95,   # definitional (the operation is known), on par with 'derived'
    'derived': 0.95,
    'inferred': 0.6,
}

# ── Processing-state ordering (audit A4) ──────────────────────────────────────────────────────
# The 'state' tag is ordered: a resolver asked for "the labels for this cell" should prefer a
# hand-refined layer (state='refined') over the raw Cellpose output (state='segmented'). This maps
# each state value to a rank so "most-processed wins" is a comparison, not a special case.
STATE_ORDER = {
    'raw': 0, 'corrected': 1, 'enhanced': 2, 'segmented': 3, 'refined': 4,
    'tracked': 5, 'measured': 6, 'fitted': 7, 'validated': 8,
}


def state_rank(value) -> int:
    """Rank of a 'state' value for most-processed-wins resolution; -1 if unknown."""
    return STATE_ORDER.get(value, -1)


# ── Representation compatibility lattice (audit A3) ───────────────────────────────────────────
# A requirement for a coarse representation is satisfied by a more specific one, but not vice versa.
# This is what stops a naive string match from silently connecting the wrong layer (e.g. handing a
# binary mask to a step that needs instance labels).
_REPRESENTATION_SATISFIES = {
    # provided → set of requirements it can satisfy (besides itself)
    'instance_labels': {'binary_mask'},   # instance labels can stand in for a mask
    'trajectories': {'coordinates'},      # trajectories are coordinates over time
    'probability_map': {'intensity_field'},  # a prob map is a scalar field
}


def representation_satisfies(provided, required) -> bool:
    """Does a layer whose representation is ``provided`` satisfy a step needing ``required``?

    Exact match always satisfies; otherwise the lattice above decides. Unknown values only satisfy
    themselves. This is intentionally conservative — a false 'no' makes the planner ask for a
    conversion step; a false 'yes' silently feeds the wrong data.
    """
    if provided == required:
        return True
    return required in _REPRESENTATION_SATISFIES.get(provided, ())


def layer_tag_id(layer) -> str:
    """A stable identifier for a layer, used as the edge target.

    napari layer names are unique within a viewer at any moment, so the name is
    a workable id; we also stash a uuid in metadata the first time we see a layer
    so an edge survives a rename. Falls back to the name if metadata is absent.
    """
    try:
        md = layer.metadata
        if not isinstance(md, dict):
            return str(getattr(layer, 'name', id(layer)))
        uid = md.get('pycat_tag_uid')
        if not uid:
            import uuid as _uuid
            uid = _uuid.uuid4().hex[:12]
            md['pycat_tag_uid'] = uid
        return uid
    except Exception:
        return str(getattr(layer, 'name', id(layer)))


def _store(layer):
    """Return the canonical tag dict on the layer, creating it if needed."""
    md = getattr(layer, 'metadata', None)
    if not isinstance(md, dict):
        # napari layers always have a metadata dict; guard for non-layer objects.
        raise TypeError("layer has no metadata dict")
    store = md.get(METADATA_KEY)
    if not isinstance(store, dict):
        store = {'tags': [], 'edges': []}
        md[METADATA_KEY] = store
    store.setdefault('tags', [])
    store.setdefault('edges', [])
    return store


def _validate(key, value):
    """Validate a (key, value) against the controlled core. Free 'user:'-prefixed
    keys and any non-core key are allowed (permissive). Returns (ok, message)."""
    if key.startswith('user:'):
        return True, ''
    if key in CORE_KEYS and key in CORE_VALUES:
        if value not in CORE_VALUES[key]:
            return False, (f"value '{value}' not in controlled set for core key "
                           f"'{key}' ({sorted(CORE_VALUES[key])})")

    # ── 'op' is validated against the OPERATION REGISTRY ────────────────────────
    #
    # Not against a set written here, because the vocabulary of operations IS the set of
    # functions that exist — and a list maintained by hand in this file would drift away from
    # them the first time someone adds a filter.
    #
    # **An op that is not registered is a degenerate tag**: nothing else will ever match it, no
    # query will find it, and it rots in the data looking like a real tag. It is refused.
    if key == 'op':
        try:
            from pycat.utils.tag_registry import get_operation
        except Exception:
            return True, ''          # the registry is optional; do not break tagging on it
        if get_operation(value) is None:
            return False, (
                f"'{value}' is not a registered operation. **A tag outside the vocabulary "
                f"cannot be queried** — declare it with @tags_layer on the function that "
                f"performs it (see pycat.utils.tag_registry).")

    return True, ''


# ── Tag assignment ───────────────────────────────────────────────────────────
def tag_layer(layer, key, value, source='inferred', confidence=None,
              overwrite=False):
    """Attach a tag to a layer (canonical store = layer.metadata).

    A user_set tag LOCKS its key: later non-user_set writes are ignored unless
    overwrite=True, so re-running inference can never clobber a user's override.

    Returns True if the tag was written, False if skipped.
    """
    ok, msg = _validate(key, value)
    if not ok:
        # Don't hard-fail on a bad controlled value; record nothing and warn.
        print(f"[PyCAT tags] rejected tag {key}={value}: {msg}")
        return False
    if source not in VALID_SOURCES:
        source = 'inferred'
    if confidence is None:
        confidence = DEFAULT_CONFIDENCE.get(source, 0.6)
    try:
        store = _store(layer)
    except TypeError:
        return False
    tags = store['tags']

    # Is this key already present?
    existing = next((t for t in tags if t.get('key') == key), None)
    if existing is not None:
        locked = existing.get('source') == 'user_set'
        if locked and source != 'user_set' and not overwrite:
            return False  # never clobber a user override with inference
        if not overwrite and existing.get('value') == value \
                and existing.get('source') == source:
            return False  # no change
        existing.update(dict(key=key, value=value, source=source,
                             confidence=float(confidence)))
        _index_put(layer)
        return True

    tags.append(dict(key=key, value=value, source=source,
                     confidence=float(confidence)))
    _index_put(layer)
    return True


def set_user_tag(layer, key, value):
    """User override: assign a tag as authoritative (source='user_set', locked)."""
    return tag_layer(layer, key, value, source='user_set',
                     confidence=1.0, overwrite=True)


def get_tags(layer):
    """Return the list of tag records for a layer (copy)."""
    try:
        store = _store(layer)
    except TypeError:
        return []
    return [dict(t) for t in store['tags']]


def get_tag(layer, key, default=None):
    """Return the VALUE of a single tag key, or default."""
    for t in get_tags(layer):
        if t.get('key') == key:
            return t.get('value')
    return default


def has_tag(layer, key, value=None):
    """True if the layer has tag `key` (optionally == value)."""
    v = get_tag(layer, key, default=_MISSING)
    if v is _MISSING:
        return False
    return True if value is None else v == value


_MISSING = object()


# ── Lineage / relationship edges ─────────────────────────────────────────────
def add_edge(layer, relation, target_layer, via=None):
    """Record a relationship edge from `layer` to `target_layer`.

    E.g. after upscaling: add_edge(new, 'derived_from', original, via='upscale')
    and add_edge(new, 'supersedes', original, via='upscale').
    """
    if relation not in VALID_RELATIONS:
        print(f"[PyCAT tags] rejected edge relation '{relation}'")
        return False
    try:
        store = _store(layer)
    except TypeError:
        return False
    target_id = layer_tag_id(target_layer)
    edge = dict(relation=relation, target=target_id, via=via)
    if edge not in store['edges']:
        store['edges'].append(edge)
        _index_put(layer)
    return True


def get_edges(layer):
    """Return the list of edge records for a layer (copy)."""
    try:
        store = _store(layer)
    except TypeError:
        return []
    return [dict(e) for e in store['edges']]


def mark_derived(new_layer, source_layer, via):
    """Convenience: record that new_layer is derived_from + supersedes
    source_layer via an operation (upscale, background_subtract, segment, …),
    and copy forward the source's identity tags (role/modality/channel) that the
    derived layer should inherit, tagging the derivation provenance.

    This is what makes autopopulation lineage-aware: the head-of-lineage layer
    carries the same role/channel as its ancestor plus a 'derived' provenance,
    so a step querying that role naturally finds the most-derived version.
    """
    add_edge(new_layer, 'derived_from', source_layer, via=via)
    # A segmentation output is a mask, not the same role as its source; other
    # image->image derivations (upscale, background subtract) keep the role.
    if via in ('segment', 'segmentation'):
        tag_layer(new_layer, 'role', 'mask', source='derived')
        tag_layer(new_layer, 'provenance', 'segmentation', source='derived')
        # a mask belongs to the image it was segmented from
        add_edge(new_layer, 'belongs_to', source_layer, via=via)
    else:
        add_edge(new_layer, 'supersedes', source_layer, via=via)
        # inherit identity tags from the source for image->image derivations
        for k in ('role', 'modality', 'channel'):
            v = get_tag(source_layer, k)
            if v is not None:
                tag_layer(new_layer, k, v, source='derived')
        tag_layer(new_layer, 'provenance', 'derived', source='derived')
    return True


# ── Session index (cache, non-authoritative) ─────────────────────────────────
# Keyed by tag-id -> {'name':..., 'tags':[...], 'edges':[...]}. Rebuilt from
# layer.metadata; used for fast cross-layer queries (e.g. head-of-lineage walk)
# without re-reading every layer each time.
_SESSION_INDEX = {}


def _index_put(layer):
    try:
        tid = layer_tag_id(layer)
        store = _store(layer)
        _SESSION_INDEX[tid] = dict(
            name=getattr(layer, 'name', ''),
            tags=[dict(t) for t in store['tags']],
            edges=[dict(e) for e in store['edges']])
    except Exception:
        pass


def rebuild_index(viewer):
    """Rebuild the session index from all layers' canonical metadata."""
    _SESSION_INDEX.clear()
    try:
        for lyr in viewer.layers:
            _index_put(lyr)
    except Exception:
        pass
    return dict(_SESSION_INDEX)


def clear_all_tags(layer):
    """Remove the entire tag store from a layer (cleanup helper)."""
    try:
        md = getattr(layer, 'metadata', None)
        if isinstance(md, dict) and METADATA_KEY in md:
            del md[METADATA_KEY]
        tid = layer_tag_id(layer)
        _SESSION_INDEX.pop(tid, None)
    except Exception:
        pass
