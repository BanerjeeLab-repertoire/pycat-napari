"""
**Which layer does this step want?** Ask the tags — and refuse to guess.

The problem
-----------
Every workflow step in PyCAT has a layer dropdown, and **the user fills every one of them by
hand, in every step, on every run.** ``field_status`` tracks *whether* a dropdown is filled;
**nothing fills it.**

The thing that must not happen
------------------------------
**A wrong auto-selection that the user does not notice is worse than an empty dropdown.** They run
the analysis on the wrong layer, get a number, and **never know.**

So the resolver returns a **confidence** and a **reason**:

===========  ==========================================  ============================
confidence   what it means                               what the UI does
===========  ==========================================  ============================
``certain``  **exactly one** layer matches               auto-select it
``likely``   several match, one is clearly best          pre-select, **and say so**
``ambiguous``  several match, no clear winner            **do not choose** — list them
``none``     nothing matches                             say what was looked for
===========  ==========================================  ============================

*This is the same principle as the brushing refusal (1.5.497) and the same principle as "an absent
tag is honest; a guessed one is a lie" (1.5.493). It keeps coming up because it is the same
mistake wearing different clothes.*
"""

import importlib
import io
import contextlib

import numpy as np
import pytest


class _FakeLayer:
    def __init__(self, data, name):
        self.data = data
        self.name = name
        self.metadata = {}


class _FakeViewer:
    def __init__(self):
        self.layers = []

    def add_image(self, data, name=None, **kwargs):
        layer = _FakeLayer(data, name)
        self.layers.append(layer)
        return layer

    def add_labels(self, data, name=None, **kwargs):
        layer = _FakeLayer(data, name)
        self.layers.append(layer)
        return layer


def _viewer_with(*layers):
    """A tagged viewer, built through the real auto-tagging hook."""
    hook = pytest.importorskip("pycat.utils.layer_tag_hook")

    for module in ('image_processing_tools', 'segmentation_tools', 'vpt_tools'):
        try:
            importlib.import_module(f'pycat.toolbox.{module}')
        except Exception:
            pass

    viewer = hook.install(_FakeViewer())

    with contextlib.redirect_stderr(io.StringIO()):
        for kind, name, data in layers:
            if kind == 'image':
                viewer.add_image(data, name=name)
            else:
                viewer.add_labels(data, name=name)

    return viewer


@pytest.mark.core
def test_an_unambiguous_layer_is_found_with_CERTAINTY():
    """The whole point: a step asks for what it needs, and the tags answer."""
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(64, 64)),
        ('image', 'CLAHE', np.random.rand(64, 64)),
        ('labels', 'Cellpose labels', np.random.randint(0, 7, (64, 64))),
    )

    layer, confidence, reason = resolver.resolve(
        viewer, dict(role='image', provenance='raw'))

    assert confidence == resolver.CERTAIN
    assert layer.name == 'movie.tif'
    assert 'only' in reason, "the reason must say WHY — a user cannot check a bare answer"


@pytest.mark.core
def test_AMBIGUITY_refuses_to_choose_and_NAMES_the_candidates():
    """**This is the property the module exists for.**

    Two images match "an image". Choosing one would be a coin flip — and an analysis run on the
    wrong layer **gives a number that looks fine.**
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(64, 64)),
        ('image', 'CLAHE', np.random.rand(64, 64)),
    )

    layer, confidence, reason = resolver.resolve(viewer, dict(role='image'))

    assert confidence == resolver.AMBIGUOUS
    assert layer is None, (
        "two layers matched and one was chosen anyway. **A wrong auto-selection the user does "
        "not notice is worse than an empty dropdown** — they run the analysis on the wrong layer "
        "and never know."
    )
    assert 'movie.tif' in reason and 'CLAHE' in reason, (
        "the user must be told WHICH layers matched, so they can pick"
    )


@pytest.mark.core
def test_head_of_lineage_finds_the_SOURCE_not_the_newest():
    """**An empty lineage graph is not evidence that a layer is a source.**

    It is evidence that **nobody recorded the lineage** — and a first version of this resolver
    read it as the former, returning the most recently added *derived* layer as "the source".

    The auto-tagging hook cannot record a parent (by the time a UI calls ``add_image(result)``,
    the transform that made ``result`` has already returned). **But it knows something better**:
    whether a layer was the first image into an empty viewer, which it records as
    ``provenance='raw'``. That answers the question **with certainty** rather than inferring it
    from an absence.
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(32, 32)),
        ('image', 'CLAHE', np.random.rand(32, 32)),
        ('image', 'Rolling ball', np.random.rand(32, 32)),
    )

    layer, confidence, _reason = resolver.resolve(
        viewer, dict(role='image', prefer='head_of_lineage'))

    assert layer.name == 'movie.tif', (
        f"the SOURCE is 'movie.tif'; got '{layer.name if layer else None}'. Reading an absent "
        f"lineage graph as 'this has no parent' returns the most recent DERIVED layer."
    )
    assert confidence == resolver.CERTAIN


@pytest.mark.core
def test_the_operations_TARGET_is_carried_onto_the_layer():
    """**The registry knew ``cellpose`` produces CELLS. The hook was throwing it away.**

    A step asking for "the cell labels" (``role=labels, target=cell``) found **nothing** — with a
    Cellpose layer sitting right there. *The information existed; it was not being carried the
    last inch.*
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")
    layer_tags = pytest.importorskip("pycat.utils.layer_tags")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(64, 64)),
        ('labels', 'Cellpose labels', np.random.randint(0, 7, (64, 64))),
    )

    cellpose_layer = viewer.layers[-1]
    tags = {t['key']: t['value'] for t in layer_tags.get_tags(cellpose_layer)}

    assert tags.get('target') == 'cell', (
        f"the Cellpose layer carries {tags}. The registry declares that cellpose targets CELLS — "
        f"if the tag is not carried onto the layer, a step asking for the cell labels finds "
        f"nothing."
    )

    layer, confidence, _reason = resolver.resolve_binding(
        viewer, 'cell_segmentation.cell_labels')

    assert confidence == resolver.CERTAIN and layer.name == 'Cellpose labels'


@pytest.mark.core
def test_the_binding_table_is_loadable_and_the_AMBIGUOUS_ones_are_DELIBERATE():
    """**A field that cannot be decided from tags should NOT be autopopulated.**

    The colocalization channels are the clearest case: with two channel masks present, choosing
    one is a **coin flip** — and *a colocalization run on the wrong pairing gives a number that
    looks fine.* Leaving ``prefer`` out of the binding is how that is said.
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    bindings = resolver._load_bindings()

    assert len(bindings) >= 10, f"only {len(bindings)} bindings loaded"

    for key in ('common.raw_image', 'common.mask', 'cell_segmentation.cell_labels'):
        assert key in bindings, f"'{key}' is missing from the binding table"
        assert 'why' in bindings[key], (
            f"'{key}' has no 'why'. The binding's reason is the SCIENTIFIC one — why this step "
            f"wants this layer — and it is what the user reads in the tooltip."
        )

    for key in ('colocalization.channel_a', 'colocalization.channel_b'):
        assert 'prefer' not in bindings[key], (
            f"'{key}' declares a preference. **It must not** — with two channel masks present, "
            f"choosing one is a coin flip, and a colocalization run on the wrong pairing gives a "
            f"number that looks fine."
        )


@pytest.mark.core
def test_a_layer_can_EXPLAIN_itself():
    """**Anti-black-box.** A user who can see *"this is a mask, made by otsu"* can check it."""
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(64, 64)),
        ('labels', 'Cellpose labels', np.random.randint(0, 7, (64, 64))),
    )

    raw_explanation = resolver.explain(viewer.layers[0])
    cellpose_explanation = resolver.explain(viewer.layers[1])

    assert 'microscope' in raw_explanation, (
        f"the raw layer must say it is the acquisition; got: {raw_explanation!r}"
    )
    assert 'cellpose' in cellpose_explanation.lower(), (
        f"a derived layer must say what MADE it; got: {cellpose_explanation!r}"
    )


@pytest.mark.core
def test_the_binding_table_SHIPS_in_the_package():
    """**A data file that is not in ``package-data`` does not exist in the wheel.**

    The resolver falls back to an empty binding table when the JSON cannot be loaded — so a
    missing ``package-data`` entry would mean **every dropdown silently stops autopopulating in
    the installed package, while working perfectly in the repo.** That is the worst kind of bug:
    it cannot be reproduced by the person who wrote it.
    """
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[1]

    # The file exists where the resolver looks for it.
    binding_file = root / "src" / "pycat" / "utils" / "layer_bindings.json"
    assert binding_file.exists(), f"{binding_file} is missing"

    # And the build is told to include it.
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding='utf-8'))
    package_data = (config['tool']['hatch']['build']['targets']['wheel']
                    ['package-data']['pycat'])

    assert any('utils' in entry and 'json' in entry for entry in package_data), (
        f"layer_bindings.json is not in the wheel's package-data: {package_data}. The resolver "
        f"would fall back to an EMPTY binding table in the installed package — silently — while "
        f"working perfectly in the repo."
    )


# ── The UI wiring: two properties that must hold ──────────────────────────────────────────

class _FakeCombo:
    def __init__(self):
        self.items = []
        self.idx = -1
        self.tip = ''

    def addItem(self, text):
        self.items.append(text)

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def setCurrentIndex(self, i):
        self.idx = i

    def currentText(self):
        return self.items[self.idx] if 0 <= self.idx < len(self.items) else ''

    def setToolTip(self, text):
        self.tip = text


@pytest.mark.core
def test_a_LIKELY_match_is_SELECTED_and_flagged_rather_than_silently_ignored():
    """**A `likely` result that selects NOTHING is the worst outcome.**

    A first version only selected on ``certain``. So a binding with ``prefer='newest'`` — which is
    **most of them** — resolved to ``likely``, **selected nothing, and said nothing.** The dropdown
    sat empty while the resolver knew perfectly well which layer was wanted.

    That is worse than either alternative: **it is the feature silently not working.**

    So a ``likely`` match IS selected, and the tooltip **says it was inferred and asks the user to
    check.** They see a filled dropdown *and* the information to catch it if it is wrong.
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('image', 'movie.tif', np.random.rand(32, 32)),
        ('labels', 'First labels', np.random.randint(0, 7, (32, 32))),
        ('labels', 'Second labels', np.random.randint(0, 5, (32, 32))),
    )

    combo = _FakeCombo()
    for layer in viewer.layers:
        combo.addItem(layer.name)

    with contextlib.redirect_stderr(io.StringIO()):
        confidence, reason = resolver.autopopulate(viewer, combo, 'common.labels')

    assert confidence == resolver.LIKELY
    assert combo.currentText() == 'Second labels', (
        f"a LIKELY match selected {combo.currentText()!r}. If it selects NOTHING, the resolver "
        f"knows the answer and the dropdown sits empty — the feature silently not working."
    )
    assert 'Check this is the one you meant' in reason, (
        "an inferred selection must SAY it was inferred, or the user cannot catch it when wrong"
    )


@pytest.mark.core
def test_AMBIGUOUS_still_selects_NOTHING():
    """The line between "infer it and flag it" and "refuse" is where the resolver **cannot know**.

    ``colocalization.channel_a`` declares no preference **on purpose**: with two channel masks
    present, choosing one is a **coin flip**, and *a colocalization run on the wrong pairing gives
    a number that looks fine.*
    """
    resolver = pytest.importorskip("pycat.utils.tag_resolver")

    viewer = _viewer_with(
        ('labels', 'Mask A', (np.random.rand(32, 32) > 0.5).astype(np.uint8)),
        ('labels', 'Mask B', (np.random.rand(32, 32) > 0.5).astype(np.uint8)),
    )

    combo = _FakeCombo()
    for layer in viewer.layers:
        combo.addItem(layer.name)

    with contextlib.redirect_stderr(io.StringIO()):
        confidence, reason = resolver.autopopulate(viewer, combo, 'colocalization.channel_a')

    assert confidence == resolver.AMBIGUOUS
    assert combo.currentText() == '', (
        f"an AMBIGUOUS binding selected {combo.currentText()!r}. It must select NOTHING — "
        f"choosing between two channel masks is a coin flip, and the analysis would run on the "
        f"wrong pairing and give a number that looks fine."
    )
    assert 'Mask A' in reason and 'Mask B' in reason, (
        "the user must be told which layers matched, so they can pick"
    )


@pytest.mark.core
def test_the_dropdown_builder_accepts_a_binding():
    """``name_hint`` matches a **layer NAME**. A binding matches what the layer **IS**.

    ``name_hint='Labeled Cell Mask'`` works until someone renames a layer, or a new operation
    produces a name containing the same substring — **and then it silently selects the wrong one.
    It is matching a label, not a fact.**

    A binding survives renaming, reordering, and a user who calls their mask *"asdf"*.
    """
    # Read the SOURCE, not the module: ui_modules imports napari, which is not importable in a
    # headless test environment. The signature is a fact about the code, and the code is right
    # there.
    import ast
    import pathlib as _pathlib

    source = (_pathlib.Path(__file__).resolve().parents[1]
              / "src" / "pycat" / "ui" / "ui_modules.py").read_text(encoding='utf-8')
    tree = ast.parse(source)

    builder = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'create_layer_dropdown':
            builder = node
            break

    assert builder is not None, "create_layer_dropdown is gone"

    parameters = [a.arg for a in builder.args.args]
    assert 'binding' in parameters, (
        f"create_layer_dropdown takes {parameters}. It must accept a tag `binding` — the strong "
        f"version of `name_hint`, which matches a layer NAME and breaks the moment one is renamed"
    )
