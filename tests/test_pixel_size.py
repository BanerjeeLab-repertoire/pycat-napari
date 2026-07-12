"""
The pixel size must never silently default to 1.0 µm/px.

``_mpx()`` was defined **ten times** across the codebase, in two forms, and **both silently
defaulted to 1.0** — eight UI copies via ``.get('microns_per_pixel_sq', 1.0)`` and two in
``_tools`` modules via ``except Exception: return 1.0``.

The caller could not distinguish *"the pixel size is 1.0 µm"* from *"the lookup failed"*, and
1.0 is a perfectly plausible pixel size, so nothing looked wrong. **It is not a harmless
default.** Every length and every area is scaled by it:

=========================  =================  ==================  ==========
true µm/px                 true area (µm²)    with fallback 1.0   error
=========================  =================  ==================  ==========
0.0264 (Zeiss 63× oil)     0.348              500.0               **1435×**
0.1 (typical 100×)         5.000              500.0               100×
0.67 (the bead videos)     224.45             500.0               2×
=========================  =================  ==================  ==========

**A 1435× overestimate of every area on a Zeiss 63×, reported as an entirely normal number.**
Same failure as ``estimate_psf_sigma`` returning 1.0 on any exception (1.5.437), duplicated ten
times.
"""

import numpy as np
import pytest


@pytest.mark.core
@pytest.mark.parametrize("repository", [
    {},                                        # the key is absent
    {"microns_per_pixel_sq": None},            # present but unset
    {"microns_per_pixel_sq": 0.0},             # zero
    {"microns_per_pixel_sq": -1.0},            # negative
    {"microns_per_pixel_sq": "abc"},           # not a number
    None,                                      # no repository at all
])
def test_unknown_pixel_size_is_nan_not_one(repository):
    """An unknown pixel size must be NaN — visible — not a plausible-looking 1.0."""
    ps = pytest.importorskip("pycat.utils.pixel_size")

    value = ps.pixel_size_um(repository, context="test")

    assert np.isnan(value), (
        f"pixel_size_um({repository!r}) returned {value!r}. An unknown pixel size must be "
        f"NaN, so that an area computed from it is visibly NaN rather than wrong by three "
        f"orders of magnitude. Returning 1.0 silently asserts '1 micron per pixel' — on a "
        f"Zeiss 63x (0.0264 µm/px) that is a 1435x overestimate of every area, and it looks "
        f"entirely normal."
    )


@pytest.mark.core
def test_known_pixel_size_is_returned():
    """A valid pixel size must come back exactly — the guard must not cry wolf."""
    ps = pytest.importorskip("pycat.utils.pixel_size")

    # The repository stores the SQUARED pixel size (µm² per pixel).
    value = ps.pixel_size_um({"microns_per_pixel_sq": 0.0264 ** 2}, context="test")
    assert value == pytest.approx(0.0264, rel=1e-6)


@pytest.mark.core
def test_explicit_default_still_warns():
    """`pixel_size_um_or_default` may return a number, but must say that it did."""
    ps = pytest.importorskip("pycat.utils.pixel_size")

    messages = []
    real = ps.napari_show_warning
    ps.napari_show_warning = lambda msg, *a, **k: messages.append(msg)
    ps._WARNED.clear()
    try:
        value = ps.pixel_size_um_or_default({}, default=1.0, context="test")
    finally:
        ps.napari_show_warning = real
        ps._WARNED.clear()

    assert value == 1.0
    assert messages, (
        "pixel_size_um_or_default fell back to 1.0 and said NOTHING. The fallback is "
        "permitted — silently taking it is not. The output is in PIXEL units, and the user "
        "must be told, or they will report it as microns."
    )
    joined = " ".join(messages).lower()
    assert "pixel" in joined, f"the warning must say the output is in pixel units: {messages}"
