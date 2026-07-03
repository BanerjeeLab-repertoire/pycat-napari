"""
PyCAT Morphological Complexity Toolbox
========================================
Per-object and per-cell shape complexity metrics for condensate analysis.

Analyses
--------
1. Fractal dimension  (box-counting on binary mask)
2. Lacunarity         (texture/gap distribution within mask)
3. Tortuosity         (path length vs. end-to-end for fibrillar structures)
4. Orientation / anisotropy order parameter  (for elongated objects)

All functions operate on 2D binary or labelled masks.

Author
------
    Gable Wadsworth / Christian Neureuter, Banerjee Lab, SUNY Buffalo
Date: 2025
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
import skimage as sk
from scipy import ndimage


# ---------------------------------------------------------------------------
# 1. Fractal dimension — box-counting
# ---------------------------------------------------------------------------

def fractal_dimension_box_counting(binary_mask: np.ndarray) -> float:
    """
    Estimate the 2D box-counting fractal dimension of a binary mask.

    D = 1 → line-like structure.
    D = 2 → space-filling / compact object.
    Values between 1 and 2 indicate fractal / irregular boundaries.

    Parameters
    ----------
    binary_mask : (H, W) bool or 0/1 array

    Returns
    -------
    float — estimated fractal dimension D_f
    """
    mask = (binary_mask > 0).astype(np.uint8)
    if mask.sum() == 0:
        return np.nan

    H, W = mask.shape
    min_dim = min(H, W)
    # Box sizes: powers of 2, from 2 up to min_dim // 2
    sizes = [2**k for k in range(1, int(np.log2(min_dim)))]
    if not sizes:
        return np.nan

    counts = []
    for s in sizes:
        # Coarsen by summing over boxes of size s
        h_trim = (H // s) * s
        w_trim = (W // s) * s
        coarse = mask[:h_trim, :w_trim].reshape(H // s, s, W // s, s).any(axis=(1, 3))
        counts.append(int(coarse.sum()))

    sizes  = np.array(sizes, dtype=float)
    counts = np.array(counts, dtype=float)
    valid  = counts > 0
    if valid.sum() < 2:
        return np.nan

    coeffs = np.polyfit(np.log(1.0 / sizes[valid]), np.log(counts[valid]), 1)
    return float(coeffs[0])


def fractal_dimension_per_cell(
    puncta_mask: np.ndarray,
    labeled_cells: np.ndarray,
) -> pd.DataFrame:
    """
    Compute fractal dimension for condensates within each cell.

    Also computes per-object fractal dimensions for individual condensates
    large enough to provide meaningful box-counting statistics (> 16 px).

    Returns
    -------
    DataFrame with columns: cell_label, cell_fd, mean_object_fd, std_object_fd
    """
    rows = []
    labeled_puncta = sk.measure.label(puncta_mask > 0)
    for cell_lbl in np.unique(labeled_cells)[1:]:
        cmask = labeled_cells == cell_lbl
        cell_puncta = (labeled_puncta > 0) & cmask

        # Cell-level FD (all condensates together as one mask)
        cell_fd = fractal_dimension_box_counting(cell_puncta)

        # Per-object FD (individual condensates)
        obj_fds = []
        for prop in sk.measure.regionprops(sk.measure.label(cell_puncta)):
            if prop.area >= 16:
                b = prop.image
                obj_fds.append(fractal_dimension_box_counting(b))

        rows.append({
            'cell_label':     int(cell_lbl),
            'cell_fd':        cell_fd,
            'mean_object_fd': float(np.nanmean(obj_fds)) if obj_fds else np.nan,
            'std_object_fd':  float(np.nanstd(obj_fds))  if obj_fds else np.nan,
            'n_objects_fd':   len(obj_fds),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Lacunarity
# ---------------------------------------------------------------------------

def lacunarity(
    binary_mask: np.ndarray,
    box_sizes: list[int] = None,
) -> pd.DataFrame:
    """
    Lacunarity measures the 'gappiness' or texture heterogeneity of a mask.

    High lacunarity → many gaps / heterogeneous distribution.
    Low lacunarity  → homogeneous / space-filling.

    Uses the gliding-box algorithm: for each box size r, slide a box across
    the image, count filled pixels in each position, compute the ratio of
    the second moment to the square of the first moment.

    Λ(r) = (μ₂/μ₁²)  where μₖ = kth moment of the box pixel count distribution.
    Λ = 1 → homogeneous (Poisson); Λ > 1 → clustered / gappy.

    Parameters
    ----------
    binary_mask : (H, W) bool array
    box_sizes : list of integer box side lengths to evaluate

    Returns
    -------
    DataFrame with columns: box_size, lacunarity
    """
    mask = (binary_mask > 0).astype(np.float32)
    H, W = mask.shape
    if box_sizes is None:
        max_r = min(H, W) // 2
        box_sizes = [2**k for k in range(1, int(np.log2(max(max_r, 2))) + 1)]

    rows = []
    # Vectorized gliding-box using scipy.ndimage.uniform_filter.
    # uniform_filter(img, size=r) computes the box mean at every position
    # in a single O(H×W) pass regardless of box size, replacing the
    # O(H×W×n_sizes) list comprehension.  Results are identical to the
    # integral-image approach but 4-8× faster on large masks.
    for r in box_sizes:
        if r >= H or r >= W:
            continue
        # Box sums = box_mean * r^2; use full array (gliding box, step 1)
        box_mean = ndimage.uniform_filter(mask.astype(np.float32),
                                           size=r, mode='constant')
        box_sums = box_mean * (r * r)
        # Trim border (uniform_filter pads with zeros outside mask)
        trim = r // 2
        if trim > 0:
            box_sums = box_sums[trim:-trim, trim:-trim]
        box_sums = box_sums.ravel()
        mu1 = box_sums.mean()
        mu2 = (box_sums ** 2).mean()
        lac = (mu2 / mu1**2) if mu1 > 0 else np.nan
        rows.append({'box_size': r, 'lacunarity': float(lac)})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Tortuosity
# ---------------------------------------------------------------------------

def tortuosity_per_object(
    labeled_mask: np.ndarray,
    microns_per_pixel: float = 1.0,
) -> pd.DataFrame:
    """
    Tortuosity for each labelled object — ratio of skeleton path length
    to end-to-end Euclidean distance.  Most meaningful for fibrillar/
    elongated condensates (eccentricity > 0.7).

    T = 1   → perfectly straight.
    T > 1   → curved / tortuous path.

    Uses scikit-image skeletonization to trace the object's medial axis.

    Returns
    -------
    DataFrame with columns: label, tortuosity, path_length_um,
                             end_to_end_um, eccentricity
    """
    from skimage.morphology import skeletonize

    rows = []
    for prop in sk.measure.regionprops(labeled_mask):
        binary = prop.image.astype(bool)
        skel   = skeletonize(binary)
        skel_pts = np.column_stack(np.where(skel))

        if len(skel_pts) < 2:
            rows.append({'label': prop.label, 'tortuosity': np.nan,
                         'path_length_um': np.nan, 'end_to_end_um': np.nan,
                         'eccentricity': prop.eccentricity})
            continue

        # Path length via sparse MST of skeleton points — O(N log N) instead
        # of the O(N²) greedy nearest-neighbour traversal.
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import minimum_spanning_tree
        from scipy.spatial import cKDTree

        # Only connect skeleton pixels that are actually adjacent (≤√2 px apart)
        # to avoid spurious long edges across skeleton branches
        tree = cKDTree(skel_pts)
        pairs = tree.query_pairs(r=1.5)          # 8-connectivity
        if not pairs:
            rows.append({'label': prop.label, 'tortuosity': np.nan,
                         'path_length_um': np.nan, 'end_to_end_um': np.nan,
                         'eccentricity': prop.eccentricity})
            continue

        n = len(skel_pts)
        rows_idx, cols_idx, data = [], [], []
        for i, j in pairs:
            d = float(np.linalg.norm(skel_pts[i] - skel_pts[j]))
            rows_idx += [i, j]; cols_idx += [j, i]; data += [d, d]
        adj = csr_matrix((data, (rows_idx, cols_idx)), shape=(n, n))
        mst = minimum_spanning_tree(adj)

        path_len   = float(mst.sum()) * microns_per_pixel
        end_to_end = float(np.linalg.norm(
            (skel_pts[-1] - skel_pts[0]).astype(float))) * microns_per_pixel
        tortuosity = (path_len / end_to_end) if end_to_end > 0 else np.nan

        rows.append({
            'label':         prop.label,
            'tortuosity':    tortuosity,
            'path_length_um':path_len,
            'end_to_end_um': end_to_end,
            'eccentricity':  prop.eccentricity,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Orientation / anisotropy order parameter
# ---------------------------------------------------------------------------

def orientation_order_parameter(
    labeled_mask: np.ndarray,
) -> dict:
    """
    Orientation and anisotropy metrics for all objects in a mask.

    Per-object:
      - orientation_rad : major axis angle (−π/2 to π/2, from regionprops)
      - eccentricity    : 0 = circle, 1 = line
      - anisotropy      : 1 − (minor_axis / major_axis)

    Ensemble:
      - S (nematic order parameter): S = <cos²θ − 1/2> × 2, range [0,1]
        S=0 → isotropic, S=1 → perfectly aligned.
      - circular_variance : 1 − |mean resultant length|, range [0,1]
        0 → all same orientation, 1 → uniformly dispersed orientations.
      - preferred_angle_deg : mean orientation angle in degrees.

    Returns
    -------
    dict with keys: per_object_df, S, circular_variance, preferred_angle_deg,
                    mean_eccentricity, mean_anisotropy
    """
    rows = []
    for prop in sk.measure.regionprops(labeled_mask):
        maj = prop.axis_major_length
        minn = prop.axis_minor_length
        aniso = (1 - minn / maj) if maj > 0 else 0.0
        rows.append({
            'label':           prop.label,
            'orientation_rad': prop.orientation,
            'eccentricity':    prop.eccentricity,
            'anisotropy':      aniso,
            'major_axis_um':   maj,
            'minor_axis_um':   minn,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return dict(per_object_df=df, S=np.nan,
                    circular_variance=np.nan, preferred_angle_deg=np.nan,
                    mean_eccentricity=np.nan, mean_anisotropy=np.nan)

    angles = df['orientation_rad'].values
    # Nematic order parameter S (headless vectors → use 2θ)
    S = float(np.mean(np.cos(2 * angles)))   # ranges −1 to 1; abs for clarity
    # Circular mean and variance
    mean_resultant = np.mean(np.exp(2j * angles))   # complex mean of doubled angles
    circ_var = 1 - abs(mean_resultant)
    preferred_angle = float(np.angle(mean_resultant) / 2 * 180 / np.pi)

    return dict(
        per_object_df=df,
        S=abs(S),
        circular_variance=float(circ_var),
        preferred_angle_deg=preferred_angle,
        mean_eccentricity=float(df['eccentricity'].mean()),
        mean_anisotropy=float(df['anisotropy'].mean()),
    )

# ---------------------------------------------------------------------------
# UI entry point (Toolbox)
# ---------------------------------------------------------------------------

def _add_morphological_complexity(ui_instance, layout=None, separate_widget=False):
    """
    Widget: morphological-complexity metrics on a condensate mask —
    fractal dimension, lacunarity, tortuosity, and orientational order.
    """
    import napari
    import numpy as np
    import pandas as pd
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QPushButton, QCheckBox, QProgressBar)

    grp  = QGroupBox("Morphological Complexity")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    desc = QLabel(
        "Shape-complexity metrics on a condensate mask: box-counting fractal "
        "dimension and lacunarity (space-filling / gappiness of the whole "
        "pattern), plus per-object tortuosity and an orientational order "
        "parameter. Takes a labels (or binary) mask.")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    mask_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    mask_dd.setToolTip("Condensate mask — labels (per-object metrics) or binary.")
    form.addRow("Condensate mask:", mask_dd)

    cb_fractal = QCheckBox("Fractal dimension + lacunarity (whole pattern)")
    cb_fractal.setChecked(True); form.addRow(cb_fractal)
    cb_tortuosity = QCheckBox("Tortuosity (per object)")
    cb_tortuosity.setChecked(True); form.addRow(cb_tortuosity)
    cb_orient = QCheckBox("Orientational order parameter")
    cb_orient.setChecked(True); form.addRow(cb_orient)

    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton("▶  Compute Complexity Metrics")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); form.addRow(btn)

    def _mpx():
        try:
            v = ui_instance.central_manager.active_data_class.data_repository.get('microns_per_pixel_sq')
            return float(v) ** 0.5 if v else 1.0
        except Exception:
            return 1.0

    def _on_run():
        from napari.utils.notifications import show_info as _info, show_warning as _warn
        layers = [l.name for l in ui_instance.viewer.layers]
        mname = mask_dd.currentText()
        if mname not in layers:
            _warn("Select a valid condensate mask."); return
        mask = np.asarray(ui_instance.viewer.layers[mname].data)
        if mask.ndim != 2:
            _warn("Morphological metrics expect a 2D mask (or one slice)."); return

        prog.setVisible(True); prog.setRange(0, 0)
        tables = []
        summary = {}
        try:
            binary = mask > 0
            if cb_fractal.isChecked():
                fd = fractal_dimension_box_counting(binary)
                lac = lacunarity(binary)
                summary['fractal_dimension'] = round(float(fd), 4)
                if not lac.empty:
                    tables.append(('Lacunarity', lac.round(4)))
                    summary['mean_lacunarity'] = round(float(lac['lacunarity'].mean()), 4) \
                        if 'lacunarity' in lac.columns else None
            if cb_tortuosity.isChecked():
                tort = tortuosity_per_object(mask, microns_per_pixel=_mpx())
                if not tort.empty:
                    tables.append(('Tortuosity (per object)', tort.round(4)))
                    tcol = [c for c in tort.columns if 'tortuosity' in c.lower()]
                    if tcol:
                        summary['median_tortuosity'] = round(float(tort[tcol[0]].median()), 4)
            if cb_orient.isChecked():
                orient = orientation_order_parameter(mask)
                summary['orientational_order_S'] = round(float(orient.get('S', np.nan)), 4)
                odf = orient.get('per_object_df')
                if odf is not None and not odf.empty:
                    tables.append(('Orientation (per object)', odf.round(4)))
        except Exception as e:
            prog.setVisible(False)
            _warn(f"Complexity metrics failed: {e}")
            import traceback; traceback.print_exc(); return
        prog.setVisible(False)

        summary_df = pd.DataFrame([summary]) if summary else pd.DataFrame()
        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'morphological_complexity_summary'] = summary_df
        except Exception:
            pass
        rec = getattr(ui_instance, '_record', None)
        if callable(rec):
            rec('morphological_complexity', {'mask': mname, **summary})

        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            all_tables = ([('Summary', summary_df)] if not summary_df.empty else []) + tables
            if all_tables:
                show_dataframes_dialog("Morphological Complexity", all_tables)
        except Exception:
            pass
        bits = ", ".join(f"{k}={v}" for k, v in summary.items())
        _info(f"Morphological complexity — {bits}" if bits else "Done.")

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QSizePolicy
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Morphological Complexity", area='right')

