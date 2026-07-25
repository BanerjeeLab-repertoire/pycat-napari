"""Acquisition data-quality checks — thin re-export shim (data_qc_decomposition).

The checks now live in the `toolbox/data_qc/` package, one module per family; presentation is in
`report.py` and orchestration in `runner.py`. This module re-exports the full historical surface so every
`pycat.toolbox.data_qc_tools.<name>` caller (the QC dashboard, gallery, navigator quality-gate and the
reliability index) keeps working unchanged.
"""
from __future__ import annotations

# shared low-level primitives
from pycat.toolbox.data_qc._base import (  # noqa: F401
    _to_float, _robust_noise_std, _dtype_max, _mean_frame, _not_applicable, _shift_normalise)
# check families
from pycat.toolbox.data_qc.exposure import qc_saturation  # noqa: F401
from pycat.toolbox.data_qc.focus import (  # noqa: F401
    edge_width_px, diffraction_limit_px, qc_focus, _qc_focus_stack, _qc_focus_absolute)
from pycat.toolbox.data_qc.noise import qc_snr  # noqa: F401
from pycat.toolbox.data_qc.sampling import qc_nyquist, qc_time_sampling  # noqa: F401
from pycat.toolbox.data_qc.illumination import qc_vignetting, qc_ghosting  # noqa: F401
from pycat.toolbox.data_qc.aberration import qc_spherical_aberration, qc_chromatic  # noqa: F401
from pycat.toolbox.data_qc.stability import qc_photobleaching, qc_drift, qc_vibration  # noqa: F401
from pycat.toolbox.data_qc.biological import qc_biological_objects  # noqa: F401
# presentation + orchestration
from pycat.toolbox.data_qc.report import plot_qc_report  # noqa: F401
from pycat.toolbox.data_qc.runner import run_full_qc  # noqa: F401
