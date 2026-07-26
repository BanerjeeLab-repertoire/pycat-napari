"""Acquisition-QC orchestration — `run_full_qc` runs every check family and assembles the report."""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc._base import _not_applicable
from pycat.toolbox.data_qc.exposure import qc_saturation
from pycat.toolbox.data_qc.focus import qc_focus
from pycat.toolbox.data_qc.noise import qc_snr
from pycat.toolbox.data_qc.sampling import qc_nyquist, qc_time_sampling
from pycat.toolbox.data_qc.illumination import qc_vignetting, qc_ghosting
from pycat.toolbox.data_qc.aberration import qc_spherical_aberration, qc_chromatic
from pycat.toolbox.data_qc.stability import qc_photobleaching, qc_drift, qc_vibration
from pycat.toolbox.data_qc.biological import qc_biological_objects

def run_full_qc(data, pixel_um=None, na=None, wavelength_nm=None, channels=None,
                frame_interval_s=None, process_timescale_s=None, n_channels=1,
                is_zstack=False, n_source_frames=None,
                labels=None, modality=None, line_time_s=None,
                object_table=None, parent_labels=None):
    """Run every applicable metric and return an ordered list of result dicts.

    n_source_frames : if `data` is an evenly-spaced SUBSAMPLE of a longer time series (QC caps a long
        movie at `QC_MAX_FRAMES` to bound memory), pass the ORIGINAL frame count so the report can say
        so honestly — QC assessed N of M frames, and a high-frequency vibration check saw a lower
        sampling rate. None means `data` is the whole thing.
    labels, modality, line_time_s : optional inputs for the scan-acquisition-artifact checks
        (`scan_qc_tools`). They are **gated by modality** and only appended when a modality is given —
        scan shear on a widefield image is noise, so when `modality` is None the scan checks are reported
        `na` with the reason, never guessed from pixels.
    object_table, parent_labels : optional inputs for the object-level BIOLOGICAL QC section
        (`qc_biological_objects`). When an object table is supplied, a second QC layer is appended asking
        *"can I trust this object?"* — edge-touching, size/shape/intensity outliers, containment. Flags
        are reported, never used to drop a row. `labels` (above) enables the edge flag; `parent_labels`
        enables containment. Absent → the section is simply not added (additive).
    """
    a = np.asarray(data)
    is_stack = a.ndim == 3 and a.shape[0] > 1
    # ── A check that cannot apply must not RUN. It must not "pass", either. ─────
    #
    # A verdict on a question the data cannot answer is worse than no verdict: the user cannot
    # act on it, and a confident false alarm **discredits the checks that are right**. Audited
    # across 2D fluorescence, brightfield, z-stacks and time series — on CLEAN data, where any
    # warn/bad is by definition a false alarm — and every failure was on the Z-STACK:
    #
    #     check                  2D fluor   brightfield   Z-STACK      time series
    #     Drift                  --         --            **bad**      good
    #     Focus / sharpness      info       info          **warn**     good
    #     Ghosting               good       good          **warn**     good
    #
    # **Drift is the worst.** On a z-stack with ZERO lateral drift it reports **89.2 px, "bad"**
    # — and adding a full pixel per plane of REAL drift moves it only to 100.1. It is not
    # measuring displacement at all: the phase correlation is failing on the sharp-vs-blurred
    # mismatch between focal planes. **A large, alarming, confident number that is blind to the
    # thing it names.**
    #
    # (Z-stack planes ARE acquired sequentially, so lateral drift between them is physically
    # real — this is not a case of an inapplicable question. It is a case of a **broken
    # measurement**, and the honest response is to say the check does not work here rather than
    # to report a number that does not mean what it says.)
    #
    # **Focus** flags 2/21 planes as below half-median sharpness — which is *what a z-stack is*.
    # The outer planes are SUPPOSED to be blurred. Flagging correct data as defective teaches
    # the user to ignore the focus check, which is the one that matters most on a 2D image.
    #
    # **Ghosting** fires on the out-of-focus signal, which is not a double image.
    is_zstack = bool(is_zstack)
    is_timeseries = bool(is_stack) and not is_zstack

    results = [
        qc_saturation(a),
        qc_snr(a),
        qc_vignetting(a),
    ]

    # Focus and ghosting are meaningful PER PLANE, and meaningless ACROSS a focal series.
    if is_zstack:
        results += [
            _not_applicable(
                'Focus / sharpness',
                "A z-stack is SUPPOSED to have blurred planes — that is what a focal series "
                "is. Comparing each plane's sharpness to the median flags the outer planes as "
                "defective when they are correct. Use the spherical-aberration check below, "
                "which asks the question that IS meaningful in z: is the through-focus "
                "response symmetric?"),
            _not_applicable(
                'Ghosting (double image)',
                "Out-of-focus signal from neighbouring planes is not a double image. The "
                "cepstral echo this check looks for is swamped by the defocus blur."),
            _not_applicable(
                'Drift',
                "This check does not work on a focal series. Lateral drift between planes IS "
                "real — they are acquired sequentially — but the phase correlation fails on "
                "the sharp-vs-blurred mismatch and reports a large number regardless: on a "
                "z-stack with ZERO drift it reports 89 px, and a full pixel per plane of real "
                "drift moves it only to 100. **It is blind to the thing it names**, so it is "
                "not reported rather than reported wrongly."),
            _not_applicable(
                'Vibration',
                "Periodicity in a focal series would be a periodic optical artefact, not a "
                "pump or a fan — and this check is not calibrated for that."),
        ]
    else:
        # Focus needs the optics for an ABSOLUTE verdict; without them it still reports the
        # sharpest edge in px, which is comparable across a dataset.
        results += [qc_focus(a, pixel_um=pixel_um, na=na, wavelength_nm=wavelength_nm),
                    qc_ghosting(a)]

    if is_timeseries:
        results += [qc_drift(a), qc_vibration(a), qc_photobleaching(a)]
    elif not is_zstack:
        results += [
            _not_applicable('Drift', "Needs a time series."),
            _not_applicable('Vibration', "Needs a time series."),
            _not_applicable('Photobleaching', "Needs a time series."),
        ]
    results += [
        qc_spherical_aberration(a, is_zstack=is_zstack),
        qc_nyquist(pixel_um, na, wavelength_nm),
        qc_time_sampling(frame_interval_s, process_timescale_s),
        # ── A working check that never receives its data never runs ──────────────
        #
        # `qc_chromatic` MEASURES correctly when handed the channel images — verified: 0.00 px
        # on registered channels, and **3.61 px on a true 3.6 px shift.** But `run_full_qc`
        # passed only the channel COUNT, so it could never do anything but report 'info'.
        #
        # A check that is correct and never invoked is indistinguishable from one that is
        # broken. Pass `channels=[ch1, ch2, ...]` and it gives a verdict.
        qc_chromatic(n_channels, channels=channels),
    ]

    # ── Scan-acquisition-geometry artifacts (gated by modality; never guessed from pixels) ──────
    # Only appended when a modality is supplied. scan_qc_tools does its own per-modality gating and
    # reports `na` with a reason for the checks that do not apply — so the report shows the whole family
    # (greyed where inapplicable) rather than silently omitting it. Scan checks read a single 2D frame.
    if modality is not None:
        try:
            from pycat.toolbox.scan_qc_tools import run_scan_qc
            scan_frame = a[0] if is_stack else a
            results += run_scan_qc(scan_frame, labels=labels, modality=modality,
                                   line_time_s=line_time_s, pixel_um=pixel_um)
        except Exception as _exc:  # broad-ok: scientific_result — an optional add-on QC family must never break the core QC report
            debug_log('run_full_qc: scan-QC checks failed', _exc)

    # ── Object-level biological QC (second QC layer; appended when an object table is given) ─────
    # "Can I trust this OBJECT?" beside the imaging checks. Additive: only runs when a table is passed,
    # never drops a row, and a failure inside it must not break the imaging report.
    if object_table is not None:
        try:
            results += qc_biological_objects(object_table, labels=labels,
                                             parent_labels=parent_labels)
        except Exception as _bexc:  # broad-ok: scientific_result — the object-QC add-on must never break the core report
            debug_log('run_full_qc: biological object-QC failed', _bexc)

    # ── Be honest when QC assessed a SAMPLE, not the whole movie ────────────────
    #
    # A long time series is capped at QC_MAX_FRAMES to bound memory, so the report must say it looked
    # at N of M frames rather than imply it read them all — and flag the one check that sampling
    # changes: vibration is a high-frequency measurement, and a wider inter-frame interval lowers the
    # frequency range it can see.
    if (n_source_frames is not None and is_stack
            and int(n_source_frames) > int(a.shape[0])):
        results.insert(0, dict(
            name='Frames assessed', tier='core', status='info',
            value=float(a.shape[0]), unit='frames',
            headline=f"{int(a.shape[0])} of {int(n_source_frames)} frames "
                     f"(evenly sampled across the acquisition)",
            how="QC is a health check, so it assesses an evenly-spaced sample of a long movie rather "
                "than every frame — this bounds memory and time. Drift, bleaching and focus are "
                "sampled across the whole run; the vibration check sees a lower sampling rate, so it "
                "detects slower motion than a full-rate read would.",
            good="", diag=None))
    return results
