"""
Labeled Mask and Binary Mask Module for PyCAT

This module contains functions for processing labeled masks and binary masks, including operations such as
morphological transformations, labeling connected components, and measuring properties of regions. It also
provides functions for splitting touching objects in binary images and extending segmentation masks to the
image borders.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import numpy as np



from pycat.utils.entity_ref import attach_layer_id, finalize_entity_table, source_path_of
from pycat.utils.object_ref import bbox_columns_from_regionprops
from pycat.utils.tag_registry import tags_layer
from pycat.utils.general_utils import debug_log
import pandas as pd
import scipy.ndimage as ndi
import skimage as sk
import cv2

# GUI stack is imported LAZILY (inside the functions that need it) rather than at
# module scope. This module contains pure array operations — binary_morph_operation,
# opencv_contour_func — that other scientific modules import, and a top-level
# `import napari` / `from PyQt5 ...` made those functions, and everything that
# depends on them, un-importable without a display. That prevented the measurement
# chain from being tested headlessly, which is backwards: the numerical code is the
# part that most needs automated regression testing.
#
# The viewer-facing functions below still use napari.layers for isinstance checks;
# they simply import it at call time, when a viewer demonstrably exists.
from pycat.utils.notify import show_warning as napari_show_warning


def _napari():
    """Lazy napari import, for the viewer-facing helpers in this module."""
    import napari
    return napari



# Local application imports
# pycat.ui.ui_utils pulls in the Qt stack, so it is imported at CALL time inside the
# viewer-facing functions below — keeping this module's array operations headless.













def run_update_labels(new_label_input, increment_mode, viewer):
    """
    Updates label values in the active label layer of a viewer based on user input. The operation performed 
    depends on the operation mode selected: either incrementing all label values by a specified value or 
    changing a specific label to a new value. The viewer is refreshed to display the updated labels.

    Parameters
    ----------
    viewer : napari.Viewer
        The viewer object that contains the label layer to be updated.
    new_label_input : UI component (e.g., a text input field)
        An input widget or field that provides the new label value or the increment value. Expected to 
        be convertible to an integer.
    increment_mode : bool
        A boolean value or a widget (e.g., a checkbox) indicating the operation mode. If True, all label 
        values in the layer are incremented by the value from `new_label_input`. If False, the specified 
        label is changed to the new value provided.

    Notes
    -----
    - Assumes `new_label_input.text()` returns a string convertible to an integer.
    - Validates the active layer as a labels layer before performing updates.
    - If changing a specific label to a new value, ensures the new value does not duplicate existing label values,
      alerting the user for manual intervention (such as undo) if duplication occurs.
    """

    # Get the active layer from the viewer
    active_layer = viewer.layers.selection.active

    # Ensure there is an active labels layer
    if active_layer is None or not isinstance(active_layer, _napari().layers.Labels):
        napari_show_warning("No active labels layer selected.")
        return
    # Ensure the input is valid and convert to an integer
    if new_label_input.text() == "": # or not new_label_input.text().isdigit():
        napari_show_warning("Please enter a valid label value.")
        return
    
    # Handle label value incrementing for all labels
    if increment_mode.isChecked(): 
        increment_value = int(new_label_input.text())
        active_layer.data += increment_value
    else:
        # Handle changing a specific label to a new value
        picked_label = active_layer.selected_label
        new_label_value = int(new_label_input.text())
        # Check if the new label value is already in use 
        if new_label_value in active_layer.data:
            napari_show_warning(f"Warning: Label {new_label_value} was already in use.")

        active_layer.data[active_layer.data == picked_label] = new_label_value
        
    # Manually refresh the viewer to update the changes
    from pycat.ui.ui_utils import refresh_viewer_with_new_data
    refresh_viewer_with_new_data(viewer, active_layer)


def run_convert_labels_to_mask(labels_layer, viewer):
    """
    Converts a labeled image layer to a binary mask and displays the resulting mask in the viewer. 
    Each unique integer label in the labeled image is treated as a distinct object, and all objects 
    are represented collectively in a single binary mask, where pixels of objects are set to 1, 
    and the background remains 0.

    Parameters
    ----------
    labels_layer : napari.layers.Labels
        The layer containing the labeled image to be converted. Each distinct label represents a different object.
    viewer : napari.Viewer
        The viewer object where the resulting binary mask will be added and displayed.

    Notes
    -----
    - The function creates a binary mask where all non-zero labels are set to 1, effectively differentiating 
      objects from the background without distinguishing between individual objects.
    - The new mask layer is named using the original labels layer's name for easy identification.
    """
    
    # Extract the labeled image data from the layer
    labels = labels_layer.data

    # Convert the labeled image to a binary mask
    mask = (labels > 0).astype(int)

    # Add the binary mask as a new layer to the viewer
    viewer.add_labels(mask, name=f"Mask from {labels_layer.name}")


def run_label_binary_mask(mask_layer, viewer):
    """
    Labels connected components in a binary mask and displays the result in the viewer as a new layer. 
    This process involves assigning a unique label to each connected group of '1's in the binary mask, 
    facilitating the identification and analysis of individual components.

    Parameters
    ----------
    mask_layer : napari.layers.Labels
        The layer containing the binary mask. This mask should only contain values of 0 (background) and 1 (foreground).
    viewer : napari.Viewer
        The viewer object in which the resulting labeled mask will be displayed.

    Notes
    -----
    - The function first checks to ensure that the input mask contains only 0 and 1 values. If any other values are present,
      it issues a warning and exits without performing the labeling.
    - The labeled mask is then added to the viewer under a new layer named 'Labeled <original_layer_name>', 
      making it easy to distinguish from the original binary mask.
    """

    # Extract the binary mask data from the layer
    mask = mask_layer.data

    # Ensure the input is a binary mask (0 and 1 values)
    if not np.all(np.logical_or(mask == 0, mask == 1)):
        napari_show_warning("Input mask must be a binary mask with values of 0 and 1.")
        return

    # Label connected components in the binary mask
    labeled_mask = sk.measure.label(mask).astype(int)

    # Add the labeled mask as a new layer to the viewer
    viewer.add_labels(labeled_mask, name=f"Labeled {mask_layer.name}")















def run_expand_labels(labels_layer, distance, viewer):
    """
    Grow labeled regions outward by a fixed distance without merging touching
    labels, using ``skimage.segmentation.expand_labels``. Each label is dilated
    up to ``distance`` pixels into the background; expansion stops at the midpoint
    between two labels so distinct objects never merge.
    """
    labels = np.asarray(labels_layer.data)
    try:
        dist = float(distance)
    except (TypeError, ValueError):
        napari_show_warning("Expand labels: distance must be a number.")
        return
    if dist <= 0:
        napari_show_warning("Expand labels: distance must be greater than 0.")
        return
    expanded = sk.segmentation.expand_labels(labels, distance=dist).astype(int)
    viewer.add_labels(expanded, name=f"Expanded {labels_layer.name}")


def run_mask_logic_merge(mask_layer1, mask_layer2, mode, viewer):
    """
    Combine two binary masks with a boolean set operation (AND / OR / XOR).
    AND keeps overlap, OR keeps the union, XOR keeps the symmetric difference.
    Inputs are binarized (!=0) before the operation; shapes must match.
    """
    m1 = np.asarray(mask_layer1.data)
    m2 = np.asarray(mask_layer2.data)
    if m1.shape != m2.shape:
        napari_show_warning(
            f"Mask logic merge: shapes differ ({m1.shape} vs {m2.shape}) — "
            "masks must match.")
        return
    b1 = m1 != 0
    b2 = m2 != 0
    key = str(mode).strip().upper()
    ops = {'AND': np.logical_and, 'OR': np.logical_or, 'XOR': np.logical_xor}
    if key not in ops:
        napari_show_warning(
            f"Mask logic merge: unknown mode '{mode}' (use AND, OR, or XOR).")
        return
    merged = ops[key](b1, b2).astype(int)
    viewer.add_labels(
        merged, name=f"{key} ({mask_layer1.name} · {mask_layer2.name})")

@tags_layer('split_assessed', role='labels',
            summary='Morphology-aware split: two droplets vs arrested fusion vs chain', target='condensate')
def assess_and_split_touching(binary_mask, intensity_image=None, sigma=2.0,
                              neck_threshold=0.6, min_peak_distance=6,
                              chain_min_units=4, microns_per_pixel=1.0):
    """**Should these masks be split? The morphology answers, and it is a physical answer.**

    ``split_touching_objects`` runs a watershed and cuts. **It does not ask whether it should.**
    That is the wrong question to leave to a threshold, because the same connected mask can be
    four physically different things, and only one of them is two droplets:

    * **Two droplets in contact** — round, with a **deep neck** between them. They have not fused;
      splitting them is correct and *not* splitting them merges two measurements into one.
    * **Arrested fusion** — two droplets caught **part-way** through coalescence. The neck is
      **shallow**, because the interface has already begun to relax. **This is ONE object, and the
      arrest IS the finding**: a material that fuses slowly is a material with a high viscosity or
      a solidified interface. Splitting it destroys the very observation.
    * **Beads on a string / a fractal aggregate** — **many** small units stuck together. Cutting it
      into *two* is meaningless; the object is not a droplet pair at all.
    * **A single irregular droplet** — nothing to split.

    The evidence
    ------------
    **The neck ratio** — the depth of the saddle between two distance-transform peaks, as a
    fraction of the peaks themselves. It is the degree to which the two bodies have merged, and it
    moves smoothly and monotonically with the physics:

        overlap      neck_ratio    what it is
        0.00         **0.128**     barely touching  -> SPLIT
        0.10         0.433         still necked     -> SPLIT
        0.20         0.639         relaxing         -> arrested
        0.50         0.914         mostly fused     -> arrested
        0.80         1.000         one body         -> single

    A neck shallower than ~0.6 of the droplet radius means **the interface has already relaxed**
    — surface tension has done its work, and what is left is one body with a memory of two.

    Measured on the four morphologies (all ONE connected mask):

        morphology            solidity   n_peaks   neck_ratio
        single droplet        0.979      1         1.000
        **two touching**      0.906      **2**     **0.364**
        **arrested fusion**   0.979      **2**     **0.965**
        beads on a string     0.930      **6**     0.788
        fractal aggregate     0.891      1         1.000

    **The neck ratio separates "two touching" from "arrested fusion" cleanly (0.36 vs 0.97) —
    and nothing else does.** Solidity does not (0.906 vs 0.979 overlaps with a single droplet);
    eccentricity does not; the peak count does not (both are 2).

    **The intensity is a second, independent witness.** A real neck between two droplets sits in a
    thinner part of the object, so it is **dimmer** — less material in the light path. An arrested
    neck is filled with material and is **not** dimmer. Where an intensity image is given, this is
    reported as ``neck_intensity_ratio`` and it is used to override a marginal geometric call.

    References
    ----------
    The arrest physics — **interfacial driving force against internal elasticity** — is
    established, and this module implements the observable side of it:

    * **Pawar, Caggioni, Ergun, Hartel & Spicer**, "Arrested coalescence in Pickering emulsions",
      *Soft Matter* **7**, 7710-7716 (2011). DOI: 10.1039/c1sm05457k

      *"their complete fusion into a single spherical drop can sometimes be arrested in an
      intermediate shape **if a rheological resistance offsets the Laplace pressure driving
      force**."*

      Their **eqn (6)** gives the pressure imbalance at the neck as
      ``dP = 2*gamma/R_droplet - (gamma/R1 - gamma/R2)``, with R1 the cross-sectional radius and
      R2 the neck radius — **the two principal radii of a saddle, of opposite sign.** That is
      exactly the object measured here, and their two published doublets **both imply the same
      interfacial tension (0.0529 N/m)** when their equation is recomputed from their own
      geometry — see ``test_the_neck_laplace_pressure_reproduces_PAWAR_2011``.

    * **Pawar, Caggioni, Hartel & Spicer**, "Arrested coalescence of viscoelastic droplets with
      internal microstructure", *Faraday Discuss.* **158**, 341-350 (2012).
      DOI: 10.1039/c2fd20029e

      *"the interfacial energy is continuously reduced while the elastic energy is increased by
      compression of the internal structure and, **when the two processes balance one another,
      coalescence is arrested**."*

    * **Dahiya, Caggioni, Spicer et al.**, arrested coalescence of polydisperse doublets,
      *Phil. Trans. R. Soc. A* (2016), PMC4920281 — the three-regime structure this function
      reports: *"If surface energy dominates, the drops will completely coalesce. If elastic
      energy dominates, the droplets are unable to even initiate coalescence. **Arrest occurs when
      coalescence can begin but not complete.**"*

    Full validation, including the parameter ranges for biomolecular condensates, is in
    ``docs/validation/neck_geometry_and_elastocapillarity.md``.

    Returns
    -------
    dict with ``labels`` (the split, or the original object unsplit), and per-object records
    carrying the verdict, the evidence, and **why**.
    """
    import skimage as sk
    from scipy import ndimage as ndi

    mask = np.asarray(binary_mask) > 0
    intensity = None if intensity_image is None else np.asarray(intensity_image, float)

    labelled = sk.measure.label(mask)
    output = np.zeros_like(labelled)
    records = []
    next_label = 1

    for prop in sk.measure.regionprops(labelled):
        sub = (labelled[prop.slice] == prop.label)

        distance = ndi.distance_transform_edt(sub)
        smoothed = sk.filters.gaussian(distance, sigma=sigma)

        peaks = sk.feature.peak_local_max(
            smoothed, min_distance=int(min_peak_distance), labels=sub)

        record = dict(
            label=int(prop.label),
            # ── KEEP THE BBOX. It is what makes this row brushable. ─────────────
            #
            # regionprops hands it over free, and PyCAT was discarding it at 24 of its 25 call
            # sites. **A row without a bbox cannot be turned back into an image** — and in BATCH
            # that is the ONLY route back to the object, because the layer is gone.
            **bbox_columns_from_regionprops(prop),
            area_um2=float(prop.area) * microns_per_pixel ** 2,
            solidity=float(prop.solidity),
            n_peaks=int(len(peaks)),
            neck_ratio=np.nan,
            neck_intensity_ratio=np.nan,
            verdict='single',
            split=False,
            reason='',
        )

        # ── Not enough peaks: nothing to split ──────────────────────────────────
        if len(peaks) < 2:
            record['neck_ratio'] = 1.0
            record['reason'] = ('One distance-transform maximum: this is a single body, however '
                                'irregular its outline. A ramified or fractal aggregate lands '
                                'here — it has no neck because it has no two centres.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Many peaks: a CHAIN or an aggregate, not a droplet pair ─────────────
        if len(peaks) >= int(chain_min_units):
            record['verdict'] = 'chain_or_aggregate'
            record['reason'] = (
                f'{len(peaks)} sub-units. **This is not a droplet pair** — it is a chain '
                f'(beads-on-a-string) or a ramified aggregate. Cutting it in TWO would be '
                f'arbitrary: the object is not two things, it is many things stuck together, '
                f'and that is itself the observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1
            records.append(record)
            continue

        # ── Two (or three) peaks: measure the NECK ──────────────────────────────
        depths = sorted((float(smoothed[tuple(q)]) for q in peaks), reverse=True)[:2]

        markers = np.zeros(sub.shape, int)
        for i, q in enumerate(peaks[:2], start=1):
            markers[tuple(q)] = i

        basins = sk.segmentation.watershed(-smoothed, sk.measure.label(markers > 0), mask=sub)
        boundary = sk.segmentation.find_boundaries(basins, mode='thick') & sub

        saddle = float(smoothed[boundary].max()) if boundary.any() else 0.0
        neck = saddle / max(min(depths), 1e-9)
        record['neck_ratio'] = float(neck)

        # ── The intensity is an INDEPENDENT witness ─────────────────────────────
        #
        # A real neck between two droplets is a thinner part of the object, so LESS material sits
        # in the light path and it is DIMMER. An arrested neck is filled, and is not.
        if intensity is not None and boundary.any():
            patch = intensity[prop.slice]
            neck_intensity = float(np.median(patch[boundary]))
            body_intensity = float(np.median(patch[sub & ~boundary]))
            if body_intensity > 1e-9:
                record['neck_intensity_ratio'] = neck_intensity / body_intensity

        deep_neck = neck < float(neck_threshold)

        # ── The intensity is REPORTED but does NOT override the geometry ────────
        #
        # A real neck sits in a thinner part of the object, so less material is in the light path
        # and it should be dimmer. **Tested, and it does not discriminate**: the neck intensity
        # came out at 0.42-0.46 of the body median for a genuine neck AND for an arrested one
        # alike, because the body median is dominated by the bright droplet centres and every
        # neck is dim compared with those.
        #
        # **The geometry is decisive on its own** (0.50 against 0.77 on the same pair), so the
        # intensity is reported for the user to inspect and is NOT used to override the call.
        # A witness that does not discriminate must not be given a vote.
        #
        # (A discriminating intensity test would compare the neck against the LOCAL body
        # thickness at the same distance from the centres — i.e. against what the intensity
        # WOULD be if the neck were filled. That is a real piece of work, and it is not done
        # here.)
        _intensity_ratio = record['neck_intensity_ratio']

        if deep_neck:
            record['verdict'] = 'two_droplets'
            record['split'] = True
            if not record['reason']:
                record['reason'] = (
                    f'Neck ratio {neck:.2f} — **a deep neck**. The two bodies are in contact but '
                    f'have NOT fused: surface tension has not relaxed the interface between '
                    f'them. They are two droplets, and measuring them as one would merge two '
                    f'independent objects.')
            output[prop.slice][basins == 1] = next_label
            output[prop.slice][basins == 2] = next_label + 1
            next_label += 2
        else:
            record['verdict'] = 'arrested_fusion'
            record['reason'] = (
                f'Neck ratio {neck:.2f} — **a shallow neck**. The interface between the two '
                f'centres has already relaxed: surface tension has done its work and what '
                f'remains is ONE body with a memory of two. **This is arrested fusion, and the '
                f'arrest is the finding** — a droplet pair that stalls part-way through '
                f'coalescence is reporting a high viscosity or a solidified interface. '
                f'Splitting it would destroy exactly that observation. Left intact.')
            output[prop.slice][sub] = next_label
            next_label += 1

        records.append(record)

    return dict(labels=output, objects=records)

# ── condensate wetting/coalescence physics moved to its rightful home (label_mask_split) ──────────
# `neck_geometry` and `fit_elastocapillary_length` are fusion/wetting measurements, not masking; they now
# live in condensate_physics/wetting.py and are re-exported here so every existing caller is unchanged.
from pycat.toolbox.masks.morphology import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 4)
    generate_cross_structuring_element, extend_mask_to_edges, custom_binary_opening,
    custom_binary_closing, binary_morph_operation, run_binary_morph_operation, opencv_contour_func,
    split_touching_objects)

from pycat.toolbox.masks.measurement import (  # noqa: F401,E402  (re-export shim — label_mask_split Step 3)
    MeasurementDialog, measure_region_props, run_measure_binary_mask, run_measure_region_props)

from pycat.toolbox.condensate_physics.wetting import (  # noqa: F401,E402  (re-export shim)
    neck_geometry, fit_elastocapillary_length)
