"""
**One place that knows which zarr is installed.**

zarr 3 renamed the one class PyCAT depends on. From the migration guide:

    # Before (v2)
    - from zarr import MemoryStore, DirectoryStore
    + from zarr.storage import MemoryStore, LocalStore   # LocalStore replaces DirectoryStore

That single rename is what makes the whole BioIO migration a chain rather than a swap:

* ``aicsimageio`` is **frozen in maintenance mode** and pins ``zarr<2.16``
* BioIO's plugins want **zarr 3**
* PyCAT's lazy loaders were written against **zarr 2**

**So the reader cannot be replaced until the store class is version-agnostic** — and that is all
this file does.

Why a capability check and not a class check
--------------------------------------------
PyCAT asks ``DirectoryStore`` exactly one question, in two places:

    *"Is this zarr backed by a directory on disk, and if so, where?"*

**That is a capability, not a class.** Written as ``isinstance(store, zarr.storage.DirectoryStore)``
it breaks the day the class is renamed — which is the day the migration needs it most. Written as
*"does this store have a filesystem path?"* it works on **zarr 2 and zarr 3 alike**, and it keeps
working when zarr 4 renames it again.

That matters for the sequencing: this ships **while ``zarr<3`` is still pinned**, under the existing
test suite, and nothing changes. The pin moves later, and this file is already ready for it.

**The rest of the surface needs nothing.** ``zarr.open`` — which PyCAT calls 25 times — is part of
the primary API the guide promises stays compatible. It was checked, not assumed.
"""

from __future__ import annotations


def zarr_major_version() -> int:
    """The installed zarr major version. ``0`` if zarr is absent or unreadable."""
    try:
        import zarr
        return int(str(zarr.__version__).split('.')[0])
    except Exception:
        return 0


def _filesystem_store_classes():
    """Every class that means *"a zarr backed by a directory on disk"* — in whichever zarr is here.

    ``DirectoryStore`` in zarr 2. ``LocalStore`` in zarr 3. Both, if some future version keeps an
    alias. **Never an error**: a store class that does not exist is simply not in the tuple.
    """
    classes = []

    try:
        import zarr.storage as storage
    except Exception:
        return tuple()

    for name in ('DirectoryStore', 'LocalStore', 'NestedDirectoryStore'):
        candidate = getattr(storage, name, None)
        if isinstance(candidate, type):
            classes.append(candidate)

    return tuple(classes)


def store_path(zarr_array):
    """**Where does this zarr live on disk?** ``None`` if it is not filesystem-backed.

    ── The question PyCAT actually asks, in the form that survives a rename ────

    Both call sites did::

        if isinstance(store, zarr.storage.DirectoryStore):
            return store.path

    which is a **class** check standing in for a **capability** question. It breaks the moment zarr
    renames the class — *which is exactly what zarr 3 did, and exactly when the migration needs it.*

    zarr 3's ``LocalStore`` exposes the path as ``.root`` (a ``Path``), not ``.path`` (a ``str``).
    So even after swapping the class name, a bare ``store.path`` would return ``None`` on zarr 3 —
    **silently**, and PyCAT would fall back to copying a stack it did not need to copy. *A silent
    fallback that merely wastes time is still a silent fallback, and it would have been very hard
    to find.*
    """
    store = getattr(zarr_array, 'store', None)
    if store is None:
        return None

    classes = _filesystem_store_classes()
    if classes and not isinstance(store, classes):
        return None

    # zarr 2: `.path` is a str. zarr 3: `.root` is a Path.
    for attribute in ('path', 'root'):
        value = getattr(store, attribute, None)
        if value:
            return str(value)

    return None


def is_filesystem_backed(zarr_array) -> bool:
    """Is this zarr a directory on disk, rather than memory or a remote store?"""
    return store_path(zarr_array) is not None
