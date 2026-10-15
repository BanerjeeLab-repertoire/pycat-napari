"""The real-data pipeline harness (Completion Spec Phase 0.3): 14 pipelines x 4 routes, every cell a verdict.

For each pipeline in ``PIPELINE_DEFS`` this holds the corpus file it runs on (``tests/data/registry.txt``), the
step config the GUI records for a typical run, the table the run must produce (with its required columns), and
-- where one exists -- a headless driver that computes the same table by calling the toolbox directly.

Routes (``tests/route_equivalence.ROUTE_ORDER``):

* ``batch``    -- the recorded config through ``BatchWorker._process_file`` on the corpus FILE (the real loader);
* ``headless`` -- the toolbox called directly on the same pixels; must equal the batch table;
* ``session``  -- the batch table written with the real session writer and restored; must equal it;
* ``kernel``   -- ``OperationService``. No pipeline runs through it (Completion Spec C1), so this route is
  ``not-implemented`` for every pipeline, said once with the reason rather than left blank.

Verdicts: ``pass`` / ``fail`` / ``not-implemented`` / ``not-applicable`` / ``no-data`` (no corpus file -- the
registry says why). Never blank. ``tools/route_matrix.py`` runs this and writes ``docs/pipeline_route_matrix.md``;
``tests/test_pipelines_on_real_data.py`` asserts it. Golden masters are pinned in ``tests/data/golden_tables.json``
(``tools/route_matrix.py --repin`` rewrites them -- deliberately, with a CHANGELOG line saying why).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

import numpy as np

GOLDEN = Path(__file__).with_name('data') / 'golden_tables.json'
ROUTES = ('headless', 'batch', 'session', 'kernel')
KERNEL_REASON = ('no pipeline runs through OperationService (27 op-level kernels, 0 production callers -- '
                 'Completion Spec C1 decision pending)')
SIG_DIGITS = 6      # golden-master rounding: catches a real change, tolerates last-bit BLAS/torch noise


@dataclass
class Verdict:
    verdict: str
    reason: str = ''
    seconds: float = float('nan')
    digest: str = ''


@dataclass
class PipelineCase:
    pipeline: str
    corpus: Optional[str]                      # corpus filename, or None when the registry has no file
    steps: list = field(default_factory=list)
    table: Optional[str] = None                # output suffix of the primary table, e.g. 'puncta_df'
    columns: tuple = ()                        # columns the table must have
    headless: Optional[Callable] = None        # (path) -> DataFrame, the toolbox called directly
    not_applicable: str = ''                   # a reason when linking/route makes no sense
    no_data: str = ''
    # headless vs batch tolerance. Exact by default; a looser one is a FINDING, stated with its reason in the matrix.
    headless_rtol: float = 1e-12
    headless_rtol_reason: str = ''


def _cond_steps(seg='Segmentation Image', fluor='Fluorescence Image', extra=()):
    return [{'step': 'open_image', 'params': {'ball_radius': 5, 'cell_diameter': 150}},
            {'step': 'preprocessing', 'params': {'active_layer': seg}},
            {'step': 'background_removal', 'params': {}},
            {'step': 'cellpose_segmentation', 'params': {'method': 'cellpose', 'image_layer': seg}},
            {'step': 'cell_analysis', 'params': {'image_layer': fluor}},
            {'step': 'condensate_segmentation', 'params': {
                'seg_image_layer': f'Background Removed Pre-Processed {fluor}', 'measure_image_layer': fluor}},
            {'step': 'condensate_analysis', 'params': {'image_layer': fluor}}, *extra]


def _fibril_chain(stem):
    p = 'Peak & Edge Enhanced Bilateral Filtered ' + stem
    return [{'step': 'open_image', 'params': {'ball_radius': 5, 'cell_diameter': 100}},
            {'step': 'bilateral_filter', 'params': {'layer': stem, 'radius': 2}},
            {'step': 'peak_edge_enhancement', 'params': {'layer': 'Bilateral Filtered ' + stem, 'ball_radius': 5}},
            {'step': 'morphological_gaussian', 'params': {'layer': p, 'filter_size': 1}},
            {'step': 'local_thresholding', 'params': {'layer': 'Filtered ' + p, 'method': 'Sauvola',
                                                      'k_value': 0.0, 'window_size': 25}},
            {'step': 'label_binary_mask', 'params': {'mask_layer': 'Locally Thresholded Filtered ' + p}},
            {'step': 'measure_binary_mask', 'params': {'mask_layer': 'Labeled Locally Thresholded Filtered ' + p,
                                                       'image_layer': stem}}]


# ── headless drivers: the toolbox called directly on the corpus pixels ──────────────────────────────────────

def _headless_timeseries(path):
    import tifffile

    from pycat.toolbox.segmentation.cellpose import cellpose_segmentation
    from pycat.toolbox.timeseries.cell_pipeline import (
        analyze_cell_timeseries,
        result_tables,
        time_average,
    )
    from pycat.utils.pixel_size import pixel_size_um_or_default
    stack = tifffile.imread(path)
    avg = time_average(stack)
    lo, hi = np.percentile(avg, (0.5, 99.9))
    cells = np.asarray(cellpose_segmentation(np.clip((avg - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32),
                                             250)).astype(np.int32)
    mpx = _file_pixel_um(path)
    res = analyze_cell_timeseries(stack, cells, 5, 125, frame_interval_s=20.0,
                                  microns_per_pixel=pixel_size_um_or_default({'microns_per_pixel_sq': mpx ** 2}
                                                                             if mpx else {}, context='harness'),
                                  contrast_floor_on=True, min_track_frames=1, n_workers=1)
    return result_tables(res, cells, str(path))['cells']


def _headless_invitro_bf(path):
    import tifffile

    from pycat.toolbox.brightfield_pipeline import ivbf_field_summary
    from pycat.toolbox.brightfield_tools import (
        preprocess_brightfield,
        segment_bf_condensates,
    )
    raw = tifffile.imread(path).astype(np.float64)
    enhanced = preprocess_brightfield(raw, bg_kernel=60, halo_weight=0.3, background_image=None)['enhanced']
    mask = segment_bf_condensates(enhanced, min_diameter_px=4.0, max_diameter_px=200.0, min_circularity=0.5,
                                  method='intensity', texture_window=9, split_touching=True)
    return ivbf_field_summary(raw, mask, 1.0, str(path))[2]


def _file_pixel_um(path):
    try:
        from tools.build_corpus import _pixel_um_from_tags
        return _pixel_um_from_tags(Path(path))
    except Exception:  # broad-ok: optional_probe — no tags means no pixel size, the pipeline's own default applies
        return None


CASES = [
    PipelineCase('condensate', 'condensate_incell1_gfp.tif', _cond_steps(), 'puncta_df',
                 ('label', 'area', 'intensity_mean', 'cell label')),
    PipelineCase('timeseries', 'timeseries_dissolution.tif',
                 [{'step': 'open_stack', 'params': {'ball_radius': 5, 'cell_diameter': 250}},
                  {'step': 'ts_cells_time_average', 'params': {'cell_diameter': 250}},
                  {'step': 'cell_timeseries', 'params': {'ball_radius': 5, 'window_size': 125,
                                                         'frame_interval_s': 20.0, 'contrast_floor_on': True,
                                                         'min_track_frames': 1, 'n_workers': 1}}],
                 'ts_cells_df', ('cell', 'expression', 'responded', 't_half_s'), headless=_headless_timeseries),
    PipelineCase('coloc', 'coloc_im2_cyx.tif', [
        {'step': 'open_image', 'params': {'ball_radius': 5, 'cell_diameter': 150, 'channel_assignment': [
            {'channel_num': 2, 'layer_name': 'DAPI Segmentation Image'},
            {'channel_num': 0, 'layer_name': 'Ch1 Fluorescence Image'},
            {'channel_num': 1, 'layer_name': 'Ch2'}]}},
        {'step': 'preprocessing', 'params': {'active_layer': 'Ch1 Fluorescence Image'}},
        {'step': 'background_removal', 'params': {}},
        *_cond_steps('DAPI Segmentation Image', 'Ch1 Fluorescence Image')[1:],
        {'step': 'pixel_colocalization', 'params': {'image_layer1': 'Ch1 Fluorescence Image', 'image_layer2': 'Ch2'}},
        {'step': 'two_channel_condensate_coloc', 'params': {}}], 'colocalization',
        ('pearson_r', 'manders_overlap', 'manders_k1', 'manders_k2')),
    PipelineCase('cellular_bf', None),
    PipelineCase('invitro_fluor', 'invitro_fluor_rg5_t40_image1.tif', [
        {'step': 'open_image', 'params': {'ball_radius': 15, 'cell_diameter': 100}},
        {'step': 'ivf_preprocess', 'params': {'method': 'rolling-ball', 'ball_radius': 15, 'sigma': 1.5}},
        {'step': 'ivf_segmentation', 'params': {'method': 'otsu', 'min_area': 6}},
        {'step': 'ivf_field_summary', 'params': {}},
        {'step': 'ivf_size_distribution', 'params': {'n_bins': 30}},
        {'step': 'ivf_spatial_metrology', 'params': {}}], 'droplet_df',
        ('droplet_label', 'partition_coefficient', 'area_um2')),
    PipelineCase('timeseries_invitro_fluor', None),
    PipelineCase('invitro_bf', 'invitro_bf_tirth_c0.tif', [
        {'step': 'open_image', 'params': {'ball_radius': 15, 'cell_diameter': 100}},
        {'step': 'ivbf_preprocess', 'params': {'bg_kernel': 60, 'halo_weight': 0.3}},
        {'step': 'ivbf_segmentation', 'params': {'method': 'intensity', 'min_d': 4.0, 'max_d': 200.0}},
        {'step': 'ivbf_field_summary', 'params': {}},
        {'step': 'ivbf_spatial_metrology', 'params': {}},
        {'step': 'ivbf_size_distribution', 'params': {}}], 'ivbf_droplet_df', ('mean_od',),
        headless=_headless_invitro_bf, headless_rtol=1e-5,
        headless_rtol_reason='FINDING: batch loads every image as dtype-max-scaled float32 (the [0, 1] loader '
                             'contract); OD is a ratio, so it agrees, but only to float32 precision (6e-7)'),
    PipelineCase('fibril', 'fibril_fibrils.tif', _fibril_chain('fibril_fibrils'), 'binary_mask_stats',
                 ('Area', 'Intensity_Mean')),
    PipelineCase('zstack', None),
    PipelineCase('vpt', 'vpt_rg5_t40.tif', [
        {'step': 'open_image', 'params': {}}, {'step': 'vpt_segment_host', 'params': {}},
        {'step': 'vpt_detect_beads', 'params': {}}, {'step': 'vpt_link_trajectories', 'params': {}},
        {'step': 'vpt_microrheology', 'params': {}}], 'vpt_tracks', ('track_id',)),
    PipelineCase('frap', 'frap_ctrap_rec.tif', [
        {'step': 'open_image', 'params': {}}, {'step': 'frap_define_roi', 'params': {}},
        {'step': 'frap_analysis', 'params': {}}], 'frap_curve', ('time_s',),
        not_applicable='rows are time points, not objects'),
    PipelineCase('fusion', 'fusion_marker4.h5', [
        {'step': 'fusion_build_signal', 'params': {}},     # the panel loads the .h5 itself; no open step is recorded
        {'step': 'fusion_fit', 'params': {}}], 'fusion_fit', ('tau_s',),
        not_applicable='rows are a fit and time points, not objects'),
    PipelineCase('temperature', 'temperature_polya_substack.tif', [
        {'step': 'open_image', 'params': {}}, {'step': 'temperature_sync', 'params': {}},
        {'step': 'temperature_turbidity', 'params': {}}], 'temperature_turbidity', ('temperature_C',),
        not_applicable='rows are temperatures, not objects'),
    PipelineCase('fd_curve', 'fd_marker3_lf.h5', [
        {'step': 'fd_load', 'params': {}}, {'step': 'fd_segment', 'params': {}},
        {'step': 'fd_rips', 'params': {}}], 'fd_rips', ('force_pN',),
        not_applicable='rows are force-distance samples, not objects'),
]


# ── running ─────────────────────────────────────────────────────────────────────────────────────────────────

def _canonical(df):
    """The table as stable CSV text: identity columns dropped, floats to SIG_DIGITS significant digits."""
    import pandas as pd
    keep = [c for c in df.columns if not str(c).startswith('_pycat_')]
    out = df[keep].copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].map(lambda v: f'{v:.{SIG_DIGITS}g}' if v == v else 'nan')
    return out.to_csv(index=False, lineterminator='\n')


def digest(df) -> str:
    return hashlib.sha256(_canonical(df).encode('utf-8')).hexdigest()[:16]


def _tables_equal(a, b, rtol=1e-12):
    """Same columns, same rows; numbers equal to ``rtol`` (default: the session CSV round-trip's 1 ULP)."""
    import pandas as pd
    a = a[[c for c in a.columns if not str(c).startswith('_pycat_')]].reset_index(drop=True)
    b = b[[c for c in b.columns if not str(c).startswith('_pycat_')]].reset_index(drop=True)
    if list(a.columns) != list(b.columns) or len(a) != len(b):
        return False, f'shape/columns differ: {a.shape} {list(a.columns)[:6]} vs {b.shape} {list(b.columns)[:6]}'
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            xa, ya = x.to_numpy(float), y.to_numpy(float)
            if not np.allclose(xa, ya, rtol=rtol, atol=0, equal_nan=True):
                rel = np.nanmax(np.abs(xa - ya) / np.maximum(np.abs(xa), 1e-300))
                return False, f'column {c!r} differs (max relative {rel:.2g})'
        elif not (x.astype(str) == y.astype(str)).all():
            return False, f'column {c!r} differs'
    return True, ''


def run_batch(case: PipelineCase, path: Path, out: Path):
    """(step results, primary table or None, seconds)."""
    import pandas as pd

    from pycat.batch_processor import BatchWorker
    from pycat.batch_step_registry import _STEP_MAP
    t0 = time.perf_counter()
    worker = SimpleNamespace(config={'steps': case.steps}, step_registry=_STEP_MAP, _auto_ball_radius=False)
    results = BatchWorker._process_file(worker, path, out)
    seconds = time.perf_counter() - t0
    csv = out / f'{path.stem}_{case.table}.csv'
    return results, (pd.read_csv(csv) if csv.is_file() else None), seconds


def evaluate(case: PipelineCase, golden: dict | None = None) -> dict:
    """{route: Verdict} for one pipeline."""
    from tests.data.fetch import CorpusUnavailable, fetch
    verdicts = {'kernel': Verdict('not-implemented', KERNEL_REASON)}
    if case.corpus is None:
        from tests.data.fetch import for_pipeline
        why = next((e.provenance.get('transform', '') for e in for_pipeline(case.pipeline)), 'no corpus entry')
        for r in ('headless', 'batch', 'session'):
            verdicts[r] = Verdict('no-data', why)
        return verdicts
    try:
        path = fetch(case.corpus)
    except CorpusUnavailable as exc:
        for r in ('headless', 'batch', 'session'):
            verdicts[r] = Verdict('no-data', str(exc))
        return verdicts
    with tempfile.TemporaryDirectory() as tmp:
        try:
            results, table, seconds = run_batch(case, path, Path(tmp))
        except Exception as exc:  # broad-ok: batch_step — a harness cell records the failure; it must not stop the matrix
            verdicts.update(batch=Verdict('fail', f'{type(exc).__name__}: {exc}'),
                            headless=Verdict('not-implemented', 'batch failed; nothing to compare'),
                            session=Verdict('not-implemented', 'batch failed; nothing to compare'))
            return verdicts
    errors = [f'{n}: {r.error}' for n, r in results if r.status == 'error']
    skipped = [n for n, r in results if r.status == 'skipped']
    if errors:
        verdicts['batch'] = Verdict('fail', '; '.join(errors), seconds)
    elif table is None or not len(table):
        verdicts['batch'] = Verdict('not-implemented' if skipped else 'fail',
                                    (f'not replayed: {", ".join(skipped)}' if skipped else '')
                                    + f' -> no {case.table} table', seconds)
    else:
        missing = [c for c in case.columns if c not in table.columns]
        d = digest(table)
        pinned = (golden or {}).get(case.pipeline)
        if missing:
            verdicts['batch'] = Verdict('fail', f'table lacks {missing}', seconds, d)
        elif pinned and pinned != d:
            verdicts['batch'] = Verdict('fail', f'golden master changed ({pinned} -> {d})', seconds, d)
        else:
            note = f'{len(table)} rows' + (f'; not replayed: {", ".join(skipped)}' if skipped else '')
            verdicts['batch'] = Verdict('pass', note + ('' if pinned else '; NOT PINNED'), seconds, d)
    if table is None or verdicts['batch'].verdict == 'fail':
        verdicts.setdefault('headless', Verdict('not-implemented', 'no batch table to compare'))
        verdicts.setdefault('session', Verdict('not-implemented', 'no batch table to compare'))
        if case.headless is None:
            verdicts['headless'] = Verdict('not-implemented', 'no headless driver for this pipeline yet')
        return verdicts
    verdicts['session'] = _session_route(case, table)
    verdicts['headless'] = _headless_route(case, path, table)
    return verdicts


def _session_route(case, table):
    from tests.route_equivalence import session_roundtrip_dataframe
    t0 = time.perf_counter()
    try:
        back = session_roundtrip_dataframe(table, f'{case.pipeline}_{case.table}')
    except Exception as exc:  # broad-ok: batch_step — the cell records the failure
        return Verdict('fail', f'{type(exc).__name__}: {exc}')
    if back is None:
        return Verdict('fail', 'the session did not restore the table', time.perf_counter() - t0)
    ok, why = _tables_equal(table, back)
    return Verdict('pass' if ok else 'fail', why or 'round-trip identical', time.perf_counter() - t0)


def _headless_route(case, path, table):
    if case.headless is None:
        return Verdict('not-implemented', 'no headless driver for this pipeline yet')
    t0 = time.perf_counter()
    try:
        direct = case.headless(path)
    except Exception as exc:  # broad-ok: batch_step — the cell records the failure
        return Verdict('fail', f'{type(exc).__name__}: {exc}')
    ok, why = _tables_equal(direct, table, case.headless_rtol)
    note = 'equal to batch' + (f' to rtol {case.headless_rtol:g}: {case.headless_rtol_reason}'
                               if case.headless_rtol_reason else '')
    return Verdict('pass' if ok else 'fail', why or note, time.perf_counter() - t0)


def load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding='utf-8')) if GOLDEN.exists() else {}


def run_matrix() -> dict:
    golden = load_golden()
    return {case.pipeline: evaluate(case, golden) for case in CASES}
