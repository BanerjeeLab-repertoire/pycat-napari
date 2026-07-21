"""**Analysis-aware kymographs — a line-scan over time, PAIRED with the quantities PyCAT already measures.**

A classic kymograph samples intensity along a line and stacks those lines over time (or depth). The
profile half already exists (`intensity_profile_tools.line_profile`); the value here is (a) doing the
time-stacking **safely** and (b) pairing it with existing measurements.

**The traps:**
- **Lazy-stack collapse.** The stack is read with `materialize_stack`, never `np.asarray` — a lazy
  time-series `__array__` returns FRAME 0 only, and a kymograph is a time-axis tool, so this is the worst
  place to hit that landmine. Guarded by a test.
- **Units.** Axes are labelled in real units (µm along the line, seconds/µm along time/depth) ONLY when
  the pixel size / frame interval are supplied; otherwise px/frame, and the `units` field says which. An
  unlabelled kymograph invites a wrong drift/velocity reading.
- **Averaging band.** `width_px > 1` averages a band perpendicular to the line to cut noise; the band
  width is recorded, because a wide band blurs sub-line structure.
- **Drift.** A fixed line samples different material over time if the sample drifts — the caller should
  drift-correct first (noted; not done here).

Analysis-aware variants reuse existing measurements: `colocalization_kymograph` pairs two channels with a
per-time-slice Pearson (the existing coloc metric), and `object_property_kymograph` plots a tracked
object's property vs time from the per-object table PyCAT already emits.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass
class Kymograph:
    """A (position × time-or-depth) kymograph with the metadata to label its axes honestly."""
    image: np.ndarray
    axis: str                              # 'time' | 'depth'
    position_um: "np.ndarray | None"       # None → position axis is in pixels
    time_or_depth: "np.ndarray | None"     # None → axis is frame/slice index
    line: tuple
    width_px: int
    units: dict                            # {'position': 'µm'|'px', 'axis': 's'|'µm'|'frame'|'slice'}


def _resolve_line(line):
    """Accept ``((y0, x0), (y1, x1))`` endpoints or a napari Shapes line layer, → ``(start_yx, end_yx)``."""
    data = getattr(line, 'data', None)
    if data is not None and len(data):
        pts = np.asarray(data[0])
        return tuple(pts[0][-2:]), tuple(pts[-1][-2:])
    (s, e) = line
    return tuple(s), tuple(e)


def kymograph(stack, line, *, axis='time', width_px=1, reduce='mean',
              pixel_size_um=None, frame_interval_s=None) -> Kymograph:
    """Sample intensity along ``line`` in every frame of a (T/Z, Y, X) ``stack`` and stack the profiles
    into a (position × time/depth) image. The stack is **materialized safely** (never collapsed to frame
    0). Axes are labelled in µm / seconds when the calibration is supplied, px / frame otherwise — the
    ``units`` field records which. ``width_px`` averages a perpendicular band (its width is recorded)."""
    from skimage.measure import profile_line
    try:
        from pycat.file_io.stack_access import materialize_stack
        arr = np.asarray(materialize_stack(stack))
    except Exception:
        arr = np.asarray(stack)
    if arr.ndim != 3:
        raise ValueError(f"kymograph needs a 3-D (T/Z, Y, X) stack; got shape {arr.shape}")

    start_yx, end_yx = _resolve_line(line)
    reduce_fn = {'mean': np.mean, 'max': np.max, 'sum': np.sum}.get(reduce, np.mean)
    cols = [profile_line(arr[i].astype(float), tuple(start_yx), tuple(end_yx),
                         linewidth=max(1, int(width_px)), mode='reflect', reduce_func=reduce_fn)
            for i in range(arr.shape[0])]
    length = min(len(c) for c in cols)                     # guard against off-by-one length drift
    kymo = np.array([c[:length] for c in cols]).T          # (position, time/depth)

    position_um = (np.arange(kymo.shape[0]) * float(pixel_size_um)) if pixel_size_um else None
    if axis == 'depth':
        axis_vals = (np.arange(kymo.shape[1]) * float(pixel_size_um)) if pixel_size_um else None
        axis_unit = 'µm' if pixel_size_um else 'slice'
    else:
        axis_vals = (np.arange(kymo.shape[1]) * float(frame_interval_s)) if frame_interval_s else None
        axis_unit = 's' if frame_interval_s else 'frame'
    units = {'position': 'µm' if pixel_size_um else 'px', 'axis': axis_unit}
    return Kymograph(image=kymo, axis=axis, position_um=position_um, time_or_depth=axis_vals,
                     line=(tuple(start_yx), tuple(end_yx)), width_px=int(width_px), units=units)


def colocalization_kymograph(stack_a, stack_b, line, *, width_px=1, pixel_size_um=None,
                             frame_interval_s=None) -> dict:
    """Two channels' kymographs along the same line, plus the **per-time-slice Pearson** between them — so
    a user sees where along time the two channels co-vary. Reuses the existing Pearson metric on each
    frame's paired profiles."""
    ka = kymograph(stack_a, line, width_px=width_px, pixel_size_um=pixel_size_um,
                   frame_interval_s=frame_interval_s)
    kb = kymograph(stack_b, line, width_px=width_px, pixel_size_um=pixel_size_um,
                   frame_interval_s=frame_interval_s)
    a, b = ka.image, kb.image
    n_t = min(a.shape[1], b.shape[1])
    pearson = []
    for t in range(n_t):
        pa, pb = a[:, t], b[:, t]
        m = np.isfinite(pa) & np.isfinite(pb)
        if m.sum() >= 2 and np.std(pa[m]) > 0 and np.std(pb[m]) > 0:
            pearson.append(float(np.corrcoef(pa[m], pb[m])[0, 1]))
        else:
            pearson.append(np.nan)
    per_slice = pd.DataFrame({
        'slice': np.arange(n_t),
        'axis_value': (ka.time_or_depth[:n_t] if ka.time_or_depth is not None else np.arange(n_t)),
        'pearson': pearson})
    return dict(kymograph_a=ka, kymograph_b=kb, per_slice=per_slice)


def object_property_kymograph(tracks_df, *, id_col, time_col, property_col, object_id) -> pd.DataFrame:
    """A tracked object's ``property_col`` vs time, from the per-object table PyCAT already emits — the
    'analysis-aware' pairing for a single condensate (diameter, intensity, circularity, partition
    coefficient …). Returns the ordered (time, value) series for that object; empty if it is not tracked."""
    df = pd.DataFrame(tracks_df)
    sub = df[df[id_col] == object_id][[time_col, property_col]].dropna()
    return sub.sort_values(time_col).reset_index(drop=True).rename(
        columns={time_col: 'time', property_col: 'value'})
