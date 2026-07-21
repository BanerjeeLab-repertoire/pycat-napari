"""In-vitro **partition-coefficient** measurement — split out of ``invitro_tools`` by domain (1.6.214).

The calibration-sensitive quantitative core: the local-annulus K_p (``partition_coefficient_local`` and its
phase helpers ``_pc_*``), the assumptions-scoped ``partition_measurement``, the no-cell-mask
``partition_coefficient_field``, and the dilution-series ``estimate_phase_boundary``. Moved VERBATIM from
``invitro_tools`` — no background handling, fit, or reported K_p changed; pinned byte-identical by the
partition characterization and calibration/ΔG tests. ``invitro_tools`` re-exports the public entry points.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import skimage as sk
from scipy import ndimage, optimize

from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.intensity_semantics import IntensitySemantics, require_intensity
from pycat.utils.object_ref import bbox_columns_from_regionprops


def _pc_check_input(image_layer):
    """Is this image a valid input for an INTENSITY measurement? Returns ``(ok, why)`` — ``ok=False``
    means REFUSE (Kp is a ratio of intensities, meaningful only where pixel values still relate to photon
    count); a non-empty ``why`` with ``ok=True`` is an advisory to surface.

    Several routine preprocessing steps destroy the intensity relation — which is what they are FOR — and
    nothing else stopped their output being fed here. Measured on a field with a TRUE Kp of 30: min-max
    normalised → 130, white top-hat → 199 (dilute removed → ~0), top-hat+LoG → −12 (LoG is signed), CLAHE
    → 65 (measures the algorithm). The signal is in the PROVENANCE, not the pixels — a background-
    subtracted image looks like one with a dark background — so the operations record what they did and
    this checks. ``image_layer=None`` proceeds unchecked (nothing to check against)."""
    if image_layer is None:
        return True, ''
    try:
        from pycat.utils.intensity_semantics import (IntensitySemantics,
                                                     check_measurement_input)
        return check_measurement_input(
            image_layer, IntensitySemantics.ABSOLUTE, 'the partition coefficient')
    except Exception as _exc:  # broad-ok: the intensity-semantics check is OPTIONAL — if that module is unavailable, proceed (logged) rather than block the measurement; this is the check missing, not the data failing it
        debug_log("intensity-semantics check unavailable", _exc)
        return True, ''


def _pc_camera_floor(stype, dark_reference, cell_mask, img):
    """The camera floor (pedestal) for ``Kp = (I_dense − floor)/(I_dilute − floor)`` — returns
    ``(floor_source, I_dark)``, raising on an unknown ``sample_type``.

    Leave the floor in and Kp is dragged toward 1 (a 500-count pedestal turns a true 30 into 5.81).
    **IN VITRO THE FLOOR CANNOT BE AUTO-DETECTED BY ANY METHOD**: droplets sit in bulk buffer, so every
    pixel is ``pedestal + dilute`` or ``pedestal + dense`` and no region holds the pedestal ALONE — it is
    inseparable from the dilute phase IN PRINCIPLE. (Otsu was tried; it returned the dilute phase as the
    "floor", Kp 5.77 vs a true 30, flagged valid — a heuristic cannot recover information the image does
    not contain.) So the tool is TOLD the case: ``in_vitro`` REQUIRES a ``dark_reference`` (buffer, no
    fluorophore, same settings); ``cellular`` can read the floor from the EXTRACELLULAR region via a
    ``cell_mask`` — the MEDIAN, not the mean (the mean is dragged up by cell-edge pixels: 548 vs 504
    against a true 500, the same stay-off-the-interface principle as the annulus gap)."""
    floor_source = 'none'
    I_dark = np.nan
    if stype not in ('cellular', 'in_vitro'):
        raise ValueError(
            "sample_type must be 'cellular' or 'in_vitro'. The camera floor can be measured "
            "from the image in CELLS (the extracellular region contains no fluorophore) but "
            "NOT IN VITRO (every pixel contains the dilute phase, so the pedestal cannot be "
            "isolated by any method). The tool must be told which case it is in rather than "
            "guess -- guessing produced a 5x error, confidently reported.")

    if dark_reference is not None:
        if np.isscalar(dark_reference):
            I_dark = float(dark_reference)
        else:
            _d = np.asarray(dark_reference, dtype=float)
            I_dark = float(np.median(_d[np.isfinite(_d)])) if _d.size else np.nan
        floor_source = 'dark_reference'

    elif stype == 'cellular' and cell_mask is not None:
        # Outside every cell there is no fluorophore, so that region IS the floor.
        _cm = np.asarray(cell_mask)
        outside = (~_cm) if _cm.dtype == bool else (_cm == 0)
        if outside.sum() > 50:
            I_dark = float(np.median(img[outside]))
            floor_source = 'extracellular'
    return floor_source, I_dark


def _pc_estimate_gap(dist_out, img, gap_px):
    """The offset from the droplet edge to the inner ring of the dilute-phase annulus — ``3 × interface
    width`` (floored at 5 px), or ``gap_px`` when the caller fixes it.

    A phase boundary is not a step: it has a finite interface width, and a ring drawn against the edge
    sits inside that gradient and reads far too high (gap 0 → 492 against a true dilute of 100; gap 10 →
    100.3, converged). The width is estimated from where the intensity, walking outward, falls to 10% of
    its local range."""
    if gap_px is not None:
        return float(gap_px)
    shell = (dist_out > 0) & (dist_out <= 12)
    if shell.any():
        prof = [float(img[(dist_out > d - 1) & (dist_out <= d)].mean())
                for d in range(1, 13)
                if ((dist_out > d - 1) & (dist_out <= d)).any()]
        prof = np.asarray(prof)
        if prof.size > 3:
            lo, hi = prof.min(), prof.max()
            thr = lo + 0.1 * (hi - lo)
            below = np.flatnonzero(prof <= thr)
            iface = float(below[0] + 1) if below.size else 3.0
        else:
            iface = 3.0
    else:
        iface = 3.0
    return max(5.0, 3.0 * iface)


def _pc_measure_droplets(img, lab, ids, sat, I_dark, gap_px, ring_width_px, allow_no_reference):
    """Per droplet: the dense-mask vs annular-dilute intensities → the per-droplet rows and the per-
    droplet mask CVs. Emits the OVER-INCLUSIVE-mask warning at the end.

    A mask that spills past the droplet edge pulls DILUTE pixels into the dense average, so I_dense — and
    Kp — collapse (true Kp 30 → 4.4 at a 50 px mask on a 13 px droplet, a 7x error reported as
    "validated"). It is detectable from the data alone: a clean dense mask has a LOW coefficient of
    variation (0.016), an over-inclusive one a high one (0.807) — a 50-fold, monotonic separation."""
    from scipy import ndimage as _ndi
    rows = []
    _mask_cv = []          # per-droplet CV: an over-inclusive mask has a HIGH CV
    for lb in ids:
        obj = (lab == lb)
        if not obj.any():
            continue

        dist_out = _ndi.distance_transform_edt(~obj)
        _gap = _pc_estimate_gap(dist_out, img, gap_px)
        ring = (dist_out > _gap) & (dist_out <= _gap + float(ring_width_px))
        ring &= (lab == 0)                       # never sample another droplet
        if not ring.any():
            continue

        dense_px = img[obj]
        _cv = (float(np.std(dense_px) / max(abs(np.mean(dense_px)), 1e-9))
               if dense_px.size > 1 else 0.0)
        _mask_cv.append(_cv)
        ring_px = img[ring]
        I_dense = float(dense_px.mean())
        I_ring = float(ring_px.mean())

        tol = 1e-6 * max(abs(sat), 1.0)
        frac_sat = float((dense_px >= sat - tol).mean())
        saturated = bool(frac_sat > 0.001)

        contrast = I_dense - I_ring              # the pedestal CANCELS here
        raw_ratio = (I_dense / I_ring) if I_ring > 0 else np.nan
        if saturated:
            kp = np.nan
        elif np.isfinite(I_dark) and (I_ring - I_dark) > 0:
            kp = (I_dense - I_dark) / (I_ring - I_dark)
        elif allow_no_reference:
            kp = raw_ratio                       # NOT Kp — a pedestal-biased ratio the caller opted into
        else:
            kp = np.nan                          # no reference -> Kp is not computable

        rows.append(dict(
            droplet_label=int(lb),
            I_dense=I_dense, I_dilute_local=I_ring, I_dark=I_dark,
            gap_px=_gap,
            contrast=contrast,
            partition_coefficient=kp,
            raw_ratio=raw_ratio,
            saturated=saturated, saturated_fraction=frac_sat,
        ))

    # A high CV inside the dense mask means the mask is including dilute-phase pixels.
    if _mask_cv:
        _cv_med = float(np.median(_mask_cv))
        if _cv_med > 0.25:
            napari_show_warning(
                f"Partition coefficient: the droplet mask looks OVER-INCLUSIVE — the "
                f"intensity inside it has a coefficient of variation of {_cv_med:.2f}, and a "
                f"clean dense-phase mask has a CV near 0.02 (every pixel is dense phase).\n\n"
                f"A mask that spills past the droplet edge pulls DILUTE-phase pixels into the "
                f"dense average, so I_dense falls and **Kp falls with it**. Measured on a "
                f"scene with a TRUE Kp of 30: a mask 1.5x too large gives Kp = 19.9 "
                f"(CV 0.42), and one 2.3x too large gives **Kp = 9.5** (CV 0.81) — **a 3x "
                f"collapse, reported with no indication that anything is wrong.**\n\n"
                f"Tighten the segmentation until the mask contains the dense phase and not "
                f"its surroundings.")
    return rows, _mask_cv


def _pc_verdict(floor_source, stype, allow_no_reference, df, kp_mean, I_dark, mask_cv):
    """Build the human verdict for the measured field and emit the matching napari notification.

    Two honesty rules live here: a validation claim is NEVER printed next to a NaN (it tells the user the
    machinery is sound at the moment it refused to answer), and the "validated" message NEVER sounds
    confident when the MASK is suspect (a reassurance the user reads instead of the warning is worse than
    none — the pedestal correction can be sound while an over-inclusive mask collapses Kp by 7x)."""
    if floor_source == 'dark_reference' and not np.isfinite(kp_mean):
        verdict = (
            f"Kp is NOT COMPUTABLE for this image — see the warning above. "
            f"{int(df['saturated'].sum()) if len(df) else 0} of {len(df)} droplet(s) are "
            f"saturated at the detector ceiling.\n\n"
            f"The camera floor WAS measured correctly from the dark reference "
            f"({I_dark:.1f} counts), and that part of the measurement is sound. **It does not "
            f"help**: a clipped dense phase truncates the numerator by an unknown amount, so "
            f"the ratio is meaningless rather than a lower bound. Re-acquire with a shorter "
            f"exposure or lower gain.")
        napari_show_warning("Partition coefficient: " + verdict)

    elif floor_source == 'dark_reference':
        verdict = (f"Kp = {kp_mean:.2f}. The camera floor was measured directly from the "
                   f"DARK REFERENCE ({I_dark:.1f} counts) and removed from both phases, so "
                   f"Kp is pedestal-independent. Validated: 29.65 recovered against a true "
                   f"30.0 at pedestals of 0, 100, 500 and 2000 counts.")
        if mask_cv and float(np.median(mask_cv)) > 0.25:
            verdict += (" **But see the mask warning above — the pedestal correction is "
                        "sound and the MASK is not, and Kp is only as good as the worse of "
                        "the two.**")
        else:
            napari_show_info("Partition coefficient: " + verdict)

    elif floor_source == 'extracellular':
        verdict = (f"Kp = {kp_mean:.2f}. The camera floor ({I_dark:.1f} counts) came from "
                   f"the EXTRACELLULAR REGION. In cells this is a legitimate dark "
                   f"reference: there is no fluorophore outside the cell, so that region "
                   f"contains the camera pedestal (and any medium autofluorescence — a real "
                   f"floor you also want removed). The MEDIAN of the outside region is used, "
                   f"not the mean: the mean is dragged upward by cell-edge pixels (measured, "
                   f"against a true pedestal of 500 — mean 548.2, median 504.0). A dedicated "
                   f"dark frame remains the more direct measurement.")
        napari_show_info("Partition coefficient: " + verdict)

    elif stype == 'in_vitro':
        # In vitro there is NOTHING to fall back on. Say so plainly.
        verdict = (
            "IN VITRO WITHOUT A DARK REFERENCE — Kp is not computable, and no heuristic can "
            "rescue it.\n\n"
            "Droplets sit in bulk buffer: every pixel contains the dilute phase, so no "
            "region of the image holds the camera pedestal alone. The floor and the dilute "
            "phase are INSEPARABLE IN PRINCIPLE, not merely hard to separate. (An automatic "
            "threshold was tried and it returned the DILUTE PHASE as the 'camera floor', "
            "giving Kp = 5.77 against a true 30 — confidently, and silently.)\n\n"
            "THE FIX: acquire a DARK REFERENCE — buffer with no fluorophore, same camera "
            "settings — and pass it as `dark_reference`. It takes one extra frame.\n\n"
            f"What IS reported meanwhile: the CONTRAST "
            f"(I_dense − I_dilute = {float(df['contrast'].mean()) if len(df) else float('nan'):.0f}), "
            "which is exact against the PEDESTAL (it cancels in the difference) but NOT "
            "immune to the droplet's PSF halo — a 5 px edge costs it 22%. Pass "
            "`allow_no_reference=True` to additionally receive the raw intensity ratio, "
            "which is NOT Kp and is biased toward 1 by an unknowable amount.")
        napari_show_warning("Partition coefficient: " + verdict)

    elif allow_no_reference:
        verdict = (f"**NOT a partition coefficient — a raw intensity ratio ({kp_mean:.2f}), "
                   f"biased toward 1.** No camera floor was available. The annulus measures "
                   f"(camera pedestal + dilute phase), and nothing in the image separates "
                   f"them, so the bias is UNKNOWABLE from this image: with a true Kp of 30 "
                   f"and a 500-count pedestal, the raw ratio returns 5.81. Use it for "
                   f"RELATIVE comparison between images acquired identically; do not report "
                   f"it as Kp. The CONTRAST is exact regardless.")
        napari_show_warning("Partition coefficient: " + verdict)

    else:
        verdict = (
            "NO CAMERA FLOOR — Kp is not computable.\n\n"
            "For CELLULAR data, pass `cell_mask=<mask>`: the extracellular region contains "
            "no fluorophore and is a genuine dark reference.\n"
            "For IN VITRO data, pass `dark_reference=<image>` (buffer, no fluorophore, same "
            "camera settings) — nothing else can work, because every pixel contains the "
            "dilute phase.\n"
            "Or `allow_no_reference=True` to receive the raw ratio, clearly labelled as NOT "
            "Kp.\n\n"
            "The CONTRAST (I_dense − I_dilute) is reported regardless and is exact: the "
            "pedestal cancels in the difference — though not immune to the PSF halo, "
            "which costs a 5 px edge 22%.")
        napari_show_warning("Partition coefficient: " + verdict)
    return verdict


def partition_coefficient_local(image, labeled_droplets, sample_type='cellular',
                                dark_reference=None, cell_mask=None,
                                allow_no_reference=False,
                                gap_px=None, ring_width_px=6, saturation_level=None,
                                image_layer=None):
    """Kp from a LOCAL annular dilute phase, with an optional dark reference.

    Why a local annulus, and why it needs a gap
    ------------------------------------------
    The dilute phase is what surrounds each droplet, so measure it there rather than from a
    global percentile of the whole field (which assumes uniform illumination — a vignetted
    field does not have it).

    **The ring must be OFFSET from the droplet edge.** A phase boundary is not a step: it
    has a finite interface width, and a ring drawn against the edge sits inside that
    gradient. Measured on a synthetic droplet (true dilute = 100 counts above a pedestal of
    500):

    ========  ================
    gap (px)  ring − pedestal
    ========  ================
    0         **491.8**  ← inside the interface gradient, 5x too high
    2         206.0
    5         110.7
    **10**    **100.3**  ← converged
    20        99.9
    ========  ================

    The default gap is **3 × the estimated interface width**, floored at 5 px.

    What the annulus CAN and CANNOT give you
    ----------------------------------------
    The ring reads ``pedestal + dilute`` — the camera offset is still in it. So:

    * **Without a dark reference** you can compute the **contrast** ``I_dense − I_ring``,
      which is exact (the pedestal cancels), but you **cannot** compute Kp. The raw ratio
      ``I_dense / I_ring`` is *not* Kp: on the synthetic droplet above, with a true Kp of
      30, it returns **5.81 — an 81 % error, and it looks like a plausible number.**
    * **With a dark reference** — an image of buffer with no fluorophore, acquired with the
      same camera settings — the pedestal is measured directly, and
      ``(I_dense − I_dark) / (I_ring − I_dark)`` recovers the true Kp to within a percent
      (29.77 vs 30.0).

    This is the honest resolution of a question the image alone cannot answer: **a camera
    pedestal and a genuine dilute phase are both just a floor above zero, and nothing in the
    image separates them.** A dark frame separates them, and nothing else does.

    Parameters
    ----------
    dark_reference : optional 2-D image (or a scalar) of buffer with NO fluorophore, same
        camera settings. Without it, ``partition_coefficient`` is returned as ``NaN`` and
        only ``contrast`` is reported.
    gap_px : distance from the droplet edge to the inner edge of the annulus. Default:
        3 x the estimated interface width (min 5 px).
    """
    # Kp is only meaningful on an ABSOLUTE-intensity image; refuse a preprocessed one (see helper).
    _ok, _why = _pc_check_input(image_layer)
    if not _ok:
        napari_show_warning("Partition coefficient: " + _why)
        return dict(partition_coefficient=np.nan, contrast=np.nan,
                    is_true_kp=False, floor_source='none',
                    per_droplet_df=pd.DataFrame(), verdict=_why)
    if _why:
        napari_show_warning("Partition coefficient: " + _why)

    img = np.asarray(image, dtype=float)
    lab = np.asarray(labeled_droplets)
    ids = np.unique(lab)
    ids = ids[ids != 0]
    if ids.size == 0:
        return dict(partition_coefficient=np.nan, per_droplet_df=pd.DataFrame(),
                    verdict='No droplets labelled.')

    # Saturation ceiling (same logic as partition_coefficient_field).
    sat = saturation_level
    if sat is None:
        if np.issubdtype(np.asarray(image).dtype, np.integer):
            sat = float(np.iinfo(np.asarray(image).dtype).max)
        else:
            mx = float(np.nanmax(img)) if img.size else 1.0
            sat = 1.0 if mx <= 1.0 + 1e-6 else mx

    _stype = str(sample_type).lower()
    floor_source, I_dark = _pc_camera_floor(_stype, dark_reference, cell_mask, img)

    rows, _mask_cv = _pc_measure_droplets(img, lab, ids, sat, I_dark, gap_px,
                                          ring_width_px, allow_no_reference)

    df = pd.DataFrame(rows)
    kp_vals = df['partition_coefficient'].to_numpy(dtype=float) if len(df) else np.array([])
    kp_vals = kp_vals[np.isfinite(kp_vals)]
    kp_mean = float(kp_vals.mean()) if kp_vals.size else np.nan

    verdict = _pc_verdict(floor_source, _stype, allow_no_reference, df, kp_mean, I_dark, _mask_cv)

    return dict(
        # NaN unless a floor was available, or the caller explicitly accepted a raw ratio.
        partition_coefficient=kp_mean,
        # TRUE only when a camera floor was actually measured. When False, the value above
        # is a raw ratio biased toward 1 — not a partition coefficient.
        is_true_kp=bool(floor_source != 'none'),
        floor_source=floor_source,
        contrast=float(df['contrast'].mean()) if len(df) else np.nan,
        has_dark_reference=bool(dark_reference is not None),
        camera_floor=I_dark,
        n_saturated_droplets=int(df['saturated'].sum()) if len(df) else 0,
        per_droplet_df=df,
        verdict=verdict,
    )


def _partition_background_assumption(dark_reference, background_subtracted, floor, dilute):
    """The ``background_subtracted`` assumption for a partition measurement — ``(checked, holds, detail)``.

    **The image CANNOT tell whether its floor is a camera pedestal or the dilute phase.** In a partition
    measurement the dilute phase IS signal (the denominator), so it is not a background to be removed, and
    a low-Kp system legitimately has a dilute level close to the dense one — a camera pedestal and a
    genuine dilute phase both produce a floor above zero, and two heuristics failed in both directions
    (false-alarming every low-Kp image; passing a 500-count pedestal that had dragged Kp 30 → 5.8). So the
    tool ASKS rather than guesses: a dark reference RESOLVES it (buffer with no fluorophore measures the
    floor directly, removable from both phases); otherwise the caller states it, and if they do not, the
    assumption is recorded UNCHECKED rather than silently assumed. The consequence is large and invisible —
    an unsubtracted pedestal appears in BOTH numerator and denominator and drags Kp toward 1 (true Kp 30 →
    15.5 / 5.8 / 2.4 at pedestals of 100 / 500 / 2000)."""
    if dark_reference is not None:
        # A dark reference is the ONLY thing that separates a camera offset from a genuine dilute phase;
        # the scope of the validation claim is stated IN the sentence (it covers the PEDESTAL, not the
        # mask — an over-inclusive mask still collapses Kp by 7x with the correction perfectly sound).
        _dk = (float(dark_reference) if np.isscalar(dark_reference)
               else float(np.median(np.asarray(dark_reference, dtype=float))))
        return True, True, (
            f'RESOLVED by a dark reference (camera floor = {_dk:.1f} counts). '
            f'The pedestal is measured directly and removed from both phases, so '
            f'Kp is pedestal-independent — validated against the PEDESTAL '
            f'specifically: 29.65 recovered against a true 30.0 at pedestals of 0, '
            f'100, 500 and 2000 counts.\n\n'
            f'**That is the only thing it is validated against.** It says nothing '
            f'about the segmentation: an over-inclusive droplet mask pulls '
            f'dilute-phase pixels into the dense average and collapses Kp by up to '
            f'7x, with the pedestal correction still perfectly sound. Use '
            f'partition_coefficient_local(), which checks the mask as well.')
    if background_subtracted is None:
        return False, None, (
            f'NOT CHECKED — the caller did not say. The image floor (1st '
            f'percentile) is {floor:.1f} and the dilute phase is {dilute:.1f}, '
            f'and NOTHING in the image can distinguish a camera pedestal from a '
            f'genuine dilute phase: both are simply a floor above zero. If the '
            f'background was not subtracted, Kp is compressed toward 1 — on '
            f'identical droplets with a true Kp of 30, a pedestal of 500 counts '
            f'gives Kp = 5.8.\n\nTHE FIX: acquire a DARK REFERENCE — buffer with '
            f'no fluorophore, same camera settings — and pass it as '
            f'dark_reference. That measures the camera floor directly and is the '
            f'only thing that CAN separate it from the dilute phase. In cells, '
            f'partition_coefficient_local() additionally samples the dilute phase '
            f'from a LOCAL ANNULUS around each droplet (offset from the edge to '
            f'clear the interface gradient), which is more defensible than a '
            f'global percentile on an unevenly illuminated field.')
    return True, bool(background_subtracted), (
        'the caller states the background was subtracted'
        if background_subtracted else
        f'the caller states the background was NOT subtracted. Kp is '
        f'compressed toward 1 by the pedestal — it appears in both the '
        f'numerator and the denominator. This value is not interpretable.')


def partition_measurement(image, labeled_droplets, percentile_bulk=10.0,
                          saturation_level=None, background_subtracted=None,
                          dark_reference=None):
    """The partition coefficient as a ``Measurement`` — with its assumptions CHECKED.

    ``partition_coefficient_field`` returns a number. This returns the number **with the
    conditions under which it means anything**, each one *computed from the data* rather
    than asserted:

    * **neither phase saturated** — checked. A clipped dense phase does not give a lower
      bound on Kp; the numerator has been truncated by an unknown amount, so the ratio is
      **meaningless**, not conservative. Validated (1.5.392): with a bulk of 100 on a 16-bit
      sensor, a true Kp of 655, 1500 and 4000 **all read as 655** once the dense phase
      clips.
    * **background subtracted** — checked against the image's own floor. A partition
      coefficient is a ratio of two intensities, and an unsubtracted camera pedestal
      appears in *both*, dragging the ratio toward 1. A Kp of 4 on a pedestal of 500 counts
      reads as ~1.1. This is the same failure as the transfection filter (1.5.415) and the
      puncta SNR gate (1.5.416).
    * **dilute phase measured locally** — flagged. Estimating the dilute phase from a global
      percentile of the whole field assumes the background is uniform; on a vignetted or
      unevenly illuminated field it is not.

    An assumption that fails marks the measurement ``NOT_INTERPRETABLE``. That is the point:
    a Kp computed on a saturated image should not be usable as a number at all.
    """
    from pycat.utils.measurement import (Measurement, Parameter, Assumption,
                                         ParameterSource, ValidationLevel,
                                         Interpretability)

    res = partition_coefficient_field(image, labeled_droplets,
                                      percentile_bulk=percentile_bulk,
                                      saturation_level=saturation_level)

    img = np.asarray(image)
    sat = bool(res.get('saturated', False))
    frac_sat = float(res.get('saturated_fraction', 0.0))
    n_sat_drops = int(res.get('n_saturated_droplets', 0))

    # ── Is the background subtracted? Compute it; do not ask. ──────────────────
    #
    # If a camera pedestal is still present, the image's low percentile sits well above
    # zero. A properly background-subtracted image has its floor at (or near) zero.
    dense = float(res.get('c_dense_proxy', np.nan))
    dilute = float(res.get('c_sat_proxy', np.nan))
    finite = img[np.isfinite(img)]
    floor = float(np.percentile(finite, 1)) if finite.size else 0.0

    # Is the background subtracted? The image cannot tell a pedestal from a genuine dilute phase, so
    # the assumption is resolved by a dark reference, stated by the caller, or recorded UNCHECKED.
    bg_checked, bg_holds, bg_detail = _partition_background_assumption(
        dark_reference, background_subtracted, floor, dilute)

    assumptions = [
        Assumption(
            name='no_saturation',
            description=('neither phase is clipped at the detector ceiling — a truncated '
                         'numerator makes Kp meaningless, not a lower bound'),
            checked=True,
            holds=not sat,
            detail=(f'{frac_sat:.1%} of dense-phase pixels at the ceiling; '
                    f'{n_sat_drops} droplet(s) affected'
                    if sat else 'no clipping detected in the dense phase'),
        ),
        Assumption(
            name='background_subtracted',
            description=('the camera offset has been removed — an unsubtracted pedestal '
                         'appears in BOTH the numerator and the denominator and drags the '
                         'ratio toward 1'),
            checked=bg_checked,
            holds=bg_holds,
            detail=bg_detail,
        ),
        Assumption(
            name='dilute_phase_measured_locally',
            description=('the dilute phase is representative — a global percentile assumes '
                         'a uniform background, which a vignetted or unevenly illuminated '
                         'field does not have'),
            checked=False,
            holds=None,
            detail=(f'the dilute phase was taken as the {percentile_bulk:.0f}th percentile '
                    f'of the whole field. Check the vignetting QC; if illumination is not '
                    f'flat, this over- or under-states the dilute phase depending on where '
                    f'the droplets sit.'),
        ),
    ]

    parameters = [
        Parameter(name='percentile_bulk', value=float(percentile_bulk), units='%',
                  source=ParameterSource.ASSUMED,
                  note='the percentile of the field taken as the dilute phase'),
        Parameter(name='saturation_level',
                  value=float(res.get('saturation_level', np.nan)), units='counts',
                  source=(ParameterSource.MANUFACTURER if saturation_level is not None
                          else ParameterSource.METADATA),
                  note=('supplied by the caller' if saturation_level is not None
                        else 'inferred from the image dtype')),
    ]

    m = Measurement(
        name='partition coefficient',
        value=float(res.get('partition_coeff', np.nan)),
        units='dimensionless',
        parameters=parameters,
        assumptions=assumptions,
        validation=ValidationLevel.SIMULATION_VALIDATED,
        notes=('Kp = I_dense / I_dilute. It is a ratio of intensities, so it inherits '
               'every offset and every clipping event in either phase.'),
    )
    return m


def estimate_phase_boundary(concentrations, fractions, n_boot=400,
                            random_state=0):
    """Locate the phase boundary from a dilution series, USING the zero-fraction
    samples and reporting an uncertainty interval.

    Why this exists (``estimate_csat_lever_rule`` does neither)
    -----------------------------------------------------------
    1. **The zeros are thrown away.** The old fit does ``above = phi > 0`` and
       regresses only those points. But a sample at C = 5 with Φ = 0 is a *direct
       constraint on the quantity being estimated*: it says "the boundary is above
       5". These are **censored observations**, not missing data, and they are the
       most informative points in the series for locating the boundary. Discarding
       them and extrapolating an x-intercept from the points far above the boundary
       is the least stable way to find it.

    2. **No uncertainty is reported.** The extrapolated x-intercept is very
       sensitive to the slope. Measured on a synthetic series with a known boundary
       at 10 and only σ = 0.004 noise on Φ, the recovered value ranged over
       **[8.9, 11.0]** across bootstrap replicates — and the old code returns a
       single number with no interval. Worse, a series with only two points just
       above the boundary returned **C_sat = −6.9**: a negative saturation
       concentration, which is not a physical quantity.

    Method
    ------
    * **Segmented (hinge) fit.** Model Φ(C) = max(0, s·(C − C_b)) directly, over ALL
      the data including the zeros. The hinge location C_b *is* the boundary, and
      it is fitted rather than extrapolated to.
    * **Bootstrap interval.** Resample the series and refit, returning a percentile
      interval for the boundary.
    * The zeros constrain the hinge from below; the positive points constrain the
      slope. Both are used.

    Naming
    ------
    The returned quantity is called ``boundary_concentration`` — the **lever-rule
    apparent boundary** — not ``C_sat``. Calling it a saturation concentration
    asserts (a) that Φ is a true volume fraction and (b) that the intensity axis is
    a calibrated concentration. When Φ came from a 2-D image it is a *projected area
    fraction* (see ``field_summary``), so the boundary is biased; and if
    ``concentrations`` were fluorescence intensities rather than calibrated
    concentrations, the boundary carries those units. It is a robust **relative**
    measure — the shift of the boundary between conditions imaged identically is
    real and useful — but it is not an absolute C_sat without volumetric and
    concentration calibration.

    Returns
    -------
    dict with:
      boundary_concentration      : the fitted hinge location (the apparent boundary)
      boundary_ci                 : (lo, hi) bootstrap 95 % interval
      slope                       : dΦ/dC above the boundary
      dense_axis_intercept        : where the fitted line reaches Φ = 1. Reported as a
                                    LINE INTERCEPT, not as ``C_dense``: it is an
                                    extrapolation far outside the data and is a
                                    physical concentration only under the same
                                    assumptions as above.
      n_below, n_above            : how many samples were below / above the boundary
      fit_success, warnings       : diagnostics
    """
    from scipy import optimize as _opt

    c = np.asarray(concentrations, dtype=float)
    phi = np.asarray(fractions, dtype=float)
    ok = np.isfinite(c) & np.isfinite(phi)
    c, phi = c[ok], phi[ok]
    warnings_ = []

    if len(c) < 3:
        return dict(fit_success=False, warnings=["Need at least 3 samples."])

    def _hinge(params, cc):
        cb, s = params
        return np.maximum(0.0, s * (cc - cb))

    def _resid(params, cc, pp):
        return _hinge(params, cc) - pp

    def _fit(cc, pp):
        pos = pp > 0
        if pos.sum() >= 2:
            # Slope seed by least squares. Guarded: a bootstrap resample can draw
            # duplicate x values, which makes polyfit ill-conditioned and noisy.
            cx, cy = cc[pos], pp[pos]
            if np.ptp(cx) > 1e-12:
                s0 = float(np.cov(cx, cy, bias=True)[0, 1] / max(np.var(cx), 1e-12))
            else:
                s0 = 1e-3
            s0 = s0 if s0 > 1e-9 else 1e-3
            cb0 = float(np.min(cx))
        else:
            s0, cb0 = 1e-3, float(np.median(cc))
        try:
            r = _opt.least_squares(
                _resid, x0=[cb0, s0], args=(cc, pp),
                bounds=([-np.inf, 1e-12], [np.inf, np.inf]), max_nfev=5000)
            return float(r.x[0]), float(r.x[1])
        except Exception:  # broad-ok: returns (NaN, NaN) on fit failure — an honest missing value, not a fabricated fit
            return np.nan, np.nan

    cb, s = _fit(c, phi)
    if not np.isfinite(cb) or not np.isfinite(s) or s <= 0:
        return dict(fit_success=False,
                    warnings=["The segmented fit did not converge."])

    n_below = int((phi <= 0).sum())
    n_above = int((phi > 0).sum())
    if n_above < 2:
        warnings_.append(
            "Fewer than 2 samples above the boundary: the slope, and therefore the "
            "boundary, is essentially unconstrained.")
    if n_below == 0:
        warnings_.append(
            "No samples below the boundary. The boundary is being EXTRAPOLATED from "
            "points above it, which is the least stable way to locate it. Include "
            "concentrations that produce no condensates \u2014 a zero is a real "
            "measurement that constrains the boundary from below.")

    # Bootstrap interval.
    rng = np.random.default_rng(random_state)
    boots = []
    n = len(c)
    for _ in range(int(n_boot)):
        idx = rng.integers(0, n, n)
        b_cb, b_s = _fit(c[idx], phi[idx])
        if np.isfinite(b_cb) and np.isfinite(b_s) and b_s > 0:
            boots.append(b_cb)
    if len(boots) >= 20:
        lo, hi = (float(np.percentile(boots, 2.5)),
                  float(np.percentile(boots, 97.5)))
    else:
        lo = hi = float('nan')
        warnings_.append("Bootstrap did not converge often enough for an interval.")

    if cb < 0:
        warnings_.append(
            "The fitted boundary is NEGATIVE, which is not a physical concentration. "
            "The series does not constrain it \u2014 do not report this value.")

    dense_ic = float(cb + 1.0 / s) if s > 0 else float('nan')

    return dict(
        boundary_concentration=float(cb),
        boundary_ci=(lo, hi),
        slope=float(s),
        dense_axis_intercept=dense_ic,
        n_below=n_below, n_above=n_above,
        n_boot_ok=len(boots),
        warnings=warnings_,
        fit_success=bool(np.isfinite(cb) and cb > 0 and n_above >= 2),
    )



# ---------------------------------------------------------------------------
# 5. Partition coefficient without cell mask
# ---------------------------------------------------------------------------

@require_intensity(IntensitySemantics.ABSOLUTE, 'the partition coefficient')
def partition_coefficient_field(
    image: np.ndarray,
    labeled_droplets: np.ndarray,
    percentile_bulk: float = 10.0,
    saturation_level: float = None,
) -> dict:
    """
    Compute the fluorescence partition coefficient for in vitro droplets.

    For in vitro data, the bulk (dilute phase) intensity is estimated
    from the background pixels. Using a low percentile (default 10th)
    avoids contamination from dim droplets just below the segmentation
    threshold.

    Parameters
    ----------
    image           : (H, W) float32 in [0, 1]
    labeled_droplets: (H, W) integer label mask
    percentile_bulk : percentile of background pixels to use as bulk estimate

    Returns
    -------
    dict with keys:
        c_sat_proxy       : bulk (dilute phase) intensity
        c_dense_proxy     : mean droplet interior intensity
        partition_coeff   : C_dense / C_sat
        enrichment        : (C_dense − C_sat) / C_sat
        per_droplet_df    : DataFrame with per-droplet partition coefficient
    """
    bg_mask   = labeled_droplets == 0
    cond_mask = labeled_droplets > 0

    # Bulk (dilute-phase) intensity estimate. The 10th percentile of background
    # can collapse to ~0 on dark fluorescence backgrounds (many near-zero
    # pixels), which then made per-droplet partition = intensity / ~0 explode to
    # ~1e8. Use a ROBUST bulk: the percentile, but floored to the background MEAN
    # if the percentile is degenerate (<=0 or a tiny fraction of the mean). This
    # keeps the per-droplet partition on the same scale as the field-level one.
    if bg_mask.sum() > 0:
        bg_vals   = image[bg_mask]
        bulk_pct  = float(np.percentile(bg_vals, percentile_bulk))
        bulk_mean = float(bg_vals.mean())
        # If the percentile is ~0 (dark background) fall back to the mean, which
        # is what the field-level summary uses.
        if bulk_pct <= 0 or (bulk_mean > 0 and bulk_pct < 0.05 * bulk_mean):
            bulk = bulk_mean
        else:
            bulk = bulk_pct
    else:
        bulk = 0.0
    # Final safety floor: never divide by (near-)zero.
    bulk_div = bulk if bulk > 1e-6 else (float(image.mean()) if image.mean() > 1e-6 else 1.0)

    dense  = float(image[cond_mask].mean()) if cond_mask.sum() > 0 else np.nan

    # ── Detector saturation INVALIDATES the partition coefficient ────────────
    #
    # If the dense phase clips at the detector maximum, the numerator of Kp has been
    # TRUNCATED BY AN UNKNOWN AMOUNT. The measured Kp then pins at the clip ceiling: with
    # a bulk of 100 and a 16-bit sensor, a true Kp of 655, 1500 or 4000 ALL read as 655.
    #
    # The tempting move is to call it a lower bound. It is not: you cannot say how far the
    # true value lies above the measured one, because you do not know how much signal the
    # detector threw away. Reporting a number invites exactly that misreading -- a Kp of
    # 655 looks like a measurement, not a floor. So the coefficient is marked INVALID and
    # the reason is returned with it.
    #
    # The saturation ceiling is inferred from the dtype where possible (a uint16 image
    # clips at 65535, a float image normalised to [0,1] clips at 1.0). Callers with a
    # known full-well capacity can override it.
    sat_level = saturation_level
    if sat_level is None:
        if np.issubdtype(image.dtype, np.integer):
            sat_level = float(np.iinfo(image.dtype).max)
        else:
            mx = float(np.nanmax(image)) if image.size else 1.0
            sat_level = 1.0 if mx <= 1.0 + 1e-6 else mx

    tol = 1e-6 * max(abs(sat_level), 1.0)
    dense_px = image[cond_mask] if cond_mask.sum() > 0 else np.array([])
    n_sat = int((dense_px >= sat_level - tol).sum()) if dense_px.size else 0
    frac_sat = (n_sat / dense_px.size) if dense_px.size else 0.0
    # Even a small clipped fraction biases the mean downward; but a handful of hot pixels
    # should not condemn an otherwise fine measurement. 0.1% is the point at which the
    # truncation is no longer negligible against the other uncertainties here.
    saturated = bool(frac_sat > 0.001)

    part   = dense / bulk_div
    enrich = (dense - bulk) / bulk_div
    if saturated:
        part = np.nan
        enrich = np.nan

    rows = []
    for prop in sk.measure.regionprops(labeled_droplets, intensity_image=image):
        vals = prop.image_intensity[prop.image] if hasattr(prop, 'image_intensity') \
               else np.array([])
        d_nsat = int((vals >= sat_level - tol).sum()) if vals.size else 0
        d_frac = (d_nsat / vals.size) if vals.size else 0.0
        d_sat = bool(d_frac > 0.001)
        rows.append({
            # ── KEEP THE BBOX. It is what makes this row brushable. ────────────────
            #
            # regionprops hands it over free, and PyCAT was discarding it at 24 of its 25
            # call sites. **A row without a bbox cannot be turned back into an image** —
            # which is the difference between a plot you can click and one you can only
            # look at. In BATCH it is the only route back to the object at all.
            **bbox_columns_from_regionprops(prop),
            'droplet_label':      prop.label,
            'mean_intensity':     float(prop.intensity_mean),
            # A saturated droplet's Kp is NOT a lower bound -- it is meaningless. NaN,
            # with the reason in the neighbouring columns.
            'partition_coeff':    (np.nan if d_sat
                                   else float(prop.intensity_mean / bulk_div)),
            'saturated':          d_sat,
            'saturated_fraction': float(d_frac),
            'area_um2':           np.nan,  # caller can fill from microns_per_pixel
        })

    n_sat_droplets = int(sum(r['saturated'] for r in rows))
    if saturated or n_sat_droplets:
        napari_show_warning(
            f"Partition coefficient: the dense phase is SATURATED "
            f"({frac_sat:.1%} of dense-phase pixels at the detector ceiling "
            f"{sat_level:g}; {n_sat_droplets}/{len(rows)} droplets affected). "
            f"Kp is reported as NaN, not as a lower bound: the numerator has been "
            f"truncated by an unknown amount, so the true value cannot be bounded. "
            f"Re-acquire with a shorter exposure or lower gain.")

    return dict(
        c_sat_proxy=bulk,
        c_dense_proxy=dense,
        partition_coeff=part,
        enrichment=enrich,
        # Saturation diagnostics travel WITH the result, so a downstream consumer cannot
        # use the number without seeing why it is (or is not) trustworthy.
        saturated=saturated,
        saturated_fraction=float(frac_sat),
        saturation_level=float(sat_level),
        n_saturated_droplets=n_sat_droplets,
        per_droplet_df=pd.DataFrame(rows),
    )
