"""
Every exemplar must trip its own metric — or the gallery teaches something false.

The QC gallery shows a scientist what a defect *looks like*, beside the verdict PyCAT gives it.
That is only useful if the verdict is real. **A teaching example that no longer matches the
software is worse than no example**: it tells the user their data is fine when the software would
say otherwise, or shows a "bad" panel the software calls good.

So the gallery does not hard-code its verdicts. It **calls the real metric on the real image**,
and this test asserts the result:

* the **clean** panel must come back ``good`` (or ``info``/``na`` for the metrics that only judge
  across a stack), and
* the **degraded** panel must come back ``warn`` or ``bad``.

Building the gallery immediately caught four exemplars that failed this, and one of them was a
**missing metric**: there was no ``qc_photobleaching`` at all. Bleaching is one of the most
destructive defects there is — it makes a FRAP recovery look **2.5× too fast** (1.5.455) and it
compounds exponentially through a bleach correction (1.5.451) — and the QC module had nothing
that could see it.
"""

import numpy as np
import pytest


@pytest.mark.core
def test_every_exemplar_trips_its_own_metric():
    """The whole point of the gallery: the degraded panel must actually be flagged."""
    gallery_mod = pytest.importorskip("pycat.toolbox.qc_gallery")

    gallery = gallery_mod.build_gallery()
    assert gallery, "the gallery is empty"

    failures = []
    for entry in gallery:
        clean = entry["clean_verdict"]["status"]
        degraded = entry["degraded_verdict"]["status"]

        # 'info'/'na' are legitimate CLEAN verdicts: qc_focus reports 'info' on a single image
        # on purpose (the absolute band-pass energy is scene-dependent), and the stack metrics
        # report 'na' below their minimum frame count.
        if clean not in ("good", "info", "na"):
            failures.append(
                f"{entry['key']}: the CLEAN panel is '{clean}' — the exemplar scene is itself "
                f"defective, so the comparison teaches nothing")

        if degraded not in ("warn", "bad"):
            failures.append(
                f"{entry['key']}: the DEGRADED panel is '{degraded}' — {entry['metric']} did "
                f"not fire on the very defect it exists to detect ({entry['params']})")

    assert not failures, (
        "The QC gallery is showing exemplars that do not match what the software says:\n\n  "
        + "\n  ".join(failures)
        + "\n\nA teaching example that disagrees with the metric is worse than none — it tells "
          "the user their data is fine when PyCAT would say otherwise."
    )


@pytest.mark.core
def test_exemplars_are_labelled_as_simulated():
    """A synthetic image must never be presented as an acquisition standard.

    The gallery exists because real bad-data exemplars accumulate slowly. Simulated placeholders
    are honest — **as long as they say so.** A gallery that quietly implied *"your data should
    look like this"* would be making a claim it has no business making: a real acquisition does
    not resemble a synthetic one, and a user comparing the two would draw the wrong conclusion
    about their own microscope.
    """
    gallery_mod = pytest.importorskip("pycat.toolbox.qc_gallery")

    for entry in gallery_mod.build_gallery():
        assert entry["source"] == "SIMULATED", (
            f"exemplar '{entry['key']}' is not labelled as simulated"
        )
        assert entry["params"], (
            f"exemplar '{entry['key']}' does not record the parameter that produced it — "
            f"without it the user cannot reason about the DEGREE of the defect, only its "
            f"presence"
        )


@pytest.mark.core
def test_photobleaching_metric_exists_and_measures_the_fade():
    """There was no photobleaching QC metric at all, and it is a core defect.

    It cannot be folded into ``qc_snr``: a global intensity scale changes the signal **and** the
    noise together, so the SNR is (correctly) invariant to it. **A stack that fades to a tenth
    of its brightness has the same SNR at the end as at the start — and is useless.**
    """
    qc = pytest.importorskip("pycat.toolbox.data_qc_tools")
    gallery_mod = pytest.importorskip("pycat.toolbox.qc_gallery")

    stack = gallery_mod.reference_stack(n_frames=32)

    stable = qc.qc_photobleaching(stack)
    assert stable["status"] == "good", (
        f"a stack that does NOT fade was flagged as bleaching ({stable['headline']})"
    )

    faded = qc.qc_photobleaching(gallery_mod._bleach(stack, 12.0))
    assert faded["status"] == "bad", (
        f"a stack that faded to {faded['value']:.0f}% of its starting signal was not flagged"
    )
    assert faded["value"] < 20.0, (
        f"the reported remaining signal ({faded['value']:.0f}%) does not match a tau of 12 "
        f"frames over 32 frames (exp(-31/12) = 7.5%)"
    )

    # And it must NOT be confused with SNR, which is scale-invariant by design.
    snr_before = qc.qc_snr(stack[0])["value"]
    snr_after = qc.qc_snr((stack[0].astype(float) * 0.1))["value"]
    assert snr_after == pytest.approx(snr_before, rel=0.25), (
        "qc_snr moved when the image was uniformly scaled. It should not — which is exactly why "
        "bleaching needs its own metric."
    )


@pytest.mark.core
def test_every_exemplar_carries_a_wikipedia_link_and_a_primary_citation():
    """A defect the user cannot look up is a defect they cannot learn from.

    The gallery makes a claim about the user's data — *"this is saturated, and your partition
    coefficient is meaningless"* — and a scientist is entitled to check it against something
    other than our own docstring.

    Two links, doing different jobs:

    * **Wikipedia** — the accessible entry point. Someone who has never heard of vignetting
      needs somewhere to start that is not a paywalled review.
    * **A primary reference** — from the quantitative-microscopy literature a reviewer would
      expect (Waters 2009; North 2006; Jost & Waters 2019; Jonkman et al. 2020).

    The saturation exemplar carries the passage from Waters (2009) verbatim, because it
    **independently justifies PyCAT's refusal** to report a clipped measurement:

        *"The linearity of the detector is therefore lost, and saturated images cannot be used
        for quantitation of fluorescence intensity values. Choosing to crop out saturated areas
        is not acceptable... because it will select for the weaker intensity parts of the
        specimen."*

    That is the canonical reference saying what 1.5.392 measured: a clipped value is **not** a
    lower bound, and the sensible-looking rescue is worse than the disease.
    """
    gallery_mod = pytest.importorskip("pycat.toolbox.qc_gallery")

    for entry in gallery_mod.build_gallery():
        wiki_title, wiki_url = entry["wiki"]
        cite_text, cite_url = entry["cite"]

        assert wiki_url.startswith("https://en.wikipedia.org/wiki/"), (
            f"exemplar '{entry['key']}' has no Wikipedia entry point ({wiki_url})"
        )
        assert wiki_title, f"exemplar '{entry['key']}' has an unlabelled Wikipedia link"

        assert cite_url.startswith("https://doi.org/"), (
            f"exemplar '{entry['key']}' has no resolvable DOI ({cite_url}). A citation the "
            f"reader cannot follow is not a citation."
        )
        assert any(year in cite_text for year in ("19", "20")), (
            f"exemplar '{entry['key']}' has a citation with no year: {cite_text!r}"
        )
