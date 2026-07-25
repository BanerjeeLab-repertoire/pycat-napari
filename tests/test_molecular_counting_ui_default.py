"""**The molecular-counting GUI reinstated the selection-effect bug the library had already fixed.**

`count_molecules_pooled` defaults to `r2_min=0.0` (keep all traces) with a documented danger note that a
0.999 gate inflated a true mean of 44 to 77 by selecting for bright traces. But the widget builder
overrode the spinbox back to 0.999, so through the GUI the selection effect was live again.

This pins the default: the panel's R² spinbox starts at 0.0 (keep all traces). Integration-marked
(needs Qt; skips headless).
"""
import pytest


@pytest.mark.integration
def test_molecular_counting_panel_default_r2_is_zero(qtbot):
    from PyQt5.QtWidgets import QComboBox
    import pycat.toolbox.molecular_counting_tools as mct

    class _UI:
        def create_layer_dropdown(self, kind):
            return QComboBox()

    (_grp, _stack_dd, _mask_dd, _fast_spin, r2_spin,
     _prog, _btn) = mct._build_molecular_counting_panel(_UI())

    # DEFAULT 0.0 -- no brightness selection effect unless the user deliberately raises it.
    assert r2_spin.value() == 0.0
    assert r2_spin.singleStep() == pytest.approx(0.05)
    # the tooltip must state the selection effect (anti-black-box)
    assert "bias" in r2_spin.toolTip().lower() or "bright" in r2_spin.toolTip().lower()
