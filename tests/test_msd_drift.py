"""
Stage drift looks exactly like superdiffusion, and drift correction removes it.

Confinement is guarded (1.5.401): it pulls α **down**, and a probe hitting a wall is reported as
"subdiffusion". **The opposite direction was not guarded** — and in bead tracking it is the more
common artifact.

Drift is **ballistic**: a stage moving at speed v contributes ``(v·τ)²`` to the MSD, which grows
as τ² and pushes α toward 2. **And the slower the probe, the worse it is**, because the drift
term is compared against a smaller diffusive signal.

In a viscous condensate this is severe. For η = 8 Pa·s and a 100 nm bead, Stokes–Einstein gives
**D = 0.00027 µm²/s** — a near-stationary probe. Measured on simulated tracks:

==============  ==============  ==========  ==============  ==========
stage drift     D uncorrected   α           D corrected     α
==============  ==============  ==========  ==============  ==========
0               0.000259        1.03        0.000253        1.03
0.02 µm/s       0.000294        **1.62**    0.000253        **1.03**
0.05 µm/s       **0.000809**    **1.91**    0.000253        **1.03**
==============  ==============  ==========  ==============  ==========

**Fifty nanometres per second of stage drift triples D and drives α to 1.91** — which reads as
directed, active transport. It is the stage.

``drift_correct_com`` subtracts the common-mode motion of all tracks and **recovers both D and α
exactly.**
"""

import numpy as np
import pytest

# η = 8 Pa·s, R = 100 nm, T = 297 K → a near-stationary probe.
_D_TRUE = 0.000272
_DT = 0.5


def _tracks(drift_um_per_s, n_tracks=40, n_frames=60, seed=0):
    """Brownian tracks plus a COMMON drift in x — which is what a moving stage produces."""
    import pandas as pd

    rng = np.random.default_rng(seed)
    step_sd = np.sqrt(2 * _D_TRUE * _DT)

    rows = []
    for track_id in range(n_tracks):
        x, y = rng.uniform(0, 50), rng.uniform(0, 50)
        for frame in range(n_frames):
            rows.append(dict(track_id=track_id, frame=frame, x_um=x, y_um=y))
            x += rng.normal(0, step_sd) + drift_um_per_s * _DT
            y += rng.normal(0, step_sd)
    return pd.DataFrame(rows)


@pytest.mark.core
@pytest.mark.parametrize("drift", [0.02, 0.05])
def test_drift_correction_recovers_alpha_and_D(drift):
    """Uncorrected drift inflates α toward 2; the correction must recover the truth."""
    vpt = pytest.importorskip("pycat.toolbox.vpt_tools")
    physics = pytest.importorskip("pycat.toolbox.condensate_physics_tools")

    tracks = _tracks(drift)

    def _fit(df):
        msd = physics.compute_msd(df, frame_interval_s=_DT)
        return physics.fit_anomalous_diffusion(msd, confine_to_defensible_bounds=False)

    uncorrected = _fit(tracks)
    corrected = _fit(vpt.drift_correct_com(tracks.copy()))

    assert uncorrected["alpha"] > 1.3, (
        f"the premise of this test is that {drift} µm/s of stage drift inflates alpha "
        f"(it came out at {uncorrected['alpha']:.2f}). If that is no longer true, the "
        f"warning text in fit_anomalous_diffusion needs re-measuring."
    )

    assert corrected["alpha"] == pytest.approx(1.0, abs=0.15), (
        f"after drift_correct_com, alpha is {corrected['alpha']:.2f} — it must come back to "
        f"~1.0 (Brownian). If this fails, the advice in the superdiffusion warning is HOLLOW: "
        f"it tells the user that drift correction fixes this, and that must be true."
    )
    assert corrected["D_um2_per_s"] == pytest.approx(_D_TRUE, rel=0.25), (
        f"after drift_correct_com, D is {corrected['D_um2_per_s']:.6f} against a true "
        f"{_D_TRUE:.6f}. Drift inflates D as well as alpha — on a slow probe it tripled it."
    )
