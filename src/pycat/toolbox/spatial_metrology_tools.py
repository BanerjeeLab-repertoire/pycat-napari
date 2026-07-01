"""
PyCAT Spatial Metrology Toolbox
=================================
Quantitative spatial analysis of condensate (puncta) positions within cells.

All analyses operate on 2D (y, x) centroid coordinates extracted from the
puncta segmentation mask, expressed in microns.  Every function is per-cell
so that cell-to-cell variability is preserved and results can be pooled or
compared across conditions.

Analyses implemented
---------------------
1.  Nearest-neighbour distance (NND)
2.  Radial localization profiling (radial density vs. cell centre)
3.  Local object density (per-condensate, kernel density estimate)
4.  Ripley's K / L function   L(r) = sqrt(K(r)/π) − r
5.  Pair correlation function  g(r) — PCF
6.  Voronoi tessellation metrics (area, perimeter, regularity)
7.  Delaunay triangulation metrics (edge lengths, angles)
8.  Minimum spanning tree (MST) metrics (edge lengths, branching)
9.  Convex hull / occupancy metrics (area, fraction of cell occupied)
10. Distance to user-defined ROI (shapes layer)

Dependencies
------------
scipy, scikit-image (already in PyCAT), networkx (for MST), shapely
(for convex hull / ROI distance — optional, falls back to scipy.spatial).

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo

Date
----
    2025
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
import skimage as sk
from scipy import spatial, stats
from typing import Optional


# ---------------------------------------------------------------------------
# Coordinate extraction
# ---------------------------------------------------------------------------

def get_puncta_centroids(
    puncta_mask: np.ndarray,
    labeled_cells: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Extract (y, x) centroid coordinates for every punctum, labelled by cell.

    Parameters
    ----------
    puncta_mask : (H, W) binary or labelled mask of condensates
    labeled_cells : (H, W) integer-labelled cell mask
    microns_per_pixel : float, pixel size in µm (linear, not squared)

    Returns
    -------
    DataFrame with columns: cell_label, punctum_label, y_px, x_px, y_um, x_um
    """
    if puncta_mask.max() <= 1:
        labeled_puncta = sk.measure.label(puncta_mask > 0)
    else:
        labeled_puncta = puncta_mask.astype(int)

    rows = []
    for prop in sk.measure.regionprops(labeled_puncta):
        cy, cx = prop.centroid
        cell_lbl = int(labeled_cells[int(cy), int(cx)])
        rows.append({
            'cell_label':   cell_lbl,
            'punctum_label': prop.label,
            'y_px': cy,
            'x_px': cx,
            'y_um': cy * microns_per_pixel,
            'x_um': cx * microns_per_pixel,
        })
    return pd.DataFrame(rows)


def _cell_centroids(df: pd.DataFrame, cell_label: int) -> np.ndarray:
    """Return (N, 2) array of [y_um, x_um] for one cell."""
    sub = df[df['cell_label'] == cell_label]
    return sub[['y_um', 'x_um']].values


# ---------------------------------------------------------------------------
# 1. Nearest-neighbour distance
# ---------------------------------------------------------------------------

def nearest_neighbour_distance(
    coords: np.ndarray,
) -> dict:
    """
    Per-condensate distance to its nearest neighbour (in µm).

    Parameters
    ----------
    coords : (N, 2) array of [y_um, x_um]

    Returns
    -------
    dict with keys: nnd_values, mean_nnd, median_nnd, std_nnd, cv_nnd
    """
    if len(coords) < 2:
        return dict(nnd_values=np.array([np.nan]), mean_nnd=np.nan,
                    median_nnd=np.nan, std_nnd=np.nan, cv_nnd=np.nan)
    tree = spatial.KDTree(coords)
    dists, _ = tree.query(coords, k=2)
    nnd = dists[:, 1]          # skip k=0 (self)
    return dict(
        nnd_values=nnd,
        mean_nnd=float(nnd.mean()),
        median_nnd=float(np.median(nnd)),
        std_nnd=float(nnd.std()),
        cv_nnd=float(nnd.std() / nnd.mean()) if nnd.mean() > 0 else np.nan,
    )


# ---------------------------------------------------------------------------
# 2. Radial localization profiling
# ---------------------------------------------------------------------------

def radial_localization_profile(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    n_bins: int = 10,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Condensate density as a function of normalised distance from cell centre.

    Normalised radius 0 = cell centroid, 1 = cell boundary.
    Each bin reports the number of condensates per unit area (µm²) so
    values are comparable across cells of different sizes.

    Returns
    -------
    DataFrame with columns: r_norm_centre, r_norm_edge, count, area_um2,
                             density_per_um2
    """
    # Cell centre (centroid of mask)
    cy, cx = np.array(np.where(cell_mask)).mean(axis=1)
    cy_um, cx_um = cy * microns_per_pixel, cx * microns_per_pixel

    # Distance transform from cell boundary → each pixel's distance to edge (px)
    from scipy.ndimage import distance_transform_edt
    dist_to_edge = distance_transform_edt(cell_mask)
    max_dist     = dist_to_edge.max()
    if max_dist == 0:
        return pd.DataFrame()

    # Condensate distances from cell centre in normalised coords
    if len(coords) == 0:
        return pd.DataFrame()

    dy = coords[:, 0] - cy_um
    dx = coords[:, 1] - cx_um
    r_abs = np.sqrt(dy**2 + dx**2)  # µm from cell centre

    # Convert pixel coords back to get normalised radius
    # r_norm: distance from centre / max possible distance in that direction
    # Approximate as distance from centre / (max dist to edge in px × mpx)
    r_norm = r_abs / (max_dist * microns_per_pixel + 1e-8)
    r_norm = np.clip(r_norm, 0, 1)

    bins = np.linspace(0, 1, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask_bin = (r_norm >= lo) & (r_norm < hi)
        count = int(mask_bin.sum())
        # Area of the annular ring for this cell (pixels in that ring)
        norm_dist = dist_to_edge / (max_dist + 1e-8)
        ring_px = ((norm_dist >= lo) & (norm_dist < hi) & cell_mask).sum()
        area_um2 = float(ring_px) * microns_per_pixel**2
        rows.append({
            'r_norm_centre': lo,
            'r_norm_edge':   hi,
            'count':         count,
            'area_um2':      area_um2,
            'density_per_um2': count / area_um2 if area_um2 > 0 else 0.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Local object density (KDE)
# ---------------------------------------------------------------------------

def local_object_density(
    coords: np.ndarray,
    bandwidth: float = None,
) -> dict:
    """
    Per-condensate local density using Gaussian KDE.

    Parameters
    ----------
    coords : (N, 2) array in µm
    bandwidth : KDE bandwidth in µm. If None, uses Scott's rule.

    Returns
    -------
    dict with keys: density_values (per condensate), mean_density,
                    bandwidth_used
    """
    if len(coords) < 2:
        return dict(density_values=np.array([np.nan]),
                    mean_density=np.nan, bandwidth_used=np.nan)

    kde = stats.gaussian_kde(coords.T, bw_method=bandwidth)
    density = kde(coords.T)
    bw = float(kde.factor * coords.std())
    return dict(
        density_values=density,
        mean_density=float(density.mean()),
        bandwidth_used=bw,
    )


# ---------------------------------------------------------------------------
# 4. Ripley's K and L function
# ---------------------------------------------------------------------------

def ripleys_l(
    coords: np.ndarray,
    cell_area_um2: float,
    r_values: np.ndarray = None,
    edge_correct: bool = True,
) -> pd.DataFrame:
    """
    Ripley's L(r) = sqrt(K(r)/π) − r.

    L(r) > 0 indicates clustering at scale r.
    L(r) < 0 indicates regularity / repulsion at scale r.
    L(r) = 0 is the CSR (complete spatial randomness) expectation.

    Uses Ripley's edge correction (toroidal) approximation.

    Parameters
    ----------
    coords : (N, 2) array in µm
    cell_area_um2 : total cell area in µm²
    r_values : radii to evaluate (µm). Defaults to 20 values up to
               sqrt(cell_area / π) (the inscribed circle radius).
    edge_correct : apply Ripley's edge correction

    Returns
    -------
    DataFrame with columns: r_um, K_r, L_r, L_r_minus_r
    """
    n = len(coords)
    if n < 3:
        return pd.DataFrame(columns=['r_um', 'K_r', 'L_r', 'L_r_minus_r'])

    if r_values is None:
        r_max = np.sqrt(cell_area_um2 / np.pi) * 0.5
        r_values = np.linspace(0, r_max, 30)[1:]

    density = n / cell_area_um2
    tree = spatial.KDTree(coords)
    rows = []
    for r in r_values:
        # Count pairs within radius r (excluding self)
        counts = tree.query_ball_point(coords, r)
        pair_sum = sum(len(c) - 1 for c in counts)  # subtract self

        if edge_correct:
            # Ripley's isotropic edge correction weight ≈ 1 + r/sqrt(A/π)
            w = 1.0 + r / max(np.sqrt(cell_area_um2 / np.pi), 1e-8)
            K = (pair_sum * w) / (n * density)
        else:
            K = pair_sum / (n * density)

        L  = np.sqrt(max(K, 0) / np.pi)
        rows.append({'r_um': r, 'K_r': K, 'L_r': L, 'L_r_minus_r': L - r})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Pair correlation function g(r)
# ---------------------------------------------------------------------------

def pair_correlation_function(
    coords: np.ndarray,
    cell_area_um2: float,
    r_max: float = None,
    dr: float = None,
    edge_correct: bool = True,
) -> pd.DataFrame:
    """
    Pair correlation function g(r) — radial distribution function.

    g(r) = 1 → CSR (random).
    g(r) > 1 → more pairs at distance r than expected (clustering).
    g(r) < 1 → fewer pairs (repulsion / exclusion zone).

    Parameters
    ----------
    coords : (N, 2) array in µm
    cell_area_um2 : total cell area in µm²
    r_max : maximum radius to evaluate (µm). Default: sqrt(A/π)/2
    dr : bin width in µm. Default: r_max / 25

    Returns
    -------
    DataFrame with columns: r_centre_um, g_r, n_pairs
    """
    n = len(coords)
    if n < 3:
        return pd.DataFrame(columns=['r_centre_um', 'g_r', 'n_pairs'])

    if r_max is None:
        r_max = np.sqrt(cell_area_um2 / np.pi) * 0.5
    if dr is None:
        dr = r_max / 25.0

    density = n / cell_area_um2
    r_edges = np.arange(0, r_max + dr, dr)
    r_centres = 0.5 * (r_edges[:-1] + r_edges[1:])
    tree = spatial.KDTree(coords)
    rows = []

    for rc, r_lo, r_hi in zip(r_centres, r_edges[:-1], r_edges[1:]):
        # Pairs in annulus [r_lo, r_hi]
        in_hi = tree.query_ball_point(coords, r_hi)
        in_lo = tree.query_ball_point(coords, r_lo)
        pairs = sum(
            max(0, len(h) - len(l) - (1 if i in h else 0))
            for i, (h, l) in enumerate(zip(in_hi, in_lo))
        )
        ring_area = np.pi * (r_hi**2 - r_lo**2)
        expected  = density * ring_area * n
        g = pairs / max(expected, 1e-10)
        if edge_correct and rc > 0:
            # Approximate toroidal correction
            g *= cell_area_um2 / (cell_area_um2 - np.pi * rc**2 / 4)
        rows.append({'r_centre_um': rc, 'g_r': g, 'n_pairs': pairs})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. Voronoi tessellation
# ---------------------------------------------------------------------------

def voronoi_metrics(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Voronoi tessellation of condensate centroids, clipped to the cell boundary.

    Returns per-condensate Voronoi cell area and a regularity index
    (CV of Voronoi areas — lower = more regular spacing).

    Parameters
    ----------
    coords : (N, 2) array in µm
    cell_mask : (H, W) binary cell mask (used to clip infinite regions)
    microns_per_pixel : float

    Returns
    -------
    DataFrame with columns: punctum_idx, voronoi_area_um2, voronoi_perimeter_um
    Plus scalar: regularity_cv (CV of areas), mean_area_um2
    """
    if len(coords) < 4:
        return pd.DataFrame(columns=['punctum_idx', 'voronoi_area_um2'])

    # Cell boundary polygon for clipping
    cell_area_um2 = float(cell_mask.sum()) * microns_per_pixel**2
    cell_contour = sk.measure.find_contours(cell_mask.astype(float), 0.5)
    if not cell_contour:
        return pd.DataFrame()

    try:
        from shapely.geometry import Polygon, MultiPolygon
        from shapely.ops import unary_union
        cell_poly = Polygon(cell_contour[0][:, ::-1] * microns_per_pixel)
    except ImportError:
        # Fallback: just compute unconstrained Voronoi areas
        cell_poly = None

    vor = spatial.Voronoi(coords)
    rows = []
    for i, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        if -1 in region or not region:
            rows.append({'punctum_idx': i, 'voronoi_area_um2': np.nan,
                         'voronoi_perimeter_um': np.nan})
            continue
        verts = vor.vertices[region]
        if cell_poly is not None:
            try:
                from shapely.geometry import Polygon as SPoly
                cell_region = SPoly(verts).intersection(cell_poly)
                area = cell_region.area
                perim = cell_region.length
            except Exception:
                area = SPoly(verts).area
                perim = SPoly(verts).length
        else:
            from scipy.spatial import ConvexHull
            try:
                hull = ConvexHull(verts)
                area = hull.volume  # 2D 'volume' = area
                perim = np.nan
            except Exception:
                area = np.nan
                perim = np.nan
        rows.append({'punctum_idx': i, 'voronoi_area_um2': area,
                     'voronoi_perimeter_um': perim})

    df = pd.DataFrame(rows)
    areas = df['voronoi_area_um2'].dropna()
    df.attrs['regularity_cv']  = float(areas.std() / areas.mean()) if len(areas) > 1 else np.nan
    df.attrs['mean_area_um2']  = float(areas.mean()) if len(areas) > 0 else np.nan
    df.attrs['cell_area_um2']  = cell_area_um2
    return df


# ---------------------------------------------------------------------------
# 7. Delaunay triangulation
# ---------------------------------------------------------------------------

def delaunay_metrics(
    coords: np.ndarray,
) -> pd.DataFrame:
    """
    Delaunay triangulation of condensate centroids.

    Returns edge lengths and triangle properties — useful for detecting
    spatial scales of organisation.

    Returns
    -------
    DataFrame with columns: edge_length_um (one row per unique edge)
    dict attrs: mean_edge_um, median_edge_um, std_edge_um,
                mean_triangle_area_um2
    """
    if len(coords) < 3:
        return pd.DataFrame(columns=['edge_length_um'])

    tri = spatial.Delaunay(coords)
    edges = set()
    for simplex in tri.simplices:
        for i in range(3):
            a, b = simplex[i], simplex[(i + 1) % 3]
            edges.add((min(a, b), max(a, b)))

    lengths = [float(np.linalg.norm(coords[a] - coords[b])) for a, b in edges]
    df = pd.DataFrame({'edge_length_um': lengths})

    # Triangle areas
    areas = []
    for s in tri.simplices:
        A, B, C = coords[s[0]], coords[s[1]], coords[s[2]]
        area = 0.5 * abs((B[0]-A[0])*(C[1]-A[1]) - (C[0]-A[0])*(B[1]-A[1]))
        areas.append(area)

    df.attrs['mean_edge_um']         = float(np.mean(lengths))
    df.attrs['median_edge_um']       = float(np.median(lengths))
    df.attrs['std_edge_um']          = float(np.std(lengths))
    df.attrs['n_edges']              = len(lengths)
    df.attrs['mean_triangle_area_um2'] = float(np.mean(areas))
    return df


# ---------------------------------------------------------------------------
# 8. Minimum spanning tree
# ---------------------------------------------------------------------------

def minimum_spanning_tree(
    coords: np.ndarray,
) -> dict:
    """
    Minimum spanning tree (MST) of condensate centroids.

    MST edge lengths characterise the typical scale of connectivity
    between condensates — short mean MST edge → tightly packed clusters.

    Uses scipy/networkx Kruskal algorithm on a complete distance graph.

    Returns
    -------
    dict with keys:
        edge_lengths_um : np.ndarray of MST edge lengths
        mean_mst_edge_um, median_mst_edge_um, std_mst_edge_um
        total_mst_length_um
        mst_edges : list of (i, j) index tuples
    """
    n = len(coords)
    if n < 2:
        return dict(edge_lengths_um=np.array([np.nan]),
                    mean_mst_edge_um=np.nan, median_mst_edge_um=np.nan,
                    std_mst_edge_um=np.nan, total_mst_length_um=np.nan,
                    mst_edges=[])

    # Full pairwise distance matrix → sparse CSR → MST
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import minimum_spanning_tree as scipy_mst

    dist_mat = spatial.distance_matrix(coords, coords)
    mst = scipy_mst(csr_matrix(dist_mat))
    cx  = mst.tocoo()
    edges   = list(zip(cx.row.tolist(), cx.col.tolist()))
    lengths = cx.data.tolist()

    return dict(
        edge_lengths_um=np.array(lengths),
        mean_mst_edge_um=float(np.mean(lengths)),
        median_mst_edge_um=float(np.median(lengths)),
        std_mst_edge_um=float(np.std(lengths)),
        total_mst_length_um=float(np.sum(lengths)),
        mst_edges=edges,
    )


# ---------------------------------------------------------------------------
# 9. Convex hull / occupancy metrics
# ---------------------------------------------------------------------------

def convex_hull_metrics(
    coords: np.ndarray,
    cell_area_um2: float,
) -> dict:
    """
    Convex hull of condensate centroids and fraction of cell occupied.

    Parameters
    ----------
    coords : (N, 2) array in µm
    cell_area_um2 : total cell area in µm²

    Returns
    -------
    dict with keys: hull_area_um2, hull_perimeter_um,
                    hull_compactness (4π·A/P²), occupancy_fraction,
                    n_hull_vertices
    """
    if len(coords) < 3:
        return dict(hull_area_um2=np.nan, hull_perimeter_um=np.nan,
                    hull_compactness=np.nan, occupancy_fraction=np.nan,
                    n_hull_vertices=len(coords))
    try:
        hull = spatial.ConvexHull(coords)
        area = hull.volume   # 2D: volume = area
        # Perimeter: sum of edge lengths of hull
        verts = coords[hull.vertices]
        perim = float(np.sum(np.linalg.norm(
            np.diff(np.vstack([verts, verts[0]]), axis=0), axis=1)))
        compactness = (4 * np.pi * area / perim**2) if perim > 0 else np.nan
        return dict(
            hull_area_um2=float(area),
            hull_perimeter_um=perim,
            hull_compactness=compactness,
            occupancy_fraction=float(area / cell_area_um2) if cell_area_um2 > 0 else np.nan,
            n_hull_vertices=len(hull.vertices),
        )
    except Exception:
        return dict(hull_area_um2=np.nan, hull_perimeter_um=np.nan,
                    hull_compactness=np.nan, occupancy_fraction=np.nan,
                    n_hull_vertices=0)


# ---------------------------------------------------------------------------
# 10. Distance to user-defined ROI
# ---------------------------------------------------------------------------

def distance_to_roi(
    coords: np.ndarray,
    roi_coords: np.ndarray,
    roi_type: str = 'polygon',
) -> dict:
    """
    Distance from each condensate centroid to a user-drawn ROI.

    Parameters
    ----------
    coords : (N, 2) array of condensate positions in µm
    roi_coords : (M, 2) array of ROI boundary points in µm
                 (vertices for polygon, two points for line, one for point)
    roi_type : 'polygon', 'line', or 'point'

    Returns
    -------
    dict with keys: distances_um (per condensate), mean_dist_um,
                    median_dist_um, std_dist_um, n_inside (for polygon ROI)
    """
    if len(coords) == 0:
        return dict(distances_um=np.array([]), mean_dist_um=np.nan,
                    median_dist_um=np.nan, std_dist_um=np.nan, n_inside=0)

    if roi_type == 'point':
        # Distance from each condensate to the single ROI point
        dists = np.linalg.norm(coords - roi_coords[0], axis=1)
        n_inside = 0

    elif roi_type == 'line':
        # Perpendicular distance from each condensate to the line segment
        A, B = roi_coords[0], roi_coords[-1]
        AB = B - A
        AB_len = np.linalg.norm(AB)
        if AB_len < 1e-10:
            dists = np.linalg.norm(coords - A, axis=1)
        else:
            t = np.clip(((coords - A) @ AB) / AB_len**2, 0, 1)
            proj = A + t[:, None] * AB
            dists = np.linalg.norm(coords - proj, axis=1)
        n_inside = 0

    else:  # polygon
        try:
            from shapely.geometry import Polygon, Point
            poly = Polygon(roi_coords)
            dists = np.array([poly.exterior.distance(Point(p)) for p in coords])
            # Negative = inside the polygon
            inside = np.array([poly.contains(Point(p)) for p in coords])
            dists[inside] = -dists[inside]
            n_inside = int(inside.sum())
        except ImportError:
            # Fallback: distance to nearest boundary point
            tree  = spatial.KDTree(roi_coords)
            dists, _ = tree.query(coords)
            n_inside = 0

    return dict(
        distances_um=dists,
        mean_dist_um=float(np.mean(dists)),
        median_dist_um=float(np.median(dists)),
        std_dist_um=float(np.std(dists)),
        n_inside=n_inside,
    )


# ---------------------------------------------------------------------------
# Convenience: run all analyses for one cell
# ---------------------------------------------------------------------------

def run_all_spatial_metrics(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    microns_per_pixel: float,
    roi_coords: Optional[np.ndarray] = None,
    roi_type: str = 'polygon',
    r_values: Optional[np.ndarray] = None,
    n_radial_bins: int = 10,
) -> dict:
    """
    Run all spatial metrology analyses for a single cell.

    Returns
    -------
    dict with keys mapping to each analysis result (dict or DataFrame).
    """
    cell_area_um2 = float(cell_mask.sum()) * microns_per_pixel**2
    results = {}

    results['nnd']          = nearest_neighbour_distance(coords)
    results['radial']       = radial_localization_profile(
        coords, cell_mask, n_bins=n_radial_bins,
        microns_per_pixel=microns_per_pixel)
    results['kde_density']  = local_object_density(coords)
    results['ripleys_l']    = ripleys_l(coords, cell_area_um2,
                                         r_values=r_values)
    results['pcf']          = pair_correlation_function(coords, cell_area_um2)
    results['voronoi']      = voronoi_metrics(coords, cell_mask,
                                               microns_per_pixel)
    results['delaunay']     = delaunay_metrics(coords)
    results['mst']          = minimum_spanning_tree(coords)
    results['convex_hull']  = convex_hull_metrics(coords, cell_area_um2)

    if roi_coords is not None:
        results['roi_distance'] = distance_to_roi(coords, roi_coords, roi_type)

    return results
