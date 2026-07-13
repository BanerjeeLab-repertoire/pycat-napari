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
