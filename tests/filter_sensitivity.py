"""**A filter default that quietly inverts the result is the worst bug this codebase can have.**

It does not crash. It does not warn. It returns a number of the right magnitude, in the right units,
that a reader would accept — and it is wrong in a direction nobody can see from the output. PyCAT has
shipped two of them, both found by accident:

* ``count_molecules_single(r2_min=0.999)`` — the R² of a bleaching fit **rises with N**, so gating on
  a good fit selects for bright cells. Reported population mean **77 against a true 44**.
* ``filter_cells_by_transfection`` with the old mean/background **ratio** at 2.0 — the camera pedestal
  sits in both numerator and denominator and drags the ratio toward 1, so on a 500-count sensor
  **every transfected cell was called untransfected.**

Both are fixed. This module is the machinery that would have caught them, so the next one does not
need an accident. It is built and *proved* against those two — a positive control (the fixed default
passes) and a negative control (the old bad value is caught) for each — because a harness nobody has
seen fail is not evidence.

── The shape of the invariant ──────────────────────────────────────────────────────────────

Build data whose true answer is **known**. Vary the parameter — or the nuisance variable the answer
should be indifferent to — and check the recovered answer does not move. What separates this from an
ordinary test is that it asserts about a *sweep*, not a point: a default that is right at its own
value and catastrophic one step away is exactly the failure mode.

Three signatures, because the two known cases fail differently:

1. **Selection bias** — the gate correlates with the quantity being measured, so filtering shifts the
   population statistic. (``r2_min``.)
2. **Offset sensitivity** — the gate breaks under a camera pedestal. (The SNR ratio.)
3. **Scale sensitivity** — a gate in PIXELS silently excludes populations at a different pixel size.
   No validated case yet; the check type exists so the increment that finds one does not also have to
   invent the harness.

**The harness drives the REAL production functions.** A reimplementation of the science would only
prove the reimplementation agrees with itself.
"""

# Standard library imports
import math

# Third party imports
import numpy as np


class FilterSensitivityError(AssertionError):
    """A filter default moved the scientific answer. Raised so a test can `pytest.raises` it —
    which is how the negative controls prove this machinery actually detects an inversion."""


def sweep(run, *, param, values, nuisance=None, nuisance_values=None):
    """Call ``run`` across ``values`` (× ``nuisance_values``). Returns ``{key: answer}``.

    No assertions — this is the measurement. ``key`` is the parameter value, or ``(value, nuisance)``
    when a nuisance is swept. Kept separate from the checks so a test can *look* at the curve; a
    sweep that only ever raises teaches nothing about how the answer moves.
    """
    results = {}
    for value in values:
        if nuisance is None:
            results[value] = run(**{param: value})
        else:
            for level in (nuisance_values or []):
                results[(value, level)] = run(**{param: value, nuisance: level})
    return results


def _deviation(answer, truth):
    if truth is None:
        return float('nan')
    if isinstance(truth, (set, frozenset, list, tuple)):
        # A set-valued answer (which cells were kept): the deviation is how many differ.
        return float(len(set(answer) ^ set(truth)))
    if answer is None or (isinstance(answer, float) and math.isnan(answer)):
        return float('inf')      # "no answer" is not "the right answer"
    return abs(float(answer) - float(truth))


def _report(results, truth, tol, what):
    lines = []
    for key, answer in results.items():
        deviation = _deviation(answer, truth)
        flag = '  <-- MOVED' if deviation > tol else ''
        lines.append(f"    {key!r:>18} -> {answer!r}   (off by {deviation:g}){flag}")
    return f"{what}\n  truth = {truth!r}, tolerance = {tol:g}\n" + "\n".join(lines)


def sweep_invariance(run, *, param, values, truth, tol, invariant_to=None,
                     invariant_values=None, what='the recovered answer'):
    """Sweep, then assert the answer never moves more than ``tol`` from ``truth``.

    Raises `FilterSensitivityError` naming the values that moved it — the message *is* the finding,
    so it has to say which setting broke the science and by how much.
    """
    results = sweep(run, param=param, values=values,
                    nuisance=invariant_to, nuisance_values=invariant_values)
    moved = {k: v for k, v in results.items() if _deviation(v, truth) > tol}
    if moved:
        raise FilterSensitivityError(_report(results, truth, tol, what))
    return results


def assert_no_selection_bias(run, *, param, values, truth, tol, statistic='the population mean'):
    """**The gate must not correlate with the quantity being measured.**

    The `r2_min` shape: filtering on goodness-of-fit looks like quality control, and is a sampling
    decision. If the recovered population statistic depends on how hard you filter, the filter is
    choosing the answer.
    """
    return sweep_invariance(
        run, param=param, values=values, truth=truth, tol=tol,
        what=f'{statistic} must not depend on the {param} gate')


def assert_offset_invariant(run, *, offsets, truth, tol, param='pedestal'):
    """**The gate must measure the specimen, not the camera.**

    A constant added to every pixel carries no information. Any gate whose verdict changes when the
    pedestal does is reading the sensor.
    """
    return sweep_invariance(
        run, param=param, values=offsets, truth=truth, tol=tol,
        what='the verdict must not depend on the camera pedestal')


def assert_scale_invariant(run, *, pixel_sizes, truth, tol, param='microns_per_pixel'):
    """**A gate in PIXELS is a gate in microns on the microscope it was tuned on.**

    Same specimen, different objective, different answer — and nothing in the output says the
    population was excluded. No validated case yet; the check exists so the increment that finds one
    inherits the machinery rather than rebuilding it.
    """
    return sweep_invariance(
        run, param=param, values=pixel_sizes, truth=truth, tol=tol,
        what='the answer must not depend on the pixel size the gate was tuned at')


# ── The registry: adding the next dangerous default should be one row ─────────────────────
#
# Seeded with the two cases this harness is *proved* against. Later increments append; each entry is
# (id, check type, and how to build data whose answer is known).
#
# **`vpt_tools.defocus_r2_max` must NOT be added** — it is deprecated and unused (`vpt_tools.py`),
# so a "sensitivity test" on it would assert about code no run reaches, and read as coverage.
#
# ── Increment 2 (1.6.89): the prioritisation call, with what it rejected and why ──────────
#
# The increment was "needs prioritisation, not machinery". Prioritising meant checking the three
# candidates rather than adding all three, and **one of them does not belong**:
#
# * `segmentation local_snr/global_snr` — ADDED, as OFFSET sensitivity, not the `r2_min` shape the
#   spec guessed. The old form was `object_mean / bg_std`: the pedestal is in the numerator and not
#   the denominator, so the verdict moved with the camera (measured: the same punctum reported "SNR"
#   115 at a 500-count pedestal and 416 at 2000). Fixed in 1.6.86 — and it had survived because the
#   fix reached only the slow filter while the DEFAULT path kept the broken form.
# * `segmentation local ring geometry` — ADDED, and it is the **first validated SCALE case**. The
#   check type had machinery and no case; 1.6.87 produced one.
# * `condensate bleach_r2_min` — **NOT ADDED, and it is not an oversight.** The spec expected the
#   `r2_min` shape, but `bleach_r2` gates nothing: `has_bleaching` only picks the reported
#   `dominant_cause` label, so there is no population statistic to bias. Nor is it offset-sensitive:
#   `fit_photobleaching` fits `I(t) = I0*exp(-t/tau) + I_inf`, and I_inf absorbs a pedestal —
#   measured, r_squared = 0.9989 at pedestals 0, 100, 500 and 2000. A sensitivity test on it would
#   assert an invariant that cannot break, which is coverage, not a warning. (Different reason from
#   `defocus_r2_max` below, which is excluded for being dead — this one runs, it just is not a filter.)
#
# ── Increment 3 (1.6.131): the next prioritisation call — a LIVE default, not a fixed one ──
#
# The first four cases pin FIXED inverters. Increment 3's survey of the remaining defaults found no
# new fixed inverter — but it found a live one worth pinning, and several non-cases worth recording so
# the next pass does not re-litigate them:
#
# * `partition.client_enrichment` `background=0.0` — ADDED, OFFSET sensitivity. K = (dense-bg)/(dilute-bg)
#   is exact at any pedestal PROVIDED the offset is supplied; the default 0.0 asserts there is none, and
#   a real pedestal then sits in both terms and drags K toward 1. Measured on a TRUE K of 30:
#   30 / 15.5 / 5.83 / 2.38 at pedestals 0/100/500/2000 — a 12x error on the flagship partition metric.
#   It is WARNED (the function says so) but the wrong number is still returned, so this is the FIRST case
#   whose NEGATIVE control is the current DEFAULT rather than a removed form: the harness pins that
#   supplying the offset recovers K, and that the default is the thing that inverts it.
#
# NOT added, with reasons (record, don't re-evaluate):
# * `segmentation.min_spot_radius=2` — a live scale risk on the reported puncta count (a raw-px
#   `min_area = ceil(pi*r^2)` gate, NOT pixel-size-derived), but it is the SAME scale shape already
#   covered by `segmentation.local_ring_geometry` in the same function, it is a user-facing px control
#   rather than a hidden constant, and `_report_refinement_drops` already surfaces the drops and warns
#   to check it against the pixel size. Reported as a finding (DEV_NOTES), not added as machinery.
# * `partition.client_enrichment_per_condensate` `shell_px=5` — a fixed-px local dilute ring (scale
#   shape, already covered). A finding, not new machinery.
# * `segmentation.max_area_fraction=0.25` — SAFE by construction: it is a FRACTION of `np.sum(cell_mask)`,
#   so object and cell area both scale as pixel_size^2 and the ratio is scale-invariant. Worth pinning
#   only against a future refactor to a fixed `max_area_px`; deferred (no live danger).
# * `segmentation.kurtosis_threshold=-3.0` — INERT, not a filter: scipy Fisher (excess) kurtosis has a
#   hard floor of -2, so `kurtosis < -3.0` can never be true and the gate rejects nothing. Like
#   `bleach_r2_min`, it is documented-absent rather than tested.
# * `estimate_object_size_px_brightfield` — DEAD/unwired (explicitly "EXPERIMENTAL, NOT VALIDATED, not
#   in the batch path"); excluded for the same reason as `defocus_r2_max`. The spec's "brightfield
#   min_diameter_px class" does not reach production.
#
# ~35-110 other defaults remain. The audit's view stands: they are not equal, and the next increment is
# another prioritisation call, not a sweep.
VALIDATED_CASES = (
    {
        'id': 'molecular_counting.r2_min',
        'check': 'selection_bias',
        'why': "R^2 of a bleaching fit rises with N, so gating on fit quality selects for bright "
               "cells. r2_min=0.999 reported a population mean of 77 against a true 44.",
        'good': 0.0,
        'bad': 0.999,
    },
    {
        'id': 'ts_cellpose.filter_cells_by_transfection',
        'check': 'offset_invariance',
        'why': "The old mean/background RATIO put the camera pedestal in both numerator and "
               "denominator, dragging it toward 1. At a 500-count pedestal every transfected cell "
               "was called untransfected.",
        'good': 'contrast-to-noise (current)',
        'bad': 'mean/background ratio at 2.0 (removed)',
    },
    {
        'id': 'segmentation.local_snr_threshold',
        'check': 'offset_invariance',
        'why': "The old gate was object_mean/bg_std — the pedestal in the numerator and NOT the "
               "denominator, so the score scaled with the camera: the same punctum reported 'SNR' "
               "115 at a 500-count pedestal and 416 at 2000. Against a threshold of 1.0 it could "
               "never reject anything, so a zero-contrast noise blob was kept at any real pedestal "
               "and counted. It survived because the 1.5.416 CNR fix reached only the slow filter "
               "while the DEFAULT path (_PYCAT_REFINE_FAST) kept the broken form (fixed 1.6.86).",
        'good': 'contrast-to-noise, background-subtracted (current)',
        'bad': 'object_mean / bg_std (removed 1.6.86)',
    },
    {
        'id': 'segmentation.local_ring_geometry',
        'check': 'scale_invariance',
        'why': "The interior/background ring was a fixed 1-4px REGARDLESS of object size, so the "
               "same physical condensate was probed differently at different pixel sizes: at a "
               "finer pixel size it spans more pixels, the fixed rim sits proportionally closer to "
               "its boundary, and the ring samples the object's own halo instead of background — "
               "contrast is underestimated and REAL objects are rejected. The rim now scales with "
               "the object (0.5/0.5/1.0 x r_eq), which reproduces the old geometry exactly for a "
               "punctum (1/1/2) and fixes it for everything larger (1.6.87).",
        'good': 'radii scaled to the object (current)',
        'bad': 'fixed 1/1/2 px rim (removed 1.6.87)',
    },
    {
        'id': 'partition.client_enrichment.background',
        'check': 'offset_invariance',
        'why': "The partition coefficient K = (dense - bg) / (dilute - bg) is exact at any camera "
               "pedestal PROVIDED the offset is supplied. The DEFAULT background=0.0 asserts there is "
               "none; with a real pedestal it sits in both terms and drags K toward 1 — measured on a "
               "TRUE K of 30: 30 / 15.5 / 5.83 / 2.38 at pedestals 0/100/500/2000. A 12x error on the "
               "flagship partition metric, warned but still returned. This is the LIVE-default case: "
               "the negative control is the current default, not a removed form.",
        'good': 'background supplied (the measured camera offset)',
        'bad': 'default background=0.0 (no offset removed)',
    },
)
