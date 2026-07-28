"""**The manuscript figure-panel registry (manuscript_toolbox Part A).**

A grouped set of *panel generators* that turn PyCAT's rigor/measurement work into publication figures. Each
panel states — in plain language — exactly what data it needs, and reports whether that data is present *now*.
The UX rule the spec is built around: **a panel whose data is absent is greyed with its requirement as the
tooltip — never a dead button, always an instruction.** So a panel whose composer or data does not yet exist
does not error; it simply reports ``available(context) == False`` and shows what to run first.

This module is **Qt-free and headless** — it composes the already-built figure/analysis machinery
(``comparative_figures``, ``reliability``, ``calibration``, …) through the canonical layer, so every panel
shares fonts, palettes, and export. ``context`` is a plain mapping the caller (a live session, or a test)
fills with the session's data; each panel reads only what it declares.
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional

# Long-table columns that are NOT experimental conditions (the fixed stem/core + identity plumbing), so the
# rest are the condition fields a comparison groups by. Mirrors comparative_figures_ui._condition_fields,
# kept Qt-free here.
_NON_CONDITION_COLS = frozenset({
    "measurement", "value", "units", "unit", "image_stem", "object_id", "frame",
    "_pycat_entity_id", "_pycat_layer_id",
})


@dataclasses.dataclass(frozen=True)
class FigurePanel:
    """One manuscript figure/table generator. ``data_requirement`` is the heart of it — the plain-language
    instruction shown (as the tooltip) when the panel is greyed for want of data. ``generate(context)`` builds
    the panel from that data; ``available(context)`` reports whether the data is present now."""
    key: str                       # 'fig3_phenotyping'
    title: str
    figure_role: str               # 'main' | 'supplementary'
    data_requirement: str          # plain language: exactly what to run first
    tooltip: str
    produces: str                  # 'figure' | 'table' | 'recorded_demo'
    generate: Callable             # (context) -> Figure / DataFrame / …
    available: Callable            # (context) -> bool


# ── data helpers (Qt-free) ────────────────────────────────────────────────────────────────────────────

def _long_table(context):
    """The consolidated long-format table from the context, or ``None``. Accepts a DataFrame under
    ``'long_table'`` (the ``consolidated_long`` a batch produces)."""
    df = (context or {}).get("long_table")
    try:
        import pandas as pd
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:      # broad-ok: optional_probe — no pandas / not a frame → no long table
        pass
    return None


def _condition_fields(df):
    return [c for c in df.columns if c not in _NON_CONDITION_COLS]


def _phenotyping_columns(context, df):
    """Resolve (measurement, condition_col, replicate_col) for the comparison, from explicit context hints or
    sensible defaults (first measurement; first condition field; ``image_stem`` as the replicate unit)."""
    ctx = context or {}
    measurement = ctx.get("measurement")
    if measurement is None and "measurement" in df.columns:
        vals = df["measurement"].dropna().unique()
        measurement = vals[0] if len(vals) else None
    conds = _condition_fields(df)
    condition_col = ctx.get("condition_col") or (conds[0] if conds else None)
    replicate_col = ctx.get("replicate_col") or ("image_stem" if "image_stem" in df.columns else condition_col)
    return measurement, condition_col, replicate_col


# ── panel: Fig 3 — comparative phenotyping (fully wired) ──────────────────────────────────────────────

def _fig3_available(context):
    df = _long_table(context)
    if df is None or "measurement" not in df.columns or "value" not in df.columns:
        return False
    measurement, condition_col, replicate_col = _phenotyping_columns(context, df)
    return bool(measurement is not None and condition_col is not None and replicate_col is not None)


def _fig3_generate(context):
    df = _long_table(context)
    measurement, condition_col, replicate_col = _phenotyping_columns(context, df)
    from pycat.utils.comparative_figures import condition_comparison_figure
    return condition_comparison_figure(df, measurement, condition_col=condition_col,
                                       replicate_col=replicate_col)


# ── panel: Fig 1 — QC pipeline (the QC sub-panel is wired; the schematic stays a template slot) ───────

#: Acquisition parameters ``run_full_qc`` uses to decide which checks apply — passed through from the context
#: when present (a check that lacks its input is reported ``na``, never guessed).
_QC_PASSTHROUGH = ("pixel_um", "na", "wavelength_nm", "frame_interval_s", "process_timescale_s",
                   "n_channels", "is_zstack", "n_source_frames", "modality", "line_time_s")


def _qc_image(context):
    """The sample image the QC panel assesses, from ``context['qc_image']`` — a 2-D frame or a (T/Z, H, W)
    stack — or ``None``."""
    img = (context or {}).get("qc_image")
    try:
        import numpy as np
        a = np.asarray(img)
        if a.ndim in (2, 3) and a.size:
            return a
    except Exception:      # broad-ok: optional_probe — a non-array qc_image just greys the panel
        pass
    return None


def _fig1_available(context):
    return _qc_image(context) is not None


def _fig1_generate(context):
    """The QC sub-panel: run every applicable acquisition-QC check on the sample image and return the report as
    a table (name / status / value / unit / headline)."""
    import pandas as pd
    from pycat.toolbox.data_qc.runner import run_full_qc
    a = _qc_image(context)
    kwargs = {k: context[k] for k in _QC_PASSTHROUGH if context and context.get(k) is not None}
    return pd.DataFrame(run_full_qc(a, **kwargs))


# ── panel: Fig 2 — benchmark / validation (Dice vs ground truth, wired to benchmark_tools) ────────────

def _fig2_scores(context):
    """``(method_name, dice)`` for every candidate scored against a ground truth, or an empty list. Reads
    ``context['benchmark_results']`` — the dict :func:`benchmark_tools.run_benchmark` returns in VALIDATION
    mode (a named ground truth). A comparison-mode result (no ground truth) has no per-method Dice, so the
    panel greys — validation needs ground-truth masks, and the panel never invents them."""
    res = (context or {}).get("benchmark_results")
    if not isinstance(res, dict) or not res.get("ground_truth"):
        return []
    scores = []
    for cand in res.get("candidates", []):
        vg = cand.get("vs_ground_truth") if isinstance(cand, dict) else None
        if not isinstance(vg, dict):
            continue
        try:
            dice = float(vg.get("dice"))
        except (TypeError, ValueError):
            continue
        if dice == dice:                       # exclude NaN (a candidate that scored nothing)
            scores.append((str(cand.get("name", "?")), dice))
    return scores


def _fig2_available(context):
    return len(_fig2_scores(context)) > 0


def _fig2_generate(context):
    """The validation figure: each candidate method's pixel-Dice overlap against the ground-truth mask,
    rendered through the canonical FigureSpec (F1/IoU are also computed — they live in the benchmark table)."""
    from pycat.utils.figure_spec import FigureData, FigureSpec, render
    scores = _fig2_scores(context)
    groups = tuple(name for name, _ in scores)
    fig_data = FigureData(measurement="segmentation_dice", groups=groups,
                          values_by_group={name: [dice] for name, dice in scores}, x_label="method")
    spec = FigureSpec(title="Segmentation accuracy vs ground truth",
                      y_label="Dice (vs ground truth)", y_limits=(0.0, 1.0), annotate_n=False)
    return render(fig_data, spec)


# ── panel: Supp — reliability / rigor (wired) ─────────────────────────────────────────────────────────

def _reliability_available(context):
    scored = (context or {}).get("reliability_scored")
    return bool(scored)


def _reliability_generate(context):
    scored = (context or {}).get("reliability_scored")
    from pycat.utils.reliability import reliability_report_section
    return reliability_report_section(scored)


# ── panel: Supp — calibrated thermodynamics (ΔG) (wired) ──────────────────────────────────────────────

def _thermo_available(context):
    ctx = context or {}
    return bool(ctx.get("calibration_curve") is not None
                and ctx.get("c_dense") is not None and ctx.get("c_dilute") is not None
                and ctx.get("temperature_K") is not None)


def _thermo_generate(context):
    ctx = context or {}
    from pycat.utils.calibration import delta_g_transfer
    return delta_g_transfer(ctx["c_dense"], ctx["c_dilute"], ctx["temperature_K"])


# ── panel: Supp — runtime by method (Part C: measured, not asserted) ──────────────────────────────────

def _runtime_scores(context):
    """``(method_name, runtime_s)`` for every candidate with a recorded runtime, or an empty list. Reads the
    same ``context['benchmark_results']`` Fig 2 does — a candidate's ``runtime_s`` comes from ``basic_metrics``
    whenever the method actually ran (an *external*/uploaded mask has ``runtime_s is None`` and is skipped —
    there is no time to report for a pre-computed result, and the panel never invents one)."""
    res = (context or {}).get("benchmark_results")
    if not isinstance(res, dict):
        return []
    scores = []
    for cand in res.get("candidates", []):
        if not isinstance(cand, dict):
            continue
        rt = cand.get("runtime_s")
        try:
            seconds = float(rt)
        except (TypeError, ValueError):
            continue                           # None (external) or non-numeric → no time to report
        if seconds == seconds:                 # exclude NaN
            scores.append((str(cand.get("name", "?")), seconds))
    return scores


def _runtime_available(context):
    return len(_runtime_scores(context)) > 0


def _runtime_generate(context):
    """The performance figure (Part C — 'generate the graph rather than the assertion'): each method's measured
    runtime, rendered through the canonical FigureSpec. A claim like 'fast' becomes a number on an axis."""
    from pycat.utils.figure_spec import FigureData, FigureSpec, render
    scores = _runtime_scores(context)
    groups = tuple(name for name, _ in scores)
    fig_data = FigureData(measurement="runtime_s", groups=groups,
                          values_by_group={name: [seconds] for name, seconds in scores}, x_label="method")
    spec = FigureSpec(title="Runtime by method (measured)", y_label="runtime (s)", annotate_n=False)
    return render(fig_data, spec)


#: The registered panels, in the manuscript's figure order. A panel is shown greyed (its ``data_requirement``
#: as the tooltip) whenever ``available(context)`` is False — never a dead button.
_PANELS = (
    FigurePanel(
        key="fig1_qc", title="Fig 1 — QC pipeline", figure_role="main",
        data_requirement="Provide a sample image (a 2-D frame or a stack) as 'qc_image'; the schematic half is a template slot.",
        tooltip="Runs every applicable acquisition-QC check on a sample image and tabulates the report (the QC sub-panel).",
        produces="table", generate=_fig1_generate, available=_fig1_available),
    FigurePanel(
        key="fig2_benchmark", title="Fig 2 — benchmark / validation", figure_role="main",
        data_requirement="Run the benchmark in validation mode (a named ground-truth mask) on at least one segmentation case; put the result dict in the context as 'benchmark_results'.",
        tooltip="Each method's pixel-Dice overlap against the ground-truth mask (F1/IoU are in the benchmark table).",
        produces="figure", generate=_fig2_generate, available=_fig2_available),
    FigurePanel(
        key="fig3_phenotyping", title="Fig 3 — comparative phenotyping", figure_role="main",
        data_requirement="Load a consolidated_long.csv (a batch's long-format table) with a measurement, a condition field, and per-image replicates.",
        tooltip="Replicate-aware condition comparison (object cloud + replicate means + honest stats) via the canonical superplot.",
        produces="figure", generate=_fig3_generate, available=_fig3_available),
    FigurePanel(
        key="supp_reliability", title="Supp — reliability / rigor", figure_role="supplementary",
        data_requirement="Score at least one measurement with the reliability index (needs the QC / sensitivity / calibration inputs it grades).",
        tooltip="Measurement Reliability Index per measurement, as a rigor table.",
        produces="table", generate=_reliability_generate, available=_reliability_available),
    FigurePanel(
        key="supp_thermodynamics", title="Supp — calibrated thermodynamics (ΔG)", figure_role="supplementary",
        data_requirement="Build a calibration curve and supply dense/dilute concentrations + temperature (Partition → concentration → ΔG).",
        tooltip="Partition → concentration → ΔG of transfer from the calibration curve.",
        produces="table", generate=_thermo_generate, available=_thermo_available),
    FigurePanel(
        key="supp_runtime", title="Supp — runtime by method", figure_role="supplementary",
        data_requirement="Run the benchmark with at least one method that executes (not an uploaded/external mask); its runtime is recorded in 'benchmark_results'.",
        tooltip="Each method's measured runtime — the performance claim as a number on an axis, not an assertion.",
        produces="figure", generate=_runtime_generate, available=_runtime_available),
)


def manuscript_panels():
    """The registered manuscript figure panels, in figure order."""
    return list(_PANELS)


def panel_is_available(panel: FigurePanel, context) -> bool:
    """Whether ``panel`` can generate from ``context`` right now — non-gating: any error in a panel's own
    availability check reports False (greyed) rather than propagating."""
    try:
        return bool(panel.available(context))
    except Exception:      # broad-ok: optional_probe — a panel's availability probe must never break the gallery
        return False


def available_panels(context):
    """The subset of panels whose data is present now (the ones that would render, not grey)."""
    return [p for p in _PANELS if panel_is_available(p, context)]
