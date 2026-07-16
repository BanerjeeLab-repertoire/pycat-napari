# FileIOClass decomposition roadmap (audit #21 + #2 + #3)

**Date:** 2026-07-15
**Target:** `file_io.py::FileIOClass` (lines ~1181–3076, ~1900 lines, 35 methods) — the god-class.

## The actual shape (not as bad as the audit implies)

A decomposition is already ~40% underway: **18 of 35 methods are already thin 3-line delegators**
forwarding to sibling modules (`napari_adapter.py`, `tagging.py`, `writers.py`, `routing.py`,
`storage.py`). The real bulk is concentrated in **5 fat loader methods** that still hold their logic
inline:

| method | lines | role |
|---|---|---|
| `_open_stack_generic` | 542 | TIFF/CZI/OME stack loader (the monster) |
| `_open_stack_ims` | 250 | IMS/HDF5 stack loader |
| `open_2d_image` | 224 | 2D image loader |
| `save_and_clear_all` | 183 | save-session path |
| `open_2d_mask` | 80 | 2D mask loader |

~1280 of ~1900 lines live in those 5. So the job is: **lift each fat loader's body into a sibling
module as a free function, leaving `FileIOClass` as pure orchestration** — the same seam already
used by the 18 stubs.

## Target end state (audit's architecture, reached incrementally)

```
file_io/
  readers/            read bytes → (array, metadata); NO napari
    mask_reader.py    read_2d_mask()
    image_reader_2d.py read_2d_image()
    ims_reader.py     read_ims_stack()   (+ the _ImsReader* wrappers)
    stack_reader.py   read_generic_stack() (TIFF/CZI/OME)
  writers/            already exists — save paths land here
  napari_adapter.py   already exists — layer construction stays here
  file_io.py          FileIOClass = thin controller that orchestrates the above
```

## Status — ✅ ALL 5 PIECES DONE (as of 1.6.62)

The decomposition is complete. Each piece shipped behaviour-preserving with headless byte-identity
tests; the final GUI confirm of #5 across all formats (plain TIFF / OME-TIFF / multi-channel TIFF /
z-stack / CZI) is pending a `run-pycat` on a machine with a display.

## Sequence (each piece independently shippable + byte-identity tested)

1. ✅ **`open_2d_mask` → `readers/mask_reader.py`.** Done (1.6.x).
2. ✅ **`open_2d_image` → `readers/image_reader_2d.py`.** Done (1.6.x).
3. ✅ **`_open_stack_ims` → `readers/ims_reader.py`.** Done (1.6.60) — IMS loader body + `_ImsReader*` wrappers.
4. ✅ **`save_and_clear_all` → `writers/`.** Done (1.6.59) — the write loop → `writers.write_session_outputs`.
5. ✅ **`_open_stack_generic` → `readers/`, in sub-pieces.** Done (1.6.62), the monster:
   - **5a** — metadata-read + reader-selection head → `readers/stack_metadata.py::read_stack_structure`.
   - **5b** — per-branch lazy builders → `readers/stack_layer_builders.py` (tifffile-fallback,
     time-series incl. the zarr-3.2 shim + on-disk paths, z-stack, T-Z).
   - **5c** — shared retain + contrast-pin + add_image tail → `_add_lazy_stack_layer`.
   - **5d** — `_open_stack_generic` is now a slim orchestrator (313 → 186 lines).
   The Zeiss streaming-CZI branch is a peer path (`_open_czi_streaming`, 1.6.61), not tangled in the
   loop. (Audit #2's single ImageSource protocol and #6's validated TIFF page order remain as
   follow-ups, but the god-method decomposition itself is finished.)

## The one discipline (non-negotiable)

Each extraction is **behavior-preserving**: the free function returns exactly what the inline code
produced. Ship one loader per zip (mid-refactor multi-file sweeps have broken this codebase before).
Where the reading half is pure (bytes → array), a byte-identity test guards it headlessly; the
napari-layer-construction half stays in the controller and gets a GUI confirm. Extract → test
identical → ship → next.

## Honest scope

The full breakup is a multi-week arc, not a session — it's audit #21 + #2 + #3 together. What's
tractable is one loader at a time behind byte-identity tests. Pieces 1–4 are mechanical; piece 5 is
the real work and carries the architectural decisions.
