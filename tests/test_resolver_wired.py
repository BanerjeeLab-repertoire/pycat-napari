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
    """Every non-empty binding= string passed to create_layer_dropdown in a source file."""
    src = (_REPO / rel_path).read_text(encoding='utf-8')
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'create_layer_dropdown'):
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
def test_every_wired_binding_is_a_real_key_in_the_binding_table():
    """A dropdown must never point at a binding key that does not exist in layer_bindings.json."""
    import json
    table = json.loads((_REPO / 'src/pycat/utils/layer_bindings.json').read_text(encoding='utf-8'))
    valid = {k for k in table if not k.startswith('_')}
    # sweep the whole UI package so a future typo'd binding is caught wherever it is wired
    for path in (_REPO / 'src/pycat').rglob('*.py'):
        for b in _wired_bindings(path.relative_to(_REPO)):
            assert b in valid, f"{path.name}: binding '{b}' is not in layer_bindings.json"
