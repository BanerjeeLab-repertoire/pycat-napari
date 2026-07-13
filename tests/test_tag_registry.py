"""
The tag vocabulary. **Non-degenerate by construction, and it stays that way.**

Gable: *"Everything that makes, merges, changes a layer should apply a tag to it. This should be
non-degenerate tags and they should be human readable — CLAHE can just be a tag, LoG is a good
tag."*

The problem with growing a vocabulary at the call site
------------------------------------------------------
**91 of 116 layer creations were in files that never tagged anything.** Hand-tagging them would
have produced a vocabulary that drifts: someone writes ``'clahe'``, someone else ``'CLAHE'``, a
third ``'contrast_limited_ahe'`` — and **the tag becomes unqueryable, which is the one thing it
exists to be.**

So the tag is declared **on the function that performs the operation**, not at the call site:

* it **cannot be forgotten** — the tag travels with the code, not with the callers
* it **cannot collide** — a duplicate name is an ``ImportError`` at import time
* it **cannot drift** — the vocabulary IS the set of functions that exist
* a new function that forgets the decorator is **caught by a test** (below)
"""

import ast
import pathlib

import pytest


_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"


def _registry():
    return pytest.importorskip("pycat.utils.tag_registry")


def _load_tagged_modules():
    """Import every module that declares tags, so the registry is populated."""
    import importlib
    for name in ('image_processing_tools', 'segmentation_tools', 'label_and_mask_tools',
                 'brightfield_tools', 'clean_spot_detection_tools', 'vpt_tools',
                 'topology_tools', 'zstack_segmentation_tools', 'fft_bandpass_tools',
                 'temporal_enhancement_tools', 'contrast_cascade_tools'):
        try:
            importlib.import_module(f'pycat.toolbox.{name}')
        except Exception:
            pass


@pytest.mark.core
def test_the_vocabulary_is_populated():
    """If the registry is empty, nothing below means anything."""
    registry = _registry()
    _load_tagged_modules()

    operations = registry.list_operations()
    assert len(operations) >= 50, (
        f"only {len(operations)} operations are registered. The toolbox has ~100 image and mask "
        f"transforms — if the registry is nearly empty, the decorators are not being reached."
    )


@pytest.mark.core
def test_a_duplicate_tag_is_an_ERROR_not_a_silent_collision():
    """**A tag must mean ONE thing.**

    If ``'watershed'`` were registered by two different functions, a query for it would return a
    **mixture** — and the tag system would be *worse* than no tag system, because it would look
    like it works.
    """
    registry = _registry()
    _load_tagged_modules()

    with pytest.raises(registry.TagCollision):
        @registry.tags_layer('watershed', role='mask', summary='a different watershed entirely')
        def _impostor(image):
            return image


@pytest.mark.core
def test_the_tags_are_HUMAN_READABLE():
    """``clahe``, ``log``, ``dog``, ``otsu``, ``watershed`` — **names, not descriptions.**

    A tag appears in the UI and in a query. If it is a sentence, it is not a tag.
    """
    registry = _registry()
    _load_tagged_modules()

    for op, entry in registry.list_operations().items():
        assert op == op.lower(), f"'{op}' is not lower-case — case variants are how a vocabulary rots"
        assert ' ' not in op, f"'{op}' contains a space; a tag is a NAME, not a description"
        assert len(op) <= 24, (
            f"'{op}' is {len(op)} characters. A tag that is too long to read at a glance will be "
            f"abbreviated inconsistently by whoever types it next."
        )
        assert entry['summary'], f"'{op}' has no summary — the tag inspector has nothing to show"


@pytest.mark.core
def test_every_operation_declares_what_it_PRODUCES():
    """A tag that does not say what kind of layer it makes cannot answer *"where is the mask?"*."""
    registry = _registry()
    _load_tagged_modules()

    for op, entry in registry.list_operations().items():
        assert entry['produces'] in registry.ROLES, (
            f"'{op}' produces '{entry['produces']}', which is not one of {registry.ROLES}"
        )


@pytest.mark.core
def test_an_UNREGISTERED_tag_is_REFUSED_not_written():
    """**A tag outside the vocabulary is a degenerate tag.** It cannot be queried, and nothing
    else will ever match it. Writing it would let it rot in the data.
    """
    registry = _registry()
    _load_tagged_modules()

    class _FakeLayer:
        def __init__(self):
            self.metadata = {}
            self.name = 'x'

    with pytest.raises(KeyError):
        registry.tag_from_operation(_FakeLayer(), 'a_tag_nobody_registered')


@pytest.mark.core
def test_the_canonical_tags_exist_and_mean_what_they_say():
    """The ones Gable named, plus the ones a user will actually reach for."""
    registry = _registry()
    _load_tagged_modules()

    operations = registry.list_operations()

    for op, expected_role in [
        ('log', 'preprocessed'),            # Laplacian of Gaussian — Gable named this one
        ('dog', 'preprocessed'),            # difference of Gaussians
        ('rolling_ball', 'preprocessed'),   # the background remover everyone uses
        ('watershed', 'labels'),            # the splitter
        ('cellpose', 'labels'),             # the segmenter
        ('bandpass', 'preprocessed'),
        ('invert', 'preprocessed'),
    ]:
        assert op in operations, f"'{op}' is not in the vocabulary"
        assert operations[op]['produces'] == expected_role, (
            f"'{op}' claims to produce '{operations[op]['produces']}', not '{expected_role}'"
        )

    # And an alias must resolve to the same operation.
    assert registry.get_operation('laplacian_of_gaussian')['op'] == 'log', (
        "the alias 'laplacian_of_gaussian' must resolve to 'log' — a user who types the long "
        "name should find the same thing"
    )


@pytest.mark.core
def test_a_plot_can_carry_the_tags_of_the_data_behind_it():
    """**A plot is a view of tagged data, and it should say so.**

    A figure cannot carry napari layer tags, but it can carry the same dictionary — and **that is
    what makes brushing possible.** A point in an MSD plot that knows its ``track_id`` and the
    layer it came from can be clicked back to the object.

    *The identity plumbing has to exist before the interaction can be built.*
    """
    registry = _registry()

    class _FakeLayer:
        def __init__(self, name):
            self.metadata = {}
            self.name = name

    layer = _FakeLayer('tracks')
    tags = registry.tags_for_plot(layer, plot_of='msd', track_id_column='track_id')

    assert tags['role'] == 'plot'
    assert tags['plot_of'] == 'msd'
    assert tags['sources'], "the plot must record WHICH layers it was made from"
    assert tags['sources'][0]['layer_name'] == 'tracks'
    assert tags['track_id_column'] == 'track_id', (
        "the plot must be able to carry the column that identifies an object, or a picked point "
        "cannot be traced back to a layer"
    )


# ── The sweep must STAY complete ──────────────────────────────────────────────────────────

_KNOWN_UNTAGGED = {
    # Functions whose NAME looks like a transform but which do not produce a layer. Each of these
    # is a deliberate exclusion, and the list is short on purpose: **if it grows, the vocabulary
    # is being eroded by exception rather than extended by design.**
    'detect_transitions',            # returns a dict of temperatures, not a layer
    'detect_rips', 'detect_all_rips', 'segment_fd_cycles',   # force-curve segmentation, not image
    # These two match only because 'contour' is in the keyword list. They compute a DNA CONTOUR
    # LENGTH from a freely-jointed-chain fit — a number, not an image contour. No layer.
    'contour_length_from_fjc', 'contour_increment_to_nucleotides',
    'detect_merge_fission',          # returns an event table
    'detect_out_of_focus',           # returns a per-frame verdict
    'detect_sedimentation',          # returns a verdict
    'detect_and_fit_fusions',        # returns fit results
    'matched_detection',             # a benchmark comparison, not a transform
    'manders_threshold_sensitivity', # a sensitivity sweep
    'scramble_pixels_within_mask',   # a NULL generator; tagging it would imply it is a real op
    'costes_thresholding',           # returns thresholds
    'topology_metrics',              # returns metrics
    'normalize_within_mask',         # a helper on an array, not a layer op
    'weights_from_native_mask',      # returns weights
    'fibril_morphometry',            # returns records
    'build_skeleton_graph',          # returns a graph
    'detect_beads_on_fibrils',       # returns detections, tagged at the layer
    'circular_mask', 'masks_from_shapes', 'masks_from_shapes_multi',  # FRAP ROI helpers
    'photofading_correction', 'taylor_normalize', 'prebleach_normalize',  # trace corrections
    'apply_bleach_correction',       # a trace correction
    'spots_to_mask',                 # a rendering helper
    'fft_annular_mask',              # builds the FILTER, not an image
    'cell_bboxes_from_mask', 'multi_otsu_cell_mask', 'crop_to_bbox',  # batch ROI helpers
    'cascade_rf_segment',            # trained-classifier segmentation; not yet swept
    'cascade_feature_stack',
    'filter_cells_by_transfection', 'apply_transfection_filter_to_stack',
    'segment_stack_per_frame', 'relabel_stack_by_track',
    'apply_static_pattern_correction', 'reference_subtraction', 'build_temperature_labels',
    'clean_spots_per_region',
    'blob_log_gpu', 'detect_beads_frame', 'build_hot_pixel_mask',
    'dedup_detections', 'dedup_detections_ring_merge', 'split_bead_populations',
    'compare_detection_variants', 'compare_segmentation_speed',
    'puncta_refinement_filtering_func_fast',
    'refine_labels_with_contours',
    'pseudo3d_tri_planar_filter',    # the tri-planar MACHINERY; the filters that use it are tagged
    'wavelet_bg_and_noise_calculation',  # the wavelet machinery; wbns_func is the tagged op
    'auto_object_size_valid', 'estimate_object_size_px', 'estimate_object_size_px_brightfield',
    'rb_gaussian_bg_removal_with_edge_enhancement',   # a composite; its parts are tagged
    'bf_condensate_metrics', 'bf_focus_metric',
    'enhance_stack',                 # tagged
}


def _transform_like_functions():
    """Every public function in the toolbox whose name looks like an image/mask transform."""
    keywords = ('threshold', 'filter', 'segment', 'detect', 'enhance', 'correct', 'subtract',
                'normalize', 'normalise', 'mask', 'label', 'morph', 'watershed', 'blur',
                'clahe', 'bandpass', 'deconv', 'split', 'merge', 'clean', 'skeleton',
                'rescale', 'invert', 'binary', 'contour')

    found = {}
    for path in sorted(_TOOLBOX.glob('*_tools.py')):
        tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith(('_', 'run_')):
                continue
            if not any(k in node.name.lower() for k in keywords):
                continue
            decorated = any(
                (isinstance(d, ast.Call) and getattr(d.func, 'id', '') == 'tags_layer')
                for d in node.decorator_list)
            found[node.name] = decorated
    return found


@pytest.mark.core
def test_every_layer_producing_transform_declares_a_tag():
    """**The sweep must stay complete.**

    A new transform that forgets ``@tags_layer`` produces an **untagged layer** — and an untagged
    layer is invisible to every query the tag system exists to answer. *"Where is the mask?"*
    silently misses it.

    This test catches that at the moment the function is written, which is the only moment anyone
    is thinking about it.

    If a function genuinely does not produce a layer, add it to ``_KNOWN_UNTAGGED`` **with a
    reason** — and note that the list is short on purpose: **if it grows, the vocabulary is being
    eroded by exception rather than extended by design.**
    """
    untagged = [name for name, tagged in _transform_like_functions().items()
                if not tagged and name not in _KNOWN_UNTAGGED]

    assert not untagged, (
        f"{len(untagged)} transform(s) produce a layer and do not declare a tag:\n\n  "
        + "\n  ".join(sorted(untagged))
        + "\n\nAn untagged layer is invisible to every query the tag system exists to answer. "
          "Add @tags_layer to the function — or, if it does not produce a layer, add it to "
          "_KNOWN_UNTAGGED with a reason."
    )
