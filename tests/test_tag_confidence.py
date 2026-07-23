"""**Confidence scores the EVIDENCE, not just the source — and a coin-flip binary is not a confidence.**

tag_confidence Part 1. Two joined fixes:

1. `channel_modality.classify_channel_from_pixels` floors a 2-way (fluorescence-vs-transmitted) call at its
   0.5 chance level: a decisive call is reported in the inferred-evidence band [0.70, 0.95]; a call at/below
   chance (or a tie) returns modality `None` (no decision), NEVER a near-chance number that reads like a coin
   flip. The finer 3-way transmitted sub-type may legitimately sit between its 1/3 chance and ~0.90.
2. `navigator/tags.confidence_for` grades WITHIN the metadata source (declarative 0.99 / derived 0.90 /
   weak 0.70) instead of a flat 0.8, while `user=1.0` / `pipeline=0.95` / `derived=0.85` stay unchanged.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.core


# ── (1) the pixel classifier: a binary call is decisive or None, never a near-chance number ──────────
from pycat.utils.channel_modality import (
    classify_channel_from_pixels, _binary_confidence, _EVIDENCE_MIN, _EVIDENCE_MAX)


def _fluorescence(seed=0):
    rng = np.random.default_rng(seed)
    a = rng.poisson(5, (256, 256)).astype(float)          # dark background
    for _ in range(40):                                    # sparse bright puncta
        y, x = rng.integers(10, 246, 2)
        a[y - 3:y + 3, x - 3:x + 3] += 800
    return a


def test_a_decisive_binary_call_is_reported_in_the_evidence_band():
    mod, conf = classify_channel_from_pixels(_fluorescence())
    assert mod == 'fluorescence'
    # never a near-chance 0.5–0.6 number, and never dressed up as "declared" (>=0.95 is metadata's band)
    assert _EVIDENCE_MIN <= conf <= _EVIDENCE_MAX


def test_the_binary_confidence_helper_floors_at_chance_and_bands_a_decision():
    assert _binary_confidence(0.4, 0.0) is None            # winner below the 0.5 chance level -> no decision
    assert _binary_confidence(0.6, 0.6) is None            # a tie is no decision, not "low confidence"
    c = _binary_confidence(1.0, 0.0)
    assert _EVIDENCE_MIN <= c <= _EVIDENCE_MAX
    # a clearer margin is never LESS confident than a narrow one
    assert _binary_confidence(1.0, 0.0) >= _binary_confidence(0.6, 0.4)


def test_a_binary_modality_is_NEVER_reported_below_the_evidence_band():
    """The invariant, checked over many real inputs: whenever the classifier commits to a binary modality
    it reports at least the band floor (0.70) — there is no code path that emits a near-chance binary
    number — and an undecided result is (None, 0.0), never a modality with a below-chance confidence."""
    rng = np.random.default_rng(7)
    saw_decided, saw_none = False, False
    for _ in range(30):
        img = rng.normal(rng.uniform(50, 800), rng.uniform(5, 220), (128, 128)).clip(0)
        mod, conf = classify_channel_from_pixels(img)
        if mod in ('fluorescence', 'transmitted'):
            assert conf >= _EVIDENCE_MIN, f"binary call {mod!r} reported a near-chance {conf}"
            saw_decided = True
        if mod is None:
            assert conf == 0.0
            saw_none = True
    assert saw_decided                                     # the sweep actually exercised the decided path


def test_garbage_still_degrades_to_none_zero():
    mod, conf = classify_channel_from_pixels(np.zeros((4, 4)))
    assert mod is None and conf == 0.0


# ── (2) tags.confidence_for: grade WITHIN the metadata source ────────────────────────────────────────
from pycat.navigator.tags import (
    confidence_for, TagSet, METADATA_EVIDENCE_CONFIDENCE, _SOURCE_CONFIDENCE)


def test_declarative_metadata_is_near_certain_and_a_weak_hint_is_low():
    assert confidence_for('metadata', 'declarative') >= 0.95     # the file STATES it
    assert confidence_for('metadata', 'weak') <= 0.75            # a name/filename hint
    assert confidence_for('metadata', 'derived') == 0.90         # emission nm -> spectral bucket


def test_user_pipeline_derived_confidence_are_UNCHANGED():
    # the spec forbids changing these three — they are already meaningful and tested elsewhere
    assert confidence_for('user') == 1.0
    assert confidence_for('pipeline') == 0.95
    assert confidence_for('derived') == 0.85


def test_ungraded_metadata_falls_back_to_the_flat_default():
    assert confidence_for('metadata') == 0.8                     # evidence kind unstated
    assert confidence_for('metadata', 'not_a_kind') == 0.8       # unknown kind -> fallback, not a crash
    assert confidence_for('a_totally_unknown_source') == 0.5     # unmapped source -> chance


def test_a_TagSet_grades_its_confidence_by_evidence():
    assert TagSet(source='metadata', evidence='declarative').confidence >= 0.95
    assert TagSet(source='metadata', evidence='weak').confidence <= 0.75
    assert TagSet(source='user').confidence == 1.0                       # unchanged
    assert TagSet(source='metadata').confidence == 0.8                   # ungraded fallback
    # an explicit confidence still wins over the graded default
    assert TagSet(source='metadata', evidence='weak', confidence=0.99).confidence == 0.99


def test_the_documented_scale_matches_what_the_code_emits():
    # the numbers in the module's documented scale must equal the code's — no decorative drift
    assert _SOURCE_CONFIDENCE['user'] == 1.0 and _SOURCE_CONFIDENCE['pipeline'] == 0.95
    assert _SOURCE_CONFIDENCE['derived'] == 0.85 and _SOURCE_CONFIDENCE['metadata'] == 0.8
    assert METADATA_EVIDENCE_CONFIDENCE == {'declarative': 0.99, 'derived': 0.90, 'weak': 0.70}
