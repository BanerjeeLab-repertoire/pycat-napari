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
    pytest.importorskip("napari")     # every test here builds a viewer; skip in a headless lane, don't error
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

        def add_tracks(self, data, name=None, **kw):
            layer = napari.layers.Tracks(np.asarray(data, float), name=name)
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


@pytest.mark.base
def test_the_vpt_bead_track_op_records_lineage_on_the_trajectories():
    """`run_vpt_analysis` is now an `@tags_layer('bead_track', role=overlay, target=bead)` op, so the VPT
    "Bead Trajectories" tracks layer carries op + a `derived_from` edge back to the bead image it was tracked
    from — the last gap in the layer-lineage story. (`overlay` is not a segmentation role, so the tracks keep
    their overlay role and gain a supersedes/derived edge, not a mask role.)"""
    from pycat.navigator.operation_spec import _populate_registry
    _populate_registry()
    from pycat.utils.tag_registry import get_operation, operation_of, tag_from_operation
    from pycat.toolbox.vpt.analysis import run_vpt_analysis
    from pycat.utils.layer_tags import get_tag, get_edges, layer_tag_id

    assert operation_of(run_vpt_analysis) == 'bead_track'
    entry = get_operation('bead_track')
    assert entry is not None and entry['produces'] == 'overlay' and entry['target'] == 'bead'

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((8, 8), np.float32), name="bead image")
    trk = viewer.add_tracks(np.array([[0, 0, 1.0, 1.0], [0, 1, 2.0, 2.0]], float), name="Bead Trajectories")
    tag_from_operation(trk, run_vpt_analysis, source_layer=src)

    assert get_tag(trk, 'op') == 'bead_track'
    assert get_tag(trk, 'target') == 'bead' and get_tag(trk, 'role') == 'overlay'
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(src) for e in get_edges(trk))


@pytest.mark.core
def test_the_vpt_add_tracks_site_wires_lineage():
    """The VPT napari adapter records lineage on the "Bead Trajectories" tracks layer it adds."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'src/pycat/toolbox/vpt/napari_adapter.py').read_text(encoding='utf-8')
    assert 'tag_from_operation(' in src and 'run_vpt_analysis' in src


@pytest.mark.base
def test_the_timeseries_per_frame_segmenter_is_a_registered_lineage_op():
    """`segment_stack_per_frame` is now an `@tags_layer` op (role=labels, target=condensate), so the
    per-frame droplet-label stack the time-series panel adds can carry op + a `derived_from` edge back to
    its source image (Outstanding-Work C1 increment 3, time-series half). @tags_layer is transparent — the
    function still returns its labelled array."""
    from pycat.navigator.operation_spec import _populate_registry
    _populate_registry()
    from pycat.utils.tag_registry import get_operation, operation_of, tag_from_operation
    from pycat.toolbox.timeseries_invitro_tools import segment_stack_per_frame
    from pycat.utils.layer_tags import get_tags, get_edges, layer_tag_id

    assert operation_of(segment_stack_per_frame) == 'ts_droplet_segment'
    entry = get_operation('ts_droplet_segment')
    assert entry is not None and entry['produces'] == 'labels' and entry['target'] == 'condensate'

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((3, 8, 8), np.float32), name="ts stack")
    out = viewer.add_labels(np.ones((3, 8, 8), int), name="TSIVF Droplet Labels (per-frame)")
    tag_from_operation(out, segment_stack_per_frame, source_layer=src)
    op_rec = next((t for t in get_tags(out) if t.get('key') == 'op'), None)
    assert op_rec is not None and op_rec['value'] == 'ts_droplet_segment' and op_rec['source'] == 'pipeline'
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(src) for e in get_edges(out))


@pytest.mark.base
def test_the_timeseries_tracked_droplet_relabel_is_a_registered_lineage_op():
    """`relabel_stack_by_track` (per-frame labels → track ids, the "TSIVF Tracked Droplets" layer) is now an
    `@tags_layer('ts_track_relabel', role=labels, target=condensate)` op, so the tracked-droplet layer — the
    primary time-series analysis output — carries op + a `derived_from` edge back to the per-frame label stack
    it recolours. This completes the chain raw → ts_droplet_segment → ts_track_relabel."""
    from pycat.navigator.operation_spec import _populate_registry
    _populate_registry()
    from pycat.utils.tag_registry import get_operation, operation_of, tag_from_operation
    from pycat.toolbox.timeseries_invitro_tools import relabel_stack_by_track
    from pycat.utils.layer_tags import get_tags, get_edges, layer_tag_id

    assert operation_of(relabel_stack_by_track) == 'ts_track_relabel'
    entry = get_operation('ts_track_relabel')
    assert entry is not None and entry['produces'] == 'labels' and entry['target'] == 'condensate'

    viewer = _install_registry_and_viewer()
    per_frame = viewer.add_labels(np.ones((3, 8, 8), int), name="TSIVF Droplet Labels (per-frame)")
    tracked = viewer.add_labels(np.ones((3, 8, 8), int), name="TSIVF Tracked Droplets")
    tag_from_operation(tracked, relabel_stack_by_track, source_layer=per_frame)
    op_rec = next((t for t in get_tags(tracked) if t.get('key') == 'op'), None)
    assert op_rec is not None and op_rec['value'] == 'ts_track_relabel'
    assert any(e['relation'] == 'derived_from' and e['target'] == layer_tag_id(per_frame)
               for e in get_edges(tracked))


@pytest.mark.core
def test_the_timeseries_ivf_add_sites_wire_lineage():
    """The time-series in-vitro-fluorescence panel records lineage on BOTH the per-frame droplet-label stack
    and the tracked-droplet relabelling."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / 'src/pycat/toolbox/timeseries_invitro_fluor_ui.py').read_text(encoding='utf-8')
    assert 'tag_from_operation(' in src
    assert 'segment_stack_per_frame' in src and 'relabel_stack_by_track' in src


@pytest.mark.base
def test_a_segmentation_output_keeps_its_own_role_not_the_source_image_role():
    """**Regression: the `mark_derived` role-inheritance bug.** A mask/labels layer produced by a
    segmentation op must carry its OWN role (the op's `produces`), NOT inherit the source image's
    `role='image'`. The old code branched on `via in ('segment','segmentation')`, but `via` is the OP NAME
    (`bf_segment`, `cellpose`, `subcellular_segment_3d`, …) — never the literal `'segment'` — so every
    pipeline segmentation output silently became `role='image'`, and every role-based binding
    (`cell_segmentation.cell_labels`, `puncta_analysis.puncta_mask`, `common.mask/labels`) failed to match it."""
    from pycat.toolbox.brightfield_tools import segment_bf_condensates
    from pycat.utils.tag_registry import tag_from_operation
    from pycat.utils.layer_tags import get_tag

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((8, 8), np.float32), name="raw image")
    mask = viewer.add_labels(np.ones((8, 8), int), name="BF Condensate Mask")
    tag_from_operation(mask, segment_bf_condensates, source_layer=src)

    assert get_tag(mask, 'role') == 'labels'          # the bug produced 'image' here
    assert get_tag(mask, 'target') == 'condensate'
    assert get_tag(mask, 'provenance') == 'segmentation'


@pytest.mark.base
def test_an_image_to_image_derivation_is_not_misclassified_as_a_segmentation():
    """The complement: a role-PRESERVING derivation (background removal: image→image) must NOT be treated as
    a segmentation — it keeps an image-like role and `provenance='derived'`, so the fix does not over-trigger."""
    from pycat.toolbox.image_processing.background import rb_gaussian_background_removal
    from pycat.utils.tag_registry import tag_from_operation
    from pycat.utils.layer_tags import get_tag

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((8, 8), np.float32), name="raw image")
    bg = viewer.add_image(np.zeros((8, 8), np.float32), name="BG removed")
    tag_from_operation(bg, rb_gaussian_background_removal, source_layer=src)

    assert get_tag(bg, 'role') not in ('mask', 'labels')     # not misread as a segmentation output
    assert get_tag(bg, 'provenance') == 'derived'


@pytest.mark.base
def test_condensate_and_cell_mask_bindings_discriminate_by_target():
    """The payoff of the role fix + target-discriminated keys: with BOTH a cell mask and a condensate mask in
    the viewer, `brightfield.condensate_mask` resolves to the condensate mask and `cell_segmentation.cell_labels`
    to the cell mask — never a silent cross-pick (the mis-selection risk that had these mask fields deferred)."""
    from pycat.toolbox.brightfield_tools import segment_bf_condensates
    from pycat.toolbox.segmentation.cellpose import cellpose_segmentation
    from pycat.utils.tag_registry import tag_from_operation
    from pycat.utils.tag_resolver import resolve_binding

    viewer = _install_registry_and_viewer()
    src = viewer.add_image(np.zeros((8, 8), np.float32), name="raw")
    cell = viewer.add_labels(np.ones((8, 8), int), name="Cell Mask")
    tag_from_operation(cell, cellpose_segmentation, source_layer=src)
    cond = viewer.add_labels(np.ones((8, 8), int), name="Condensate Mask")
    tag_from_operation(cond, segment_bf_condensates, source_layer=src)

    def _name(r):
        layer = r[0] if isinstance(r, tuple) else r
        return getattr(layer, 'name', None)

    assert _name(resolve_binding(viewer, 'brightfield.condensate_mask')) == "Condensate Mask"
    assert _name(resolve_binding(viewer, 'cell_segmentation.cell_labels')) == "Cell Mask"


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
