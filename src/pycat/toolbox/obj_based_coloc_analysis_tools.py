"""Object-based colocalization — a thin RE-EXPORT SHIM over `coloc/object_based.py` (coloc_decomposition).

Per-object Manders/Jaccard/Dice/centroid-distance colocalization and its Qt method-picker were moved into
the `coloc/` package (the last of the colocalization decomposition). Every public name is re-exported here,
so callers importing from `obj_based_coloc_analysis_tools` are unchanged."""
from pycat.toolbox.coloc.object_based import (  # noqa: F401  (re-export shim)
    manders_coloc,
    run_manders_coloc,
    manders_m1_calculation,
    manders_m2_calculation,
    jaccard_index_calculation,
    sorensen_dice_coefficient_calculation,
    calculate_centroid_distance,
    process_single_pairs,
    process_multiple_pairs,
    categorize_pairings,
    object_based_distance_analysis,
    object_based_colocalization_analysis,
    process_obca_methods,
    run_obca,
    coloc_significance)
