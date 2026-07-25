"""Image-acquisition QC — the **report** (presentation only) (data_qc_decomposition).

`plot_qc_report` renders the multi-panel QC summary (per-check status rows, the focus/exposure panels, the
sampling gauge) from the results the check families produce. Presentation only — no science. Moved VERBATIM
out of `data_qc_tools`, which re-exports it; pinned by the QC report tests.
"""
from __future__ import annotations

import numpy as np


_STATUS_COLOR = {'good': '#2ca02c', 'warn': '#ff9800', 'bad': '#d62728',
                 'info': '#1f77b4', 'na': '#888888'}
_STATUS_LABEL = {'good': 'GOOD', 'warn': 'CHECK', 'bad': 'POOR',
                 'info': 'INFO', 'na': 'N/A'}


def plot_qc_report(results, title='Data Quality Report', interactive=True, reliability_scores=None):
    """Render a teaching QC report: a colour-coded scorecard plus a diagnostic
    panel for every metric that produced one, each captioned with how it is
    measured and what good data looks like.

    ``reliability_scores`` is an optional iterable of ``(label, ReliabilityScore)`` for the scored
    measurement family; when given, a footer section lists the measurements whose reliability is capped
    below `high` and why — so the report says which numbers to trust less."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    # ── A metric with no RENDERER got an EMPTY panel ────────────────────────────
    #
    # The panel dispatch is a chain of ``if 'key' in diag``, and three metrics return diag dicts
    # of **scalars** with no matching branch — SNR (``signal``/``noise``), Focus
    # (``edge_width_px``/``diffraction_px``) and Nyquist (``resolution_um``/``nyquist_um``).
    # Each got an axes drawn, labelled, and left **completely blank**: three empty boxes with
    # 0-to-1 axes in the middle of the report.
    #
    # They are scalars, but they are scalars WITH A REFERENCE — a measured value against a
    # threshold — and that is exactly what a bar-against-a-line shows. So they are rendered,
    # rather than dropped: *the comparison is the whole point of those three checks.*
    _RENDERABLE = ('hist_counts', 'radial_profile', 'per_frame', 'cepstrum', 'shifts',
                   'spectrum', 'axial_profile', 'signal', 'edge_width_px', 'resolution_um')
    diag_metrics = [r for r in results
                    if r.get('diag') and any(k in r['diag'] for k in _RENDERABLE)]
    n_diag = len(diag_metrics)
    ncols = 3
    nrows_diag = int(np.ceil(n_diag / ncols)) if n_diag else 0

    import textwrap

    # Taller scorecard: each metric gets a score line + a teaching line.
    # Each diagnostic row needs room for the plot AND its caption underneath.
    # ── Size the scorecard from its CONTENT, not from a magic ratio ─────────────
    #
    # The rows are laid out in FRACTIONAL axes coordinates (``dy = 1/(n+0.5)``), so they always
    # fill whatever height the axes is given. A ratio that is too large leaves a huge empty box
    # with the scorecard in its upper third; too small and the teaching line under each check
    # collides with the check below it. **Both happened**, on successive attempts to tune it.
    #
    # Each check needs a score line and a teaching line: ~0.30 inches. Give the axes exactly
    # that, plus room for the title and verdict, and the fractional layout then fills it
    # correctly at any number of checks.
    # ── The row height must be what the FONTS need, in inches ──────────────────
    #
    # Each row draws a 10 pt score line and a 7.8 pt teaching line, at fixed FRACTIONS of the
    # row height (``dy``). ``dy`` is a fraction of the AXES, so if the axes is short the two
    # lines collide — and if it is tall they scatter with a huge empty gap. **Both happened**,
    # on successive attempts to tune the ratio by eye.
    #
    # 10 pt + 7.8 pt + leading ~= 0.36 inches of ink per row. Give each row that much, and the
    # fractional placement inside it then works at any number of checks.
    _ROW_IN = 0.36
    _rows_in = _ROW_IN * len(results)              # the scorecard rows themselves
    _score_in = 0.75 + _rows_in                    # title + verdict + the rows
    _diag_in = 4.5 * nrows_diag                    # plot + its caption underneath
    # ── SubFigure + constrained_layout: overlap becomes STRUCTURALLY impossible ─
    #
    # Eight attempts to hand-tune the geometry all failed, and each fix on one report size
    # re-created the problem on the other. **The scorecard is a text LIST and the panels are a
    # plot GRID** — they have nothing in common, and forcing them into one coordinate system is
    # what caused every overlap. The panels' tick labels extend ABOVE their axes box, so a grid
    # whose top is flush with the scorecard still collides with it: measured, the scorecard's
    # last row ended at y = 923 px and the histogram's topmost tick reached y = 988 — **a 65 px
    # overlap that looked fine by eye.**
    #
    # ``SubFigure`` with ``constrained_layout`` packs the grid — tick labels, titles and
    # captions included — **by construction.** The scorecard gets its own subfigure and is laid
    # out as what it is: a list. Neither can intrude on the other, and the mechanical overlap
    # test (``test_the_report_has_no_overlapping_text``) is what proved it.
    _fig_h = _score_in + _diag_in
    # constrained_layout on the PARENT; SubFigures inherit it.
    fig = plt.figure(figsize=(12.5, _fig_h), constrained_layout=bool(nrows_diag))

    if nrows_diag:
        _sf_score, _sf_diag = fig.subfigures(
            2, 1, height_ratios=[_score_in, _diag_in])
        gs = _sf_diag.add_gridspec(nrows_diag, ncols)
    else:
        _sf_score, _sf_diag, gs = fig, None, None

    ax = _sf_score.add_axes([0.005, 0.02, 0.99, 0.80])
    ax.axis('off')
    _sf_score.suptitle(title, fontsize=14, fontweight='bold', x=0.012,
                       ha='left', y=0.99)

    # Overall verdict banner — orient the reader before the details.
    # ── "All assessed metrics look good" is technically true and practically a trap ──
    #
    # The verdict counted only `bad` and `warn`. On an image with no pixel size, no NA and no
    # frame interval, **only 4 of 12 checks actually run** — Nyquist, time sampling, chromatic,
    # drift, vibration, photobleaching and spherical aberration are all skipped — and the report
    # still said *"All assessed metrics look good."*
    #
    # The word "assessed" is doing enormous work there, and no user reads it that way. They read
    # "my data is good". **A report that looks clean because most of it did not run is the exact
    # bait this module exists to prevent**, and the fix is to say the coverage out loud.
    n_bad = sum(1 for r in results if r['status'] == 'bad')
    n_warn = sum(1 for r in results if r['status'] == 'warn')
    n_assessed = sum(1 for r in results if r['status'] in ('good', 'warn', 'bad'))
    n_skipped = len(results) - n_assessed
    if n_bad:
        verdict = f"{n_bad} metric(s) look poor and {n_warn} worth checking — see the guidance below."
        vcol = _STATUS_COLOR['bad']
    elif n_warn:
        verdict = (f"No serious problems; {n_warn} metric(s) worth a look. "
                   f"({n_assessed} of {len(results)} checks ran)")
        vcol = _STATUS_COLOR['warn']
    else:
        verdict = (f"All {n_assessed} checks that ran look good."
                   + (f" — but {n_skipped} could NOT run (missing metadata, or the wrong "
                      f"kind of data). This is not a clean bill of health."
                      if n_skipped else ""))
        vcol = _STATUS_COLOR['good']
    # ── Title and verdict must be in the SAME coordinate system ─────────────────
    #
    # The title is a ``suptitle`` (FIGURE fractions) and the verdict was ``ax.text`` (AXES
    # fractions). As the figure grows taller — a stack report is 18.6 in against a 2-D
    # report's 14.1 — those two track differently, and on the taller one **they collide
    # again.** Fixing the overlap on one report re-created it on the other.
    #
    # Both now live in figure fractions, a fixed number of inches apart, so the spacing is
    # the same at any height.
    _sf_score.text(0.012, 0.90, verdict, fontsize=10.5,
                   fontweight='bold', color=vcol, va='top', ha='left')

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
        row = i // ncols          # GridSpec now owns ONLY the diagnostic grid (no scorecard row)
        col = i % ncols
        dax = _sf_diag.add_subplot(gs[row, col])
        d = r['diag']
        c = _STATUS_COLOR.get(r['status'], '#1f77b4')
        try:
            if 'hist_counts' in d:                      # saturation
                # ── The x-axis spanned 0-65535 and the data was a sliver ────────
                #
                # The histogram is binned over the DATA range, but the ceiling line was drawn at
                # the DTYPE maximum, which forced the axis out to 65535 — so a 12-bit image's
                # entire histogram was compressed into the leftmost 1/16th of the panel and the
                # clipping spike, the thing the panel exists to show, was invisible.
                #
                # The axis now follows the data, and the ceiling line is drawn only if it is
                # actually within view.
                edges = d['hist_edges']
                dax.bar(0.5 * (edges[:-1] + edges[1:]), d['hist_counts'],
                        width=np.diff(edges), color='0.6', log=True)
                _ceil = float(d['ceiling'])
                _dmax = float(edges[-1])
                if _ceil <= _dmax * 1.05:
                    dax.axvline(_ceil, color=c, ls='--', lw=1.2)
                    dax.set_xlabel('intensity (dashed = ceiling)')
                else:
                    dax.set_xlabel(f'intensity  (ceiling {_ceil:.0f}, off-scale)')
                dax.set_xlim(float(edges[0]), _dmax * 1.02)
                dax.set_ylabel('count (log)')
            elif 'signal' in d and 'noise' in d:        # SNR — the value against its floor
                _snr = float(d['signal']) / max(float(d['noise']), 1e-9)
                dax.barh([0], [_snr], color=c, height=0.5)
                dax.axvline(10.0, color='0.3', ls='--', lw=1.0)
                dax.axvline(4.0, color=_STATUS_COLOR['bad'], ls=':', lw=1.0)
                dax.set_yticks([])
                dax.set_xlabel('SNR   (dashed = comfortable 10, dotted = buried 4)')
                dax.set_xlim(0, max(_snr * 1.25, 14))

            elif 'edge_width_px' in d:                  # focus — edge vs the optical limit
                _w = float(d['edge_width_px'])
                _lim = float(d.get('diffraction_px') or np.nan)
                if np.isfinite(_lim):
                    dax.barh([0], [_w], color=c, height=0.5)
                    dax.axvline(_lim, color='0.3', ls='--', lw=1.2)
                    dax.set_xlabel('sharpest edge, px   (dashed = diffraction limit)')
                    dax.set_xlim(0, max(_w * 1.3, _lim * 2.2))
                else:
                    dax.barh([0], [_w], color=c, height=0.5)
                    dax.set_xlabel('sharpest edge, px   (supply NA for the optical limit)')
                dax.set_yticks([])

            elif 'resolution_um' in d:                  # Nyquist — pixel vs the required size
                _res = float(d['resolution_um'])
                _nyq = float(d['nyquist_um'])
                _px = float(d.get('pixel_um') or np.nan)
                dax.barh([0], [_px if np.isfinite(_px) else 0], color=c, height=0.5)
                dax.axvline(_nyq, color='0.3', ls='--', lw=1.2)
                dax.axvline(_res, color='0.6', ls=':', lw=1.0)
                dax.set_yticks([])
                dax.set_xlabel('pixel size, \u00b5m   (dashed = Nyquist, dotted = resolution)')
                dax.set_xlim(0, max(_res * 1.3, (_px if np.isfinite(_px) else 0) * 1.3))

            elif 'radial_profile' in d:                 # vignetting
                # ── Autoscaling made a FLAT field look like a disaster ──────────
                #
                # The panel plotted the raw radial profile on an autoscaled y-axis, so a
                # perfectly flat field — varying by 2 counts out of 200 — was drawn as a wild
                # oscillation filling the panel. **A user looking at that concludes their
                # illumination is a mess when the check said "good".** The picture contradicted
                # the verdict.
                #
                # Normalising to the centre and fixing the axis at 0-1.1 makes a flat field look
                # flat and a vignetted one look vignetted, which is what the panel is for.
                _prof = np.asarray(d['radial_profile'], dtype=float)
                _c0 = float(np.mean(_prof[:max(1, len(_prof) // 8)]))
                if np.isfinite(_c0) and abs(_c0) > 1e-9:
                    dax.plot(d['radius_bins'], _prof / _c0, color=c)
                    dax.axhline(1.0, color='0.75', ls=':', lw=0.8)
                    dax.axhline(0.9, color='0.4', ls='--', lw=1.0)
                    dax.set_ylim(0.0, 1.12)
                    dax.set_ylabel('brightness / centre')
                else:
                    dax.plot(d['radius_bins'], _prof, color=c)
                    dax.set_ylabel('mean intensity')
                dax.set_xlabel('radius (px)   (dashed = the 0.9 threshold)')
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
        # ── The caption was landing on the NEXT ROW's axis labels ──────────────
        #
        # ``y = -0.42`` puts it 42 % of a panel-height below the panel — which, with the row
        # spacing used, is exactly where the row beneath draws its y-label and title. Every
        # caption overlapped the panel below it.
        #
        # Moved further down and given the room to sit in (hspace below), and wrapped so it
        # cannot run into the neighbouring column either.
        # ── constrained_layout cannot see a free-floating text ──────────────────
        #
        # ``ax.text(y=-0.42)`` is placed in AXES coordinates and is invisible to the layout
        # engine, which then packs the panels tightly and lets the captions land on each other
        # and on the footer. Detected mechanically: eight overlapping pairs on the stack report.
        #
        # Folding the caption into the X-LABEL makes it part of the axes' own bounding box, and
        # constrained_layout then reserves room for it — **the caption becomes structurally
        # impossible to overlap**, instead of being tuned away.
        dax.set_xlabel((dax.get_xlabel() or '') + '\n\n' + how,
                       fontsize=6.6, color='0.35', linespacing=1.35, loc='left')

    # ── The footer was a free-floating fig.text, invisible to the layout engine ─
    #
    # ``fig.text(y=0.012)`` sits in FIGURE coordinates, and ``constrained_layout`` does not know
    # it exists — so it packed the panels down onto it, and the footer landed on the last row's
    # caption. (Detected mechanically, not by eye.)
    #
    # It belongs with the scorecard, which is a plain unconstrained subfigure — and it reads
    # better there anyway, next to the legend it is explaining, rather than orphaned at the foot
    # of the page.
    _sf_score.text(0.006, 0.005,
                   "CORE metrics use absolute thresholds; ADVISORY metrics (spherical, "
                   "Nyquist, time, vibration, chromatic) are heuristics or need "
                   "optics/timing input. The italic line under each metric is what "
                   "good data looks like / how to improve it.",
                   fontsize=7.5, color='0.4', va='bottom')

    # ── The reliability footer: which scored measurements are capped below 'high', and why ──────────
    if reliability_scores:
        from pycat.utils.reliability import reliability_report_section
        section = reliability_report_section(reliability_scores)
        if section:
            fig.text(0.99, 0.005, section, fontsize=7, ha='right', va='bottom',
                     family='monospace', color='#8B0000')

    if interactive:
        plt.show(block=False)
    return fig
