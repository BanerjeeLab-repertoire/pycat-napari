"""**The manuscript report generator (manuscript_toolbox) — headless generate-to-disk.**

`generate_manuscript_report` iterates the panel registry, generates every panel the data supports, and saves
each to a folder (figures → PNG, tables → CSV / text), returning a manifest. These pin: a partial context
generates exactly the panels it can and greys the rest with their requirement (never a dead entry), the files
land on disk, and one panel's failure is recorded without aborting the report.
"""
import numpy as np
import pandas as pd
import pytest

from pycat.toolbox.manuscript.report import generate_manuscript_report, report_summary

pytestmark = pytest.mark.base      # composes the manuscript panels (matplotlib/pandas/scipy)


def _long_table():
    rng = np.random.default_rng(0)
    rows = []
    for cond, centre in (("control", 10.0), ("drug", 14.0)):
        for rep in range(3):
            for _ in range(6):
                rows.append({"measurement": "area_um2", "value": float(centre + rng.normal(0, 1)),
                             "condition": cond, "image_stem": f"{cond}_img{rep}"})
    return pd.DataFrame(rows)


def test_report_generates_the_supported_panels_and_greys_the_rest(tmp_path):
    ctx = {"long_table": _long_table(),
           "qc_image": np.random.default_rng(1).normal(500, 40, (48, 48)).clip(0).astype(np.uint16)}
    manifest = generate_manuscript_report(ctx, tmp_path)

    by_key = {e["key"]: e for e in manifest}
    # Fig 3 (phenotyping) → a PNG; Fig 1 (QC) → a CSV table; both land on disk
    assert by_key["fig3_phenotyping"]["status"] == "generated"
    assert (tmp_path / "fig3_phenotyping.png").exists()
    assert by_key["fig1_qc"]["status"] == "generated"
    assert (tmp_path / "fig1_qc.csv").exists()

    # Fig 2 (benchmark) has no composer/data → greyed with its requirement, never a dead entry
    assert by_key["fig2_benchmark"]["status"] == "greyed"
    assert by_key["fig2_benchmark"]["reason"].strip()
    # the reliability/thermo supp panels lack their inputs here → also greyed
    assert by_key["supp_reliability"]["status"] == "greyed"


def test_report_on_empty_context_greys_everything_and_writes_nothing(tmp_path):
    manifest = generate_manuscript_report({}, tmp_path)
    assert manifest and all(e["status"] == "greyed" for e in manifest)
    assert all(e["reason"].strip() for e in manifest)          # every greyed panel says what it needs
    assert not any(tmp_path.iterdir())                          # no files produced


def test_a_panel_that_raises_is_recorded_not_fatal(tmp_path, monkeypatch):
    # Force Fig 3's generator to raise (its lazy import of condition_comparison_figure); the report must still
    # complete the other panels.
    def _boom(*a, **k):
        raise RuntimeError("synthetic generate failure")

    monkeypatch.setattr("pycat.utils.comparative_figures.condition_comparison_figure", _boom)
    ctx = {"long_table": _long_table(),
           "qc_image": np.random.default_rng(2).normal(500, 40, (32, 32)).clip(0).astype(np.uint16)}
    manifest = generate_manuscript_report(ctx, tmp_path)

    by_key = {e["key"]: e for e in manifest}
    assert by_key["fig3_phenotyping"]["status"] == "error"     # recorded, with a reason
    assert "synthetic generate failure" in by_key["fig3_phenotyping"]["reason"]
    assert by_key["fig1_qc"]["status"] == "generated"          # the rest of the report still ran


def test_report_summary_is_one_line_per_panel(tmp_path):
    manifest = generate_manuscript_report({"long_table": _long_table()}, tmp_path)
    summary = report_summary(manifest)
    assert len(summary.splitlines()) == len(manifest)
    assert "fig3_phenotyping" in summary
