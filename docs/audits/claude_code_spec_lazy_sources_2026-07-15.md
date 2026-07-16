# Claude Code spec — GUI-free `lazy_sources.py` (the loader keystone)

## ✅ STATUS — DONE, shipped in 1.6.70 (executed against the 1.6.69 tree)
Definition of done met: `lazy_sources.py` holds both wrappers and imports with **no PyQt5/napari in
`sys.modules`**; `file_io.py` re-exports them (3061 → 2691 lines); the new headless test proves the
Qt-free contract + a bit-identical plane read; `pytest -m core` is **589 passed, 2 skipped**; the real
MMStack fixture loads through the real `_open_stack_generic` and scrubs all 20 frames bit-identically.
Roadmap item marked resolved; the Z/TZ reader is noted as unblocked.

**Three things the spec got wrong or missed — worth knowing before writing the next one:**
1. **The dependency list was incomplete, and it was load-bearing.** `_TiffPageStack.__init__` calls
   `resolve_ome_file_set` and `build_ome_page_map`, both defined in `file_io.py`. They could not be
   imported back (`file_io` now imports `lazy_sources` → hard circular import), so they **moved too**
   (they have no other caller) and are re-exported alongside the classes.
2. **`tests/test_tiff_planes.py::test_every_TiffPageStack_CONSTRUCTION_has_enough_arguments` was a
   blocker, not a "must still pass".** It parses `file_io.py` for the `_TiffPageStack` **ClassDef** —
   a re-export is invisible to `ast.ClassDef`, so the move tripped its `assert definition is not None`.
   Repointed at `lazy_sources.py`. **Separately: that guard had already gone toothless** — since
   decomposition #5 the loader *injects* the class as `tiff_page_stack_cls`, so there were **zero**
   literal `_TiffPageStack(` calls left for it to check. It now counts the injected name too.
3. **`tests/test_nothing_was_dropped.py` fails by design on any move** and must be told. Seven keys
   (`file_io.py::resolve_ome_file_set`, `::build_ome_page_map`, and the five `_TiffPageStack`
   methods) were added to `_DELIBERATE` with the reason.

Also: the spec's line refs were off — the re-export block to mirror is at ~1052, not ~228, and it sits
**between** the two classes (a naive "cut 839–1160" eats it and the `EAGER_DIAMETER_LAYERS` re-export).
The runtime Qt check had to run **in a subprocess**: `test_ui_smoke.py` imports PyQt5 at module scope,
so an in-process `'PyQt5' not in sys.modules` assertion fails for unrelated reasons. And the new test's
pycat imports must sit **inside** the test bodies — `conftest.py`'s `pytest_ignore_collect` silently
un-collects modules that import `pycat.file_io` at module scope when the GUI stack is absent, which
would have made the headless contract vanish from the headless CI job.

**Date:** 2026-07-15 · **Target tree:** 1.6.61 · Verified against the uploaded 1.6.61 tree.

## Why this one, and why now
This closes a roadmap item AND unblocks two others. Audited against the current tree:
- `roadmap.rst` — *"The lazy wrappers live behind a Qt import"*: **still true.** `_TiffPageStack`
  (file_io.py 839–1082) and `_LazyArraySource` (1083–1160) live in `file_io.py`, which imports the
  GUI stack at module scope — so **reaching a TIFF lazy wrapper drags in PyQt5**, and the wrappers
  **cannot be exercised headlessly** (a perf harness / CI perf gate can't touch them). The IMS
  wrappers already moved out in decomposition #3 (`readers/ims_reader.py`); these two are the last
  GUI-coupled lazy sources still stuck in `file_io.py`.
- `roadmap.rst` — *"Z-stack and T+Z TIFF still go through BioIO's broken zarr path"*: **still true**
  — `_TiffPageStack` is hardcoded `ndim=3`, `shape=(frames,H,W)` (TYX only); TIFF ZYX/TZYX fall to
  BioIO's zarr-3.2-broken path and **fail today**. That fix is the NEXT task, and it's far cleaner to
  add `_TiffPageStackZYX`/`_TiffPageStackTZYX` into a Qt-free `lazy_sources.py` (headlessly testable)
  than to bolt more classes into the 3000-line Qt-coupled `file_io.py`.

So: **extract first (this spec), then the Z/TZ reader lands cleanly in the new module.** This is the
keystone.

## Sequencing
- Do this AFTER decomposition #5 and the file-I/O cleanup commit (all touch `file_io.py`). Confirm
  `git log` is clean. This is a SMALL, self-contained move — lower risk than #5.
- Ships as its own version bump + PyPI push + commit (EXPLICIT filenames) + CHANGELOG entry.

## What moves → `src/pycat/file_io/lazy_sources.py`
Verified Qt/napari-FREE in their bodies (only `tifffile`, `numpy`, and two already-shared helpers;
the only napari mentions are in COMMENTS explaining duck-typing):

| class | current location | notes |
|---|---|---|
| `_TiffPageStack` | file_io.py 839–1082 | TYX lazy page-seek wrapper; multifile-OME aware (`_page_map`) |
| `_LazyArraySource` | file_io.py 1083–1160 | generic lazy array source |

Shared helpers they call (already in reusable modules — import, don't move):
- `to_unit_float32` from `pycat.file_io.stack_access`
- `refuse_implicit_full_read` from `pycat.file_io.lazy_guard` (imported lazily inside `__array__` —
  keep that lazy import as-is; it's what makes the module import-cheap)

## Steps
1. Create `src/pycat/file_io/lazy_sources.py`. Module docstring: *"GUI-free lazy array sources
   (`_TiffPageStack`, `_LazyArraySource`) — extracted from `file_io.py` so the TIFF lazy wrappers can
   be imported and perf-tested without dragging in PyQt5. Qt/napari-free by contract."* Move both
   classes VERBATIM (byte-for-byte bodies). Add `from pycat.file_io.stack_access import
   to_unit_float32` at module scope; keep the `refuse_implicit_full_read` import lazy inside
   `__array__` exactly as it is now.
2. In `file_io.py`: delete the two class definitions; add
   `from pycat.file_io.lazy_sources import _TiffPageStack, _LazyArraySource`. **Grep first** for every
   use of both names in file_io.py (they're constructed in several loader branches — `_TiffPageStack(`
   appears at ~2250/2410 and is referenced in comments; `_LazyArraySource` similarly). All
   constructions must resolve to the import. There is already a re-export block at ~228
   (`from pycat.file_io.stack_access import (... re-exported ...)`) — mirror that pattern so the ~25
   external `from pycat.file_io.file_io import _TiffPageStack` callers (if any) still work: re-export
   both names from file_io.py (`# noqa: F401`). CHECK who imports them from file_io.py:
   `grep -rn "from pycat.file_io.file_io import.*_TiffPageStack\|_LazyArraySource" src tests`.
3. Compile: `python -c "import pycat.file_io.lazy_sources"` MUST succeed **without importing PyQt5**
   — that's the whole point. Verify: `python -c "import sys; import pycat.file_io.lazy_sources;
   assert 'PyQt5' not in sys.modules, 'lazy_sources dragged in Qt'"`. Also
   `python -c "import pycat.file_io.file_io"` still imports.
4. Tests:
   - New `tests/test_lazy_sources_headless.py` (mark `core`): assert `import
     pycat.file_io.lazy_sources` leaves `PyQt5` out of `sys.modules` (the headless contract), and
     that `_TiffPageStack` reads a plane bit-identically from a small fixture TIFF (mirror
     `tests/test_tiff_planes.py::test_a_plane_is_BIT_IDENTICAL_to_a_full_read`). This is the test the
     roadmap wanted — the wrappers exercised WITHOUT Qt.
   - Existing tests must still pass: `test_tiff_planes.py`, `test_no_eager_reads.py`,
     `test_one_plane_reads_one_plane.py`, and any importing `_TiffPageStack`.

## Definition of done
- `lazy_sources.py` holds both wrappers, imports with NO PyQt5 in `sys.modules`.
- `file_io.py` imports + re-exports them; all loader construction sites work unchanged.
- New headless test proves the Qt-free contract + bit-identical plane read; existing lazy/tiff tests
  green.
- TIFF time-series still loads + scrubs in the GUI (behaviour-preserving).
- Shipped: own version + PyPI push + commit + CHANGELOG. Update `roadmap.rst` to mark
  *"lazy wrappers behind a Qt import"* resolved, and note the Z/TZ TIFF reader is now unblocked to
  land in `lazy_sources.py`.

## Cautions
- Move VERBATIM. `_TiffPageStack` carries load-bearing subtlety: the `__array__` guard
  (`refuse_implicit_full_read` — blocks accidental full-stack materialization), the multifile-OME
  `_page_map` handling (zero-fills absent companions and says so), and `to_unit_float32` normalizing
  from the SOURCE dtype (a bare `astype(float32)` here would return raw counts — the 1.6.x intensity
  bug). Do NOT "clean up" the bodies.
- Keep the `refuse_implicit_full_read` import lazy (inside `__array__`) — hoisting it to module scope
  is fine functionally but the lazy form documents intent; leave as-is.
- Re-export from file_io.py so external `from ...file_io import _TiffPageStack` callers don't break —
  this is the same courtesy the stack_access re-export block already provides.
- Behaviour-preserving: same lazy reads, same shapes, same dtypes. This is a pure move for
  testability, not a behaviour change.

## What this unblocks (the next spec, do NOT do it here)
Once `lazy_sources.py` exists, the Z/T+Z TIFF reader is a clean addition IN that module:
`_TiffPageStackZYX` (ndim=3, shape=(Z,Y,X)) and `_TiffPageStackTZYX` (ndim=4, shape=(T,Z,Y,X)),
both built on the existing `tiff_planes.read_tiff_plane` (which ALREADY computes the Z/TZ page index
via `_page_and_slice` / `_legacy_geometry`'s `frame=((t*n_z)+z)*channels+c`). The generic loader's
TIFF branch then picks ZYX/TZYX like the IMS branch already does. That's the next task — spec to
follow after this lands.
