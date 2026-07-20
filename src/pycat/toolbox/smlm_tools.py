"""**SMLM / localization-table analysis — load, normalize to µm, and hand to the spatial stats that exist.**

The lab has super-resolution instruments (TIRF single-molecule, STED, Airyscan) and no localization-table
analysis. The hard part — the spatial statistics (`ripleys_l`, `pair_correlation_function`,
`nearest_neighbour_distance`, `local_object_density` in `spatial_metrology_tools`) — **already exists and
is validated**. So this is the front door, not the analysis: load a PALM/STORM/PAINT localization table,
normalize it, and feed it in.

**The traps ARE the science:**

1. **Units are the whole risk.** ThunderSTORM exports x/y in **nanometres**; other tools use pixels or µm.
   Guessing wrong scales every downstream distance and destroys the cluster analysis exactly as a wrong
   pixel size corrupts viscosity. Units are detected from the column header where possible and REQUIRED
   explicitly (`pixel_size_um`) when ambiguous — never silently assumed. Everything downstream is µm.
2. **Localization precision sets a resolution floor.** A PCF computed without accounting for localization
   uncertainty over-reports clustering at short range (each molecule is a fuzzy blob, not a point). The
   median uncertainty is reported so a user knows the length scale below which structure is not real.
3. **Blinks over-count density.** One molecule blinks and is localized repeatedly across frames; naive
   clustering reads that as a dense cluster. An optional temporal merge (localizations within a distance
   AND consecutive frames collapse to one) is offered with the over-count warning — never silently merged
   or silently not.

Import-and-analyze: PyCAT does not do the localization itself, and does not reimplement the spatial stats.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd


@dataclasses.dataclass
class LocalizationSet:
    """A localization table normalized to µm (the units trap resolved once, at load). ``source_units``
    records what the file used, for provenance; ``uncertainty_nm`` is the per-localization precision when
    the file carried it (the resolution floor)."""
    x_um: np.ndarray
    y_um: np.ndarray
    z_um: "np.ndarray | None" = None
    frame: "np.ndarray | None" = None
    uncertainty_nm: "np.ndarray | None" = None
    n: int = 0
    source_units: str = ''

    def coords_um(self) -> np.ndarray:
        """The (N, 2) µm coordinate array the spatial-stats backend consumes."""
        return np.column_stack([self.x_um, self.y_um])

    def median_precision_nm(self):
        """The median localization precision (nm) — the resolution floor below which clustering is not
        trustworthy — or ``None`` when the file carried no uncertainty column."""
        if self.uncertainty_nm is None:
            return None
        u = self.uncertainty_nm[np.isfinite(self.uncertainty_nm)]
        return float(np.median(u)) if u.size else None


def _detect_unit(colname):
    """The distance unit a column header declares (``'nm'`` / ``'um'`` / ``'px'``), or ``None`` when it is
    bare (``x`` with no unit — the ambiguous case that must be declared, not guessed)."""
    c = colname.lower()
    if '[nm]' in c or '(nm)' in c or ' nm' in c:
        return 'nm'
    if '[um]' in c or '[µm]' in c or '(um)' in c or 'micron' in c or ' um' in c:
        return 'um'
    if '[px]' in c or 'pixel' in c or ' px' in c:
        return 'px'
    return None


def _find_column(df, base):
    """The column whose name is ``base`` or ``base [unit]`` (case-insensitive), or ``None``."""
    base = base.lower()
    for c in df.columns:
        stem = str(c).lower().split('[')[0].split('(')[0].strip()
        if stem == base:
            return c
    return None


def _to_um(values, unit, pixel_size_um):
    if unit == 'nm':
        return values / 1000.0
    if unit == 'um':
        return values.astype(float)
    if unit == 'px':
        return values * float(pixel_size_um)
    raise ValueError(f"unhandled unit {unit!r}")


def load_localization_table(path, *, format='auto', pixel_size_um=None) -> LocalizationSet:
    """Load a localization table (ThunderSTORM CSV / generic CSV with x,y[,z,frame,uncertainty]) into a
    **µm-normalized** ``LocalizationSet``.

    Units are detected from the x/y column headers; when the headers are bare (no unit), the coordinates
    are assumed to be in PIXELS and a ``pixel_size_um`` is REQUIRED — a ``ValueError`` is raised rather
    than guessing, the same gate images already enforce. ``uncertainty``/``sigma`` (nm) and ``frame`` are
    carried through when present.
    """
    df = pd.read_csv(path)
    xcol, ycol = _find_column(df, 'x'), _find_column(df, 'y')
    if xcol is None or ycol is None:
        raise ValueError(f"localization table has no x/y columns (found {list(df.columns)})")

    unit = _detect_unit(str(xcol)) or _detect_unit(str(ycol))
    if unit is None:
        if pixel_size_um is None:
            raise ValueError(
                "the localization table's x/y columns declare NO unit, so their scale is ambiguous. "
                "Pass pixel_size_um to interpret them as pixels, or use a file whose headers state the "
                "unit (e.g. ThunderSTORM's 'x [nm]'). Guessing would scale every distance wrongly and "
                "destroy the cluster analysis.")
        unit = 'px'
    elif unit == 'px' and pixel_size_um is None:
        raise ValueError("the columns are in pixels but no pixel_size_um was given to convert to µm.")

    x_um = _to_um(df[xcol].to_numpy(dtype=float), unit, pixel_size_um)
    y_um = _to_um(df[ycol].to_numpy(dtype=float), unit, pixel_size_um)

    zcol = _find_column(df, 'z')
    z_um = _to_um(df[zcol].to_numpy(dtype=float),
                  _detect_unit(str(zcol)) or unit, pixel_size_um) if zcol is not None else None

    fcol = _find_column(df, 'frame')
    frame = df[fcol].to_numpy() if fcol is not None else None

    ucol = _find_column(df, 'uncertainty') or _find_column(df, 'sigma')
    unc_nm = None
    if ucol is not None:
        u = df[ucol].to_numpy(dtype=float)
        uu = _detect_unit(str(ucol))
        unc_nm = u if uu in (None, 'nm') else (u * 1000.0 if uu == 'um' else u * float(pixel_size_um or 0) * 1000.0)

    return LocalizationSet(x_um=x_um, y_um=y_um, z_um=z_um, frame=frame,
                           uncertainty_nm=unc_nm, n=len(x_um), source_units=unit)


def temporal_merge(locset: LocalizationSet, *, radius_um=0.05, gap_frames=1) -> LocalizationSet:
    """Collapse repeated localizations of one blinking molecule — those within ``radius_um`` AND within
    ``gap_frames`` consecutive frames — to a single averaged position. Reduces the density over-count that
    naive clustering reads as a cluster. Requires a ``frame`` column; without one the set is returned
    unchanged (there is nothing to merge on)."""
    if locset.frame is None:
        return locset
    order = np.argsort(locset.frame, kind='stable')
    xs, ys, fs = locset.x_um[order], locset.y_um[order], np.asarray(locset.frame)[order]
    used = np.zeros(len(xs), dtype=bool)
    mx, my = [], []
    for i in range(len(xs)):
        if used[i]:
            continue
        grp = [i]
        for j in range(i + 1, len(xs)):
            if used[j]:
                continue
            if fs[j] - fs[grp[-1]] > gap_frames:
                if fs[j] - fs[i] > gap_frames:
                    break
                continue
            if (xs[j] - xs[i]) ** 2 + (ys[j] - ys[i]) ** 2 <= radius_um ** 2:
                grp.append(j); used[j] = True
        used[i] = True
        mx.append(float(np.mean(xs[grp]))); my.append(float(np.mean(ys[grp])))
    return LocalizationSet(x_um=np.asarray(mx), y_um=np.asarray(my), z_um=None, frame=None,
                           uncertainty_nm=None, n=len(mx), source_units=locset.source_units)


def analyze_localizations(locset: LocalizationSet, *, cell_area_um2=None, merged=False) -> dict:
    """Run the existing spatial statistics on a loaded localization set and annotate the result with the
    resolution floor (median precision) and the blink caveat.

    ``cell_area_um2`` (the imaged area) is needed by Ripley's L and the PCF; without it those are skipped
    and only the density-free stats run. ``merged`` records whether a temporal merge was applied — an
    un-merged set OVER-COUNTS density, which the result states."""
    from pycat.toolbox.spatial_metrology_tools import (nearest_neighbour_distance,
                                                       local_object_density, ripleys_l,
                                                       pair_correlation_function)
    coords = locset.coords_um()
    out = {
        'n_localizations': int(locset.n),
        'median_localization_precision_nm': locset.median_precision_nm(),
        'temporally_merged': bool(merged),
        'source_units': locset.source_units,
    }
    if not merged and locset.frame is not None:
        out['warning'] = ('Localizations were NOT temporally merged — a blinking molecule localized across '
                          'frames is counted repeatedly, OVER-COUNTING density. Apply temporal_merge() to '
                          'collapse blinks, or interpret density as an upper bound.')
    if locset.n >= 2:
        out['nn'] = nearest_neighbour_distance(coords)
        out['density'] = local_object_density(coords)
    if cell_area_um2:
        out['ripley_l'] = ripleys_l(coords, float(cell_area_um2))
        out['pcf'] = pair_correlation_function(coords, float(cell_area_um2))
    return out
