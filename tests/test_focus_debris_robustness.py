"""**"The best frame" must be the sample, not the sharpest speck of dust.**

Every whole-frame focus / quality metric in PyCAT scores sharpness by aggregating a per-pixel
gradient or variance with a plain ``mean`` / ``var``. That aggregate is dominated by its largest
values — so a **bright speck of debris on a different focal plane** (dust has its own focus curve)
can, at *its* focus frame, contribute a handful of extreme-gradient pixels that outscore a genuinely
in-focus but spatially-extended sample. The argmax lands on the junk frame, and nothing says so. This
is documented in ``bf_focus_metric``'s own docstring, with a synthetic z-sweep.

There is **no mask available** at the callers (the focus-QC panels have only an image dropdown, and
QC usually runs before segmentation exists), so the fix cannot be "score inside the object". It is a
maskless robustification: ``math_utils.robust_focus_energy`` drops the top ~1% of per-pixel
contributions before averaging — which removes a small speck's dominance while barely touching an
extended object, and does **not** move the chosen frame on clean data.

These tests drive the **real** metric functions over a synthetic z-sweep whose true focus frames are
known, and assert:

* on a **debris** sweep, the recovered best frame is the SAMPLE (the fix), and a plain-mean baseline
  would have picked the DEBRIS (the mechanism is really present);
* on a **clean** sweep, the recovered best frame is unchanged (no regression).
"""

# Third party imports
import numpy as np
import pytest
from scipy import ndimage


pytestmark = pytest.mark.core

_N, _H, _W = 20, 128, 128
_SAMPLE_FOCUS = 14
_DEBRIS_FOCUS = 4


def _sharpness(z, focus):
    """A simple focus curve — sharpest at ``focus``, falling off with |z − focus|."""
    return 1.0 / (1.0 + abs(z - focus))


def _z_sweep(with_debris, seed=0):
    """A z-stack: an extended textured SAMPLE focusing at frame 14, optionally a tiny bright
    DEBRIS speck (on a different plane) focusing at frame 4."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:_H, 0:_W]
    sample_region = (yy - 64) ** 2 + (xx - 64) ** 2 < 26 ** 2       # ~2100 px, extended
    texture = rng.normal(0, 1, (_H, _W)) * sample_region
    speck_region = (yy - 20) ** 2 + (xx - 20) ** 2 < 2 ** 2         # ~13 px, tiny

    stack = np.empty((_N, _H, _W), dtype=np.float32)
    for z in range(_N):
        frame = np.full((_H, _W), 0.3, dtype=np.float32)
        s = _sharpness(z, _SAMPLE_FOCUS)
        frame += ndimage.gaussian_filter(texture, sigma=(1 - s) * 6 + 0.3) * 0.10
        if with_debris:
            speck = np.zeros((_H, _W), dtype=np.float32)
            speck[speck_region] = 6.0
            frame += ndimage.gaussian_filter(speck, sigma=(1 - _sharpness(z, _DEBRIS_FOCUS)) * 4 + 0.2)
        stack[z] = frame
    return stack


# ── the helper itself ─────────────────────────────────────────────────────────────────────

def test_robust_focus_energy_trims_the_extreme_pixels():
    from pycat.utils.math_utils import robust_focus_energy

    # 990 modest values + 10 enormous ones. A plain mean is dragged up by the 10; trimming the top
    # 1% (10 of 1000) removes them and returns the bulk mean.
    values = np.concatenate([np.full(990, 1.0), np.full(10, 1000.0)])
    plain = float(values.mean())
    robust = robust_focus_energy(values, trim_fraction=0.01)

    assert plain > 10, "the baseline must actually be inflated by the outliers"
    assert robust == pytest.approx(1.0), f"the outliers were not trimmed: {robust}"


def test_robust_focus_energy_trim_zero_is_the_plain_mean():
    """The explicit opt-out reproduces the original behaviour exactly."""
    from pycat.utils.math_utils import robust_focus_energy
    values = np.array([1.0, 2.0, 3.0, 100.0])
    assert robust_focus_energy(values, trim_fraction=0.0) == pytest.approx(values.mean())


def test_robust_focus_energy_handles_empty_and_tiny():
    from pycat.utils.math_utils import robust_focus_energy
    assert robust_focus_energy([]) == 0.0
    # Too few values for the trim count to reach 1 → falls back to the plain mean, no crash.
    assert robust_focus_energy([5.0, 7.0]) == pytest.approx(6.0)


# ── bf_focus_metric (the demonstrated case) ─────────────────────────────────────────────────

def test_bf_focus_metric_picks_the_SAMPLE_over_the_debris():
    """The real ``bf_focus_metric``, maskless, across the debris sweep."""
    from pycat.toolbox.brightfield_tools import bf_focus_metric

    stack = _z_sweep(with_debris=True)
    best = int(np.argmax([bf_focus_metric(f) for f in stack]))
    assert best == _SAMPLE_FOCUS, (
        f"maskless focus picked frame {best}; the sample focuses at {_SAMPLE_FOCUS} and the debris "
        f"at {_DEBRIS_FOCUS} — the speck won")


def test_the_MECHANISM_is_real_a_plain_mean_would_pick_the_debris():
    """The negative control: without the robustification, the debris wins — so the test above is
    proving a real fix, not an inert one."""
    stack = _z_sweep(with_debris=True)

    def plain_brenner(f):
        d = f[:, 2:] - f[:, :-2]
        return float((d ** 2).mean())

    best = int(np.argmax([plain_brenner(f) for f in stack]))
    assert best == _DEBRIS_FOCUS, (
        f"a plain mean picked frame {best}, not the debris frame {_DEBRIS_FOCUS} — the fixture no "
        f"longer reproduces the bug, so the fix test proves nothing")


def test_bf_focus_metric_is_UNCHANGED_on_a_clean_sweep():
    """No regression: with no debris, the robust metric chooses the same frame a plain mean would."""
    from pycat.toolbox.brightfield_tools import bf_focus_metric

    stack = _z_sweep(with_debris=False)

    def plain_brenner(f):
        d = f[:, 2:] - f[:, :-2]
        return float((d ** 2).mean())

    robust_best = int(np.argmax([bf_focus_metric(f) for f in stack]))
    plain_best = int(np.argmax([plain_brenner(f) for f in stack]))
    assert robust_best == plain_best == _SAMPLE_FOCUS, (
        f"clean-data best frame changed: robust {robust_best} vs plain {plain_best}")


def test_bf_focus_metric_mask_path_is_UNTOUCHED():
    """The masked path is exact where a region is supplied — the robustification is maskless-only."""
    from pycat.toolbox.brightfield_tools import bf_focus_metric

    rng = np.random.default_rng(0)
    frame = rng.random((32, 32)).astype(np.float32)
    mask = np.zeros((32, 32), bool)
    mask[8:24, 8:24] = True

    diff = frame[:, 2:] - frame[:, :-2]
    mb = (mask[:, 2:] != 0) & (mask[:, :-2] != 0)
    expected = float((diff[mb] ** 2).mean())          # the exact masked mean
    assert bf_focus_metric(frame, mask=mask) == pytest.approx(expected)


# ── the live quality functions ──────────────────────────────────────────────────────────────

def _sharpest_frame(per_frame_df):
    """The frame a quality table would call sharpest, by its Laplacian-variance / Brenner column."""
    for col in ('laplacian_variance', 'brenner', 'focus_score'):
        if col in per_frame_df.columns:
            return int(per_frame_df.loc[per_frame_df[col].idxmax(), 'frame'])
    raise AssertionError(f"no sharpness column in {list(per_frame_df.columns)}")


def test_the_condensate_quality_table_scores_the_SAMPLE_sharpest():
    """The real ``analyse_frame_quality`` — its Laplacian-variance column must peak at the sample,
    not the debris. This is the table that drives 'best frame' for condensate analysis."""
    from pycat.toolbox.condensate_physics_tools import analyse_frame_quality

    result = analyse_frame_quality(_z_sweep(with_debris=True))
    per_frame = result['per_frame_df']
    best = int(per_frame.loc[per_frame['laplacian_variance'].idxmax(), 'frame'])
    assert best == _SAMPLE_FOCUS, (
        f"Laplacian variance peaked at frame {best}; the debris (frame {_DEBRIS_FOCUS}) should not "
        f"outscore the in-focus sample")


def test_the_brightfield_quality_table_scores_the_SAMPLE_sharpest():
    """The real ``bf_analyse_frame_quality`` picks the sample as sharpest.

    **This is a sanity check, not a demonstration of the flip** — and the distinction is worth
    recording. Unlike ``bf_focus_metric`` and the condensate path, this function min-max normalises
    **each frame independently** before scoring, which already partly counters a bright speck (the
    speck sets the per-frame max, so it is divided back down). So on this fixture its Brenner column
    peaks at the sample with or without the robustification — the robustification is applied here for
    consistency and defence-in-depth (it does not regress the clean case), not because a flip could
    be shown. The demonstrated negative controls are ``bf_focus_metric`` and the condensate table
    above; those are what the mutation test moves.
    """
    from pycat.toolbox.brightfield_tools import bf_analyse_frame_quality

    result = bf_analyse_frame_quality(_z_sweep(with_debris=True))
    per_frame = result['per_frame_df'] if isinstance(result, dict) else result
    best = int(per_frame.loc[per_frame['brenner'].idxmax(), 'frame'])
    assert best == _SAMPLE_FOCUS, (
        f"Brenner peaked at frame {best}, not the sample frame {_SAMPLE_FOCUS}")
