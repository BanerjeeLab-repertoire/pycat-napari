"""**The manuscript figure-panel registry (manuscript_toolbox Part A).**

Pins the mechanism: the registry lists the manuscript panels; every panel exposes a plain-language
``data_requirement`` so a greyed one is an instruction, never a dead button; availability is data-driven and
non-gating; and the one fully-wired main panel — Fig 3 comparative phenotyping — generates a real figure from a
consolidated long table end-to-end (proving the composition, exactly as the spec intends: assembly of the
already-tested machinery).
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.manuscript.panels import (
    manuscript_panels, panel_is_available, available_panels, FigurePanel)

pytestmark = pytest.mark.base      # composes comparative_figures (matplotlib/pandas/scipy) → the fuller lane


def _synthetic_long_table(seed=0):
    """A consolidated long-format table: 2 conditions x 3 replicate images x 6 objects, one measurement."""
    rng = np.random.default_rng(seed)
    rows = []
    for cond, centre in (("control", 10.0), ("drug", 14.0)):
        for rep in range(3):
            stem = f"{cond}_img{rep}"
            for _ in range(6):
                rows.append({"measurement": "area_um2", "value": float(centre + rng.normal(0, 1.0)),
                             "condition": cond, "image_stem": stem})
    return pd.DataFrame(rows)


def test_registry_lists_the_manuscript_panels_in_figure_order():
    panels = manuscript_panels()
    keys = [p.key for p in panels]
    assert keys == ["fig1_qc", "fig2_benchmark", "fig3_phenotyping",
                    "supp_reliability", "supp_thermodynamics"]
    assert all(isinstance(p, FigurePanel) for p in panels)


def test_every_panel_states_what_it_needs_so_a_greyed_one_is_an_instruction():
    for p in manuscript_panels():
        assert p.data_requirement.strip(), f"{p.key} has no data_requirement (would be a dead button)"
        assert p.tooltip.strip()
        assert p.figure_role in ("main", "supplementary")
        assert p.produces in ("figure", "table", "recorded_demo")


def test_with_no_data_every_panel_is_greyed():
    ctx = {}
    assert available_panels(ctx) == []
    for p in manuscript_panels():
        assert panel_is_available(p, ctx) is False


def test_panel_availability_is_non_gating_on_a_malformed_context():
    # a long_table that is not a DataFrame must not raise — the gallery just greys the panel
    ctx = {"long_table": "not a frame", "reliability_scored": None}
    assert panel_is_available(manuscript_panels()[2], ctx) is False   # fig3


def test_fig3_comparative_phenotyping_generates_a_figure_end_to_end():
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.figure import Figure

    ctx = {"long_table": _synthetic_long_table()}
    fig3 = next(p for p in manuscript_panels() if p.key == "fig3_phenotyping")

    assert panel_is_available(fig3, ctx) is True
    assert fig3 in available_panels(ctx)

    fig = fig3.generate(ctx)
    try:
        assert isinstance(fig, Figure)
        assert fig.axes, "the comparison figure has no axes"
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)


def test_fig3_greys_without_a_measurement_or_condition():
    fig3 = next(p for p in manuscript_panels() if p.key == "fig3_phenotyping")
    # a frame with value but no 'measurement' column and no condition field → not enough to compare
    bare = pd.DataFrame({"value": [1.0, 2.0], "image_stem": ["a", "b"]})
    assert panel_is_available(fig3, {"long_table": bare}) is False


def test_supp_thermodynamics_generates_a_delta_g_when_its_inputs_are_present():
    thermo = next(p for p in manuscript_panels() if p.key == "supp_thermodynamics")
    assert panel_is_available(thermo, {}) is False
    ctx = {"calibration_curve": object(), "c_dense": 50.0, "c_dilute": 2.0, "temperature_K": 298.0}
    assert panel_is_available(thermo, ctx) is True
    dg = thermo.generate(ctx)
    assert np.isfinite(float(dg.value))    # a ΔG-of-transfer Parameter (value + units + 1σ)
    assert dg.units == "kcal/mol"


def test_fig1_qc_tabulates_the_acquisition_qc_report_from_a_sample_image():
    fig1 = next(p for p in manuscript_panels() if p.key == "fig1_qc")
    assert panel_is_available(fig1, {}) is False                 # no sample image → greyed
    assert panel_is_available(fig1, {"qc_image": "not an array"}) is False

    rng = np.random.default_rng(1)
    img = rng.normal(500, 40, (48, 48)).clip(0).astype(np.uint16)
    ctx = {"qc_image": img, "pixel_um": 0.1, "na": 1.4, "wavelength_nm": 520}
    assert panel_is_available(fig1, ctx) is True

    table = fig1.generate(ctx)
    assert isinstance(table, pd.DataFrame) and not table.empty
    assert {"name", "status"} <= set(table.columns)              # a QC check table (name + verdict per row)
    assert set(table["status"]) <= {"good", "warn", "bad", "na", "info"}


def test_supp_reliability_gates_on_scored_measurements():
    rel = next(p for p in manuscript_panels() if p.key == "supp_reliability")
    assert panel_is_available(rel, {}) is False
    assert panel_is_available(rel, {"reliability_scored": ["something"]}) is True
