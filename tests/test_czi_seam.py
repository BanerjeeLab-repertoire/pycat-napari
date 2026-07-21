"""**The CZI mosaic seam, as a measurement — the regression test carried across three audits.**

The reported CZI defect is a vertical column discontinuity from a mis-assembled mosaic tile. It was
"architecturally improved but not validated against the reported defect" — because there was no *number*.
This is that number: a per-boundary seam z-score, and the many-frame persistence test that separates a
real seam (anomalous at a fixed column on every frame) from ordinary image structure (which moves).

Assertions 1–2 + 4 run in CI against synthetic mosaics (the metric detects the defect class and does not
cry wolf). Assertion 3 — the real CZI path is seam-free — runs against the real file only when
``PYCAT_CZI_SEAM_FILE`` points at it (the large file cannot live in the repo). **This measures the seam;
it does not fix it — a persistent seam here is a finding for a separate fix spec, now with a number.**
"""
import os

import numpy as np
import pytest

from pycat.file_io.czi_seam import (
    column_seam_score, persistent_seam_columns,
    sample_frame_indices, seam_qc_message, seam_qc_from_lazy_stack)

pytestmark = pytest.mark.core

_SEAM_X = 64
_H, _W = 64, 128


def _clean_frame(seed):
    """A natural-looking frame: low-frequency structure + noise, NO tile seam."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:_H, 0:_W]
    base = 50 + 20 * np.sin(xx / 25.0) + 10 * np.cos(yy / 18.0)
    return base + rng.normal(0, 3, (_H, _W))


def _offset_frame(seed):
    """The same, but with a mosaic tile assembled with a brightness discontinuity at ``_SEAM_X`` — a seam."""
    f = _clean_frame(seed).copy()
    f[:, _SEAM_X:] += 25
    return f


def test_a_clean_mosaic_has_no_seam_the_metric_does_not_cry_wolf():
    """Assertion 1: ordinary structure must not read as a seam — otherwise the metric is useless."""
    frames = [_clean_frame(s) for s in range(8)]
    assert persistent_seam_columns(frames) == []
    assert max(column_seam_score(frames[0], x) for x in range(1, _W)) < 5.0


def test_an_injected_offset_scores_high_at_exactly_that_boundary():
    """Assertion 2: the metric detects the defect class, and localizes it to the injected column."""
    of = _offset_frame(0)
    assert column_seam_score(of, _SEAM_X) > 10.0                       # a clear seam
    others = [column_seam_score(of, x) for x in range(1, _W) if x != _SEAM_X]
    assert max(others) < 5.0                                           # and only there


def test_the_seam_is_PERSISTENT_across_frames_structure_is_not():
    """Assertion 4: the many-frame test is what separates a seam (fixed column, every frame) from image
    content (a one-frame spike). The injected seam persists; nothing in the clean set does."""
    assert persistent_seam_columns([_offset_frame(s) for s in range(8)]) == [_SEAM_X]
    assert persistent_seam_columns([_clean_frame(s) for s in range(8)]) == []


def test_the_score_is_normalized_against_neighbours_not_absolute():
    """A globally bright/contrasty frame (large absolute steps everywhere) must not score as a seam — the
    metric is a z-score against neighbouring boundaries, so uniform contrast cancels."""
    high_contrast = _clean_frame(0) * 20.0                            # 20x the pixel steps, no new seam
    assert max(column_seam_score(high_contrast, x) for x in range(1, _W)) < 5.0


@pytest.mark.skipif(not os.environ.get('PYCAT_CZI_SEAM_FILE'),
                    reason="set PYCAT_CZI_SEAM_FILE to the real streaming CZI to run the actual regression")
def test_the_real_czi_path_is_seam_free():
    """Assertion 3 (opt-in): read frames through PyCAT's real CZI path and assert no column is a seam on a
    majority of them. THIS is what closes (or reopens, with a number) the reported defect."""
    path = os.environ['PYCAT_CZI_SEAM_FILE']
    pytest.importorskip("jpype1")
    from pycat.file_io.readers.czi_bioformats import open_czi_streaming  # reader entry point
    reader = open_czi_streaming(path)
    n = getattr(reader, 'shape', (8,))[0]
    idx = list(range(0, min(int(n), 24), max(1, int(n) // 8)))         # a spread of frames
    frames = [np.asarray(reader[i]) for i in idx]
    frames = [f.reshape(-1, f.shape[-1]) if f.ndim > 2 else f for f in frames]
    seams = persistent_seam_columns(frames)
    assert not seams, (
        f"the real CZI path has persistent seam column(s) {seams} — the reported defect is PRESENT. "
        "This is a finding for a fix spec; the number is the evidence.")


# ── Load-time QC wiring (wire_orphans B2): sample a handful of frames, warn on a persistent seam ──
def test_sample_frame_indices_is_a_handful_never_the_whole_movie():
    assert sample_frame_indices(3) == [0, 1, 2]                 # fewer than the cap → all
    got = sample_frame_indices(10000, max_frames=5)
    assert got[0] == 0 and got[-1] == 9999 and len(got) == 5    # first, last, evenly spaced, a handful
    assert sample_frame_indices(0) == []


def test_seam_qc_message_is_None_for_clean_and_a_warning_for_a_seam():
    assert seam_qc_message([_clean_frame(s) for s in range(6)]) is None
    msg = seam_qc_message([_offset_frame(s) for s in range(6)])
    assert msg is not None and 'seam' in msg.lower() and str(_SEAM_X) in msg


def test_seam_qc_from_lazy_stack_samples_by_frame_and_flags_a_persistent_seam():
    seam_stack = np.stack([_offset_frame(s) for s in range(8)])   # (T,H,W) with a persistent seam
    clean_stack = np.stack([_clean_frame(s) for s in range(8)])
    assert seam_qc_from_lazy_stack(clean_stack) is None
    msg = seam_qc_from_lazy_stack(seam_stack)
    assert msg is not None and str(_SEAM_X) in msg


def test_seam_qc_from_lazy_stack_degrades_safely_and_needs_multiple_frames():
    assert seam_qc_from_lazy_stack(np.zeros((64, 128))) is None          # 2-D, not a stack → None
    assert seam_qc_from_lazy_stack(np.stack([_offset_frame(0)])) is None  # a single frame is not a seam
    assert seam_qc_from_lazy_stack(object()) is None                     # no .shape → best-effort None


def test_the_streaming_czi_loader_wires_the_seam_qc():
    """AST: the streaming CZI open path (Qt/IO-bound, not run in core) must call seam_qc_from_lazy_stack —
    else the orphan is unwired again."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat' / 'file_io'
           / 'stack_openers.py').read_text(encoding='utf-8')
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == '_open_czi_streaming')
    called = any(
        isinstance(c, ast.Call) and
        (getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)) == 'warn_seam_qc'
        for c in ast.walk(fn))
    assert called, "the streaming CZI loader no longer runs the mosaic-seam QC"
