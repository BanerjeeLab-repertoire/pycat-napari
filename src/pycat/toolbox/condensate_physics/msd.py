"""Condensate **MSD / anomalous diffusion** — split out of condensate_physics_tools by quantity (1.6.220).

The general-condensate MSD path: compute_msd (per-lag MSD with the >=200-frame track-length gate),
fit_anomalous_diffusion (the log-log power-law fit -> D, alpha, with lag-window gating, identifiability CI
and motion-type classification), test_confinement, and msd_per_track. This is the MSD/D chain the
golden-master pins (D to 1.1%, alpha to 0.1%, viscosity to 3.2%); moved VERBATIM - no arithmetic
reassociated. The tools module re-exports the public entry points + MIN_TRACK_LENGTH_FRAMES.

(Note: distinct from VPT's Stokes-Einstein viscosity path in vpt/ - the two MSD paths are kept separate.)
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy import optimize, stats

from pycat.utils.general_utils import debug_log
from pycat.utils.notify import show_warning as napari_show_warning
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.fit_quality import assess_fit


# ── How long must a track be before its MSD means anything? ──────────────────
#
# The default was **5 frames**, and five frames cannot support an MSD fit. This is
# not a taste question, and the number is not invented here — it follows from the
# lag-window reasoning this codebase already committed to.
#
# `compute_msd` computes lags out to `n_frames // 4` (beyond n/4 there are too few
# displacement pairs for the estimate to be reliable). And
# `viscosity_from_diffusion_with_ci` documents what a lag window is worth,
# measured against ground truth:
#
#     30 lags -> the 95% CI on D is honest        (fold 1.7x)
#      4 lags -> it claims 95% and delivers 78%   (fold 1.9x, OVER-CONFIDENT)
#
# So a usable fit needs ~30 lags, and the n/4 rule turns that into a track length:
#
#     30 lags x 4  =  120 frames MINIMUM
#     200 frames   ->  50 lags, comfortably inside the honest regime
#     5 frames     ->   1 lag   <- the old default
#
# At one lag there is no log-log slope to fit: alpha is unconstrained, and D is a
# single displacement variance dominated by the localisation-noise floor. The MSD
# of a diffusing bead is
#
#     MSD(tau) = 4 D tau^alpha + 4 sigma_loc^2
#
# and that constant `4 sigma_loc^2` offset is exactly what a broad lag window is
# needed to separate from the slope. With a handful of lags the fit cannot tell a
# slow bead from a noisy stationary one, and the finite-track-length bias (each
# track's own MSD is itself an average over few pairs) sits on top of it.
#
# 200 is the floor Gable specified and it is defensible: 50 lags is well past the
# 30 where the CI was measured to be honest, and it leaves headroom for tracks
# that are gappy rather than contiguous.
#
# **This filter reports what it rejects** (`_report_short_track_rejections`). A
# short track is often a LINKING failure rather than an absent bead, and raising
# the bar without saying so would turn one silent problem into a bigger one.
MIN_TRACK_LENGTH_FRAMES = 200

# Lag-window facts the constant above is derived from. Named so the two cannot
# drift apart silently: if the fit gate's honest-lag count changes, the minimum
# track length has to move with it.
_HONEST_LAG_COUNT = 30          # lags at which the CI on D was measured honest
_MAX_LAG_FRACTION = 4           # compute_msd's max_lag = n_frames // 4


def _short_track_rejections(tracks_df, min_track_length):
    """Which tracks the length filter drops, and whether they look like FRAGMENTS.

    A short track has two very different meanings:

      * the bead genuinely was not there (it left, bleached, never existed) — the
        filter is doing its job;
      * the bead WAS there and the LINKER lost it — the track is short because
        tracking failed, and the number that survives is a biased subset.

    The second is Gable's report: *stable beads, still fragmented.* The two look
    identical in the output, which is why this reports rather than just filters.

    Two signatures, both computed from `tracks_df` alone:

    **gappy** — the track has few points but spans many frames (`span >> len`).
    The bead was detected on and off across a long window, so it was present the
    whole time and the linker only caught it sometimes. Dropout.

    **co-located** — two or more short tracks sit within `radius` of each other and
    their frame windows DO NOT OVERLAP. One bead cannot be in two places, and a
    second bead cannot occupy the first one's spot the moment it disappears. That
    is one bead cut into pieces. (Overlapping windows are excluded precisely
    because those CAN be two real neighbouring beads.)

    `radius` is derived from the data, not chosen: it is a small multiple of the
    median per-frame step, i.e. how far a bead of this population actually moves.
    A heuristic pointing at a cause — not a measurement, and it deliberately does
    not attempt to re-link anything.

    Returns a dict; never raises — a diagnostic must not be able to break the
    analysis it is describing.
    """
    out = dict(n_rejected=0, n_total=0, n_gappy=0, n_colocated=0, radius_um=float('nan'))
    try:
        short = []
        steps = []
        for tid, grp in tracks_df.groupby('track_id'):
            if tid < 0:
                continue
            out['n_total'] += 1
            g = grp.sort_values('frame')
            t = g['frame'].values.astype(int)
            y = g['y_um'].values.astype(float)
            x = g['x_um'].values.astype(float)
            if len(g) > 1:
                d = np.hypot(np.diff(y), np.diff(x)) / np.maximum(1, np.diff(t))
                steps.extend(d[np.isfinite(d)].tolist())
            if len(g) >= min_track_length:
                continue
            out['n_rejected'] += 1
            span = int(t.max() - t.min() + 1)
            if span >= min_track_length:
                out['n_gappy'] += 1          # present across a long window, caught rarely
            short.append((float(y.mean()), float(x.mean()), int(t.min()), int(t.max())))

        if not short:
            return out

        # How far does a bead in THIS population move in a frame? Fragments of one
        # bead land within a few of those. Median, so a handful of mis-links do not
        # set the scale.
        med_step = float(np.median(steps)) if steps else 0.0
        radius = max(5.0 * med_step, 1e-9)
        out['radius_um'] = radius

        # O(n^2) over REJECTED tracks only, which is the small set by construction.
        involved = set()
        for i in range(len(short)):
            yi, xi, a0, a1 = short[i]
            for j in range(i + 1, len(short)):
                yj, xj, b0, b1 = short[j]
                if a0 <= b1 and b0 <= a1:
                    continue                  # windows overlap -> could be two real beads
                if np.hypot(yi - yj, xi - xj) <= radius:
                    involved.add(i); involved.add(j)
        out['n_colocated'] = len(involved)
    except Exception as exc:
        debug_log('short-track diagnostic failed; not reporting', exc)
    return out


def _report_short_track_rejections(stats):
    """Say what the length filter did. **A filter that says nothing is a lie of omission.**

    Escalates: a fragmentation signature is a warning because the surviving tracks
    are then a biased subset and the viscosity computed from them is not the
    viscosity of the sample.
    """
    n_rej, n_tot = stats.get('n_rejected', 0), stats.get('n_total', 0)
    if not n_rej:
        return
    frag = stats.get('n_colocated', 0) + stats.get('n_gappy', 0)
    msg = (f"MSD: {n_rej} of {n_tot} tracks rejected as shorter than the minimum "
           f"track length.")
    if frag:
        bits = []
        if stats.get('n_colocated'):
            bits.append(f"{stats['n_colocated']} sit on top of each other at different "
                        f"times (within {stats['radius_um']*1000:.0f} nm)")
        if stats.get('n_gappy'):
            bits.append(f"{stats['n_gappy']} span more frames than they have points")
        napari_show_warning(
            msg + " **" + " and ".join(bits) + "** — that is the signature of a bead "
            "the LINKER lost, not a bead that was absent. One bead cannot be in two "
            "places at different times, and a bead detected on and off across a long "
            "window was present the whole time.\n\nSo these are a linking failure, and "
            "the tracks that survived are a biased subset — the viscosity from them is "
            "not the viscosity of the sample. Check the linking distance and max frame "
            "gap before trusting the number.")
    elif n_tot and n_rej >= 0.8 * n_tot:
        napari_show_warning(
            msg + " At least 80% of tracks were rejected. Either the movie is too "
            "short for the minimum track length, or the linker is not holding beads — "
            "worth checking before trusting what is left.")
    else:
        napari_show_info(msg)


# ---------------------------------------------------------------------------
# 1. Mean Squared Displacement
# ---------------------------------------------------------------------------

def compute_msd(
    tracks_df: pd.DataFrame,
    max_lag: int = None,
    frame_interval_s: float = 1.0,
    min_track_length: int = MIN_TRACK_LENGTH_FRAMES,
    reject_outlier_tracks: bool = True,
    outlier_iqr_factor: float = 1.5,
) -> pd.DataFrame:
    """
    Compute ensemble-averaged MSD from linked trajectories, with a per-track
    uncertainty that reflects the number of INDEPENDENT tracks (not the number
    of correlated, overlapping displacement pairs).

    For each track and lag τ we first average that track's own squared
    displacements into a single per-track MSD(τ). The ensemble MSD is then the
    mean across tracks, and the uncertainty (msd_std / msd_sem) is the
    track-to-track spread — a statistically honest error bar, since tracks are
    independent whereas overlapping pairs within a track are not.

    Coordinates are expected in microns (columns y_um, x_um).

    Parameters
    ----------
    tracks_df : DataFrame with columns track_id, frame, y_um, x_um
        Output of link_trajectories() or link_trajectories_bayesian().
    max_lag : int or None
        Maximum lag (in frames) to compute.  Default: n_frames // 4
        (beyond n/4 the MSD estimate has too few samples to be reliable).
    frame_interval_s : float
        Physical time per frame in seconds.
    min_track_length : int
        Tracks shorter than this are excluded. **Default 200 frames** — see
        ``MIN_TRACK_LENGTH_FRAMES``. Rejected tracks are counted and reported, not
        silently dropped: a short track is often a LINKING failure rather than an
        absent bead, and the two have very different meanings.

    Returns
    -------
    msd_df : DataFrame with columns:
        lag_frames, lag_s, msd_um2 (ensemble MSD = mean over tracks),
        msd_std (spread across tracks), msd_sem (standard error of the mean
        over tracks), n_tracks (independent tracks at this lag),
        n_pairs (total displacement pairs — reference only)
    """
    frames = sorted(tracks_df['frame'].unique())
    if max_lag is None:
        max_lag = max(1, len(frames) // _MAX_LAG_FRACTION)

    # Say what the length filter rejected, and whether those look like beads the
    # LINKER lost rather than beads that were absent. Reported before the work
    # below, so the message still lands when nothing survives to compute.
    _report_short_track_rejections(
        _short_track_rejections(tracks_df, min_track_length))

    # One MSD value per track per lag (tracks are the independent unit).
    per_track: dict[int, list[float]] = {lag: [] for lag in range(1, max_lag + 1)}
    pair_counts: dict[int, int] = {lag: 0 for lag in range(1, max_lag + 1)}

    # ── Outlier-track rejection (matches the reference analysis notebook) ────
    # A movie yields many trajectories; spurious ones (mis-links, brief tracks
    # that happen to jump) have anomalously HIGH first/last MSD and, if included,
    # inflate the ensemble MSD → inflate D → deflate viscosity by a large factor.
    # The reference workflow rejects tracks whose first and last per-track MSD
    # fall outside a 1.5×IQR fence in LOG space (get_outlier_bounds). We replicate
    # that: compute each eligible track's first- and last-lag MSD, build the log
    # IQR fences, and keep only tracks inside both. This is what brings PyCAT's
    # viscosity into line with the hand-analysis on real data.
    accepted_ids = None
    if reject_outlier_tracks:
        firsts, lasts, ids = [], [], []
        for tid, grp in tracks_df.groupby('track_id'):
            if tid < 0:
                continue
            g = grp.sort_values('frame')
            if len(g) < min_track_length:
                continue
            t = g['frame'].values.astype(int)
            y = g['y_um'].values.astype(float)
            x = g['x_um'].values.astype(float)
            f0, f1 = t.min(), t.max()
            span = f1 - f0 + 1
            ys = np.full(span, np.nan); xs = np.full(span, np.nan)
            ys[t - f0] = y; xs[t - f0] = x
            # first-lag MSD (lag 1)
            dy1 = ys[1:] - ys[:-1]; dx1 = xs[1:] - xs[:-1]
            sq1 = dy1 * dy1 + dx1 * dx1; v1 = np.isfinite(sq1)
            # last-lag MSD (largest available lag for this track)
            L = span - 1
            dyL = ys[L:] - ys[:-L]; dxL = xs[L:] - xs[:-L]
            sqL = dyL * dyL + dxL * dxL; vL = np.isfinite(sqL)
            if v1.any() and vL.any():
                m1 = float(np.mean(sq1[v1])); mL = float(np.mean(sqL[vL]))
                if m1 > 0 and mL > 0:
                    firsts.append(m1); lasts.append(mL); ids.append(tid)
        if len(ids) >= 8:
            lf = np.log(np.asarray(firsts)); ll = np.log(np.asarray(lasts))
            q1f, q3f = np.percentile(lf, [25, 75]); iqrf = q3f - q1f
            q1l, q3l = np.percentile(ll, [25, 75]); iqrl = q3l - q1l
            lo_f = q1f - outlier_iqr_factor * iqrf
            hi_f = q3f + outlier_iqr_factor * iqrf
            lo_l = q1l - 1.0 * iqrl                     # notebook uses 1×IQR lower
            hi_l = q3l + outlier_iqr_factor * iqrl
            accepted_ids = {tid for tid, a, b in zip(ids, lf, ll)
                            if lo_f <= a <= hi_f and lo_l <= b <= hi_l}

    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0:
            continue
        if accepted_ids is not None and tid not in accepted_ids:
            continue
        grp = grp.sort_values('frame').reset_index(drop=True)
        if len(grp) < min_track_length:
            continue

        t = grp['frame'].values.astype(int)
        y = grp['y_um'].values.astype(float)
        x = grp['x_um'].values.astype(float)

        # Gap-aware position series indexed by frame, so displacements at a fixed
        # lag are a vectorised array shift instead of an O(n^2) Python double
        # loop over pairs. Missing frames are NaN and excluded per lag. This is
        # numerically identical to the pairwise loop but far faster on long
        # tracks (the double loop made large movies hang).
        f0, f1 = t.min(), t.max()
        span = f1 - f0 + 1
        ys = np.full(span, np.nan); xs = np.full(span, np.nan)
        ys[t - f0] = y; xs[t - f0] = x

        for lag in range(1, max_lag + 1):
            if lag >= span:
                break
            dy = ys[lag:] - ys[:-lag]
            dx = xs[lag:] - xs[:-lag]
            sq = dy * dy + dx * dx
            valid = np.isfinite(sq)
            n_valid = int(valid.sum())
            if n_valid:
                per_track[lag].append(float(np.mean(sq[valid])))  # this track's MSD(τ)
                pair_counts[lag] += n_valid

    rows = []
    for lag in range(1, max_lag + 1):
        vals = per_track[lag]
        if not vals:
            continue
        arr = np.asarray(vals)
        n_tracks = arr.size
        std = float(np.std(arr, ddof=1)) if n_tracks > 1 else np.nan
        sem = std / np.sqrt(n_tracks) if n_tracks > 1 else np.nan
        rows.append({
            'lag_frames': lag,
            'lag_s':      lag * frame_interval_s,
            'msd_um2':    float(np.mean(arr)),
            'msd_std':    std,
            'msd_sem':    sem,
            'n_tracks':   n_tracks,
            'n_pairs':    pair_counts[lag],
        })
    return pd.DataFrame(rows)


def _confined_msd(t, L2, D, off):
    """MSD of a probe confined to a domain: rises, then PLATEAUS at L2."""
    return L2 * (1.0 - np.exp(-4.0 * D * t / max(L2, 1e-12))) + 4.0 * off


def _aicc(y, y_fit, k):
    n = len(y)
    rss = float(np.sum((y - y_fit) ** 2))
    if rss <= 0 or n <= k + 2:
        return np.inf
    return n * np.log(rss / n) + 2 * k + (2 * k * (k + 1)) / (n - k - 1)


def test_confinement(tau, msd):
    """Is this MSD a power law, or a probe hitting a WALL?

    Why this test and not a residual runs test
    ------------------------------------------
    ``motion_type`` is read straight off ``alpha``, and alpha is the entire
    anomalous-vs-Brownian claim — but alpha only means anything if the power law is the
    right model. **Confinement is the failure that matters:** a probe trapped in a small
    condensate produces an MSD that *plateaus*, and a power law cannot plateau, so it
    fits the plateau with a spuriously small exponent::

        truly Brownian:  alpha = 1.006, R² = 1.000  ->  'Brownian'      correct
        CONFINED:        alpha = 0.000, R² = 0.903  ->  'subdiffusion'  WRONG

    The confined probe is reported as **subdiffusion with a healthy R²**, which a reader
    takes as "the medium is viscoelastic / crowded". It is not: the probe is hitting a
    wall. Different physics, wrong conclusion, and R² does not blink.

    A residual **runs test** detects this in principle — but it needs at least 8 residuals
    to have any power, and PyCAT's *defensible lag window* is deliberately narrow, often
    only ~6 lags. Applying it there flagged **100 % of fits, including textbook Brownian
    ones**, because "could not assess" was being conflated with "the model is wrong".

    So compare the **models** instead, which works at n = 6. Fitting both a power law and
    a confined model and choosing by AICc (Δ > 2):

    ======  ================  ================  ====================
    n lags  Brownian→power    subdiffusion→     **confined→
            (false alarm)     power             confined** (detect)
    ======  ================  ================  ====================
    **6**   **100 %**         85 %              **60 %**
    8       100 %             95 %              85 %
    **10**  **100 %**         95 %              **100 %**
    15+     100 %             100 %             100 %
    ======  ================  ================  ====================

    **Zero false alarms on Brownian data at every window size** — a genuinely diffusing
    probe is never called confined. Detection of real confinement is 60 % at six lags and
    100 % from ten, so a *negative* result on a short window means "not detected", not
    "not confined".
    """
    tau = np.asarray(tau, dtype=float)
    msd = np.asarray(msd, dtype=float)
    ok = np.isfinite(tau) & np.isfinite(msd) & (tau > 0)
    tau, msd = tau[ok], msd[ok]
    if tau.size < 5:
        return dict(confined=False, assessable=False,
                    verdict="Too few lags to test for confinement.")

    try:
        p_pl, _ = optimize.curve_fit(
            lambda t, D, a, off: 4.0 * D * t ** a + 4.0 * off,
            tau, msd, p0=[max(msd[0] / (4 * tau[0]), 1e-6), 1.0, 0.0], maxfev=30000)
        pl_fit = 4.0 * p_pl[0] * tau ** p_pl[1] + 4.0 * p_pl[2]
        a_pl = _aicc(msd, pl_fit, 3)
    except Exception:  # broad-ok: reports the failure explicitly (assessable=False + a verdict that says the fit failed), not a fabricated confinement result
        return dict(confined=False, assessable=False,
                    verdict="Power-law fit failed; confinement not assessed.")

    try:
        p_cf, _ = optimize.curve_fit(
            _confined_msd, tau, msd,
            p0=[max(msd.max(), 1e-9), max(msd[0] / (4 * tau[0]), 1e-6), 0.0],
            maxfev=30000)
        cf_fit = _confined_msd(tau, *p_cf)
        a_cf = _aicc(msd, cf_fit, 3)
    except Exception:  # broad-ok: the confined-model fit failed, so the already-fit power law is retained — a reported fallback (the verdict states it), not a fabricated default
        return dict(confined=False, assessable=True,
                    verdict="Confined-model fit failed; power law retained.")

    delta = float(a_pl - a_cf)          # positive => confined model is better
    confined = bool(delta > 2.0)
    L_um = float(np.sqrt(max(p_cf[0], 0.0)))

    if confined:
        verdict = (f"The MSD is better described by a CONFINED model than by a power law "
                   f"(ΔAICc = {delta:.1f}, plateau ≈ {p_cf[0]:.4g} µm², i.e. a domain of "
                   f"about {L_um:.2f} µm). **alpha is not a measure of anomalous "
                   f"diffusion here — the probe is hitting a wall, not moving through a "
                   f"viscoelastic medium.** Check that the probe is sampling the bulk: a "
                   f"probe inside a condensate smaller than a few times its own diameter "
                   f"cannot report bulk viscosity.")
    else:
        verdict = (f"No evidence of confinement (ΔAICc = {delta:.1f} in favour of the "
                   f"power law). Note that detection is ~60 % at six lags and ~100 % from "
                   f"ten, so on a short lag window this is 'not detected', not 'not "
                   f"confined'.")

    return dict(confined=confined, assessable=True, delta_aicc=delta,
                plateau_um2=float(p_cf[0]), domain_size_um=L_um,
                n_lags=int(tau.size), verdict=verdict)


def _lag_window_gate(msd_df, max_lag_fit, frame_interval_s, upper_lag_rule, upper_lag_fraction,
                     upper_lag_fixed_s, min_independent_pairs, confine_to_defensible_bounds):
    """The reliable-lag-window fit gate for ``fit_anomalous_diffusion`` -- extracted verbatim
    (science_function_split, no numerical change): filter to lags with enough pairs, compute the defensible
    ``[lag_lo, lag_hi]`` per the chosen rule, and clip to it (warns, never blocks). Returns
    ``(df, lag_lo, lag_hi, window_warning)``."""
    df = msd_df[msd_df['n_pairs'] > 5].copy()
    if max_lag_fit is not None:
        df = df.head(max_lag_fit)

    # ── Lag-window fit gate ──────────────────────────────────────────────────
    window_warning = None
    lag_lo = lag_hi = None
    if 'lag_s' in df.columns and len(df):
        all_lags = df['lag_s'].values.astype(float)
        # High-frequency cutoff = one frame interval.
        lag_lo = float(frame_interval_s) if (frame_interval_s and frame_interval_s > 0) \
            else float(np.min(all_lags))
        # Low-frequency (upper-lag) cutoff per the chosen rule.
        max_lag_available = float(np.max(all_lags))
        rule = (upper_lag_rule or 'fraction').lower()
        if rule == 'fixed' and upper_lag_fixed_s and upper_lag_fixed_s > 0:
            lag_hi = float(upper_lag_fixed_s)
        elif rule == 'min_pairs' and 'n_tracks' in df.columns:
            ok = df[df['n_tracks'] >= int(min_independent_pairs)]
            lag_hi = float(ok['lag_s'].max()) if len(ok) else lag_lo
        else:  # 'fraction' (default)
            lag_hi = float(upper_lag_fraction) * max_lag_available

        # Sanity + coverage warnings (warn, never block).
        if lag_hi <= lag_lo:
            window_warning = (
                f"Requested lag window collapses (lag_lo={lag_lo:.3g}s ≥ "
                f"lag_hi={lag_hi:.3g}s). The acquisition is too short, or the "
                f"upper-lag rule is too strict, to define a fit band. Fitting the "
                f"full available range instead.")
            lag_hi = max_lag_available
        elif lag_hi > max_lag_available + 1e-12:
            window_warning = (
                f"Requested upper lag ({lag_hi:.3g}s) exceeds the longest "
                f"available lag ({max_lag_available:.3g}s): the acquisition "
                f"duration is too short to reach the low-frequency cutoff, so "
                f"G(τ)/viscosity may be under-resolved at long lags.")
            lag_hi = max_lag_available

        if confine_to_defensible_bounds:
            gated = df[(df['lag_s'] >= lag_lo - 1e-12)
                       & (df['lag_s'] <= lag_hi + 1e-12)]
            if len(gated) >= 3:
                df = gated
            else:
                window_warning = (
                    (window_warning + " ") if window_warning else ""
                ) + (f"Only {len(gated)} lag(s) fall inside the defensible "
                     f"window [{lag_lo:.3g}, {lag_hi:.3g}]s — too few to fit; "
                     f"using the full available range instead.")
    return df, lag_lo, lag_hi, window_warning


def _insufficient_result(lag_lo, lag_hi, window_warning):
    """The result when fewer than 3 lags survive the gate -- extracted verbatim."""
    return dict(D_um2_per_s=np.nan, alpha=np.nan, motion_type='unknown',
                r_squared=np.nan, fit_lags_s=np.array([]),
                fit_msd=np.array([]), log_log_slope=np.nan,
                log_log_intercept=np.nan,
                fit_window_s=(lag_lo, lag_hi),
                fit_window_warning=window_warning)


def _fit_msd_powerlaw(df, tau, msd, fit_localization_offset, D_ll, a_ll):
    """The non-linear MSD = 4*D*tau^alpha (+4*sigma_loc^2) refinement -- extracted verbatim. Keeps the
    log-log estimate if the non-linear fit fails. Returns ``(D, alpha, sigma_loc_um, popt, pcov)`` (popt/pcov
    are None on a failed fit, which is how the identifiability check sees it)."""
    D, alpha = D_ll, a_ll
    sigma_loc_um = float('nan')
    popt = None
    pcov = None   # so the identifiability check can see a failed fit
    try:
        sigma = None
        if 'msd_sem' in df.columns:
            sem = df['msd_sem'].values.astype(float)
            if np.all(np.isfinite(sem)) and np.all(sem > 0):
                sigma = sem
        if fit_localization_offset:
            # Fit MSD = 4·D·τ^α + 4·σ_loc², separating the STATIC LOCALIZATION
            # ERROR (a constant offset from centroid uncertainty) from real
            # diffusion. This matters enormously in viscous samples: when the
            # medium is thick the bead barely moves per frame, so the constant
            # localization floor can dwarf the real τ-dependent signal. A fit
            # WITHOUT the offset absorbs that floor into D, inflating D (and thus
            # deflating Stokes-Einstein viscosity) by a large factor. The offset
            # term lets D reflect only the genuine time-dependent motion.
            # Parameter 3 is σ_loc² (µm²); reported back as σ_loc (nm) for the
            # user to sanity-check against their expected localization precision.
            # Offset bound matches the reference notebook workflow: the constant
            # term N = 4·σ_loc² cannot exceed the smallest MSD value, since
            # MSD = (non-negative diffusion signal) + N. Our fit parameter is
            # off = N/4, so its upper bound is min(msd)/4.
            off_max = max(float(np.min(msd)) / 4.0, 1e-9)
            off0 = min(max(float(np.min(msd)) * 0.25, 1e-9), off_max)
            popt, pcov = optimize.curve_fit(
                lambda tt, D_, a_, off_: 4.0 * D_ * tt ** a_ + 4.0 * off_,
                tau, msd,
                p0=[max(D_ll, 1e-9), a_ll, off0], sigma=sigma,
                absolute_sigma=False,
                bounds=([1e-12, 0.05, 0.0], [1e6, 3.0, off_max]), maxfev=10000)
            D, alpha = float(popt[0]), float(popt[1])
            sigma_loc_um = float(np.sqrt(max(popt[2], 0.0)))
        else:
            popt, pcov = optimize.curve_fit(
                lambda tt, D_, a_: 4.0 * D_ * tt ** a_, tau, msd,
                p0=[max(D_ll, 1e-9), a_ll], sigma=sigma, absolute_sigma=False,
                bounds=([1e-12, 0.05], [1e6, 3.0]), maxfev=10000)
            D, alpha = float(popt[0]), float(popt[1])
    except Exception:
        pass  # keep the log-log estimate if the non-linear fit fails
    return D, alpha, sigma_loc_um, popt, pcov


def _assess_msd_identifiability(popt, pcov):
    """The 95% CI of D and alpha from the fit covariance -- extracted verbatim. The interval is REPORTED,
    never reduced to a pass/fail flag (D and alpha are coupled, so no single scalar separates good fits from
    bad); a wide CI is warned. Returns the identifiability dict."""
    identifiability = {}
    try:
        if pcov is not None and np.all(np.isfinite(pcov)):
            perr = np.sqrt(np.diag(pcov))
            for i, name in enumerate(('D_um2_per_s', 'alpha')):
                val, se = float(popt[i]), float(perr[i])
                if not np.isfinite(se) or se <= 0:
                    identifiability[name] = dict(
                        value=val, identifiable=False,
                        reason='the covariance is singular — the parameter is unconstrained')
                    continue
                lo, hi = val - 1.96 * se, val + 1.96 * se
                rel = (hi - lo) / max(abs(val), 1e-12)
                identifiability[name] = dict(
                    value=val, se=se, ci=(float(lo), float(hi)),
                    relative_ci_width=float(rel))
    except Exception as _exc:
        debug_log('MSD: could not assess identifiability', _exc)

    # A CI spanning more than half the value is worth flagging in the log — NOT as a
    # pass/fail verdict, but so it is not missed.
    _wide = [k for k, v in identifiability.items()
             if v.get('relative_ci_width', 0.0) > 0.5]
    if _wide:
        _detail = "; ".join(
            f"{k} = {identifiability[k]['value']:.4g} "
            f"[95% CI {identifiability[k]['ci'][0]:.4g} to "
            f"{identifiability[k]['ci'][1]:.4g}]"
            for k in _wide if 'ci' in identifiability[k])
        napari_show_warning(
            f"MSD fit: {', '.join(_wide)} has a WIDE confidence interval. {_detail}.\n\n"
            f"D and alpha are strongly coupled in MSD = 4·D·tau^alpha: a larger alpha trades "
            f"against a smaller D and fits almost as well, so a short lag window cannot "
            f"separate them. R² stays high regardless (measured: R² RISES from 0.958 to "
            f"0.973 as the window shrinks from 30 lags to 4, while the scatter in D grows "
            f"6-fold).\n\n"
            f"**Viscosity is computed from D. An unidentifiable D is an unidentifiable "
            f"viscosity.** Use more lags, or report the interval alongside the value.")
    return identifiability


def fit_anomalous_diffusion(
    msd_df: pd.DataFrame,
    max_lag_fit: int = None,
    fit_localization_offset: bool = True,
    frame_interval_s: float = None,
    upper_lag_rule: str = 'fraction',
    upper_lag_fraction: float = 0.25,
    upper_lag_fixed_s: float = None,
    min_independent_pairs: int = 10,
    confine_to_defensible_bounds: bool = True,
) -> dict:
    """
    Fit MSD(τ) = 4D·τ^α (anomalous diffusion model) using log-log regression.

    LAG-WINDOW FIT GATE
    -------------------
    The reliable MSD lag window is bounded by hardware on both ends:

    * **High-frequency cutoff = frame rate.** The shortest resolvable lag is one
      frame interval; nothing faster is sampled.
    * **Low-frequency cutoff = acquisition duration.** At long lags there are very
      few independent displacement pairs, so the MSD becomes unreliable well
      before the full record length.

    Fitting outside this band (e.g. only the first fraction of a second, where the
    curve is dominated by the localization-noise floor, or out toward the full
    duration, where a handful of pairs dominate) produces a wrong D/α. The gate
    computes a defensible ``[lag_lo, lag_hi]`` and, when
    ``confine_to_defensible_bounds`` is on (default), fits only within it. It
    **warns rather than blocks** when the data can't cover the requested window.

    Parameters
    ----------
    msd_df : output of compute_msd() (needs lag_s, msd_um2, n_pairs; n_tracks used
        for the min-pairs rule when present).
    max_lag_fit : legacy cap on the number of head lags (kept for back-compat;
        applied before the window gate if given).
    fit_localization_offset : fit the +4σ_loc² localization-offset term.
    frame_interval_s : seconds per frame; sets the high-frequency cutoff
        (lag_lo = frame_interval_s). If None, inferred from the smallest lag.
    upper_lag_rule : how to set the low-frequency (upper-lag) cutoff:
        * 'fraction'    — lag_hi = upper_lag_fraction × (max track duration).
                          The standard convention; conservative.
        * 'fixed'       — lag_hi = upper_lag_fixed_s (a hardware-defensible band,
                          e.g. matching routine lab practice).
        * 'min_pairs'   — keep lags while ≥ min_independent_pairs independent
                          tracks span them (statistically principled; adapts to
                          how many/how long the tracks are).
    upper_lag_fraction : fraction for the 'fraction' rule (default 0.25).
    upper_lag_fixed_s : upper lag (s) for the 'fixed' rule.
    min_independent_pairs : threshold for the 'min_pairs' rule.
    confine_to_defensible_bounds : if True (default), clip the fit to the computed
        window; if False, fit the full available range (at the user's risk).

    Returns
    -------
    dict with keys:
        D_um2_per_s     : apparent diffusion coefficient in µm²/s
        alpha           : anomalous exponent (1=Brownian, <1=subdiff, >1=superdiff)
        motion_type     : 'subdiffusion' | 'Brownian' | 'superdiffusion'
        r_squared       : goodness of fit
        fit_lags_s      : lag times used in fit (array)
        fit_msd         : fitted MSD values (array)
        log_log_slope   : raw slope from log-log regression (= alpha)
        log_log_intercept : raw intercept (log(4D))
        fit_window_s    : (lag_lo, lag_hi) the defensible window used
        fit_window_warning : str or None — set when data can't cover the window
    """
    df, lag_lo, lag_hi, window_warning = _lag_window_gate(
        msd_df, max_lag_fit, frame_interval_s, upper_lag_rule, upper_lag_fraction,
        upper_lag_fixed_s, min_independent_pairs, confine_to_defensible_bounds)

    if len(df) < 3:
        return _insufficient_result(lag_lo, lag_hi, window_warning)

    tau = df['lag_s'].values.astype(float)
    msd = df['msd_um2'].values.astype(float)

    # Initial guess from a log-log regression (fast, unbiased enough to seed).
    log_slope, log_intercept, r, _p, _se = stats.linregress(np.log(tau), np.log(msd))

    # Refine with a DIRECT non-linear fit of MSD = 4·D·τ^α. This avoids the
    # log-transform bias (Jensen) of the pure log-log fit, and weights points by
    # their measured uncertainty (msd_sem) so noisy large-lag points, which have
    # few independent tracks, no longer count as much as precise short-lag ones.
    D_ll = float(np.exp(log_intercept) / 4.0)
    a_ll = float(log_slope)

    D, alpha, sigma_loc_um, popt, pcov = _fit_msd_powerlaw(
        df, tau, msd, fit_localization_offset, D_ll, a_ll)

    identifiability = _assess_msd_identifiability(popt, pcov)

    r2, msd_fit, tau_fit, motion_type, fit_quality, confinement = _classify_msd_motion(
        D, alpha, sigma_loc_um, tau, msd)

    return _package_msd_result(
        D, alpha, motion_type, r2, fit_quality, confinement, sigma_loc_um, tau_fit, msd_fit,
        a_ll, log_intercept, lag_lo, lag_hi, window_warning, identifiability)


def _classify_msd_motion(D, alpha, sigma_loc_um, tau, msd):
    """The fitted curve, R^2, fit-quality, and motion-type classification for fit_anomalous_diffusion --
    extracted verbatim (science_function_split, no numerical change). Returns
    (r2, msd_fit, tau_fit, motion_type, fit_quality, confinement)."""
    tau_fit = tau
    _off = (sigma_loc_um ** 2) if np.isfinite(sigma_loc_um) else 0.0
    msd_fit = 4 * D * tau_fit ** alpha + 4 * _off
    # R² of the (non-linear) model on the actual MSD values.
    ss_res = float(np.sum((msd - msd_fit) ** 2))
    ss_tot = float(np.sum((msd - msd.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan

    # ── Does the POWER LAW actually describe this MSD? ──────────────────────────
    #
    # `motion_type` is read straight off alpha, and alpha is the entire
    # anomalous-vs-Brownian claim. But alpha is only meaningful if the power law is the
    # right model -- and R² will not tell you, because beating a flat line is a trivially
    # low bar for a monotonic MSD.
    #
    # The failure that matters here is CONFINEMENT. A bead trapped in a small condensate
    # produces an MSD that PLATEAUS, and a power law cannot plateau -- so it fits the
    # plateau with a tiny exponent. Measured on a synthetic confined trajectory:
    #
    #     truly Brownian:  alpha = 1.006, R² = 1.000, runs p = 0.78   -> 'Brownian'   OK
    #     CONFINED:        alpha = 0.000, R² = 0.903, runs p = 0.0005 -> 'subdiffusion'
    #
    # The confined bead is reported as SUBDIFFUSION with a healthy R², which a reader
    # takes as "the medium is viscoelastic / crowded". It is not: the bead is hitting a
    # wall. Different physics, wrong conclusion, and R² does not blink.
    #
    # The runs test does: a power law fitted to a plateauing curve sits systematically
    # above the data at short lags and below it at long lags, so the residual signs run
    # in blocks instead of flipping like noise.
    fit_quality = assess_fit(msd, msd_fit, n_params=3,
                             model_name="MSD power law")

    if alpha < 0.85:
        motion_type = 'subdiffusion'
    elif alpha > 1.15:
        motion_type = 'superdiffusion'
        # ── alpha > 1 is what UNCORRECTED STAGE DRIFT looks like ────────────────
        #
        # Confinement is guarded (it pulls alpha DOWN — see the docstring). **The opposite
        # direction was not**, and it is the more common artifact in bead tracking.
        #
        # Drift is BALLISTIC: a stage moving at speed v contributes (v·τ)² to the MSD, which
        # grows as τ² — so it pushes alpha toward 2. And **the slower the probe, the worse it
        # is**, because the drift term is compared against a smaller diffusive signal.
        #
        # In a viscous condensate this is severe. For η = 8 Pa·s and a 100 nm bead,
        # Stokes-Einstein gives D = 0.00027 µm²/s — a near-stationary probe. Measured:
        #
        #     stage drift    alpha    R²
        #     0.000 µm/s     1.12     0.993
        #     0.005 µm/s     1.28     0.993
        #     0.010 µm/s     **1.53** 0.994
        #     0.050 µm/s     **2.09** 0.997
        #
        # **Ten nanometres per second of stage drift takes alpha from 1.0 to 1.53** — and R²
        # does not move. A reader takes 'superdiffusion' as an active or directed process. It
        # is the stage.
        #
        # `drift_correct_com` (vpt_tools) subtracts the common-mode motion of all tracks and
        # IS applied in the VPT pipeline. This warning is for the residual — and for anyone
        # fitting an MSD that did not come through that pipeline.
        napari_show_warning(
            f"MSD: alpha = {alpha:.2f} — reported as SUPERDIFFUSION. **Check for residual "
            f"stage drift before believing that.**\n\n"
            f"Drift is ballistic: a stage moving at speed v adds (v·tau)\u00b2 to the MSD, "
            f"which grows as tau\u00b2 and pushes alpha toward 2. The SLOWER the probe, the "
            f"worse it is — the drift term is compared against a smaller diffusive signal.\n\n"
            f"In a viscous condensate this is severe. For 8 Pa\u00b7s and a 100 nm bead, "
            f"D = 0.00027 \u00b5m\u00b2/s, and **10 nm/s of stage drift takes alpha from 1.0 "
            f"to 1.53 — with R\u00b2 unchanged at 0.993.** A reader takes 'superdiffusion' as "
            f"an active or directed process; it may simply be the stage.\n\n"
            f"The VPT pipeline applies `drift_correct_com`, which subtracts the common-mode "
            f"motion of all tracks. If this MSD did not come through that pipeline, or if "
            f"drift persists after it, alpha is not interpretable as a motion type.")
    else:
        motion_type = 'Brownian'

    # CONFINEMENT is the failure that actually matters here -- see test_confinement.
    # (The residual runs test is kept in `fit_quality` for the record, but it needs >= 8
    # residuals and the defensible lag window is often only ~6, so it usually cannot say
    # anything. It reports 'not assessed' rather than pretending.)
    confinement = test_confinement(tau, msd)
    if confinement.get('confined'):
        motion_type = 'confined (not anomalous diffusion)'
        napari_show_warning("MSD fit: " + confinement['verdict'])
    elif fit_quality.get('assessable', True) and not fit_quality['adequate']:
        motion_type = 'indeterminate (power law does not fit)'
        napari_show_warning(
            "MSD fit: " + fit_quality['verdict'] + " The power law does not describe "
            "this MSD, so alpha is not interpretable and the motion type cannot be "
            "assigned. The most common cause is CONFINEMENT -- a probe hitting the "
            "boundary of a small condensate produces a plateauing MSD, which a power law "
            "fits with a spuriously small exponent and reports as 'subdiffusion'. That "
            "is a wall, not viscoelasticity. Check the probe is sampling the bulk.")

    return r2, msd_fit, tau_fit, motion_type, fit_quality, confinement


def _package_msd_result(D, alpha, motion_type, r2, fit_quality, confinement, sigma_loc_um, tau_fit,
                        msd_fit, a_ll, log_intercept, lag_lo, lag_hi, window_warning, identifiability):
    """Assemble the fit_anomalous_diffusion result dict -- extracted verbatim (no numerical change)."""
    return dict(
        # The interval the data actually supports. Viscosity is computed from D — an
        # unidentifiable D is an unidentifiable viscosity, and it must not be reported as a
        # single confident number.
        identifiability=identifiability,
        D_um2_per_s=D,
        alpha=alpha,
        motion_type=motion_type,
        r_squared=float(r2),
        # Adequacy travels WITH alpha: an R² of 0.90 on a power law that cannot describe
        # a plateauing MSD must not be readable without the evidence that it is wrong.
        fit_quality=fit_quality,
        fit_adequate=bool(fit_quality['adequate']),
        confinement=confinement,
        confined=bool(confinement.get('confined', False)),
        localization_error_nm=(float(sigma_loc_um * 1000.0)
                               if np.isfinite(sigma_loc_um) else float('nan')),
        fit_lags_s=tau_fit,
        fit_msd=msd_fit,
        log_log_slope=a_ll,
        log_log_intercept=log_intercept,
        fit_window_s=(lag_lo, lag_hi),
        fit_window_warning=window_warning,
    )


def msd_per_track(
    tracks_df: pd.DataFrame,
    frame_interval_s: float = 1.0,
    min_track_length: int = MIN_TRACK_LENGTH_FRAMES,
) -> pd.DataFrame:
    """
    Fit anomalous diffusion to each individual track.

    Returns DataFrame with columns:
        track_id, n_frames, D_um2_per_s, alpha, motion_type, r_squared
    """
    rows = []
    for tid, grp in tracks_df.groupby('track_id'):
        if tid < 0 or len(grp) < min_track_length:
            continue
        grp = grp.sort_values('frame')
        # Build single-track MSD
        y, x, t = (grp['y_um'].values, grp['x_um'].values,
                    grp['frame'].values)
        max_lag = max(1, len(t) // 4)
        lag_vals = {}
        for lag in range(1, max_lag + 1):
            disps = [
                (y[j]-y[i])**2 + (x[j]-x[i])**2
                for i in range(len(t))
                for j in range(i+1, len(t))
                if t[j]-t[i] == lag
            ]
            if disps:
                lag_vals[lag] = np.mean(disps)

        if len(lag_vals) < 3:
            continue
        msd_df = pd.DataFrame({
            'lag_frames': list(lag_vals.keys()),
            'lag_s':      [k * frame_interval_s for k in lag_vals],
            'msd_um2':    list(lag_vals.values()),
            'n_pairs':    [10] * len(lag_vals),  # dummy for filter
        })
        fit = fit_anomalous_diffusion(msd_df)
        rows.append({
            'track_id':    int(tid),
            'n_frames':    len(grp),
            'D_um2_per_s': fit['D_um2_per_s'],
            'alpha':       fit['alpha'],
            'motion_type': fit['motion_type'],
            'r_squared':   fit['r_squared'],
        })
    return pd.DataFrame(rows)
