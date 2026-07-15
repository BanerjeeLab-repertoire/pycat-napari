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

## Sequence (each piece independently shippable + byte-identity tested)

1. **`open_2d_mask` (80) → `readers/mask_reader.py`.** Smallest fat loader = the pilot. Extract the
   pure read (path → array + metadata) to a free function; the method becomes a delegator that calls
   it then does napari-layer construction. **← doing this now.**
2. **`open_2d_image` (224) → `readers/image_reader_2d.py`.** Same pattern, the 2D image path.
3. **`_open_stack_ims` (250) → `readers/ims_reader.py`.** IMS loader body + `_ImsReader*` wrappers move together.
4. **`save_and_clear_all` (183) → `writers/`.** Save path; the `_save_layer` stub already points at writers.py, so the seam exists.
5. **`_open_stack_generic` (542) → `readers/stack_reader.py`, LAST and in sub-pieces.** The monster,
   and where the audit's "one function decides 7 things at once" (#3) lives. Split it INTERNALLY
   first (probe → reader-selection → axis-interpretation → layer-construction, each a callable) and
   only then lift the reading half out. This is also where audit #2 (one ImageSource protocol) and
   #6 (validated TIFF page order) get addressed.

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
