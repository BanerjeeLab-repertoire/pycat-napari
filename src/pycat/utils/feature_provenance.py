"""**Per-feature provenance — attach the workflow chain to each measurement, not just the session.**

`batch_processor` records a complete, ordered, replayable workflow — but that chain is attached to the
*session*, not to the *measurement*. A results table with 40 columns and a 12-step workflow gives no way to
know that `partition_coefficient` depended on steps 3, 5 and 9 but not on the fibril segmentation in step 7.
A table exported today and opened in six months carries its values but not the route to them, and
reproducibility is the manuscript's central claim.

This elevates the existing recording to the feature level, **composed from what already exists — never a
second recording mechanism** (a parallel recorder would drift from `batch_processor`, the registry tax this
codebase works to remove). Each field is derived from a source already present: `operation_id` from the
`operation_context` tags, `input_layers` from the layer-tag identities, `step_indices` from the recorded
config, `software`/`acquisition` from the environment and `metadata_extract`.

**Absent beats guessed** (the layer-tag hook's `derived` vs `inferred` principle): a field that cannot be
derived is left `None` with a reason. In particular, **"all steps" is not provenance** — a record that
lists every step is indistinguishable from no record — so `step_indices` walks the layer LINEAGE backward
and reports only the steps that actually produced the feature's ancestors, or `None` when the lineage
cannot discriminate. Capturing provenance never touches a computed value.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class FeatureProvenance:
    feature: str                      # column name, e.g. 'partition_coefficient'
    operation_id: "str | None" = None      # the operation that computed it (from operation_context tags)
    input_layers: tuple = ()          # pycat_layer_id(s) consumed
    step_indices: "tuple | None" = None    # indices into the recorded workflow; None = could not discriminate
    parameters: dict = dataclasses.field(default_factory=dict)   # the params that affected it
    software: dict = dataclasses.field(default_factory=dict)     # pycat + key dependency versions
    acquisition: dict = dataclasses.field(default_factory=dict)  # pixel size, frame interval, exposure
    step_reason: str = ''             # why step_indices is None, when it is


def software_versions() -> dict:
    """The pycat version plus the key scientific dependencies — captured automatically so a table records
    the software that produced it. An unavailable version is omitted, never guessed."""
    import importlib.metadata as _md
    out = {}
    for dist, key in (('pycat-napari', 'pycat'), ('numpy', 'numpy'), ('scipy', 'scipy'),
                      ('scikit-image', 'scikit-image'), ('pandas', 'pandas')):
        try:
            out[key] = _md.version(dist)
        except Exception:  # broad-ok: an absent dist just means that version is not recorded — never fabricate one
            pass
    return out


def acquisition_from_metadata(metadata) -> dict:
    """The acquisition fields that scale a measurement, pulled from a `metadata_extract` common dict. Only
    the ones present are recorded (absent → omitted, not guessed)."""
    meta = metadata or {}
    fields = {}
    for key in ('pixel_size_um', 'frame_interval_s', 'exposure_s', 'z_step_um', 'acquisition_mode'):
        value = meta.get(key)
        if value is not None:
            fields[key] = value
    return fields


def trace_step_indices(feature_layer_id, lineage, layer_step):
    """Walk the layer LINEAGE backward from the feature's producing layer, collecting only the recorded
    step indices that produced it or its ancestors — so a feature from one branch does NOT claim the steps
    of an unrelated branch.

    Parameters
    ----------
    feature_layer_id : the ``pycat_layer_id`` the feature was measured on.
    lineage : ``{layer_id: [parent_layer_id, ...]}`` — the derivation edges (a layer's immediate sources).
    layer_step : ``{layer_id: step_index}`` — which recorded step produced each layer.

    Returns ``(step_indices | None, reason)``. Returns ``None`` with a reason when the feature's layer is
    not in the recorded lineage at all — because "all steps" would be indistinguishable from no record.
    """
    if feature_layer_id is None or (feature_layer_id not in layer_step and feature_layer_id not in lineage):
        return None, ("the feature's producing layer is not in the recorded lineage, so the steps behind "
                      "it cannot be attributed — reported as unknown rather than 'all steps'")
    seen, stack, steps = set(), [feature_layer_id], set()
    while stack:
        lid = stack.pop()
        if lid in seen:
            continue
        seen.add(lid)
        if lid in layer_step:
            steps.add(int(layer_step[lid]))
        for parent in lineage.get(lid, ()):  # noqa: dict.get default () is intentional
            if parent not in seen:
                stack.append(parent)
    return tuple(sorted(steps)), ''


def compose_provenance(feature, *, operation_id=None, input_layers=(), step_indices=None,
                       step_reason='', parameters=None, metadata=None):
    """Compose a `FeatureProvenance` from the fields the caller could derive, filling software and
    acquisition automatically. **Never fabricates** — an underivable field stays its `None`/empty default.
    Pure: it reads its inputs and the environment, and touches no computed value."""
    return FeatureProvenance(
        feature=str(feature),
        operation_id=operation_id,
        input_layers=tuple(input_layers or ()),
        step_indices=(tuple(step_indices) if step_indices is not None else None),
        step_reason=step_reason,
        parameters=dict(parameters or {}),
        software=software_versions(),
        acquisition=acquisition_from_metadata(metadata))


# ── Part D — the sidecar (not 40 extra CSV columns) and the "where did this come from?" query ────
def provenance_sidecar_dict(provenance_by_column) -> dict:
    """A JSON-serializable dict keyed by COLUMN NAME — the companion to an exported table, so 40 columns
    of provenance do not bloat the CSV into unreadability."""
    out = {}
    for col, prov in provenance_by_column.items():
        out[col] = {
            'feature': prov.feature,
            'operation_id': prov.operation_id,
            'input_layers': list(prov.input_layers),
            'step_indices': (None if prov.step_indices is None else list(prov.step_indices)),
            'step_reason': prov.step_reason,
            'parameters': prov.parameters,
            'software': prov.software,
            'acquisition': prov.acquisition,
        }
    return out


def write_provenance_sidecar(table_path, provenance_by_column):
    """Write ``<table_stem>_provenance.json`` next to an exported table. Returns the sidecar path."""
    import json
    import pathlib
    p = pathlib.Path(table_path)
    sidecar = p.with_name(p.stem + '_provenance.json')
    sidecar.write_text(json.dumps(provenance_sidecar_dict(provenance_by_column), indent=1,
                                  ensure_ascii=False, default=str), encoding='utf-8')
    return sidecar


def read_provenance_sidecar(sidecar_path) -> dict:
    """Read a provenance sidecar back as ``{column: dict}``."""
    import json
    import pathlib
    return json.loads(pathlib.Path(sidecar_path).read_text(encoding='utf-8'))


def describe_provenance(prov) -> str:
    """The "where did this number come from?" answer for one feature — a readable chain. This is the
    affordance that makes the record worth keeping: provenance nobody can query is just storage."""
    lines = [f"'{prov.feature}' was produced by:"]
    lines.append(f"  operation: {prov.operation_id or '(not recorded)'}")
    if prov.step_indices is None:
        lines.append(f"  workflow steps: unknown — {prov.step_reason or 'lineage incomplete'}")
    else:
        lines.append(f"  workflow steps: {list(prov.step_indices) or '(none — a root/loaded layer)'}")
    if prov.input_layers:
        lines.append(f"  from layers: {list(prov.input_layers)}")
    if prov.parameters:
        lines.append(f"  parameters: {prov.parameters}")
    if prov.acquisition:
        lines.append(f"  acquisition: {prov.acquisition}")
    if prov.software.get('pycat'):
        lines.append(f"  software: pycat {prov.software['pycat']}")
    return "\n".join(lines)
