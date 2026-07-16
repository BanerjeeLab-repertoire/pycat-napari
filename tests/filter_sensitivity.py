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
# Next up (increment 2, needs prioritising first): the select-for-the-measured-quantity class —
# segmentation's `local_snr_threshold` / `global_snr_threshold` (~10 sites) and condensate
# `bleach_r2_min`. They share the `r2_min` shape: a quality gate on the thing being measured.
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
)
