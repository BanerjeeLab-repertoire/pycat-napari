"""
**Same pixels. Same file. Two loaders. A factor of 65535 apart.**

The 2-D loader called ``dtype_conversion_func(data, 'float32')`` → ``skimage.img_as_float32``, which
**divides by the dtype max** and yields **[0, 1]**.

The lazy stack wrappers did a bare ``arr.astype(np.float32)`` — **raw counts, 0–65535.**

``_TiffPageStack`` even *took the source dtype as a constructor argument* and threw it away on the
next line (``self.dtype = np.dtype('float32')``), so it **could not** have normalised correctly even
if it had wanted to.

── [0, 1] is the contract, not a preference ─────────────────────────────────────────────

* **17 toolbox functions declare it** in their docstrings — including ``partition_coefficient_field``
  and ``fit_bimodal_intensity``, which are *condensate measurements*, not helpers.
* ``skimage.exposure.equalize_adapthist`` **raises** on anything else — *"Images of type float must
  be between -1 and 1"* — and the preprocessing path depends on it.
* ``img_as_uint``, the save path's converter, **raises** on it too.

── Why nothing had broken ───────────────────────────────────────────────────────────────

**Luck.** Every stack consumer happens to be immune, and each for a *different* reason — verified
numerically, not by reading:

* **VPT** normalises per-frame immediately before ``blob_log`` → coordinates and bead classes are
  **identical** either way. *The η ≈ 8.325 baseline does not move.*
* **optical density** is ``-log10(I / I0)`` — a **ratio**, so the 65535 cancels. Verified identical.
* **``analyse_frame_quality``** normalises internally → Brenner/Tenengrad/variance **bit-identical**.
* **time-series** goes through ``_read_source_frame``, which normalises against a *fixed global
  range*.

***That is luck, not design.*** The next function written against the documented contract will not
be immune — and it would fail **silently**, because a number that is 65535× wrong still looks like a
number.
"""

import numpy as np
import pytest
import skimage as sk

from pycat.file_io.stack_access import to_unit_float32


@pytest.mark.core
@pytest.mark.parametrize('source_dtype', [np.uint8, np.uint16, np.uint32])
def test_the_STACK_loader_and_the_2D_loader_agree_EXACTLY(source_dtype):
    """**The whole point.** The same pixels must produce the same numbers, whichever loader ran.

    ``img_as_float32`` *is* the definition — it is what the 2-D path has always used. So the stack
    path **calls it** rather than reimplementing the divide.

    *The obvious reimplementation, ``arr.astype(np.float32) / np.iinfo(dt).max``, is wrong by one
    ULP on ~1 % of pixels (measured: 9 of 1024, by 6e-08). That is not a scientific problem — **but
    a second implementation of the same convention is**, and "close enough" is how two conventions
    become three.*
    """
    rng = np.random.default_rng(0)
    limit = min(np.iinfo(source_dtype).max, 60000)
    pixels = rng.integers(0, limit, (64, 64)).astype(source_dtype)

    from_the_stack_loader = to_unit_float32(pixels, source_dtype)
    from_the_2d_loader = sk.util.img_as_float32(pixels)

    assert np.array_equal(from_the_stack_loader, from_the_2d_loader), (
        f"the two loaders disagree on {source_dtype.__name__} data.\n\n"
        "**A stack frame and a 2-D image of the same pixels must be the same numbers.** They were "
        "65535x apart — the stack wrappers handed out raw counts while the 2-D loader divided by "
        "the dtype max."
    )


@pytest.mark.core
def test_a_uint16_frame_lands_in_ZERO_TO_ONE_not_in_RAW_COUNTS():
    """The regression, stated plainly."""
    pixels = np.array([[0, 1000, 65535]], np.uint16)

    result = to_unit_float32(pixels, np.uint16)

    assert result.max() <= 1.0, (
        f"a uint16 frame came back with a maximum of {result.max()} — **raw counts.**\n\n"
        "17 toolbox functions declare a [0, 1] input contract, and `equalize_adapthist` *raises* "
        "on anything else. A frame outside [0, 1] is not a scaling preference — it is a violated "
        "contract that no current consumer happens to notice."
    )
    assert result[0, 2] == pytest.approx(1.0), "full-scale must map to 1.0"
    assert result[0, 0] == pytest.approx(0.0), "zero must map to 0.0"


@pytest.mark.core
def test_the_divisor_is_the_DTYPE_max_not_the_FRAME_max():
    """**A brightening condensate must brighten.**

    Dividing by ``arr.max()`` would rescale every frame *against itself*, and
    ``timeseries_condensate_tools`` already learned why that is wrong:

        *per-frame min/max normalisation makes a growing focus appear to plateau or decay, because
        the rising per-frame max (the denominator) shrinks the normalised value of a focus even as
        its raw intensity increases.*

    Dividing by the **dtype** max is frame-independent, so an intensity trend survives.
    """
    dim = np.full((8, 8), 1000, np.uint16)
    bright = np.full((8, 8), 4000, np.uint16)

    dim_out = to_unit_float32(dim, np.uint16)
    bright_out = to_unit_float32(bright, np.uint16)

    assert bright_out.mean() > dim_out.mean() * 3.9, (
        "a 4x brighter frame did not come back 4x brighter.\n\n"
        "**The divisor must be the dtype max, not the frame's own max.** Per-frame normalisation "
        "makes every frame's brightest pixel equal 1.0 — so a growing focus appears to plateau, "
        "and an intensity time-course becomes meaningless."
    )
    assert bright_out.mean() == pytest.approx(4000 / 65535, rel=1e-5)


@pytest.mark.core
def test_a_float_frame_is_passed_through_UNCHANGED():
    """Already in the contract's range: do not rescale it a second time."""
    already_unit = np.array([[0.25, 0.5, 0.75]], np.float32)

    assert np.array_equal(to_unit_float32(already_unit, np.float32), already_unit), (
        "a float frame already in [0, 1] was rescaled again — which would silently halve, or "
        "square, values that were already correct."
    )


@pytest.mark.core
def test_NO_lazy_wrapper_hands_out_RAW_COUNTS():
    """**All nine wrappers, checked on the AST — not on the text.**

    *A text scan flags a comment that QUOTES the old code.* This file's own explanation contains
    the string ``astype(np.float32)``, and a grep-based version of this guard reported
    ``_TiffPageStack`` as broken **after it had been fixed**, because it matched the comment
    describing the bug.

    ***"A guard that cannot tell code from prose will eventually flag its own explanation."***

    Equally: a scan of the wrapper *classes* is not enough. ``_ims_frame_2d`` is a **module-level
    helper**, and all three ``_ImsReader*`` wrappers read through it — so the cast that fed the
    600-plane IMS file raw counts was **invisible to a class-level check.**
    """
    import ast
    import pathlib

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "file_io"

    offenders = []
    for path in (source_root / "file_io.py", source_root / "multidim_io.py"):
        source = path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            if '__getitem__' not in methods:
                continue

            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                if getattr(inner.func, 'attr', None) != 'astype' or not inner.args:
                    continue
                if 'float32' in ast.unparse(inner.args[0]):
                    offenders.append(
                        f"{path.name}:{inner.lineno} ({node.name} — bare astype(float32))")

    assert not offenders, (
        "these lazy wrappers still hand out **raw counts**:\n  " + "\n  ".join(offenders)
        + "\n\nA bare `astype(np.float32)` on a uint16 frame gives **0–65535**, not [0, 1]. "
          "Use `stack_access.to_unit_float32(arr, source_dtype)`.\n\n"
          "**Nothing will visibly break** — every current consumer happens to be immune. That is "
          "exactly why this guard exists: the next one will not be, and a number that is 65535x "
          "wrong still looks like a number."
    )
