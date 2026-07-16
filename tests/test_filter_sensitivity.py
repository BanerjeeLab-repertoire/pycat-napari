"""**Proving the harness, on the two defaults that already inverted a result.**

`filter_sensitivity.py` is machinery for catching a filter default that silently changes the science.
A harness nobody has seen fail is not evidence — so it is aimed first at the two cases PyCAT has
*already shipped and fixed*, which gives each check a pair:

* a **positive control** — the current, fixed default passes;
* a **negative control** — the old, known-bad value is *caught*.

If the negative control ever stops raising, the harness has gone blind and these tests are decoration.

Everything here drives the **real** production functions. The old bad forms are reconstructed as local
lambdas — never put back into production — purely to prove detection.
"""

# Third party imports
import numpy as np
import pytest

from tests.filter_sensitivity import (VALIDATED_CASES, FilterSensitivityError,
                                      assert_no_selection_bias,
                                      assert_offset_invariant, assert_scale_invariant, sweep)


pytestmark = pytest.mark.core


# ── Case 1: molecular counting / r2_min — SELECTION BIAS ──────────────────────────────────

_DIM_N, _BRIGHT_N = 20, 68
_TRUE_MEAN_N = (_DIM_N + _BRIGHT_N) / 2.0          # 44 — the answer the population really has


def _bleaching_trace(n_molecules, nu=50.0, tau=30.0, frames=80, seed=0):
    """A bleaching trace of ``n_molecules`` fluorophores, with shot noise.

    Shot noise is the point: it makes a **bright trace fit better than a dim one**, for no reason
    connected to whether its molecule count is correct. That is the whole mechanism.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(frames)
    signal = n_molecules * nu * np.exp(-t / tau)
    return signal + rng.normal(0, np.sqrt(np.maximum(signal, 1)), frames)


def _population():
    """Half dim, half bright. True mean N = 44."""
    return ([_bleaching_trace(_DIM_N, seed=i) for i in range(10)]
            + [_bleaching_trace(_BRIGHT_N, seed=100 + i) for i in range(10)])


def _recovered_mean_N(r2_min):
    """The REAL `count_molecules_single`, over the population, at this gate."""
    from pycat.toolbox.molecular_counting_tools import count_molecules_single

    kept = [out['N'] for out in (count_molecules_single(t, r2_min=r2_min) for t in _population())
            if out['accepted'] and np.isfinite(out['N'])]
    return float(np.mean(kept)) if kept else float('nan')


def test_the_MECHANISM_is_real_a_brighter_trace_fits_better():
    """**Why the gate is a sampling decision, not quality control.**

    R² measures how well the bleaching curve fits — and it rises with N, because a brighter trace has
    a better signal-to-shot-noise ratio. So "keep only good fits" means "keep the bright ones", and
    the population mean it reports is of a sample it chose by the very quantity being measured.
    """
    from pycat.toolbox.molecular_counting_tools import count_molecules_single

    outs = [count_molecules_single(t, r2_min=0.0) for t in _population()]
    dim_r2 = np.mean([o['bleach_r2'] for o in outs[:10]])
    bright_r2 = np.mean([o['bleach_r2'] for o in outs[10:]])

    assert bright_r2 > dim_r2, (
        f"the mechanism is not present in this fixture (dim R^2 {dim_r2:.4f} >= bright "
        f"{bright_r2:.4f}) — then the negative control below proves nothing")


def test_POSITIVE_the_current_r2_min_default_recovers_the_truth():
    """`r2_min=0.0` — the fixed default. No gate, no selection, the right answer."""
    assert_no_selection_bias(
        lambda r2_min: _recovered_mean_N(r2_min),
        param='r2_min', values=[0.0], truth=_TRUE_MEAN_N, tol=6.0,
        statistic='the mean molecule count')


def test_NEGATIVE_the_harness_CATCHES_the_r2_min_inversion():
    """**The proof the harness works.** `r2_min=0.998` keeps the bright half and reports a
    population mean far above the truth. If this stops raising, the harness has gone blind."""
    with pytest.raises(FilterSensitivityError) as caught:
        assert_no_selection_bias(
            lambda r2_min: _recovered_mean_N(r2_min),
            param='r2_min', values=[0.0, 0.998], truth=_TRUE_MEAN_N, tol=6.0,
            statistic='the mean molecule count')

    assert '0.998' in str(caught.value), "the report must name the value that broke it"


def test_the_bias_grows_MONOTONICALLY_with_the_gate():
    """Not one bad value — a direction. The harder you filter, the higher the reported mean, which
    is what makes this a sampling effect rather than noise."""
    answers = sweep(lambda r2_min: _recovered_mean_N(r2_min),
                    param='r2_min', values=[0.0, 0.995, 0.998])
    recovered = [answers[v] for v in (0.0, 0.995, 0.998)]

    assert recovered == sorted(recovered), (
        f"the recovered mean did not climb with the gate: {recovered}")
    assert recovered[0] < _TRUE_MEAN_N + 6.0 < recovered[-1], (
        f"expected the gate to carry the answer from ~{_TRUE_MEAN_N} upward; got {recovered}")


# ── Case 2: transfection gate / camera pedestal — OFFSET SENSITIVITY ──────────────────────

_BACKGROUND = 20.0                 # a real sample's background sits above zero
_EXPRESSION = (0, 0, 60, 200)      # cells 1,2 dark; cells 3,4 expressing
_TRANSFECTED = {3, 4}              # the truth


def _transfection_scene(pedestal, seed=0):
    """Four cells on a fluor frame, plus a camera pedestal — a constant carrying no information."""
    rng = np.random.default_rng(seed)
    size = 96
    labels = np.zeros((size, size), int)
    frame = np.full((size, size), _BACKGROUND)
    yy, xx = np.mgrid[0:size, 0:size]
    for index, expression in enumerate(_EXPRESSION, start=1):
        cy, cx = 24 + 48 * ((index - 1) // 2), 24 + 48 * ((index - 1) % 2)
        cell = (yy - cy) ** 2 + (xx - cx) ** 2 < 14 ** 2
        labels[cell] = index
        frame[cell] = _BACKGROUND + expression
    return labels, frame + rng.normal(0, 3.0, (size, size)) + pedestal


def _kept_by_production(pedestal):
    """The REAL `filter_cells_by_transfection` — the current contrast-to-noise form."""
    from pycat.toolbox.ts_cellpose_tools import filter_cells_by_transfection

    labels, frame = _transfection_scene(pedestal)
    kept, _dropped, _stats, _efficiency = filter_cells_by_transfection(labels, frame)
    return {int(label) for label in kept}


def _kept_by_the_OLD_ratio(pedestal, threshold=2.0):
    """The removed mean/background RATIO, reconstructed **locally**.

    Never back into production — it exists here only so the harness can be caught working. The
    pedestal appears in both the numerator and the denominator, so it drags the ratio toward 1.
    """
    labels, frame = _transfection_scene(pedestal)
    background = float(np.percentile(frame, 25.0))
    return {int(label) for label in np.unique(labels) if label != 0
            and (frame[labels == label].mean() / background) >= threshold}


def test_POSITIVE_the_current_transfection_gate_is_pedestal_invariant():
    """The same cells are transfected whatever the camera adds. It is measuring the specimen."""
    assert_offset_invariant(
        lambda pedestal: _kept_by_production(pedestal),
        offsets=[0, 100, 500, 2000], truth=_TRANSFECTED, tol=0)


def test_NEGATIVE_the_harness_CATCHES_the_pedestal_inversion():
    """**On a 500-count sensor the old form called every transfected cell untransfected.**

    Reproduced here: it keeps {3,4} at pedestal 0, {4} at 100, and *nothing* from 500 up. A gate that
    decides which cells are analysed at all, answering to the camera.
    """
    with pytest.raises(FilterSensitivityError) as caught:
        assert_offset_invariant(
            lambda pedestal: _kept_by_the_OLD_ratio(pedestal),
            offsets=[0, 100, 500, 2000], truth=_TRANSFECTED, tol=0)

    assert '500' in str(caught.value), "the report must name the pedestal that broke it"


def test_the_old_ratio_loses_EVERY_cell_by_500_counts():
    """The specific, quotable failure — pinned so the harness's report can be trusted."""
    assert _kept_by_the_OLD_ratio(0) == _TRANSFECTED, (
        "the reconstruction does not match the old form at pedestal 0 — then it is not the thing "
        "that shipped, and catching it proves nothing")
    assert _kept_by_the_OLD_ratio(500) == set()
    assert _kept_by_the_OLD_ratio(2000) == set()


# ── The scale check has no validated case yet — prove it on a known-answer stand-in ───────

def test_the_SCALE_check_type_works_before_it_has_a_real_case():
    """A gate in PIXELS is a gate in microns on the microscope it was tuned on. No production case
    is validated yet, so the check is proved on an explicit stand-in — a harness that has never been
    seen to fire is not machinery, it is an intention."""
    def count_above_min_diameter(microns_per_pixel, min_diameter_px=10):
        # 12 objects, each 1.0 µm across. Tuned at 0.1 µm/px they are 10 px and pass.
        diameter_px = 1.0 / microns_per_pixel
        return 12 if diameter_px >= min_diameter_px else 0

    assert_scale_invariant(count_above_min_diameter, pixel_sizes=[0.1, 0.05],
                           truth=12, tol=0)      # finer pixels -> bigger in px -> still found

    with pytest.raises(FilterSensitivityError):
        # At 0.2 µm/px the same 1 µm object is 5 px and the gate silently excludes the population.
        assert_scale_invariant(count_above_min_diameter, pixel_sizes=[0.1, 0.2],
                               truth=12, tol=0)


# ── The registry: adding the next dangerous default should be one row ─────────────────────

@pytest.mark.parametrize('case', VALIDATED_CASES, ids=lambda c: c['id'])
def test_every_registered_case_is_DOCUMENTED_and_has_both_controls(case):
    """The scaffold increment 2 appends to. Each row must say what the failure *is* — a registry of
    parameter names with no explanation is a list, not a warning."""
    assert case['check'] in ('selection_bias', 'offset_invariance', 'scale_invariance')
    assert len(case['why']) > 40, f"{case['id']} does not explain how it inverts the result"
    assert case['good'] is not None and case['bad'] is not None, (
        f"{case['id']} needs both controls — a positive alone cannot show the harness detects "
        f"anything")


def test_the_registry_does_not_list_DEPRECATED_parameters():
    """`vpt_tools.defocus_r2_max` is deprecated and unused. A sensitivity test on it would assert
    about code no run reaches, and read as coverage."""
    assert not any('defocus_r2_max' in case['id'] for case in VALIDATED_CASES)
