"""**A Z-stack must behave the same whichever reader made it.**

PyCAT already had several Z/TZ wrapper families (the IMS `_ImsReader*`, `_ZarrZYX`/`_ZarrTZYX`,
the generic `_LazyArraySource` over a dask array). Adding a fourth, TIFF-only shape that behaves
*almost* like the others is the failure mode this file exists to prevent: downstream code —
segmentation, 3-D volume, measurement, scrubbing, brushing — must never have to know which reader
built the layer.

So these tests drive the **real** `_TiffPageStackZYX` / `_TiffPageStackTZYX` and the **real**
`_ImsReaderZYX` / `_ImsReaderTZYX` over the *same volume* and demand identical answers. The IMS
wrappers only need an object with `.shape` and `__getitem__`, so a numpy stand-in drives the
genuine IMS code with no `.ims` file and no `imaris_ims_file_reader` installed — this is a real
cross-reader comparison, not stub-vs-stub.

Context: before 1.6.71 a z-stack TIFF **did not load at all**. The Z/T+Z branches were dask-only,
BioIO reads TIFF pixels through tifffile's zarr store, and that store is broken on zarr 3.2 — so
the layer died with `zarr 3.2.1 < 3 is not supported` on the first plane read. Only the T branch
had ever been given the native tifffile cure.

pycat imports live inside the test bodies on purpose: `conftest.py`'s `pytest_ignore_collect` drops
any module whose module-scope imports name `pycat.file_io` when the GUI stack is absent, which
would silently delete this file from the headless `core` job.
"""

# Standard library imports
import os
import tempfile

# Third party imports
import numpy as np
import pytest


pytestmark = pytest.mark.core

_Z, _T, _H, _W = 6, 4, 16, 20


class _FakeImsReader:
    """The only surface `_ImsReader*` touches: `.shape` (T, C, Z, Y, X) and `__getitem__`."""

    def __init__(self, volume_tczyx):
        self._a = volume_tczyx
        self.shape = volume_tczyx.shape
        self.dtype = volume_tczyx.dtype

    def __getitem__(self, key):
        return self._a[key]


def _write_tiff(tmpdir, name, data, axes):
    import tifffile
    path = os.path.join(tmpdir, name)
    tifffile.imwrite(path, data, ome=True, metadata={'axes': axes})
    return path


def _zyx_pair(tmpdir):
    """The same (Z, Y, X) volume behind a TIFF wrapper and an IMS wrapper."""
    from pycat.file_io.lazy_sources import _TiffPageStackZYX
    from pycat.file_io.readers.ims_reader import _ImsReaderZYX

    vol = np.random.default_rng(7).integers(0, 4096, (_Z, _H, _W), dtype=np.uint16)
    path = _write_tiff(tmpdir, "z.ome.tif", vol, 'ZYX')

    tiff = _TiffPageStackZYX(path, _Z, _H, _W, vol.dtype, channel_idx=0, n_channels=1)
    ims = _ImsReaderZYX(_FakeImsReader(vol[None, None, ...]), 0, t=0)   # (T,C,Z,Y,X)
    return tiff, ims, vol


def _tzyx_pair(tmpdir):
    """The same (T, Z, Y, X) volume behind a TIFF wrapper and an IMS wrapper."""
    from pycat.file_io.lazy_sources import _TiffPageStackTZYX
    from pycat.file_io.readers.ims_reader import _ImsReaderTZYX

    vol = np.random.default_rng(8).integers(0, 4096, (_T, _Z, _H, _W), dtype=np.uint16)
    path = _write_tiff(tmpdir, "tz.ome.tif", vol, 'TZYX')

    tiff = _TiffPageStackTZYX(path, _T, _Z, _H, _W, vol.dtype, channel_idx=0, n_channels=1)
    ims = _ImsReaderTZYX(_FakeImsReader(vol[:, None, ...]), 0)          # (T,C,Z,Y,X)
    return tiff, ims, vol


# The index patterns napari and the analysis code actually use.
_ZYX_INDEX = [0, 2, _Z - 1,
              slice(1, 3), slice(None),
              (0,), (2, slice(4, 9), slice(3, 7)),
              (slice(None), slice(4, 9), slice(3, 7))]

_TZYX_INDEX = [0, _T - 1,
               (0, 0), (1, 2), (0, slice(None)), (slice(None), 0),
               (slice(0, 2), slice(1, 3)),
               (0, 0, slice(2, 8), slice(1, 5))]


def test_ZYX_tiff_and_ims_present_the_SAME_contract():
    """shape / ndim / dtype / len must not depend on which reader built the layer."""
    pytest.importorskip("tifffile")
    with tempfile.TemporaryDirectory() as tmp:
        tiff, ims, _ = _zyx_pair(tmp)
        try:
            assert tiff.shape == ims.shape, "ZYX shape differs between the TIFF and IMS readers"
            assert tiff.ndim == ims.ndim == 3
            assert tiff.dtype == ims.dtype == np.dtype('float32')
            assert len(tiff) == len(ims) == _Z
        finally:
            tiff.close()


def test_ZYX_tiff_and_ims_return_IDENTICAL_pixels_for_every_index_pattern():
    """The squeeze semantics are the subtle part: an integer Z-select must drop the Z axis in BOTH.
    A quietly different squeeze is what makes downstream code reader-dependent."""
    pytest.importorskip("tifffile")
    with tempfile.TemporaryDirectory() as tmp:
        tiff, ims, _ = _zyx_pair(tmp)
        try:
            for idx in _ZYX_INDEX:
                a, b = tiff[idx], ims[idx]
                assert a.shape == b.shape, f"ZYX shape disagreement at index {idx!r}: {a.shape} vs {b.shape}"
                assert np.array_equal(a, b), f"ZYX pixel disagreement at index {idx!r}"
        finally:
            tiff.close()


def test_TZYX_tiff_and_ims_present_the_SAME_contract():
    pytest.importorskip("tifffile")
    with tempfile.TemporaryDirectory() as tmp:
        tiff, ims, _ = _tzyx_pair(tmp)
        try:
            assert tiff.shape == ims.shape, "TZYX shape differs between the TIFF and IMS readers"
            assert tiff.ndim == ims.ndim == 4
            assert tiff.dtype == ims.dtype == np.dtype('float32')
            assert len(tiff) == len(ims) == _T
        finally:
            tiff.close()


def test_TZYX_tiff_and_ims_return_IDENTICAL_pixels_for_every_index_pattern():
    """Covers the T and Z select combinations, including the reverse-order squeeze that makes
    `arr[0, 0]` -> (Y, X) and `arr[0, :]` -> (Z, Y, X)."""
    pytest.importorskip("tifffile")
    with tempfile.TemporaryDirectory() as tmp:
        tiff, ims, _ = _tzyx_pair(tmp)
        try:
            for idx in _TZYX_INDEX:
                a, b = tiff[idx], ims[idx]
                assert a.shape == b.shape, f"TZYX shape disagreement at index {idx!r}: {a.shape} vs {b.shape}"
                assert np.array_equal(a, b), f"TZYX pixel disagreement at index {idx!r}"
        finally:
            tiff.close()


def test_a_ZYX_plane_is_BIT_IDENTICAL_to_the_direct_plane_reader():
    """**The floor.** A faster reader that returns different pixels is not a reader.

    Compared against `read_tiff_plane` — the one-shot reader — put through the same `[0, 1]`
    normalisation, because the wrapper normalises from the SOURCE dtype and `read_tiff_plane`
    returns raw counts. (Comparing to raw counts would be asserting the 1.6.x intensity bug.)
    """
    pytest.importorskip("tifffile")
    from pycat.file_io.stack_access import to_unit_float32
    from pycat.file_io.tiff_planes import read_tiff_plane

    with tempfile.TemporaryDirectory() as tmp:
        tiff, _ims, vol = _zyx_pair(tmp)
        path = os.path.join(tmp, "z.ome.tif")
        try:
            for z in range(_Z):
                direct = read_tiff_plane(path, t=0, c=0, z=z, n_channels=1, n_z=_Z)
                assert direct is not None, f"the direct plane reader declined at z={z}"
                expected = to_unit_float32(direct, vol.dtype)
                assert np.array_equal(tiff[z], expected), f"z={z} differs from a direct plane read"
                # ...and it is genuinely normalised, not float32-shaped raw counts.
                assert tiff[z].max() <= 1.0
        finally:
            tiff.close()


def test_the_page_map_follows_the_FILE_declared_axis_order_not_a_formula():
    """`_page_and_slice` folds over the axis order the file DECLARES. A hardcoded
    `frame = ((t * n_z) + z) * n_c + c` — the formula the spec quoted — is only the fallback for a
    file that declares no axes, and it silently reads the wrong plane from a Z-major file.

    A wrong plane is the worst failure mode here: it puts a real, plausible image on screen with
    nothing to indicate it is the wrong one.
    """
    pytest.importorskip("tifffile")
    from pycat.file_io.lazy_sources import _TiffPageStackTZYX
    from pycat.file_io.stack_access import to_unit_float32

    with tempfile.TemporaryDirectory() as tmp:
        # Stored Z-major on disk, and the file SAYS so.
        vol = np.random.default_rng(9).integers(0, 4096, (_Z, _T, _H, _W), dtype=np.uint16)
        path = _write_tiff(tmp, "zt.ome.tif", vol, 'ZTYX')

        w = _TiffPageStackTZYX(path, _T, _Z, _H, _W, vol.dtype, channel_idx=0, n_channels=1)
        try:
            for t in range(_T):
                for z in range(_Z):
                    assert np.array_equal(w[t, z], to_unit_float32(vol[z, t], vol.dtype)), (
                        f"(t={t}, z={z}) resolved to the wrong page — the reader assumed an axis "
                        f"order instead of reading the one the file declares")
        finally:
            w.close()


@pytest.mark.parametrize("which", ['zyx', 'tzyx'])
def test_neither_Z_wrapper_will_MATERIALIZE_itself(which):
    """`np.asarray(wrapper)` silently returning one plane is the bug that has bitten four times.
    Every other lazy wrapper refuses; these must too."""
    pytest.importorskip("tifffile")
    with tempfile.TemporaryDirectory() as tmp:
        tiff, _ims, _ = _zyx_pair(tmp) if which == 'zyx' else _tzyx_pair(tmp)
        try:
            with pytest.raises(RuntimeError, match="implicit full-stack read"):
                np.asarray(tiff)
        finally:
            tiff.close()


def test_a_TIFF_z_step_reaches_the_voxel_volume_path_and_is_NaN_when_UNKNOWN():
    """**Physical consistency, not just array shape.** A ZYX TIFF and a ZYX IMS of the same
    specimen must give the same voxel volume.

    Voxel volume is computed from `pixel_size.z_step_um(repository)`, which reads
    `file_metadata['common']['z_step_um']` — written from the reader's `physical_pixel_sizes.Z`.
    That path is format-agnostic, so pinning it for TIFF pins the agreement.

    Unknown stays **NaN**, never a guessed 1.0: an assumed-isotropic voxel is a wrong number that
    looks like a right one. (`z_step_um_or_default` is the opt-in that warns.)
    """
    pytest.importorskip("tifffile")
    pytest.importorskip("bioio")
    import tifffile
    from bioio import BioImage
    from pycat.file_io.metadata_extract import extract_metadata
    from pycat.utils.pixel_size import z_step_um

    with tempfile.TemporaryDirectory() as tmp:
        vol = np.random.default_rng(3).integers(0, 4096, (_Z, _H, _W), dtype=np.uint16)

        known = os.path.join(tmp, "known.ome.tif")
        tifffile.imwrite(known, vol, ome=True,
                         metadata={'axes': 'ZYX', 'PhysicalSizeZ': 0.30,
                                   'PhysicalSizeX': 0.065, 'PhysicalSizeY': 0.065})
        repo = {'file_metadata': extract_metadata(known, image=BioImage(known))}
        assert z_step_um(repo) == pytest.approx(0.30), (
            "an OME-TIFF's declared PhysicalSizeZ did not reach the voxel-volume path, so a TIFF "
            "z-stack would measure a different volume than an IMS one of the same specimen")

        # No declared z-step -> honest unknown.
        silent = os.path.join(tmp, "silent.ome.tif")
        tifffile.imwrite(silent, vol, ome=True, metadata={'axes': 'ZYX'})
        repo2 = {'file_metadata': extract_metadata(silent, image=BioImage(silent))}
        assert np.isnan(z_step_um(repo2)), (
            "an unknown z-step must be NaN, never a guessed 1.0 — a silently isotropic voxel is a "
            "wrong number that looks like a right one")
