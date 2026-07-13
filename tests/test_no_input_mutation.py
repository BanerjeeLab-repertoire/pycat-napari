"""
**A function that writes into the array it was given is a bug generator.**

``extend_mask_to_edges`` did::

    mask[0:size, :] = mask[size, None]

That modifies the **caller's** array. Measured: a caller's mask went from **361 px to 400 px**, and
``result is mask`` was **True** — *there was no new array at all.*

**If that array is a napari layer, the user's mask on screen silently changes.** And a workflow
re-run starts from data that is no longer what the user segmented.

It happened to be idempotent — running it twice gave the same answer — but that was **luck, not
design**: the second call simply found the border already filled. ***The aliasing is the bug, and
idempotence does not excuse it.***

And ``segmentation_tools`` passes ``refined_labels`` to it — a **labels** array, not a boolean mask
— so the propagated border carries **label IDs**.
"""

import ast
import io
import contextlib
import pathlib

import numpy as np
import pytest


@pytest.mark.core
def test_extend_mask_to_edges_does_not_touch_the_callers_array():
    """**The user's mask is theirs.** A tool that reads it must not rewrite it."""
    masks = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    mask = np.zeros((32, 32), bool)
    mask[1:20, 1:20] = True          # touches nothing at the border — the case that mutates
    before = mask.copy()

    with contextlib.redirect_stderr(io.StringIO()):
        result = masks.extend_mask_to_edges(mask, 1)

    assert np.array_equal(mask, before), (
        f"the caller's mask was modified in place: {before.sum()} px -> {mask.sum()} px. "
        f"If that array is a napari layer, the user's mask on screen has silently changed."
    )
    assert result is not mask, (
        "the function returned the SAME OBJECT it was given — there is no new array, so the "
        "caller and the result are aliases of each other"
    )

    # And it still does its job.
    assert np.asarray(result).sum() > before.sum(), (
        "the border was not extended — the fix broke the function"
    )


@pytest.mark.core
def test_a_LABELS_array_survives_the_border_extension():
    """``segmentation_tools`` passes **labels** here, not a boolean mask."""
    masks = pytest.importorskip("pycat.toolbox.label_and_mask_tools")

    labels = np.zeros((32, 32), np.int32)
    labels[1:20, 1:20] = 7
    before = labels.copy()

    with contextlib.redirect_stderr(io.StringIO()):
        result = masks.extend_mask_to_edges(labels, 1)

    assert np.array_equal(labels, before), "the caller's LABELS array was modified in place"
    assert 7 in np.unique(np.asarray(result)), "the label ID was lost"


@pytest.mark.core
def test_no_TOOLBOX_function_writes_into_a_parameter_array():
    """**A category, not a one-off.**

    Any ``arr[...] = ...`` where ``arr`` is a parameter modifies the caller's data. The
    exceptions below are deliberate and say so in their names.
    """
    allowed = {
        'stitch_into',            # the name IS the contract: it writes into the array you give it
        'plot_msd_trajectories',  # `line_registry` is a registry — a dict the caller wants filled
        '_draw_msd_into',         # ditto, and the name says "into"
        'plot_vpt_panel',         # ditto
    }

    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    offenders = []
    for path in sorted(toolbox.glob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name in allowed:
                continue

            parameters = {a.arg for a in node.args.args}
            body = ast.get_source_segment(source, node) or ''

            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign):
                    continue
                for target in inner.targets:
                    if not (isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Name)):
                        continue
                    name = target.value.id
                    if name not in parameters:
                        continue

                    # Is it copied before being written to?
                    import re
                    copied = re.search(
                        rf'\b{re.escape(name)}\s*=\s*[^\n]*(\.copy\(\)|np\.array\([^)]*copy=True)',
                        body)
                    if not copied:
                        offenders.append(
                            f"{path.name}:{inner.lineno} {node.name} writes into `{name}`")

    assert not offenders, (
        "these functions write into an array they were GIVEN:\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\n\nThe caller's data is silently changed — and if it is a napari layer, the user's "
          "own mask or image on screen changes with it. Copy it first, or add the function to "
          "`allowed` **with a reason** if writing into the caller's array is the contract."
    )
