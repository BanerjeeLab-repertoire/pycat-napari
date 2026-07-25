"""Colocalization **over time** — the per-frame coefficient trace (coloc_decomposition).

`coloc_time_trace` runs the chosen colocalization coefficients (from `coloc.metrics`) on each frame of a
two-channel stack and returns the trace as a tidy DataFrame; `plot_per_cell_coloc_time_trace` and
`plot_coloc_time_trace` render it (each with a pick handler for brushing). Moved VERBATIM out of
`pixel_wise_corr_analysis_tools`, which re-exports them — no number changed; pinned by the coloc test net.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pycat.toolbox.coloc.metrics import (
    pearsons_correlation, spearman_r_calculation, kendall_tau_calculation, weighted_tau_calculation,
    manders_overlap, manders_k1_calculation, manders_k2_calculation, li_intensity_correlation)


def coloc_time_trace(stack1, stack2, selected_methods, roi_stack=None,
                     frame_interval_s=1.0, progress_callback=None):
    """Run pixel-wise colocalization frame-by-frame over a time-series (or
    z-stack) and return a tidy per-frame time trace.

    This is the reusable foundation for tracking how colocalization EVOLVES over
    time (e.g. during fusion, maturation, or recruitment) — it can be driven from
    either the colocalization widget or a time-series method. It streams frames one
    at a time (never materialising the whole stack) and applies the same scalar
    coloc metrics used by the single-frame analysis, so the per-frame numbers match
    what the single-frame tool would report on each frame.

    Parameters
    ----------
    stack1, stack2 : (T, H, W) array-like or lazy wrappers — the two channels.
    selected_methods : list of metric names (see the SCALAR_METRICS keys below).
        Only scalar-per-frame metrics are supported here; matrix/plot/Costes
        methods are single-frame-only and are ignored for the trace.
    roi_stack : optional (T, H, W) mask stack, or a single (H, W) mask applied to
        every frame, or None (whole frame).
    frame_interval_s : seconds per frame, for the time_s column.
    progress_callback : optional callable(done, total).

    Returns
    -------
    pandas.DataFrame with columns: frame, time_s, and one column per selected
    scalar metric (the coefficient for that frame). Empty if no scalar metric was
    selected.
    """
    from pycat.file_io.file_io import iter_frames, layer_is_stack

    # The scalar-per-frame metrics (coefficient we can trend over time). Matrix /
    # histogram / Costes / thresholding methods don't reduce to one number per
    # frame, so they're excluded from the trace.
    scalar_metrics = {
        "Pearson's R value": pearsons_correlation,
        "Spearman's R value": spearman_r_calculation,
        "Kendall's Tau value": kendall_tau_calculation,
        "Weighted Tau value": weighted_tau_calculation,
        "Li's ICQ value": li_intensity_correlation,
        "Mander's Overlap Coefficient": manders_overlap,
        "Mander's k1 value": manders_k1_calculation,
        "Mander's k2 value": manders_k2_calculation,
    }
    use = [m for m in selected_methods if m in scalar_metrics]
    if not use:
        return pd.DataFrame(columns=['frame', 'time_s'])

    # Determine frame count from the first stack.
    shp1 = getattr(stack1, 'shape', None)
    n_t = int(shp1[0]) if (shp1 is not None and len(shp1) == 3) else 1

    # A single 2-D ROI is broadcast to every frame; a 3-D ROI is indexed per frame.
    roi_is_stack = roi_stack is not None and layer_is_stack(roi_stack)
    roi_2d = None
    if roi_stack is not None and not roi_is_stack:
        roi_2d = np.asarray(roi_stack)
        if roi_2d.ndim == 3:                 # a wrapper that returned one plane
            roi_2d = roi_2d[0]
        roi_2d = roi_2d > 0

    rows = []
    it1 = iter_frames(stack1)
    it2 = iter_frames(stack2)
    for (t, f1), (_, f2) in zip(it1, it2):
        if roi_is_stack:
            roi_f = (np.asarray(roi_stack[t]) > 0)
        else:
            roi_f = roi_2d
        row = {'frame': int(t), 'time_s': float(t) * float(frame_interval_s)}
        for m in use:
            try:
                coeff, _p = scalar_metrics[m](f1, f2, roi_f)
            except Exception:
                coeff = np.nan
            row[m] = coeff
        rows.append(row)
        if progress_callback is not None:
            try:
                progress_callback(t + 1, n_t)
            except Exception:
                pass

    return pd.DataFrame(rows)


def plot_per_cell_coloc_time_trace(trace_df, metric=None,
                                   title="Per-cell colocalization over time",
                                   on_pick_frame=None):
    """Plot a PER-CELL coloc time trace: one line per cell (identified by
    cell_label) for a chosen metric, vs time. Expects columns frame, time_s,
    cell_label, and metric columns. If metric is None, the first metric column is
    used. Returns the figure (or None).

    on_pick_frame : optional callable(frame_index) — clicking a point jumps the
        viewer to that frame (plot→viewer brushing)."""
    if trace_df is None or trace_df.empty or 'cell_label' not in trace_df:
        return None
    metric_cols = [c for c in trace_df.columns
                   if c not in ('frame', 'time_s', 'cell_label', 'n_cells')]
    if not metric_cols:
        return None
    m = metric if (metric in metric_cols) else metric_cols[0]
    import matplotlib.pyplot as plt
    xcol = 'time_s' if 'time_s' in trace_df else 'frame'
    xlabel = "time (s)" if xcol == 'time_s' else "frame"
    fig, ax = plt.subplots(figsize=(7.8, 4.8))
    pickable = on_pick_frame is not None
    frames_all = trace_df['frame'].values
    xvals_all = trace_df[xcol].values
    for cl, g in trace_df.groupby('cell_label'):
        g = g.sort_values(xcol)
        ln, = ax.plot(g[xcol], g[m], '-o', ms=3, lw=1.2, label=f"cell {int(cl)}")
        if pickable:
            ln.set_picker(5)
    ax.set_xlabel(xlabel); ax.set_ylabel(m)
    ax.set_title(title + ("  (click a point to jump to that frame)"
                          if pickable else ""), fontweight='bold')
    ax.grid(True, alpha=0.15)
    # Only show a legend if the cell count is manageable.
    n_cells = trace_df['cell_label'].nunique()
    if n_cells <= 12:
        ax.legend(fontsize=8, ncol=2)
    else:
        ax.text(0.99, 0.01, f"{n_cells} cells", transform=ax.transAxes,
                ha='right', va='bottom', fontsize=8, color='#888')
    sel_line = ax.axvline(xvals_all[0] if len(xvals_all) else 0,
                          color='#ff8c00', lw=1.5, alpha=0.0)
    if pickable:
        def _on_pick(event):
            try:
                ind = event.ind[0] if hasattr(event, 'ind') and len(event.ind) else None
            except Exception:
                ind = None
            if ind is None:
                return
            # event.artist is the picked cell's line; map its x back to a frame.
            try:
                xd = event.artist.get_xdata()[ind]
            except Exception:
                return
            # nearest frame for that x
            j = int(np.argmin(np.abs(xvals_all - xd)))
            fr = int(frames_all[j])
            try:
                sel_line.set_xdata([xd, xd]); sel_line.set_alpha(0.9)
                event.canvas.draw_idle()
            except Exception:
                pass
            try:
                on_pick_frame(fr)
            except Exception as _e:
                print(f"[PyCAT coloc] per-cell frame-jump failed: {_e}")
        try:
            fig.canvas.mpl_connect('pick_event', _on_pick)
        except Exception:
            pass
    fig.tight_layout()
    try:
        plt.show(block=False)
    except Exception:
        pass
    return fig


def plot_coloc_time_trace(trace_df, title="Colocalization over time",
                          on_pick_frame=None):
    """Plot a coloc time-trace DataFrame (one line per metric vs time). Returns
    the matplotlib figure (or None if nothing to plot).

    on_pick_frame : optional callable(frame_index). If given, the trace markers
        are pickable and clicking a point calls it with that frame's index — used
        to jump the napari viewer to the clicked frame (plot→viewer brushing)."""
    if trace_df is None or trace_df.empty:
        return None
    metric_cols = [c for c in trace_df.columns
                   if c not in ('frame', 'time_s', 'n_cells')]
    if not metric_cols:
        return None
    import matplotlib.pyplot as plt
    x = trace_df['time_s'] if 'time_s' in trace_df else trace_df['frame']
    xlabel = "time (s)" if 'time_s' in trace_df else "frame"
    frames = trace_df['frame'].values if 'frame' in trace_df else np.arange(len(trace_df))
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    pickable = on_pick_frame is not None
    for c in metric_cols:
        ln, = ax.plot(x, trace_df[c], '-o', ms=4, lw=1.4, label=c)
        if pickable:
            ln.set_picker(5)
    ax.set_xlabel(xlabel); ax.set_ylabel("coefficient")
    ax.set_title(title + ("  (click a point to jump to that frame)"
                          if pickable else ""), fontweight='bold')
    ax.grid(True, alpha=0.15); ax.legend(fontsize=8)
    ax.axhline(0, color='#888', lw=0.8, alpha=0.5)
    # A movable marker showing the currently-selected frame.
    sel_line = ax.axvline(x.iloc[0] if hasattr(x, 'iloc') else x[0],
                          color='#ff8c00', lw=1.5, alpha=0.0)

    if pickable:
        xvals = np.asarray(x)
        def _on_pick(event):
            try:
                ind = event.ind[0] if hasattr(event, 'ind') and len(event.ind) else None
            except Exception:
                ind = None
            if ind is None:
                return
            fr = int(frames[ind])
            # Move the selection marker and jump the viewer.
            try:
                sel_line.set_xdata([xvals[ind], xvals[ind]])
                sel_line.set_alpha(0.9)
                event.canvas.draw_idle()
            except Exception:
                pass
            try:
                on_pick_frame(fr)
            except Exception as _e:
                print(f"[PyCAT coloc] frame-jump callback failed: {_e}")
        try:
            fig.canvas.mpl_connect('pick_event', _on_pick)
        except Exception:
            pass

    fig.tight_layout()
    try:
        plt.show(block=False)
    except Exception:
        pass
    return fig
