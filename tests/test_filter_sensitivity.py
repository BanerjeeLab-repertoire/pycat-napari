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


# ── Case 3: segmentation local/global SNR — OFFSET SENSITIVITY ────────────────────────────
#
# The spec expected the `r2_min` (selection-bias) shape here. It is not that shape: the gate was
# `object_mean / bg_std`, with the pedestal in the numerator and NOT the denominator, so its verdict
# moved with the camera. Same family as case 2, found independently — and it survived far longer,
# because the 1.5.416 CNR fix reached only the slow filter while the DEFAULT path kept the broken
# form until 1.6.86.

_SNR_BG_SIGMA = 5.0
_SNR_CONTRAST = 40.0        # a real punctum, well clear of the noise
_SNR_TRUTH = {'real'}       # a zero-contrast blob must never be kept, at any pedestal


def _snr_scene(pedestal, seed=0):
    """Background pixels, plus the dilated-object mean for a real punctum and for a noise blob.

    The noise blob has ZERO contrast — it is background wearing a mask. No pedestal makes it real.
    """
    rng = np.random.default_rng(seed)
    bg = rng.normal(pedestal, _SNR_BG_SIGMA, 600)
    return bg, pedestal + _SNR_CONTRAST, pedestal      # bg, real_mean, noise_mean


def _kept_by_production_snr(pedestal):
    """The REAL `_snr_conditions` — the current contrast-to-noise form."""
    from pycat.toolbox.segmentation_tools import _snr_conditions

    bg, real_mean, noise_mean = _snr_scene(pedestal)
    kept = set()
    for name, mean in (('real', real_mean), ('noise', noise_mean)):
        local_rejects, global_rejects = _snr_conditions(mean, bg, bg, 1.0, 1.0)
        if not (local_rejects or global_rejects):
            kept.add(name)
    return kept


def _kept_by_the_OLD_snr_ratio(pedestal, threshold=1.0):
    """The removed `object_mean / bg_std`, reconstructed **locally**.

    Never back into production — it exists here only so the harness can be caught working.
    """
    bg, real_mean, noise_mean = _snr_scene(pedestal)
    bg_std = float(np.std(bg))
    kept = set()
    for name, mean in (('real', real_mean), ('noise', noise_mean)):
        if not ((mean / (bg_std + np.finfo(np.float32).eps)) <= threshold):
            kept.add(name)
    return kept


def test_the_MECHANISM_is_real_the_old_snr_score_tracked_the_pedestal():
    """**Why the old gate read the sensor, not the specimen.** A constant added to every pixel
    carries no information, and it multiplied the score."""
    scores = []
    for pedestal in (0.0, 500.0, 2000.0):
        bg, _real, noise_mean = _snr_scene(pedestal)
        scores.append(noise_mean / float(np.std(bg)))

    assert scores == sorted(scores) and scores[-1] > 10 * max(scores[0], 1e-9), (
        f"the pedestal did not carry the old score in this fixture ({scores}) — then the negative "
        f"control below proves nothing")


def test_POSITIVE_the_current_snr_gate_is_pedestal_invariant():
    """CNR subtracts the background first, so the verdict is the specimen's."""
    assert_offset_invariant(
        _kept_by_production_snr, offsets=[0.0, 100.0, 500.0, 2000.0],
        truth=_SNR_TRUTH, tol=0.0)


def test_NEGATIVE_the_harness_CATCHES_the_old_snr_ratio():
    """**The proof.** At a real pedestal the zero-contrast blob scores ~100 against a threshold of
    1.0 and is kept — which is why the default path counted noise for the life of the pipeline."""
    with pytest.raises(FilterSensitivityError) as caught:
        assert_offset_invariant(
            _kept_by_the_OLD_snr_ratio, offsets=[0.0, 500.0, 2000.0],
            truth=_SNR_TRUTH, tol=0.0)

    assert '500' in str(caught.value), "the report must name the pedestal that broke it"


# ── Case 4: segmentation local ring geometry — SCALE SENSITIVITY (first of its type) ───────
#
# The harness shipped `assert_scale_invariant` with no validated case: "the check type exists so the
# increment that finds one does not also have to invent the harness." 1.6.87 found one.
#
# A fixed 1-4px rim is a probe in PIXELS. The same physical condensate at a finer pixel size spans
# more pixels, so the fixed rim sits proportionally closer to its boundary and samples the object's
# own halo instead of background. Same specimen, different objective, different verdict.

_RING_DIAMETER_UM = 3.0            # one condensate, several microns across — a real one
_RING_TRUTH = {1}                  # it is real at every magnification


def _condensate_at(microns_per_pixel, size=128):
    """The SAME physical condensate, imaged at `microns_per_pixel`.

    The halo is physical too — it scales with the object, which is exactly why a fixed-pixel rim
    cannot describe both magnifications.
    """
    rng = np.random.default_rng(0)
    r_px = (_RING_DIAMETER_UM / 2.0) / microns_per_pixel
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((yy - size / 2) ** 2 + (xx - size / 2) ** 2)

    img = rng.normal(120, 4, (size, size)).astype(np.float32)
    img += (60.0 * np.exp(-((d - r_px) ** 2) / (2 * (0.35 * r_px) ** 2))).astype(np.float32)
    obj = d <= r_px
    img[obj] += 25.0

    cell = np.zeros((size, size), dtype=int)
    cell[2:-2, 2:-2] = 1
    return img.astype(np.float32), cell, obj


def _kept_by_production_ring(microns_per_pixel):
    """The REAL slow filter, at the current (scaled) geometry."""
    from pycat.toolbox.segmentation_tools import puncta_refinement_filtering_func

    img, cell, obj = _condensate_at(microns_per_pixel)
    out = puncta_refinement_filtering_func(
        img, img.copy(), obj, cell, obj.astype(int), 2,
        kurtosis_threshold=-3.0, local_snr_threshold=1.0, global_snr_threshold=1.0,
        intensity_hwhm_scale=1.17, max_area_fraction=0.25)
    return {1} if np.asarray(out).sum() > 0 else set()


def _kept_by_the_OLD_fixed_ring(microns_per_pixel):
    """The removed fixed 1/1/2 px rim, reconstructed **locally** by pinning the radii.

    Never back into production. Pinning `_local_ring_radii` is the honest reconstruction: it is the
    one function the fix introduced, so forcing its old return value restores the old behaviour
    exactly, without keeping a second copy of the filter that could drift from the real one.
    """
    from pycat.toolbox import segmentation_tools as seg

    real = seg._local_ring_radii
    seg._local_ring_radii = lambda area, cell_area: (1, 1, 2)
    try:
        return _kept_by_production_ring(microns_per_pixel)
    finally:
        seg._local_ring_radii = real


def test_the_MECHANISM_is_real_a_finer_pixel_size_makes_the_object_bigger_in_PIXELS():
    """The premise: the specimen does not change, the sampling does. If the object were the same
    pixel size at both magnifications there would be nothing to test."""
    from pycat.toolbox.segmentation_tools import _local_ring_radii

    coarse = int(_condensate_at(0.10)[2].sum())
    fine = int(_condensate_at(0.05)[2].sum())

    assert fine > 3 * coarse, f"the fixture does not rescale the object ({coarse} -> {fine} px)"
    assert (_local_ring_radii(fine, 128 * 128)
            != _local_ring_radii(coarse, 128 * 128)), (
        'the ring did not scale with the object — then this is not testing the fix')


def test_POSITIVE_the_scaled_ring_gives_the_same_verdict_at_any_pixel_size():
    """One condensate, two objectives, one answer."""
    assert_scale_invariant(
        _kept_by_production_ring, pixel_sizes=[0.10, 0.05], truth=_RING_TRUTH, tol=0.0)


def test_NEGATIVE_the_harness_CATCHES_the_fixed_ring():
    """**The first validated scale case.** With the rim pinned at its old fixed 1/1/2, the same
    physical condensate is kept at one magnification and rejected at the other — and nothing in the
    output would say the population had been excluded."""
    with pytest.raises(FilterSensitivityError) as caught:
        assert_scale_invariant(
            _kept_by_the_OLD_fixed_ring, pixel_sizes=[0.10, 0.05],
            truth=_RING_TRUTH, tol=0.0)

    assert '0.05' in str(caught.value), "the report must name the pixel size that broke it"


# ── The prioritisation call: what increment 2 deliberately did NOT add ────────────────────

def test_bleach_r2_min_is_NOT_a_filter_and_is_correctly_absent():
    """**Why `condensate.bleach_r2_min` is not in the registry, and it is not an oversight.**

    The spec expected the `r2_min` shape — a quality gate that selects which objects contribute to a
    population statistic. It is not that: `bleach_r2 >= bleach_r2_min` only sets `has_bleaching`,
    which only picks the reported `dominant_cause` label. Nothing is filtered, so there is no
    statistic to bias. Getting it wrong mislabels a diagnosis; it does not invert a number.

    Pinned as a test rather than a comment because it is a claim about the code, and code changes:
    if `has_bleaching` ever gates a population, this fails and the case should be added.
    """
    import inspect
    from pycat.toolbox import condensate_physics_tools as cpt

    src = inspect.getsource(cpt)
    uses = [l.strip() for l in src.splitlines()
            if 'has_bleaching' in l and not l.strip().startswith('#')]

    assert uses, 'has_bleaching vanished — re-check whether bleach_r2_min now gates something'
    for line in uses:
        assert ('dominant_cause' in line or line.startswith('has_bleaching =')
                or line.startswith('if has_bleaching') or line.startswith('elif has_bleaching')), (
            f"`has_bleaching` is used for something other than the reported label: {line!r}. "
            f"If it now selects a population, bleach_r2_min belongs in VALIDATED_CASES.")


def test_bleach_r2_min_is_not_offset_sensitive_either():
    """The other reason it does not belong. `fit_photobleaching` fits
    `I(t) = I0*exp(-t/tau) + I_inf`, and **I_inf absorbs a camera pedestal** — so unlike case 2 and
    case 3, this R^2 does not read the sensor. Measured, not assumed."""
    from pycat.toolbox.condensate_physics_tools import fit_photobleaching

    rng = np.random.default_rng(0)
    t = np.arange(60)
    base = 500 * np.exp(-t / 20.0) + rng.normal(0, 5, 60)

    r2 = [fit_photobleaching(base + pedestal, 1.0).get('r_squared', 0.0)
          for pedestal in (0, 100, 500, 2000)]

    assert max(r2) - min(r2) < 0.01, (
        f'bleach_r2 moved with the pedestal ({r2}) — then it IS an offset case and belongs in the '
        f'registry after all')
    assert min(r2) > 0.9, f'the fixture does not bleach convincingly ({r2})'
