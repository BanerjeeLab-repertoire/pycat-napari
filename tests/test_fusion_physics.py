"""
Droplet fusion: τ is the physics, and it feeds η/γ directly.

Two droplets coalesce and the aspect ratio relaxes exponentially. The **inverse capillary
velocity** — η/γ, the viscosity-to-surface-tension ratio — is read off the **slope of τ against
droplet length**. So **a biased τ is a biased η/γ, by exactly the same factor.**

``inverse_capillary_velocity`` is correct: it recovers **2.58 against a true 2.5 s/µm** (3 %) at
R² = 0.98.

``fit_fusion_relaxation`` was not, and R² could not see it
----------------------------------------------------------
The covariance was discarded (``popt, _ = curve_fit(...)``) — the same bug as FRAP (1.5.446), the
MSD fit (1.5.447) and photobleaching (1.5.451). Measured, **true τ = 20 s**:

============  =================  ===============  ==========  =========
window        τ observed         fitted τ         error       R²
============  =================  ===============  ==========  =========
0–40 s        2.0                15.69            **−21.6 %** **1.000**
0–60 s        3.0                17.89            −10.6 %     **1.000**
0–100 s       5.0                19.11            −4.4 %      1.000
0–200 s       10.0               19.79            −1.0 %      1.000
============  =================  ===============  ==========  =========

**A 21.6 % error in τ, at R² = 1.000.** The curve fits the points that exist; it says nothing
about whether they constrain the decay. **And a 21.6 % error in τ is a 21.6 % error in η/γ.**
"""

import numpy as np
import pytest


@pytest.mark.core
@pytest.mark.parametrize("true_tau", [5.0, 10.0])
def test_fusion_recovers_a_known_relaxation_time(true_tau):
    """The baseline: an adequately long record must recover τ."""
    fusion = pytest.importorskip("pycat.toolbox.fusion_tools")

    rng = np.random.default_rng(0)
    t = np.linspace(0, 15 * true_tau, 80)              # 15 time constants: ample
    signal = fusion.fusion_relaxation_model(t, a=2.0, tau=true_tau, b=0.0, d=1.0)
    signal = signal + rng.normal(0, 0.02, len(t))

    result = fusion.fit_fusion_relaxation(t, signal)

    assert result["tau_s"] == pytest.approx(true_tau, rel=0.10), (
        f"tau = {result['tau_s']:.2f} against a true {true_tau} over a 15-tau record. This is "
        f"the easy case — if it fails, nothing downstream is meaningful, because eta/gamma is "
        f"the slope of tau against droplet length."
    )


@pytest.mark.core
def test_a_short_record_biases_tau_low_and_says_so():
    """**A 21.6 % error in τ, at R² = 1.000** — and τ is the physics.

    A record shorter than ~3 relaxation times cannot constrain τ, and R² cannot see it: the
    curve fits the points that exist. Since the inverse capillary velocity is the slope of τ
    against droplet length, **a τ biased 20 % low gives an η/γ biased 20 % low** — and it would
    be reported with a perfect fit statistic beside it.
    """
    fusion = pytest.importorskip("pycat.toolbox.fusion_tools")

    true_tau = 20.0
    rng = np.random.default_rng(0)

    short_t = np.linspace(0, 40, 60)                   # 2 time constants
    short = fusion.fit_fusion_relaxation(
        short_t,
        fusion.fusion_relaxation_model(short_t, a=2.0, tau=true_tau, b=1.0, d=0.0)
        + rng.normal(0, 0.02, 60))

    long_t = np.linspace(0, 200, 60)                   # 10 time constants
    long = fusion.fit_fusion_relaxation(
        long_t,
        fusion.fusion_relaxation_model(long_t, a=2.0, tau=true_tau, b=1.0, d=0.0)
        + rng.normal(0, 0.02, 60))

    # The premise: R² is blind to this.
    assert short["r_squared"] > 0.99, (
        "the whole point is that a badly biased fit still has a perfect R2"
    )
    assert short["tau_s"] < 0.85 * true_tau, (
        f"a 2-tau record fitted tau = {short['tau_s']:.1f} against a true {true_tau} — the "
        f"premise of this test is that a short record biases tau LOW"
    )

    assert short["relaxations_observed"] < 3.0, (
        f"the record covers {short['relaxations_observed']:.1f} relaxation times and must be "
        f"reported as too short"
    )
    assert long["relaxations_observed"] > 3.0, (
        "a 10-tau record must NOT be flagged — the gate must not cry wolf"
    )
    assert long["tau_s"] == pytest.approx(true_tau, rel=0.05)


@pytest.mark.core
def test_tau_carries_an_interval_because_eta_over_gamma_is_read_from_it():
    """**τ without an interval is not a measurement**, and η/γ is read straight off it."""
    fusion = pytest.importorskip("pycat.toolbox.fusion_tools")

    rng = np.random.default_rng(0)

    intervals = {}
    for span in (40, 200):
        t = np.linspace(0, span, 60)
        y = fusion.fusion_relaxation_model(t, a=2.0, tau=20.0, b=1.0, d=0.0)
        result = fusion.fit_fusion_relaxation(t, y + rng.normal(0, 0.02, 60))

        assert result["tau_ci"] is not None, (
            "fit_fusion_relaxation reported no confidence interval on tau. The covariance from "
            "curve_fit is the only thing that knows whether the record constrains the decay, "
            "and it was being discarded (`popt, _ = curve_fit(...)`)."
        )
        low, high = result["tau_ci"]
        assert low < result["tau_s"] < high
        intervals[span] = high - low

    assert intervals[200] < 0.5 * intervals[40], (
        f"the interval on tau must NARROW as the record lengthens — it is "
        f"{intervals[40]:.2f} s over 2 time constants and {intervals[200]:.2f} s over 10. If it "
        f"does not, the covariance is not tracking the information in the data."
    )


@pytest.mark.core
def test_inverse_capillary_velocity_recovers_eta_over_gamma():
    """The physics output: **η/γ is the slope of τ against droplet length.**

    This is what a fusion paper reports, and it is correct — 2.58 against a true 2.5 s/µm.
    """
    fusion = pytest.importorskip("pycat.toolbox.fusion_tools")

    true_eta_over_gamma = 2.5                          # s/µm
    lengths = np.array([2.0, 4.0, 6.0, 8.0, 10.0, 12.0])

    rng = np.random.default_rng(1)
    taus = true_eta_over_gamma * lengths * (1 + rng.normal(0, 0.08, len(lengths)))

    result = fusion.inverse_capillary_velocity(taus, lengths)

    assert result["inverse_capillary_velocity_s_per_um"] == pytest.approx(
        true_eta_over_gamma, rel=0.15), (
        f"eta/gamma = {result['inverse_capillary_velocity_s_per_um']:.2f} s/um against a true "
        f"{true_eta_over_gamma}. This is the number the paper reports."
    )
    assert result["r_squared"] > 0.9
