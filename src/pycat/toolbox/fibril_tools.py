"""
PyCAT Fibril Analysis Tools
============================
Quantitative analysis of fibrillar / filamentous structures in fluorescence
images, addressing four goals:

1. **Bead-on-fibril detection** — local widenings of the fibril skeleton (varicosities,
   condensate decorations, aggregates) detected via the distance-transform local-width
   profile along the skeleton, following the spirit of BlebQuant (Bhatt et al.) but
   using a geometry-correct approach that handles beads that are topologically connected
   to the fibril rather than separate objects.

2. **Fibril morphometry** — per-segment length, tortuosity, curvature, and
   persistence length (tangent autocorrelation); per-node degree, branching order;
   mesh/pore size from enclosed areas.

3. **Before/after registration** — subpixel image registration (port of the
   Guizar-Sicairos DFT algorithm via ``skimage.registration.phase_cross_correlation``)
   to align two acquisitions of the same field, then compare bead counts or morphometry
   across conditions.

4. **Crossing node map + graph theory** — skeleton junction pixels as a napari Points
   layer, plus NetworkX graph metrics: degree distribution, betweenness centrality of
   crossing nodes, connected component analysis, cycle basis (mesh loops), clustering.

The shared substrate for goals 1, 2, and 4 is the **skeleton graph** — a NetworkX
graph built directly from the thinned skeleton image, where:

- **Nodes** are skeleton pixels at junctions (degree > 2) or endpoints (degree 1).
- **Edges** are the degree-2 chains between them (fibril segments).
- Each edge carries the pixel path, path length, and local width profile.

Author
------
    Gable Wadsworth, Banerjee Lab, SUNY Buffalo
"""

import numpy as np
import networkx as nx
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

try:
    import skimage as sk
    from skimage.morphology import skeletonize, dilation, disk
except ImportError as _e:
    raise ImportError("fibril_tools requires scikit-image >= 0.19") from _e

try:
    from napari.utils.notifications import show_warning as napari_show_warning
    from napari.utils.notifications import show_info as napari_show_info
except Exception:
    def napari_show_warning(msg): print(f"[warning] {msg}")
    def napari_show_info(msg): print(f"[info] {msg}")


# ---------------------------------------------------------------------------
# 0.  Shared substrate — skeleton graph
# ---------------------------------------------------------------------------
def build_skeleton_graph(binary_mask):
    """
    Thin a binary fibril mask and build a NetworkX graph of its skeleton.

    Nodes carry ``pos=(row, col)`` and ``kind`` ('junction', 'endpoint', or
    'isolate'). Edges carry the pixel ``path`` (list of (r,c) tuples),
    ``length_px`` (number of pixels along the path), and a ``width_profile``
    (half-widths from the distance transform at each path pixel).

    Parameters
    ----------
    binary_mask : 2D bool array

    Returns
    -------
    G : nx.Graph
    skel : 2D bool array
        The thinned skeleton.
    dt : 2D float array
        Distance transform of the mask (half-width at each pixel).
    """
    mask = np.asarray(binary_mask, bool)
    skel = skeletonize(mask)
    dt = distance_transform_edt(mask)

    if not skel.any():
        return nx.Graph(), skel, dt

    pts = np.column_stack(np.where(skel))        # (N, 2) array of skeleton pixels
    idx = {(r, c): i for i, (r, c) in enumerate(pts)}
    tree = cKDTree(pts)

    # Build the pixel-adjacency graph (8-connected)
    pixel_G = nx.Graph()
    for i, (r, c) in enumerate(pts):
        pixel_G.add_node(i, pos=(r, c), width=float(dt[r, c]))
    for a, b in tree.query_pairs(r=1.5):
        pixel_G.add_edge(a, b)

    # Classify pixels by degree
    degrees = dict(pixel_G.degree())

    def _kind(d):
        if d == 1:  return 'endpoint'
        if d == 2:  return 'filament'
        return 'junction'

    for n, d in degrees.items():
        pixel_G.nodes[n]['kind'] = _kind(d)

    # Condense to segment graph: nodes = junctions + endpoints,
    # edges = degree-2 chains between them (the filament segments).
    G = nx.Graph()
    seed_nodes = {n for n, d in degrees.items() if d != 2}
    if not seed_nodes:
        # A closed loop with no junctions: treat any pixel as a junction.
        seed_nodes = {0}

    for n in seed_nodes:
        r, c = pts[n]
        G.add_node(n, pos=(r, c), kind=pixel_G.nodes[n]['kind'],
                   width=float(dt[r, c]))

    visited_edges = set()
    for start in seed_nodes:
        for nb in pixel_G.neighbors(start):
            if (start, nb) in visited_edges or (nb, start) in visited_edges:
                continue
            # Walk degree-2 chain from start -> nb until we hit another seed
            path_nodes = [start, nb]
            while path_nodes[-1] not in seed_nodes:
                nexts = [n for n in pixel_G.neighbors(path_nodes[-1])
                         if n != path_nodes[-2]]
                if not nexts:
                    break
                path_nodes.append(nexts[0])
            for i in range(len(path_nodes) - 1):
                visited_edges.add((path_nodes[i], path_nodes[i + 1]))
                visited_edges.add((path_nodes[i + 1], path_nodes[i]))
            end = path_nodes[-1]
            if end not in G:
                r2, c2 = pts[end]
                G.add_node(end, pos=(r2, c2), kind=pixel_G.nodes[end]['kind'],
                           width=float(dt[r2, c2]))
            if G.has_edge(start, end):
                continue
            path_coords = [tuple(pts[n]) for n in path_nodes]
            width_profile = [float(dt[r2][c2]) for r2, c2 in path_coords]
            # Euclidean path length (sum of step distances)
            deltas = np.diff(path_coords, axis=0)
            length_px = float(np.sum(np.linalg.norm(deltas, axis=1))) if len(deltas) else 0.0
            G.add_edge(start, end, path=path_coords, length_px=length_px,
                       width_profile=width_profile, n_pixels=len(path_coords))
    return G, skel, dt


# ---------------------------------------------------------------------------
# 1.  Bead-on-fibril detection
# ---------------------------------------------------------------------------
def detect_beads_on_fibrils(binary_mask, min_bead_radius=2.0,
                             width_threshold_factor=2.0,
                             min_bead_area_px=None,
                             max_bead_area_px=None):
    """
    Detect local widenings (beads/varicosities) along the fibril skeleton using
    the distance-transform local-width profile.

    A skeleton point is classified as a bead if its local half-width (from the
    distance transform) exceeds ``width_threshold_factor`` times the background
    fibril half-width (estimated robustly as the 25th percentile of all skeleton
    widths). Bead points are then clustered by connectivity to produce distinct
    bead objects with centroid, size, and max half-width.

    Parameters
    ----------
    binary_mask : 2D bool array
    min_bead_radius : float
        Minimum bead half-width in pixels. Points below this are never beads.
    width_threshold_factor : float
        A bead point needs width > ``width_threshold_factor * background_width``
        AND > ``min_bead_radius``.
    min_bead_area_px, max_bead_area_px : float or None
        Area (px²) gates on the clustered bead objects.

    Returns
    -------
    beads : list of dict
        Each dict: centroid (r,c), area_px, max_half_width_px,
        mean_half_width_px, bead_pixels (array of (r,c)).
    skel : 2D bool array
    dt : 2D float array
    """
    mask = np.asarray(binary_mask, bool)
    _, skel, dt = build_skeleton_graph(mask)

    if not skel.any():
        return [], skel, dt

    skel_pts = np.column_stack(np.where(skel))
    widths = dt[skel_pts[:, 0], skel_pts[:, 1]]

    bg_width = float(np.percentile(widths, 25))
    threshold = max(float(min_bead_radius), bg_width * float(width_threshold_factor))

    bead_pt_mask = widths > threshold
    if not bead_pt_mask.any():
        return [], skel, dt

    bead_skel = np.zeros_like(skel, bool)
    bead_skel[skel_pts[bead_pt_mask, 0], skel_pts[bead_pt_mask, 1]] = True
    bead_skel = dilation(bead_skel, disk(max(1, int(bg_width))))
    labeled = sk.measure.label(bead_skel, connectivity=2)

    beads = []
    for prop in sk.measure.regionprops(labeled):
        area = prop.area
        if min_bead_area_px is not None and area < min_bead_area_px:
            continue
        if max_bead_area_px is not None and area > max_bead_area_px:
            continue
        # Width at the bead skeleton points
        coords = prop.coords
        in_skel = skel[coords[:, 0], coords[:, 1]]
        skel_coords = coords[in_skel]
        if len(skel_coords) == 0:
            continue
        hw = dt[skel_coords[:, 0], skel_coords[:, 1]]
        beads.append({
            'centroid': prop.centroid,
            'area_px': float(area),
            'max_half_width_px': float(hw.max()),
            'mean_half_width_px': float(hw.mean()),
            'bead_pixels': skel_coords,
            'background_half_width_px': bg_width,
        })
    return beads, skel, dt


def run_bead_detection(image_layer, mask_layer, min_bead_radius,
                       width_factor, viewer):
    """UI runner for bead-on-fibril detection."""
    if mask_layer is None:
        napari_show_warning("Fibril bead detection: select a binary fibril mask.")
        return
    mask = np.asarray(mask_layer.data, bool)
    if mask.ndim > 2:
        mask = mask[tuple(int(i) for i in viewer.dims.current_step[:mask.ndim-2])]

    beads, skel, dt = detect_beads_on_fibrils(
        mask, min_bead_radius=min_bead_radius,
        width_threshold_factor=width_factor)

    if not beads:
        napari_show_info("Fibril bead detection: no beads found with current thresholds.")
        return

    centroids = np.array([b['centroid'] for b in beads])
    viewer.add_points(centroids, name=f"Beads ({mask_layer.name})",
                      size=6, face_color='red', edge_color='white')
    viewer.add_image(skel.astype(np.uint8),
                     name=f"Skeleton ({mask_layer.name})", colormap='green',
                     opacity=0.6, blending='additive')

    areas = [b['area_px'] for b in beads]
    widths = [b['max_half_width_px'] for b in beads]
    bg = beads[0]['background_half_width_px']
    napari_show_info(
        f"── Bead-on-fibril detection ─────────────────\n"
        f"Beads detected   : {len(beads)}\n"
        f"Fibril bg width  : {bg:.1f} px (half-width)\n"
        f"Bead area (px²)  : min={min(areas):.0f}  max={max(areas):.0f}  "
        f"mean={np.mean(areas):.0f}\n"
        f"Max half-width   : min={min(widths):.1f}  max={max(widths):.1f}  "
        f"mean={np.mean(widths):.1f} px\n"
        f"Interpretation   : beads are local widenings >{width_factor:.1f}× the "
        f"background fibril diameter.")


# ---------------------------------------------------------------------------
# 2.  Fibril morphometry
# ---------------------------------------------------------------------------
def fibril_morphometry(binary_mask, px_size_um=1.0):
    """
    Compute per-segment and per-node morphometry from the skeleton graph.

    Returns
    -------
    segment_df : list of dict (one per edge/segment)
        length_um, length_px, tortuosity, mean_curvature, mean_half_width_px,
        max_half_width_px, persistence_length_um (from tangent autocorrelation).

        .. warning::

           **``persistence_length_um`` SCALES WITH THE FIBRE LENGTH, and comparing it between
           conditions whose fibres differ in length will manufacture a stiffness difference.**

           Persistence length is estimated from the decay of the tangent autocorrelation. On a
           **perfectly straight fibre the correlation never decays**, so the fit is bounded only
           by **how much fibre was available** — not by the material.

           Measured on straight fibres (tortuosity ~ 1.00, i.e. no bending at all):

               fibre length   reported Lp
               40 px          72.1
               80 px          149.1
               120 px         226.0
               200 px         379.9

           **Lp ~ 1.9 x the fibre length.** A straight fibre has *infinite* persistence length; the
           number reported is a property of the measurement window.

           **Two conditions whose fibres differ only in LENGTH will show different "stiffness"**,
           and it would look like a real result. Compare Lp only between fibres of comparable
           length — and treat any Lp that is a fixed multiple of the segment length as
           unmeasured, not as stiff.

           (``tortuosity`` does NOT have this problem: it is 1.0021-1.0106 across the same
           fibres, correctly reporting that all of them are straight. **Prefer it** when the
           question is "how bendy are these fibres".)
    node_df : list of dict (one per junction/endpoint node)
        pos, degree, kind.
    summary : dict
        Global summary: total_length_um, n_segments, n_junctions, n_endpoints,
        mean_tortuosity, mean_persistence_length_um, mesh_size_um2.
    """
    G, skel, dt = build_skeleton_graph(np.asarray(binary_mask, bool))
    s = float(px_size_um)

    segment_rows = []
    for u, v, edata in G.edges(data=True):
        path = np.array(edata['path'], dtype=float)
        length_um = edata['length_px'] * s

        # Tortuosity = path length / end-to-end distance
        end_to_end = float(np.linalg.norm(path[-1] - path[0])) * s if len(path) > 1 else 0.0
        tortuosity = (length_um / end_to_end) if end_to_end > 1e-9 else np.nan

        # Mean curvature: average angular change per unit length along the path
        if len(path) >= 3:
            tangents = np.diff(path, axis=0)
            norms = np.linalg.norm(tangents, axis=1, keepdims=True)
            norms = np.where(norms < 1e-9, 1.0, norms)
            unit_t = tangents / norms
            # dot product between consecutive tangents
            dots = np.clip((unit_t[:-1] * unit_t[1:]).sum(axis=1), -1, 1)
            angles = np.arccos(dots)                  # radians
            mean_curvature = float(np.mean(angles)) / s   # rad / um
        else:
            mean_curvature = np.nan

        # Persistence length from tangent autocorrelation:
        # <cos θ(s)> = exp(-s / L_p) => fit L_p
        persistence_um = np.nan
        if len(path) >= 6:
            try:
                tangents = np.diff(path, axis=0)
                norms = np.linalg.norm(tangents, axis=1, keepdims=True)
                unit_t = tangents / np.where(norms < 1e-9, 1.0, norms)
                cos_theta = [(unit_t[0] * unit_t[k]).sum()
                             for k in range(len(unit_t))]
                cos_theta = np.clip(cos_theta, 1e-9, 1.0)
                s_vals = np.arange(len(cos_theta)) * s
                # Fit: -s/L_p = log(<cos θ(s)>) => L_p = -s / log(<cos θ>)
                log_cos = np.log(cos_theta)
                # robust linear fit (force through origin)
                valid = (log_cos < -1e-6) & np.isfinite(log_cos)
                if valid.sum() >= 3:
                    from numpy.polynomial import polynomial as P
                    # slope of log_cos vs s_vals through origin
                    slope = (s_vals[valid] * log_cos[valid]).sum() / (s_vals[valid] ** 2).sum()
                    persistence_um = float(-1.0 / slope) if slope < 0 else np.nan
            except Exception:
                pass

        wps = edata['width_profile']
        segment_rows.append({
            'length_um': length_um,
            'length_px': edata['length_px'],
            'tortuosity': tortuosity,
            'mean_curvature_rad_um': mean_curvature,
            'mean_half_width_px': float(np.mean(wps)),
            'max_half_width_px': float(np.max(wps)),
            'persistence_length_um': persistence_um,
        })

    node_rows = []
    for n, ndata in G.nodes(data=True):
        node_rows.append({
            'pos': ndata['pos'],
            'degree': G.degree(n),
            'kind': ndata.get('kind', ''),
            'width_px': ndata.get('width', np.nan),
        })

    # Mesh size: mean enclosed area from cycle basis in the graph
    mesh_um2 = np.nan
    try:
        cycles = nx.cycle_basis(G)
        if cycles:
            areas = []
            for cycle in cycles:
                coords = [G.nodes[n]['pos'] for n in cycle]
                coords = np.array(coords)
                # Shoelace formula
                x, y = coords[:, 1], coords[:, 0]
                area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
                areas.append(area * s ** 2)
            mesh_um2 = float(np.mean(areas))
    except Exception:
        pass

    n_junctions = sum(1 for _, d in G.degree() if d > 2)
    n_endpoints = sum(1 for _, d in G.degree() if d == 1)
    valid_torts = [r['tortuosity'] for r in segment_rows
                   if np.isfinite(r.get('tortuosity', np.nan))]
    valid_pl = [r['persistence_length_um'] for r in segment_rows
                if np.isfinite(r.get('persistence_length_um', np.nan))]

    summary = {
        'n_segments': len(segment_rows),
        'n_junctions': n_junctions,
        'n_endpoints': n_endpoints,
        'total_length_um': sum(r['length_um'] for r in segment_rows),
        'mean_tortuosity': float(np.mean(valid_torts)) if valid_torts else np.nan,
        'mean_persistence_length_um': float(np.mean(valid_pl)) if valid_pl else np.nan,
        'mesh_size_um2': mesh_um2,
    }
    return segment_rows, node_rows, summary


def run_fibril_morphometry(mask_layer, px_size_um, viewer):
    """UI runner for fibril morphometry."""
    if mask_layer is None:
        napari_show_warning("Fibril morphometry: select a binary fibril mask.")
        return
    mask = np.asarray(mask_layer.data, bool)
    if mask.ndim > 2:
        mask = mask[tuple(int(i) for i in viewer.dims.current_step[:mask.ndim-2])]

    seg_rows, node_rows, summary = fibril_morphometry(mask, px_size_um=px_size_um)
    if not seg_rows:
        napari_show_info("Fibril morphometry: no segments found.")
        return

    napari_show_info(
        f"── Fibril morphometry ──────────────────────\n"
        f"Segments         : {summary['n_segments']}\n"
        f"Junctions        : {summary['n_junctions']}\n"
        f"Endpoints        : {summary['n_endpoints']}\n"
        f"Total length     : {summary['total_length_um']:.1f} µm\n"
        f"Mean tortuosity  : {summary['mean_tortuosity']:.3f}  (1=straight)\n"
        f"Mean persistence : {summary['mean_persistence_length_um']:.2f} µm\n"
        f"Mean mesh size   : {summary['mesh_size_um2']:.1f} µm²"
        if np.isfinite(summary['mesh_size_um2'])
        else f"Mean mesh size   : (no loops detected)")


# ---------------------------------------------------------------------------
# 3.  Before/after registration
# ---------------------------------------------------------------------------
def register_images(reference, moving):
    """
    Subpixel image registration: find the translation that aligns ``moving`` to
    ``reference`` using phase cross-correlation (Guizar-Sicairos 2008, the same
    algorithm as BlebQuant's ``dftregistration.m``).

    Parameters
    ----------
    reference, moving : 2D ndarray

    Returns
    -------
    shift : ndarray, shape (2,)
        (row, col) shift to apply to ``moving``.
    registered : 2D ndarray
        ``moving`` shifted to align with ``reference``.
    error : float
        Normalised RMS registration error (lower = better).
    """
    from skimage.registration import phase_cross_correlation
    from scipy.ndimage import shift as nd_shift

    ref = np.asarray(reference, float)
    mov = np.asarray(moving, float)
    detected_shift, error, _ = phase_cross_correlation(ref, mov, upsample_factor=10)
    registered = nd_shift(mov, detected_shift)
    return detected_shift, registered, float(error)


def run_fibril_registration(ref_layer, moving_layer, viewer):
    """UI runner: register moving to reference, add registered + difference layers."""
    if ref_layer is None or moving_layer is None:
        napari_show_warning("Fibril registration: select both reference and moving layers.")
        return
    ref  = np.asarray(ref_layer.data,   float)
    mov  = np.asarray(moving_layer.data, float)
    if ref.ndim > 2:
        s = tuple(int(i) for i in viewer.dims.current_step[:ref.ndim-2])
        ref = ref[s]
    if mov.ndim > 2:
        s = tuple(int(i) for i in viewer.dims.current_step[:mov.ndim-2])
        mov = mov[s]
    if ref.shape != mov.shape:
        napari_show_warning("Fibril registration: images must have the same shape.")
        return

    shift, registered, error = register_images(ref, mov)
    diff = registered - ref
    viewer.add_image(registered, name=f"Registered ({moving_layer.name})",
                     colormap='green', blending='additive', opacity=0.7)
    viewer.add_image(diff, name=f"Difference ({moving_layer.name} − ref)",
                     colormap='bop orange', blending='additive', opacity=0.7)
    napari_show_info(
        f"── Fibril registration ─────────────────────\n"
        f"Shift applied    : ({shift[0]:.2f}, {shift[1]:.2f}) px (row, col)\n"
        f"Registration err : {error:.4f}  (lower = better alignment)\n"
        "Registered layer and difference map added.")


# ---------------------------------------------------------------------------
# 4.  Crossing node map + graph theory
# ---------------------------------------------------------------------------
def fibril_graph_analysis(binary_mask, px_size_um=1.0):
    """
    Extract crossing-node positions and compute graph-theoretic metrics.

    Returns
    -------
    junction_coords : ndarray, shape (J, 2)
        (row, col) of junction pixels in the skeleton.
    endpoint_coords : ndarray, shape (E, 2)
    metrics : dict
        n_nodes, n_edges, n_junctions, n_endpoints, density, mean_degree,
        degree_distribution (dict), n_connected_components,
        largest_component_fraction, mean_betweenness_centrality,
        mean_closeness_centrality, n_cycles, mean_cycle_length.
    G : nx.Graph
    skel : 2D bool array
    """
    G, skel, dt = build_skeleton_graph(np.asarray(binary_mask, bool))
    if G.number_of_nodes() == 0:
        return np.zeros((0,2)), np.zeros((0,2)), {}, G, skel

    degrees = dict(G.degree())
    junction_nodes = [n for n, d in degrees.items() if d > 2]
    endpoint_nodes = [n for n, d in degrees.items() if d == 1]
    junction_coords = np.array([G.nodes[n]['pos'] for n in junction_nodes]) \
        if junction_nodes else np.zeros((0, 2))
    endpoint_coords = np.array([G.nodes[n]['pos'] for n in endpoint_nodes]) \
        if endpoint_nodes else np.zeros((0, 2))

    # Degree distribution
    deg_vals = list(degrees.values())
    unique_degs = sorted(set(deg_vals))
    deg_dist = {d: deg_vals.count(d) for d in unique_degs}

    # Connected components
    comps = list(nx.connected_components(G))
    n_nodes = G.number_of_nodes()
    largest_frac = max(len(c) for c in comps) / n_nodes if n_nodes else 0.0

    # Betweenness / closeness on junctions (subset — full graph can be large)
    bc_vals, cc_vals = [], []
    try:
        bc = nx.betweenness_centrality(G, normalized=True)
        cc_cent = nx.closeness_centrality(G)
        bc_vals = list(bc.values())
        cc_vals = list(cc_cent.values())
    except Exception:
        pass

    # Cycle basis
    cycles = []
    try:
        cycles = nx.cycle_basis(G)
    except Exception:
        pass

    metrics = {
        'n_nodes': n_nodes,
        'n_edges': G.number_of_edges(),
        'n_junctions': len(junction_nodes),
        'n_endpoints': len(endpoint_nodes),
        'mean_degree': float(np.mean(deg_vals)) if deg_vals else 0.0,
        'degree_distribution': deg_dist,
        'n_connected_components': len(comps),
        'largest_component_fraction': float(largest_frac),
        'mean_betweenness_centrality': float(np.mean(bc_vals)) if bc_vals else np.nan,
        'mean_closeness_centrality': float(np.mean(cc_vals)) if cc_vals else np.nan,
        'n_cycles': len(cycles),
        'mean_cycle_length_nodes': float(np.mean([len(c) for c in cycles])) if cycles else np.nan,
    }
    return junction_coords, endpoint_coords, metrics, G, skel


def run_fibril_graph(mask_layer, px_size_um, viewer):
    """UI runner: build crossing-node map and report graph metrics."""
    if mask_layer is None:
        napari_show_warning("Fibril graph: select a binary fibril mask.")
        return
    mask = np.asarray(mask_layer.data, bool)
    if mask.ndim > 2:
        mask = mask[tuple(int(i) for i in viewer.dims.current_step[:mask.ndim-2])]

    j_coords, e_coords, metrics, G, skel = fibril_graph_analysis(mask, px_size_um)
    if G.number_of_nodes() == 0:
        napari_show_info("Fibril graph: no skeleton found.")
        return

    viewer.add_image(skel.astype(np.uint8),
                     name=f"Skeleton ({mask_layer.name})",
                     colormap='green', opacity=0.6, blending='additive')
    if len(j_coords):
        viewer.add_points(j_coords, name=f"Crossings ({mask_layer.name})",
                          size=8, face_color='red', edge_color='white',
                          symbol='cross')
    if len(e_coords):
        viewer.add_points(e_coords, name=f"Endpoints ({mask_layer.name})",
                          size=6, face_color='cyan', edge_color='white',
                          symbol='disc')

    deg_str = '  '.join(f"deg{d}:{n}" for d, n in
                        sorted(metrics['degree_distribution'].items()))
    napari_show_info(
        f"── Fibril graph analysis ───────────────────\n"
        f"Nodes / edges    : {metrics['n_nodes']} / {metrics['n_edges']}\n"
        f"Junctions        : {metrics['n_junctions']}\n"
        f"Endpoints        : {metrics['n_endpoints']}\n"
        f"Mean degree      : {metrics['mean_degree']:.2f}\n"
        f"Degree dist.     : {deg_str}\n"
        f"Connected comps  : {metrics['n_connected_components']}  "
        f"(largest = {metrics['largest_component_fraction']*100:.0f}% of nodes)\n"
        f"Cycles (loops)   : {metrics['n_cycles']}\n"
        f"Mean betweenness : {metrics['mean_betweenness_centrality']:.4f}\n"
        f"Crossing nodes are shown as red crosses; endpoints as cyan discs.")
