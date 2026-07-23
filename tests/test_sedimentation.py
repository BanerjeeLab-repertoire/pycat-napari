"""
**Sedimentation MIMICS coarsening**, and the test that separates them could not see both at once.

Droplets settling into the focal plane make the volume fraction rise. So does droplet growth. If
you call one the other, *a coarsening rate is an artefact of gravity* — and the module knows this;
it exists to catch exactly that.

The bug
-------
The rule was::

    sed    = phi_s > 0 and phi_r2 > 0.3 and **n_s > 0**      # more droplets appear
    coarse = r_s   > 0 and r_r2   > 0.3 and **n_s < 0**      # fewer droplets (they merge)

**``n_s`` cannot be both positive and negative.** So ``sed`` and ``coarse`` were **mutually
exclusive by construction**, and the ``'both'`` branch was **unreachable.**

That is not a style point. **When both processes run at once, the droplet count is the SUM of a
sedimentation gain and a coalescence loss — and it can take either sign.**

Measured on a sample with genuine sedimentation **and** genuine coarsening, the old rule called it
**"sedimentation" 98 % of the time**, and its recommendation said *"no sedimentation artefact"*
about the coarsening. **The opposite of the truth.**

The physics
-----------
=================  ===============================================  ==========================
process            what happens                                     signature
=================  ===============================================  ==========================
sedimentation      droplets settle **into** the focal plane          **φ up**, n up, r flat
coarsening         droplets merge / Ostwald-ripen                    **r up**, n down, φ flat
**both**           settling **while** the residents coarsen          **φ up AND r up**, n either
=================  ===============================================  ==========================

**So φ and r are the discriminators, and n is CORROBORATION** — it strengthens a call, it does not
gate one.
"""

import numpy as np
import pandas as pd
import pytest


def _series(n_frames=20, phi_rate=0.0, radius_rate=0.0, count_rate=0.0,
            noise=0.08, seed=0):
    rng = np.random.default_rng(seed)
    time = np.arange(n_frames) * 10.0
    return pd.DataFrame(dict(
        time_s=time,
        volume_fraction=(0.10 + phi_rate * time) * (1 + rng.normal(0, noise, n_frames)),
        n_droplets=(50 + count_rate * time) * (1 + rng.normal(0, noise, n_frames)),
        mean_radius_um=(2.0 + radius_rate * time) * (1 + rng.normal(0, noise, n_frames)),
    ))


def _calls(scene, repeats=40):
    invitro = pytest.importorskip("pycat.toolbox.invitro_tools")
    return [invitro.detect_sedimentation(_series(seed=seed, **scene))['dominant_process']
            for seed in range(repeats)]


@pytest.mark.base
def test_BOTH_processes_at_once_are_seen_as_BOTH():
    """**The branch was unreachable.** A sample with both was called "sedimentation" 98 % of the
    time, and told the user there was **no coarsening artefact**.
    """
    both = _calls(dict(phi_rate=2e-4, radius_rate=3e-3, count_rate=+0.3))
    fraction = both.count('both') / len(both)

    assert fraction >= 0.7, (
        f"a sample with genuine sedimentation AND genuine coarsening was called 'both' only "
        f"{fraction:.0%} of the time (it was: {set(both)}).\n\n"
        f"The droplet count cannot GATE both processes — when they run together it is the SUM of "
        f"a settling gain and a coalescence loss, and it can take either sign."
    )


@pytest.mark.base
@pytest.mark.parametrize("scene,expected", [
    (dict(phi_rate=2e-4, radius_rate=0.0, count_rate=+0.3), 'sedimentation'),
    (dict(phi_rate=0.0, radius_rate=3e-3, count_rate=-0.3), 'coarsening'),
])
def test_each_process_alone_is_still_identified(scene, expected):
    """**Fixing the 'both' case must not cost specificity.** Measured: 98 % and 83 %."""
    calls = _calls(scene)
    fraction = calls.count(expected) / len(calls)

    assert fraction >= 0.7, (
        f"'{expected}' was detected only {fraction:.0%} of the time; got {set(calls)}"
    )


@pytest.mark.base
def test_a_STABLE_sample_is_not_called_anything():
    """**A false 'coarsening' is a physical claim about a sample that is not changing.**

    The ``R² > 0.3`` gate does real work here: on a 20-frame series the false-positive rate is
    **2 %**. (On a **5**-frame series it is **8 %** — a slope fitted to five noisy points finds a
    trend by chance, and that is a property of the data, not of the code.)
    """
    calls = _calls(dict(phi_rate=0.0, radius_rate=0.0, count_rate=0.0), repeats=100)
    false_positives = sum(1 for c in calls if c != 'stable')

    assert false_positives <= 10, (
        f"{false_positives}/100 STABLE samples were called sedimentation or coarsening. A false "
        f"call here is a physical claim about a sample that is not changing."
    )
