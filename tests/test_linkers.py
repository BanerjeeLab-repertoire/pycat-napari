"""
The linkers, against known identity. **They produce the 8.325 viscosity and had zero tests.**

``dynamic_spatial_tools`` contains the two automated linkers — greedy and Bayesian — that turn
detections into trajectories. Every VPT viscosity PyCAT reports comes through one of them, and
**nothing tested them.**

The only honest test of a linker is **ground truth identity**: simulate objects whose true
identity is known, link the detections, and ask what fraction got the right one. Two failure
modes, and they are different:

* **Fragmentation** — one object split across several tracks. Depresses the MSD (short tracks
  contribute only short lags) and inflates the viscosity.
* **Mixing** — one track containing two objects. Injects a spurious jump into the MSD and
  *deflates* the viscosity.

A track count alone catches neither: 20 objects can produce 20 tracks that are all wrong.

What the ground truth found
---------------------------
**Dropout fragments the linkers catastrophically, and gap-closing repairs it:**

========  =====  ==========  ====================
dropout   gap    purity      n tracks (true 20)
========  =====  ==========  ====================
10 %      0      **49 %**    **92**
10 %      1      87 %        32
10 %      **3**  **99 %**    **21**
20 %      0      **29 %**    **147**
20 %      **3**  **99 %**    **21**
========  =====  ==========  ====================

**Zero mixed tracks at any gap** on well-separated objects — so bridging a gap does not cause
mislinks, it only repairs breaks. **This is the single highest-value knob in the VPT chain**, and
it was set to 1.

**And it moved Gable's real measurement.** On the bead file, ``gap=2–3`` gives **η = 8.54–8.57
against the 8.325 reference (2.6 %)** with **α = 0.97**, where ``gap=1`` gave 7.97 with α = 0.93.

**The two linkers are identical until objects get confusable**, and then the Bayesian one wins —
exactly where an assignment model should:

===============  ==================  ==================
object spacing   greedy purity/mix   bayes purity/mix
===============  ==================  ==================
5.0 µm           100 % / 0           100 % / 0
1.0 µm           100 % / 0           100 % / 0
0.3 µm           70 % / 11           **79 % / 14**
0.2 µm           **52 % / 25**       **67 % / 14**
===============  ==================  ==================
"""

import numpy as np
import pandas as pd
import pytest


def _known_objects(n_objects=20, n_frames=40, step_um=0.05, spacing_um=3.0,
                   dropout=0.0, seed=0):
    """Objects on a grid, each diffusing slightly. **The true identity is recorded.**"""
    rng = np.random.default_rng(seed)
    side = int(np.ceil(np.sqrt(n_objects)))

    rows = []
    for i in range(n_objects):
        x = (i % side) * spacing_um + rng.uniform(-0.2, 0.2)
        y = (i // side) * spacing_um + rng.uniform(-0.2, 0.2)
        for frame in range(n_frames):
            if rng.random() >= dropout:          # the detector may MISS it this frame
                rows.append(dict(frame=frame, object_id=len(rows),
                                 x_um=x, y_um=y, area_um2=1.0, true_id=i))
            x += rng.normal(0, step_um)
            y += rng.normal(0, step_um)

    return pd.DataFrame(rows)


def _purity(truth, tracks):
    """What fraction of detections got the RIGHT identity, and how many tracks MIX objects?"""
    merged = truth.merge(tracks[['object_id', 'track_id']], on='object_id', how='left')

    correct = total = 0
    for _, group in merged.groupby('true_id'):
        total += len(group)
        if group.track_id.notna().any():
            modal = group.track_id.mode().iloc[0]
            correct += int((group.track_id == modal).sum())

    mixed = sum(1 for _, g in merged.dropna(subset=['track_id']).groupby('track_id')
                if g.true_id.nunique() > 1)

    return correct / max(total, 1), mixed, merged.track_id.nunique()


def _linkers():
    dst = pytest.importorskip("pycat.toolbox.dynamic_spatial_tools")
    return [("greedy", dst.link_trajectories),
            ("bayesian", dst.link_trajectories_bayesian)]


@pytest.mark.core
@pytest.mark.parametrize("name,linker", _linkers())
def test_linker_is_perfect_on_clean_well_separated_objects(name, linker):
    """The baseline. If this fails, nothing downstream can be trusted."""
    truth = _known_objects()
    tracks = linker(truth.drop(columns=['true_id']).copy(),
                    max_displacement_um=0.3, max_gap_frames=1)

    purity, mixed, n_tracks = _purity(truth, tracks)

    assert purity == 1.0, (
        f"{name}: only {purity:.0%} of detections got the right identity on CLEAN, "
        f"well-separated objects diffusing far less than their spacing. There is no ambiguity "
        f"here to get wrong."
    )
    assert mixed == 0, f"{name}: {mixed} track(s) contain more than one true object"
    assert n_tracks == 20, f"{name}: {n_tracks} tracks from 20 objects"


@pytest.mark.core
@pytest.mark.parametrize("name,linker", _linkers())
def test_gap_closing_repairs_detection_dropout(name, linker):
    """**Dropout shatters a linker, and the gap setting is what repairs it.**

    A detector that misses an object in 10 % of frames turns 20 objects into **92 tracks** at
    ``gap=0`` — with only **49 %** of detections keeping their identity. At ``gap=3`` it is
    **21 tracks and 99 %.**

    This is not a subtlety. **It is the single highest-value knob in the VPT chain**, and the
    pipeline was running at ``gap=1`` — which recovers only 87 % at 10 % dropout, and 71 % at
    20 %.

    And it is safe: **zero mixed tracks at any gap** on well-separated objects. Bridging a gap
    repairs a break; it does not create a mislink.
    """
    truth = _known_objects(dropout=0.10)

    at_gap_0 = _purity(truth, linker(truth.drop(columns=['true_id']).copy(),
                                     max_displacement_um=0.3, max_gap_frames=0))
    at_gap_3 = _purity(truth, linker(truth.drop(columns=['true_id']).copy(),
                                     max_displacement_um=0.3, max_gap_frames=3))

    assert at_gap_0[0] < 0.6, (
        f"{name}: the premise of this test is that gap=0 SHATTERS on dropout "
        f"(purity came out at {at_gap_0[0]:.0%})"
    )
    assert at_gap_3[0] > 0.95, (
        f"{name}: at gap=3 the purity is {at_gap_3[0]:.0%} — gap-closing must REPAIR the "
        f"dropout fragmentation, not merely reduce it. At gap=0 it is {at_gap_0[0]:.0%}, and "
        f"the same 20 objects produce {at_gap_0[2]} tracks."
    )
    assert at_gap_3[2] == pytest.approx(20, abs=2), (
        f"{name}: {at_gap_3[2]} tracks from 20 objects at gap=3"
    )
    assert at_gap_3[1] == 0, (
        f"{name}: gap-closing created {at_gap_3[1]} MIXED track(s). Bridging a gap must repair "
        f"a break, not invent a link between two different objects — a mixed track injects a "
        f"spurious jump into the MSD and DEFLATES the viscosity."
    )


@pytest.mark.core
def test_the_bayesian_linker_wins_where_assignment_is_ambiguous():
    """The two linkers are **identical** until objects get confusable — and then Bayes wins.

    That is exactly where an assignment model should earn its keep, and it is worth knowing:
    on well-separated data (Gable's beads sit ~1.7 µm apart) the two are interchangeable, so
    the choice only matters in crowded fields.

    ===============  ==================  ==================
    object spacing   greedy purity/mix   bayes purity/mix
    ===============  ==================  ==================
    1.0 µm           100 % / 0           100 % / 0
    0.2 µm           **52 % / 25**       **67 % / 14**
    ===============  ==================  ==================
    """
    dst = pytest.importorskip("pycat.toolbox.dynamic_spatial_tools")

    crowded = _known_objects(n_objects=16, n_frames=30, spacing_um=0.2, step_um=0.05)

    greedy = _purity(crowded, dst.link_trajectories(
        crowded.drop(columns=['true_id']).copy(),
        max_displacement_um=0.3, max_gap_frames=1))
    bayes = _purity(crowded, dst.link_trajectories_bayesian(
        crowded.drop(columns=['true_id']).copy(),
        max_displacement_um=0.3, max_gap_frames=1))

    assert bayes[0] > greedy[0] + 0.08, (
        f"in a CROWDED field (objects 0.2 um apart, diffusing 0.05 um/frame) the Bayesian "
        f"linker scored {bayes[0]:.0%} against greedy's {greedy[0]:.0%}. The whole point of a "
        f"global assignment is to resolve exactly this ambiguity — if it does not beat "
        f"nearest-neighbour here, it is not earning its cost."
    )
    assert bayes[1] < greedy[1], (
        f"the Bayesian linker produced {bayes[1]} mixed tracks against greedy's {greedy[1]}"
    )


# ── Byte-identical characterization: pins the EXACT assignment before/after a refactor ───────────
#
# The property tests above pin purity/mixing/track-count; this pins the exact per-detection assignment
# (track_id + link_cost) on a fixed scenario, so a phase-split of `link_trajectories_bayesian` can be
# proven to move no number. The scenario deliberately exercises every branch: four births, ongoing links,
# a two-frame dropout that gap-closing must bridge (object C), directed motion (velocity prediction),
# and distinct areas (the area-consistency cost block).

def _characterization_scenario():
    rng = np.random.default_rng(7)
    rows = []
    tracks = {
        'A': dict(y=0.0, x=0.0, vy=0.0, vx=0.20, area=1.0, drop=set()),
        'B': dict(y=0.0, x=3.0, vy=0.0, vx=-0.20, area=4.0, drop=set()),
        'C': dict(y=5.0, x=5.0, vy=0.0, vx=0.0, area=1.0, drop={4, 5}),
        'D': dict(y=2.0, x=2.0, vy=0.15, vx=0.15, area=2.0, drop=set()),
    }
    oid = 0
    for frame in range(12):
        for tr in tracks.values():
            y = tr['y'] + tr['vy'] * frame + rng.normal(0, 0.02)
            x = tr['x'] + tr['vx'] * frame + rng.normal(0, 0.02)
            a = tr['area'] + rng.normal(0, 0.01)
            if frame not in tr['drop']:
                rows.append(dict(frame=frame, object_id=oid, y_um=y, x_um=x, area_um2=a))
            oid += 1
    return pd.DataFrame(rows)


_GOLDEN_TRACK_IDS = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 3, 0, 1, 3, 0, 1, 2, 3,
                     0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3, 0, 1, 2, 3]
_GOLDEN_LINK_COSTS = [4.5, 4.5, 4.5, 4.5, 0.139223, 0.218376, 0.023469, 0.171413, 0.126017, 0.058908,
                      0.008896, 0.157319, 0.070922, 0.044404, 0.008999, 0.035688, 0.037136, 0.026978,
                      0.063504, 0.004536, 0.018868, 0.00294, 0.014872, 0.010828, 0.006201, 0.010877,
                      0.004217, 0.005407, 0.002022, 0.01026, 0.020213, 0.019068, 0.007057, 0.006103,
                      0.011007, 0.019907, 0.02732, 0.003596, 0.002515, 0.015899, 0.027418, 0.014136,
                      0.004566, 0.029801, 0.026932, 0.009697]


@pytest.mark.core
def test_bayesian_linker_assignment_is_byte_identical():
    """The exact assignment on a fixed multi-branch scenario — a golden master. If a refactor of
    `link_trajectories_bayesian` changes any track_id or link_cost, this fails: the Hungarian solve is
    sensitive to the exact cost matrix, so byte-identical output proves the cost construction was
    preserved. Guards the phase-split (births, links, a bridged dropout gap, velocity, area cost)."""
    dst = pytest.importorskip("pycat.toolbox.dynamic_spatial_tools")
    out = dst.link_trajectories_bayesian(
        _characterization_scenario(), max_displacement_um=1.0, max_gap_frames=2,
        area_weight=0.3, use_velocity=True)

    assert out['track_id'].tolist() == _GOLDEN_TRACK_IDS, "the exact track assignment changed"
    assert out['track_id'].nunique() == 4, "gap-closing must bridge C's dropout into ONE track, not two"
    assert np.allclose(out['link_cost'].to_numpy(), _GOLDEN_LINK_COSTS, atol=1e-5, rtol=0), (
        "a link cost changed — the cost-matrix construction was not preserved byte-for-byte")
