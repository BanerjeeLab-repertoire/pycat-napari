"""**Lineage was never recorded, so "which image is behind this mask?" was unanswerable.**

`tag_from_operation` stamps a layer with the operation that produced it AND records a `derived_from`
edge to its source layer -- but it had zero real call sites, so the resolver's head-of-lineage and
"labels derived from this image" queries always fell back to weak guesses.

This pins the first batch of lineage-bearing UI add-sites (Outstanding-Work spec C1): the pre-processing
add-site records op + source edge through the real UI path, and the shared add-labels mechanism
(cellpose / subcellular segmentation) records op + source edge on a labels layer.

Integration-marked (builds real napari layers; skips headless).
"""
import numpy as np
import pytest


def _install_registry_and_viewer():
    hook = pytest.importorskip("pycat.utils.layer_tag_hook")
    from pycat.navigator.operation_spec import _populate_registry
    assert not _populate_registry(), "tag discovery could not import a decorated module"
    import napari

    class _Selection:
        def __init__(self):
            self.active = None

    class _Layers(list):
        def __init__(self):
            super().__init__()
            self.selection = _Selection()

        def __getitem__(self, k):
            if isinstance(k, str):
                return next(l for l in self if l.name == k)
            return list.__getitem__(self, k)

    class _Viewer:
        def __init__(self):
            self.layers = _Layers()

        def add_image(self, data, name=None, **kw):
            layer = napari.layers.Image(np.asarray(data), name=name)
            self.layers.append(layer)
            return layer

        def add_labels(self, data, name=None, **kw):
            layer = napari.layers.Labels(np.asarray(data).astype(int), name=name)
            self.layers.append(layer)
            return layer

    return hook.install(_Viewer())


class _DataInstance:
    def __init__(self, **repo):
        self.data_repository = dict(repo)


@pytest.mark.integration
def test_preprocess_add_site_records_op_and_source_edge():
    """run_pre_process_image must stamp the pre-processed layer with op='preprocess' (source='pipeline')
    and a derived_from edge back to the active input image."""
    from pycat.toolbox.image_processing.preprocessing import run_pre_process_image
    from pycat.utils.layer_tags import get_tags, get_edges, layer_tag_id

    viewer = _install_registry_and_viewer()
    rng = np.random.default_rng(0)
    img = rng.uniform(0, 1000, (64, 64)).astype(np.float32)
    input_layer = viewer.add_image(img, name="raw")
    viewer.layers.selection.active = input_layer

    di = _DataInstance(ball_radius=3, cell_diameter=20, suppress_foreground=False)
    run_pre_process_image(di, viewer)

    produced = viewer.layers[-1]
    assert produced is not input_layer
    op_rec = next((t for t in get_tags(produced) if t.get('key') == 'op'), None)
    assert op_rec is not None and op_rec['value'] == 'preprocess'
    assert op_rec['source'] == 'pipeline'
    # the lineage edge points back at the raw input
    edges = get_edges(produced)
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(input_layer)
               for e in edges), edges


@pytest.mark.core
def test_increment_2_ui_sites_wire_tag_from_operation():
    """Wiring guard (Outstanding-Work C1 increment 2): the z-stack (bg / cell / condensate) and the two
    brightfield condensate-mask add-sites now record lineage. A source-level check so it runs in the fast
    lane without building Qt — the mechanism itself is proved below."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    zstack = (repo / 'src/pycat/toolbox/zstack_segmentation_ui.py').read_text(encoding='utf-8')
    assert zstack.count('tag_from_operation(') >= 3, "z-stack bg/cell/condensate lineage not all wired"
    for fname in ('brightfield_ui.py', 'invitro_bf_ui.py'):
        src = (repo / 'src/pycat/toolbox' / fname).read_text(encoding='utf-8')
        assert 'tag_from_operation(' in src, f"{fname}: condensate-mask lineage not wired"


@pytest.mark.integration
@pytest.mark.parametrize("module, fn_name, expected_op", [
    ("pycat.toolbox.zstack_segmentation_tools", "bg_removal_3d", "bg_subtract_3d"),
    ("pycat.toolbox.zstack_segmentation_tools", "cellpose_segmentation_3d", "cellpose_3d"),
    ("pycat.toolbox.zstack_segmentation_tools", "segment_subcellular_objects_3d", "subcellular_segment_3d"),
    ("pycat.toolbox.brightfield_tools", "segment_bf_condensates", "bf_segment"),
])
def test_increment_2_producers_record_op_and_source_edge(module, fn_name, expected_op):
    """Each increment-2 producer is a REGISTERED op (so tag_from_operation records rather than no-ops) and
    stamps op (source='pipeline') + a derived_from edge back to its source layer."""
    import importlib
    producer = getattr(importlib.import_module(module), fn_name)
    from pycat.utils.tag_registry import tag_from_operation
    from pycat.utils.layer_tags import get_tags, get_edges, layer_tag_id

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((4, 16, 16), np.float32), name="input volume")
    out = viewer.add_labels(np.ones((4, 16, 16), int), name=f"{expected_op} output")

    tag_from_operation(out, producer, source_layer=src)

    op_rec = next((t for t in get_tags(out) if t.get('key') == 'op'), None)
    assert op_rec is not None and op_rec['value'] == expected_op
    assert op_rec['source'] == 'pipeline'
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(src)
               for e in get_edges(out))


@pytest.mark.integration
def test_add_labels_mechanism_records_op_and_source_edge():
    """The shared cellpose/subcellular pattern: tag_from_operation on a produced labels layer records the
    op (source='pipeline') and a derived_from edge to the source image."""
    from pycat.toolbox.segmentation.cellpose import cellpose_segmentation
    from pycat.utils.tag_registry import tag_from_operation
    from pycat.utils.layer_tags import get_tags, get_edges, layer_tag_id

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((32, 32), np.float32), name="cell image")
    labels = viewer.add_labels(np.eye(32, dtype=int), name="Cellpose Segmentation on cell image")

    tag_from_operation(labels, cellpose_segmentation, source_layer=src)

    op_rec = next((t for t in get_tags(labels) if t.get('key') == 'op'), None)
    assert op_rec is not None and op_rec['value'] == 'cellpose'
    assert op_rec['source'] == 'pipeline'
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(src)
               for e in get_edges(labels))
