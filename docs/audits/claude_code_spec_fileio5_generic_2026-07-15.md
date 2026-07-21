# Claude Code spec — File-I/O decomposition #5: `_open_stack_generic` → `readers/` (sub-pieced)

> **✅ STATUS — DONE, shipped across 1.6.61–1.6.63** (git commits f21a85e / 5d8d00e / 0d2f53e; predate the
> current CHANGELOG, which starts at 1.6.103). The generic path was sub-pieced into
> `readers/stack_metadata.py` (`read_stack_structure`, 5a) and `readers/stack_layer_builders.py` (per-branch
> `build_*_layer`, 5b–5d); `_open_stack_generic` (now in `stack_openers.py`) is a slim orchestrator over
> them. Commit 0d2f53e marked the fileio-godclass decomposition roadmap complete. Every Definition-of-done
> item met.

**Date:** 2026-07-15 · **Target tree:** 1.6.59 · Verified against the uploaded 1.6.59 tree.
**This is the LAST and BIGGEST decomposition piece. Do NOT do it in one shot — sub-piece it.**

## ⚠️ SEQUENCING — read before starting
**This task is BLOCKED until the CZI reader work lands.** CZI (spec
`claude_code_spec_czi_2026-07-15.md`) adds a BioFormats branch *inside* `_open_stack_generic`. If you
decompose first, CZI has to be re-fitted; if you run both together, they collide badly in the same
method. **Order: CZI ships and commits FIRST → then this #5 decomposes the settled code.**

Because of that, the line numbers below are from the CURRENT (pre-CZI) 1.6.59 tree and WILL shift once
CZI adds its branch. So this spec is written by **region/responsibility**, not line number — find the
regions by their `# ──` section headers and behaviour, and expect a BioFormats branch to exist
alongside the tifffile branches by the time you run this.

## Read first
- `docs/audits/fileio_godclass_roadmap_2026-07-15.md` — piece **#5** of 5, the final one.
- Pieces #1 (mask), #2 (2d image), #4 (writers) shipped; #3 (IMS) in progress. Follow the SAME
  discipline: behaviour-preserving, byte-identity where possible, ship each sub-piece as its own
  version bump + PyPI push + commit + CHANGELOG. **Compile after each sub-piece.**

## Why this one is different
`_open_stack_generic` (currently file_io.py 2356–2898, ~542 lines) is the god-method's core. Unlike
the reader pieces, it is NOT a single liftable block — it's a **head + a scene×channel construction
loop with 3–4 format branches + shared post-load logic**, deeply interwoven with napari
(`add_image`/`load_into_viewer`), the zarr-3.2 shim, contrast-pinning, and reader retention. A
one-shot move WILL break the build. Sub-piece it, compile between each, ship incrementally.

## The anatomy (find by section header, not line)
1. **Head — metadata read + reader selection** (`# ── Read metadata`): tries the structured reader
   (bioio/AICSImage) for dims/scenes/channel metadata; on failure falls back to direct tifffile,
   setting `reader_has_structure = False`. Includes pixel-size recovery (OME-XML → TIFF tags) and the
   "a metadata defect must not trigger a full EAGER read" lazy-TIFF-page guard.
2. **Multi-position / scene detection** (`# ── Multi-position (scene) detection`): scenes via the
   reader, offered through the position dialog.
3. **The scene × channel construction loop** — for each scene, each channel, builds a lazy layer via
   ONE of these branches:
   - **tifffile-fallback branch** (`if not reader_has_structure`): single (T,H,W), no Z/scene
     metadata, contrast pinned, `add_image(wrapper)`.
   - **pure-time-series branch** (`elif n_z == 1`): structured reader's dask array, transcoded into an
     on-disk zarr store for random-access scrubbing; includes the zarr-3.2-shim workaround (`bioio`'s
     dask path is broken for TIFF on zarr 3.2 → tifffile-page wrapper instead).
   - **z-stack branch**: the (Z,Y,X) / (T,Z,Y,X) path.
   - **(post-CZI: a BioFormats branch will live here too)** — leave it a peer of the others.
   Each branch repeats: contrast-limit pinning (`# ── Pin the contrast limits`) + reader/dask
   retention (`_stack_lazy_refs`) + `add_image`/`load_into_viewer`.
4. **Shared post-load logic** (`# ── Shared post-load logic`, ~line 2898-area/end): common tail after
   the loop.

## Sub-piece plan (ship each as its own version)
Do them in THIS order; each is independently shippable and compiles standalone.

**5a — Extract the metadata-read + reader-selection head → `readers/stack_metadata.py`.**
A pure function `read_stack_structure(file_path, ext) -> StackStructure` returning the reader handle
(or None), `reader_has_structure`, dims (n_t/n_c/n_z/H/W), scenes, pixel size, and the tifffile
fallback array/wrapper when structured read failed. No napari, no `self`. The controller calls it and
branches on the result. This is the cleanest seam and the highest-value one (it's the part CZI's
reader-selection interacts with).

**5b — Extract the per-branch lazy-layer BUILDERS → `readers/stack_layer_builders.py`.**
Pure functions, one per branch: `build_tifffile_fallback_layer(...)`,
`build_timeseries_layer(...)` (incl. the zarr transcode + zarr-3.2 shim),
`build_zstack_layer(...)`, and (post-CZI) `build_bioformats_layer(...)`. Each takes the reader/dims +
target zarr dir and returns `(wrapper, add_kwargs, retain_refs)` — it does NOT call `add_image`
itself (keep napari in the controller). The controller's loop picks the builder and does the
`add_image`. This isolates the gnarly zarr/dask/contrast logic from the viewer wiring.

**5c — Move the contrast-pinning + retention helpers** if they're duplicated across branches into one
shared helper (`_pin_contrast_limits`, `_retain_lazy`). Small consolidation; only if 5b reveals real
duplication.

**5d — Slim `_open_stack_generic`** to: call `read_stack_structure` (5a) → loop scenes/channels →
pick a builder (5b) → `add_image` → shared post-load. It should end up a readable ~80–120 line
orchestrator that reads like the IMS loader does post-#3.

## Steps per sub-piece
1. Create the target `readers/*.py` module; move the region's pure logic VERBATIM, re-parameterised
   (replace `self.viewer`/`self.central_manager`/`self.base_file_name` with passed args).
2. Wire the controller to call it. Import back any helper still used elsewhere in file_io.py (grep
   first — same lesson as #3, where `_suppress_ims_chunk_prints` was used 10×).
3. Compile: `python -c "import pycat.file_io.file_io"` + import the new module.
4. Test: pure test with a fake reader (mirror `tests/test_mask_reader_extraction.py` /
   `test_ims_reader_extraction.py`) asserting the extracted function returns identical structure/
   builds identical wrappers to an inline oracle.
5. GUI confirm at the END of the whole piece (needs real files): open a plain TIFF, an OME-TIFF, a
   multi-channel TIFF (the tifffile-zarr shim's beneficiary — still needs this confirm anyway), a
   z-stack, and the CZI — all four branches must still load + scrub. Ship each sub-piece; GUI-confirm
   the set once at the end.

## Definition of done
- `_open_stack_generic` reduced to a slim orchestrator; the head, branch-builders, and helpers live in
  `readers/stack_metadata.py` + `readers/stack_layer_builders.py`, Qt/napari-free and unit-tested.
- All formats (plain TIFF, OME-TIFF, multi-channel TIFF, z-stack, CZI) still open + scrub in the GUI.
- Each sub-piece shipped as its own version + PyPI push + commit + CHANGELOG.
- The god-class decomposition roadmap is COMPLETE — update
  `docs/audits/fileio_godclass_roadmap_2026-07-15.md` to mark all 5 done.

## Cautions
- **Do this AFTER CZI.** Confirm `git log` shows the CZI reader committed before starting.
- The zarr-3.2 shim workaround (`bioio` dask path broken for TIFF on zarr 3.2 → tifffile-page wrapper)
  is SUBTLE and load-bearing — move it verbatim, do not "clean up." Same for the on-disk zarr
  transcode (it's what makes TIFF time-series scrub at zarr random-access speed).
- Contrast limits MUST stay pinned from the first frame — if you drop the pinning, napari eager-reads
  EVERY frame to compute limits (the exact bug the `# ── Pin the contrast limits` guards call out).
- `_stack_lazy_refs` / reader retention keeps lazy sources alive for the layer's life — the builders
  must return the refs to retain, and the controller must hold them (or attach via ImageSource, as the
  IMS/generic loaders do). Dropping a ref = dead layer mid-scrub.
- Behaviour-preserving throughout: same layers, same names, same lazy reads. This is a
  READABILITY/architecture refactor, not a behaviour change.
