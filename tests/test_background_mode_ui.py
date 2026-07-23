"""**The background-mode selector is wired end-to-end (backlog: background_mode Part A).**

The science — the three background modes, the guardrail (`assess_background_region`), the mode/source
travelling with the result, and the ontology caveat — is pinned Qt-free in `test_background_mode`. This
pins that the Client-Enrichment UI now EXPOSES the signal-free-region mode: picking a region that is really
dilute phase fires the consequence-stating guardrail, and the chosen background mode rides in the emitted
overview table. Integration-marked (skips headless).
"""
import numpy as np
import pytest


@pytest.mark.integration
def test_the_region_mode_fires_the_guardrail_and_records_the_mode(qtbot, monkeypatch):
    from PyQt5.QtWidgets import QComboBox, QPushButton, QVBoxLayout
    import napari
    import pycat.toolbox.partition_enrichment_tools as pet

    warnings = []
    captured = {}
    # the guardrail inside client_enrichment uses the module-level name; the UI imports show_* locally
    monkeypatch.setattr(pet, 'napari_show_warning', lambda m: warnings.append(m))
    monkeypatch.setattr('napari.utils.notifications.show_warning', lambda m: warnings.append(m))
    monkeypatch.setattr('napari.utils.notifications.show_info', lambda m: None)
    monkeypatch.setattr('pycat.toolbox.analysis_plots.plot_enrichment_distribution',
                        lambda *a, **k: None)
    monkeypatch.setattr('pycat.ui.ui_utils.show_dataframes_dialog',
                        lambda title, tables: captured.update(tables=tables))

    # A client image: bright condensate, uniform dilute ~100. The "background" region is drawn on the
    # dilute phase (also ~100) — the mistake the guardrail exists to catch.
    client = np.full((20, 20), 100.0, dtype=float)
    client[5:10, 5:10] = 600.0
    dense = np.zeros((20, 20), dtype=int); dense[5:10, 5:10] = 1
    region = np.zeros((20, 20), dtype=int); region[0:4, 0:4] = 1     # inside the dilute phase (~100)

    class _Layer:
        def __init__(self, name, data): self.name, self.data = name, data

    class _Layers(list):
        def __getitem__(self, k):
            if isinstance(k, str):
                return next(l for l in self if l.name == k)
            return list.__getitem__(self, k)

    layers = _Layers([_Layer('client', client), _Layer('dense', dense), _Layer('bg_region', region)])

    class _Viewer:
        def __init__(self): self.layers = layers

    class _DR(dict):
        pass

    class _Stub:
        def __init__(self):
            self.viewer = _Viewer()
            self.central_manager = type('CM', (), {
                'active_data_class': type('ADC', (), {'data_repository': _DR()})()})()
        def create_layer_dropdown(self, ltype):
            dd = QComboBox(); dd.addItems(['None', 'client', 'dense', 'bg_region']); return dd
        def _record(self, *a, **k): pass

    from PyQt5.QtWidgets import QGroupBox
    stub = _Stub()
    root = QVBoxLayout()
    pet._add_client_enrichment(stub, layout=root, separate_widget=False)

    grp = next(root.itemAt(i).widget() for i in range(root.count())
               if isinstance(root.itemAt(i).widget(), QGroupBox))
    combos = grp.findChildren(QComboBox)           # order: client, condensate, cell, background-region
    combos[0].setCurrentText('client')
    combos[1].setCurrentText('dense')
    combos[2].setCurrentText('None')
    combos[3].setCurrentText('bg_region')
    run = next(b for b in grp.findChildren(QPushButton) if 'Enrichment' in b.text())
    run.click()

    assert any('DESTROY' in w or 'dilute phase' in w.lower() for w in warnings), \
        "picking a dilute-phase region as background must fire the consequence-stating guardrail"
    overview = captured['tables'][0][1]                       # ('Overview', df)
    assert overview.iloc[0]['background mode'] == 'region', "the chosen background mode must ride in the table"
