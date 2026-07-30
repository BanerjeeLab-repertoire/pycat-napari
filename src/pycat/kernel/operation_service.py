"""``OperationService.execute(op_id, inputs, params) -> AnalysisResult`` — the one execution kernel (Spec 6).

The scientific execution of a single operation, in ONE place, below batch / Navigator / generated panels /
manual panels / headless. Today those layers each reach the science their own way (the batch ``replay_*``
handlers are the de facto scientific API, wrapped in path/IO concerns). This kernel is where that convergence
starts: an operation's science is a pure ``(inputs, params) -> AnalysisResult`` function registered here, and
every layer calls it, so the same step cannot compute differently depending on how it was launched.

**Migrated per operation family, behind route-equivalence.** The existing harness already pins
``manual ≈ batch ≈ Navigator ≈ session`` for a workflow; a migrated family adds ``≈ kernel`` and closes its row.
An op with no kernel yet raises a clear error — it still runs through its batch/panel route until migrated;
nothing is silently rerouted. The first migrated family is background removal (rolling-ball).
"""
from __future__ import annotations

from typing import Callable, Dict

from pycat.utils.errors import ScientificAssumptionError
from pycat.utils.result_models import AnalysisResult

#: op-id -> kernel function ``(inputs: dict, params: dict) -> AnalysisResult``.
_KERNELS: Dict[str, Callable] = {}


def register_kernel(op_id: str, fn: Callable) -> None:
    """Register the science for one operation. ``fn(inputs, params)`` must return an :class:`AnalysisResult`
    (measurements table for a measure op; the produced array in ``artifacts`` for a create/enhance op)."""
    _KERNELS[str(op_id)] = fn


class OperationService:
    """The single scientific-execution kernel — see the module docstring."""

    @staticmethod
    def execute(op_id: str, inputs: dict, params: dict = None) -> AnalysisResult:
        """Run operation ``op_id`` on ``inputs`` (arrays / masks / state, keyed by role) with reviewed ``params``,
        returning a typed :class:`AnalysisResult`. Raises ``ScientificAssumptionError`` for an unregistered op —
        the kernel is migrated per family, and an unmigrated op must NOT be silently rerouted, only reported."""
        fn = _KERNELS.get(op_id)
        if fn is None:
            raise ScientificAssumptionError(
                f"No execution kernel registered for operation {op_id!r}. The kernel is migrated one family at a "
                f"time; until this op is migrated it runs through its batch/panel route.")
        return fn(dict(inputs or {}), dict(params or {}))

    @staticmethod
    def has_kernel(op_id: str) -> bool:
        return op_id in _KERNELS

    @staticmethod
    def migrated_ops() -> frozenset:
        """Every op with a kernel — grows one family at a time, each behind a route-equivalence row."""
        return frozenset(_KERNELS)


# ── Family 1: background removal (rolling-ball). The route-equivalence harness proves this kernel computes ──
# bit-for-bit identically to the manual / batch / session routes (Workflow 1, the `kernel` route). ────────────

def _kernel_background_removal(inputs: dict, params: dict) -> AnalysisResult:
    """Rolling-ball + Gaussian background removal with edge enhancement. The SAME toolbox call the manual and
    batch routes make — on the same raw image — so the routes agree bit for bit. Enhance op: no measurements;
    the background-removed image is the artifact."""
    from pycat.toolbox.image_processing_tools import rb_gaussian_bg_removal_with_edge_enhancement
    image = inputs["image"]
    ball_radius = params["ball_radius"]
    output = rb_gaussian_bg_removal_with_edge_enhancement(image, ball_radius)
    return AnalysisResult(operation_id="rolling_ball", entity_type="image",
                          measurements=None, artifacts=(output,))


register_kernel("rolling_ball", _kernel_background_removal)


# ── Family 2: MSD transport analysis. A MEASURE op — the result is a measurements TABLE, so this exercises ──
# AnalysisResult.measurements (family 1 used only artifacts). Route-equivalence proves it in Workflow 3. ───────

def _kernel_compute_msd(inputs: dict, params: dict) -> AnalysisResult:
    """Ensemble MSD from linked trajectories — the SAME `compute_msd` call the manual/session routes make. Measure
    op: the ensemble-MSD-per-lag table is the measurement. Pixel size / frame interval come from `params` (the
    scale gate lives in the batch handler / caller, not the pure science)."""
    from pycat.toolbox.condensate_physics_tools import compute_msd
    tracks = inputs["tracks"]
    msd = compute_msd(tracks,
                      frame_interval_s=params.get("frame_interval_s", 1.0),
                      min_track_length=params.get("min_track_length", 200),
                      max_lag=params.get("max_lag"))
    return AnalysisResult(operation_id="condensate_physics.compute_msd", entity_type="track",
                          measurements=msd, artifacts=())


register_kernel("condensate_physics.compute_msd", _kernel_compute_msd)


# ── Family 3: clean spot detection + measurement. Another MEASURE op (per-object table). Closes the last ──
# torch-free route-equivalence row (Workflow 2, puncta). ──────────────────────────────────────────────────────

def _kernel_clean_detect(inputs: dict, params: dict) -> AnalysisResult:
    """Clean-mask spot detection with per-object measurement — the SAME `clean_detect` call the manual/session
    routes make. Measure op: the per-punctum detection+measurement table is the measurement."""
    from pycat.toolbox.clean_spot_detection_tools import clean_detect
    df = clean_detect(inputs["image"],
                      psf_sigma=params.get("psf_sigma", 2.5),
                      psf_size=params.get("psf_size", 11))
    return AnalysisResult(operation_id="clean", entity_type="puncta", measurements=df, artifacts=())


register_kernel("clean", _kernel_clean_detect)


# ── Family 4: Cellpose cell segmentation. A CREATE op — the produced label mask is the artifact. The flagship ──
# segmenter and the biggest parameter surface. Route-equivalence proves it in Workflow 4 (torch-gated). ────────

def _kernel_cellpose(inputs: dict, params: dict) -> AnalysisResult:
    """Cellpose segmentation — the SAME `cellpose_segmentation` call the manual/batch/session routes make, on the
    image the caller has already normalised (normalisation is a preprocessing concern the caller owns, exactly as
    the other routes do). Create op: the label mask is the artifact."""
    from pycat.toolbox.segmentation_tools import cellpose_segmentation
    masks = cellpose_segmentation(inputs["image"], params["cell_diameter"],
                                  postprocess=params.get("postprocess", False))
    masks = masks[0] if isinstance(masks, (tuple, list)) else masks
    return AnalysisResult(operation_id="cellpose", entity_type="cell", measurements=None, artifacts=(masks,))


register_kernel("cellpose", _kernel_cellpose)


# ── Family 5: client partition / enrichment. A MEASURE op returning a one-row table (dense/dilute means, the ──
# enrichment ratio, pixel counts). Route-equivalence proves it in Workflow 6 (per-frame, looped). ─────────────

def _kernel_client_enrichment(inputs: dict, params: dict) -> AnalysisResult:
    """Partition / enrichment coefficient of a dense phase against its dilute surround — the SAME
    `client_enrichment` call the manual/session routes make. Measure op: the one-row metrics table (dense_mean,
    dilute_mean, enrichment, pixel counts, background provenance) is the measurement."""
    from pycat.toolbox.partition_enrichment_tools import client_enrichment
    import pandas as pd
    out = client_enrichment(inputs["image"], inputs["dense_mask"],
                            cell_mask=inputs.get("cell_mask"),
                            background=params.get("background", 0.0))
    return AnalysisResult(operation_id="client_enrichment", entity_type="condensate",
                          measurements=pd.DataFrame([out]), artifacts=())


register_kernel("client_enrichment", _kernel_client_enrichment)


# ── Family 6: colocalization coefficients. A composite WORKFLOW over three single-coefficient ops — each is ──
# its own kernel (Manders M1, Manders M2, pixel-wise Pearson), and the coloc route calls all three. This is the ─
# kernel handling a multi-op workflow: the workflow orchestrates, the kernel runs one op. Proven in Workflow 5. ─

def _one_value(op_id: str, entity: str, value: float) -> AnalysisResult:
    import pandas as pd
    return AnalysisResult(operation_id=op_id, entity_type=entity,
                          measurements=pd.DataFrame([{"value": float(value)}]), artifacts=())


def _kernel_manders_m1(inputs: dict, params: dict) -> AnalysisResult:
    """Manders M1 — fraction of channel-1 (in ``masked_image_1``) overlapping channel-2's mask, within the ROI.
    Same `manders_m1_calculation` call as the manual/session routes."""
    from pycat.toolbox.obj_based_coloc_analysis_tools import manders_m1_calculation
    return _one_value("coloc.manders_m1", "coloc",
                      manders_m1_calculation(inputs["masked_image_1"], inputs["mask_2"], inputs["roi"]))


def _kernel_manders_m2(inputs: dict, params: dict) -> AnalysisResult:
    """Manders M2 — the symmetric channel-2-over-channel-1 fraction. Same `manders_m2_calculation` call."""
    from pycat.toolbox.obj_based_coloc_analysis_tools import manders_m2_calculation
    return _one_value("coloc.manders_m2", "coloc",
                      manders_m2_calculation(inputs["mask_1"], inputs["masked_image_2"], inputs["roi"]))


def _kernel_pearson_coloc(inputs: dict, params: dict) -> AnalysisResult:
    """Pixel-wise Pearson correlation within the ROI. Same `pearsons_correlation` call ([0] = the coefficient)."""
    from pycat.toolbox.pixel_wise_corr_analysis_tools import pearsons_correlation
    return _one_value("colocalization", "coloc",
                      pearsons_correlation(inputs["ch1"], inputs["ch2"], inputs["roi"])[0])


register_kernel("coloc.manders_m1", _kernel_manders_m1)
register_kernel("coloc.manders_m2", _kernel_manders_m2)
register_kernel("colocalization", _kernel_pearson_coloc)


# ── Increment B — image filters (pure array→array enhance ops). Deterministic; each proven by its own new ──
# route-equivalence workflow. ────────────────────────────────────────────────────────────────────────────────

def _kernel_gaussian(inputs: dict, params: dict) -> AnalysisResult:
    """Gaussian smoothing — the SAME `gaussian_smooth_2d` call the manual/session routes make. Enhance op: the
    smoothed image is the artifact."""
    from pycat.toolbox.image_processing.filters import gaussian_smooth_2d
    out = gaussian_smooth_2d(inputs["image"], params["sigma"])
    return AnalysisResult(operation_id="gaussian", entity_type="image", measurements=None, artifacts=(out,))


def _kernel_dog(inputs: dict, params: dict) -> AnalysisResult:
    """Difference-of-Gaussians blob enhancement — the SAME `dog_blob_enhance_2d` call. Enhance op: the enhanced
    image is the artifact."""
    from pycat.toolbox.image_processing.filters import dog_blob_enhance_2d
    out = dog_blob_enhance_2d(inputs["image"],
                              sigma_lo=params.get("sigma_lo", 2.0), sigma_hi=params.get("sigma_hi", 3.2))
    return AnalysisResult(operation_id="dog", entity_type="image", measurements=None, artifacts=(out,))


register_kernel("gaussian", _kernel_gaussian)
register_kernel("dog", _kernel_dog)
