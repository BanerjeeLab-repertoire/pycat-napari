"""
The QC *UI* — what a user actually touches. Three bugs, all of them "the fix never reached here".

The library was audited across 1.5.465–471. **The UI was not**, and it carried its own copies of
bugs that had already been fixed one layer down:

1. **``np.asarray(layer.data)`` on a lazy stack returns FRAME 0 ONLY.** This is the 1.5.273 bug,
   still live in the QC UI while every other stack-consuming UI had moved to
   ``materialize_stack``. **The consequence is that QC lies about what it checked**: drift,
   vibration and photobleaching all need a time series, and given one frame they report *"n/a —
   needs a time series"*. A user looking at their movie reads that as *"PyCAT looked and found
   nothing to report."* **It did not look.**

2. **The coverage trap** — *"QC — all assessed metrics look good"* — fixed in ``plot_qc_report``
   in 1.5.469, while the UI carried its own hardcoded copy of the same sentence. **A correction
   that lands in one of two copies has not landed.**

3. **``qc_chromatic`` could never run.** It measures correctly when handed the channel images
   (0.00 px registered, **3.61 px on a true 3.6 px shift**), and the UI passed only the channel
   *count*. **A working check sat idle in every session.**

These are static contract tests: they read the UI source, because instantiating a Qt widget is
not possible headlessly and **the bug is structural, not behavioural.**
"""

import pathlib
import re

import pytest

_UI = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox" / "data_qc_ui.py"


@pytest.mark.core
def test_qc_ui_does_not_collapse_a_lazy_stack_to_one_frame():
    """``np.asarray`` on a lazy ``_TiffPageStack`` silently returns frame 0.

    The lazy wrapper implements ``__array__`` as a deliberately-truncated single frame, so
    ``np.asarray(layer.data)`` turns a 1000-frame movie into one image — **without error, and
    without any indication.** The 1.5.273 bug, and it has recurred three times in this codebase.

    ``materialize_stack`` is the fix, and every other stack-consuming UI already used it.
    """
    source = _UI.read_text(encoding="utf-8")

    assert "materialize_stack" in source, (
        "the QC UI does not use materialize_stack. np.asarray() on a lazy stack returns FRAME "
        "0 ONLY, so drift, vibration and photobleaching would silently report 'n/a — needs a "
        "time series' on a movie the user is looking at."
    )

    # And it must not have gone back to the naive call on the layer data.
    naive = re.search(r"np\.asarray\(\s*ui_instance\.viewer\.layers\[[^\]]+\]\.data\s*\)", source)
    assert naive is None, (
        "the QC UI is calling np.asarray() directly on a napari layer's data. If that layer is "
        "a lazy TIFF stack, this returns a single frame."
    )


@pytest.mark.core
def test_qc_ui_does_not_claim_a_clean_bill_of_health():
    """*"All assessed metrics look good"* is technically true and practically a trap.

    On an image with no pixel size, no NA and no frame interval, **only 4 of 12 checks run.**
    The user reads *"all good"* and concludes their data passed a test that was never performed.

    Fixed in ``plot_qc_report`` (1.5.469) — **and the UI had its own hardcoded copy**, so the
    fix never reached the message the user actually sees.
    """
    import ast

    source = _UI.read_text(encoding="utf-8")

    # Check the STRINGS THE USER SEES, not the source text — a comment documenting the bug is
    # not the bug, and a guard that cannot tell the difference will be disabled by the next
    # person who trips over it.
    literals = [n.value.lower() for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    offenders = [s for s in literals if "all assessed metrics look good" in s]
    assert not offenders, (
        f"the QC UI still tells the user {offenders[0]!r}. That sentence is the coverage trap: "
        f"it is true, and it reads as a clean bill of health while most of the report never ran."
    )
    assert "could NOT run" in source or "could not run" in source, (
        "the QC UI must tell the user how many checks were SKIPPED, not only how many passed"
    )


@pytest.mark.core
def test_qc_ui_passes_the_channel_images_not_just_the_count():
    """A correct check that never receives its data never runs.

    ``qc_chromatic`` measures the inter-channel shift correctly — **3.61 px on a true 3.6 px
    shift** — but only if it is given the channel images. The UI passed ``n_channels`` and not
    ``channels``, so it could never do anything but report *"info — pass the channel images"*.

    **A check that is correct and never invoked is indistinguishable from one that is broken.**
    """
    source = _UI.read_text(encoding="utf-8")

    assert "channels=" in source, (
        "the QC UI passes n_channels but never `channels=`, so qc_chromatic — which works — can "
        "never run"
    )


@pytest.mark.core
def test_the_exemplar_gallery_is_reachable_from_the_qc_ui():
    """A teaching tool nothing can open is not a teaching tool.

    The gallery (1.5.466) shows a clean image beside one carrying a known defect, with the
    verdict PyCAT gives each — the **Image** half of *Image → Assessment → Interpretation →
    Recommendation*. **It was built and wired to nothing.**

    A user reading *"Focus: bad"* on their own data has no reference for what *bad* looks like
    unless they can open it from the report that just told them.
    """
    source = _UI.read_text(encoding="utf-8")

    assert "qc_gallery_ui" in source, (
        "the QC report cannot open the exemplar gallery. The gallery exists (1.5.466) and "
        "nothing in the application can reach it."
    )
