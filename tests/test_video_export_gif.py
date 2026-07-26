"""**The GIF export path (manuscript_toolbox — the one genuinely-new video piece).**

`export_stack_as_gif` mirrors `export_stack_as_mp4`: same LUT + contrast handling (now shared via
`_sampled_contrast_limits` / `_lut_rgb`), writing a small, universally-viewable animated GIF for a preview
loop. These pin that a (T, H, W) stack round-trips to a (T, H, W, 3) GIF, that the contrast/LUT helpers behave,
and — because the MP4 body was refactored onto the same helpers — that the MP4 path still writes a real file.
"""
import numpy as np
import pytest

from pycat.toolbox.video_export_tools import (
    export_stack_as_gif, export_stack_as_mp4, _sampled_contrast_limits, _lut_rgb)

pytestmark = pytest.mark.base      # video_export_tools imports napari/PyQt5 at module scope → the fuller lane


def _ramp_stack(t=5, h=12, w=10):
    """A (T, H, W) stack whose intensity ramps over time, so contrast normalisation has something to do."""
    stack = np.zeros((t, h, w), dtype=np.float32)
    for i in range(t):
        stack[i] = float(i)
    return stack


def test_export_stack_as_gif_roundtrips_to_a_TxHxWx3_animation(tmp_path):
    import imageio.v3 as iio
    stack = _ramp_stack(t=5, h=12, w=10)
    out = export_stack_as_gif(stack, tmp_path / "clip.gif", colormap="viridis", fps=10)

    assert out.exists() and out.suffix == ".gif" and out.stat().st_size > 0
    frames = np.asarray(iio.imread(str(out), index=None))
    assert frames.shape == (5, 12, 10, 3)          # every frame preserved, RGB
    assert frames.dtype == np.uint8


def test_export_stack_as_gif_reports_progress_for_every_frame(tmp_path):
    stack = _ramp_stack(t=4)
    seen = []
    export_stack_as_gif(stack, tmp_path / "p.gif", progress_callback=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]     # monotonic, one per frame, total constant


def test_sampled_contrast_limits_guards_a_flat_stack():
    flat = np.full((3, 4, 4), 7.0, dtype=np.float32)
    lo, hi = _sampled_contrast_limits(flat, None)
    assert lo == 7.0 and hi > lo                    # never divides by zero
    # an explicit range is honoured
    assert _sampled_contrast_limits(flat, (2.0, 9.0)) == (2.0, 9.0)


def test_lut_rgb_maps_min_and_max_across_the_colormap():
    from pycat.toolbox.video_export_tools import _get_cmap
    cmap = _get_cmap("viridis")
    frame = np.array([[0.0, 10.0]], dtype=np.float32)
    rgb = _lut_rgb(frame, cmap, 0.0, 10.0)
    assert rgb.shape == (1, 2, 3) and rgb.dtype == np.uint8
    assert not np.array_equal(rgb[0, 0], rgb[0, 1])   # min and max land on different colours


def test_export_stack_as_mp4_still_writes_after_the_shared_helper_refactor(tmp_path):
    """The MP4 body was refactored onto the shared helpers; prove it still produces a file (pyav-gated)."""
    import imageio.v3 as iio
    try:
        with iio.imopen(str(tmp_path / "_probe.mp4"), "w", plugin="pyav") as w:
            w.init_video_stream("libx264", fps=5)
            w.write_frame(np.zeros((8, 8, 3), np.uint8))
    except Exception:
        pytest.skip("pyav/libx264 not available in this environment")

    out = export_stack_as_mp4(_ramp_stack(t=4), tmp_path / "clip.mp4", fps=5)
    assert out.exists() and out.suffix == ".mp4" and out.stat().st_size > 0
