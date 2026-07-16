"""
**The OperationSpec drift guard** (increment 1: validate-first).

The Navigator's ``data/operation_catalog.json`` is a COMMITTED SNAPSHOT of the live ``@tags_layer`` /
UI-registered operations. Before this test, nothing checked it still matched the decorators: the old
``test_catalog_has_real_layer_operations`` only asserted a hard-coded handful (``cellpose``, ``clahe``)
were present — it could not catch a decorator that was ADDED, REMOVED, or had its ``role``/``target``
CHANGED without the JSON being regenerated. That is exactly the "one identity, five encodings that
drift" problem the OperationSpec effort exists to end.

This guard makes drift a test failure, three ways:

1. **Coverage** — every live ``@tags_layer``/UI op appears in the snapshot.
2. **No stale layer-ops** — every snapshot layer op corresponds to a live op.
3. **Field fidelity** — ``role`` / ``produces`` / ``target`` in the snapshot match the live declaration.

When it fails for a LEGITIMATE change, the fix is not to edit the JSON by hand — it is to regenerate:

    python -m pycat.navigator.op_catalog --regenerate

…then commit the JSON. The failure messages say so.

Headless: populating the live registry imports the tag-bearing toolbox modules + ``tag_registry``
(the UI ops); none import Qt/napari at module scope, so this runs under ``-m core`` with no display.
"""

import pytest

pytestmark = pytest.mark.core

from pycat.navigator.op_catalog import _measure_ops, load_operation_catalog
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
    """A decorator whose ``role``/``produces``/``target`` changed without regeneration → this fails,
    naming the field."""
    live, catalog = _live(), _catalog()
    mismatches = []
    for op in sorted(set(live) & set(catalog)):
        spec, entry = live[op], catalog[op]
        for field, live_value in (("role", spec.role),
                                  ("produces", spec.produces),
                                  ("target", spec.target)):
            if (entry.get(field) or None) != (live_value or None):
                mismatches.append(f"{op}.{field}: catalog={entry.get(field)!r} live={live_value!r}")
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
