"""**Does my segmentation actually work on MY data? — answered with the user's own controls.**

`benchmark_tools` scores candidates against a ground truth *within one image*. It cannot answer the
question every microscopist's reviewer asks: does the method detect the objects in a **positive control**
(a sample known to contain them) *and* detect **nothing** in a **negative control** (untransfected,
no-primary, dye-only)? A segmentation can score beautifully on ground truth and still fire on empty
fields — and the false-positive rate on a matched negative control is the number that tells a reviewer the
detections are real.

This sweeps one method across a parameter grid on **both** controls with identical settings, and
recommends the operating point that **maximizes positive detection subject to the negative control staying
near zero** — not the point that maximizes detections outright. When *no* setting separates the two, it
**refuses with a stated reason**: "no parameter set distinguishes your positive from your negative control"
is an extremely valuable finding — it means the assay, not the software, needs work. Returning a least-bad
setting as if it were usable would launder an assay problem into a software recommendation, so it is never
done.

Scoring reuses `benchmark_tools` (`run_candidate`, `basic_metrics`) rather than a parallel implementation
that would drift. Counts are density-normalized (objects per µm²) through a real pixel size so controls of
different field size are comparable — and the pixel-size gate is respected: a density in objects-per-pixel²
is not a scientific quantity, so without a real pixel size the density is left NaN, never faked to 1.0.
"""
from __future__ import annotations

import dataclasses
import warnings

import numpy as np


# ── Verdict thresholds — where 'usable' / 'marginal' / 'unusable' fall ──────────────────────────
# false_positive_rate = negative detections (over the declared expected count) as a fraction of positive
# detections; separation ∈ [-1, 1] is how cleanly the two counts are distinguished (1 = clean, 0 = same).
_USABLE_FPR = 0.05         # a "<1–5% detections in the negative control" operating point
_USABLE_SEP = 0.80
_MARGINAL_FPR = 0.20
_MARGINAL_SEP = 0.50


@dataclasses.dataclass(frozen=True)
class ControlResult:
    """One parameter setting scored across the matched positive/negative pair."""
    method: str
    params: dict
    n_positive: int                  # objects found in the positive control
    n_negative: int                  # objects found in the negative control  ← should be ~expected
    false_positive_rate: float       # negative excess as a fraction of positive detection (→ 0 is good)
    positive_density: float          # objects per µm² (NaN without a real pixel size — never faked)
    separation: float                # how cleanly the two are distinguished, [-1, 1]
    verdict: str                     # 'usable' | 'marginal' | 'unusable'
    reason: str                      # the stated reason for the verdict — never a bare label


def _field_area_px(image) -> float:
    """The spatial pixel count of a 2D field (product of the two spatial dims)."""
    arr = np.asarray(image)
    if arr.ndim >= 2:
        return float(arr.shape[-1] * arr.shape[-2])
    return float(arr.size)


def _count_objects(mask) -> int:
    """Object count via the SAME machinery benchmark_tools uses — no parallel scoring to drift."""
    from pycat.toolbox.benchmark_tools import _labelled, basic_metrics
    return int(basic_metrics(_labelled(mask), None)['n_objects'])


def _verdict(n_positive, false_positive_rate, separation):
    if n_positive == 0:
        return 'unusable', 'no objects detected in the positive control — the method finds nothing here'
    if false_positive_rate <= _USABLE_FPR and separation >= _USABLE_SEP:
        return 'usable', ('strong detection in the positive control with a near-empty negative control '
                          f'(false-positive rate {false_positive_rate:.0%})')
    if false_positive_rate <= _MARGINAL_FPR and separation >= _MARGINAL_SEP:
        return 'marginal', ('positive detection, but the negative control fires enough to warrant caution '
                            f'(false-positive rate {false_positive_rate:.0%})')
    return 'unusable', ('the negative control fires nearly as often as the positive — no clean separation '
                        f'(false-positive rate {false_positive_rate:.0%})')


# ── Acquisition comparability — mismatched exposure/gain/laser invalidates the comparison ───────
def check_controls_comparable(positive_metadata, negative_metadata):
    """Were the two controls acquired comparably? Returns ``(comparable: bool, reason: str)``.

    An intensity-threshold method compared across mismatched exposure/gain/laser produces a meaningless
    verdict — brighter acquisition detects more, and the difference is the acquisition, not the biology.
    Reuses the calibration module's `AcquisitionFingerprint` and its tolerances (the same notion of "the
    same acquisition"). A field present-and-different beyond tolerance is a mismatch; a field absent on
    either side is "cannot verify", reported but not fatal (it is a warning, not a block)."""
    from pycat.utils.calibration import (AcquisitionFingerprint, _relclose,
                                         _EXPOSURE_TOL, _GAIN_TOL, _LASER_TOL)
    pos = AcquisitionFingerprint.from_metadata(positive_metadata or {})
    neg = AcquisitionFingerprint.from_metadata(negative_metadata or {})

    mismatches = []
    for name, pv, nv, tol in (('exposure', pos.exposure_s, neg.exposure_s, _EXPOSURE_TOL),
                              ('gain', pos.gain, neg.gain, _GAIN_TOL),
                              ('laser power', pos.laser_power, neg.laser_power, _LASER_TOL)):
        near = _relclose(pv, nv, tol)
        if near is False:
            mismatches.append(f"{name} differs ({pv} vs {nv})")
    if mismatches:
        return False, ("the two controls were NOT acquired comparably: " + "; ".join(mismatches)
                       + " — an intensity-based comparison across these is meaningless")
    return True, "acquisition matches (or is not recorded) — controls are comparable"


def validate_against_controls(positive_image, negative_image, method, param_grid, *,
                             microns_per_px=None, expected_negative=0,
                             positive_metadata=None, negative_metadata=None):
    """Sweep ``method`` across ``param_grid`` on BOTH controls with identical settings; return a
    per-setting summary as a DataFrame.

    Parameters
    ----------
    positive_image, negative_image : 2D arrays — the matched positive/negative control fields.
    method : callable ``(image, **params) -> mask`` (binary or labelled). The SAME method on both.
    param_grid : list of param dicts — one setting per entry.
    microns_per_px : real pixel size. Density is computed only with this; without it density is NaN
        (never faked to 1.0 — the pixel-size gate: objects-per-pixel² is not a scientific quantity).
    expected_negative : the count the negative control is EXPECTED to contain (default 0). Negative
        controls are not always empty — autofluorescence or a low baseline is legitimate — so false
        positives are counted as the EXCESS over this declared count, not the raw negative count.
    positive_metadata, negative_metadata : acquisition metadata dicts. If both are given and the two
        controls were not acquired comparably, a loud warning is issued (see `check_controls_comparable`).

    Returns a DataFrame (one row per setting) with the `ControlResult` fields. The `ControlResult`
    objects are stashed on ``df.attrs['control_results']`` for `recommend_parameters`, and any
    acquisition-comparability warning on ``df.attrs['acquisition_warning']``.
    """
    import pandas as pd

    # Acquisition comparability — warn loudly BEFORE producing numbers that a mismatch would invalidate.
    acq_warning = None
    if positive_metadata is not None and negative_metadata is not None:
        comparable, reason = check_controls_comparable(positive_metadata, negative_metadata)
        if not comparable:
            acq_warning = reason
            warnings.warn("Control acquisition mismatch — " + reason, stacklevel=2)

    method_name = getattr(method, '__name__', 'method')
    pos_area_um2 = (_field_area_px(positive_image) * float(microns_per_px) ** 2) if microns_per_px else None

    results = []
    for params in param_grid:
        n_pos = _count_objects(method(positive_image, **params))
        n_neg = _count_objects(method(negative_image, **params))

        neg_excess = max(0, n_neg - int(expected_negative))
        false_positive_rate = neg_excess / n_pos if n_pos > 0 else (1.0 if neg_excess > 0 else 0.0)
        # separation ∈ [-1, 1]: 1 when the negative (net of expected) is empty and the positive is not.
        denom = n_pos + neg_excess
        separation = (n_pos - neg_excess) / denom if denom > 0 else 0.0
        density = (n_pos / pos_area_um2) if pos_area_um2 else float('nan')

        verdict, why = _verdict(n_pos, false_positive_rate, separation)
        results.append(ControlResult(
            method=method_name, params=dict(params), n_positive=n_pos, n_negative=n_neg,
            false_positive_rate=float(false_positive_rate), positive_density=float(density),
            separation=float(separation), verdict=verdict, reason=why))

    rows = []
    for r in results:
        row = {'method': r.method}
        row.update(r.params)                      # the swept parameter(s) as columns
        row.update({'n_positive': r.n_positive, 'n_negative': r.n_negative,
                    'false_positive_rate': r.false_positive_rate,
                    'positive_density': r.positive_density, 'separation': r.separation,
                    'verdict': r.verdict, 'reason': r.reason})
        rows.append(row)
    df = pd.DataFrame(rows)
    df.attrs['control_results'] = results
    df.attrs['acquisition_warning'] = acq_warning
    df.attrs['expected_negative'] = int(expected_negative)
    return df


def _as_control_results(results):
    """Accept either a DataFrame from `validate_against_controls` or a list of `ControlResult`."""
    if isinstance(results, (list, tuple)):
        return list(results)
    stashed = getattr(results, 'attrs', {}).get('control_results')
    if stashed is not None:
        return list(stashed)
    raise TypeError("recommend_parameters needs the DataFrame from validate_against_controls "
                    "(carrying attrs['control_results']) or a list of ControlResult")


def recommend_parameters(results, *, max_false_positive_rate=_USABLE_FPR):
    """The scientific core: return the setting that **maximizes positive detection subject to the negative
    control staying near zero** — NOT the setting with the most detections outright.

    Eligible settings are those that detect something in the positive control (``n_positive > 0``) AND keep
    the negative control near zero (``false_positive_rate <= max_false_positive_rate``). Among those, the
    one with the highest positive density (or count, when density is unavailable) is returned.

    **If no setting qualifies, returns ``None`` and warns with the reason.** "No parameter set distinguishes
    your positive from your negative control" is a real, valuable finding about the assay — a least-bad
    setting is never returned in its place, because that would present an assay problem as a usable result.
    """
    rows = _as_control_results(results)
    eligible = [r for r in rows
                if r.n_positive > 0 and r.false_positive_rate <= max_false_positive_rate]
    if not eligible:
        warnings.warn(
            "No parameter set distinguishes your positive control from your negative control: every "
            f"setting either detected nothing in the positive control or fired in the negative control "
            f"above the {max_false_positive_rate:.0%} false-positive threshold. This points to the "
            "ASSAY, not the software — the controls do not separate, and no recommendation can be made.",
            stacklevel=2)
        return None

    def _score(r):
        return r.positive_density if r.positive_density == r.positive_density else float(r.n_positive)

    return max(eligible, key=_score)


# ── Part B — the validation report (DataFrame is above; the figure is here) ─────────────────────
def _varying_param_key(results_df):
    """The single swept parameter's column name — the one param that takes >1 numeric value."""
    reserved = {'method', 'n_positive', 'n_negative', 'false_positive_rate',
                'positive_density', 'separation', 'verdict', 'reason'}
    for col in results_df.columns:
        if col in reserved:
            continue
        vals = results_df[col]
        try:
            if vals.nunique() > 1 and np.issubdtype(np.asarray(vals).dtype, np.number):
                return col
        except TypeError:
            continue
    return None


def control_report_figure(results_df, recommended=None, param_key=None, ax=None):
    """A supplementary-figure artifact: detections vs the swept parameter for BOTH controls on one axis,
    with the recommended operating point marked and its separation stated.

    This is the figure a Methods/supplement paragraph refers to — *"segmentation parameters were chosen to
    maximize detection in positive controls while yielding <1% detections in matched negative controls."*
    """
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    key = param_key or _varying_param_key(results_df)
    fig = ax.figure if ax is not None else plt.figure(figsize=(6, 4))
    ax = ax or fig.add_subplot(111)

    if key is not None:
        order = results_df.sort_values(key)
        x = order[key].to_numpy()
        ax.plot(x, order['n_positive'].to_numpy(), '-o', color='#2471a3', label='positive control')
        ax.plot(x, order['n_negative'].to_numpy(), '--s', color='#c0392b', label='negative control')
        ax.set_xlabel(str(key))
    else:                                          # single setting (or no numeric sweep): a bar pair
        ax.bar([0, 1], [results_df['n_positive'].iloc[0], results_df['n_negative'].iloc[0]],
               color=['#2471a3', '#c0392b'])
        ax.set_xticks([0, 1]); ax.set_xticklabels(['positive', 'negative'])

    ax.set_ylabel('objects detected')
    title = 'Positive vs negative control detection'
    if recommended is not None:
        if key is not None and key in recommended.params:
            ax.axvline(recommended.params[key], color='#27ae60', linestyle=':', linewidth=2,
                       label='recommended operating point')
        title += (f'\nrecommended: {recommended.params} — {recommended.verdict} '
                  f'(separation {recommended.separation:.2f}, '
                  f'false-positive rate {recommended.false_positive_rate:.0%})')
    ax.set_title(title, fontsize=9, loc='left')
    ax.legend(loc='best', fontsize=8, frameon=False)
    fig.tight_layout()
    return fig
