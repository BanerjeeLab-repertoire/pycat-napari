"""Pixel-wise colocalization — a thin RE-EXPORT SHIM over the `coloc/` package (coloc_decomposition).

The module was decomposed by domain into `coloc/metrics.py` (raw measures), `thresholding.py`, `nulls.py`
(spatial null + randomisation), `temporal.py` (the time trace), and `analysis.py` (the orchestration,
`pwcaDialog`, and the visualizers). Every public name is re-exported here, so callers importing from
`pixel_wise_corr_analysis_tools` are unchanged."""


# ── raw pairwise metrics moved to coloc/metrics.py (coloc_decomposition) — re-exported here ─────────
from pycat.toolbox.coloc.metrics import (  # noqa: F401,E402  (re-export shim)
    pearsons_correlation, manders_overlap, manders_k1_calculation, manders_k2_calculation,
    spearman_r_calculation, kendall_tau_calculation, weighted_tau_calculation,
    li_intensity_correlation)


# ── Costes/Manders thresholding moved to coloc/thresholding.py (coloc_decomposition) — re-exported ──
from pycat.toolbox.coloc.thresholding import (  # noqa: F401,E402  (re-export shim)
    manders_threshold_sensitivity, costes_linear_model, costes_thresholding)


# ── spatial-null + randomisation machinery moved to coloc/nulls.py (coloc_decomposition) — re-exported ─
from pycat.toolbox.coloc.nulls import (  # noqa: F401,E402  (re-export shim)
    spatial_correlation_length, _block_shuffle, spatial_null_test, scramble_blocks,
    scramble_pixels, scramble_pixels_within_mask, perform_costes_test)


# ── temporal coloc trace + its plots moved to coloc/temporal.py (coloc_decomposition) — re-exported ──
from pycat.toolbox.coloc.temporal import (  # noqa: F401,E402  (re-export shim)
    coloc_time_trace, plot_per_cell_coloc_time_trace, plot_coloc_time_trace)

# ── orchestration + visualizers + the Qt dialog moved to coloc/analysis.py — re-exported ─────────────
from pycat.toolbox.coloc.analysis import (  # noqa: F401,E402  (re-export shim)
    li_ica_histogram, li_ica_plot, cytofluorogram_plot, cross_correlation_matrix,
    pixel_wise_correlation_analysis, process_pwcca_methods, run_pwcca, pwcaDialog,
    LAST_COLOC_DIAGNOSTICS)
