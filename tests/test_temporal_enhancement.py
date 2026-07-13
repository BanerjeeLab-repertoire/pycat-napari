"""
Temporal enhancement destroys intensity-vs-time information. **Every method. By construction.**

This is not a bug in one of them — it is what a contrast enhancement *does*: it normalises each
frame against its own statistics, and **a real change in brightness over time is normalised away
with it.**

Measured, on objects that genuinely grow **+44 %** across 20 frames:

===============  ===================  ==========
method           trend still present  Spearman
===============  ===================  ==========
*(raw)*          **+44 %**            —
per_frame        **+1 %**             0.23
pooled_stats     **+1 %**             0.23
windowed_mean    **+3 %**             −0.03
triplanar        **+2 %**             0.17
===============  ===================  ==========

**A 44 % growth becomes 1 %.** So an enhanced stack must NOT be used for condensate growth or
coarsening rates, FRAP recovery, photobleaching correction, or partition/enrichment over time.
**The numbers will still come out, and they will be wrong.**

It is safe — and useful — for **segmentation and detection**, where only the *shape* matters and
the absolute intensity is discarded anyway. **That is what it is for.**

``score_trend_preservation`` measures exactly this damage, and ``enhance_stack`` **never called
it.**
"""

import numpy as np
import pytest


def _growing_stack(trend=0.5, n_frames=20, size=64, seed=0):
    """Objects whose intensity genuinely changes over time. ``trend=0`` is static."""
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(seed)

    base = np.full((size, size), 100.0)
    for cy, cx in [(20, 20), (20, 44), (44, 20), (44, 44)]:
        base += 400 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2)) / (2 * 4.0 ** 2))

    return np.stack([
        np.clip(base * (1 + trend * t / n_frames) + rng.normal(0, 30, base.shape), 0, 4095)
        for t in range(n_frames)]).astype(np.uint16)


@pytest.mark.core
@pytest.mark.parametrize("method", ["per_frame", "pooled_stats", "windowed_mean"])
def test_every_enhancement_method_destroys_the_intensity_trend(method):
    """**A 44 % growth becomes 1 %.** This is the whole finding, and it must not regress silently.

    If a future method DOES preserve the trend, this test will fail — and that is the right
    outcome: it would be a genuine improvement, and the warning attached to ``enhance_stack``
    would need to stop firing for it.
    """
    enh_mod = pytest.importorskip("pycat.toolbox.temporal_enhancement_tools")

    raw = _growing_stack(trend=0.5)
    enhanced = np.asarray(enh_mod.enhance_stack(raw, ball_radius=8, method=method, window=2))

    mask = raw[0] > np.percentile(raw[0], 97)

    def _trend(stack):
        values = [float(np.asarray(stack)[t][mask].mean()) for t in range(stack.shape[0])]
        return values[-1] / max(values[0], 1e-9) - 1.0

    raw_trend = _trend(raw)
    enhanced_trend = _trend(enhanced)

    assert raw_trend > 0.30, (
        f"the test stack must actually GROW (it changed by {raw_trend:.0%})"
    )
    assert abs(enhanced_trend) < 0.10, (
        f"'{method}' preserved {enhanced_trend:.0%} of a {raw_trend:.0%} growth. If an "
        f"enhancement method now PRESERVES the intensity trend, that is a real improvement — "
        f"and the warning on enhance_stack must be updated to stop firing for it."
    )


@pytest.mark.core
def test_enhance_stack_warns_when_it_flattens_a_real_trend():
    """The check existed and was **never run**.

    ``score_trend_preservation`` measures exactly the damage ``enhance_stack`` does, and
    ``enhance_stack`` never called it — so the user got an enhanced stack with **no indication**
    that a real intensity trend had been flattened out of it.
    """
    enh_mod = pytest.importorskip("pycat.toolbox.temporal_enhancement_tools")

    messages = []
    real_warn = enh_mod.napari_show_warning
    enh_mod.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    try:
        enh_mod.enhance_stack(_growing_stack(trend=0.5), ball_radius=8,
                              method='pooled_stats', window=2)
        on_growth = len(messages)

        messages.clear()
        enh_mod.enhance_stack(_growing_stack(trend=0.0), ball_radius=8,
                              method='pooled_stats', window=2)
        on_static = len(messages)
    finally:
        enh_mod.napari_show_warning = real_warn

    assert on_growth > 0, (
        "a stack whose objects grow 44% was enhanced, the growth was flattened to 1%, and the "
        "user was told NOTHING. Any growth rate, coarsening exponent or FRAP recovery measured "
        "on that stack is destroyed — and the numbers still come out looking reasonable."
    )
    assert on_static == 0, (
        "the warning fired on a STATIC stack. There is no trend there to destroy, so flattening "
        "it costs nothing — and a warning that cries wolf will be turned off."
    )


@pytest.mark.core
def test_the_warning_also_fires_on_a_fade_not_only_on_growth():
    """Photobleaching is a trend too, and enhancement erases it — which erases the correction."""
    enh_mod = pytest.importorskip("pycat.toolbox.temporal_enhancement_tools")

    messages = []
    real_warn = enh_mod.napari_show_warning
    enh_mod.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    try:
        enh_mod.enhance_stack(_growing_stack(trend=-0.4), ball_radius=8,
                              method='pooled_stats', window=2)
    finally:
        enh_mod.napari_show_warning = real_warn

    assert messages, (
        "a stack that FADES by 40% — a photobleaching series — was enhanced and the fade was "
        "erased. The fade IS the signal a bleach correction fits, and erasing it silently makes "
        "the correction meaningless."
    )


@pytest.mark.core
@pytest.mark.parametrize("true_tau", [1.0, 3.0, 8.0])
def test_temporal_correlation_is_exact_on_a_known_AR1_process(true_tau):
    """``estimate_temporal_correlation`` recovers the frame-to-frame correlation **exactly**.

    An AR(1) process with correlation time τ has ρ(1) = exp(−1/τ) analytically, and the
    estimator matches it to **three decimal places** at every τ tested:

    ==========  =============  ===========
    true τ      true ρ(1)      reported
    ==========  =============  ===========
    1.0         0.368          **0.368**
    3.0         0.717          **0.718**
    8.0         0.882          **0.883**
    ==========  =============  ===========

    This is the measurement that decides whether temporal enhancement is *worth* applying — and
    it feeds a recommendation that is honest about the cost (*"may help but could slightly
    soften fast dynamics; inspect results before relying on it for quantitative analysis"*).

    **Recording a pass is as much the point as recording a bug.** An audit is only worth
    something if a clean result means something.
    """
    ts = pytest.importorskip("pycat.toolbox.timeseries_condensate_tools")

    size, n_frames = 48, 60
    rng = np.random.default_rng(0)
    phi = np.exp(-1.0 / true_tau)                       # the AR(1) coefficient

    frames = [rng.normal(0, 1, (size, size))]
    for _ in range(1, n_frames):
        frames.append(phi * frames[-1]
                      + np.sqrt(1 - phi ** 2) * rng.normal(0, 1, (size, size)))

    stack = (np.stack(frames) * 50 + 500).astype(np.float32)

    result = ts.estimate_temporal_correlation(stack)

    assert result["mean_correlation"] == pytest.approx(phi, abs=0.02), (
        f"reported {result['mean_correlation']:.3f} against an analytic rho(1) = "
        f"exp(-1/{true_tau}) = {phi:.3f}"
    )
    assert result["recommendation"], "the estimate must come with a recommendation"
