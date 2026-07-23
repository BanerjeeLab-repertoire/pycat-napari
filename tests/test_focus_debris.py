"""**Focus selection must not pick the sharpest DEBRIS — the SPATIAL layer.**

Most of the debris defense already shipped: ``robust_focus_energy`` (a trimmed mean) drops the top ~1%
of per-pixel magnitudes, so a *small* out-of-plane speck cannot hijack the "best frame". That is the
STATISTICAL layer, and it is covered independently at the bottom of this file.

What trimming cannot reach is a LARGE out-of-plane structure — well above the ~1% trim fraction. The
only defense there is to score inside the biological region: pass a ``mask``. This file pins that
spatial layer for the two focus-series scorers that gained a ``mask=`` parameter:

* ``bf_analyse_focus_series`` (brightfield)
* ``analyse_frame_quality`` (condensate physics)

The fixture is deliberately adversarial: the debris is LARGE (≥8% of the frame, far above the 1% trim)
and sharper per-pixel than the condensate, so ``mask=None`` genuinely picks the debris — proving the
mask, not the trim, is what saves the masked case. (A checkerboard would be a trap here: its period-2
pattern has zero stride-2 Brenner gradient, so it would fail to fool Brenner for the wrong reason —
hence a high-amplitude random texture.)
"""
import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from pycat.toolbox.brightfield_tools import bf_analyse_focus_series
from pycat.toolbox.condensate_physics_tools import analyse_frame_quality
from pycat.utils.math_utils import robust_focus_energy

pytestmark = pytest.mark.base

_H = _W = 120
#: The condensate / biological region — the mask. Central 40×40 = 1600 px ≈ 11% of the frame.
_MASK = np.zeros((_H, _W), bool); _MASK[40:80, 40:80] = True


def _adversarial_stack():
    """3 frames: [0]=in-focus condensate (no debris), [1]=blurry condensate + LARGE sharp debris
    OUTSIDE the mask, [2]=everything blurry. Whole-frame scoring must pick frame 1 (the debris);
    scoring inside the mask must pick frame 0 (the condensate)."""
    rng = np.random.default_rng(0)
    bg = np.full((_H, _W), 0.5, np.float32)
    cond = (0.3 + 0.4 * rng.random((40, 40))).astype(np.float32)      # moderate texture
    debris = rng.random((40, 40)).astype(np.float32)                  # full-range: sharper per pixel
    a = bg.copy(); a[40:80, 40:80] = cond                            # sharp condensate, no debris
    b = bg.copy()
    b[40:80, 40:80] = gaussian_filter(cond, 2.0)                      # condensate now blurry
    b[0:40, 0:40] = debris                                            # 1600 px debris, corner, off-mask
    c = gaussian_filter(a, 3.0).astype(np.float32)                    # all blurry
    return np.stack([a, b, c])


def _clean_stack():
    """3 frames, condensate sharpness decreasing, smooth background, NO debris. The chosen frame must
    be the same with or without a mask — the mask must not perturb good data."""
    rng = np.random.default_rng(1)
    bg = np.full((_H, _W), 0.5, np.float32)
    cond = (0.3 + 0.4 * rng.random((40, 40))).astype(np.float32)
    frames = []
    for sig in (0.0, 1.5, 3.0):
        f = bg.copy()
        f[40:80, 40:80] = cond if sig == 0 else gaussian_filter(cond, sig)
        frames.append(f.astype(np.float32))
    return np.stack(frames)


# ── bf_analyse_focus_series ──────────────────────────────────────────────────
def test_bf_series_unmasked_picks_the_large_debris():
    """Proof the fixture is adversarial: whole-frame scoring picks the debris frame — trimming alone
    does NOT save a large out-of-plane structure."""
    df = bf_analyse_focus_series(_adversarial_stack())
    assert int(df['focus_score'].values.argmax()) == 1


def test_bf_series_masked_picks_the_condensate():
    """Scoring inside the condensate region picks the in-focus condensate frame."""
    df = bf_analyse_focus_series(_adversarial_stack(), mask=_MASK)
    assert int(df['focus_score'].values.argmax()) == 0


def test_bf_series_clean_stack_same_frame_masked_or_not():
    stack = _clean_stack()
    a = int(bf_analyse_focus_series(stack)['focus_score'].values.argmax())
    b = int(bf_analyse_focus_series(stack, mask=_MASK)['focus_score'].values.argmax())
    assert a == b == 0


# ── analyse_frame_quality ────────────────────────────────────────────────────
def _afq_argmax(stack, col, mask=None):
    return int(analyse_frame_quality(stack, mask=mask)['per_frame_df'][col].values.argmax())


def test_afq_unmasked_picks_the_large_debris():
    stack = _adversarial_stack()
    assert _afq_argmax(stack, 'laplacian_variance') == 1
    assert _afq_argmax(stack, 'gradient_energy') == 1


def test_afq_masked_picks_the_condensate():
    stack = _adversarial_stack()
    assert _afq_argmax(stack, 'laplacian_variance', mask=_MASK) == 0
    assert _afq_argmax(stack, 'gradient_energy', mask=_MASK) == 0


def test_afq_clean_stack_same_frame_masked_or_not():
    stack = _clean_stack()
    assert _afq_argmax(stack, 'laplacian_variance') == _afq_argmax(stack, 'laplacian_variance', mask=_MASK) == 0


def test_afq_full_true_mask_reduces_to_whole_frame():
    """`mask=None` must be byte-identical to whole-frame — and a full-True mask (score every pixel)
    must reduce to exactly the same numbers, since the masked path just selects all pixels."""
    stack = _adversarial_stack()
    full = np.ones((_H, _W), bool)
    base = analyse_frame_quality(stack)['per_frame_df']
    masked = analyse_frame_quality(stack, mask=full)['per_frame_df']
    for col in ('laplacian_variance', 'image_entropy', 'gradient_energy'):
        assert np.allclose(base[col].values, masked[col].values), col


# ── the STATISTICAL layer, independent of the mask ───────────────────────────
def test_robust_focus_energy_defeats_small_debris_without_a_mask():
    """The trim layer alone handles a SMALL speck: a plain mean is hijacked by a handful of extreme
    pixels, but the trimmed mean is not — so the two layers are covered independently."""
    extended = np.full(10_000, 0.3)               # spatially-extended, moderate per-pixel energy
    speck = np.zeros(10_000); speck[:80] = 50.0   # 0.8% of pixels (< 1% trim), extreme
    assert speck.mean() > extended.mean()                                   # plain mean is fooled
    assert robust_focus_energy(speck, 0.01) < robust_focus_energy(extended, 0.01)   # trim is not


# ── the mask contract ────────────────────────────────────────────────────────
def test_a_wrong_shaped_mask_fails_loudly():
    """A wrong mask is worse than whole-frame — a shape mismatch must raise, never silently mis-score."""
    stack = _adversarial_stack()
    with pytest.raises(ValueError):
        bf_analyse_focus_series(stack, mask=np.ones((10, 10), bool))
    with pytest.raises(ValueError):
        analyse_frame_quality(stack, mask=np.ones((10, 10), bool))
