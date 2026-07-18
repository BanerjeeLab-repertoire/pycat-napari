"""
Shared analysis plots for PyCAT workflows.

These replace table pop-ups with the graph that actually communicates the
result (people read curves, not dataframes). Each function returns the
matplotlib Figure so the caller can save it; with interactive=True it also shows
the window (non-blocking) like the temperature/QC plots.
"""

import numpy as np


# ── One click is ONE selection, even where hundreds of curves converge ───────────────────────
#
# The MSD spaghetti plot draws every trajectory as its own ``Line2D``, and the curves all fan out
# from near the origin. The first fix gave each line ``set_picker(5)`` and tried to collapse the
# resulting flood of ``pick_event``s — matplotlib fires one PER line whose 5px radius contains the
# click — with a zero-delay-timer debounce. **That debounce is intrinsically fragile:** it assumes
# every ``pick_event`` from one press arrives before ``QTimer.singleShot(0)`` fires, which is not a
# safe contract. Depending on redraws and queued Qt/napari callbacks the resolve can run *between*
# groups of picks, so one click still resolves several tracks. (Its unit tests passed only because
# they drove the resolver synchronously — they never reproduced the real interleaving.)
#
# So the mechanism is replaced, not patched. **matplotlib fires exactly ONE ``button_press_event``
# per physical click.** The lines are made non-pickable; a single canvas-level handler scans the
# visible curves once, ranks them by point-to-SEGMENT distance in display pixels (a click can be
# nearest a segment of one curve yet nearest a *vertex* of another — segments are what the eye sees),
# and selects the nearest within a threshold. There is nothing to debounce because there is one event.
#
# For DENSE overlap it always picks the nearest and lets repeated clicks CYCLE through the stack of
# curves under the cursor — a first design that *refused* to choose where curves overlapped meant
# nothing ever got selected, because in real MSD data the curves overlap essentially everywhere. The
# user drives the disambiguation by clicking again, rather than being asked for an uncrowded pixel
# that does not exist. See ``_connect_nearest_curve_click``.

def _segment_distance_px(line, mx, my):
    """Minimum distance in DISPLAY pixels from ``(mx, my)`` to ``line``'s polyline (point-to-SEGMENT).

    Pixels, through the artist's transform, so it is correct on a log-log MSD plot. Segment distance,
    not vertex distance, because a click can sit right on the drawn edge of one curve while being
    closer to a sampled *point* of a neighbour — vertex distance would then select the wrong track.
    """
    try:
        xd, yd = line.get_data()
        xd = np.asarray(xd, float); yd = np.asarray(yd, float)
        if xd.size == 0:
            return float('inf')
        pts = line.get_transform().transform(np.column_stack([xd, yd]))
        if len(pts) == 1:
            return float(np.hypot(pts[0, 0] - mx, pts[0, 1] - my))
        p, q = pts[:-1], pts[1:]                       # segment endpoints
        seg = q - p
        click = np.array([mx, my], float)
        denom = np.maximum(np.sum(seg * seg, axis=1), 1e-12)
        t = np.clip(np.sum((click - p) * seg, axis=1) / denom, 0.0, 1.0)
        proj = p + t[:, None] * seg                    # nearest point on each segment
        return float(np.min(np.hypot(proj[:, 0] - mx, proj[:, 1] - my)))
    except Exception:
        return float('inf')


# How close (display pixels) two clicks must be to count as "the same spot" for cycling.
_CYCLE_TOLERANCE_PX = 6.0


def _connect_nearest_curve_click(fig, ax, line_to_tid, state, apply_selection, *,
                                 radius_px=8.0, notify=None):
    """Connect ONE ``button_press_event`` that always selects a nearby curve, and CYCLES on repeats.

    No ``pick_event``, no debounce — one event per click. The MSD curves overlap densely everywhere,
    so refusing to choose where they overlap (the earlier design) meant nothing ever got selected. The
    honest model for dense data is the opposite: **always pick the nearest curve within ``radius_px``,
    and let repeated clicks at the same spot cycle through the others there** — the user drives the
    disambiguation instead of being asked to find an uncrowded pixel that does not exist.

    * A click near a curve (segment distance ≤ ``radius_px``) selects the nearest, always.
    * Clicking again at (roughly) the same spot advances through the stack of curves under the cursor,
      nearest-first, wrapping around.
    * A click at a NEW spot starts a fresh stack from its nearest curve.
    * Clicks outside ``ax``, in empty space (beyond ``radius_px``), or with a non-left button do
      nothing. Re-clicking a lone track (only one curve near) is a no-op.
    """
    cycle = {'pos': None, 'cands': [], 'idx': -1}

    def _on_click(event):
        if event.inaxes is not ax or getattr(event, 'button', 1) != 1:
            return
        mx, my = getattr(event, 'x', None), getattr(event, 'y', None)
        if mx is None or my is None or not line_to_tid:
            return
        near = sorted((d, tid, ln) for d, tid, ln in
                      ((_segment_distance_px(ln, mx, my), tid, ln)
                       for ln, tid in line_to_tid.items())
                      if d <= radius_px)
        if not near:
            return                                     # empty space — nothing near the click

        same_spot = (cycle['pos'] is not None
                     and abs(mx - cycle['pos'][0]) <= _CYCLE_TOLERANCE_PX
                     and abs(my - cycle['pos'][1]) <= _CYCLE_TOLERANCE_PX)
        if same_spot and len(cycle['cands']) > 1:
            cycle['idx'] = (cycle['idx'] + 1) % len(cycle['cands'])   # advance through the stack
        elif same_spot:
            return                                     # a lone track re-clicked — nothing new
        else:
            cycle['pos'] = (mx, my)                    # a fresh stack, nearest first
            cycle['cands'] = near
            cycle['idx'] = 0
            if notify is not None and len(near) > 1:
                notify(f"{len(near)} tracks overlap here — click again to cycle through them.")

        _d, tid, ln = cycle['cands'][cycle['idx']]
        apply_selection(ln, tid)

    return fig.canvas.mpl_connect('button_press_event', _on_click)


def _default_notify(message):
    """Surface an ambiguity hint, headless-safe."""
    try:
        from pycat.utils.notify import show_info
        show_info(message)
    except Exception:
        print(f"[PyCAT VPT] {message}")


def _show(fig, interactive):
    if interactive:
        import matplotlib.pyplot as plt
        plt.show(block=False)
    return fig


def _band_from_long(df, tids, qs=(10, 50, 90)):
    """Percentile band of msd_um2 at each lag, over the given track_ids.

    Works on the long (track_id, lag_s, msd_um2) frame and is robust to tracks
    having different lag sets — groups by lag_s and takes percentiles across
    whatever tracks have that lag. Returns (lags, band) with band shape
    (len(qs), n_lags) in LOG space (the plot is log-log, so the eye reads the
    spread there)."""
    sub = df[df['track_id'].isin(tids)]
    if sub.empty:
        return None, None
    g = sub.groupby('lag_s')['msd_um2']
    lags = np.array(sorted(sub['lag_s'].unique()), dtype=float)
    band = np.vstack([
        np.array([np.percentile(g.get_group(l).values, q) for l in lags])
        for q in qs
    ])
    with np.errstate(divide='ignore'):
        return lags, np.log(np.clip(band, 1e-12, None))


def representative_track_sample(per_track_df, target_fidelity=0.95,
                                candidates=(50, 100, 150, 200, 300, 400, 600, 800),
                                n_rep=6, seed=0):
    """Choose the SMALLEST number of tracks whose MSD percentile band reproduces
    the full set's band to at least ``target_fidelity``.

    The spaghetti plot exists to show the SPREAD of MSD curves, not each line —
    past a point, extra lines just overplot into the same band and add no visual
    information. Empirically the band converges at a track count that is roughly
    CONSTANT (≈100 for 95% fidelity) whether the set is 500 or 50 000 tracks, so
    this keeps rendering fast and bounded while staying honest about how
    representative the sample is.

    Fidelity = 1 − mean|log-band(sample) − log-band(full)| / (mean 10–90 band
    width): how closely the sample's spread matches the full spread, relative to
    the spread's own size. Returns (chosen_ids, n_total, measured_fidelity). The
    full data is untouched — this governs only how many LINES are drawn.
    """
    if per_track_df is None or len(per_track_df) == 0:
        return [], 0, 1.0
    rng = np.random.default_rng(seed)
    all_tids = per_track_df['track_id'].unique()
    n_all = len(all_tids)
    lags_full, fb = _band_from_long(per_track_df, all_tids)
    if fb is None or n_all <= candidates[0]:
        return list(all_tids), n_all, 1.0
    extent = float((fb[2] - fb[0]).mean()) or 1.0

    def _fidelity(k):
        vals = []
        for _ in range(n_rep):
            ids = rng.choice(all_tids, k, replace=False)
            lags_s, sb = _band_from_long(per_track_df, ids)
            if sb is None:
                continue
            common = np.intersect1d(lags_full, lags_s)
            if common.size == 0:
                continue
            fi = np.searchsorted(lags_full, common)
            si = np.searchsorted(lags_s, common)
            err = np.abs(sb[:, si] - fb[:, fi]).mean()
            vals.append(max(0.0, 1.0 - err / extent))
        return float(np.mean(vals)) if vals else 0.0

    chosen_k = candidates[-1]
    measured = 0.0
    for k in candidates:
        if k >= n_all:
            chosen_k = n_all
            measured = 1.0
            break
        measured = _fidelity(k)
        if measured >= target_fidelity:
            chosen_k = k
            break
    ids = list(rng.choice(all_tids, min(chosen_k, n_all), replace=False))
    return ids, n_all, measured


def plot_msd_trajectories(per_track_df, ensemble_msd_df=None, fit=None,
                          title="MSD", interactive=True, on_pick_track=None,
                          line_registry=None, render_mode='auto',
                          target_fidelity=0.95, max_tracks=None):
    """
    Log-log MSD spaghetti plot: every track's MSD(τ) semi-transparent, the
    ensemble mean MSD as a solid line, and (optionally) the fitted power law.

    Parameters
    ----------
    per_track_df : long DataFrame (track_id, lag_s, msd_um2) from
        per_track_msd_curves().
    ensemble_msd_df : optional DataFrame (lag_s, msd_um2[, msd_sem]) from
        compute_msd() — drawn as the solid mean with an error band.
    fit : optional dict from fit_anomalous_diffusion() — draws 4Dτ^α and labels
        D and α.
    on_pick_track : optional callable(track_id). If given, each per-track line is
        made pickable and clicking one calls this with the track_id — the hook
        the VPT UI uses to highlight that track in the napari Tracks layer and
        centre the viewer on it (plot -> data brushing). Kept as a callback so
        this plotting module stays decoupled from napari/the viewer.
    line_registry : optional dict. If given, it is populated with
        {'lines': {track_id: Line2D}, 'canvas': FigureCanvas} so the caller's
        linked-selection dispatcher can emphasise a track's curve when the
        selection is driven from another view (image/table -> plot brushing).
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    # Faint per-track curves. Each track is a separate matplotlib artist, so
    # drawing thousands at once freezes the window — but more importantly, past a
    # point extra lines add NO visual information: they overplot into the same
    # spread. The spaghetti plot's job is to show that spread (the 10–90%
    # percentile band of MSD across tracks), and the band CONVERGES at a track
    # count that is roughly constant (~100 for 95% fidelity) regardless of the
    # dataset size. So by default we draw the SMALLEST representative sample that
    # reproduces the full band to `target_fidelity`, label the plot honestly with
    # the measured fidelity, and leave the full data untouched for the ensemble
    # mean + fit (which always use ALL tracks). This is a render choice, not a
    # data choice.
    #
    #   render_mode='auto'  → fidelity-targeted sample (default; fast + faithful)
    #   render_mode='all'   → draw every track (progressive, opt-out)
    #   max_tracks=N        → draw a random N (explicit override of the sample)
    _line_to_tid = {}
    _tid_to_line = {}
    _pickable = on_pick_track is not None
    _fidelity_note = None

    def _draw_one_track(tid, g):
        g = g.sort_values('lag_s')
        (ln,) = ax.plot(g['lag_s'], g['msd_um2'], color='#4c72b0',
                        alpha=0.18, lw=0.8, zorder=1)
        if _pickable:
            # NOT pickable — selection is one canvas button_press, nearest-curve. See the note above
            # `_connect_nearest_curve_click` for why per-line pick_event cannot be made reliable here.
            _line_to_tid[ln] = int(tid)
        _tid_to_line[int(tid)] = ln

    if per_track_df is not None and len(per_track_df):
        all_tids = per_track_df['track_id'].unique()
        n_all = len(all_tids)

        # Decide which track ids to DRAW.
        if max_tracks is not None:
            import numpy as _np
            _rng = _np.random.default_rng(0)
            shown = list(_rng.choice(all_tids, min(int(max_tracks), n_all),
                                     replace=False))
            _fidelity_note = None
        elif render_mode == 'all':
            shown = list(all_tids)
            _fidelity_note = None
        else:  # 'auto' — fidelity-targeted representative sample
            shown, _, _fid = representative_track_sample(
                per_track_df, target_fidelity=target_fidelity)
            if len(shown) < n_all:
                _fidelity_note = _fid
            else:
                _fidelity_note = None

        _groups = {int(tid): g for tid, g in
                   per_track_df[per_track_df['track_id'].isin(shown)].groupby('track_id')}
        _order = [int(t) for t in shown if int(t) in _groups]

        # For a small/auto sample (~100 lines) synchronous drawing is instant;
        # only the 'all' opt-out on a big set needs progressive streaming.
        _FIRST_BATCH = 200
        _BATCH = 200
        _BATCH_MS = 30
        for tid in _order[:_FIRST_BATCH]:
            _draw_one_track(tid, _groups[tid])

        # Legend label: report the TRUE track count, how many are shown, and — for
        # the auto sample — the measured band fidelity, so the sample is honest
        # rather than an arbitrary cap.
        _shown_n = len(_order)
        if _fidelity_note is not None:
            _lbl = (f"tracks: showing {_shown_n} of {n_all} "
                    f"(band fidelity ≈{_fidelity_note*100:.0f}%)")
        elif _shown_n < n_all:
            _lbl = f"individual tracks (n={n_all}; showing {_shown_n})"
        else:
            _lbl = f"individual tracks (n={n_all})"
        if on_pick_track is not None:
            _lbl += " — click a track to reveal it"
        ax.plot([], [], color='#4c72b0', alpha=0.5, lw=1.0, label=_lbl)

        # Stream any remaining tracks (only non-empty for 'all'/large max_tracks)
        # on a Qt timer so the UI stays live; synchronous fallback otherwise.
        _rest = _order[_FIRST_BATCH:]
        if _rest:
            _drew_progressively = False
            if interactive:
                try:
                    from PyQt5.QtCore import QTimer
                    _idx = {'i': 0}
                    _timer = QTimer()

                    def _tick():
                        i = _idx['i']
                        batch = _rest[i:i + _BATCH]
                        if not batch:
                            _timer.stop()
                            try:
                                cb = getattr(fig.canvas, '_pycat_recapture_bg', None)
                                if cb is not None:
                                    cb()
                            except Exception:
                                pass
                            try:
                                fig.canvas.draw_idle()
                            except Exception:
                                pass
                            return
                        for tid in batch:
                            _draw_one_track(tid, _groups[tid])
                        _idx['i'] = i + _BATCH
                        try:
                            fig.canvas.draw_idle()
                        except Exception:
                            pass

                    _timer.timeout.connect(_tick)
                    _timer.start(_BATCH_MS)
                    fig._pycat_spaghetti_timer = _timer   # keep a ref (no GC)
                    _drew_progressively = True
                except Exception:
                    _drew_progressively = False
            if not _drew_progressively:
                for tid in _rest:
                    _draw_one_track(tid, _groups[tid])

    # solid ensemble mean with SEM band
    if ensemble_msd_df is not None and len(ensemble_msd_df):
        e = ensemble_msd_df.sort_values('lag_s')
        ax.plot(e['lag_s'], e['msd_um2'], color='#c44e52', lw=2.4,
                zorder=3, label="ensemble mean MSD")
        if 'msd_sem' in e.columns and np.isfinite(e['msd_sem']).any():
            lo = np.clip(e['msd_um2'] - e['msd_sem'], 1e-12, None)
            hi = e['msd_um2'] + e['msd_sem']
            ax.fill_between(e['lag_s'], lo, hi, color='#c44e52',
                            alpha=0.20, zorder=2)

    # fitted power law
    if fit and np.isfinite(fit.get('D_um2_per_s', np.nan)):
        tf = np.asarray(fit['fit_lags_s']); mf = np.asarray(fit['fit_msd'])
        if tf.size:
            ax.plot(tf, mf, '--', color='k', lw=1.6, zorder=4,
                    label=(f"fit: D={fit['D_um2_per_s']:.3g} µm²/s, "
                           f"α={fit['alpha']:.2f} ({fit.get('motion_type','')})"))

    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xlabel("lag time τ (s)")
    ax.set_ylabel("MSD ⟨Δr²⟩ (µm²)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, which='both', alpha=0.15)
    ax.legend(fontsize=8, loc='upper left')
    fig.tight_layout()

    # Plot -> data brushing: clicking a per-track line calls back with its
    # track_id (the VPT UI uses this to highlight the track in napari and centre
    # the viewer). The last-picked line is emphasised so the selection is visible
    # on the plot too.
    #
    # SPEED: a spaghetti plot has hundreds of lines, so a full-figure redraw on
    # every pick (canvas.draw_idle) is what made selection lag. Instead we BLIT:
    # capture the axes background once, and on each selection restore that
    # background and redraw ONLY the two lines that changed (the previously- and
    # newly-highlighted track). That is O(1) in the number of tracks.
    _blit = {'bg': None, 'canvas': fig.canvas, 'ax': ax}

    def _capture_bg(*_a):
        try:
            _blit['bg'] = fig.canvas.copy_from_bbox(ax.bbox)
        except Exception:
            _blit['bg'] = None
    # Let the progressive draw-in timer refresh the cached background once all
    # batches are drawn, so picking is fast over the full (streamed-in) set.
    try:
        fig.canvas._pycat_recapture_bg = _capture_bg
    except Exception:
        pass

    def _blit_highlight(new_line, prev_line):
        """Restore the cached background and re-draw only the changed lines."""
        canvas = _blit['canvas']
        bg = _blit['bg']
        try:
            if bg is None:
                _capture_bg()
                bg = _blit['bg']
            if bg is not None:
                canvas.restore_region(bg)
                for _ln in (prev_line, new_line):
                    if _ln is not None:
                        ax.draw_artist(_ln)
                canvas.blit(ax.bbox)
                canvas.flush_events()
            else:
                canvas.draw_idle()   # fallback if background capture failed
        except Exception:
            try:
                canvas.draw_idle()
            except Exception:
                pass

    # Re-capture the background whenever the figure is redrawn (resize, pan, zoom,
    # first draw) so the cached bitmap stays valid.
    try:
        fig.canvas.mpl_connect('draw_event', _capture_bg)
    except Exception:
        pass

    if on_pick_track is not None and _line_to_tid:
        _state = {'prev': None}

        def _apply_pick(ln, tid):
            # The handler already ruled out a re-click of the selected line, so this only ever runs
            # for a NEW selection: restyle the previous, emphasise this one, fire once.
            prev = _state['prev']
            if prev is not None and prev in _line_to_tid:
                prev.set_color('#4c72b0'); prev.set_alpha(0.18); prev.set_linewidth(0.8)
                prev.set_zorder(1)
            ln.set_color('#ff8c00'); ln.set_alpha(1.0); ln.set_linewidth(2.2)
            ln.set_zorder(5)
            _state['prev'] = ln
            _blit_highlight(ln, prev)
            try:
                on_pick_track(tid)
            except Exception as _e:
                print(f"[PyCAT VPT] pick callback failed: {_e}")

        # One button_press per click -> the single nearest curve. No pick_event, no debounce.
        try:
            _connect_nearest_curve_click(fig, ax, _line_to_tid, _state, _apply_pick,
                                         notify=_default_notify)
        except Exception:
            pass

    # Expose the track_id -> Line2D map + canvas so a linked-selection dispatcher
    # can highlight a curve when the selection comes from another view.
    # `blit_highlight` lets the dispatcher redraw only the changed lines too,
    # instead of a second full-figure redraw. `state` is SHARED with the pick
    # handler's `_state` so a plot-pick and a dispatcher-driven highlight track
    # the same "previously highlighted line" (no stale double-highlight).
    if line_registry is not None:
        try:
            line_registry['lines'] = _tid_to_line
            line_registry['canvas'] = fig.canvas
            line_registry['state'] = _state if (on_pick_track is not None and _line_to_tid) else {'prev': None}
            line_registry['blit_highlight'] = _blit_highlight
        except Exception:
            pass

    return _show(fig, interactive)


def _draw_centered_tracks(ax, tracks_df, max_tracks=400):
    """Draw every trajectory translated so it starts at (0,0) at its first frame,
    overlaid on one x–y axes. The fan-out from the origin shows the spatial spread
    of the ensemble; for Brownian motion the cloud is isotropic and grows with
    time. Draws onto a supplied Axes (so it can be a subplot or its own window)."""
    if tracks_df is None or not len(tracks_df):
        ax.text(0.5, 0.5, "No tracks", ha='center', va='center')
        return
    tids = tracks_df['track_id'].unique()
    tids = tids[tids >= 0]
    if len(tids) > max_tracks:
        rng = np.random.default_rng(0)
        tids = rng.choice(tids, max_tracks, replace=False)
    for tid in tids:
        g = tracks_df[tracks_df['track_id'] == tid].sort_values('frame')
        if len(g) < 2:
            continue
        x = g['x_um'].values.astype(float)
        y = g['y_um'].values.astype(float)
        ax.plot(x - x[0], y - y[0], color='#4c72b0', alpha=0.15, lw=0.7)
    ax.set_aspect('equal', adjustable='datalim')
    ax.axhline(0, color='0.7', lw=0.6); ax.axvline(0, color='0.7', lw=0.6)
    ax.set_xlabel("Δx from start (µm)")
    ax.set_ylabel("Δy from start (µm)")
    ax.set_title("Centered trajectories (origin at t=0)", fontweight='bold')
    ax.grid(True, alpha=0.15)


def _draw_van_hove(ax, tracks_df, lag_frames=1, frame_interval_s=1.0, bins=60):
    """Van Hove self-correlation: histogram of single-axis displacements at a
    fixed lag, overlaid with the Gaussian of matching variance. For purely
    Brownian motion the displacement distribution is Gaussian, so a data histogram
    that tracks the dashed Gaussian ⇒ Brownian; heavier tails ⇒ heterogeneous /
    non-Gaussian dynamics. Pools Δx and Δy (isotropic assumption) for statistics.
    Draws onto a supplied Axes."""
    if tracks_df is None or not len(tracks_df):
        ax.text(0.5, 0.5, "No tracks", ha='center', va='center')
        return
    disps = []
    for tid, g in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        g = g.sort_values('frame')
        t = g['frame'].values.astype(int)
        x = g['x_um'].values.astype(float)
        y = g['y_um'].values.astype(float)
        f0, f1 = t.min(), t.max()
        span = f1 - f0 + 1
        xs = np.full(span, np.nan); ys = np.full(span, np.nan)
        xs[t - f0] = x; ys[t - f0] = y
        if lag_frames >= span:
            continue
        dx = xs[lag_frames:] - xs[:-lag_frames]
        dy = ys[lag_frames:] - ys[:-lag_frames]
        disps.append(dx[np.isfinite(dx)])
        disps.append(dy[np.isfinite(dy)])
    if not disps:
        ax.text(0.5, 0.5, "Not enough data at this lag", ha='center', va='center')
        return
    d = np.concatenate(disps)
    d = d[np.isfinite(d)]
    if d.size < 10:
        ax.text(0.5, 0.5, "Too few displacements", ha='center', va='center')
        return
    sigma = np.std(d)
    ax.hist(d, bins=bins, density=True, color='#4c72b0', alpha=0.55,
            label=f"data (σ={sigma:.3g} µm)")
    # Gaussian reference (same σ) — the Brownian expectation.
    xs_g = np.linspace(d.min(), d.max(), 200)
    if sigma > 0:
        gauss = np.exp(-xs_g ** 2 / (2 * sigma ** 2)) / (sigma * np.sqrt(2 * np.pi))
        ax.plot(xs_g, gauss, 'r--', lw=1.8, label="Gaussian (Brownian)")
    # A non-Gaussian parameter α₂ = ⟨d⁴⟩/(3⟨d²⟩²) − 1 (0 for a Gaussian).
    m2 = np.mean(d ** 2); m4 = np.mean(d ** 4)
    a2 = (m4 / (3 * m2 ** 2) - 1.0) if m2 > 0 else np.nan
    lag_s = lag_frames * frame_interval_s
    ax.set_title(f"Van Hove displacement dist. (lag={lag_s:.3g}s, α₂={a2:.2f})",
                 fontweight='bold')
    ax.set_xlabel("displacement (µm)")
    ax.set_ylabel("probability density")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.15)


def _draw_msd_into(ax, per_track_df, ensemble_msd_df=None, fit=None,
                   max_spaghetti=400, line_registry=None, pickable=False):
    """Draw the MSD spaghetti + ensemble mean + fit onto a supplied Axes (the
    consolidated-panel version). If line_registry is given it is populated with
    {track_id: Line2D} (and, when pickable, each line is given a pick radius) so
    the consolidated panel supports the same table↔plot brushing as the
    standalone plot."""
    tid_to_line = {}
    if per_track_df is not None and len(per_track_df):
        tids = per_track_df['track_id'].unique()
        n_all = len(tids)
        if n_all > max_spaghetti:
            rng = np.random.default_rng(0)
            shown = set(rng.choice(tids, max_spaghetti, replace=False))
            pdf = per_track_df[per_track_df['track_id'].isin(shown)]
        else:
            pdf = per_track_df
        for tid, g in pdf.groupby('track_id'):
            g = g.sort_values('lag_s')
            (ln,) = ax.plot(g['lag_s'], g['msd_um2'], color='#4c72b0',
                            alpha=0.18, lw=0.8)
            # NOT pickable: the panel selects via one canvas button_press (nearest curve), not
            # per-line pick_event. See `_connect_nearest_curve_click`.
            tid_to_line[int(tid)] = ln
        ax.plot([], [], color='#4c72b0', alpha=0.5, lw=1.0,
                label=f"individual tracks (n={n_all})")
    if line_registry is not None:
        line_registry['lines'] = tid_to_line
        line_registry.setdefault('state', {'prev': None})
    if ensemble_msd_df is not None and len(ensemble_msd_df):
        e = ensemble_msd_df.sort_values('lag_s')
        ax.plot(e['lag_s'], e['msd_um2'], color='#c44e52', lw=2.4,
                label="ensemble mean MSD")
    if fit and np.isfinite(fit.get('D_um2_per_s', np.nan)):
        tf = np.asarray(fit['fit_lags_s']); mf = np.asarray(fit['fit_msd'])
        if tf.size:
            ax.plot(tf, mf, '--', color='k', lw=1.6,
                    label=(f"fit: D={fit['D_um2_per_s']:.3g} µm²/s, "
                           f"α={fit['alpha']:.2f}"))
    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xlabel("lag time τ (s)"); ax.set_ylabel("MSD ⟨Δr²⟩ (µm²)")
    ax.set_title("MSD (per-track + ensemble)", fontweight='bold')
    ax.grid(True, which='both', alpha=0.15); ax.legend(fontsize=7, loc='upper left')


def _draw_moduli_into(ax, moduli_df):
    """Draw G′/G″ (Evans) onto a supplied Axes (consolidated-panel version).

    Negative moduli are NOT clipped. The previous version did
    ``np.clip(g_prime, 1e-12, None)`` on a log axis, which silently mapped every
    negative G′ onto the bottom of the plot as a positive-looking point — turning
    "the Evans conversion is not locally valid here" into a tidy curve that appears
    to be a measured elastic modulus.

    This matters most in exactly the regime PyCAT is used for. Biological condensates
    are viscous-dominated at accessible lag times (roughly water-to-honey and beyond),
    so the true G′ is small and any G′/G″ crossover lies outside the measurable
    window. Noise therefore pushes G′ negative *routinely*, and that is the honest
    signal: there is no measurable elasticity at that frequency. Drawing it as a
    positive floor value manufactures the appearance of one.

    So: valid (positive) points are drawn as a line; invalid (≤0) points are marked
    at the axis floor with an open marker and counted in the legend.
    """
    if moduli_df is None or not len(moduli_df):
        ax.text(0.5, 0.5, "No moduli", ha='center', va='center'); return
    d = moduli_df.sort_values('omega_rad_s')
    w = d['omega_rad_s'].values

    def _plot_modulus(vals, colour, marker, label):
        vals = np.asarray(vals, dtype=float)
        good = np.isfinite(vals) & (vals > 0)
        bad = np.isfinite(vals) & (vals <= 0)
        n_bad = int(bad.sum())
        lab = label if not n_bad else f"{label}  [{n_bad}/{len(vals)} invalid]"
        if good.any():
            ax.plot(w[good], vals[good], '-' + marker, ms=3, color=colour, label=lab)
        else:
            # Nothing valid at all — still register the label so the reader knows.
            ax.plot([], [], '-' + marker, ms=3, color=colour, label=lab)
        return good, bad, n_bad

    g1 = d['g_prime_pa'].values
    g2 = d['g_double_prime_pa'].values
    good1, bad1, nbad1 = _plot_modulus(g1, '#c44e52', 'o', "G′ (storage)")
    good2, bad2, nbad2 = _plot_modulus(g2, '#4c72b0', 's', "G″ (loss)")

    ax.set_xscale('log'); ax.set_yscale('log')

    # Mark the invalid points explicitly at the bottom of the axes, rather than
    # pretending they are small positive numbers.
    if bad1.any() or bad2.any():
        try:
            ymin = ax.get_ylim()[0]
            if bad1.any():
                ax.plot(w[bad1], np.full(bad1.sum(), ymin), 'x', ms=6,
                        color='#c44e52', alpha=0.8, clip_on=False)
            if bad2.any():
                ax.plot(w[bad2], np.full(bad2.sum(), ymin), 'x', ms=6,
                        color='#4c72b0', alpha=0.8, clip_on=False)
            ax.text(0.02, 0.02,
                    "×  modulus ≤ 0 — Evans conversion not locally valid\n"
                    "    (expected where the material is viscous-dominated:\n"
                    "     there is no measurable elasticity at that frequency).\n"
                    "    Passive VPT cannot resolve a G′/G″ crossover here —\n"
                    "    use active microrheology (optical tweezers) for that.",
                    transform=ax.transAxes, ha='left', va='bottom', fontsize=6.5,
                    color='#999')
        except Exception:
            pass

    ax.set_xlabel("angular frequency ω (rad/s)"); ax.set_ylabel("modulus (Pa)")
    ax.set_title("Complex moduli G′/G″ — Evans method", fontweight='bold')
    ax.grid(True, which='both', alpha=0.15); ax.legend(fontsize=8)


def plot_vpt_panel(per_track_df, ensemble_msd_df=None, fit=None, moduli_df=None,
                   tracks_df=None, frame_interval_s=1.0, van_hove_lag=1,
                   consolidated=True, interactive=True,
                   line_registry=None, on_pick_track=None):
    """Render the four VPT plots — MSD spaghetti, Evans G′/G″, centered
    trajectories, and the van Hove displacement distribution — either as a single
    2×2 subplot figure (consolidated, the default) or as four separate windows.
    A button on the consolidated window re-renders the other way live.

    tracks_df : the raw per-detection tracks (track_id, frame, x_um, y_um), used
        by the two spread plots. If None, those panels show a placeholder.
    line_registry : optional dict, populated with the consolidated MSD panel's
        {track_id: Line2D} map + canvas so table/image selections can highlight a
        curve here too (consolidated-mode brushing).
    on_pick_track : optional callable(track_id); if given, the consolidated MSD
        panel's per-track lines are pickable and clicking one calls it (plot→other
        brushing from the consolidated panel).
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    def _render_separate():
        figs = []
        f1, a1 = plt.subplots(figsize=(6.2, 5.0)); _draw_msd_into(a1, per_track_df, ensemble_msd_df, fit); f1.tight_layout(); figs.append(f1)
        f2, a2 = plt.subplots(figsize=(6.0, 5.0)); _draw_moduli_into(a2, moduli_df); f2.tight_layout(); figs.append(f2)
        f3, a3 = plt.subplots(figsize=(5.4, 5.2)); _draw_centered_tracks(a3, tracks_df); f3.tight_layout(); figs.append(f3)
        f4, a4 = plt.subplots(figsize=(6.0, 4.6)); _draw_van_hove(a4, tracks_df, van_hove_lag, frame_interval_s); f4.tight_layout(); figs.append(f4)
        if interactive:
            plt.show(block=False)
        return figs

    def _render_consolidated():
        fig, axes = plt.subplots(2, 2, figsize=(12.5, 10.0))
        _pickable = on_pick_track is not None
        _reg = line_registry if line_registry is not None else {}
        _draw_msd_into(axes[0, 0], per_track_df, ensemble_msd_df, fit,
                       line_registry=_reg, pickable=_pickable)
        _draw_moduli_into(axes[0, 1], moduli_df)
        _draw_centered_tracks(axes[1, 0], tracks_df)
        _draw_van_hove(axes[1, 1], tracks_df, van_hove_lag, frame_interval_s)
        fig.suptitle("VPT microrheology", fontweight='bold', fontsize=13)
        fig.tight_layout(rect=[0, 0.03, 1, 0.97])
        # Register the canvas so external (table/image) selections can redraw.
        if line_registry is not None:
            line_registry['canvas'] = fig.canvas
        # Plot→other brushing from the consolidated MSD panel.
        if _pickable:
            _lines = _reg.get('lines', {})
            _line_to_tid = {ln: tid for tid, ln in _lines.items()}
            _pstate = {'prev': None}
            _msd_ax = axes[0, 0]
            # BLIT: the panel is a 2×2 grid, so a full-figure redraw on each pick
            # is even more expensive than the single-plot case. Cache the MSD
            # subplot's background and redraw only the changed lines within it.
            _pblit = {'bg': None}

            def _pcapture(*_a):
                try:
                    _pblit['bg'] = fig.canvas.copy_from_bbox(_msd_ax.bbox)
                except Exception:
                    _pblit['bg'] = None

            def _pblit_highlight(new_line, prev_line):
                try:
                    if _pblit['bg'] is None:
                        _pcapture()
                    bg = _pblit['bg']
                    if bg is not None:
                        fig.canvas.restore_region(bg)
                        for _ln in (prev_line, new_line):
                            if _ln is not None:
                                _msd_ax.draw_artist(_ln)
                        fig.canvas.blit(_msd_ax.bbox)
                        fig.canvas.flush_events()
                    else:
                        fig.canvas.draw_idle()
                except Exception:
                    try:
                        fig.canvas.draw_idle()
                    except Exception:
                        pass

            try:
                fig.canvas.mpl_connect('draw_event', _pcapture)
            except Exception:
                pass
            # Share state + blit with the dispatcher so image/table→plot is fast too.
            if line_registry is not None:
                line_registry['state'] = _pstate
                line_registry['blit_highlight'] = _pblit_highlight

            def _apply_pick(artist, tid):
                # New selection only — the handler already suppressed a re-click of the same line.
                prev = _pstate['prev']
                if prev is not None and prev in _line_to_tid:
                    try:
                        prev.set_color('#4c72b0'); prev.set_alpha(0.18)
                        prev.set_linewidth(0.8); prev.set_zorder(1)
                    except Exception:
                        pass
                try:
                    artist.set_color('#ff8c00'); artist.set_alpha(1.0)
                    artist.set_linewidth(2.2); artist.set_zorder(5)
                    _pstate['prev'] = artist
                    _pblit_highlight(artist, prev)
                except Exception:
                    pass
                try:
                    on_pick_track(tid)
                except Exception as _e:
                    print(f"[PyCAT VPT] consolidated pick callback failed: {_e}")
            # One button_press per click -> the single nearest curve in the MSD subplot (axes[0,0]).
            try:
                _connect_nearest_curve_click(fig, _msd_ax, _line_to_tid, _pstate, _apply_pick,
                                             notify=_default_notify)
            except Exception:
                pass
        # Live toggle: a button that closes this figure and opens separate windows.
        try:
            from matplotlib.widgets import Button
            axbtn = fig.add_axes([0.85, 0.005, 0.14, 0.03])
            btn = Button(axbtn, 'Separate windows')
            def _switch(_evt):
                try:
                    plt.close(fig)
                except Exception:
                    pass
                _render_separate()
            btn.on_clicked(_switch)
            fig._pycat_toggle_btn = btn  # keep a ref so it isn't GC'd
        except Exception:
            pass
        if interactive:
            plt.show(block=False)
        return fig

    return _render_consolidated() if consolidated else _render_separate()


def plot_moduli(moduli_df, title="Complex moduli G′/G″ — Evans method (microrheology)",
                interactive=True):
    """
    Log-log plot of storage G'(ω) and loss G''(ω) vs angular frequency, from
    compute_moduli_evans() (or compute_moduli_gser()). The crossover (G'=G'') marks the viscoelastic
    relaxation frequency.
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.2, 5.0))
    if moduli_df is None or len(moduli_df) == 0:
        ax.text(0.5, 0.5, "No moduli to plot", ha='center', va='center')
        return _show(fig, interactive)

    d = moduli_df.sort_values('omega_rad_s')
    w = d['omega_rad_s'].values
    gp = d['g_prime_pa'].values
    gpp = d['g_double_prime_pa'].values

    # NOTHING here is clipped onto the log axis. Three separate ways the previous
    # version turned a null result into an apparent measurement:
    #
    #  1. The CI BANDS were clipped: np.clip(g_prime_lo, 1e-12, None). A bootstrap
    #     band whose lower bound is NEGATIVE straddles zero -- the data are consistent
    #     with NO elasticity. Clipping the lower bound to 1e-12 makes the band appear
    #     to EXCLUDE zero, converting "not significantly different from zero" into
    #     "significantly positive".
    #  2. The LINES were clipped, drawing negative G' as points on the floor.
    #  3. The CROSSOVER was annotated from sign(G' - G''). Where G' is noise about
    #     zero, that sign flip is noise, and the figure would label a physically
    #     meaningless crossover frequency.
    #
    # This matters because PyCAT's media are viscous-dominated (biological condensates
    # run from about water to well past honey): G' is genuinely ~0 in the accessible
    # window and negative values are EXPECTED. Passive VPT cannot resolve a G'/G''
    # crossover in that regime; active microrheology (optical tweezers) is the correct
    # technique, and a future module will cover it.
    gp_arr = np.asarray(gp, dtype=float)
    gpp_arr = np.asarray(gpp, dtype=float)
    gp_ok = gp_arr > 0
    gpp_ok = gpp_arr > 0

    # Respect the per-frequency validity class, when the moduli carry one. An
    # EDGE-AFFECTED point can be positive and still unreliable (the Evans transform
    # needs neighbours on both sides, so the spectral endpoints are systematically
    # off) -- positivity alone is not enough to justify plotting it as a measurement.
    # These points used to be silently DROPPED upstream; they are now returned and
    # labelled, so the plot must show them for what they are rather than as data.
    _edge = np.zeros(len(gp_arr), dtype=bool)
    if 'validity' in d.columns:
        _val = d['validity'].values
        _edge = (_val == 'edge_affected')
        gp_ok = gp_ok & ~_edge
        gpp_ok = gpp_ok & ~_edge

    def _band(lo_c, hi_c, colour):
        if not {lo_c, hi_c}.issubset(d.columns):
            return 0
        lo = d[lo_c].values.astype(float)
        hi = d[hi_c].values.astype(float)
        drawable = (lo > 0) & (hi > 0)
        if drawable.any():
            ax.fill_between(w[drawable], lo[drawable], hi[drawable],
                            color=colour, alpha=0.18, linewidth=0)
        return int(((lo <= 0) & (hi > 0)).sum())

    n_straddle = _band('g_prime_lo', 'g_prime_hi', '#c44e52')
    _band('g_double_prime_lo', 'g_double_prime_hi', '#4c72b0')

    if gp_ok.any():
        _lab = "G\u2032 (storage / elastic)"
        if (~gp_ok).any():
            _lab += f"  [{int((~gp_ok).sum())}/{len(gp_arr)} \u2264 0]"
        ax.plot(w[gp_ok], gp_arr[gp_ok], '-o', ms=4, color='#c44e52', label=_lab)
    else:
        ax.plot([], [], '-o', ms=4, color='#c44e52',
                label=f"G\u2032 \u2014 all {len(gp_arr)} points \u2264 0 (no measurable elasticity)")
    if gpp_ok.any():
        ax.plot(w[gpp_ok], gpp_arr[gpp_ok], '-s', ms=4, color='#4c72b0',
                label="G\u2033 (loss / viscous)")

    # Crossover: only where BOTH moduli are positive, so a sign flip of noise in G'
    # cannot be reported as a material crossover.
    try:
        both = gp_ok & gpp_ok
        if both.sum() >= 2:
            ww = w[both]
            diff = gp_arr[both] - gpp_arr[both]
            idx = np.where(np.diff(np.sign(diff)) != 0)[0]
            if idx.size:
                wc = ww[idx[0]]
                ax.axvline(wc, color='0.5', ls=':', lw=1)
                ax.text(wc, ax.get_ylim()[0], f"  crossover \u2248 {wc:.2g} rad/s",
                        fontsize=8, color='0.4', rotation=90, va='bottom')
    except Exception:
        pass

    if (~gp_ok).any() or n_straddle:
        _msg = []
        if (~gp_ok).any():
            _msg.append(f"{int((~gp_ok).sum())}/{len(gp_arr)} G\u2032 points \u2264 0 \u2014 not "
                        f"plottable; Evans conversion not locally valid there.")
        if n_straddle:
            _msg.append(f"{n_straddle} G\u2032 CI band(s) straddle zero \u2014 consistent "
                        f"with NO elasticity.")
        if _edge.any():
            _msg.append(f"{int(_edge.sum())} edge-affected frequency/ies excluded "
                        f"(the transform is unreliable at the spectral endpoints).")
        _msg.append("Expected for a viscous-dominated medium. Passive VPT cannot "
                    "resolve a G\u2032/G\u2033 crossover \u2014 use active microrheology.")
        ax.text(0.02, 0.02, "\n".join(_msg), transform=ax.transAxes,
                ha='left', va='bottom', fontsize=6.5, color='#c44e52',
                bbox=dict(boxstyle='round', fc='#fff5f5', ec='#c44e52', alpha=0.85))

    ax.set_xscale('log'); ax.set_yscale('log')
    ax.set_xlabel("angular frequency ω (rad/s)")
    ax.set_ylabel("modulus (Pa)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, which='both', alpha=0.15)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _show(fig, interactive)


def plot_frap_recovery(time_s, norm_intensity, fit=None, title="FRAP recovery",
                       interactive=True):
    """
    FRAP recovery curve: normalized intensity vs time with the fitted recovery
    model overlaid, annotating the mobile fraction and half-time.

    Parameters
    ----------
    time_s, norm_intensity : the measured recovery curve.
    fit : optional dict from fit_frap_recovery() (a, b, tau_half,
        mobile_fraction, half_time_s, fit_time, fit_curve).
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    t = np.asarray(time_s, dtype=float)
    y = np.asarray(norm_intensity, dtype=float)
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    ax.plot(t, y, 'o', ms=4, color='#4c72b0', alpha=0.8, label="data", zorder=2)

    if fit and np.isfinite(fit.get('tau_half', np.nan)):
        ft = np.asarray(fit.get('fit_time', [])); fc = np.asarray(fit.get('fit_curve', []))
        if ft.size:
            ax.plot(ft, fc, '-', color='#c44e52', lw=2.2, zorder=3,
                    label="fitted recovery")
        mob = fit.get('mobile_fraction', np.nan)
        th = fit.get('half_time_s', fit.get('tau_half', np.nan))
        # mobile-fraction plateau + immediate post-bleach markers
        b = fit.get('b', np.nan); a = fit.get('a', np.nan)
        if np.isfinite(b):
            ax.axhline(b, color='0.6', ls='--', lw=1)
            ax.text(t.max(), b, f" plateau (mobile≈{mob:.2f})", fontsize=8,
                    va='bottom', ha='right', color='0.35')
        if np.isfinite(th):
            ax.axvline(th, color='0.6', ls=':', lw=1)
            ax.text(th, y.min(), f" t½≈{th:.2g}s", fontsize=8, rotation=90,
                    va='bottom', color='0.35')
        r2 = fit.get('r_squared', np.nan)
        if np.isfinite(r2):
            ax.text(0.98, 0.05, f"R²={r2:.3f}", transform=ax.transAxes,
                    ha='right', fontsize=8, color='0.4')

    ax.set_xlabel("time (s)")
    ax.set_ylabel("normalized intensity")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15)
    ax.legend(fontsize=9, loc='lower right')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_coarsening(time_s, radius_um, res, title="Coarsening kinetics",
                    interactive=True):
    """Mean radius vs time with the fitted Ostwald (t^1/3) and coalescence
    (t^1/2) curves; the preferred mechanism and its confidence are annotated."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t = np.asarray(time_s, dtype=float)
    R = np.asarray(radius_um, dtype=float)
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    ax.plot(t, R, 'o', ms=4, color='#333', label="measured", zorder=3)
    fo = np.asarray(res.get('fit_radii_ostwald', []))
    fc = np.asarray(res.get('fit_radii_coalescence', []))
    if fo.size == t.size:
        ax.plot(t, fo, '-', color='#c44e52', lw=1.8,
                label=f"Ostwald t^⅓ (R²={res.get('ostwald_r2', float('nan')):.2f})")
    if fc.size == t.size:
        ax.plot(t, fc, '-', color='#4c72b0', lw=1.8,
                label=f"coalescence t^½ (R²={res.get('coalescence_r2', float('nan')):.2f})")
    mech = res.get('preferred_mechanism', '?')
    conf = res.get('mechanism_confidence', '')
    ax.text(0.02, 0.98, f"preferred: {mech}\nconfidence: {conf}",
            transform=ax.transAxes, va='top', fontsize=9,
            bbox=dict(boxstyle='round', fc='#f4f4f4', ec='0.8'))
    ax.set_xlabel("time (s)"); ax.set_ylabel("mean radius (µm)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15); ax.legend(fontsize=8, loc='lower right')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_km_survival(km_df, title="Kaplan–Meier condensate survival",
                     interactive=True):
    """Step survival curve S(t). Expects columns time (or t) and survival (or S)."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    cols = {c.lower(): c for c in km_df.columns}
    tcol = cols.get('time') or cols.get('t') or cols.get('time_s') or km_df.columns[0]
    scol = (cols.get('survival') or cols.get('s') or cols.get('survival_prob')
            or km_df.columns[1])
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    ax.step(km_df[tcol], km_df[scol], where='post', color='#c44e52', lw=2)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("lifetime (s)"); ax.set_ylabel("surviving fraction S(t)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15)
    fig.tight_layout()
    return _show(fig, interactive)


def plot_molecular_counting(var_x, var_y, nu, n_values=None,
                            title="Molecular counting", interactive=True):
    """Two panels: (left) the step-variance vs intensity line forced through the
    origin whose slope is the single-fluorophore brightness ν; (right) the
    distribution of molecule counts N across regions."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    var_x = np.asarray(var_x, dtype=float)
    var_y = np.asarray(var_y, dtype=float)
    ncol = 2 if n_values is not None and len(np.asarray(n_values)) else 1
    fig, axes = plt.subplots(1, ncol, figsize=(5.4 * ncol, 4.4))
    axes = np.atleast_1d(axes)
    ax = axes[0]
    ax.plot(var_x, var_y, 'o', ms=3, alpha=0.5, color='#4c72b0')
    if var_x.size and np.isfinite(nu):
        xx = np.linspace(0, np.nanmax(var_x), 50)
        ax.plot(xx, nu * xx, '-', color='#c44e52', lw=2,
                label=f"slope ν = {nu:.3g}")
        ax.legend(fontsize=9)
    ax.set_xlabel("p(1−p)·I(t)"); ax.set_ylabel("(I(t+1) − p·I(t))²")
    ax.set_title("brightness fit (through origin)", fontsize=10)
    ax.grid(True, alpha=0.15)
    if ncol == 2:
        axN = axes[1]
        Nv = np.asarray(n_values, dtype=float)
        Nv = Nv[np.isfinite(Nv)]
        axN.hist(Nv, bins=min(30, max(5, Nv.size // 3)), color='#55a868',
                 edgecolor='white')
        axN.set_xlabel("molecules per region N"); axN.set_ylabel("count")
        axN.set_title("molecule-count distribution", fontsize=10)
        axN.grid(True, alpha=0.15)
    fig.suptitle(title, fontweight='bold')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_fusion_relaxation(time_s, aspect_ratio, fit, title="Fusion relaxation",
                           interactive=True):
    """Aspect ratio vs time after a merge, with the exponential relaxation fit;
    annotates τ and (if R was given) η/γ."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t = np.asarray(time_s, float); ar = np.asarray(aspect_ratio, float)
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    ax.plot(t, ar, 'o', ms=4, color='#4c72b0', label="aspect ratio", zorder=2)
    fa = np.asarray(fit.get('fit_ar', []))
    if fa.size == t.size:
        ax.plot(t, fa, '-', color='#c44e52', lw=2, zorder=3, label="fit")
    txt = f"τ = {fit.get('tau_s', float('nan')):.3g} s"
    eg = fit.get('eta_over_gamma_s_per_um', np.nan)
    R = fit.get('characteristic_length_um', None)
    if eg == eg:  # not NaN
        txt += f"\nη/γ = {eg:.3g} s/µm  (R = {R:.2g} µm)"
    r2 = fit.get('r_squared', np.nan)
    if r2 == r2:
        txt += f"\nR² = {r2:.3f}"
    ax.text(0.98, 0.95, txt, transform=ax.transAxes, va='top', ha='right',
            fontsize=9, bbox=dict(boxstyle='round', fc='#f4f4f4', ec='0.8'))
    ax.axhline(1.0, color='0.6', ls='--', lw=0.8)
    ax.set_xlabel("time after merge (s)"); ax.set_ylabel("aspect ratio (major/minor)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15); ax.legend(fontsize=9)
    fig.tight_layout()
    return _show(fig, interactive)


def _intensity_col(df):
    for c in ('intensity', 'mean_intensity', 'value', 'profile'):
        if c in df.columns:
            return c
    # first numeric column that isn't a distance/radius/index
    for c in df.columns:
        if c not in ('distance_px', 'distance_um', 'radius_px', 'radius_um',
                     'center_index', 'line_index'):
            return c
    return df.columns[-1]


def plot_line_profiles(df, group_col='line_index', title="Line intensity profiles",
                       interactive=True):
    """Intensity vs distance along each drawn line."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ycol = _intensity_col(df)
    xcol = 'distance_um' if 'distance_um' in df.columns else 'distance_px'
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    if group_col in df.columns and df[group_col].nunique() > 1:
        for gid, g in df.groupby(group_col):
            ax.plot(g[xcol], g[ycol], lw=1.5, alpha=0.85, label=f"line {gid}")
        ax.legend(fontsize=8)
    else:
        ax.plot(df[xcol], df[ycol], lw=1.8, color='#4c72b0')
    ax.set_xlabel("distance (µm)" if xcol.endswith('um') else "distance (px)")
    ax.set_ylabel("intensity")
    ax.set_title(title, fontweight='bold'); ax.grid(True, alpha=0.15)
    fig.tight_layout()
    return _show(fig, interactive)


def plot_radial_profiles(df, group_col='center_index',
                         title="Radial intensity profiles", interactive=True):
    """Intensity vs radius from each centre (condensate interface profile)."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ycol = _intensity_col(df)
    xcol = 'radius_um' if 'radius_um' in df.columns else 'radius_px'
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    if group_col in df.columns and df[group_col].nunique() > 1:
        for gid, g in df.groupby(group_col):
            ax.plot(g[xcol], g[ycol], lw=1.0, alpha=0.5, color='#4c72b0')
        # mean profile
        piv = df.pivot_table(index=xcol, values=ycol, aggfunc='mean')
        ax.plot(piv.index, piv[ycol], lw=2.4, color='#c44e52', label="mean")
        ax.legend(fontsize=9)
    else:
        ax.plot(df[xcol], df[ycol], lw=1.8, color='#4c72b0')
    ax.set_xlabel("radius (µm)" if xcol.endswith('um') else "radius (px)")
    ax.set_ylabel("intensity")
    ax.set_title(title, fontweight='bold'); ax.grid(True, alpha=0.15)
    fig.tight_layout()
    return _show(fig, interactive)


def plot_enrichment_distribution(per_cond_df, value_col='enrichment',
                                 title="Client enrichment per condensate",
                                 interactive=True):
    """Histogram of per-condensate enrichment (partition) with the median marked."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    # ── A HISTOGRAM BAR IS NOT AN OBJECT ────────────────────────────────────────
    #
    # A first pass at this sweep listed this plot as "brushable — each point is one condensate".
    # **It is not.** It is a histogram, and a bar is a BIN: it holds however many condensates fell
    # into that range. Clicking it cannot mean "show me the object", because there is no object —
    # there are twelve.
    #
    # This is the same trap as the ensemble curves (FRAP, coarsening, moduli): making them
    # pickable would be a **lie**, because the user clicks expecting an image and gets whichever
    # row happened to sit at that index.
    #
    # **The brushable view of this same data is a SCATTER** — one point per condensate, which is
    # what ``PlottingWidget`` builds when the user plots enrichment against area. That is where
    # the click means something, and it already works (1.5.496).
    vals = per_cond_df[value_col].values.astype(float)
    vals = vals[np.isfinite(vals)]
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    if vals.size:
        ax.hist(vals, bins=min(30, max(6, vals.size // 3)), color='#55a868',
                edgecolor='white')
        med = float(np.median(vals))
        ax.axvline(med, color='#c44e52', lw=2, ls='--',
                   label=f"median = {med:.2f}×")
        ax.axvline(1.0, color='0.5', lw=1, ls=':', label="no enrichment (1×)")
        ax.legend(fontsize=9)
    ax.set_xlabel("enrichment (dense / dilute)")
    ax.set_ylabel("number of condensates")
    ax.set_title(title, fontweight='bold'); ax.grid(True, alpha=0.15)
    fig.tight_layout()
    return _show(fig, interactive)


def plot_spatial_metrology(dfs, title="Spatial metrology", interactive=True):
    """Multi-panel plot of the curve-type spatial outputs that are present:
    NND histogram, Ripley's L(r)−r, pair-correlation g(r), and radial density.
    `dfs` is the dict of result DataFrames (keys like nnd_per_condensate,
    ripleys_l, pcf, radial)."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    panels = []
    if 'nnd_per_condensate' in dfs and 'nnd_um' in dfs['nnd_per_condensate']:
        panels.append('nnd')
    if 'ripleys_l' in dfs and 'L_r_minus_r' in dfs['ripleys_l']:
        panels.append('ripley')
    if 'pcf' in dfs and 'g_r' in dfs['pcf']:
        panels.append('pcf')
    if 'radial' in dfs and 'density_per_um2' in dfs['radial']:
        panels.append('radial')
    if not panels:
        return None

    n = len(panels)
    ncol = min(2, n); nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.6 * ncol, 4.2 * nrow))
    axes = np.atleast_1d(axes).ravel()

    def _grouped(ax, df, xcol, ycol, ref=None):
        # ── This gate never fired on the one table it exists for ──────────────
        #
        # It read `'cell_label'`; `puncta_analysis_func` writes `'cell label'` — with a
        # SPACE — and is the only producer in the codebase that does. So multi-cell
        # puncta data always fell to the `else`, where one `ax.plot` over a POOLED
        # frame connects points ACROSS cells into a single line: a zigzag between
        # unrelated objects, drawn as though it were a trajectory. Not a cosmetic
        # miss — the picture said something untrue.
        #
        # `cell_label_column` accepts both spellings. The column is deliberately NOT
        # renamed: it is user-visible in results tables and CSVs. Same call 1.6.74
        # made for `ObjectRef.from_row`, whose comment already named this site.
        from pycat.utils.object_ref import cell_label_column

        label_col = cell_label_column(df)
        if label_col is not None and df[label_col].nunique() > 1:
            for _, g in df.groupby(label_col):
                g = g.sort_values(xcol)
                ax.plot(g[xcol], g[ycol], lw=1.0, alpha=0.4, color='#4c72b0')
            piv = df.pivot_table(index=xcol, values=ycol, aggfunc='mean')
            ax.plot(piv.index, piv[ycol], lw=2.4, color='#c44e52', label='mean')
            ax.legend(fontsize=8)
        else:
            d = df.sort_values(xcol)
            ax.plot(d[xcol], d[ycol], lw=1.8, color='#4c72b0')
        if ref is not None:
            ax.axhline(ref, color='0.5', ls=':', lw=1)

    for ax, p in zip(axes, panels):
        if p == 'nnd':
            v = dfs['nnd_per_condensate']['nnd_um'].values
            v = v[np.isfinite(v)]
            ax.hist(v, bins=min(30, max(6, v.size // 4)), color='#55a868',
                    edgecolor='white')
            ax.set_xlabel("nearest-neighbour distance (µm)")
            ax.set_ylabel("count"); ax.set_title("NND distribution", fontsize=10)
        elif p == 'ripley':
            _grouped(ax, dfs['ripleys_l'], 'r_um', 'L_r_minus_r', ref=0.0)
            ax.set_xlabel("r (µm)"); ax.set_ylabel("L(r) − r")
            ax.set_title("Ripley's L (>0 clustered)", fontsize=10)
        elif p == 'pcf':
            _grouped(ax, dfs['pcf'], 'r_centre_um', 'g_r', ref=1.0)
            ax.set_xlabel("r (µm)"); ax.set_ylabel("g(r)")
            ax.set_title("pair correlation (>1 clustered)", fontsize=10)
        elif p == 'radial':
            _grouped(ax, dfs['radial'], 'r_norm_centre', 'density_per_um2')
            ax.set_xlabel("normalised radius (0=centre, 1=edge)")
            ax.set_ylabel("density (µm⁻²)")
            ax.set_title("radial localisation", fontsize=10)
        ax.grid(True, alpha=0.15)
    for ax in axes[n:]:
        ax.axis('off')
    fig.suptitle(title, fontweight='bold')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_distributions(df, columns=None, title="Distributions", interactive=True,
                       max_panels=6):
    """Generic small-multiples of histograms for the numeric columns of a
    per-object DataFrame (feature analysis, morphological complexity, etc.)."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    num = df.select_dtypes(include=[np.number])
    # drop id-like / constant columns
    cols = [c for c in (columns or num.columns)
            if c in num.columns and num[c].nunique() > 3
            and not c.lower().endswith(('label', 'index', 'id'))]
    cols = cols[:max_panels]
    if not cols:
        return None
    n = len(cols); ncol = min(3, n); nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, 3.4 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax, c in zip(axes, cols):
        v = num[c].values.astype(float); v = v[np.isfinite(v)]
        ax.hist(v, bins=min(24, max(5, v.size // 4)), color='#4c72b0',
                edgecolor='white')
        med = float(np.median(v)) if v.size else np.nan
        if np.isfinite(med):
            ax.axvline(med, color='#c44e52', lw=1.5, ls='--')
        ax.set_title(c, fontsize=9); ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.15)
    for ax in axes[n:]:
        ax.axis('off')
    fig.suptitle(title, fontweight='bold')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_focus_diagnostic(df, title="Why are the dim objects dim?",
                          blur_ratio=0.65, dim_ratio=0.6, interactive=True,
                          source_path=None, viewer=None, on_select=None):
    """Scatter of edge-sharpness vs intensity (both relative to the body) that
    separates below-focus (blurry-dim) from nucleation/growth (sharp-dim)
    objects. From focus_vs_growth_diagnostic().

    **Brushable.** Every point IS an object, and this is the plot where a user most wants to click
    one: *"is that really out of focus, or is it a young condensate?"* is a question you answer by
    **looking at the object**, not at its coordinates. Pass ``viewer=`` to reveal it in napari, or
    ``source_path=`` so a batch plot can crop it straight out of the file.
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colmap = {'bright': '#888888',
              'sharp_dim (likely nucleation/growth)': '#2ca02c',
              'blurry_dim (likely below focus)': '#d62728'}
    fig, ax = plt.subplots(figsize=(6.6, 5.2))

    # ── The scatter is drawn PER GROUP, so the refs must be per group too ───────
    #
    # A single flat list of refs would be silently mis-indexed: matplotlib reports the index
    # WITHIN the picked artist, and each group is its own artist. Clicking the third green point
    # would resolve to the third row of the whole table — **which is a different object**, and the
    # user would never know.
    for interp, g in df.groupby('interpretation'):
        _points = ax.scatter(g['intensity_ratio'], g['sharpness_ratio'], s=60,
                             color=colmap.get(interp, '#4c72b0'), edgecolor='k',
                             linewidth=0.5, label=interp.split(' (')[0], alpha=0.85,
                             picker=(5 if (viewer is not None or source_path or on_select)
                                     else None))
        if viewer is not None or source_path or on_select:
            add_brushing(fig, _points, g, source_path=source_path, viewer=viewer,
                         on_select=on_select)
    # reference lines / regions
    ax.axvline(dim_ratio, color='0.6', ls='--', lw=1)
    ax.axhline(blur_ratio, color='0.6', ls=':', lw=1)
    ax.text(dim_ratio, ax.get_ylim()[1], "  dim ↔ bright", fontsize=8,
            color='0.4', va='top')
    # annotate the body
    if 'body_label' in df.attrs:
        b = df[df['label'] == df.attrs['body_label']]
        if len(b):
            ax.annotate("body", (b['intensity_ratio'].iloc[0],
                                 b['sharpness_ratio'].iloc[0]),
                        fontsize=9, fontweight='bold',
                        xytext=(5, 5), textcoords='offset points')
    ax.set_xlabel("intensity relative to body")
    ax.set_ylabel("edge sharpness relative to body")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15); ax.legend(fontsize=8, loc='lower right')
    # quadrant guidance
    ax.text(0.02, 0.02, "dim + blurry → below focus",
            transform=ax.transAxes, fontsize=8, color='#d62728', va='bottom')
    ax.text(0.02, 0.92, "dim + sharp → growth phase",
            transform=ax.transAxes, fontsize=8, color='#2ca02c', va='top')
    fig.tight_layout()
    return _show(fig, interactive)


def plot_phase_diagram(df, x_name='concentration', two_phase='above',
                       title="Phase diagram", interactive=True):
    """
    Phase diagram: temperature (y) vs a swept variable (x), with the cloud-point
    phase boundary drawn as an Akima interpolation and the 2-phase region shaded.

    The shaded 2-phase region has sharp edges along the plot borders and the
    smooth Akima boundary as its interior edge. Replicates at the same x are
    shown as individual points with the mean ± SD overlaid.

    Parameters
    ----------
    df : DataFrame with columns 'x_value' and 'T_cloud' (one row per file /
        replicate). An optional 'T_clear' column is drawn as a second boundary.
    x_name : label for the x-axis (e.g. 'LiCl (mM)').
    two_phase : 'above' (2-phase above the boundary; LCST-like) or 'below'
        (UCST-like) — which side of the cloud boundary is two-phase.
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    d = df.dropna(subset=['x_value', 'T_cloud']).copy()
    if d['x_value'].nunique() < 2:
        return None
    agg = d.groupby('x_value')['T_cloud'].agg(['mean', 'std', 'count']).reset_index()
    xs = agg['x_value'].values.astype(float)
    ys = agg['mean'].values.astype(float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]

    # Akima boundary (needs ≥3 points; else linear)
    xd = np.linspace(xs.min(), xs.max(), 300)
    if len(xs) >= 3:
        try:
            from scipy.interpolate import Akima1DInterpolator
            boundary = Akima1DInterpolator(xs, ys)(xd)
        except Exception:
            boundary = np.interp(xd, xs, ys)
    else:
        boundary = np.interp(xd, xs, ys)

    fig, ax = plt.subplots(figsize=(6.8, 5.2))
    # individual replicates (faint) + mean ± SD
    ax.scatter(d['x_value'], d['T_cloud'], s=22, color='#4c72b0', alpha=0.4,
               zorder=2, label='replicates')
    ax.errorbar(xs, ys, yerr=agg['std'].values[order], fmt='o', ms=6,
                color='#c44e52', capsize=3, zorder=4, label='mean cloud point')
    ax.plot(xd, boundary, '-', color='#c44e52', lw=2, zorder=3,
            label='phase boundary (Akima)')

    # shade ONLY the 2-phase region: Akima boundary → plot border (sharp edges)
    ymin = min(d['T_cloud'].min(), ys.min()) - 2
    ymax = max(d['T_cloud'].max(), ys.max()) + 2
    ax.set_ylim(ymin, ymax)
    if two_phase == 'below':
        ax.fill_between(xd, ymin, boundary, color='#c44e52', alpha=0.13, zorder=1)
        ax.text(0.5, 0.08, "2-phase", transform=ax.transAxes, ha='center',
                color='#c44e52', fontsize=11, fontweight='bold')
        ax.text(0.5, 0.9, "1-phase", transform=ax.transAxes, ha='center',
                color='0.5', fontsize=11)
    else:
        ax.fill_between(xd, boundary, ymax, color='#c44e52', alpha=0.13, zorder=1)
        ax.text(0.5, 0.9, "2-phase", transform=ax.transAxes, ha='center',
                color='#c44e52', fontsize=11, fontweight='bold')
        ax.text(0.5, 0.08, "1-phase", transform=ax.transAxes, ha='center',
                color='0.5', fontsize=11)

    ax.set_xlabel(x_name); ax.set_ylabel("temperature (\u00b0C)")
    ax.set_title(title, fontweight='bold')
    ax.grid(True, alpha=0.15); ax.legend(fontsize=8, loc='best')
    fig.tight_layout()
    return _show(fig, interactive)


# ── Brushing: which of these plots have points that ARE objects? ─────────────────────────
#
# **Almost none of them.** The goal going in was "wire the 13 unpickable plots". The goal was
# wrong, and it is worth writing down why.
#
#     plot_msd_trajectories      a LINE is a track            -> **brushable** (and already is)
#     plot_vpt_panel             ditto                        -> **brushable** (and already is)
#     plot_moduli                a point is a FREQUENCY       -> ensemble
#     plot_frap_recovery         a point is a TIMEPOINT       -> ensemble
#     plot_coarsening            a point is a MOMENT          -> ensemble
#     plot_km_survival           a step is a survival time    -> ensemble
#     plot_molecular_counting    a point is a VARIANCE BIN    -> ensemble
#     plot_fusion_relaxation     a point is a TIMEPOINT       -> ensemble
#     plot_enrichment_distribution   a bar is a BIN           -> ensemble (see the note there)
#     plot_spatial_metrology     every panel is a CURVE       -> ensemble
#     plot_distributions         a bar is a BIN               -> ensemble
#     plot_focus_diagnostic      a point is a FRAME           -> ensemble
#     plot_phase_diagram         a point is a CONDITION       -> ensemble
#
# **A point on a Ripley curve is a radius. A bar of a histogram holds twelve condensates.** There
# is no object behind them, and making them pickable would be a **lie** — the user clicks
# expecting an image and gets whichever row happened to sit at that index.
#
# **The brushable view of per-object data is a SCATTER**, and that is exactly what
# ``PlottingWidget`` builds: the user picks any results table and any two columns, and every point
# is one object. **That is where the wiring belongs** (data_viz_tools, 1.5.496) — one place, and it
# covers every per-object table rather than fifteen fixed figures.
#
# ``add_brushing`` below stays, for a plot whose points genuinely ARE objects. **Only call it when
# they are.**

def add_brushing(figure, artist, df, *, source_path=None, viewer=None, hub=None,
                 on_select=None, tags=None):
    """**Make a plot's points clickable back to the objects behind them.**

    One call. A plot that supplies its DataFrame gets: reveal-in-viewer when a session is live,
    **a cropped thumbnail out of the source file when it is not**, and propagation to any other
    view on the hub.

    ::

        points = ax.scatter(df.area_um2, df.partition_coeff, picker=5)
        add_brushing(fig, points, df, source_path=path, viewer=viewer)

    **Only call this when a point IS an object.** Five of PyCAT's plots draw ensemble curves — a
    FRAP recovery point is a *timepoint*, a coarsening point is a *moment*, a molecular-counting
    point is a *variance bin*. **There is no object behind them, and making them pickable would be
    a lie**: the user would click expecting an image and get whichever row happened to sit at that
    index.

    The DataFrame must carry a ``bbox`` (``bbox_y0``/``bbox_x0``/``bbox_y1``/``bbox_x1``) for the
    **batch** case to work. Without it the point still resolves interactively, and
    ``resolve_offline`` will say plainly why it cannot produce an image.
    """
    from pycat.utils.brushing import make_pickable
    from pycat.utils.object_ref import refs_from_dataframe

    if df is None or not len(df):
        return figure

    refs = refs_from_dataframe(df, source_path=source_path, tags=tags)
    return make_pickable(figure, artist, refs, hub=hub, on_select=on_select, viewer=viewer)
