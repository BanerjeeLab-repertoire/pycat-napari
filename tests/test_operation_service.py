"""**The execution kernel (Method-Widget Spec 6): OperationService.execute -> AnalysisResult.**

`OperationService` is the one place an operation's science runs, below batch / Navigator / panels / headless.
This pins its contract; the cross-route agreement (`kernel` computes bit-for-bit like manual/batch/session) is
proven in `test_route_equivalence.py`'s Workflow 1, the row the first migrated family closes.
"""
import numpy as np
import pytest

from pycat.kernel.operation_service import OperationService
from pycat.utils.result_models import AnalysisResult
from pycat.utils.errors import ScientificAssumptionError

pytestmark = pytest.mark.base


def test_execute_returns_a_typed_AnalysisResult_for_a_migrated_op():
    raw = np.random.default_rng(0).uniform(0, 1000, (48, 48)).astype(np.float64)
    result = OperationService.execute("rolling_ball", {"image": raw}, {"ball_radius": 25})
    assert isinstance(result, AnalysisResult)
    assert result.operation_id == "rolling_ball" and result.entity_type == "image"
    # an enhance op: no measurement table, the produced image is the artifact
    assert result.measurements is None
    assert len(result.artifacts) == 1 and np.asarray(result.artifacts[0]).shape == raw.shape


def test_execute_runs_the_same_science_as_the_toolbox_call():
    """The kernel is a THIN wrapper over the toolbox science — not a reimplementation — so its output equals a
    direct call, bit for bit. (Route-equivalence proves the batch/session routes agree too.)"""
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement
    raw = np.random.default_rng(1).uniform(0, 1000, (40, 40)).astype(np.float64)
    kernel_out = np.asarray(OperationService.execute("rolling_ball", {"image": raw},
                                                     {"ball_radius": 20}).artifacts[0])
    direct = np.asarray(rb_gaussian_bg_removal_with_edge_enhancement(raw, 20))
    np.testing.assert_array_equal(kernel_out, direct)


def test_an_unmigrated_op_raises_a_clear_error_never_silently_reroutes():
    assert OperationService.has_kernel("rolling_ball") is True
    assert OperationService.has_kernel("no_such_op") is False        # not migrated
    with pytest.raises(ScientificAssumptionError, match="No execution kernel registered"):
        OperationService.execute("no_such_op", {"image": np.zeros((8, 8))}, {})


def test_a_measure_op_returns_its_table_in_measurements_not_artifacts():
    """Family 2 (compute_msd) exercises the OTHER AnalysisResult path: a MEASURE op's result is the measurements
    TABLE (a DataFrame), not an artifact array."""
    import pandas as pd
    rng = np.random.default_rng(0)
    step = np.sqrt(2 * 0.05 * 0.1)
    rows = []
    for tid in range(8):
        pos = np.zeros(2)
        for f in range(40):
            rows.append({"track_id": tid, "frame": f, "y_um": pos[0], "x_um": pos[1]})
            pos = pos + rng.normal(0, step, 2)
    tracks = pd.DataFrame(rows)
    result = OperationService.execute("condensate_physics.compute_msd", {"tracks": tracks},
                                      {"frame_interval_s": 0.1, "min_track_length": 20})
    assert isinstance(result, AnalysisResult) and result.entity_type == "track"
    assert result.measurements is not None and "msd_um2" in result.measurements.columns
    assert result.artifacts == ()


def test_migrated_ops_reports_the_kernel_coverage():
    migrated = OperationService.migrated_ops()
    # families 1–6 + increment B filters
    assert {"rolling_ball", "condensate_physics.compute_msd", "clean", "cellpose", "client_enrichment",
            "coloc.manders_m1", "coloc.manders_m2", "colocalization",
            "gaussian", "dog", "bilateral", "log", "bandpass", "local_threshold",
            "invert", "rescale", "gabor", "felzenszwalb", "upscale"} <= migrated
