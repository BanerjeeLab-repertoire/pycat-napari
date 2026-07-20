"""**The measurement ontology stays honest — units match the code, no aspirational or orphan entries.**

An ontology that drifts from what the code emits is worse than none (it would put a wrong equation in a
Methods section). So three guards: the units it states must match the units the code actually emits; every
key must correspond to a real emitted measurement (no aspirational entries); and any entry that cites a
reference must carry the equation that reference supports.
"""
import pathlib

import pytest

from pycat.utils.measurement_ontology import MEASUREMENTS, describe, units_for

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'pycat'


def test_every_emitted_key_appears_in_the_codebase():
    """No aspirational entries: every ``emitted=True`` key must appear as a real name in the source (an
    emitted column or a Parameter), so the registry cannot fill with measurements the code never produces."""
    src_text = "\n".join(p.read_text(encoding='utf-8', errors='ignore')
                         for p in _SRC.rglob('*.py'))
    missing = [k for k, m in MEASUREMENTS.items()
               if m.emitted and k not in src_text]
    assert not missing, (
        f"these ontology keys are marked emitted but appear nowhere in src/pycat: {missing} — either the "
        "key is wrong (use the EMITTED column name) or mark it emitted=False if it is reported-only.")


def test_a_reference_without_an_equation_is_an_orphan_claim():
    """A citation without the formula it supports is decoration — and a hazard in a Methods context."""
    orphans = [k for k, m in MEASUREMENTS.items() if m.reference and not (m.equation or '').strip()]
    assert not orphans, f"these entries cite a reference but carry no equation: {orphans}"


def test_units_agree_with_what_the_code_emits():
    """The load-bearing guard. For a measurement emitted as a ``Parameter``, the ontology's units must
    match the units the code produces — construct the emitter and compare. ``delta_g_transfer`` is the
    clean case (a pure Parameter-returning function); a mismatch is a bug the test names."""
    from pycat.utils.calibration import delta_g_transfer
    param = delta_g_transfer(10.0, 1.0, 298.0)                 # C_dense=10, C_dilute=1, T=298 K → a Parameter
    assert getattr(param, 'units', None) == units_for('delta_g_transfer'), (
        f"delta_g_transfer emits units {param.units!r} but the ontology says "
        f"{units_for('delta_g_transfer')!r} — one of them is wrong.")


def test_every_entry_is_well_formed():
    """Guard the guard: each entry has a non-empty definition, equation, and units — the registry cannot
    carry a blank claim."""
    blank = [k for k, m in MEASUREMENTS.items()
             if not (m.definition and m.equation and m.units)]
    assert not blank, f"these ontology entries are missing a definition/equation/units: {blank}"
    assert len(MEASUREMENTS) >= 8                              # a real seed, not a stub


def test_describe_and_units_for_round_trip():
    m = describe('viscosity')
    assert m is not None and m.units == 'Pa·s' and units_for('viscosity') == 'Pa·s'
    assert 'Stokes' in (m.reference or '')                     # the one sourced reference
    assert describe('not_a_real_measurement') is None
