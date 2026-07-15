# Claude Code spec — File-I/O decomposition #3: `_open_stack_ims` → `readers/ims_reader.py`

**Date:** 2026-07-15 · **Target tree:** 1.6.58 · Verified against the uploaded 1.6.58 tree.

## Read first
- `docs/audits/fileio_godclass_roadmap_2026-07-15.md` — the decomposition roadmap. This is **piece #3**
  of 5. Pieces #1 (`open_2d_mask`→`mask_reader.py`) and #2 (`open_2d_image`→`image_reader_2d.py`) are
  done and shipped; follow the SAME discipline: behavior-preserving, byte-identity tested, ship one
  loader per version.
- The roadmap's line for #3: *"IMS loader body + `_ImsReader*` wrappers move together."*

## The discipline (non-negotiable, from the roadmap)
Behavior-preserving: the moved code produces byte-identical output to the inline version. Ship this as
its own version bump + PyPI push + commit + CHANGELOG entry. Compile after each step; don't do a
big-bang move.

## How #3 DIFFERS from #1/#2 (important — don't blindly copy the pattern)
Pieces #1/#2 extracted a pure *read function* (path → channel arrays) and left napari construction in
the controller. #3 is **not shaped that way.** The IMS loader's read is not a separable
"read-then-construct" flow — the three `_ImsReader*` classes are **lazy adapters consumed DURING
layer construction**, and `_open_stack_ims` interleaves reader setup, metadata, ImageSource retention,
a multi-position dialog, and napari `add_image` calls.

So #3 is a **"move the pure lazy-reader classes + their helpers into the reader module"** extraction,
NOT a "lift a read function out." The controller keeps `_open_stack_ims` (all the napari/dialog/
ImageSource logic) and IMPORTS the moved classes.

## What moves to `src/pycat/file_io/readers/ims_reader.py`
All verified PURE in the 1.6.58 tree (reference only the reader + numpy + a suppress-context + the two
already-shared helpers below — NO Qt, NO napari, NO `self` from FileIOClass):

| symbol | current location (file_io.py) | notes |
|---|---|---|
| `_ImsReaderTYX` | class @ line 842 | lazy (T,Y,X) view |
| `_ImsReaderZYX` | class @ line 880 | lazy (Z,Y,X) view |
| `_ImsReaderTZYX` | class @ line 919 | lazy (T,Z,Y,X) view |
| `_suppress_ims_chunk_prints` | def @ line 32 | context manager the wrappers default to |
| `_ims_indices` | def @ line 56 | selector→index helper the wrappers call |
| `_ims_pixel_size_um` | def @ line 105 | IMS pixel-size reader (used by `_open_stack_ims`, IMS-specific) |

The wrappers already import these two from reusable modules — the moved module imports them the same way:
- `to_unit_float32` from `pycat.file_io.stack_access` (line 463 there)
- `refuse_implicit_full_read` from `pycat.file_io.lazy_guard` (line 35 there)

## What STAYS in `file_io.py`
- `_open_stack_ims` (method @ line 2107, ~250 lines) — keeps ALL of: the `ImsReader` construction,
  metadata-repo writes (`extract_metadata`), the `ImageSource` retention (`image_source.py`), the
  multi-position sibling dialog (`multidim_io.find_sibling_position_files` /
  `show_position_selection_dialog`), the `load_into_viewer`/`add_image` layer construction, the
  first-frame probe for diameter estimates, and the `self._ims_file_path` line (a documented,
  intentionally-not-migrated cross-file reach-in — leave it).
- It now IMPORTS the three wrapper classes + `_suppress_ims_chunk_prints` + `_ims_indices` +
  `_ims_pixel_size_um` from `pycat.file_io.readers.ims_reader` instead of defining them inline.

## Steps
1. Create `src/pycat/file_io/readers/ims_reader.py`. Move the 3 wrapper classes and the 3 helpers
   verbatim (byte-for-byte the class/function bodies). Add the two shared imports
   (`to_unit_float32`, `refuse_implicit_full_read`). Module docstring: "Pure lazy IMS readers —
   extracted from `FileIOClass._open_stack_ims` (god-class decomposition #3). Qt/napari-free."
2. In `file_io.py`: delete the moved definitions; add
   `from pycat.file_io.readers.ims_reader import (_ImsReaderTYX, _ImsReaderZYX, _ImsReaderTZYX,
   _suppress_ims_chunk_prints, _ims_indices, _ims_pixel_size_um)`. **Check** those six names aren't
   used by any OTHER method in file_io.py before removing (grep first). **Verified in 1.6.58:**
   `_suppress_ims_chunk_prints` is used **10×** in file_io.py (the `_open_stack_ims` layer-construction
   loop calls it directly, not only the wrappers), `_ims_indices` **5×**, `_ims_pixel_size_um` **2×** —
   so ALL THREE must be imported back into file_io.py (they define once, but are called from the
   controller too). Import them from the new module; don't leave dangling refs.
3. Compile (`python -c "import pycat.file_io.file_io"` and `import pycat.file_io.readers.ims_reader`).
4. Byte-identity test `tests/test_ims_reader_extraction.py`: because these are lazy WRAPPERS not a
   read-fn, the test asserts the moved wrappers produce identical lazy reads to the originals. Build a
   tiny fake `reader` object (with `.shape` and `__getitem__` returning deterministic per-(t,c,z)
   arrays — mirror the fake in `tests/test_mask_reader_extraction.py`), wrap it in each
   `_ImsReader*`, and assert `wrapper[i]` / `wrapper.shape` / `wrapper.dtype` match a
   reimplemented-inline oracle across a few shapes. Skip-if-no-deps not needed (fakes avoid the real
   IMS lib). Mirror the structure of `tests/test_mask_reader_extraction.py`.
5. **GUI confirm** (needs a real .ims): open an IMS stack via `run-pycat` and scrub it — confirms the
   moved wrappers still read lazily and the multi-position path works. There is an existing
   `tests/test_ims_reader_retention.py` — run it, it must still pass (the ImageSource retention is
   untouched but the wrappers moved).

## Definition of done
- `readers/ims_reader.py` holds the 3 wrappers + 3 helpers, Qt/napari-free, importable headlessly.
- `file_io.py` imports them; `_open_stack_ims` otherwise unchanged (napari/dialog/ImageSource logic
  intact).
- `test_ims_reader_extraction.py` (new) + `test_ims_reader_retention.py` (existing) both pass.
- IMS files still open + scrub in the GUI (single 2D, pure time-series, z-stack, and TZYX paths — the
  four branches in `_open_stack_ims`).
- Shipped: own version bump + PyPI push + commit (EXPLICIT filenames) + CHANGELOG entry.

## Cautions
- These wrappers carry SUBTLE correctness (the `__array__` methods call `refuse_implicit_full_read` to
  block accidental full-stack materialization — the lazy-guard; and `to_unit_float32` normalizes from
  the SOURCE dtype, not raw counts). Move them VERBATIM; do not "clean up" or refactor the bodies.
- `_ims_pixel_size_um` is IMS-specific and used by `_open_stack_ims` — it moves too, but confirm no
  other caller (grep). If something else uses it, keep an import.
- Do NOT touch `_open_stack_generic` (that's piece #5) or the CZI work (spec
  `claude_code_spec_czi_2026-07-15.md` — a SEPARATE task that also edits file_io.py; do these one at a
  time, commit between them, to avoid colliding in file_io.py).
- After this ships, the roadmap's next pieces are #4 (`save_and_clear_all`→writers) and #5
  (`_open_stack_generic`, the 542-line monster, LAST and in sub-pieces).
