"""
Time-Series In Vitro Fluorescence — analysis foundation (2D+t).

This is the *platform* layer for temporal analysis of in vitro condensates
(protein/RNA LLPS droplets on a coverslip, imaged over time). It extends the
validated 2D in vitro fluorescence analysis (``invitro_tools`` +
``invitro_fluor_ui``) frame-by-frame, then LINKS droplets across frames into
per-condensate temporal objects.

The design goal is a durable per-condensate *object record* that later,
specialised analyses attach their own time-series to. Planned consumers of this
foundation (each its own follow-on module):
  * interior bubbling (void/vacuole formation and motion inside a droplet)
  * catalysis kinetics (intensity-vs-time of a fluorescent reporter)
  * internal flow (bead diffusion tracking interior flow fields)
  * fiber growth off the interface
  * the contrast-cascade method

Key differences from bead tracking (VPT):
  * objects are much larger, slower, and have irregular, changing boundaries;
  * linking keys on centroid + AREA/shape consistency with a size-scaled search
    radius (not a tight per-frame displacement), and is FUSION-AWARE — two
    condensate tracks can merge into one (droplet fusion), which the bead linker
    forbids. Fusion events are detected and flagged rather than suppressed.

Nothing here imports Qt; all UI lives in ``timeseries_invitro_fluor_ui``. All
functions are import-and-analyse and operate on plain numpy / pandas objects so
they can be unit-tested headless.
"""

from __future__ import annotations

from typing import Optional, Callable, List, Dict, Any

import numpy as np
import pandas as pd

from pycat.utils.tag_registry import tags_layer


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-frame segmentation → a labelled (T, H, W) stack
# ─────────────────────────────────────────────────────────────────────────────

def make_frame_segmenter(method='Multi-Otsu', multiotsu_classes=3, min_area_px=20):
    """The per-frame droplet segmenter the in-vitro time-series panel offers, as ``seg(frame, t) -> labels``
    for `segment_stack_per_frame` -- shared by the GUI (Step 3) and batch replay (1.7.004).

    ``method``: 'Droplet …' (per-droplet boundaries on the raw frame, `invitro.droplets.segment_droplets_by_peak`),
    'Multi-Otsu' (brightest of ``multiotsu_classes`` classes), 'Otsu', or anything else for Otsu + distance
    watershed. Objects under ``min_area_px`` are dropped.
    """
    from pycat.utils.general_utils import remove_small_objects_compat

    def _drop_small(lab):
        from skimage import measure
        keep = remove_small_objects_compat(lab > 0, int(min_area_px))
        return measure.label(keep).astype(np.int32)

    def _seg(frame, t):
        from skimage import filters, measure
        if method.startswith("Droplet"):
            # Per-droplet boundaries on the RAW frame (it rescales internally; no per-frame stretch).
            from pycat.toolbox.invitro.droplets import segment_droplets_by_peak
            return segment_droplets_by_peak(np.asarray(frame), min_area=max(int(min_area_px), 1))
        f = np.asarray(frame, dtype=np.float32)
        mn, mx = float(f.min()), float(f.max())
        fn = (f - mn) / (mx - mn + 1e-8) if mx > mn else f
        if method == "Multi-Otsu":
            try:
                ths = filters.threshold_multiotsu(fn, classes=int(multiotsu_classes))
                binary = fn > ths[-1]      # brightest class = droplets
            except ValueError:             # too few distinct values for that many classes
                binary = fn > filters.threshold_otsu(fn)
        elif method == "Otsu":
            binary = fn > filters.threshold_otsu(fn)
        else:  # Threshold + watershed
            from scipy import ndimage as ndi
            from skimage.feature import peak_local_max
            from skimage.segmentation import watershed
            binary = fn > filters.threshold_otsu(fn)
            dist = ndi.distance_transform_edt(binary)
            coords = peak_local_max(dist, min_distance=5, labels=binary)
            markers = np.zeros(dist.shape, dtype=np.int32)
            for i, (yy, xx) in enumerate(coords, 1):
                markers[yy, xx] = i
            lab = watershed(-dist, markers, mask=binary)
            return _drop_small(lab) if min_area_px > 0 else lab
        b = np.asarray(binary) > 0
        if min_area_px > 0:
            b = remove_small_objects_compat(b, int(min_area_px))
        return measure.label(b).astype(np.int32)
    return _seg


@tags_layer('ts_droplet_segment', role='labels', target='condensate', inputs=('image',),
            summary='Time-series per-frame droplet segmentation')
def segment_stack_per_frame(
    stack_like,
    segment_frame_fn: Callable[[np.ndarray, int], np.ndarray],
    n_frames: Optional[int] = None,
    keyframe_every: int = 1,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    dtype=np.int32,
    n_workers: int = 1,
) -> np.ndarray:
    """Segment every frame of a (T, H, W) stack into a labelled stack.

    Streams frames one at a time (never materialising the whole movie) via
    ``file_io.iter_frames`` and calls ``segment_frame_fn(frame, t)`` per frame,
    which must return a 2D integer label image for that frame. Labels are
    per-frame here (independent); cross-frame identity is assigned later by
    :func:`link_condensates`.

    Parameters
    ----------
    stack_like : (T, H, W) array or lazy wrapper.
    segment_frame_fn : callable(frame2d, t) -> (H, W) int label image.
    n_frames : total frames, if known (else inferred from shape).
    keyframe_every : if > 1, only frames at multiples of this are segmented and
        the previous keyframe's mask is carried forward to the frames between
        (an OPT-IN speed trade for very long stacks — masks between keyframes
        are copies, so growth/motion within the gap is not resolved). Default 1
        (segment every frame). Multi-Otsu-class segmentation is cheap, so the
        default is per-frame; keyframing exists only for exceptional lengths.
    progress_callback : optional callable(done, total).
    n_workers : frames segmented concurrently in threads (default 1 = serial). Only ``2 * n_workers`` frames
        are in flight at once, so the movie is still streamed. The per-droplet method spends its time in
        numpy/scipy, which release the GIL: 8 threads segment the 180-frame HHrz movie in ~3 min instead of
        ~14 (4.6 -> 1.0 s/frame), with identical labels. Ignored when keyframing.

    Returns
    -------
    (T, H, W) int32 labelled stack (labels are per-frame, not yet linked).
    """
    from pycat.file_io.stack_access import iter_frames

    shp = getattr(stack_like, 'shape', None)
    if n_frames is None:
        if shp is not None and len(shp) >= 3:
            n_frames = int(shp[0])
        elif shp is not None and len(shp) == 2:
            n_frames = 1
        else:
            n_frames = len(np.asarray(stack_like))

    keyframe_every = max(1, int(keyframe_every))
    if int(n_workers) > 1 and keyframe_every == 1:
        return _segment_stack_threaded(stack_like, segment_frame_fn, n_frames, int(n_workers),
                                       progress_callback, dtype)
    out = None
    last_label = None
    done = 0
    for t, frame in iter_frames(stack_like):
        if keyframe_every > 1 and (t % keyframe_every != 0) and last_label is not None:
            lab = last_label
        else:
            lab = np.asarray(segment_frame_fn(frame, t)).astype(dtype)
            last_label = lab
        if out is None:
            H, W = lab.shape
            out = np.zeros((n_frames, H, W), dtype=dtype)
        if 0 <= t < out.shape[0]:
            out[t] = lab
        done += 1
        if progress_callback is not None:
            try:
                progress_callback(done, n_frames)
            except Exception:
                pass
    if out is None:
        out = np.zeros((max(1, n_frames), 1, 1), dtype=dtype)
    return out


def _segment_stack_threaded(stack_like, segment_frame_fn, n_frames, n_workers, progress_callback, dtype):
    """``segment_stack_per_frame`` with a bounded window of frames segmented concurrently (see ``n_workers``)."""
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor
    from pycat.file_io.stack_access import iter_frames

    out, done, pending = None, 0, deque()

    def _drain(block_until):
        nonlocal out, done
        while pending and (len(pending) > block_until or pending[0][1].done()):
            t, fut = pending.popleft()
            lab = np.asarray(fut.result()).astype(dtype)
            if out is None:
                out = np.zeros((n_frames,) + lab.shape, dtype=dtype)
            if 0 <= t < out.shape[0]:
                out[t] = lab
            done += 1
            if progress_callback is not None:
                try:
                    progress_callback(done, n_frames)
                except Exception:   # broad-ok: ui_cleanup -- a progress-bar failure must not stop segmentation
                    pass

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        for t, frame in iter_frames(stack_like):
            pending.append((t, ex.submit(segment_frame_fn, np.asarray(frame), t)))
            _drain(2 * n_workers)
        _drain(0)
    return out if out is not None else np.zeros((max(1, n_frames), 1, 1), dtype=dtype)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Per-frame region properties → a tidy detections table
# ─────────────────────────────────────────────────────────────────────────────

def stack_frame_properties(
    label_stack: np.ndarray,
    intensity_stack_like=None,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """Extract per-object properties for every frame of a labelled stack.

    Returns a tidy DataFrame with one row per (frame, object), carrying the
    geometric + intensity descriptors the linker and trajectory builders need:
        frame, object_id, y_um, x_um, y_px, x_px, area_um2, area_px,
        equiv_radius_um, eccentricity, solidity, mean_intensity,
        integrated_intensity.

    ``object_id`` is the per-frame region label (not a track id yet).
    """
    from skimage import measure
    from pycat.file_io.stack_access import iter_frames

    mpp = float(microns_per_pixel) if microns_per_pixel else 1.0
    px_area = mpp * mpp

    # Build an index of intensity frames if provided (streamed).
    intensity_frames: Dict[int, np.ndarray] = {}
    if intensity_stack_like is not None:
        for t, frame in iter_frames(intensity_stack_like):
            intensity_frames[int(t)] = frame

    rows: List[Dict[str, Any]] = []
    T = label_stack.shape[0]
    for t in range(T):
        lab = np.asarray(label_stack[t]).astype(np.int32)
        if lab.max() == 0:
            continue
        inten = intensity_frames.get(t)
        props = measure.regionprops(lab, intensity_image=inten)
        for p in props:
            cy, cx = p.centroid
            # skimage renamed mean_intensity → intensity_mean (0.26+); support both.
            if inten is not None:
                _mi = getattr(p, 'intensity_mean', None)
                if _mi is None:
                    _mi = p.mean_intensity
                mean_i = float(_mi)
            else:
                mean_i = np.nan
            rows.append({
                'frame': int(t),
                'object_id': int(p.label),
                'y_px': float(cy), 'x_px': float(cx),
                'y_um': float(cy) * mpp, 'x_um': float(cx) * mpp,
                'area_px': float(p.area),
                'area_um2': float(p.area) * px_area,
                'equiv_radius_um': float(np.sqrt(p.area * px_area / np.pi)),
                'eccentricity': float(getattr(p, 'eccentricity', np.nan)),
                'solidity': float(getattr(p, 'solidity', np.nan)),
                'mean_intensity': mean_i,
                'integrated_intensity': (mean_i * float(p.area)
                                         if inten is not None else np.nan),
            })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Fusion-aware condensate linking across frames
# ─────────────────────────────────────────────────────────────────────────────

def link_condensates(
    props_df: pd.DataFrame,
    max_displacement_um: Optional[float] = None,
    search_radius_scale: float = 2.0,
    max_gap_frames: int = 2,
    area_weight: float = 0.6,
    detect_fusion: bool = True,
    fusion_area_tol: float = 0.35,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Link per-frame condensate detections into temporal tracks (fusion-aware).

    Reuses the Bayesian/Hungarian linker (``link_trajectories_bayesian``) but
    tuned for large, slow, irregular objects:
      * ``max_displacement_um`` defaults to a size-scaled radius (a condensate
        moves at most a fraction of its own radius per frame), computed from the
        median object radius × ``search_radius_scale`` when not given;
      * ``area_weight`` is up-weighted (default 0.6 vs 0.3 for beads) so size
        consistency disambiguates neighbouring droplets;
      * velocity prediction is OFF (condensates are not ballistic; a predicted
        position from jitter would only cause mis-links).

    After the base per-object linking, a FUSION pass detects the droplet-fusion
    events that plain linking cannot represent: when two tracks that were both
    alive in frame t−1 both terminate at t, and a single track appears at t at
    their combined location with ~their combined area, that is recorded as a
    fusion (parent tracks → child track). Fusions are *flagged*, not merged
    away, because they are scientifically central for in vitro condensates.

    Returns
    -------
    (linked_df, fusion_df)
        linked_df : props_df + a ``track_id`` column.
        fusion_df : one row per detected fusion with columns
            frame, child_track_id, parent_track_ids (list), parent_areas_um2,
            child_area_um2.
    """
    from pycat.toolbox.dynamic_spatial_tools import link_trajectories_bayesian

    if props_df is None or len(props_df) == 0:
        return props_df, pd.DataFrame(
            columns=['frame', 'child_track_id', 'parent_track_ids',
                     'parent_areas_um2', 'child_area_um2'])

    df = props_df.copy()

    # Size-scaled default search radius.
    if max_displacement_um is None:
        med_r = float(np.nanmedian(df['equiv_radius_um'])) if 'equiv_radius_um' in df else 0.0
        if not np.isfinite(med_r) or med_r <= 0:
            med_r = 1.0
        max_displacement_um = max(0.5, med_r * float(search_radius_scale))

    linked = link_trajectories_bayesian(
        df,
        max_displacement_um=float(max_displacement_um),
        max_gap_frames=int(max_gap_frames),
        area_weight=float(area_weight),
        use_velocity=False,           # condensates are not ballistic
        progress_callback=progress_callback,
    )

    fusion_rows: List[Dict[str, Any]] = []
    if detect_fusion and 'track_id' in linked.columns:
        fusion_rows = _detect_fusions(linked, fusion_area_tol)

    fusion_df = pd.DataFrame(
        fusion_rows,
        columns=['frame', 'child_track_id', 'parent_track_ids',
                 'parent_areas_um2', 'child_area_um2'])
    return linked, fusion_df


def _detect_fusions(linked: pd.DataFrame, area_tol: float) -> List[Dict[str, Any]]:
    """Detect fusion events from a linked table.

    When two condensates fuse, the frame-to-frame Hungarian linker cannot
    represent a true many-to-one merge: it continues ONE of the parents through
    the fused region and terminates the other(s). So the observable signature of
    a fusion is:

      * a parent track P *ends* at frame t (present at t−1, absent from t on),
        AND
      * a surviving track S, near P's last position, *gains* approximately P's
        area between t−1 and t (S absorbed P).

    We record S as the fusion child and P as a parent. Multiple simultaneous
    absorptions accumulate into one event per (t, S).
    """
    fusions_by_key: Dict[tuple, Dict[str, Any]] = {}
    frames = sorted(linked['frame'].unique())
    by_frame = {t: linked[linked['frame'] == t] for t in frames}
    present = {t: set(by_frame[t]['track_id']) for t in frames}

    for i in range(1, len(frames)):
        t = frames[i]
        t_prev = frames[i - 1]
        curr = by_frame[t]
        prev = by_frame[t_prev]
        ended = present[t_prev] - present[t]          # tracks that ended at t
        if not ended:
            continue
        survivors = present[t] & present[t_prev]      # alive both frames
        if not survivors:
            continue

        prev_by_tid = {int(r['track_id']): r for _, r in prev.iterrows()}
        curr_by_tid = {int(r['track_id']): r for _, r in curr.iterrows()}

        for p_tid in ended:
            p_prev = prev_by_tid.get(int(p_tid))
            if p_prev is None:
                continue
            py, px = p_prev['y_um'], p_prev['x_um']
            p_area = float(p_prev.get('area_um2', np.nan))
            if not np.isfinite(p_area) or p_area <= 0:
                continue
            # Find the surviving track that (a) is nearest P's last position and
            # (b) grew by ~P's area between t−1 and t.
            best = None
            best_d = np.inf
            for s_tid in survivors:
                s_prev = prev_by_tid.get(int(s_tid))
                s_curr = curr_by_tid.get(int(s_tid))
                if s_prev is None or s_curr is None:
                    continue
                sy, sx = s_curr['y_um'], s_curr['x_um']
                d2 = (sy - py) ** 2 + (sx - px) ** 2
                sr = max(float(s_curr.get('equiv_radius_um', 1.0)), 0.5)
                if d2 > (4.0 * sr) ** 2:
                    continue
                gain = float(s_curr.get('area_um2', 0.0)) - float(s_prev.get('area_um2', 0.0))
                # gain should be within tol of the absorbed parent's area
                if p_area > 0 and abs(gain - p_area) / p_area <= area_tol and d2 < best_d:
                    best = int(s_tid); best_d = d2
            if best is not None:
                key = (int(t), best)
                if key not in fusions_by_key:
                    child_area = float(curr_by_tid[best].get('area_um2', np.nan))
                    fusions_by_key[key] = {
                        'frame': int(t),
                        'child_track_id': best,
                        'parent_track_ids': [best],
                        'parent_areas_um2': [float(prev_by_tid[best].get('area_um2', np.nan))],
                        'child_area_um2': child_area,
                    }
                fusions_by_key[key]['parent_track_ids'].append(int(p_tid))
                fusions_by_key[key]['parent_areas_um2'].append(p_area)

    return list(fusions_by_key.values())


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-condensate temporal object records
# ─────────────────────────────────────────────────────────────────────────────

def build_object_records(
    linked_df: pd.DataFrame,
    frame_interval_s: float = 1.0,
    fusion_df: Optional[pd.DataFrame] = None,
) -> Dict[int, Dict[str, Any]]:
    """Assemble per-track temporal object records from a linked table.

    Each record is the durable per-condensate object the specialised analyses
    (bubbling, catalysis, internal flow, fiber growth, contrast cascade) will
    attach their own time-series to. Structure per track_id:

        {
          'track_id', 'n_frames', 'frames' : [...],
          't_s'      : [...],   # time in seconds (frame × interval)
          'area_um2' : [...], 'equiv_radius_um' : [...],
          'y_um', 'x_um' : [...],  # centroid trajectory
          'mean_intensity', 'integrated_intensity' : [...],
          'eccentricity', 'solidity' : [...],
          'area_growth_rate_um2_per_s' : float,   # linear slope
          'fusion_child_of' : [parent ids] or [],
          'is_fusion_product' : bool,
        }
    """
    records: Dict[int, Dict[str, Any]] = {}
    if linked_df is None or 'track_id' not in linked_df.columns:
        return records

    dt = float(frame_interval_s) if frame_interval_s else 1.0
    fusion_map: Dict[int, List[int]] = {}
    if fusion_df is not None and len(fusion_df) > 0:
        for _, fr in fusion_df.iterrows():
            fusion_map[int(fr['child_track_id'])] = list(fr['parent_track_ids'])

    for tid, g in linked_df.groupby('track_id'):
        g = g.sort_values('frame')
        frames = g['frame'].to_numpy()
        t_s = frames.astype(float) * dt
        area = g['area_um2'].to_numpy(dtype=float)
        rec = {
            'track_id': int(tid),
            'n_frames': int(len(g)),
            'frames': [int(x) for x in frames],
            't_s': [float(x) for x in t_s],
            'area_um2': [float(x) for x in area],
            'equiv_radius_um': [float(x) for x in g['equiv_radius_um']],
            'y_um': [float(x) for x in g['y_um']],
            'x_um': [float(x) for x in g['x_um']],
            'mean_intensity': [float(x) for x in g['mean_intensity']],
            'integrated_intensity': [float(x) for x in g['integrated_intensity']],
            'eccentricity': [float(x) for x in g['eccentricity']],
            'solidity': [float(x) for x in g['solidity']],
            'fusion_child_of': fusion_map.get(int(tid), []),
            'is_fusion_product': int(tid) in fusion_map,
        }
        # Linear area-growth rate (coarsening / evaporation signal).
        if len(t_s) >= 2 and np.ptp(t_s) > 0:
            try:
                slope = float(np.polyfit(t_s, area, 1)[0])
            except Exception:
                slope = np.nan
        else:
            slope = np.nan
        rec['area_growth_rate_um2_per_s'] = slope
        records[int(tid)] = rec
    return records


def object_records_to_df(records: Dict[int, Dict[str, Any]]) -> pd.DataFrame:
    """Flatten object records to a per-track summary DataFrame (one row/track)."""
    rows = []
    for tid, r in records.items():
        area = np.asarray(r['area_um2'], dtype=float)
        inten = np.asarray(r['integrated_intensity'], dtype=float)
        rows.append({
            'track_id': tid,
            'n_frames': r['n_frames'],
            'first_frame': r['frames'][0] if r['frames'] else np.nan,
            'last_frame': r['frames'][-1] if r['frames'] else np.nan,
            'mean_area_um2': float(np.nanmean(area)) if area.size else np.nan,
            'start_area_um2': float(area[0]) if area.size else np.nan,
            'end_area_um2': float(area[-1]) if area.size else np.nan,
            'area_growth_rate_um2_per_s': r.get('area_growth_rate_um2_per_s', np.nan),
            'mean_integrated_intensity': (float(np.nanmean(inten))
                                          if inten.size else np.nan),
            'is_fusion_product': r.get('is_fusion_product', False),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Field-level temporal trajectories (aggregate, per frame)
# ─────────────────────────────────────────────────────────────────────────────

def field_trajectories(
    label_stack: np.ndarray,
    intensity_stack_like,
    microns_per_pixel: float = 1.0,
    frame_interval_s: float = 1.0,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    dark_reference=None,
) -> pd.DataFrame:
    """Whole-field summary per frame → a time-series table.

    Applies the validated 2D field-summary + partition functions
    (``invitro_tools``) to each frame, producing Φ(t), partition(t), C_sat(t),
    number-density(t), mean-radius(t). One row per frame.

    partition(t) is ``partition_coefficient_local`` -- each droplet against its OWN annular dilute phase, the
    estimator the 2D panel and batch use (Completion Spec A2). It used the global field estimator, whose
    low-percentile dilute phase is biased by the frame's noise -- so a frame-to-frame change in noise read as
    KINETICS. In vitro the camera pedestal cannot be separated from the dilute phase by any method, so without a
    ``dark_reference`` (scalar or image: buffer, no fluorophore, same settings) Kp(t) is NaN and the exact,
    pedestal-free ``contrast`` (I_dense − I_dilute) carries the time course; ``c_sat_proxy`` / ``c_dense_proxy``
    are the mean local dilute / dense intensities (raw, above ``dark_reference`` when given).
    """
    from pycat.toolbox.invitro_tools import field_summary
    from pycat.toolbox.invitro.partition import partition_coefficient_local
    from pycat.file_io.stack_access import iter_frames

    mpp = float(microns_per_pixel) if microns_per_pixel else 1.0
    dt = float(frame_interval_s) if frame_interval_s else 1.0

    # Index intensity frames (streamed, one at a time held).
    rows = []
    T = label_stack.shape[0]
    done = 0
    for t, frame in iter_frames(intensity_stack_like):
        if t >= T:
            continue
        lab = np.asarray(label_stack[t]).astype(np.int32)
        # RAW counts (1.7.002). This used to rescale every frame to its own [0, 1] -- the bug the 2D path fixed in
        # 1.5.424 (`field_summary` documents it: Kp swung 323 -> 22 with noise alone). In a TIME SERIES it is
        # worse: each frame gets its own unit, so the reaction's rise -- the thing being measured -- is divided
        # out of C_sat(t) and partition(t). The original dtype is kept so the saturation check knows the ceiling.
        fn = np.asarray(frame)
        try:
            summ = field_summary(lab, fn, mpp)
        except Exception:
            summ = {}
        try:
            part = partition_coefficient_local(fn, lab, sample_type='in_vitro', dark_reference=dark_reference,
                                               notify=False)
        except Exception as exc:     # broad-ok: batch_step — one frame's Kp failure is a NaN row, never the series
            from pycat.utils.general_utils import report_guarantee_failure
            report_guarantee_failure('field_trajectories: per-frame partition coefficient', exc)
            part = {}
        pdf = part.get('per_droplet_df')
        _dark = part.get('camera_floor', np.nan)
        _dark = float(_dark) if _dark is not None and np.isfinite(_dark) else 0.0
        has_rows = isinstance(pdf, pd.DataFrame) and len(pdf) > 0
        rows.append({
            'frame': int(t),
            't_s': float(t) * dt,
            'n_droplets': summ.get('n_droplets', np.nan),
            'volume_fraction': summ.get('volume_fraction', np.nan),
            'mean_radius_um': summ.get('mean_radius_um', np.nan),
            'number_density_per_um2': summ.get('number_density_per_um2', np.nan),
            'partition_coefficient': part.get('partition_coefficient', np.nan),
            'kp_is_true': bool(part.get('is_true_kp', False)),
            'contrast': part.get('contrast', np.nan),
            'c_sat_proxy': float(pdf['I_dilute_local'].mean()) - _dark if has_rows else np.nan,
            'c_dense_proxy': float(pdf['I_dense'].mean()) - _dark if has_rows else np.nan,
        })
        done += 1
        if progress_callback is not None:
            try:
                progress_callback(done, T)
            except Exception:
                pass
    return pd.DataFrame(rows).sort_values('frame').reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Relabel a per-frame label stack by track id (for a napari Labels overlay)
# ─────────────────────────────────────────────────────────────────────────────

@tags_layer('ts_track_relabel', role='labels', target='condensate', inputs=('labels',),
            summary='Time-series tracked-droplet relabelling (per-frame labels -> track ids)')
def relabel_stack_by_track(
    label_stack: np.ndarray,
    linked_df: pd.DataFrame,
) -> np.ndarray:
    """Return a (T, H, W) label stack whose labels are TRACK ids.

    Each per-frame region (object_id) is recoloured to its track_id so a single
    condensate keeps one colour through the movie — the visual counterpart of
    the linking, and the basis for plot↔layer brushing later.
    """
    out = np.zeros_like(label_stack, dtype=np.int32)
    if linked_df is None or 'track_id' not in linked_df.columns:
        return label_stack.astype(np.int32)
    for t, g in linked_df.groupby('frame'):
        lab = np.asarray(label_stack[t]).astype(np.int32)
        remap = np.zeros(int(lab.max()) + 1, dtype=np.int32)
        for _, row in g.iterrows():
            oid = int(row['object_id'])
            if 0 <= oid < remap.shape[0]:
                remap[oid] = int(row['track_id']) + 1  # +1: keep 0 = background
        out[t] = remap[lab]
    return out
