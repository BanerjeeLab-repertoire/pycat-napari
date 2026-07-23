"""
**Batch segmented the same image differently from the recording.**

Reported by Gable, and it is the failure that makes the whole feature unusable: *a batch run that
does not reproduce what the user saw interactively is not a batch run, it is a different
experiment.*

The cause
---------
``pre_process_image`` **normalises internally** — ``img = img / img.max()``. It expects **raw
counts**, and it **divides**.

Batch called ``_normalize_to_float`` first, which does ``(x - min) / (max - min)`` — **it subtracts
the pedestal too.** The ``/max`` inside ``pre_process_image`` is then a **no-op**, and the two
callers hand the rolling ball genuinely different images::

    INTERACTIVE   img / max            ->  range **[0.425, 1.0]**
    BATCH         (img-min)/(max-min)  ->  range **[0.000, 1.0]**

**And the rolling ball is NOT scale-invariant.**
``skimage.restoration.rolling_ball`` rolls a ball in **(x, y, INTENSITY)**, and its ``radius``
applies to **all three axes.** Change the intensity range and the same radius fits the background
differently:

===============  ==========================================
path             mean of the background-subtracted image
===============  ==========================================
interactive      **0.0205**
batch (before)   **0.0493**   ← *2.4× more background removed*
batch (fixed)    **0.0205**   ← bit-for-bit identical
===============  ==========================================

And a WORSE one: the branch itself diverged
--------------------------------------------
``run_enhanced_rb_gaussian_bg_removal`` decides whether the input is *"already preprocessed"* with
``median(nonzero) < 0.05``. **That heuristic is scale-dependent, and batch changed the scale.**

On a **high-contrast image — a bright spot on a dim background, i.e. exactly a condensate image**:

===============  ================  ======================  ==============================
path             median            verdict                 processing applied
===============  ================  ======================  ==============================
INTERACTIVE      **403 counts**    not enhanced            **full rolling-ball removal**
BATCH            **0.030**         **"already enhanced"**  **soft suppression only**
===============  ================  ======================  ==============================

***Not a scale shift in one number — a different algorithm.***

What was NOT broken (verified, not assumed)
--------------------------------------------
* **Cellpose** — its default ``normalize=True`` percentile-rescales internally, and that is
  **invariant** under an affine ``(x-min)/(max-min)``. Verified through the full chain (raw →
  ``img_as_uint`` → percentile-norm): **max difference 1.85e-05.**
* **Multi-Otsu** — thresholds on the histogram's *shape*. Scale-invariant by construction.
* The recorded **parameters** — ``ball_radius``, ``window_size``, ``cell_diameter`` — are all saved
  correctly and replayed correctly. *The first hypothesis (that the measured lines were not being
  recorded) was wrong, and checking it took ten minutes.*
"""

import numpy as np
import pytest


def _synthetic_field(size=128, seed=0, contrast=300):
    """A realistic uint16 field: an object on a background with a gradient and a pedestal."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    field = (400
             + contrast * np.exp(-(((yy - size // 2) ** 2 + (xx - size // 2) ** 2)) / (2 * 15.0 ** 2))
             + 200 * np.exp(-yy / 40.0)
             + rng.normal(0, 20, (size, size)))
    return np.clip(field, 0, 65535).astype(np.uint16)


@pytest.mark.base
def test_the_rolling_ball_is_NOT_scale_invariant():
    """**The fact the whole bug rests on.** If this were false, the normalisation would be harmless.

    ``skimage.restoration.rolling_ball`` rolls a ball in **(x, y, intensity)**. Its ``radius``
    applies to **all three axes** — so a radius-30 ball on a [0,1] image is **30 units tall**,
    thirty times the entire intensity range, and it slides underneath everything.
    """
    from skimage.restoration import rolling_ball

    image = _synthetic_field().astype(np.float64)

    raw_subtracted = image - rolling_ball(image, radius=30)

    normalised = (image - image.min()) / (image.max() - image.min())
    normalised_subtracted = normalised - rolling_ball(normalised, radius=30)

    def _standardise(x):
        return (x - x.mean()) / (x.std() + 1e-12)

    correlation = float(np.corrcoef(_standardise(raw_subtracted).ravel(),
                                    _standardise(normalised_subtracted).ravel())[0, 1])

    assert correlation < 0.99, (
        f"the rolling ball appears scale-invariant (correlation {correlation:.4f}). If that were "
        f"true, batch's pre-normalisation would be harmless — and this whole fix would be "
        f"unnecessary. **Re-derive the bug before trusting this test.**"
    )


@pytest.mark.base
def test_batch_preprocessing_passes_RAW_COUNTS_not_a_normalised_image():
    """``pre_process_image`` divides by its own max. **It expects raw counts.**

    Pre-normalising subtracts the pedestal, and the internal ``/max`` is then a no-op — so the two
    callers give the rolling ball different images.
    """
    import ast
    import pathlib

    registry = (pathlib.Path(__file__).resolve().parents[1]
                / "src" / "pycat" / "batch_step_registry.py")
    source = registry.read_text(encoding='utf-8', errors='ignore')
    tree = ast.parse(source)

    # Every function that calls pre_process_image or a rolling-ball routine must NOT hand it
    # the output of _normalize_to_float.
    rolling_ball_users = ('pre_process_image', 'rb_gaussian_bg_removal',
                          'soft_foreground_suppression')

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(source, node) or ''

        if not any(user in body for user in rolling_ball_users):
            continue
        if '_normalize_to_float(' in body:
            offenders.append(node.name)

    assert not offenders, (
        f"these replay steps normalise the image and then feed it to a rolling-ball routine: "
        f"{offenders}\n\n"
        f"`_normalize_to_float` does (x-min)/(max-min) — it **subtracts the pedestal**. "
        f"`pre_process_image` divides by its own max and expects RAW COUNTS. The rolling ball's "
        f"radius has an INTENSITY component, so the two paths remove different amounts of "
        f"background. Use `_raw_counts`."
    )


@pytest.mark.base
def test_the_already_enhanced_HEURISTIC_sees_the_same_scale_in_both_paths():
    """**The branch itself diverged**, which is worse than a shifted number.

    ``median(nonzero) < 0.05`` decides whether to run the **full rolling-ball removal** or only
    **soft suppression**. On a high-contrast condensate image, batch's normalisation pushed the
    median below the threshold and it took the **wrong branch**.
    """
    # ── The condition that ACTUALLY diverges, found by measuring ────────────────
    #
    # A first version used the standard fixture (with a background gradient) at contrast 3000, and
    # its normalised median came out at **0.0588** — *above* the 0.05 threshold. **It did not
    # reproduce the bug**, and loosening the assertion to make it pass would have produced a test
    # that asserts nothing.
    #
    # The gradient keeps the background wide, which **raises** the normalised median. On a **flat**
    # background — which is what in-vitro condensate data has — the divergence appears at just
    # **3000 counts of contrast**:
    #
    #     contrast   raw median   normalised median   diverges?
    #     1000       405          0.0775              no
    #     **3000**   **406**      **0.0280**          **YES**
    #     6000       407          0.0144              YES
    #
    # **A bright fluorescent condensate on a dark background is exactly this.**
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:128, 0:128]
    image = np.clip(400
                    + 3000 * np.exp(-(((yy - 64) ** 2 + (xx - 64) ** 2)) / (2 * 10.0 ** 2))
                    + rng.normal(0, 20, (128, 128)), 0, 65535).astype(np.uint16).astype(np.float64)

    raw_median = float(np.median(image[image > 0]))
    assert raw_median >= 0.05, "the raw image must NOT look 'already enhanced'"

    normalised = (image - image.min()) / (image.max() - image.min())
    normalised_median = float(np.median(normalised[normalised > 0]))

    assert normalised_median < 0.05, (
        f"the normalised median is {normalised_median:.4f}. If it were ABOVE 0.05, the branch "
        f"would not diverge — and this test would be asserting something that cannot happen. "
        f"**Re-derive the bug.**"
    )

    # ...which is exactly why batch must not normalise before this heuristic runs.
    import pathlib
    registry = (pathlib.Path(__file__).resolve().parents[1]
                / "src" / "pycat" / "batch_step_registry.py")
    source = registry.read_text(encoding='utf-8', errors='ignore')

    start = source.find('def replay_background_removal')
    assert start > 0
    end = source.find('\ndef ', start + 1)
    body = source[start:end if end > 0 else len(source)]

    assert '_raw_counts(' in body, (
        "replay_background_removal must pass RAW COUNTS — the 'already enhanced' heuristic it "
        "runs is scale-dependent, and normalising first makes batch take a different branch from "
        "the GUI."
    )


@pytest.mark.base
def test_CELLPOSE_is_genuinely_unaffected():
    """**Not every normalisation is a bug, and saying so is part of the audit.**

    Cellpose's default ``normalize=True`` percentile-rescales internally, and a percentile
    transform is **invariant** under an affine ``(x-min)/(max-min)``. Verified end-to-end: max
    difference **1.85e-05**.
    """
    from skimage.util import img_as_uint

    image = _synthetic_field(contrast=3000)

    def _cellpose_normalisation(x):
        x = np.asarray(x, dtype=np.float64)
        low, high = np.percentile(x, [1, 99])
        return (x - low) / (high - low + 1e-12)

    interactive = _cellpose_normalisation(image)          # the GUI hands over the raw layer

    pre_normalised = ((image.astype(np.float32) - image.min())
                      / (image.max() - image.min() + 1e-8))
    batch = _cellpose_normalisation(img_as_uint(pre_normalised.astype(np.float64)))

    difference = float(np.abs(interactive - batch).max())

    assert difference < 1e-2, (
        f"cellpose's input differs by {difference:.4f} between the two paths. If this ever fails, "
        f"the pre-normalisation is NOT harmless for cellpose either, and replay_cellpose_"
        f"segmentation needs the same fix as the rolling-ball steps."
    )
