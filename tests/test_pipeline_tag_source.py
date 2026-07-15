"""
`source='pipeline'` must survive as a high-confidence source — it must NOT be silently downgraded
to 'inferred'.

The bug this guards (found in the 2026-07-15 codebase audit, item A1)
--------------------------------------------------------------------
`tag_registry.py` writes pipeline-produced tags with ``source='pipeline'`` (the operation that made
the layer is KNOWN — definitional, not a guess). But ``VALID_SOURCES`` used to omit 'pipeline', so
``tag_layer`` rewrote it to 'inferred' and dropped its confidence to 0.6 — mislabelling every
pipeline-produced tag as a low-confidence inference. Any tag resolver that ranks candidates by
confidence (the Scientific Navigator's whole premise) would then distrust pipeline layers.

This test is CORE (no napari / Qt / GPU): ``tag_layer`` only needs an object with a ``.metadata``
dict, so a trivial fake layer exercises the real code headlessly.
"""

import pytest

pytestmark = pytest.mark.core


class _FakeLayer:
    """Minimal stand-in for a napari layer: all tag_layer needs is a metadata dict."""
    def __init__(self):
        self.metadata = {}


def _tags():
    return pytest.importorskip("pycat.utils.layer_tags")


def test_pipeline_is_a_valid_source():
    lt = _tags()
    assert 'pipeline' in lt.VALID_SOURCES, (
        "source='pipeline' must be recognised — tag_registry writes it, and if it is not in "
        "VALID_SOURCES it gets silently rewritten to 'inferred'."
    )


def test_pipeline_source_is_preserved_not_downgraded():
    lt = _tags()
    layer = _FakeLayer()
    ok = lt.tag_layer(layer, 'op', 'clahe', source='pipeline')
    assert ok
    rec = next(t for t in lt.get_tags(layer) if t['key'] == 'op')
    assert rec['source'] == 'pipeline', (
        f"expected source to stay 'pipeline', got '{rec['source']}' — it was downgraded, which is "
        "exactly the audit-A1 bug."
    )


def test_pipeline_source_carries_high_confidence():
    lt = _tags()
    layer = _FakeLayer()
    lt.tag_layer(layer, 'role', 'mask', source='pipeline')
    rec = next(t for t in lt.get_tags(layer) if t['key'] == 'role')
    # A definitional pipeline tag should be trusted on par with 'derived' (~0.95), NOT 'inferred'
    # (0.6). We assert it is clearly above the inferred default rather than pinning an exact number.
    assert rec['confidence'] >= 0.9, (
        f"pipeline-produced tag confidence {rec['confidence']} is too low — it is being treated as "
        "an inference (0.6) instead of a known operation (~0.95)."
    )


def test_genuinely_invalid_source_still_downgrades():
    """The fix must not weaken validation: an unknown source is still downgraded to 'inferred'."""
    lt = _tags()
    layer = _FakeLayer()
    lt.tag_layer(layer, 'target', 'cell', source='not_a_real_source')
    rec = next(t for t in lt.get_tags(layer) if t['key'] == 'target')
    assert rec['source'] == 'inferred', (
        "an unrecognised source must still fall back to 'inferred' — only 'pipeline' was added to "
        "the valid set, not a general bypass of validation."
    )
