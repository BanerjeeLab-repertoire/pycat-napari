"""**The tag resolver + layer_bindings.json were fully built but dormant: 0 of ~170 dropdowns bound.**

`create_layer_dropdown` accepts a ``binding=`` naming an entry in ``layer_bindings.json``; when set, the
dropdown autopopulates from the layer whose TAGS match (surviving renames/reordering), and -- crucially --
selects NOTHING when several layers match and none is clearly right, naming the candidates rather than
guessing. But almost no call site passed ``binding=``, so the mechanism had no consumers.

This is the "0 bound → N bound" regression guard (Outstanding-Work spec D, increment 1). It asserts the
curated high-value dropdowns now carry their binding. The binding SEMANTICS (ambiguous coloc channels
select nothing) are covered by test_tag_resolver; this pins the wiring.
"""
import ast
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _wired_bindings(rel_path):
    """Every non-empty binding= string passed to a dropdown-builder in a source file.

    Both entry points are swept: create_layer_dropdown (the direct builder) and _layer_row (the
    status-circle wrapper that forwards binding= to it), so a binding wired through either is validated.
    """
    src = (_REPO / rel_path).read_text(encoding='utf-8')
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('create_layer_dropdown', '_layer_row')):
            for kw in node.keywords:
                if kw.arg == 'binding' and isinstance(kw.value, ast.Constant) and kw.value.value:
                    found.append(kw.value.value)
    return found


@pytest.mark.core
def test_object_coloc_dropdowns_are_bound_to_the_channel_keys():
    """The object-based coloc mask dropdowns carry the deliberately-ambiguous channel bindings."""
    bound = _wired_bindings('src/pycat/ui/ui_analysis_mixin.py')
    assert 'colocalization.channel_a' in bound
    assert 'colocalization.channel_b' in bound


@pytest.mark.core
def test_increment_2_domain_dropdowns_carry_their_bindings():
    """Increment 2: the highest-value, tag-discriminated dropdowns (cell segmentation in/out, puncta mask,
    brightfield input, VPT bead stack, invitro-fluor input) now carry their bindings. These discriminate by
    target/modality (not merely newest), so the resolver auto-selects the right layer or, when ambiguous,
    none — never a silent wrong pick."""
    analysis = _wired_bindings('src/pycat/ui/ui_analysis_mixin.py')
    assert 'cell_segmentation.cell_labels' in analysis
    assert 'puncta_analysis.puncta_mask' in analysis
    assert 'cell_segmentation.input_image' in _wired_bindings('src/pycat/ui/ui_segmentation_mixin.py')
    assert 'brightfield.input_image' in _wired_bindings('src/pycat/toolbox/brightfield_ui.py')
    assert 'brightfield.input_image' in _wired_bindings('src/pycat/toolbox/invitro_bf_ui.py')
    assert 'vpt.bead_stack' in _wired_bindings('src/pycat/toolbox/vpt/panels.py')
    assert 'invitro_fluor.input_image' in _wired_bindings('src/pycat/toolbox/invitro_fluor_ui.py')


@pytest.mark.core
def test_increment_3_raw_and_preprocessed_image_dropdowns_are_bound():
    """Increment 3: the clearly-labeled raw / preprocessed IMAGE dropdowns across the brightfield, in-vitro,
    and z-stack panels carry common.raw_image / common.preprocessed_image. `raw` is provenance-discriminated
    (prefer=head_of_lineage) and both degrade to an EMPTY dropdown when several images match — never a silent
    wrong pick — so these role-only bindings are safe to wire ahead of the ambiguous mask/labels ones."""
    for f in ('src/pycat/toolbox/brightfield_ui.py', 'src/pycat/toolbox/invitro_bf_ui.py',
              'src/pycat/toolbox/invitro_fluor_ui.py', 'src/pycat/toolbox/zstack_segmentation_ui.py'):
        bound = _wired_bindings(f)
        assert 'common.raw_image' in bound, f
        assert 'common.preprocessed_image' in bound, f


@pytest.mark.core
def test_increment_4_condensate_and_cell_mask_consumers_are_bound():
    """Increment 4 (unblocked by the mark_derived role fix): the brightfield / in-vitro condensate & droplet
    mask consumers bind to `brightfield.condensate_mask`, and the brightfield cell-mask consumers to
    `cell_segmentation.cell_labels`. Both are target-discriminated (condensate vs cell), so a condensate slot
    never auto-picks a co-existing cell mask, and vice versa."""
    bf = _wired_bindings('src/pycat/toolbox/brightfield_ui.py')
    assert 'brightfield.condensate_mask' in bf
    assert 'cell_segmentation.cell_labels' in bf
    assert 'brightfield.condensate_mask' in _wired_bindings('src/pycat/toolbox/invitro_bf_ui.py')


@pytest.mark.core
def test_every_wired_binding_is_a_real_key_in_the_binding_table():
    """A dropdown must never point at a binding key that does not exist in layer_bindings.json."""
    import json
    table = json.loads((_REPO / 'src/pycat/utils/layer_bindings.json').read_text(encoding='utf-8'))
    valid = {k for k in table if not k.startswith('_')}
    # sweep the whole UI package so a future typo'd binding is caught wherever it is wired
    for path in (_REPO / 'src/pycat').rglob('*.py'):
        for b in _wired_bindings(path.relative_to(_REPO)):
            assert b in valid, f"{path.name}: binding '{b}' is not in layer_bindings.json"
