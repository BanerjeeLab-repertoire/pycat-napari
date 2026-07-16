"""
**zarr 3 renamed the one class PyCAT depends on**, and that rename is what makes the BioIO
migration a chain rather than a swap.

From zarr's own migration guide::

    # Before (v2)
    - from zarr import MemoryStore, DirectoryStore
    + from zarr.storage import MemoryStore, LocalStore   # LocalStore replaces DirectoryStore

The chain:

* ``aicsimageio`` is **frozen in maintenance mode** and pins ``zarr<2.16``
* BioIO's plugins want **zarr 3**
* PyCAT's lazy loaders were written against **zarr 2**

***So the reader cannot be replaced until the store class is version-agnostic.***

Why a CAPABILITY check and not a CLASS check
---------------------------------------------
PyCAT asks ``DirectoryStore`` exactly one question, in two places:

    *"Is this zarr backed by a directory on disk, and if so, where?"*

**That is a capability, not a class.** Written as ``isinstance(store, DirectoryStore)`` it breaks
the day the class is renamed — *which is the day the migration needs it.*

And there is a **second, quieter trap**: zarr 3's ``LocalStore`` exposes the path as ``.root``
(a ``Path``), not ``.path`` (a ``str``). **So even after fixing the class name, a bare
``store.path`` would return ``None`` on zarr 3 — silently** — and PyCAT would copy a stack it did
not need to copy. *A silent fallback that merely wastes time is still a silent fallback, and it
would have been very hard to find.*

What this test can and CANNOT prove
------------------------------------
**zarr is not installed in the sandbox this was written in.** These tests prove the **logic**
handles both APIs, using fakes shaped like the real store classes.

***They do not prove the integration.*** That has to run against a real zarr — and it does, in CI,
where zarr IS installed. If this file passes in CI on zarr 2 today and on zarr 3 after the pin
moves, the port is real.
"""

import sys
import types
import pathlib

import pytest


def _fake_zarr(major):
    """A zarr module shaped like the real one — v2 or v3."""
    zarr = types.ModuleType('zarr')
    zarr.__version__ = f'{major}.0.0'

    storage = types.ModuleType('zarr.storage')

    if major == 2:
        class DirectoryStore:
            def __init__(self, path):
                self.path = str(path)              # zarr 2: `.path`, a str
        storage.DirectoryStore = DirectoryStore
    else:
        class LocalStore:
            def __init__(self, path):
                self.root = pathlib.Path(path)     # zarr 3: `.root`, a Path
        storage.LocalStore = LocalStore
        # DirectoryStore does NOT exist in zarr 3.

    class MemoryStore:
        pass

    storage.MemoryStore = MemoryStore
    zarr.storage = storage

    return zarr, storage


def _reload_compat(zarr, storage):
    for name in [k for k in sys.modules if k.startswith('zarr')
                 or k.endswith('zarr_compat')]:
        del sys.modules[name]

    sys.modules['zarr'] = zarr
    sys.modules['zarr.storage'] = storage

    from pycat.file_io import zarr_compat
    return zarr_compat


@pytest.mark.core
@pytest.mark.parametrize("major,attribute", [(2, 'path'), (3, 'root')])
def test_the_store_path_is_found_on_BOTH_zarr_2_and_zarr_3(major, attribute):
    """**zarr 2 puts the path on ``.path``. zarr 3 puts it on ``.root``.**

    A check that reads only one of them returns ``None`` on the other — *silently* — and PyCAT
    copies a stack it did not need to copy.
    """
    saved = {k: sys.modules.get(k) for k in ('zarr', 'zarr.storage')}
    try:
        zarr, storage = _fake_zarr(major)
        compat = _reload_compat(zarr, storage)

        store_class = (storage.DirectoryStore if major == 2 else storage.LocalStore)
        array = types.SimpleNamespace(store=store_class('/tmp/data.zarr'))

        found = compat.store_path(array)

        # Compare as PATHS, not raw strings. zarr 3's LocalStore exposes `.root` as a ``Path``, and
        # ``store_path`` returns ``str(root)`` — whose separator is platform-dependent
        # (``\tmp\data.zarr`` on Windows). A literal ``== '/tmp/data.zarr'`` check would fail on
        # Windows while the path is in fact correct; ``pathlib`` equality treats ``/`` and ``\`` as
        # the same separator, so this asserts the capability across platforms.
        assert found is not None and pathlib.Path(found) == pathlib.Path('/tmp/data.zarr'), (
            f"on zarr {major} the path lives on `.{attribute}` — and it was not found. "
            f"got {found!r}"
        )
        assert compat.is_filesystem_backed(array) is True

    finally:
        for name in [k for k in sys.modules if k.startswith('zarr')
                     or k.endswith('zarr_compat')]:
            del sys.modules[name]
        for key, value in saved.items():
            if value is not None:
                sys.modules[key] = value


@pytest.mark.core
@pytest.mark.parametrize("major", [2, 3])
def test_an_IN_MEMORY_zarr_is_not_reported_as_a_file_on_disk(major):
    """**A guard with no discrimination is a guard that says yes to everything.**

    The whole point of the question is to tell a filesystem-backed stack from one that is not.
    """
    saved = {k: sys.modules.get(k) for k in ('zarr', 'zarr.storage')}
    try:
        zarr, storage = _fake_zarr(major)
        compat = _reload_compat(zarr, storage)

        array = types.SimpleNamespace(store=storage.MemoryStore())

        assert compat.store_path(array) is None
        assert compat.is_filesystem_backed(array) is False

    finally:
        for name in [k for k in sys.modules if k.startswith('zarr')
                     or k.endswith('zarr_compat')]:
            del sys.modules[name]
        for key, value in saved.items():
            if value is not None:
                sys.modules[key] = value


@pytest.mark.core
def test_NOTHING_still_names_DirectoryStore_directly():
    """**A single ``isinstance(store, DirectoryStore)`` left behind is a line that breaks on
    zarr 3** — and it breaks *silently*, by returning ``None``.
    """
    import ast

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        if path.name == 'zarr_compat.py':
            continue        # the compat layer is where the class names belong

        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == 'DirectoryStore':
                offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "these lines still name `DirectoryStore` directly:\n  " + "\n  ".join(offenders)
        + "\n\n**zarr 3 renamed it to `LocalStore`.** Use `pycat.file_io.zarr_compat.store_path`, "
          "which asks the CAPABILITY question and works on both."
    )
