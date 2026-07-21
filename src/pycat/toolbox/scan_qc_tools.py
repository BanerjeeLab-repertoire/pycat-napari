"""**QC for acquisition-GEOMETRY artifacts — the way the pixels were collected, not the optics or sample.**

`data_qc_tools` asks about the image as a whole or about the optics (saturation, focus, SNR, drift,
vibration, aberration). None asks *"was this OBJECT distorted by the way the pixels were collected?"* —
and that is a distinct, per-object question:

* A **laser-scanning confocal** builds a frame one line at a time. Two vertically adjacent pixels are
  acquired one line-time apart, so a *mobile* condensate is recorded sheared/torn (its shape is a motion
  artifact) while a *stable* one in the SAME frame is clean. The artifact is **per-object**, and every
  whole-frame QC check passes this image.
* A **spinning disk** does not shear, but imprints periodic pinhole structure when the exposure is not an
  integer multiple of the disk period, and suffers pinhole crosstalk in bright/dense fields.

The rigor here comes from an **in-frame control**: a per-object shear slope is compared against the other
objects in the same frame, not a fixed global threshold (which varies with sample, scan speed, and zoom).
If every object shears identically, that is stage drift or sample flow — a different diagnosis — and the
check says so rather than flagging every object as mobile.

**Flag, never filter** (composing with `biological_qc_tools`); **gate by modality** (a shear check on a
widefield image is noise — report `na` with a reason rather than a confident wrong verdict); and **report
a velocity only when the line time is known** (the pixel-size-gate principle — an unknown calibration
yields an honest unitless px/row, never a plausible-looking µm/s).
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc_tools import _to_float, _not_applicable


# Modalities on which each family of checks is meaningful.
_POINT_SCAN = ('point-scanning', 'confocal', 'laser-scanning', 'lsm')
_SPINNING = ('spinning-disk', 'csu', 'spinning disk')

#: An object must span at least this many rows for a per-row slope fit to mean anything.
_MIN_ROWS = 6
#: Above this eccentricity, a per-row centroid slope is expected from the object's own tilt, so shear and
#: orientation cannot be separated — report `ambiguous` rather than claiming motion.
_ECC_AMBIGUOUS = 0.85
#: A slope this small (px per row) is sub-pixel noise, not motion, regardless of statistical significance.
_MIN_SLOPE_PX = 0.05
#: |slope / se(slope)| above this is a statistically consistent (not noise) slope.
_T_SIGNIFICANT = 3.0


# ── Part 1a: scan shear (per object, with an in-frame control) ──────────────────────────────────
def _per_row_shear(sub_mask, sub_intensity):
    """Fit the per-row intensity-weighted column centroid against row index for one object.

    Returns ``(slope_px_per_row, se_slope, n_rows)`` — the lateral offset accumulated per scanned line,
    with the standard error of that slope (for a significance test) and the number of rows that carried
    object signal. ``(nan, nan, n_rows)`` when there are too few rows to fit."""
    rows_idx, cols_c = [], []
    for r in range(sub_mask.shape[0]):
        row_mask = sub_mask[r]
        if not row_mask.any():
            continue
        w = sub_intensity[r][row_mask].astype(float)
        cols = np.nonzero(row_mask)[0].astype(float)
        wsum = w.sum()
        if wsum <= 0:
            continue
        rows_idx.append(float(r))
        cols_c.append(float((cols * w).sum() / wsum))
    n = len(rows_idx)
    if n < _MIN_ROWS:
        return float('nan'), float('nan'), n
    x = np.asarray(rows_idx); y = np.asarray(cols_c)
    xm = x.mean(); sxx = float(np.sum((x - xm) ** 2))
    if sxx <= 0:
        return float('nan'), float('nan'), n
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    dof = n - 2
    resid_std = float(np.sqrt(np.sum(resid ** 2) / dof)) if dof > 0 else 0.0
    se = resid_std / np.sqrt(sxx) if sxx > 0 else float('nan')
    return float(slope), float(se), n


def qc_scan_shear(labels, image, *, line_time_s=None, pixel_um=None, slow_axis=0):
    """Per-object motion shear on a laser-scanning confocal frame.

    For each object, the per-row column-centroid is fit against row index (the slow-scan axis). A stable
    object gives a flat fit (slope ≈ 0); a mobile one gives a consistent slope — its shape sheared by
    motion during the raster. The verdict per object is one of ``stable`` / ``sheared`` / ``ambiguous``
    (too elongated to separate shear from tilt) / ``na`` (too few rows to fit).

    **In-frame control:** if enough objects shear together, that is stage drift or sample flow (a global
    diagnosis), not per-object motion — reported as such, not as every object being mobile.

    A velocity is reported **only** when ``line_time_s`` is known (and µm/s only when ``pixel_um`` is too);
    otherwise the shear is an honest px/row, never converted with an assumed line time. ``slow_axis`` is the
    raster's slow axis (0 = rows, the default); the check assumes the fast axis is the other one."""
    a = _to_float(image)
    if a.ndim == 3:
        a = a[0]                                   # a scan artifact lives in a single acquired frame
    lab = np.asarray(labels)
    if slow_axis == 1:                             # normalise so rows are always the slow axis
        a = a.T; lab = lab.T

    from skimage.measure import regionprops
    props = regionprops(lab, intensity_image=a)
    if not props:
        return _not_applicable('Scan shear (motion tearing)',
                               'No segmented objects — scan shear is measured per object.')

    per_obj = []                                   # (label, slope, se, n_rows, ecc, status)
    for p in props:
        minr, minc, maxr, maxc = p.bbox
        sub_mask = (lab[minr:maxr, minc:maxc] == p.label)
        sub_int = a[minr:maxr, minc:maxc]
        slope, se, n_rows = _per_row_shear(sub_mask, sub_int)
        ecc = float(getattr(p, 'eccentricity', 0.0) or 0.0)
        if not np.isfinite(slope) or n_rows < _MIN_ROWS:
            status = 'na'
        else:
            t = abs(slope) / se if (se and np.isfinite(se) and se > 0) else np.inf
            significant = (abs(slope) >= _MIN_SLOPE_PX) and (t >= _T_SIGNIFICANT)
            if not significant:
                status = 'stable'
            elif ecc >= _ECC_AMBIGUOUS:
                status = 'ambiguous'               # elongated & tilted — cannot separate shear from morphology
            else:
                status = 'sheared'
        per_obj.append(dict(label=int(p.label), slope_px_per_row=slope, se=se,
                            n_rows=int(n_rows), eccentricity=ecc, status=status))

    sheared = [o for o in per_obj if o['status'] == 'sheared']
    assessable = [o for o in per_obj if o['status'] in ('stable', 'sheared', 'ambiguous')]

    # ── In-frame control: is this per-object motion, or a global drift/flow? ─────────────────────
    # If ≥3 objects all shear by a tightly-clustered, same-sign slope, that is the whole field moving
    # (stage drift / sample flow), not individual mobile condensates. Reclassify rather than flag each.
    uniform = False
    if len(sheared) >= 3:
        slopes = np.array([o['slope_px_per_row'] for o in sheared])
        if np.all(np.sign(slopes) == np.sign(slopes[0])) and abs(slopes.mean()) > 0:
            if slopes.std() / abs(slopes.mean()) < 0.25:
                uniform = True

    n_assessable = len(assessable)
    flags = {o['label']: (o['status'] == 'sheared') for o in per_obj}
    if uniform:
        for o in per_obj:
            flags[o['label']] = False              # uniform shear is not per-object mobility
    frac = (len(sheared) / n_assessable) if n_assessable else float('nan')

    # Velocity, only if the line time is known (px/row → px/s → µm/s).
    def _velocity(slope):
        if not (line_time_s and np.isfinite(slope)):
            return None
        v_px_s = slope / float(line_time_s)
        return (v_px_s * float(pixel_um)) if pixel_um else v_px_s
    for o in per_obj:
        o['velocity'] = _velocity(o['slope_px_per_row'])
    vel_unit = 'µm/s' if (line_time_s and pixel_um) else ('px/s' if line_time_s else None)

    if n_assessable == 0:
        status, headline = 'na', 'no object large enough to fit a per-row slope'
    elif uniform:
        status = 'warn'
        headline = ('uniform shear across all objects — this is stage drift or sample flow, not '
                    'per-object motion (a whole-field diagnosis)')
    elif not sheared:
        status, headline = 'good', f'no motion shear ({n_assessable} objects assessed)'
    else:
        status = 'warn' if frac <= 0.5 else 'bad'
        headline = (f'{len(sheared)} of {n_assessable} objects motion-sheared '
                    f'({frac:.0%}) — their shape is a motion artifact, not morphology')

    return dict(
        name='Scan shear (motion tearing)', tier='advisory', status=status,
        value=(None if frac != frac else float(frac)), unit='fraction sheared',
        headline=headline,
        how="For each object, the intensity-weighted column centroid is fit against row index (the "
            "slow-scan axis). A mobile object moves between successive lines, so its centroid drifts "
            "systematically with row — a shear. Each object's slope is compared against the others in "
            "the SAME frame (an in-frame control), so a stable object right beside a sheared one is the "
            "discriminating evidence, not an absolute threshold. Velocity is reported only when the line "
            "time is known.",
        good="On a laser-scanning confocal, immobile objects show a flat centroid-vs-row fit. A "
             "significant slope means the object moved during the raster — reduce the line time or use "
             "resonant scanning to freeze motion, or acquire this sample on a spinning disk.",
        diag=dict(per_object=per_obj, flags=flags, uniform=uniform, velocity_unit=vel_unit,
                  n_assessable=n_assessable))


def scan_shear_flags(labels, image, **kwargs):
    """Per-object motion-shear flags as a ``pd.Series`` indexed by label id (True = motion-sheared), for
    composing into the biological-QC flag columns. Flag, never filter."""
    import pandas as pd
    res = qc_scan_shear(labels, image, **kwargs)
    flags = (res.get('diag') or {}).get('flags', {}) if isinstance(res.get('diag'), dict) else {}
    if not flags:
        ids = np.unique(np.asarray(labels)); ids = ids[ids != 0]
        return pd.Series(False, index=ids, name='qc_scan_shear', dtype=bool)
    return pd.Series(flags, name='qc_scan_shear', dtype=bool)


# ── Part 1b: bidirectional-scan phase mismatch (comb / interlace) ────────────────────────────────
def qc_bidirectional_phase(image):
    """Bidirectional-scan comb artifact: alternate lines acquired in opposite directions, so a phase
    mismatch offsets odd rows laterally against even rows. Cross-correlate the odd-row and even-row
    sub-images and report the lateral (fast-axis) offset. A non-zero systematic offset corrupts every
    measurement in the frame — it is a scanner calibration problem."""
    a = _to_float(image)
    if a.ndim == 3:
        a = a[0]
    if a.shape[0] < 8:
        return dict(name='Bidirectional scan phase', tier='advisory', status='na', value=None,
                    unit='px', headline='needs ≥ 8 rows', how='', good='', diag=None)
    n = (a.shape[0] // 2) * 2
    odd = a[1:n:2]
    even = a[0:n:2]
    try:
        from skimage.registration import phase_cross_correlation
        shift = phase_cross_correlation(even, odd, upsample_factor=20)[0]
        dx = float(shift[1])                       # lateral offset along the fast axis
    except Exception as exc:  # broad-ok: phase_cross_correlation can fail on degenerate input; report na rather than crash QC
        debug_log('scan_qc: bidirectional phase correlation failed', exc)
        return dict(name='Bidirectional scan phase', tier='advisory', status='na', value=None,
                    unit='px', headline='could not be assessed', how='', good='', diag=None)

    mag = abs(dx)
    if mag < 0.3:
        status, headline = 'good', f'odd/even rows aligned ({dx:+.2f} px)'
    elif mag < 1.0:
        status, headline = 'warn', f'possible bidirectional phase mismatch ({dx:+.2f} px)'
    else:
        status, headline = 'bad', f'bidirectional comb artifact: odd/even rows offset {dx:+.2f} px'
    return dict(
        name='Bidirectional scan phase', tier='advisory', status=status, value=float(dx), unit='px',
        headline=headline,
        how="The odd-row and even-row sub-images are cross-correlated; a bidirectional scan acquires "
            "them in opposite directions, so a phase mismatch shows up as a lateral offset between them.",
        good="Odd and even rows aligned (offset ≈ 0). A systematic offset is a scanner phase "
             "calibration problem — re-calibrate the bidirectional scan phase.",
        diag=dict(lateral_offset_px=dx))


# ── Part 2a: spinning-disk pattern residual (detrended spectral peak) ────────────────────────────
def qc_disk_pattern(image):
    """Spinning-disk pinhole striping/honeycomb: when the exposure is not an integer multiple of the disk
    period, the pinhole array imprints a periodic pattern in the background. Detected as a sharp spectral
    peak above the local spectral background — **after detrending**, because a smooth vignetting gradient
    is a low-frequency component that a naive spectral test reads as striping (`qc_vibration`'s recorded
    lesson: a steady drift once read as a perfect periodic component)."""
    a = _to_float(image)
    if a.ndim == 3:
        a = a.mean(axis=0)
    if a.ndim != 2 or min(a.shape) < 16:
        return dict(name='Disk-pattern residual', tier='advisory', status='na', value=None,
                    unit='% modulation', headline='needs a 2D field ≥ 16 px', how='', good='', diag=None)

    from scipy.ndimage import gaussian_filter
    norm = a / max(float(a.mean()), 1e-6)
    # DETREND: remove low-frequency shading so vignetting is not mistaken for striping.
    bg = gaussian_filter(norm, sigma=max(a.shape) / 8.0)
    d = norm - bg
    win = np.outer(np.hanning(d.shape[0]), np.hanning(d.shape[1]))
    F = np.fft.fftshift(np.fft.fft2(d * win))
    P = np.abs(F) ** 2

    cy, cx = np.array(P.shape) // 2
    yy, xx = np.ogrid[:P.shape[0], :P.shape[1]]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    # Exclude the central low-frequency disk (residual shading) and the very-high-frequency corners.
    band = (r > min(P.shape) * 0.06) & (r < min(P.shape) * 0.5)
    if not band.any():
        return dict(name='Disk-pattern residual', tier='advisory', status='na', value=None,
                    unit='% modulation', headline='field too small', how='', good='', diag=None)
    Pb = np.where(band, P, 0.0)
    peak_idx = np.unravel_index(np.argmax(Pb), P.shape)
    peak = float(P[peak_idx])
    bg_level = float(np.median(P[band]))
    ratio = peak / max(bg_level, 1e-12)
    # Peak spatial frequency (cycles/px) from its offset, and the implied pitch in px.
    fy = (peak_idx[0] - cy) / P.shape[0]
    fx = (peak_idx[1] - cx) / P.shape[1]
    freq = float(np.hypot(fy, fx))
    pitch_px = (1.0 / freq) if freq > 0 else float('nan')
    # A rough modulation depth: the periodic component's RMS as a % of mean background (=1 after norm).
    modulation_pct = 100.0 * float(np.sqrt(2.0 * peak) / (d.shape[0] * d.shape[1]) * 2.0)

    if ratio < 20:
        status, headline = 'good', 'no periodic disk pattern in the background'
    elif ratio < 60:
        status, headline = 'warn', f'possible disk striping (pitch ≈ {pitch_px:.1f} px)'
    else:
        status, headline = 'bad', f'disk pinhole pattern in the background (pitch ≈ {pitch_px:.1f} px)'
    return dict(
        name='Disk-pattern residual', tier='advisory', status=status, value=float(modulation_pct),
        unit='% modulation', headline=headline, p_value=None,
        how="The field is detrended (low-frequency shading removed, so vignetting is not mistaken for "
            "striping), windowed, and Fourier-transformed. A sharp peak above the local spectral "
            "background at a fixed spatial frequency is the pinhole pitch.",
        good="A flat background with no periodic peak. A peak means the camera exposure is not an "
             "integer multiple of the disk rotation period — set the exposure to an integer multiple of "
             "the disk period.",
        diag=dict(peak_over_background=ratio, peak_frequency_cpp=freq, pitch_px=pitch_px, spectrum=P))


# ── Part 2b: pinhole crosstalk (elevated local background near bright objects) ────────────────────
def qc_pinhole_crosstalk(labels, image):
    """Spinning-disk pinhole crosstalk: in dense/bright fields, light from one pinhole leaks through its
    neighbours, raising the apparent background near bright objects and inflating measured intensity.
    Detected as an elevated local background in the immediate neighbourhood of bright objects relative to
    distant background. Warns that partition coefficients and enrichment ratios — the measurements this
    most directly corrupts — will be biased."""
    a = _to_float(image)
    if a.ndim == 3:
        a = a.mean(axis=0)
    lab = np.asarray(labels)
    if lab.shape != a.shape or not (lab > 0).any():
        return _not_applicable('Pinhole crosstalk',
                               'Needs segmented objects on the field to compare near vs distant background.')

    from scipy.ndimage import binary_dilation
    fg = lab > 0
    # Restrict to the BRIGHT objects — crosstalk scales with source brightness. Mean intensity is read
    # directly from the mask (version-robust vs regionprops' intensity_mean / mean_intensity rename).
    ids = np.unique(lab); ids = ids[ids != 0]
    means = {int(i): float(a[lab == i].mean()) for i in ids}
    med_int = float(np.median(list(means.values()))) if means else 0.0
    bright = np.zeros_like(fg)
    for i in ids:
        if means[int(i)] >= med_int:
            bright[lab == i] = True

    near = binary_dilation(bright, iterations=4) & ~binary_dilation(fg, iterations=1)
    distant = ~binary_dilation(fg, iterations=15)
    if not near.any() or not distant.any():
        return _not_applicable('Pinhole crosstalk',
                               'Objects fill the field — no distant background to compare against.')
    local_bg = float(np.median(a[near]))
    distant_bg = float(np.median(a[distant]))
    scale = max(abs(distant_bg), 1e-6)
    elevation = (local_bg - distant_bg) / scale

    if elevation < 0.10:
        status, headline = 'good', 'no background elevation around bright objects'
    elif elevation < 0.30:
        status, headline = 'warn', f'background {elevation:.0%} elevated near bright objects (possible crosstalk)'
    else:
        status, headline = 'bad', (f'background {elevation:.0%} elevated near bright objects — pinhole '
                                   f'crosstalk will bias partition coefficients and enrichment ratios')
    return dict(
        name='Pinhole crosstalk', tier='advisory', status=status, value=float(elevation), unit='fraction',
        headline=headline,
        how="The median background in a ring just outside bright objects is compared against distant "
            "background. Crosstalk leaks light from bright pinholes into their neighbours, so the local "
            "background rises with object brightness.",
        good="Local background near bright objects equals distant background. Elevation means light is "
             "leaking between pinholes — reduce density/brightness, and treat partition coefficients "
             "measured here as biased high.",
        diag=dict(local_background=local_bg, distant_background=distant_bg, elevation=elevation))


# ── Part 3/4: modality gating + orchestration ────────────────────────────────────────────────────
def run_scan_qc(image, labels=None, *, modality=None, line_time_s=None, pixel_um=None):
    """Run only the scan-artifact checks that apply to this acquisition modality; report the rest as `na`
    with a stated reason. **Modality is never guessed from pixel data** — a confident wrong verdict is
    worse than "not assessed — acquisition mode unknown". Pass the modality explicitly (from metadata or
    the QC UI)."""
    mode = str(modality).strip().lower() if modality else None
    is_point = mode in _POINT_SCAN
    is_spinning = mode in _SPINNING
    results = []

    if mode is None:
        why = ('not assessed — acquisition mode unknown. Scan shear applies only to a point-scanning '
               'confocal, disk pattern only to a spinning disk; run on the wrong modality they give a '
               'confident wrong answer. Select the modality in the QC panel.')
        for nm in ('Scan shear (motion tearing)', 'Bidirectional scan phase',
                   'Disk-pattern residual', 'Pinhole crosstalk'):
            results.append(_not_applicable(nm, why))
        return results

    # Point-scanning: shear + bidirectional phase apply; disk checks do not.
    if is_point:
        if labels is not None:
            results.append(qc_scan_shear(labels, image, line_time_s=line_time_s, pixel_um=pixel_um))
        else:
            results.append(_not_applicable('Scan shear (motion tearing)',
                                           'Needs a segmentation — scan shear is measured per object.'))
        results.append(qc_bidirectional_phase(image))
        results.append(_not_applicable('Disk-pattern residual',
                                       'A point-scanner has no spinning disk — no pinhole pattern to find.'))
        results.append(_not_applicable('Pinhole crosstalk',
                                       'Pinhole crosstalk is a spinning-disk artifact, not a point-scanner one.'))
    elif is_spinning:
        results.append(qc_disk_pattern(image))
        if labels is not None:
            results.append(qc_pinhole_crosstalk(labels, image))
        else:
            results.append(_not_applicable('Pinhole crosstalk',
                                           'Needs a segmentation to compare near vs distant background.'))
        results.append(_not_applicable('Scan shear (motion tearing)',
                                       'A spinning disk exposes the whole field at once — it does not shear.'))
        results.append(_not_applicable('Bidirectional scan phase',
                                       'Bidirectional scanning is a point-scanner concept.'))
    else:  # widefield or any other known-but-non-scanning mode
        why = f"acquisition mode '{mode}' is not a scanning modality — these artifacts cannot occur."
        for nm in ('Scan shear (motion tearing)', 'Bidirectional scan phase',
                   'Disk-pattern residual', 'Pinhole crosstalk'):
            results.append(_not_applicable(nm, why))
    return results
