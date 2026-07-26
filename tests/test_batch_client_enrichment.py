"""**Headless calibrated concentration in the batch (reliability_index roadmap).**

`replay_client_enrichment` replaces the interactive-only `client_enrichment` skip-stub with a real batch step:
given a calibration curve, it converts a droplet field's partition to real concentrations + K_p + ΔG **through
the validity gate**, writes the calibrated columns to a per-image CSV, and stashes the verdict for the
reliability index. These pin: the calibrated path (a matching curve → concentrations written + verdict
stashed), the safety gate (a mismatched curve → the verdict is written but NO concentration is fabricated), and
that it is non-breaking (no curve → a documented no-op, exactly as the old stub).
"""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from types import SimpleNamespace

from pycat.batch.steps.invitro_steps import replay_client_enrichment
from pycat.utils.calibration import CalibrationCurve, AcquisitionFingerprint

pytestmark = pytest.mark.base      # scientific stack (partition_enrichment / calibration)


def _droplet_field():
    """A 32x32 field: dilute background at 600 counts, one 10x10 dense droplet at 3500 (true K ≈ 5.83)."""
    img = np.full((32, 32), 600.0, dtype=np.float32)
    img[11:21, 11:21] = 3500.0
    mask = np.zeros((32, 32), dtype=np.int32)
    mask[11:21, 11:21] = 1
    return img, mask


def _curve(channel="emission:520"):
    fp = AcquisitionFingerprint(exposure_s=0.1, channel=channel, pixel_size_um=0.1)
    return CalibrationCurve(
        channel=channel, fluorophore="GFP", slope=0.01, intercept=0.0, conc_units="uM",
        r_squared=0.99, acquisition=fp, created="2026-01-01T00:00:00", standard_id="test-std",
        n=6, intensity_mean=1000.0, intensity_sxx=1.0e6, residual_std=5.0,
        intensity_min=0.0, intensity_max=1.0e5)


def _state(img, mask, repo):
    return {"image": img, "ivf_droplet_mask": mask,
            "data_instance": SimpleNamespace(data_repository=dict(repo))}


def _matching_metadata():
    # from_metadata → channel 'emission:520', exposure 0.1, pixel 0.1 (gain/laser absent → a WARN, still computes)
    return {"exposure_s": 0.1, "emission_nm": 520, "pixel_size_um": 0.1, "microns_per_pixel_sq": 0.01}


def test_calibrated_path_writes_concentrations_and_stashes_the_verdict(tmp_path):
    img, mask = _droplet_field()
    state = _state(img, mask, _matching_metadata())
    state["calibration_curve"] = _curve()

    replay_client_enrichment(state, Path("sampleA.tif"), {}, tmp_path)

    v = state["_calibration_validity"]
    assert v and v["valid"] is True                      # a matching curve is usable
    csv = tmp_path / "sampleA_client_enrichment.csv"
    assert csv.exists()
    row = pd.read_csv(csv).iloc[0]
    assert {"dense_concentration", "dilute_concentration", "Kp_calibrated", "enrichment_ratio"} <= set(row.index)
    assert np.isfinite(row["dense_concentration"]) and np.isfinite(row["dilute_concentration"])
    assert row["dense_concentration"] > row["dilute_concentration"]        # the droplet is enriched
    assert row["enrichment_ratio"] > 1


def test_the_validity_gate_refuses_a_mismatched_curve_without_fabricating_a_concentration(tmp_path):
    img, mask = _droplet_field()
    # image is emission 647, curve was measured on emission 520 → a different fluorophore → HARD BLOCK
    state = _state(img, mask, {"exposure_s": 0.1, "emission_nm": 647, "pixel_size_um": 0.1})
    state["calibration_curve"] = _curve(channel="emission:520")

    replay_client_enrichment(state, Path("sampleB.tif"), {}, tmp_path)

    v = state["_calibration_validity"]
    assert v and v["valid"] is False and "channel" in v["reason"].lower()
    row = pd.read_csv(tmp_path / "sampleB_client_enrichment.csv").iloc[0]
    # the verdict is recorded, but a refused calibration leaves NO concentration behind
    assert "dense_concentration" not in row.index or pd.isna(row.get("dense_concentration"))
    assert row["calibration_valid"] == False        # noqa: E712 — pandas truthiness on the cell


def test_no_curve_is_a_non_breaking_no_op(tmp_path):
    img, mask = _droplet_field()
    state = _state(img, mask, _matching_metadata())        # no calibration_curve, no path

    replay_client_enrichment(state, Path("sampleC.tif"), {}, tmp_path)

    assert "_calibration_validity" not in state             # nothing calibrated
    assert not list(tmp_path.glob("*client_enrichment*"))   # no CSV — same effect as the old skip-stub


def test_missing_droplet_mask_skips_gracefully(tmp_path):
    state = {"image": _droplet_field()[0], "calibration_curve": _curve(),
             "data_instance": SimpleNamespace(data_repository=_matching_metadata())}
    replay_client_enrichment(state, Path("sampleD.tif"), {}, tmp_path)      # no mask in state
    assert not list(tmp_path.glob("*client_enrichment*"))


# ── the reliability-context threading (BatchWorker side) ──────────────────────────────────────────────

def _scored_records():
    """A consolidated record carrying a SCORED_FAMILY measurement, so the reliability context is computed."""
    return [("droplet", pd.DataFrame({"partition_coefficient": [1.0, 2.0]}))]


def _qc_image():
    return np.random.default_rng(2).normal(500, 40, (32, 32)).clip(0).astype(np.uint16)


def test_reliability_context_threads_the_calibration_verdict():
    from pycat.batch_processor import BatchWorker
    verdict = {"valid": True, "level": "warn", "reason": "gain not recorded"}
    fake = SimpleNamespace(_last_image=_qc_image(), _last_calibration=verdict)
    ctx = BatchWorker._reliability_context_for(fake, _scored_records(), "a.tif")
    assert ctx is not None
    assert ctx["calibration"] == verdict        # the verdict reaches the reliability index
    assert "image_qc" in ctx


def test_reliability_context_omits_calibration_when_no_curve_ran():
    from pycat.batch_processor import BatchWorker
    fake = SimpleNamespace(_last_image=_qc_image(), _last_calibration=None)
    ctx = BatchWorker._reliability_context_for(fake, _scored_records(), "a.tif")
    assert ctx is not None and "image_qc" in ctx
    assert "calibration" not in ctx             # stays missing → the grade is honestly capped, never assumed


def test_reliability_context_is_none_for_a_non_scored_batch():
    from pycat.batch_processor import BatchWorker
    fake = SimpleNamespace(_last_image=_qc_image(), _last_calibration=None)
    non_scored = [("cell", pd.DataFrame({"area": [10.0]}))]   # no SCORED_FAMILY column
    assert BatchWorker._reliability_context_for(fake, non_scored, "a.tif") is None
