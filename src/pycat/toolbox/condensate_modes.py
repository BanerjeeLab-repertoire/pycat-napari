"""**Explicit 2D / 3D / time-series condensate modes — refuse an approximation, don't emit a plausible lie.**

`invitro_fluor_ui` already prints *"area fraction=… (2D projection, not a volume fraction)"* — the code is
honest, but the honesty lives in a transient napari message while the number travels onward into tables,
the consolidated long table, and comparative figures with no qualifier attached. Two problems follow: a
projected area fraction is NOT a volume fraction (their relationship depends on object size/shape/axial
overlap), and the same workflow is applied to fundamentally different data shapes where some measurements
valid in one are meaningless in another (a "volume fraction" from a single 2D plane; a per-frame size
distribution treated as independent samples when it is one drifting population).

**The fix is labelling and gating, not recomputation** — the 2D numbers are correct *as projected
quantities*. The mode is declared or derived from the data, **never silently assumed** (a 3D array is
ambiguous — z-stack vs time series — and that is disambiguated, not guessed). Volume fraction is **refused
with a stated reason** in 2D rather than converted, because the conversion needs assumptions
(mono-disperse spheres, no axial overlap) the data cannot support — the same "refuse rather than lie"
contract as the pixel-size and calibration gates. Every emitted quantity carries its `condensate_mode`, and
a time series declares itself one biological unit so downstream statistics do not pseudoreplicate it.
"""
from __future__ import annotations

import enum

import numpy as np

from pycat.utils.errors import ScientificAssumptionError


class CondensateMode(str, enum.Enum):
    FIELD_2D = '2d'            # one plane; projected quantities only
    ZSTACK_3D = '3d'          # true volumes available
    TIMESERIES = 'timeseries'  # one population through time; per-frame rows are NOT independent samples


def resolve_condensate_mode(data, *, declared=None, axis_kind=None) -> CondensateMode:
    """The mode, declared or derived — **never silently guessed for an ambiguous 3D array.**

    ``declared`` (a `CondensateMode` or its value) always wins. Otherwise a 2D array is `FIELD_2D`; a 3D
    array is ambiguous (z-stack vs time series) and requires ``axis_kind`` ('z' → 3D, 't'/'time' →
    timeseries) — because z and t have different valid measurements (a volume fraction is meaningful for z,
    meaningless for t). With neither, it **refuses** rather than guessing, pointing at the loader's existing
    disambiguation."""
    if declared is not None:
        return CondensateMode(declared)
    arr = np.asarray(data)
    if arr.ndim == 2:
        return CondensateMode.FIELD_2D
    if arr.ndim == 3:
        if axis_kind == 'z':
            return CondensateMode.ZSTACK_3D
        if axis_kind in ('t', 'time', 'timeseries'):
            return CondensateMode.TIMESERIES
        raise ScientificAssumptionError(
            "a 3D condensate array is ambiguous — is the third axis Z (a z-stack, true volumes available) "
            "or T (a time series, one drifting population)? These have different valid measurements, so the "
            "mode must be declared (use the loader's stack-axis disambiguation), not guessed from the shape.")
    raise ScientificAssumptionError(f"a condensate mode needs a 2D or 3D array, got ndim={arr.ndim}")


# ── Which quantities are valid in which mode (the gating table, made data) ───────────────────────
_QUANTITY_AVAILABILITY = {
    'projected_area_fraction': {CondensateMode.FIELD_2D: 'primary', CondensateMode.ZSTACK_3D: 'available',
                                CondensateMode.TIMESERIES: 'per-frame'},
    'volume_fraction': {CondensateMode.FIELD_2D: 'refused', CondensateMode.ZSTACK_3D: 'true',
                        CondensateMode.TIMESERIES: 'refused'},
    'size_distribution': {CondensateMode.FIELD_2D: 'projected-radii', CondensateMode.ZSTACK_3D: 'true-radii',
                          CondensateMode.TIMESERIES: 'non-independent'},
}


def quantity_status(quantity, mode) -> str:
    """How a quantity is treated in a mode: 'primary'/'available'/'true'/'refused'/'per-frame'/… ."""
    return _QUANTITY_AVAILABILITY.get(quantity, {}).get(CondensateMode(mode), 'unknown')


def projected_area_fraction(mask_2d, *, cell_area_px=None) -> float:
    """The 2D projected area fraction — a PROJECTION proxy, correct as such (see the ontology caveat)."""
    dense = np.asarray(mask_2d) > 0
    total = float(dense.size if cell_area_px is None else cell_area_px)
    return float(dense.sum() / total) if total > 0 else float('nan')


def volume_fraction(masks, mode, *, cell_volume_px=None):
    """The volume fraction, gated by mode. Returns ``(value, reason)``.

    - **`FIELD_2D`**: refused — returns ``(nan, reason)``. A single plane cannot measure a volume fraction,
      and converting the projected area fraction needs assumptions the data cannot support. **No estimate.**
    - **`TIMESERIES`**: refused unless a z dimension is present — a time series of 2D frames has none.
    - **`ZSTACK_3D`**: the true value — dense voxels over total (or cell) voxels.
    """
    mode = CondensateMode(mode)
    if mode == CondensateMode.FIELD_2D:
        return float('nan'), ("volume fraction is NOT measurable from a single 2D plane — the projected "
                              "area fraction is a projection proxy, and converting it needs assumptions "
                              "(mono-disperse spheres, no axial overlap) the data cannot support. Reported "
                              "as NaN, not a fabricated estimate.")
    if mode == CondensateMode.TIMESERIES:
        return float('nan'), ("volume fraction needs a Z dimension; a time series of 2D frames has none. "
                              "Acquire a z-stack to measure volumes.")
    dense = np.asarray(masks) > 0
    total = float(dense.size if cell_volume_px is None else cell_volume_px)
    return (float(dense.sum() / total) if total > 0 else float('nan')), ''


def attach_mode_column(table, mode):
    """Return ``table`` with a ``condensate_mode`` column, so the qualifier travels with every number
    instead of evaporating with the napari info message."""
    df = table.copy()
    df['condensate_mode'] = CondensateMode(mode).value
    return df


# ── Time-series independence — a series is ONE biological unit, not N independent frames ─────────
def is_pseudoreplicated(mode) -> bool:
    """True in `TIMESERIES` mode: per-frame measurements of the same droplets are not independent samples,
    so treating them as N observations would pseudoreplicate."""
    return CondensateMode(mode) == CondensateMode.TIMESERIES


def mark_timeseries_as_unit(table, series_id, *, unit_col='biological_unit'):
    """Stamp a per-frame time-series table with a single biological-unit id, so the comparative-figures
    replicate aggregation (`aggregate_to_unit(unit_cols=[unit_col])`) collapses the whole series to ONE
    unit rather than counting each frame as an independent replicate. Reuses the existing pseudoreplication
    machinery — a time series declares itself one unit rather than a new aggregation being written."""
    df = table.copy()
    df[unit_col] = str(series_id)
    df['condensate_mode'] = CondensateMode.TIMESERIES.value
    df['pseudoreplicated'] = True
    return df
