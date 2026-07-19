"""
**The OperationSpec catalog guard** — validate-first (inc 1) → **generate-from-the-spec (inc 4)**.

The Navigator's ``data/operation_catalog.json`` began as a COMMITTED SNAPSHOT of the live
``@tags_layer`` / UI-registered operations, and this guard made any divergence a test failure three
ways (coverage, no-stale-layer-ops, field fidelity) — ending the "one identity, five encodings that
drift" problem.

**Increment 4 flips it.** The Navigator now GENERATES its operation set from the live spec
(``build_operation_registry(from_spec=True)`` → ``iter_operation_specs`` → the decorators) rather than
reading the committed JSON. The decorators are the runtime source of truth; the JSON is a reviewable,
shippable *artifact* — no longer authoritative at run time — kept faithful by a **regeneration check**:
the committed file must equal ``build_catalog_document()`` exactly. The granular coverage/field tests
are kept because they name *what* diverged; the regeneration check is the authoritative one and also
catches provenance/ordering/field-set drift the granular tests do not.

When any of these fails for a LEGITIMATE change, the fix is not to edit the JSON by hand — regenerate:

    python -m pycat.navigator.op_catalog --regenerate

…then commit the JSON. The failure messages say so.

Headless: populating the live registry imports the tag-bearing toolbox modules + ``tag_registry``
(the UI ops); none import Qt/napari at module scope, so this runs under ``-m core`` with no display.
"""

import json
import os

import pytest

pytestmark = pytest.mark.core

from pycat.navigator import op_catalog
from pycat.navigator.op_catalog import (
    _measure_ops, build_catalog_document, build_operation_registry, load_operation_catalog)
from pycat.navigator.operation_spec import OperationSpec, iter_operation_specs

_REGEN = "python -m pycat.navigator.op_catalog --regenerate"


def _live():
    """{op_id: OperationSpec} for every live @tags_layer/UI operation."""
    specs = iter_operation_specs()
    assert specs and all(isinstance(s, OperationSpec) for s in specs)
    return {s.id: s for s in specs}


def _catalog():
    return {o["op"]: o for o in load_operation_catalog()}


def _measure_op_ids():
    """Ids of the build-time-injected measure/interpret ops. They are legitimately NOT @tags_layer
    ops; exclude any snapshot entry that is one of them from the 'no stale layer-ops' check so the
    guard never false-positives on them. (Today the snapshot contains none — measure-ops are added
    at build time, not stored here — but computing the set keeps the guard correct if that changes.)"""
    return {o["id"] for o in _measure_ops()}


def test_every_live_operation_is_in_the_catalog():
    """A ``@tags_layer`` added without regenerating the JSON → this fails, naming the missing op."""
    live, catalog = _live(), _catalog()
    missing = sorted(set(live) - set(catalog))
    assert not missing, (
        f"{len(missing)} live @tags_layer/UI operation(s) are MISSING from operation_catalog.json:\n  "
        + "\n  ".join(missing)
        + f"\n\nThe decorators are the source of truth and the snapshot fell behind. "
          f"Regenerate it:\n    {_REGEN}\n"
    )


def test_no_stale_layer_ops_in_the_catalog():
    """A ``@tags_layer`` removed but left in the JSON → this fails, naming the stale op."""
    live, catalog = _live(), _catalog()
    measure = _measure_op_ids()
    stale = sorted(op for op in catalog if op not in live and op not in measure)
    assert not stale, (
        f"{len(stale)} catalog layer-op(s) no longer correspond to a live @tags_layer/UI decorator:\n  "
        + "\n  ".join(stale)
        + f"\n\nThe operation was removed or renamed in the code but left in the snapshot. "
          f"Regenerate it:\n    {_REGEN}\n"
    )


def test_catalog_fields_match_the_live_declaration():
    """A decorator whose ``role``/``produces``/``target``/``inputs`` changed without regeneration →
    this fails, naming the field."""
    live, catalog = _live(), _catalog()
    mismatches = []
    for op in sorted(set(live) & set(catalog)):
        spec, entry = live[op], catalog[op]
        for field, live_value in (("role", spec.role),
                                  ("produces", spec.produces),
                                  ("target", spec.target)):
            if (entry.get(field) or None) != (live_value or None):
                mismatches.append(f"{op}.{field}: catalog={entry.get(field)!r} live={live_value!r}")
        # `inputs` is a list in the snapshot, a tuple live — compare order-preserving as tuples so a
        # declared-vs-snapshot divergence on the graph edges fails like any other field.
        if tuple(entry.get("inputs") or ()) != tuple(spec.inputs):
            mismatches.append(
                f"{op}.inputs: catalog={entry.get('inputs')!r} live={list(spec.inputs)!r}")
    assert not mismatches, (
        f"{len(mismatches)} catalog field(s) disagree with the live decorator:\n  "
        + "\n  ".join(mismatches)
        + f"\n\nThe declaration changed in the code; the snapshot is stale. Regenerate it:\n    {_REGEN}\n"
    )


def test_the_explicit_canonical_ops_survive():
    """Fold in the old hard-coded check so its value isn't lost: the ops everyone reaches for are
    present and carry the produces/target the code declares."""
    live = _live()
    for op in ("cellpose", "subcellular_segment", "watershed", "clahe", "rolling_ball",
               "bandpass", "bead_detect"):
        assert op in live, f"canonical op '{op}' is not registered — the sweep is incomplete"
    assert live["cellpose"].produces == "labels" and live["cellpose"].target == "cell"
    assert live["clahe"].produces == "image"


# ── Increment 4: the catalog is GENERATED from the spec; the guard is a regeneration check ─────

def test_the_committed_catalog_is_the_regeneration_of_the_spec():
    """**The authoritative guard (increment 4).** The committed ``operation_catalog.json`` must equal
    ``build_catalog_document()`` — the document generated purely from the live decorators — exactly.

    This is stronger than the field-by-field checks above: it also catches a changed provenance field,
    a dropped/added key, or a re-ordering. The catalog is no longer hand-maintained; it is generated,
    and this proves the committed artifact is that generation and nothing else.
    """
    committed_path = os.path.join(os.path.dirname(op_catalog.__file__), "data",
                                  op_catalog.CATALOG)
    with open(committed_path, encoding="utf-8") as fh:
        committed = json.load(fh)

    generated = build_catalog_document()

    assert committed == generated, (
        "the committed operation_catalog.json is NOT the regeneration of the live spec — it is stale "
        "or was hand-edited. It is a generated artifact now; regenerate and commit:\n    "
        "python -m pycat.navigator.op_catalog --regenerate")


def test_the_navigator_registry_GENERATES_from_the_spec_not_the_file():
    """The flip itself: ``build_operation_registry`` builds its layer ops from the spec by default, and
    that produces the same registry as building from the committed file — so the JSON is an artifact,
    not the runtime source of truth (the Navigator would work even without it)."""
    from_spec = build_operation_registry(from_spec=True)
    from_file = build_operation_registry(from_spec=False)

    # Same set of operation contracts either way — the file and the spec are equal by construction.
    spec_names = {c.name for c in from_spec.all()}
    file_names = {c.name for c in from_file.all()}
    assert spec_names == file_names, (
        "generating the registry from the spec yields a different op set than reading the committed "
        "file — the two have diverged, which the regeneration check should also have caught")

    # And the generated set covers every live layer op (the acquisition source aside).
    live_ids = set(_live())
    assert live_ids <= spec_names, (
        f"the generated registry is missing live ops: {sorted(live_ids - spec_names)}")
