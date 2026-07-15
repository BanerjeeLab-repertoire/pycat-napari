"""
tifffile ↔ zarr 3.2 compatibility shim.

── The problem ──────────────────────────────────────────────────────────────────────────
``tifffile`` (≤ 2026.4.11) builds its zarr store — the thing ``bioio-tifffile`` and
``bioio-ome-tiff`` call for lazy/dask reads — with, at ``tifffile/zarr.py`` import time::

    from zarr.core.chunk_grids import RegularChunkGrid          # (1) import
    ...
    RegularChunkGrid(chunk_shape=zarray.chunks)                 # (2) construct

**zarr 3.2 restructured that class away.** ``zarr.core.chunk_grids`` now exposes only a base
``ChunkGrid``; the concrete regular-grid type moved/renamed to
``zarr.core.metadata.v3.RegularChunkGridMetadata``. Import (1) raises ``ImportError``, tifffile
catches it, and re-raises the **misleading**::

    ValueError: zarr 3.2.1 < 3 is not supported

(3.2.1 is obviously not < 3 — tifffile blames the version for *any* ImportError out of its zarr-3
module. The real cause is the one missing symbol.)

The effect in PyCAT: any TIFF/CZI that falls to the BioIO dask path — **multi-channel TIFFs**
(where ``_TiffPageStack`` declines on the page-count check) and **all CZI** (no tifffile fast path
at all) — fails to load lazily.

── Why a shim and not a version bump ────────────────────────────────────────────────────
The tifffile release that fixes this (2026.5.2, ``Require zarr>=3.2.0``) also *drops numpy 2.0*
(SPEC0) and lands a wave of breaking changes (``TiffFile.series`` list→callable, ``aszarr``
deprecation, single-char axis codes). Bumping tifffile would drag ``numpy>=2.1`` through the entire
scientific stack (torch, cellpose, scikit-image, cupy). That is the highest-blast-radius change in
the environment — not acceptable to fix a single missing symbol.

── The fix ──────────────────────────────────────────────────────────────────────────────
Supply the missing name **before** ``tifffile.zarr`` is first imported. In zarr 3.2.1,
``RegularChunkGridMetadata.__init__`` has signature ``(*, chunk_shape: tuple[int, ...])`` — exactly
what tifffile constructs with — and ``BasicIndexer`` accepts it as its ``chunk_grid`` argument.
Verified end-to-end (aszarr → open → read a real chunk) on zarr 3.2.1 / tifffile 2026.4.11.

So the shim aliases the metadata class into the name tifffile looks for. This is the same
capability-not-class principle as ``zarr_compat.py``: reconcile a renamed symbol so the layer above
keeps working across zarr versions, without pinning anything.

**Idempotent and defensive.** If the symbol is already present (older zarr, or a future tifffile
that no longer needs it), the shim does nothing. If zarr's internals have moved further than we know
how to reconcile, it does nothing and lets the original (clear) failure surface rather than masking
it with a broken stand-in.
"""

from __future__ import annotations


def install_tifffile_zarr_shim() -> bool:
    """Ensure ``zarr.core.chunk_grids.RegularChunkGrid`` exists so tifffile's zarr store imports.

    Returns True if the name is present after this call (either already there, or we installed it),
    False if we could not supply it (in which case tifffile's own error is left to surface).

    Safe to call many times and safe to call when zarr/tifffile are absent.
    """
    try:
        import zarr.core.chunk_grids as _cg
    except Exception:
        # zarr not installed or restructured beyond this module path — nothing to do.
        return False

    # Already present (older zarr, or a tifffile/zarr combo that doesn't need the shim).
    if hasattr(_cg, "RegularChunkGrid"):
        return True

    # Locate the replacement class introduced by the zarr 3.2 restructure.
    stand_in = None
    for modname in (
        "zarr.core.chunk_grids",
        "zarr.core.metadata.v3",
        "zarr.core.array",
    ):
        try:
            import importlib
            m = importlib.import_module(modname)
        except Exception:
            continue
        cand = getattr(m, "RegularChunkGridMetadata", None)
        if cand is not None:
            stand_in = cand
            break

    if stand_in is None:
        # Could not find a compatible class — do NOT install a broken stand-in. Let tifffile's
        # own (clear, if mislabelled) error surface instead of masking it with something that
        # imports but fails at construction/read time.
        return False

    # Install the alias where tifffile looks for it. tifffile does
    # ``from zarr.core.chunk_grids import RegularChunkGrid`` and later
    # ``RegularChunkGrid(chunk_shape=...)``; RegularChunkGridMetadata's constructor is
    # ``(*, chunk_shape=...)`` and is accepted by BasicIndexer as its chunk_grid — verified
    # end-to-end on zarr 3.2.1.
    _cg.RegularChunkGrid = stand_in
    return True
