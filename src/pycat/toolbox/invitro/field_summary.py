"""In-vitro **whole-field summary** — split out of ``invitro_tools`` by domain (1.6.215).

Per-field droplet-size and phase-intensity statistics (``field_summary`` + ``_field_summary_metrics``),
with the honest-name result dict and its measured caveats (the area fraction is a 2-D projection, not a
volume fraction; the intensity ratio is not a partition coefficient; the dense/dilute contrast is
pedestal-exact but not halo-immune). Moved VERBATIM from ``invitro_tools`` — no number changed; pinned by
``test_field_summary_is_byte_identical`` and the enrichment/halo tests. ``invitro_tools`` re-exports it.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import skimage as sk

from pycat.utils.intensity_semantics import IntensitySemantics, require_intensity


@require_intensity(IntensitySemantics.ABSOLUTE, 'field summary')
def field_summary(
    labeled_droplets: np.ndarray,
    image: np.ndarray,
    microns_per_pixel: float,
    field_area_um2: Optional[float] = None,
) -> dict:
    """
    Compute whole-field summary statistics for an in vitro droplet image.

    Parameters
    ----------
    labeled_droplets : (H, W) integer label mask (0 = background / buffer)
    image : (H, W) fluorescence or OD image in **RAW COUNTS**.

        **Not normalised.** This function reports intensity statistics, and min-max
        normalisation maps the image minimum to zero — which silently subtracts an
        uncontrolled floor (the darkest noise pixel in that field) and makes every ratio
        a function of the exposure. The docstring previously said "in [0, 1]", and the
        in-vitro widget duly fed it normalised data; the reported partition coefficient
        then swung from 323 to 22 with the noise level alone, against a true value of 30.
    microns_per_pixel : µm per pixel
    field_area_um2 : total imaged area in µm².  If None, computed from mask shape.

    Returns
    -------
    dict with keys:
        n_droplets                : number of detected droplets
        projected_area_fraction   : total droplet AREA / field AREA. This is a 2-D
                                    projected area fraction, **not a volume
                                    fraction** — see the note below.
        volume_fraction           : DEPRECATED alias of projected_area_fraction,
                                    kept so existing scripts and saved tables do not
                                    break. Do not use in new code; it is misnamed.
        mean_radius_um            : mean droplet radius (from area)
        median_radius_um
        std_radius_um
        number_density_per_um2    : droplets per µm²
        mean_droplet_intensity    : mean image value inside droplets
        dilute_phase_intensity    : mean image value OUTSIDE droplets. This is a
                                    fluorescence intensity, **not a concentration**
                                    — see the note below.
        bulk_intensity            : DEPRECATED alias of dilute_phase_intensity.
        intensity_ratio           : mean_droplet_intensity / dilute_phase_intensity. This
                                    is NOT a partition coefficient — no camera floor is
                                    removed, so it is biased toward 1 (a true Kp of 30
                                    reads as 5.8 on a 500-count pedestal). Use
                                    `partition_coefficient_local` for a real Kp.
        dense_dilute_contrast     : I_dense − I_dilute. Exact against the PEDESTAL (it
                                    cancels in the difference), but NOT immune to the
                                    droplet's PSF halo, which corrupts both terms — a 5 px
                                    edge costs it 22 %. See the note on the return value.
        partition_coefficient     : DEPRECATED alias of intensity_ratio.
                                    An apparent, intensity-based partition
                                    coefficient (dimensionless ratio of signals), not
                                    a thermodynamic one.
        total_droplet_area_um2
        field_area_um2

    Notes on what these quantities are, and are not
    -----------------------------------------------
    **The area fraction is not a volume fraction.** ``total_area / field_area`` is the
    fraction of a 2-D *projection* that is occupied by droplets. It equals the bulk
    volume fraction only under restrictive assumptions (an isotropic random section
    through a statistically homogeneous 3-D material, or a genuinely quasi-2-D
    chamber whose depth is small compared with the droplets). In a typical flow cell
    neither holds: droplets settle, so a plane near the coverslip over-represents
    them and a plane in the bulk under-represents them; and large droplets are more
    likely to intersect any given plane than small ones, biasing the in-plane size
    distribution toward large objects. Reporting this number as "volume fraction"
    invites it to be read as a physical volumetric quantity that it is not. Use the
    **Z-Stack (3-D) Object Analysis** workflow when a true volume fraction is needed.

    **The dilute-phase intensity is not C_sat.** It is a mean fluorescence (or optical
    density) value. Converting it to a saturation concentration requires a calibration
    curve relating intensity to concentration for *that* fluorophore, on *that*
    instrument, with *that* illumination — plus the assumption that the probe reports
    linearly over the range in question. Without that calibration it is a *proxy*: it
    is monotonic with concentration and therefore useful for comparison, but it has no
    units and should not be reported as a concentration.

    The same distinction applies to ``partition_coefficient``: it is a ratio of
    measured intensities. It equals the thermodynamic partition coefficient only if
    the intensity-to-concentration relationship is linear and identical in both
    phases — which is not guaranteed (quenching, inner-filter effects, and
    environment-sensitive quantum yield all break it).
    """
    H, W = labeled_droplets.shape
    if field_area_um2 is None:
        field_area_um2 = H * W * microns_per_pixel**2

    props      = sk.measure.regionprops(labeled_droplets)
    n          = len(props)
    bg_mask    = labeled_droplets == 0
    cond_mask  = labeled_droplets > 0

    if n == 0:
        _empty_bulk = float(image.mean())
        return dict(n_droplets=0,
                    projected_area_fraction=0.0,
                    volume_fraction=0.0,          # deprecated alias
                    mean_radius_um=0.0,
                    median_radius_um=0.0, std_radius_um=0.0,
                    number_density_per_um2=0.0,
                    mean_droplet_intensity=np.nan,
                    dilute_phase_intensity=_empty_bulk,
                    bulk_intensity=_empty_bulk,   # deprecated alias
                    partition_coefficient=np.nan,
                    total_droplet_area_um2=0.0, field_area_um2=field_area_um2)

    return _field_summary_metrics(props, image, bg_mask, cond_mask,
                                  microns_per_pixel, field_area_um2)


def _field_summary_metrics(props, image, bg_mask, cond_mask, microns_per_pixel, field_area_um2):
    """The non-empty whole-field metrics — droplet sizes, phase intensities, and the honest-name
    result dict (with the deprecated aliases kept for back-compat and the measured caveats on what
    each quantity IS and is not: the area fraction is a projection not a volume fraction; the
    intensity ratio is not a partition coefficient; the contrast is pedestal-exact but not
    halo-immune)."""
    n = len(props)
    areas_um2 = np.array([p.area * microns_per_pixel**2 for p in props])
    radii_um  = np.sqrt(areas_um2 / np.pi)
    total_area = float(areas_um2.sum())

    bulk_int  = float(image[bg_mask].mean())   if bg_mask.sum()  > 0 else np.nan
    cond_int  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan
    part      = (cond_int / max(bulk_int, 1e-9)) if (bulk_int and bulk_int > 0) else np.nan

    _area_frac = total_area / field_area_um2

    return dict(
        n_droplets=n,
        # Honest name first; the old key is kept as a deprecated alias so existing
        # scripts, saved CSVs and downstream code keep working.
        projected_area_fraction=_area_frac,
        volume_fraction=_area_frac,               # DEPRECATED: misnamed, see docstring
        mean_radius_um=float(radii_um.mean()),
        median_radius_um=float(np.median(radii_um)),
        std_radius_um=float(radii_um.std()),
        number_density_per_um2=n / field_area_um2,
        mean_droplet_intensity=cond_int,
        dilute_phase_intensity=bulk_int,
        bulk_intensity=bulk_int,                  # DEPRECATED alias

        # ── This is an INTENSITY RATIO, not a partition coefficient ────────────
        #
        # Kp = (I_dense - floor) / (I_dilute - floor). This is I_dense / I_dilute, with no
        # camera floor removed — so it is dragged toward 1 by the pedestal. Measured with a
        # TRUE Kp of 30 and a 500-count pedestal, it returns **5.83**.
        #
        # And if the caller feeds a MIN-MAX NORMALISED image (which the in-vitro widget did
        # until 1.5.424, and which this function's own docstring still invited by saying
        # "image in [0, 1]"), it is worse than biased — it becomes a function of the noise,
        # because normalisation maps the image MINIMUM to zero and the minimum is a noise
        # excursion below the dilute phase:
        #
        #     noise sd    reported "partition"   (true Kp = 30)
        #        2               323.5
        #        5               130.0
        #       15                44.0
        #       30                22.5
        #
        # A 14x swing driven entirely by the exposure. It is not a measurement of anything.
        #
        # The name is therefore changed to say what it is. `partition_coefficient` is kept
        # as a DEPRECATED alias so existing callers do not break, but it carries the same
        # caveat. For a real Kp, use `partition_coefficient_local` with a dark reference
        # (in vitro) or a cell mask (cellular) — see 1.5.423.
        intensity_ratio=part,
        partition_coefficient=part,               # DEPRECATED: this is NOT Kp — see above
        # ── The contrast is PEDESTAL-exact, NOT halo-immune ─────────────────────
        #
        # I claimed in 1.5.426 that this is "exact — the pedestal cancels in the difference".
        # The first half is right and the second is a blanket reassurance that does not hold:
        # the pedestal does cancel, but the contrast is NOT immune to a bad dilute reference.
        #
        # A droplet edge is not sharp, and the PSF halo corrupts BOTH terms — the dense mean
        # is pulled DOWN by edge pixels inside the mask, and the dilute mean is pulled UP by
        # halo pixels outside it. Measured, TRUE contrast = 2900:
        #
        #     droplet edge    contrast    error
        #     sharp           2898        -0 %
        #     1 px            2773        -4 %
        #     2.5 px          2560        **-12 %**
        #     5 px            2269        **-22 %**
        #
        # So: exact against the PEDESTAL, and degraded by the HALO like everything else.
        # Stating "exact" without that qualifier is the kind of true-but-incomplete
        # reassurance that 1.5.459 was about.
        dense_dilute_contrast=float(cond_int - bulk_int),

        total_droplet_area_um2=total_area,
        field_area_um2=field_area_um2,
    )
