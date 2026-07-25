"""Sampling QC — Nyquist spatial sampling and temporal-sampling adequacy.

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations


from pycat.utils.general_utils import debug_log

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
        diag=dict(resolution_um=resolution, nyquist_um=nyq, pixel_um=float(pixel_um)))


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
