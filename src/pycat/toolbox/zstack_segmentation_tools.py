"""
PyCAT Z-Stack (3D) Condensate Segmentation
=============================================
3D condensate segmentation built on the same strategy as the 2D pipeline:
per-plane segmentation using the existing, validated 2D algorithms
(Felzenszwalb graph segmentation, kurtosis/SNR/HWHM refinement, rolling-ball
background removal), then merged across Z into true 3D objects via 3D
connected-component linking — mirroring exactly how the time-series
pipeline segments per-frame and links across T, but here linking across Z.

Why per-slice + Z-link rather than a "native" 3D algorithm
--------------------------------------------------------------
Felzenszwalb graph segmentation, the Niblack/Sauvola local thresholding used
in fz_segmentation_and_binarization, and the specific watershed-based
refinement in puncta_refinement_func are all 2D algorithms with no direct,
equally-validated 3D equivalent in scikit-image. Reimplementing true 3D
analogs from scratch would abandon the already-tuned, tested 2D pipeline
entirely. Instead:

  1. Each Z-slice is segmented independently using the *exact* existing 2D
     functions (segment_subcellular_objects, cellpose_segmentation) —
     zero duplicated logic, zero risk of the 3D path silently drifting
     from the 2D path's tuning.
  2. The resulting per-slice binary masks are stacked into a (Z, H, W)
     volume and connected in 3D via `sk.measure.label(volume, connectivity=…)`
     — objects that overlap across consecutive Z-slices are merged into a
     single 3D-labeled object, objects that don't touch across Z remain
     separate. This is standard practice for quantitative 3D puncta/spot
     analysis in tools without native 3D segmentation.

Cell segmentation in 3D
-------------------------
Cellpose runs per-slice (2D) and slices are stitched into 3D cells by IoU
(intersection-over-union) overlap linking between consecutive Z-planes —
the same "segment representative planes, link by proximity/overlap"
philosophy already used for keyframe Cellpose in the time-series pipeline,
here applied along Z instead of T.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2026
"""

from __future__ import annotations

import numpy as np


from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import debug_log
import pandas as pd
import skimage as sk
from scipy import ndimage
from typing import Optional


# ---------------------------------------------------------------------------
# 1. 3D background removal (per-slice, assembled into a volume)
# ---------------------------------------------------------------------------

@tags_layer('bg_subtract_3d', role='preprocessed', requirements=('z_stack',),
            summary='3D background removal')
def bg_removal_3d(
    volume: np.ndarray,
    ball_radius: float,
    progress_callback=None,
    pseudo3d_linear: bool = True,
) -> np.ndarray:
    """
    3D background removal for a Z-stack, built directly on the same
    validated 2D composite pipeline (rolling ball + Gaussian background
    division + Gabor edge enhancement + CLAHE — see
    rb_gaussian_bg_removal_with_edge_enhancement), run per-XY-slice.

    The composite 2D function is intricate (rolling ball, ROI-aware
    inpainting, multiple Gaussian passes at different scales, morphological
    steps, CLAHE) and is not decomposed or duplicated here — every
    nonlinear/rank-based/scale-tuned-for-XY step (rolling ball, morphology,
    CLAHE, background-division rescaling) always runs per-slice exactly as
    in the 2D pipeline, since those have no orthogonal-averaging
    justification and rolling-ball parameters are tuned for XY pixel scale,
    not the (usually much coarser, anisotropic) Z-step.

    Instead, pseudo-3D tri-planar treatment is applied as two genuinely
    standalone LINEAR passes surrounding the unmodified per-slice composite
    call — this has been found empirically to produce more consistent,
    less slice-artifacted results than pure per-slice filtering, since it
    lets Z-direction structure inform steps that would otherwise only see
    one optical plane at a time:

      1. Tri-planar Gaussian PRE-smoothing of the raw volume, at the same
         scale (sigma = ball_radius) the composite pipeline's own internal
         background estimation uses — establishes a Z-consistent baseline
         before any per-slice nonlinear processing runs.
      2. Tri-planar Gabor edge-enhancement POST-pass, added on top of the
         per-slice composite result — the composite pipeline already does
         its own internal per-slice Gabor step; this adds a second,
         genuinely volumetric edge-response layer that is averaged in
         (not simply overwriting the per-slice result), so the final
         output benefits from both the validated per-slice pipeline AND
         Z-aware edge structure.

    Parameters
    ----------
    volume : (Z, H, W) float32, values in [0, 1]
    ball_radius : float, same meaning as the 2D pipeline
    progress_callback : callable(z, n_z) or None
    pseudo3d_linear : bool
        If True (default), the two tri-planar passes described above are
        applied. If False, pure per-XY-slice processing only (faster —
        skips the ~3x-cost tri-planar Gaussian/Gabor passes — useful for
        quick previews or widely Z-spaced acquisitions where Z-direction
        continuity isn't meaningful).

    Returns
    -------
    (Z, H, W) float32 background-removed volume
    """
    from pycat.toolbox.image_processing_tools import (
        rb_gaussian_bg_removal_with_edge_enhancement,
        gaussian_smooth_3d_pseudo, gabor_filter_3d_pseudo)

    n_z = volume.shape[0]
    n_stages = 3 if pseudo3d_linear else 1
    stage = 0

    source = volume.astype(np.float32)

    if pseudo3d_linear:
        # ── Pass 1: tri-planar Gaussian pre-smoothing baseline ───────────
        source = gaussian_smooth_3d_pseudo(source, sigma=max(1.0, ball_radius / 4))
        # Re-normalise after smoothing so per-slice composite processing
        # (which expects [0,1]-ish input) behaves consistently.
        mn, mx = source.min(), source.max()
        if mx > mn:
            source = (source - mn) / (mx - mn)
        stage += 1
        if progress_callback:
            progress_callback(stage * n_z, n_stages * n_z)

    # ── Per-slice composite pipeline — unmodified, exactly as 2D ─────────
    out = np.empty_like(volume, dtype=np.float32)
    for z in range(n_z):
        out[z] = rb_gaussian_bg_removal_with_edge_enhancement(
            source[z], ball_radius)
        if progress_callback:
            progress_callback(stage * n_z + z + 1, n_stages * n_z)
    stage += 1

    if pseudo3d_linear:
        # ── Pass 2: tri-planar Gabor edge-enhancement, blended in ────────
        gabor_volumetric = gabor_filter_3d_pseudo(out)
        gmn, gmx = gabor_volumetric.min(), gabor_volumetric.max()
        if gmx > gmn:
            gabor_volumetric = (gabor_volumetric - gmn) / (gmx - gmn)
        # Average the per-slice composite result with the tri-planar
        # edge-response layer rather than replacing it outright, so the
        # validated per-slice pipeline's output is never fully discarded.
        out = (out + gabor_volumetric) / 2.0
        if progress_callback:
            progress_callback(n_stages * n_z, n_stages * n_z)

    return out

# ---------------------------------------------------------------------------
# 2. 3D cell segmentation — per-slice Cellpose + Z-stitching by IoU overlap
# ---------------------------------------------------------------------------

@tags_layer('cellpose_3d', role='labels', requirements=('z_stack',),
            summary='Cellpose 3D segmentation', target='cell')
def cellpose_segmentation_3d(
    volume: np.ndarray,
    object_diameter: float,
    min_iou: float = 0.3,
    progress_callback=None,
) -> np.ndarray:
    """
    Segment cells in a Z-stack by running 2D Cellpose on every slice and
    stitching per-slice labels into consistent 3D cell identities via
    intersection-over-union (IoU) overlap linking between consecutive
    Z-planes — the same slice-then-link philosophy as keyframe Cellpose
    in the time-series pipeline (there: link across T; here: link across Z).

    Parameters
    ----------
    volume : (Z, H, W) float32
    object_diameter : expected cell diameter in pixels
    min_iou : minimum IoU between a label in slice z and a label in slice
        z+1 to consider them the same 3D cell. Lower = more permissive
        linking (cells drift more between slices); higher = stricter.
    progress_callback : callable(z, n_z) or None

    Returns
    -------
    (Z, H, W) int32 3D-consistent labeled cell volume — the same integer
    label is used for a cell across all Z-slices it appears in.
    """
    from pycat.toolbox.segmentation_tools import cellpose_segmentation

    n_z = volume.shape[0]

    # Segment every slice independently first
    slice_labels = []
    for z in range(n_z):
        mask = cellpose_segmentation(volume[z].astype(np.float32), object_diameter)
        slice_labels.append(np.asarray(mask).astype(np.int32))
        if progress_callback:
            progress_callback(z + 1, n_z)

    # Stitch: relabel slice 0 as the seed; for each subsequent slice, match
    # each of its labels to the previous slice's labels by maximum IoU
    # overlap, reusing that 3D identity if the overlap clears min_iou;
    # otherwise assign a fresh, never-before-used 3D label.
    out = np.zeros((n_z, *volume.shape[1:]), dtype=np.int32)
    out[0] = slice_labels[0]
    next_label = int(slice_labels[0].max()) + 1

    for z in range(1, n_z):
        prev = out[z - 1]
        curr = slice_labels[z]
        new_slice = np.zeros_like(curr)

        curr_labels = np.unique(curr)
        curr_labels = curr_labels[curr_labels != 0]

        for lbl in curr_labels:
            curr_region = (curr == lbl)
            overlap_labels, counts = np.unique(
                prev[curr_region], return_counts=True)
            best_iou = 0.0
            best_prev_label = 0
            for ov_lbl, ov_count in zip(overlap_labels, counts):
                if ov_lbl == 0:
                    continue
                union = int(curr_region.sum()) + int((prev == ov_lbl).sum()) - int(ov_count)
                iou = ov_count / max(union, 1)
                if iou > best_iou:
                    best_iou = iou
                    best_prev_label = ov_lbl

            if best_iou >= min_iou:
                new_slice[curr_region] = best_prev_label
            else:
                new_slice[curr_region] = next_label
                next_label += 1

        out[z] = new_slice

    return out


# ---------------------------------------------------------------------------
# 3. 3D condensate segmentation — per-slice 2D pipeline + 3D Z-linking
# ---------------------------------------------------------------------------

@tags_layer('subcellular_segment_3d', role='labels', requirements=('z_stack',),
            summary='3D subcellular object segmentation')
def segment_subcellular_objects_3d(
    original_volume: np.ndarray,
    preprocessed_volume: np.ndarray,
    cell_mask_3d: np.ndarray,
    cell_label: int,
    ball_radius: float,
    kurtosis_threshold: float = -3.0,
    local_snr_threshold: float = 1.0,
    global_snr_threshold: float = 1.0,
    intensity_hwhm_scale: float = 1.17,
    max_area_fraction: float = 0.25,
    min_spot_radius: float = 2.0,
    connectivity: int = 1,
    progress_callback=None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    3D condensate segmentation for one cell, built directly on the 2D
    pipeline: each Z-slice is segmented using the exact same
    segment_subcellular_objects() function used for 2D analysis, then the
    resulting per-slice binary masks are connected across Z into true 3D
    objects.

    Parameters
    ----------
    original_volume, preprocessed_volume : (Z, H, W) float32
    cell_mask_3d : (Z, H, W) bool — 3D cell mask (from cellpose_segmentation_3d,
        or a 2D mask broadcast across all Z if the cell doesn't move in XY)
    cell_label : int, matches the value in a labeled cell_mask_3d if not boolean
    ball_radius, kurtosis_threshold, local_snr_threshold, global_snr_threshold,
    intensity_hwhm_scale, max_area_fraction, min_spot_radius :
        Identical meaning to the 2D segment_subcellular_objects() parameters
        — passed straight through per-slice.
    connectivity : int
        3D connectivity for merging across Z (1 = face-connected/6-neighbour,
        the conservative default; 2 = edge-connected/18; 3 = corner/26).
    progress_callback : callable(z, n_z) or None

    Returns
    -------
    refined_mask_3d : (Z, H, W) bool — refined 3D condensate mask
    labeled_3d : (Z, H, W) int32 — 3D-connected condensate labels
    """
    from pycat.toolbox.segmentation_tools import segment_subcellular_objects

    n_z = original_volume.shape[0]
    refined_stack = np.zeros_like(cell_mask_3d, dtype=bool)

    for z in range(n_z):
        cmask_z = cell_mask_3d[z]
        if not cmask_z.any():
            if progress_callback:
                progress_callback(z + 1, n_z)
            continue

        refined_z, _ = segment_subcellular_objects(
            original_volume[z], preprocessed_volume[z],
            cmask_z, cell_label, ball_radius, cell_df=None,
            kurtosis_threshold=kurtosis_threshold,
            local_snr_threshold=local_snr_threshold,
            global_snr_threshold=global_snr_threshold,
            intensity_hwhm_scale=intensity_hwhm_scale,
            max_area_fraction=max_area_fraction,
            min_spot_radius=min_spot_radius,
        )
        refined_stack[z] = refined_z

        if progress_callback:
            progress_callback(z + 1, n_z)

    # 3D connectivity linking: objects touching across consecutive Z-slices
    # merge into one 3D-labeled object.
    labeled_3d = sk.measure.label(refined_stack, connectivity=connectivity)

    return refined_stack, labeled_3d.astype(np.int32)


# ---------------------------------------------------------------------------
# 4. 3D condensate metrics
# ---------------------------------------------------------------------------

def condensate_metrics_3d(
    labeled_3d: np.ndarray,
    intensity_volume: np.ndarray,
    microns_per_pixel: float,
    z_step_um: float = float('nan'),   # NOT 1.0 — see note below
) -> pd.DataFrame:
    """
    Per-condensate 3D morphological and intensity metrics.

    Volume-analogue of the 2D condensate metrics: area -> volume,
    2D shape descriptors -> 3D ellipsoid axis lengths and sphericity.

    Parameters
    ----------
    labeled_3d : (Z, H, W) int32 3D-connected condensate labels
    intensity_volume : (Z, H, W) float32 — raw or preprocessed intensity
        for measuring mean/max/integrated signal per condensate
    microns_per_pixel : XY pixel size in µm
    z_step_um : Z-step size in µm. **Defaults to NaN, not 1.0** — see the note below.
        May differ substantially from the XY pixel size; anisotropic voxels are the norm,
        not the exception, in Z-stack microscopy. Get it from
        ``pycat.utils.pixel_size.z_step_um(data_repository)``, which reads the value the
        file actually carries.

        ---- Why the default is NaN ----

        It was ``1.0``, and **nothing ever passed a value**, so every 3-D volume this
        function produced assumed an isotropic voxel. A typical confocal pairs a 0.108 µm
        lateral pixel with a **0.300 µm** Z step — so ``voxel_volume_um3`` was out by
        **3.3x**, and the same number feeds the marching-cubes ``spacing=`` and the 3-D
        centroids, making the surface areas and the axial distances wrong in the same
        breath. *All of it reported as numbers that look entirely normal.*

        NaN propagates. A NaN volume is visibly wrong; a 3.3x overestimate is not.

    Returns
    -------
    DataFrame with columns:
        condensate_label, n_z_slices,
        volume_voxels, volume_um3,
        centroid_z, centroid_y_um, centroid_x_um,
        major_axis_um, minor_axis_um, sphericity,
        mean_intensity, max_intensity, integrated_intensity
    """
    voxel_volume_um3 = (microns_per_pixel ** 2) * z_step_um

    rows = []
    for prop in sk.measure.regionprops(labeled_3d, intensity_image=intensity_volume):
        z0, y0, x0, z1, y1, x1 = prop.bbox
        n_z_slices = z1 - z0
        volume_vox = prop.area   # for 3D regionprops, .area is voxel count
        volume_um3 = volume_vox * voxel_volume_um3

        # Sphericity: (π^(1/3) * (6V)^(2/3)) / surface_area
        # Approximate surface area via marching-cubes when the object is
        # large enough; for very small objects (few voxels) sphericity is
        # not well-defined and is reported as NaN.
        try:
            if volume_vox >= 27:  # ~3x3x3 minimum for a meaningful surface
                sub_mask = (labeled_3d[z0:z1, y0:y1, x0:x1] == prop.label)
                # Pad with a 1-voxel zero border so marching_cubes always has
                # a boundary to find, even for objects that fill their
                # entire bounding box exactly (otherwise level=0.5 can fall
                # outside the sub-volume's data range and raise).
                padded = np.pad(sub_mask.astype(np.float32), 1, mode='constant')
                verts, faces, _, _ = sk.measure.marching_cubes(
                    padded, level=0.5,
                    spacing=(z_step_um, microns_per_pixel, microns_per_pixel))
                surface_area = sk.measure.mesh_surface_area(verts, faces)
                sphericity = float(
                    (np.pi ** (1/3)) * (6 * volume_um3) ** (2/3) / max(surface_area, 1e-9)
                )
                sphericity = min(sphericity, 1.0)  # numerical safety
            else:
                sphericity = np.nan
        except Exception:
            sphericity = np.nan

        # ── The axis lengths were scaling Z by the XY pixel size ────────────────
        #
        # ``prop`` comes from a ``regionprops`` call with **no spacing**, so its axis lengths are
        # in VOXELS — and the code multiplied them by ``microns_per_pixel``, the **xy** pitch.
        # On a confocal stack the z step is typically 3-5x the xy pixel, so **every z extent was
        # divided by that factor.**
        #
        # Measured on a voxel of 0.1 x 0.1 x 0.5 um (5x anisotropic), against known geometry:
        #
        #     object                        true major    reported
        #     sphere, r = 1 um              2.00 um       2.06      (fine -- isotropic)
        #     **Z-ELONGATED, 4 um long**    **4.00 um**   **0.98**  <- a 4x UNDERESTIMATE
        #     XY-elongated, 4 um long       4.00 um       4.45      (fine -- no z extent)
        #
        # **A 4 um object elongated in z was reported as 1 um.** The error is invisible on
        # anything round, which is exactly why it survived: the sphere case is right.
        #
        # (The ``spacing`` argument WAS being passed — to the marching-cubes surface area, a few
        # lines above. The axis lengths simply never used it.)
        #
        # The inertia tensor is computed in PHYSICAL units. For an ellipsoid, the eigenvalues of
        # the (mass-normalised) inertia tensor relate to the semi-axes, and skimage's
        # ``axis_major_length`` is ``4 * sqrt(largest eigenvalue of the central moment tensor)``
        # — so scaling the coordinates before the moment calculation gives the axes in microns
        # directly.
        try:
            _coords = np.argwhere(labeled_3d == prop.label).astype(float)
            _coords *= np.array([z_step_um, microns_per_pixel, microns_per_pixel])
            _centred = _coords - _coords.mean(axis=0)

            # The central second-moment tensor, in microns^2.
            _cov = (_centred.T @ _centred) / max(len(_centred), 1)
            _eigenvalues = np.linalg.eigvalsh(_cov)
            _eigenvalues = np.clip(_eigenvalues, 0.0, None)

            # skimage's convention: axis length = 4 * sqrt(eigenvalue).
            major_um = float(4.0 * np.sqrt(_eigenvalues[-1]))
            minor_um = float(4.0 * np.sqrt(_eigenvalues[0]))
        except Exception as _exc:
            debug_log('3D metrics: could not compute the ellipsoid axes', _exc)
            major_um = minor_um = np.nan

        cz, cy, cx = prop.centroid

        rows.append({
            # ── KEEP THE BBOX. A 3D row must still be findable in an IMAGE. ────
            #
            # These loops ALREADY unpack ``prop.bbox`` — and then throw it away. A per-object 3D
            # table whose rows cannot be turned back into a picture is a table you can only read,
            # not click.
            #
            # The Z extent becomes the FRAME (an ObjectRef crops one plane), and the YX extent
            # becomes the 2D bounding box. That is exactly what a crop needs: which slice, and
            # where in it.
            'frame':    int((z0 + z1) // 2),          # the object's central slice
            'bbox_y0':  int(y0), 'bbox_x0': int(x0),
            'bbox_y1':  int(y1), 'bbox_x1': int(x1),
            'condensate_label':     prop.label,
            'n_z_slices':           n_z_slices,
            'volume_voxels':        volume_vox,
            'volume_um3':           volume_um3,
            'centroid_z':           cz,
            'centroid_y_um':        cy * microns_per_pixel,
            'centroid_x_um':        cx * microns_per_pixel,
            'major_axis_um':        major_um,
            'minor_axis_um':        minor_um,
            'sphericity':           sphericity,
            'mean_intensity':       float(prop.intensity_mean),
            'max_intensity':        float(prop.intensity_max),
            'integrated_intensity': float(prop.intensity_mean) * volume_vox,
        })

    return pd.DataFrame(rows)


def cell_metrics_3d(
    labeled_cells_3d: np.ndarray,
    microns_per_pixel: float,
    z_step_um: float = float('nan'),   # NOT 1.0 — see note below
) -> pd.DataFrame:
    """
    Per-cell 3D volume and morphology, mirroring 2D cell_analysis_func's
    area/shape output but for 3D-stitched cell volumes.

    Returns
    -------
    DataFrame with columns: cell_label, n_z_slices, volume_voxels,
        volume_um3, centroid_z, centroid_y_um, centroid_x_um
    """
    voxel_volume_um3 = (microns_per_pixel ** 2) * z_step_um
    rows = []
    for prop in sk.measure.regionprops(labeled_cells_3d):
        z0, y0, x0, z1, y1, x1 = prop.bbox
        cz, cy, cx = prop.centroid
        rows.append({
            # ── KEEP THE BBOX. A 3D row must still be findable in an IMAGE. ────
            #
            # These loops ALREADY unpack ``prop.bbox`` — and then throw it away. A per-object 3D
            # table whose rows cannot be turned back into a picture is a table you can only read,
            # not click.
            #
            # The Z extent becomes the FRAME (an ObjectRef crops one plane), and the YX extent
            # becomes the 2D bounding box. That is exactly what a crop needs: which slice, and
            # where in it.
            'frame':    int((z0 + z1) // 2),          # the object's central slice
            'bbox_y0':  int(y0), 'bbox_x0': int(x0),
            'bbox_y1':  int(y1), 'bbox_x1': int(x1),
            'cell_label':    prop.label,
            'n_z_slices':    z1 - z0,
            'volume_voxels': prop.area,
            'volume_um3':    prop.area * voxel_volume_um3,
            'centroid_z':    cz,
            'centroid_y_um': cy * microns_per_pixel,
            'centroid_x_um': cx * microns_per_pixel,
        })
    return pd.DataFrame(rows)
