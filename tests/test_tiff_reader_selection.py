"""
**A user opened an ordinary TIFF and PyCAT printed a parse failure naming their own file.**

Reported against 1.6.17. Opening ``In Cell 8-DAPI.tif``::

    Attempted file (In Cell 8-DAPI.tif) load with reader:
    <class 'bioio_ome_tiff.reader.Reader'> failed with error:
    bioio-ome-tiff does not support the image ... Failed to parse XML for the provided
    file. Error: syntax error: line 1, column 0

**And then the file opened fine.** ``P=1 T=1 C=1 Z=1 → 2D``.

``BioImage(path)`` with no ``reader=`` runs BioIO's **plugin auto-selection**: it tries
``bioio-ome-tiff`` first, that plugin goes looking for OME-XML, **a plain microscope TIFF has
none**, and it raises. BioIO catches it, prints the attempt, and falls through to
``bioio-tifffile``, which works.

*So the error is BioIO's, it is not fatal, and the load succeeds.* **But the user has no way to know
any of that.** It reads exactly like a corrupt file, and it names their image. ***A scientist seeing
that goes looking at their microscope, or at their data.*** That is the same cost as the
``'_TIFF' object has no attribute 'RESUNIT'`` message this codebase already carries a startup check
for — a message that sends people to debug the wrong thing entirely.

── Why ``bioio-tifffile`` is right for BOTH plain and OME TIFF ───────────────────────────

It wraps ``tifffile``, which reads both. And **PyCAT does not take TIFF pixels from BioIO at all** —
``tiff_planes.read_tiff_plane`` seeks the page directly, precisely because ``bioio-ome-tiff`` reads
through ``tif.aszarr()``, which is broken on zarr 3.2.

*BioIO is only supplying dimensions, scenes, channel names and pixel size for TIFF, and
``bioio-tifffile`` supplies all of them. The OME plugin was never on the pixel path. It was only
ever a noisy first guess.*
"""

import sys
import types

import pytest

from pycat.file_io.image_reader import _reader_kwargs_for


@pytest.fixture
def a_fake_tifffile_plugin(monkeypatch):
    """Stand in for ``bioio-tifffile``, which is not installed in the sandbox.

    The thing under test is the **selection** — *which* reader PyCAT asks BioIO for — not BioIO.
    """
    class Reader:
        pass

    package = types.ModuleType('bioio_tifffile')
    submodule = types.ModuleType('bioio_tifffile.reader')
    package.Reader = Reader
    submodule.Reader = Reader

    monkeypatch.setitem(sys.modules, 'bioio_tifffile', package)
    monkeypatch.setitem(sys.modules, 'bioio_tifffile.reader', submodule)
    return Reader


@pytest.mark.core
@pytest.mark.parametrize('filename', ['In Cell 8-DAPI.tif', 'beads.tiff', 'stack.ome.tif'])
def test_a_TIFF_is_PINNED_to_the_tifffile_reader(filename, a_fake_tifffile_plugin):
    """**Do not let BioIO guess.** Guessing tries the OME plugin first and prints a parse error."""
    selected = _reader_kwargs_for(filename, {}).get('reader')

    assert selected is a_fake_tifffile_plugin, (
        f"`{filename}` was not pinned to the tifffile reader.\n\n"
        "BioIO will auto-probe, try `bioio-ome-tiff` first, fail to find OME-XML, and **print a "
        "parse error naming the user's own file** — before quietly succeeding with a different "
        "reader. The user cannot tell that from a corrupt image."
    )


@pytest.mark.core
@pytest.mark.parametrize('filename', ['image.czi', 'volume.ims', 'movie.nd2'])
def test_a_NON_TIFF_is_left_for_BioIO_to_choose(filename, a_fake_tifffile_plugin):
    """The pin is for TIFF only. Everything else keeps BioIO's own plugin selection."""
    assert 'reader' not in _reader_kwargs_for(filename, {}), (
        f"`{filename}` was pinned to the TIFF reader, which cannot read it."
    )


@pytest.mark.core
def test_an_EXPLICIT_reader_from_the_caller_WINS(a_fake_tifffile_plugin):
    """A caller that says which reader it wants is not overridden. *An explicit request is data.*"""
    class CallersOwnReader:
        pass

    selected = _reader_kwargs_for('x.tif', {'reader': CallersOwnReader})['reader']

    assert selected is CallersOwnReader, "the caller's explicit reader was overridden"


@pytest.mark.core
def test_a_MISSING_plugin_falls_back_to_BioIOs_probe_rather_than_failing(monkeypatch):
    """**A noisy load beats no load.**

    ``bioio-tifffile`` is a declared dependency, but if it is genuinely absent — or the plugin moves
    its ``Reader`` in a point release — PyCAT must fall back to the auto-probe, not raise.
    """
    monkeypatch.setitem(sys.modules, 'bioio_tifffile', None)
    monkeypatch.setitem(sys.modules, 'bioio_tifffile.reader', None)

    kwargs = _reader_kwargs_for('x.tif', {})

    assert 'reader' not in kwargs, (
        "with the plugin missing, PyCAT must let BioIO probe as before — a noisy load beats no load"
    )


@pytest.mark.core
def test_the_pin_does_NOT_disable_the_reader_CACHE():
    """*The cache key is built from the CALLER's kwargs, before the pin is applied.*

    The cache is bypassed when a caller passes options — deliberately, because a caller wanting a
    reader built their way must not be handed a differently-configured one. **The pin is added
    internally, at construction**, so it must not look like a caller option and switch the cache
    off for every TIFF PyCAT opens.
    """
    import pycat.file_io.image_reader as reader_module

    assert reader_module._cache_key('pyproject.toml') is not None, (
        "the cache key could not be built at all — this test cannot prove anything"
    )

    source = __import__('inspect').getsource(reader_module.open_image)
    assert '_key = _cache_key(path) if not kwargs else None' in source, (
        "the cache key is no longer derived from the CALLER's kwargs.\n\n"
        "If it is built from the *expanded* kwargs instead, the internally-added `reader=` pin "
        "will look like a caller option and **disable the reader cache for every TIFF** — "
        "reopening the same file three to four times per drag-and-drop."
    )
