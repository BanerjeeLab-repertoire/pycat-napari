"""
Data Quality Control (QC) metrics for microscopy images and stacks.

This module is written to be a *teaching* tool as much as a checker: every metric
returns not only a value and a pass/warn/fail status, but also a short
description of HOW it is measured and WHAT good data looks like, plus (where
useful) a diagnostic array the dashboard can plot so the user can see the
evidence rather than just a coloured light.

Two tiers:
  * CORE      — rock-solid, absolute-ish thresholds: saturation, focus, SNR,
                vignetting, drift, ghosting.
  * ADVISORY  — heuristics or metrics that need user-supplied optics/timing:
                Nyquist sampling, time sampling, spherical aberration,
                vibration, chromatic aberration.

Each metric function returns a dict with at least:
    name, tier, status ('good'|'warn'|'bad'|'info'|'na'), value (float|None),
    unit, headline (short result string), how ('how it is measured'),
    good ('what good data looks like'), and optionally `diag` (a dict of arrays
    for plotting).
"""

import numpy as np


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_float(img):
    return np.asarray(img, dtype=np.float64)


def _robust_noise_std(img):
    """Noise std from median absolute adjacent-pixel differences (robust to
    real structure and sparse edges)."""
    d = np.abs(np.diff(_to_float(img), axis=-1))
    if d.size == 0:
        return 0.0
    return 1.4826 * float(np.median(d)) / np.sqrt(2.0)


def _dtype_max(img):
    """Best guess at the sensor's full-scale value for clipping detection."""
    a = np.asarray(img)
    if np.issubdtype(a.dtype, np.integer):
        return float(np.iinfo(a.dtype).max)
    # floats: assume the data max is the ceiling unless it looks normalised
    m = float(np.nanmax(a)) if a.size else 1.0
    if m <= 1.0 + 1e-6:
        return 1.0
    # common camera bit depths
    for full in (255, 4095, 65535):
        if m <= full:
            return float(full)
    return m


def _mean_frame(data):
    """Collapse a (T/Z, H, W) stack to a representative 2-D frame (the mean)."""
    a = _to_float(data)
    return a.mean(axis=0) if a.ndim == 3 else a


# ---------------------------------------------------------------------------
# CORE metrics
# ---------------------------------------------------------------------------

def qc_saturation(img):
    """Fraction of pixels clipped at the sensor ceiling or floor."""
    a = _to_float(img)
    full = _dtype_max(img)
    hi = float(np.mean(a >= full * (1 - 1e-6)))
    lo = float(np.mean(a <= 0.0))
    worst = max(hi, lo)
    status = 'good' if worst < 0.001 else ('warn' if worst < 0.01 else 'bad')
    return dict(
        name='Saturation / clipping', tier='core', status=status,
        value=worst * 100.0, unit='%',
        headline=f"{hi*100:.2f}% at ceiling, {lo*100:.2f}% at floor",
        how="Fraction of pixels sitting exactly at the sensor's maximum (or at "
            "zero). Clipped pixels have lost their true intensity.",
        good="Well under 0.1% clipped. Any bright saturated region means "
             "intensity/quantitative measurements there are unreliable.",
        diag=dict(hist_counts=np.histogram(a, bins=64)[0],
                  hist_edges=np.histogram(a, bins=64)[1], ceiling=full))


def qc_focus(data):
    """Sharpness via the variance of the Laplacian. Absolute value is
    scene-dependent, so for a stack we also flag frames far below the median."""
    from scipy.ndimage import laplace
    a = _to_float(data)
    def _sharp(f):
        return float(np.var(laplace(f)))
    if a.ndim == 3:
        vals = np.array([_sharp(f) for f in a])
        med = float(np.median(vals))
        # frames well below the median sharpness are likely defocused/drifted
        lo = vals < 0.5 * med if med > 0 else np.zeros(len(vals), bool)
        status = 'good' if not lo.any() else ('warn' if lo.mean() < 0.15 else 'bad')
        return dict(
            name='Focus / sharpness', tier='core', status=status,
            value=med, unit='var(∇²)',
            headline=f"{int(lo.sum())}/{len(vals)} frames below half-median sharpness",
            how="Variance of the Laplacian per frame — high-frequency edge "
                "energy. Sharper images score higher.",
            good="All frames near the same sharpness. Frames dipping far below "
                 "the others are out of focus or drifted axially.",
            diag=dict(per_frame=vals, median=med))
    v = _sharp(a)
    return dict(
        name='Focus / sharpness', tier='core', status='info', value=v,
        unit='var(∇²)', headline=f"sharpness = {v:.1f} (relative)",
        how="Variance of the Laplacian — high-frequency edge energy.",
        good="Higher = sharper, but the absolute number depends on the scene; "
             "compare against a known in-focus image of similar content.",
        diag=None)


def qc_snr(img):
    """Signal-to-noise: signal (robust dynamic range) over noise (robust
    high-frequency estimate)."""
    a = _mean_frame(img)
    noise = _robust_noise_std(a)
    # signal = spread of the real structure, robustly (5–95 percentile range)
    p5, p95 = np.percentile(a, [5, 95])
    signal = float(p95 - p5)
    snr = signal / noise if noise > 0 else np.inf
    status = 'good' if snr >= 10 else ('warn' if snr >= 4 else 'bad')
    return dict(
        name='SNR / noise', tier='core', status=status,
        value=float(snr), unit='×',
        headline=f"SNR ≈ {snr:.1f}  (noise σ ≈ {noise:.1f})",
        how="Signal = 5–95th-percentile intensity spread; noise = robust "
            "estimate from adjacent-pixel differences. SNR = signal / noise.",
        good="SNR ≳ 10 is comfortable; below ~4 the structure is buried in "
             "noise — increase exposure/illumination or average frames.",
        diag=dict(noise=noise, signal=signal))


def qc_vignetting(img):
    """Radial illumination falloff: edge-to-centre mean intensity ratio."""
    a = _mean_frame(img)
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    rmax = r.max()
    nb = 24
    edges = np.linspace(0, rmax, nb + 1)
    prof = np.array([a[(r >= edges[i]) & (r < edges[i + 1])].mean()
                     if np.any((r >= edges[i]) & (r < edges[i + 1])) else np.nan
                     for i in range(nb)])
    prof = np.interp(np.arange(nb), np.flatnonzero(np.isfinite(prof)),
                     prof[np.isfinite(prof)])
    centre = float(np.mean(prof[:max(1, nb // 8)]))
    edge = float(np.mean(prof[-max(1, nb // 8):]))
    ratio = edge / centre if centre != 0 else 1.0
    status = 'good' if ratio >= 0.9 else ('warn' if ratio >= 0.7 else 'bad')
    return dict(
        name='Vignetting / flat-field', tier='core', status=status,
        value=float(ratio), unit='edge/centre',
        headline=f"edge is {ratio*100:.0f}% of centre brightness",
        how="Mean intensity binned by distance from the image centre; the "
            "edge-to-centre ratio measures illumination falloff.",
        good="Ratio ≳ 0.9 (nearly flat). Strong falloff biases intensity "
             "measurements by position — apply a flat-field correction.",
        diag=dict(radial_profile=prof, radius_bins=0.5 * (edges[:-1] + edges[1:])))


def qc_ghosting(img):
    """Ghosting (double image from reflections / internal lens echoes) via the
    image *cepstrum* — the inverse transform of the log power spectrum. An echo
    (a faint shifted copy of the whole scene) leaves a sharp cepstral peak at the
    ghost offset. The cepstrum is far more specific than plain autocorrelation:
    it responds to the coherent whole-field echo, not to ordinary repeated
    structure (spots, cells) in the sample."""
    from scipy.ndimage import gaussian_filter
    a = _mean_frame(img)
    a = a - a.mean()
    P = np.log(np.abs(np.fft.fft2(a)) ** 2 + 1e-6)
    C = np.abs(np.fft.fftshift(np.fft.ifft2(P)))
    C /= C.max() if C.max() != 0 else 1.0
    h, w = C.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    mask = (r > 4) & (r < 0.4 * min(h, w))
    if not mask.any():
        return dict(name='Ghosting (double image)', tier='core', status='na',
                    value=None, unit='', headline='image too small',
                    how='', good='', diag=None)
    prom = C - gaussian_filter(C, 3)
    sec = float(prom[mask].max())
    idx = np.argwhere(mask & (prom == sec))
    off = np.sqrt(((idx[0][0] - cy) ** 2 + (idx[0][1] - cx) ** 2)) if len(idx) else 0.0
    status = 'good' if sec < 0.005 else ('warn' if sec < 0.012 else 'bad')
    return dict(
        name='Ghosting (double image)', tier='core', status=status,
        value=sec, unit='cepstral peak',
        headline=f"cepstral echo {sec:.4f} at ~{off:.0f} px offset",
        how="Cepstrum (inverse transform of the log power spectrum). A reflection "
            "ghost is a faint shifted copy of the whole scene and leaves a sharp "
            "cepstral peak at the ghost's displacement.",
        good="No sharp cepstral peak (well below ~0.005). A clear peak means a "
             "double image — check for filter/coverslip reflections.",
        diag=dict(cepstrum=C))


# ---------------------------------------------------------------------------
# CORE metrics needing a stack
# ---------------------------------------------------------------------------

def qc_drift(stack):
    """Lateral sample/stage drift across a (T, H, W) stack via phase
    cross-correlation to the first frame."""
    a = _to_float(stack)
    if a.ndim != 3 or a.shape[0] < 2:
        return dict(name='Drift', tier='core', status='na', value=None, unit='px',
                    headline='needs a multi-frame stack', how='', good='', diag=None)
    from skimage.registration import phase_cross_correlation
    ref = a[0]
    shifts = np.zeros((a.shape[0], 2))
    for i in range(1, a.shape[0]):
        sh = phase_cross_correlation(ref, a[i], upsample_factor=10)[0]
        shifts[i] = sh
    mag = np.sqrt((shifts ** 2).sum(axis=1))
    total = float(mag.max())
    fov = min(a.shape[1], a.shape[2])
    frac = total / fov
    status = 'good' if frac < 0.01 else ('warn' if frac < 0.05 else 'bad')
    return dict(
        name='Drift', tier='core', status=status, value=total, unit='px',
        headline=f"max drift {total:.1f} px ({frac*100:.1f}% of FOV)",
        how="Each frame is registered to the first by phase cross-correlation; "
            "the shift magnitude is the drift.",
        good="Well under ~1% of the field of view. Large drift misaligns "
             "time-series measurements — register or crop, or fix the stage.",
        diag=dict(shifts=shifts, magnitude=mag))


def qc_vibration(stack):
    """Mechanical vibration: an oscillatory component in the frame-to-frame
    shift trace (advisory — needs several frames)."""
    a = _to_float(stack)
    if a.ndim != 3 or a.shape[0] < 8:
        return dict(name='Vibration', tier='advisory', status='na', value=None,
                    unit='', headline='needs ≥ 8 frames', how='', good='', diag=None)
    from skimage.registration import phase_cross_correlation
    dx = np.zeros(a.shape[0] - 1)
    dy = np.zeros(a.shape[0] - 1)
    for i in range(1, a.shape[0]):
        sh = phase_cross_correlation(a[i - 1], a[i], upsample_factor=10)[0]
        dy[i - 1], dx[i - 1] = sh
    # ── Do NOT collapse the shift to its MAGNITUDE ─────────────────────────────
    #
    # This used to be `sig = np.hypot(dx, dy)`. A stage vibrating in a CIRCLE or ellipse
    # -- a real and common mode -- has a shift of CONSTANT magnitude, so hypot() turns it
    # into a FLAT LINE and the periodicity is destroyed before the FFT ever sees it.
    # Measured on a synthetic circular vibration: the magnitude trace was literally all
    # zeros, and the check reported "no periodic component (p = 1.00)" for a stage that
    # was vibrating throughout.
    #
    # Analyse the two axes separately and take the stronger periodicity: a linear
    # vibration shows up in one axis, a circular one in both.
    def _conc(s):
        sp = np.abs(np.fft.rfft(s - s.mean())) ** 2
        if len(sp) < 3:
            return np.nan
        return float(sp[1:].max() / max(sp[1:].sum(), 1e-12))

    axes = {'y': dy - dy.mean(), 'x': dx - dx.mean()}
    concs = {k: _conc(v) for k, v in axes.items()}
    worst_axis = max(concs, key=lambda k: (concs[k] if np.isfinite(concs[k]) else -np.inf))
    sig = axes[worst_axis]
    ratio = concs[worst_axis]
    spec = np.abs(np.fft.rfft(sig)) ** 2

    # ── The old gate measured STACK LENGTH, not vibration ───────────────────────
    #
    # The status used to be `good if ratio < 0.35 else warn if < 0.6 else bad`. But the
    # spectral concentration of a *random* jitter trace depends entirely on how many
    # frequency bins there are — i.e. on the number of frames. Measured, with NO vibration
    # present at all:
    #
    #      5 frames -> ratio 0.79  -> "bad"
    #     10 frames -> ratio 0.54  -> "warn"
    #     20 frames -> ratio 0.31  -> "good"
    #    200 frames -> ratio 0.05  -> "good"
    #
    # The same microscope on the same table got a different verdict depending on how many
    # frames were acquired. A short stack of perfectly good data was condemned; a long
    # stack could hide a real vibration.
    #
    # So reference the statistic against its own null: PERMUTE the jitter trace, which
    # destroys any periodicity while preserving the amplitude distribution exactly, and
    # ask how often a random ordering concentrates its energy as sharply as the observed
    # one. That p-value does not depend on the frame count.
    #
    # Validated: random jitter is called "no vibration" at EVERY stack length (including
    # 5 frames, where the old gate said "bad"), and real periodic vibration is detected
    # from ~20 frames upward. Below ~20 frames there are too few bins to detect anything,
    # and it says so rather than reporting "good".
    _rng = np.random.default_rng(0)
    _ps = []
    for _k, _v in axes.items():
        _c = concs[_k]
        if not np.isfinite(_c):
            continue
        _null = np.array([_conc(_rng.permutation(_v)) for _ in range(400)])
        _null = _null[np.isfinite(_null)]
        if _null.size:
            _ps.append(float((np.sum(_null >= _c) + 1) / (_null.size + 1)))
    if _ps:
        # Two axes tested -> Bonferroni. A vibration in EITHER axis is a vibration.
        p_vib = float(min(1.0, 2.0 * min(_ps)))
    else:
        p_vib = float('nan')

    n_frames = int(a.shape[0])
    if not np.isfinite(p_vib):
        status = 'na'
        headline = 'vibration could not be assessed'
    elif n_frames < 20:
        # Too few frequency bins for the test to have power. "Not assessed" is NOT "good".
        status = 'na'
        headline = (f'not assessable: {n_frames} frames (≥ 20 needed to detect a '
                    f'periodic component)')
    elif p_vib < 0.01:
        status = 'bad'
        headline = f'periodic vibration detected (p = {p_vib:.3f})'
    elif p_vib < 0.05:
        status = 'warn'
        headline = f'possible periodic vibration (p = {p_vib:.3f})'
    else:
        status = 'good'
        headline = f'no periodic component (p = {p_vib:.2f})'

    return dict(
        name='Vibration', tier='advisory', status=status, value=float(ratio),
        unit='spectral conc.',
        headline=headline,
        p_value=p_vib,
        how="Frame-to-frame shift jitter is Fourier-transformed, and its spectral "
            "concentration is compared against permutations of the SAME trace "
            "(which destroy periodicity but keep the amplitudes). The raw "
            "concentration depends strongly on the frame count; the p-value does not.",
        good="Jitter energy spread across frequencies, indistinguishable from a random "
             "reordering of the same jitter. A significant peak suggests a vibration "
             "source (pump, fan, footsteps).",
        diag=dict(spectrum=spec, p_value=p_vib, n_frames=n_frames,
                  axis=worst_axis, concentration_by_axis=concs))


# ---------------------------------------------------------------------------
# ADVISORY metrics
# ---------------------------------------------------------------------------

def qc_spherical_aberration(data, is_zstack=False):
    """Spherical aberration (e.g. from a coverslip/coating thicker than the
    objective's correction) spreads light axially and asymmetrically.

    Only meaningful on a z-stack (through-focus): the axial intensity response
    becomes asymmetric about best focus. Because a time-series stack looks the
    same shape as a z-stack, this is computed only when the caller marks the
    data as a z-stack; otherwise it falls back to a weak 2-D halo proxy.
    """
    a = _to_float(data)
    if is_zstack and a.ndim == 3 and a.shape[0] >= 5:
        # axial profile of sharpness through the stack
        from scipy.ndimage import laplace
        prof = np.array([np.var(laplace(f)) for f in a])
        k = int(np.argmax(prof))
        # skew of the axial profile about its peak (0 = symmetric = well-corrected)
        z = np.arange(len(prof), dtype=float) - k
        p = prof / (prof.sum() + 1e-12)
        m2 = float((p * z ** 2).sum())
        m3 = float((p * z ** 3).sum())
        skew = m3 / (m2 ** 1.5 + 1e-12) if m2 > 0 else 0.0
        val = abs(skew)
        status = 'good' if val < 0.4 else ('warn' if val < 0.8 else 'bad')
        return dict(
            name='Spherical aberration', tier='advisory', status=status,
            value=val, unit='|axial skew|',
            headline=f"through-focus asymmetry (skew) = {skew:+.2f}",
            how="Sharpness is profiled through the z-stack; spherical aberration "
                "makes this through-focus curve asymmetric about best focus.",
            good="A near-symmetric axial response (|skew| ≲ 0.4). Strong "
                 "asymmetry suggests a coverslip/coating thickness mismatch — "
                 "adjust the correction collar or use the right coverslip.",
            diag=dict(axial_profile=prof, focus_index=k))
    # 2-D halo proxy (advisory only)
    from scipy.ndimage import gaussian_filter
    hp = a - gaussian_filter(a, 3)
    ring = float(np.mean(np.abs(hp)) / (a.std() + 1e-9))
    return dict(
        name='Spherical aberration', tier='advisory', status='info',
        value=ring, unit='halo proxy',
        headline="single image — provide a z-stack for a real measurement",
        how="Proxy only: residual high-frequency halo energy around structure. "
            "Spherical aberration is properly measured from a bead z-stack "
            "(axial PSF asymmetry).",
        good="For a real assessment, image sub-resolution beads as a z-stack "
             "and look for a symmetric axial PSF.",
        diag=None)


def qc_nyquist(pixel_um, na, wavelength_nm):
    """Spatial (Nyquist) sampling: pixel size vs the optical resolution limit.
    Needs pixel size, objective NA, and emission wavelength."""
    if not (pixel_um and na and wavelength_nm):
        return dict(name='Nyquist sampling', tier='advisory', status='info',
                    value=None, unit='',
                    headline="enter pixel size, NA and wavelength to check",
                    how="Nyquist pixel size = λ / (4·NA).",
                    good="Pixel size ≤ λ/(4·NA) to resolve the optics.",
                    diag=None)
    lam_um = wavelength_nm / 1000.0
    resolution = lam_um / (2.0 * na)      # Abbe lateral resolution
    nyq = lam_um / (4.0 * na)             # Nyquist pixel size
    ratio = pixel_um / nyq
    if ratio <= 1.05:
        status = 'good'
        note = "properly sampled"
    elif ratio <= 2.0:
        status = 'warn'
        note = "marginally undersampled"
    else:
        status = 'bad'
        note = "undersampled — fine detail is lost"
    if ratio < 0.4:
        status = 'warn'
        note = "heavily oversampled (photon-inefficient)"
    return dict(
        name='Nyquist sampling', tier='advisory', status=status,
        value=float(ratio), unit='× Nyquist',
        headline=f"pixel {pixel_um:.3f} µm vs Nyquist {nyq:.3f} µm — {note}",
        how="Abbe resolution = λ/(2·NA); Nyquist pixel = λ/(4·NA). Ratio = your "
            "pixel size ÷ Nyquist pixel.",
        good="Ratio ≈ 1 (pixel ≈ Nyquist). >2 loses resolution; <0.4 wastes "
             "photons and field of view.",
        diag=dict(resolution_um=resolution, nyquist_um=nyq))


def qc_time_sampling(frame_interval_s, process_timescale_s):
    """Temporal Nyquist: frame interval vs the fastest process you want to
    capture. Needs the process timescale from the user."""
    if not (frame_interval_s and process_timescale_s):
        return dict(name='Time sampling', tier='advisory', status='info',
                    value=None, unit='',
                    headline="enter frame interval and process timescale",
                    how="Sample at least twice per process timescale.",
                    good="Frame interval ≤ half the fastest dynamics.",
                    diag=None)
    ratio = frame_interval_s / (process_timescale_s / 2.0)
    status = 'good' if ratio <= 1.0 else ('warn' if ratio <= 2.0 else 'bad')
    return dict(
        name='Time sampling', tier='advisory', status=status, value=float(ratio),
        unit='× Nyquist',
        headline=f"interval {frame_interval_s:g}s vs needed ≤{process_timescale_s/2:g}s",
        how="Temporal Nyquist: to capture a process of timescale τ you must "
            "sample faster than τ/2.",
        good="Frame interval ≤ τ/2. Slower and you alias/miss the dynamics.",
        diag=None)


def qc_chromatic(n_channels):
    """Chromatic aberration needs ≥2 co-registered channels; flag if single."""
    if n_channels and n_channels >= 2:
        return dict(name='Chromatic aberration', tier='advisory', status='info',
                    value=None, unit='',
                    headline="multi-channel — register channels on beads to check",
                    how="Compare the position of the same bead across channels; "
                        "a shift is lateral/axial chromatic aberration.",
                    good="Sub-pixel channel registration on multi-colour beads.",
                    diag=None)
    return dict(name='Chromatic aberration', tier='advisory', status='na',
                value=None, unit='',
                headline="single channel — cannot assess",
                how="Requires ≥2 channels imaged of the same beads.",
                good="Assess with multi-colour bead images.", diag=None)


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------

def run_full_qc(data, pixel_um=None, na=None, wavelength_nm=None,
                frame_interval_s=None, process_timescale_s=None, n_channels=1,
                is_zstack=False):
    """Run every applicable metric and return an ordered list of result dicts."""
    a = np.asarray(data)
    is_stack = a.ndim == 3 and a.shape[0] > 1
    results = [
        qc_saturation(a),
        qc_focus(a),
        qc_snr(a),
        qc_vignetting(a),
        qc_ghosting(a),
    ]
    if is_stack:
        results += [qc_drift(a), qc_vibration(a)]
    results += [
        qc_spherical_aberration(a, is_zstack=is_zstack),
        qc_nyquist(pixel_um, na, wavelength_nm),
        qc_time_sampling(frame_interval_s, process_timescale_s),
        qc_chromatic(n_channels),
    ]
    return results


# ---------------------------------------------------------------------------
# teaching report plot
# ---------------------------------------------------------------------------

_STATUS_COLOR = {'good': '#2ca02c', 'warn': '#ff9800', 'bad': '#d62728',
                 'info': '#1f77b4', 'na': '#888888'}
_STATUS_LABEL = {'good': 'GOOD', 'warn': 'CHECK', 'bad': 'POOR',
                 'info': 'INFO', 'na': 'N/A'}


def plot_qc_report(results, title='Data Quality Report', interactive=True):
    """Render a teaching QC report: a colour-coded scorecard plus a diagnostic
    panel for every metric that produced one, each captioned with how it is
    measured and what good data looks like."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    diag_metrics = [r for r in results if r.get('diag')]
    n_diag = len(diag_metrics)
    ncols = 3
    nrows_diag = int(np.ceil(n_diag / ncols)) if n_diag else 0

    import textwrap

    # Taller scorecard: each metric gets a score line + a teaching line.
    fig = plt.figure(figsize=(12.5, 4.6 + 3.1 * nrows_diag))
    gs = GridSpec(1 + nrows_diag, ncols, figure=fig,
                  height_ratios=[len(results) * 0.62] + [1] * nrows_diag,
                  hspace=0.85, wspace=0.28)

    # --- scorecard (spans the top row) ---
    ax = fig.add_subplot(gs[0, :]); ax.axis('off')
    ax.set_title(title, fontsize=14, fontweight='bold', loc='left')

    # Overall verdict banner — orient the reader before the details.
    n_bad = sum(1 for r in results if r['status'] == 'bad')
    n_warn = sum(1 for r in results if r['status'] == 'warn')
    if n_bad:
        verdict = f"{n_bad} metric(s) look poor and {n_warn} worth checking — see the guidance below."
        vcol = _STATUS_COLOR['bad']
    elif n_warn:
        verdict = f"No serious problems; {n_warn} metric(s) worth a look."
        vcol = _STATUS_COLOR['warn']
    else:
        verdict = "All assessed metrics look good."
        vcol = _STATUS_COLOR['good']
    ax.text(0.005, 1.02, verdict, transform=ax.transAxes, fontsize=10.5,
            fontweight='bold', color=vcol, va='bottom')

    y = 0.98
    dy = 1.0 / (len(results) + 0.5)
    for r in results:
        col = _STATUS_COLOR.get(r['status'], '#888')
        ax.add_patch(plt.Rectangle((0.005, y - dy * 0.62), 0.016, dy * 0.5,
                                    color=col, transform=ax.transAxes, clip_on=False))
        # line 1 — the score
        ax.text(0.03, y - dy * 0.28,
                f"{_STATUS_LABEL.get(r['status'],''):5}  {r['name']}",
                fontsize=10, fontweight='bold', color=col, va='center',
                transform=ax.transAxes)
        ax.text(0.30, y - dy * 0.28, r['headline'], fontsize=8.5, color='0.2',
                va='center', transform=ax.transAxes)
        # line 2 — the teaching / guidance (what good looks like + how to improve)
        teach = r.get('good', '') or r.get('how', '')
        teach = textwrap.shorten(teach, width=155, placeholder=" …")
        ax.text(0.03, y - dy * 0.72, "→ " + teach, fontsize=7.8,
                color='0.45', style='italic', va='center', transform=ax.transAxes)
        y -= dy

    # --- diagnostic panels, each captioned with HOW it is measured ---
    for i, r in enumerate(diag_metrics):
        row = 1 + i // ncols
        col = i % ncols
        dax = fig.add_subplot(gs[row, col])
        d = r['diag']
        c = _STATUS_COLOR.get(r['status'], '#1f77b4')
        try:
            if 'hist_counts' in d:                      # saturation
                edges = d['hist_edges']
                dax.bar(0.5 * (edges[:-1] + edges[1:]), d['hist_counts'],
                        width=np.diff(edges), color='0.6', log=True)
                dax.axvline(d['ceiling'], color=c, ls='--', lw=1.2)
                dax.set_xlabel('intensity (dashed = ceiling)')
                dax.set_ylabel('count (log)')
            elif 'radial_profile' in d:                 # vignetting
                dax.plot(d['radius_bins'], d['radial_profile'], color=c)
                dax.set_xlabel('radius (px)'); dax.set_ylabel('mean intensity')
            elif 'per_frame' in d:                      # focus
                dax.plot(d['per_frame'], '-o', ms=3, color=c)
                dax.axhline(d['median'], color='0.5', ls='--', lw=0.8)
                dax.axhline(0.5 * d['median'], color='#d62728', ls=':', lw=0.8)
                dax.set_xlabel('frame'); dax.set_ylabel('sharpness')
            elif 'cepstrum' in d:                       # ghosting
                C = d['cepstrum']; h, w = C.shape
                dax.imshow(C, cmap='magma', vmax=np.percentile(C, 99.5),
                           extent=[-w//2, w//2, -h//2, h//2])
                dax.set_xlabel('offset x (px)'); dax.set_ylabel('offset y (px)')
            elif 'magnitude' in d:                      # drift
                dax.plot(d['magnitude'], '-o', ms=3, color=c)
                dax.set_xlabel('frame'); dax.set_ylabel('drift (px)')
            elif 'spectrum' in d:                       # vibration
                dax.plot(d['spectrum'][1:], color=c)
                dax.set_xlabel('frequency bin'); dax.set_ylabel('power')
            elif 'axial_profile' in d:                  # spherical
                dax.plot(d['axial_profile'], '-o', ms=3, color=c)
                dax.axvline(d['focus_index'], color='0.5', ls='--', lw=0.8)
                dax.set_xlabel('z slice'); dax.set_ylabel('sharpness')
        except Exception:
            dax.axis('off')
        dax.set_title(r['name'], fontsize=10, color=c, fontweight='bold')
        dax.tick_params(labelsize=7)
        # caption: how this metric is measured (teaching)
        how = textwrap.fill("How: " + r.get('how', ''), width=52)
        dax.text(0.0, -0.42, how, transform=dax.transAxes, fontsize=6.8,
                 color='0.45', va='top', ha='left')

    fig.text(0.01, 0.005,
             "CORE metrics use absolute thresholds; ADVISORY metrics (spherical, "
             "Nyquist, time, vibration, chromatic) are heuristics or need "
             "optics/timing input. The italic line under each metric is what "
             "good data looks like / how to improve it.",
             fontsize=7.5, color='0.4')
    if interactive:
        plt.show(block=False)
    return fig
