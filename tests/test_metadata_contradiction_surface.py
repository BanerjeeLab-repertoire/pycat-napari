"""**Surfacing contradictions on real loaded metadata: a report, a warning glyph, a concrete tooltip.**

tag_confidence Part 3. The Qt-free engine (`detect_contradictions`) is now driven by loaded `{common, raw}`
metadata — immersion/medium/RI come from the `raw['instrument']` block the deep-metadata OME parse adds — and
turned into what the metadata button shows: a WARNING glyph (distinct from the field_status step-status red)
and a concrete tooltip only when a CRITICAL contradiction is present, the neutral info glyph otherwise. A clean
file surfaces nothing (the cry-wolf contract, end to end); a marked-expected pattern is demoted. The installer
is duck-typed, so the button/viewer wiring is tested here with fakes — no Qt.
"""
import pytest

from pycat.utils.user_settings import UserSettings
from pycat.utils.metadata_contradictions import (
    ContradictionReport, contradiction_report, indicator_label, report_tooltip,
    install_metadata_indicator, acquisition_fingerprint, mark_expected,
    _METADATA_LABEL, _METADATA_WARN_LABEL)

pytestmark = pytest.mark.core


def _oil_air_metadata():
    # a loaded {common, raw} where the OME instrument block says Oil objective but Air medium (RI 1.518 = oil)
    return {'common': {'objective': '63x', 'software': 'ZEN', 'microscope': 'Zeiss'},
            'raw': {'instrument': {'immersion': 'Oil', 'medium': 'Air', 'refractive_index': 1.518}}}


def _clean_metadata():
    return {'common': {'objective': '63x', 'modality': 'Fluorescence'},
            'raw': {'instrument': {'immersion': 'Oil', 'medium': 'Oil', 'refractive_index': 1.518}}}


def _store(tmp_path):
    return UserSettings(path=tmp_path / 's.json')


# ── the report over loaded metadata ─────────────────────────────────────────────────────────────────

def test_a_critical_contradiction_in_the_instrument_block_produces_a_critical_report():
    report = contradiction_report(_oil_air_metadata())
    assert report.is_critical
    assert len(report.contradictions) == 1
    assert report.contradictions[0].pattern == 'immersion_vs_medium'
    assert 'RI 1.518 indicates oil' in report.contradictions[0].message


def test_a_clean_file_surfaces_nothing_the_end_to_end_cry_wolf_test():
    report = contradiction_report(_clean_metadata())
    assert report == ContradictionReport(contradictions=(), fingerprint=report.fingerprint, is_critical=False)
    assert not report.is_critical and report.contradictions == ()
    # absent metadata is also clean
    assert not contradiction_report({}).is_critical
    assert not contradiction_report(None).is_critical


def test_marking_the_pattern_expected_demotes_the_report_below_critical(tmp_path):
    store = _store(tmp_path)
    md = _oil_air_metadata()
    assert contradiction_report(md, store=store).is_critical           # critical before marking
    fp = acquisition_fingerprint({'instrument': 'Zeiss', 'software': 'ZEN', 'objective': '63x'})
    mark_expected('immersion_vs_medium', fp, store)
    demoted = contradiction_report(md, store=store)
    assert not demoted.is_critical                                     # demoted → no warning glyph
    assert demoted.contradictions and 'expected for this instrument' in demoted.contradictions[0].message


# ── presentation: label glyph + concrete tooltip ─────────────────────────────────────────────────────

def test_the_label_shows_a_warning_glyph_only_when_critical():
    assert indicator_label(contradiction_report(_oil_air_metadata())) == _METADATA_WARN_LABEL
    assert indicator_label(contradiction_report(_clean_metadata())) == _METADATA_LABEL


def test_the_tooltip_names_the_contradiction_concretely_and_is_clean_when_none():
    warn = report_tooltip(contradiction_report(_oil_air_metadata()))
    assert 'Oil immersion' in warn and 'Air medium' in warn        # concrete, not "there are contradictions"
    assert report_tooltip(contradiction_report(_clean_metadata())) == "Acquisition metadata for the loaded file."


# ── the duck-typed installer (Qt-free with fakes) ────────────────────────────────────────────────────

class _FakeAction:
    def __init__(self):
        self.text = None
        self.tooltip = None

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        self.tooltip = t


class _FakeEvent:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def fire(self):
        for cb in list(self._cbs):
            cb(None)


class _FakeViewer:
    def __init__(self):
        self.layers = type('L', (), {'events': type('E', (), {'inserted': _FakeEvent()})()})()


def test_the_installer_styles_the_button_and_refreshes_on_layer_insert(tmp_path):
    action = _FakeAction()
    viewer = _FakeViewer()
    state = {'md': _clean_metadata()}                                  # starts clean

    refresh = install_metadata_indicator(action, viewer, store=_store(tmp_path),
                                         get_metadata=lambda: state['md'])
    # initial refresh ran on install → neutral
    assert action.text == _METADATA_LABEL

    # a new file with a critical contradiction loads, then inserts a layer → the indicator turns to warning
    state['md'] = _oil_air_metadata()
    viewer.layers.events.inserted.fire()
    assert action.text == _METADATA_WARN_LABEL
    assert 'Oil immersion' in action.tooltip


def test_the_installer_never_raises_on_a_bad_metadata_getter(tmp_path):
    action = _FakeAction()
    viewer = _FakeViewer()

    def _boom():
        raise RuntimeError("data repository gone")

    # must not propagate — the toolbar button must keep working
    install_metadata_indicator(action, viewer, store=_store(tmp_path), get_metadata=_boom)
