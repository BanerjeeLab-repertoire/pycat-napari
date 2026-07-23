"""**Background mode: surface the three offset treatments, and guard the mistake the docstring warns about.**

`client_enrichment` already supports a scalar offset, a signal-free-region mask, and a local dilute shell —
but nothing surfaced the reasoning, so every GUI partition coefficient used `background=0.0`. The
scientifically load-bearing part is the **guardrail**: the dilute phase is NOT background, and a
well-intentioned user who selects "the region outside the condensate" as background would subtract the
denominator from itself and destroy the measurement. These tests pin: each mode produces the expected
offset; a background region that is really dilute phase triggers a consequence-stating warning while a
genuinely dark region does not; offset subtraction recovers K across a pedestal; and the mode/offset/source
travel with the result.
"""
import numpy as np
import pytest

from pycat.toolbox.partition_enrichment_tools import assess_background_region, client_enrichment

pytestmark = pytest.mark.base


def _scene(pedestal, k=5.0, size=128, dilute_signal=100.0):
    """A cell (dilute) with a dense condensate, on a dark field outside the cell, plus a camera pedestal.
    True K = k at any pedestal, provided the pedestal is removed."""
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((yy - size / 2) ** 2 + (xx - size / 2) ** 2)
    cell = d < 40
    dense = d < 12
    img = np.full((size, size), float(pedestal))
    img[cell] = pedestal + dilute_signal
    img[dense] = pedestal + k * dilute_signal

    dark_bg = np.zeros((size, size), bool); dark_bg[0:12, 0:12] = True        # a corner OUTSIDE the cell
    dilute_bg = (d > 28) & (d < 36)                                            # a patch INSIDE the dilute phase
    return img, dense, cell, dark_bg, dilute_bg


# ── Each mode produces the expected offset, and it travels with the result ──────────────────────
def test_the_three_background_modes_produce_the_expected_offset():
    img, dense, cell, dark_bg, _ = _scene(pedestal=100)

    none = client_enrichment(img, dense, cell_mask=cell)
    assert none['background'] == 0.0 and none['background_mode'] == 'none'

    scalar = client_enrichment(img, dense, cell_mask=cell, background=100.0)
    assert scalar['background'] == 100.0 and scalar['background_mode'] == 'scalar'

    region = client_enrichment(img, dense, cell_mask=cell, background_mask=dark_bg)
    assert region['background'] == pytest.approx(100.0) and region['background_mode'] == 'region'
    assert region['background_source'] == 'signal-free region mask (mean)'


def test_the_default_is_none_so_existing_behaviour_is_unchanged():
    img, dense, cell, _, _ = _scene(pedestal=0)
    out = client_enrichment(img, dense, cell_mask=cell)     # no background args → the safe default
    assert out['background'] == 0.0 and out['background_mode'] == 'none'
    assert out['background_warning'] is None


# ── THE guardrail: a "background" region that is really dilute phase warns; a dark one does not ──
def test_a_dilute_phase_region_triggers_the_guardrail_and_a_dark_one_does_not():
    img, dense, cell, dark_bg, dilute_bg = _scene(pedestal=50)

    # A region drawn inside the dilute phase — its mean ≈ the dilute mean → suspect, with the consequence.
    bad = client_enrichment(img, dense, cell_mask=cell, background_mask=dilute_bg)
    assert bad['background_warning'] is not None
    assert 'DESTROY' in bad['background_warning'] and 'dilute phase' in bad['background_warning']

    # A genuinely dark region outside the cell — meaningfully darker → no warning.
    good = client_enrichment(img, dense, cell_mask=cell, background_mask=dark_bg)
    assert good['background_warning'] is None


def test_assess_background_region_is_a_pure_consequence_stating_check():
    suspect, msg = assess_background_region(region_mean=98.0, dilute_mean=100.0)   # comparable
    assert suspect and 'DESTROY' in msg
    ok, msg2 = assess_background_region(region_mean=10.0, dilute_mean=100.0)       # meaningfully darker
    assert not ok and msg2 is None
    # A degenerate dilute mean cannot be judged — no false alarm.
    assert assess_background_region(50.0, 0.0) == (False, None)


# ── Offset subtraction recovers K across a pedestal (the pedestal-invariance contract via this path) ─
def test_a_region_offset_recovers_K_across_a_pedestal():
    k_true = 5.0
    values = []
    for pedestal in (0, 50, 100):
        img, dense, cell, dark_bg, _ = _scene(pedestal=pedestal, k=k_true)
        out = client_enrichment(img, dense, cell_mask=cell, background_mask=dark_bg)
        assert out['background_warning'] is None        # the dark corner is not flagged
        values.append(out['enrichment'])
    for v in values:
        assert v == pytest.approx(k_true, rel=1e-6), (
            f"the dark-region offset should recover the true K={k_true} at every pedestal, got {values}")


def test_the_ontology_caveat_records_the_background_reasoning():
    """The docstring reasoning is now available to figure footnotes via the measurement ontology."""
    from pycat.utils.measurement_ontology import describe
    caveats = ' '.join(describe('partition_coefficient').caveats)
    assert 'dilute phase is NOT background' in caveats and 'instrument/camera offset' in caveats
