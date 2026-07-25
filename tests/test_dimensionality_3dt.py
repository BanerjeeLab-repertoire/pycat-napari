"""**A volumetric time-lapse (TZYX) was mislabelled as a 2D movie.**

The dimensionality classifier checked ``n_t > 1`` before ``n_z > 1``, so a stack with BOTH a time and a
z axis was tagged ``2d+t`` — silently dropping the z dimension from the tag — even though ``axis_order``
correctly recorded ``TZYX``. The tag vocabulary also lacked ``3d+t`` (and the ``phase`` / ``DIC`` /
``trace`` modalities). Both are added (Outstanding-Work spec F1).
"""
import pytest


class _Layer:
    def __init__(self):
        self.metadata = {}


@pytest.mark.core
@pytest.mark.parametrize("n_t,n_z,expected", [
    (1, 1, '2d'),
    (5, 1, '2d+t'),
    (1, 8, 'z-stack'),
    (5, 8, '3d+t'),          # the fix: TZYX is a volumetric time-lapse, not 2d+t
])
def test_dimensionality_classification(n_t, n_z, expected):
    lt = pytest.importorskip("pycat.utils.layer_tags")
    tagging = pytest.importorskip("pycat.file_io.tagging")
    layer = _Layer()
    tagging._tag_layout(lt, layer, n_t, n_z, n_p=1, axis_answer=None)
    assert lt.get_tag(layer, 'dimensionality') == expected


@pytest.mark.core
def test_tzyx_stack_also_records_the_full_axis_order():
    lt = pytest.importorskip("pycat.utils.layer_tags")
    tagging = pytest.importorskip("pycat.file_io.tagging")
    layer = _Layer()
    tagging._tag_layout(lt, layer, n_t=5, n_z=8, n_p=1, axis_answer=None)
    assert lt.get_tag(layer, 'dimensionality') == '3d+t'
    assert lt.get_tag(layer, 'axis_order') == 'TZYX'


@pytest.mark.core
def test_new_vocabulary_values_validate():
    lt = pytest.importorskip("pycat.utils.layer_tags")
    assert lt._validate('dimensionality', '3d+t')[0]
    for m in ('phase', 'DIC', 'trace'):
        assert lt._validate('modality', m)[0], m
    # a genuinely bogus value is still refused (the vocabulary is still controlled)
    assert not lt._validate('dimensionality', '7d')[0]
