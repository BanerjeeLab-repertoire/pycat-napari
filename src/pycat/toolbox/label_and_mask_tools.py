"""
Labeled Mask and Binary Mask Module for PyCAT — re-export shim.

This module's implementations were decomposed into a focused ``toolbox/masks/`` package plus the condensate
wetting physics (``label_mask_split``); the module is now a thin shim that re-exports every public name so all
existing callers (``from pycat.toolbox.label_and_mask_tools import ...``) keep working unchanged. The homes:

- ``masks/labels.py``      — label-editing ops (update/convert/label/expand/mask-logic-merge)
- ``masks/morphology.py``  — binary morphological ops, structuring elements, the watershed split, contour filter
- ``masks/splitting.py``   — the assess-whether-to-split decision path
- ``masks/measurement.py`` — region-property / binary-mask measurement + the property-picker dialog
- ``condensate_physics/wetting.py`` — neck geometry + elastocapillary length (fusion/wetting physics)

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

from pycat.toolbox.masks.labels import (  # noqa: F401  (re-export shim — label_mask_split Step 6)
    _napari, run_update_labels, run_convert_labels_to_mask, run_label_binary_mask, run_expand_labels,
    run_mask_logic_merge)

from pycat.toolbox.masks.morphology import (  # noqa: F401  (re-export shim — label_mask_split Step 4)
    generate_cross_structuring_element, extend_mask_to_edges, custom_binary_opening,
    custom_binary_closing, binary_morph_operation, run_binary_morph_operation, opencv_contour_func,
    split_touching_objects)

from pycat.toolbox.masks.measurement import (  # noqa: F401  (re-export shim — label_mask_split Step 3)
    MeasurementDialog, measure_region_props, run_measure_binary_mask, run_measure_region_props)

from pycat.toolbox.masks.splitting import (  # noqa: F401  (re-export shim — label_mask_split Step 5)
    assess_and_split_touching)

from pycat.toolbox.condensate_physics.wetting import (  # noqa: F401  (re-export shim — label_mask_split Step 2)
    neck_geometry, fit_elastocapillary_length)
