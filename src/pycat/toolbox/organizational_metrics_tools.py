"""
PyCAT Organizational Metrics Toolbox
======================================
Cell-level and population-level organizational metrics for condensate spatial
distribution.

Analyses
--------
1. Spatial entropy (information-theoretic spatial disorder)
2. Cluster size distribution (DBSCAN-based)
3. Inter-condensate spacing distribution
4. Per-cell occupancy / fractional area
5. Distance-to-boundary distributions

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import skimage as sk
from scipy import ndimage, spatial, stats


# ---------------------------------------------------------------------------
# 1. Spatial entropy
# ---------------------------------------------------------------------------

def spatial_entropy(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    n_bins: int = 10,
    microns_per_pixel: float = 1.0,
) -> dict:
    """
    Spatial entropy of condensate distribution within a cell.

    The cell is divided into an n_bins × n_bins grid; condensate counts
    per grid cell form a probability distribution whose Shannon entropy
    is computed.

    H_max = log₂(n_bins²) for a perfectly uniform distribution.
    Normalised entropy H/H_max: 0 = all condensates in one bin, 1 = uniform.

    Parameters
    ----------
    coords : (N, 2) array of [y_um, x_um]
    cell_mask : (H, W) binary mask
    n_bins : grid resolution for entropy calculation

    Returns
    -------
    dict with keys: entropy_bits, normalised_entropy, n_occupied_bins,
                    total_bins
    """
    if len(coords) == 0:
        return dict(entropy_bits=np.nan, normalised_entropy=np.nan,
                    n_occupied_bins=0, total_bins=n_bins**2)

    # Grid over the cell bounding box
    rows, cols = np.where(cell_mask)
    y_min = rows.min() * microns_per_pixel
    y_max = rows.max() * microns_per_pixel
    x_min = cols.min() * microns_per_pixel
    x_max = cols.max() * microns_per_pixel

    y_edges = np.linspace(y_min, y_max + 1e-9, n_bins + 1)
    x_edges = np.linspace(x_min, x_max + 1e-9, n_bins + 1)

    hist, _, _ = np.histogram2d(
        coords[:, 0], coords[:, 1], bins=[y_edges, x_edges])
    hist = hist.flatten()
    hist = hist[hist > 0]

    if len(hist) == 0:
        return dict(entropy_bits=0.0, normalised_entropy=0.0,
                    n_occupied_bins=0, total_bins=n_bins**2)

    p = hist / hist.sum()
    H = float(-np.sum(p * np.log2(p + 1e-15)))
    H_max = np.log2(n_bins**2)

    return dict(
        entropy_bits=H,
        normalised_entropy=H / H_max if H_max > 0 else np.nan,
        n_occupied_bins=int(len(hist)),
        total_bins=n_bins**2,
    )


# ---------------------------------------------------------------------------
# 2. Cluster size distribution (DBSCAN)
# ---------------------------------------------------------------------------

def cluster_size_distribution(
    coords: np.ndarray,
    eps_um: float = 2.0,
    min_samples: int = 2,
) -> pd.DataFrame:
    """
    DBSCAN clustering of condensate centroids to identify spatial clusters,
    then report the distribution of cluster sizes.

    Parameters
    ----------
    coords : (N, 2) array in µm
    eps_um : DBSCAN neighbourhood radius in µm
    min_samples : minimum condensates to form a cluster

    Returns
    -------
    DataFrame with columns: cluster_id, cluster_size, is_noise
    Plus attrs: n_clusters, n_noise, mean_cluster_size, fraction_clustered
    """
    if len(coords) < min_samples:
        return pd.DataFrame(columns=['cluster_id', 'cluster_size', 'is_noise'])

    from sklearn.cluster import DBSCAN
    labels = DBSCAN(eps=eps_um, min_samples=min_samples).fit_predict(coords)

    rows = []
    unique_labels = set(labels)
    n_clusters = sum(1 for l in unique_labels if l >= 0)
    n_noise    = int((labels == -1).sum())

    for lbl in sorted(unique_labels):
        size = int((labels == lbl).sum())
        rows.append({
            'cluster_id':   int(lbl),
            'cluster_size': size,
            'is_noise':     (lbl == -1),
        })

    df = pd.DataFrame(rows)
    df.attrs['n_clusters']         = n_clusters
    df.attrs['n_noise']            = n_noise
    df.attrs['mean_cluster_size']  = (
        float(df[~df['is_noise']]['cluster_size'].mean()) if n_clusters > 0 else np.nan)
    df.attrs['fraction_clustered'] = (
        float((labels >= 0).sum()) / len(labels) if len(labels) > 0 else np.nan)
    return df


# ---------------------------------------------------------------------------
# 3. Inter-condensate spacing distribution
# ---------------------------------------------------------------------------

def inter_condensate_spacing(
    coords: np.ndarray,
    k_neighbours: int = 3,
) -> pd.DataFrame:
    """
    Distribution of pairwise and k-nearest-neighbour distances between
    condensate centroids.

    Returns
    -------
    DataFrame with columns: condensate_idx, neighbour_rank (1..k),
                             distance_um
    Plus attrs: mean_spacing_um, median_spacing_um, std_spacing_um,
                coefficient_of_variation
    """
    if len(coords) < 2:
        return pd.DataFrame(columns=['condensate_idx', 'neighbour_rank',
                                      'distance_um'])

    k = min(k_neighbours + 1, len(coords))
    tree  = spatial.KDTree(coords)
    dists, idxs = tree.query(coords, k=k)

    rows = []
    for i, (row_dists, row_idxs) in enumerate(zip(dists, idxs)):
        for rank, (d, j) in enumerate(zip(row_dists[1:], row_idxs[1:]), 1):
            rows.append({'condensate_idx': i, 'neighbour_rank': rank,
                          'distance_um': float(d)})

    df = pd.DataFrame(rows)
    all_dists = df['distance_um'].values
    df.attrs['mean_spacing_um']        = float(all_dists.mean())
    df.attrs['median_spacing_um']      = float(np.median(all_dists))
    df.attrs['std_spacing_um']         = float(all_dists.std())
    df.attrs['coefficient_of_variation'] = (
        float(all_dists.std() / all_dists.mean()) if all_dists.mean() > 0 else np.nan)
    return df


# ---------------------------------------------------------------------------
# 4. Per-cell occupancy / fractional area
# ---------------------------------------------------------------------------

def per_cell_occupancy(
    puncta_mask: np.ndarray,
    labeled_cells: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Fraction of each cell's area occupied by condensates.

    Also reports condensate number density (condensates per µm²) and
    mean condensate area within each cell.

    Returns
    -------
    DataFrame with columns: cell_label, cell_area_um2, condensate_area_um2,
                             occupancy_fraction, n_condensates,
                             condensate_density_per_um2, mean_condensate_area_um2
    """
    binary_puncta = puncta_mask > 0
    rows = []
    for lbl in np.unique(labeled_cells)[1:]:
        cmask = labeled_cells == lbl
        cell_area  = float(cmask.sum()) * microns_per_pixel**2
        cond_area  = float((binary_puncta & cmask).sum()) * microns_per_pixel**2
        labeled_in_cell = sk.measure.label(binary_puncta & cmask)
        n_cond     = int(labeled_in_cell.max())
        density    = n_cond / cell_area if cell_area > 0 else np.nan
        mean_a     = cond_area / n_cond if n_cond > 0 else np.nan
        rows.append({
            'cell_label':                int(lbl),
            'cell_area_um2':             cell_area,
            'condensate_area_um2':       cond_area,
            'occupancy_fraction':        cond_area / cell_area if cell_area > 0 else np.nan,
            'n_condensates':             n_cond,
            'condensate_density_per_um2': density,
            'mean_condensate_area_um2':  mean_a,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Distance-to-boundary distributions
# ---------------------------------------------------------------------------

def distance_to_boundary(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> dict:
    """
    Distance from each condensate centroid to the cell boundary.

    Uses the Euclidean distance transform of the cell mask (each interior
    pixel's distance to the nearest boundary pixel).

    Returns
    -------
    dict with keys: distances_um (per condensate), mean_dist_um,
                    median_dist_um, std_dist_um,
                    normalised_distances (distance / max_dist_in_cell),
                    max_inscribed_radius_um
    """
    if len(coords) == 0:
        return dict(distances_um=np.array([]), mean_dist_um=np.nan,
                    median_dist_um=np.nan, std_dist_um=np.nan,
                    normalised_distances=np.array([]),
                    max_inscribed_radius_um=np.nan)

    from scipy.ndimage import distance_transform_edt
    dist_map = distance_transform_edt(cell_mask) * microns_per_pixel
    max_inscribed = float(dist_map.max())

    # Sample dist_map at condensate positions
    # coords are in µm — convert back to pixels for indexing
    y_px = (coords[:, 0] / microns_per_pixel).astype(int)
    x_px = (coords[:, 1] / microns_per_pixel).astype(int)

    # Clip to mask bounds
    H, W = cell_mask.shape
    y_px = np.clip(y_px, 0, H - 1)
    x_px = np.clip(x_px, 0, W - 1)
    dists = dist_map[y_px, x_px]

    return dict(
        distances_um=dists,
        mean_dist_um=float(dists.mean()),
        median_dist_um=float(np.median(dists)),
        std_dist_um=float(dists.std()),
        normalised_distances=dists / max_inscribed if max_inscribed > 0 else dists,
        max_inscribed_radius_um=max_inscribed,
    )


# ---------------------------------------------------------------------------
# Convenience: run all organizational metrics for one cell
# ---------------------------------------------------------------------------

def run_all_organizational_metrics(
    coords: np.ndarray,
    cell_mask: np.ndarray,
    puncta_mask: np.ndarray,
    labeled_cells: np.ndarray,
    cell_label: int,
    microns_per_pixel: float,
    eps_um: float = 2.0,
    k_neighbours: int = 3,
    n_entropy_bins: int = 10,
) -> dict:
    results = {}
    results['spatial_entropy']   = spatial_entropy(
        coords, cell_mask, n_entropy_bins, microns_per_pixel)
    results['cluster_size']      = cluster_size_distribution(coords, eps_um)
    results['spacing']           = inter_condensate_spacing(coords, k_neighbours)
    results['occupancy']         = per_cell_occupancy(
        puncta_mask, labeled_cells, microns_per_pixel)
    results['dist_to_boundary']  = distance_to_boundary(
        coords, cell_mask, microns_per_pixel)
    return results
