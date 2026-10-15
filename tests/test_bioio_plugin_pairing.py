"""**The BioIO plugins PyCAT pins read files against the bioio-base that bioio pulls** (Completion Spec D4).

``bioio>=3`` pulls ``bioio-base`` 3.x; the ome-tiff / tifffile / imageio plugins are 1.x and declare only
``bioio-base>=1.4``, so pip pairs a 1.x plugin with a 3.x base and nothing upstream says that works. It was
the one dependency claim the completion audit could not settle. Verified here by READING a file through each
plugin -- with PyCAT's tifffile/zarr shim installed, as every PyCAT open path has it (``pycat.file_io``
installs it on import; without it tifffile <= 2026.4.11 misreports zarr 3.4 as "zarr < 3").

CZI cannot be synthesised; ``realdata`` covers it when the corpus is present.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.base


def _read(path, reader_module):
    import importlib

    from bioio import BioImage

    import pycat.file_io  # noqa: F401  -- installs the tifffile/zarr shim, as every PyCAT open does
    reader = importlib.import_module(reader_module).Reader
    return BioImage(str(path), reader=reader).get_image_dask_data('YX', T=0, C=0, Z=0).compute()


def _image():
    return (np.arange(64 * 48, dtype=np.uint16).reshape(64, 48) * 7) % 4000


@pytest.mark.parametrize('reader_module', ['bioio_tifffile', 'bioio_ome_tiff'])
def test_a_tiff_reads_through_the_pinned_plugin(tmp_path, reader_module):
    pytest.importorskip(reader_module)
    import tifffile
    img = _image()
    path = tmp_path / 'img.ome.tif'
    tifffile.imwrite(path, img, ome=True, metadata={'axes': 'YX'})
    assert np.array_equal(_read(path, reader_module), img)


def test_a_png_reads_through_the_pinned_plugin(tmp_path):
    pytest.importorskip('bioio_imageio')
    import imageio.v3 as iio
    img = (_image() % 256).astype(np.uint8)
    path = tmp_path / 'img.png'
    iio.imwrite(path, img)
    assert np.array_equal(np.squeeze(_read(path, 'bioio_imageio')), img)


def test_the_installed_pairing_is_inside_the_declared_pins():
    """A pin that the tested environment does not satisfy is not a tested pin."""
    import importlib.metadata as md
    import pathlib
    import tomllib

    from packaging.requirements import Requirement
    deps = tomllib.loads((pathlib.Path(__file__).resolve().parents[1] / 'pyproject.toml')
                         .read_text(encoding='utf-8'))['project']['dependencies']
    outside = []
    for dep in deps:
        req = Requirement(dep)
        if not (req.name.startswith('bioio') or req.name == 'napari') or not req.specifier:
            continue
        try:
            version = md.version(req.name)
        except md.PackageNotFoundError:
            continue
        if not req.specifier.contains(version, prereleases=True):
            outside.append(f'{req.name} {version} not in {req.specifier}')
    assert not outside, outside
