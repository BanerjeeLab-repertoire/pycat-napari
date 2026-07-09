"""
Shared analysis plots for PyCAT workflows.

These replace table pop-ups with the graph that actually communicates the
result (people read curves, not dataframes). Each function returns the
matplotlib Figure so the caller can save it; with interactive=True it also shows
the window (non-blocking) like the temperature/QC plots.
"""

import numpy as np


def _show(fig, interactive):
    if interactive:
        import matplotlib.pyplot as plt
        plt.show(block=False)
    return fig


def plot_msd_trajectories(per_track_df, ensemble_msd_df=None, fit=None,
                          title="MSD", interactive=True):
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
    """
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    # faint per-track curves. Cap the number actually drawn: a movie can produce
    # tens of thousands of tracks, and drawing one matplotlib line each freezes
    # the UI (each ax.plot is a separate artist). A random sample of a few
    # hundred conveys the spread just as well; the quantitative result is the
    # ensemble mean and fit, which use ALL tracks regardless of this cap.
    MAX_SPAGHETTI = 400
    if per_track_df is not None and len(per_track_df):
        all_tids = per_track_df['track_id'].unique()
        n_all = len(all_tids)
        if n_all > MAX_SPAGHETTI:
            import numpy as _np
            _rng = _np.random.default_rng(0)
            shown = set(_rng.choice(all_tids, MAX_SPAGHETTI, replace=False))
            plot_df = per_track_df[per_track_df['track_id'].isin(shown)]
        else:
            plot_df = per_track_df
        for tid, g in plot_df.groupby('track_id'):
            g = g.sort_values('lag_s')
            ax.plot(g['lag_s'], g['msd_um2'], color='#4c72b0',
                    alpha=0.18, lw=0.8, zorder=1)
        # a proxy handle for the legend (report the TRUE track count)
        _lbl = (f"individual tracks (n={n_all}"
                + (f"; showing {MAX_SPAGHETTI}" if n_all > MAX_SPAGHETTI else "")
                + ")")
        ax.plot([], [], color='#4c72b0', alpha=0.5, lw=1.0, label=_lbl)

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
    return _show(fig, interactive)


def plot_moduli(moduli_df, title="Viscoelastic moduli (microrheology)",
                interactive=True):
    """
    Log-log plot of storage G'(ω) and loss G''(ω) vs angular frequency, from
    compute_moduli_gser(). The crossover (G'=G'') marks the viscoelastic
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
    # only positive values are meaningful on log axes
    ax.plot(w, np.clip(gp, 1e-12, None), '-o', ms=4, color='#c44e52',
            label="G′ (storage / elastic)")
    ax.plot(w, np.clip(gpp, 1e-12, None), '-s', ms=4, color='#4c72b0',
            label="G″ (loss / viscous)")

    # crossover annotation
    try:
        diff = gp - gpp
        sign = np.sign(diff)
        idx = np.where(np.diff(sign) != 0)[0]
        if idx.size:
            k = idx[0]
            wc = w[k]
            ax.axvline(wc, color='0.5', ls=':', lw=1)
            ax.text(wc, ax.get_ylim()[0], f"  crossover ≈ {wc:.2g} rad/s",
                    fontsize=8, color='0.4', rotation=90, va='bottom')
    except Exception:
        pass

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
        if 'cell_label' in df.columns and df['cell_label'].nunique() > 1:
            for _, g in df.groupby('cell_label'):
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
                          blur_ratio=0.65, dim_ratio=0.6, interactive=True):
    """Scatter of edge-sharpness vs intensity (both relative to the body) that
    separates below-focus (blurry-dim) from nucleation/growth (sharp-dim)
    objects. From focus_vs_growth_diagnostic()."""
    import matplotlib
    if not interactive:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colmap = {'bright': '#888888',
              'sharp_dim (likely nucleation/growth)': '#2ca02c',
              'blurry_dim (likely below focus)': '#d62728'}
    fig, ax = plt.subplots(figsize=(6.6, 5.2))
    for interp, g in df.groupby('interpretation'):
        ax.scatter(g['intensity_ratio'], g['sharpness_ratio'], s=60,
                   color=colmap.get(interp, '#4c72b0'), edgecolor='k',
                   linewidth=0.5, label=interp.split(' (')[0], alpha=0.85)
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
