"""**Characterization pin for `segment_ivf_droplets` — the in-vitro-fluorescence droplet segmenter.**

The segmentation used to live as an inline `_task` closure inside `invitro_fluor_ui`, so it could not be
tested or carry lineage. It was extracted VERBATIM into `toolbox/invitro/segmentation.py` as a named,
`@tags_layer`-registered producer (Outstanding-Work C1 increment 3). Per the decomposition discipline
(**no test, no move**), this pins its exact output on a fixed scene for each deterministic method so the
extraction is provably behaviour-preserving, and confirms the op is registered so the produced droplet
mask can carry a lineage edge.

The `rf` method is excluded (it needs painted scribbles and trains a stochastic classifier); it shares the
same `_postfilter` tail as the others, which the deterministic methods already exercise.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base      # scikit-image / segmentation_tools stack


def _scene():
    """A fixed, [0,1]-normalised field with two bright droplets (the range CLAHE-based methods need)."""
    rng = np.random.default_rng(0)
    h = w = 64
    pre = rng.normal(0.2, 0.02, (h, w)).astype(np.float32)
    pre[18:26, 18:26] += 0.6
    pre[40:47, 42:49] += 0.5
    pre = np.clip(pre, 0, 1).astype(np.float32)
    return pre, pre.copy()


# (method, n_droplets, labelled_px, unrefined_px) — pinned on the fixed scene.
_EXPECTED = [
    ('otsu',      2,  113,  113),
    ('multiotsu', 11, 1724, 1724),
    ('sauvola',   16, 723,  723),
    ('spot',      2,  159,  367),
]


@pytest.mark.parametrize("method, n, lab_px, unref_px", _EXPECTED)
def test_each_method_segments_the_fixed_scene_exactly(method, n, lab_px, unref_px):
    from pycat.toolbox.invitro.segmentation import segment_ivf_droplets
    pre, raw = _scene()
    labeled, unrefined = segment_ivf_droplets(pre, raw, method=method)
    assert int(labeled.max()) == n
    assert int((labeled > 0).sum()) == lab_px
    assert int((np.asarray(unrefined) > 0).sum()) == unref_px
    assert labeled.dtype == np.int32


def test_the_segmentation_is_deterministic():
    from pycat.toolbox.invitro.segmentation import segment_ivf_droplets
    pre, raw = _scene()
    a, _ = segment_ivf_droplets(pre, raw, method='otsu')
    b, _ = segment_ivf_droplets(pre, raw, method='otsu')
    assert np.array_equal(a, b)


def test_the_op_is_registered_so_the_droplet_mask_can_carry_lineage():
    """`segment_ivf_droplets` is an `@tags_layer` op (role=labels, target=condensate), so
    `tag_from_operation` records rather than no-ops when the UI tags the produced droplet mask."""
    from pycat.navigator.operation_spec import _populate_registry
    _populate_registry()
    from pycat.utils.tag_registry import get_operation, operation_of
    from pycat.toolbox.invitro.segmentation import segment_ivf_droplets

    assert operation_of(segment_ivf_droplets) == 'ivf_droplet_segment'
    entry = get_operation('ivf_droplet_segment')
    assert entry is not None
    assert entry['produces'] == 'labels' and entry['target'] == 'condensate'


@pytest.mark.core
def test_the_ui_add_site_wires_lineage():
    """The in-vitro-fluorescence panel records lineage on the droplet mask it adds."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'src/pycat/toolbox/invitro_fluor_ui.py').read_text(encoding='utf-8')
    assert 'tag_from_operation(' in src and 'segment_ivf_droplets' in src
