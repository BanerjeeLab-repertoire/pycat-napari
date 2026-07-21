"""VPT **bead population routing** — split out of vpt_tools (1.6.239).

split_bead_populations separates classified detections into the three NEVER-MIXED populations (singlet
probes, aggregates, out-of-plane); select_bead_population picks one; aggregate_population_stats summarises
the aggregate secondary set. Moved VERBATIM - no number changed; pinned by the VPT tests. The tools module
re-exports the three public entry points (used by vpt_ui and run_vpt_analysis).
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 4b. Bead population routing (primary probes vs. aggregate secondary set)
# ---------------------------------------------------------------------------

def split_bead_populations(detections_df: pd.DataFrame,
                           recover_out_of_plane: bool = False) -> dict:
    """Separate classified detections into three NEVER-MIXED populations.

    The three bead classes are kept strictly separate so microrheology runs on a
    known, homogeneous probe population:

      singlet     (green)  — clean, in-focus single beads. The correct default
                             for Stokes-Einstein viscosity (known single-bead
                             size, reliable centroid).
      out_of_plane(yellow) — dim / out-of-focus beads. Position is less certain,
                             so they are NOT mixed into the singlet measurement
                             by default. They can be analysed ON THEIR OWN (to
                             check whether they give a consistent viscosity) and
                             only then, at the user's choice, combined with the
                             singlets.
      aggregate   (red)    — aggregates (and ambiguous). Their size biases
                             Stokes-Einstein, so they are ALWAYS a separate
                             readout (count / size / mobility), never in the
                             viscosity population.

    Returns a dict with 'singlet', 'out_of_plane', 'aggregate' DataFrames, plus
    'primary' for backward compatibility (singlets, or singlets+out_of_plane if
    recover_out_of_plane is True). Callers that want a specific population should
    read the named key directly rather than 'primary'.
    """
    if detections_df is None or detections_df.empty \
            or 'bead_class' not in detections_df.columns:
        empty = pd.DataFrame()
        base = detections_df if detections_df is not None else empty
        return dict(primary=base, singlet=base, out_of_plane=empty,
                    aggregate=empty)
    df = detections_df
    singlet = df[df['bead_class'].isin(['singlet', 'unfit'])].reset_index(drop=True)
    out_of_plane = df[df['bead_class'] == 'out_of_plane'].reset_index(drop=True)
    aggregate = df[df['bead_class'].isin(['aggregate', 'ambiguous'])].reset_index(drop=True)
    # 'primary' kept for backward compatibility with existing callers.
    if recover_out_of_plane and len(out_of_plane):
        primary = pd.concat([singlet, out_of_plane], ignore_index=True)
    else:
        primary = singlet
    return dict(primary=primary, singlet=singlet,
                out_of_plane=out_of_plane, aggregate=aggregate)


def select_bead_population(detections_df: pd.DataFrame, which: str = 'singlet') -> pd.DataFrame:
    """Return one (or a deliberate combination) of the bead populations for
    microrheology, by name.

    which : 'singlet' (green, default) | 'out_of_plane' (yellow) |
            'singlet+out_of_plane' (green+yellow, opt-in) | 'aggregate' (red).
    Populations are never mixed except the explicit 'singlet+out_of_plane'.
    """
    pops = split_bead_populations(detections_df)
    if which == 'singlet+out_of_plane':
        parts = [pops['singlet'], pops['out_of_plane']]
        parts = [p for p in parts if p is not None and len(p)]
        return pd.concat(parts, ignore_index=True) if parts else pops['singlet']
    return pops.get(which, pops['singlet'])


def aggregate_population_stats(aggregate_df: pd.DataFrame,
                              total_by_frame: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    Per-frame aggregation readout from the aggregate population.

    Parameters
    ----------
    aggregate_df : detections classified as aggregates (with n_units_est,
        integrated_intensity, sigma_mean).
    total_by_frame : optional Series indexed by frame giving the TOTAL number
        of beads (all classes) per frame, so an aggregated fraction can be
        reported.

    Returns
    -------
    DataFrame indexed by frame:
        n_aggregates       : count of aggregate detections
        total_aggregated_units : summed n_units_est (total beads' worth of
                                 signal tied up in aggregates)
        median_aggregate_units : typical aggregate size (in bead-units)
        median_sigma           : typical aggregate width (px)
        aggregated_fraction    : n_aggregates / total beads (if total given)
    """
    if aggregate_df is None or aggregate_df.empty:
        return pd.DataFrame(columns=[
            'frame', 'n_aggregates', 'total_aggregated_units',
            'median_aggregate_units', 'median_sigma', 'aggregated_fraction'])
    g = aggregate_df.groupby('frame')
    cols = {'n_aggregates': g.size()}
    # n_units_est and sigma_mean only exist in FIT detection mode; fast
    # (template) mode does not fit a Gaussian, so guard each column and fill
    # NaN when it is absent rather than raising a KeyError.
    if 'n_units_est' in aggregate_df.columns:
        cols['total_aggregated_units'] = g['n_units_est'].sum(min_count=1)
        cols['median_aggregate_units'] = g['n_units_est'].median()
    else:
        cols['total_aggregated_units'] = np.nan
        cols['median_aggregate_units'] = np.nan
    if 'sigma_mean' in aggregate_df.columns:
        cols['median_sigma'] = g['sigma_mean'].median()
    else:
        cols['median_sigma'] = np.nan
    out = pd.DataFrame(cols)
    if total_by_frame is not None:
        out['aggregated_fraction'] = (out['n_aggregates']
                                      / total_by_frame.reindex(out.index)).astype(float)
    else:
        out['aggregated_fraction'] = np.nan
    return out.reset_index()
