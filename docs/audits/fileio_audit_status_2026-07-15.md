# File-I/O audit (BioIO eager-read regression, 24 findings) — status against current code

**Date:** 2026-07-15
**Audit source:** external review "BioIO eager-read regression" (24 findings + implementation sequence)
**This doc:** grounds each finding against the *actual* 1.6.36 code, so the audit isn't followed as
if the code were still in the older snapshot it was written against. **Key discovery: the audit was
written against an earlier state; several of its highest-priority hazards are already fixed.**

---

## Findings already FIXED in current code (verify-don't-rebuild)

- **#1 — `__array__` returns frame 0.** FIXED. All lazy wrappers now call
  `lazy_guard.refuse_implicit_full_read()` (raises), which is the audit's recommended option 1. There
  is a `test_no_eager_reads.py`. This was the audit's single largest correctness hazard; it is closed.
- **#18 — file-handle ownership.** FIXED (the reader-retention refactor, 1.6.31–1.6.34): layer-scoped
  `ImageSource` with `retain()`/`close()`, sole owner of readers. Guard test
  `test_generic_stack_reader_retention.py`.
- **#23 (core hazard) — metadata failure → eager full read.** FIXED (1.6.4+). The
  `tifffile.imread(file_path)` eager path (file_io.py ~L2284) is now reached only *after* the lazy
  `_TiffPageStack` fallback has been tried and failed — "the honest last resort." The comment
  documents that the prior fix was broken and always fell through; it no longer does.
- **#9 (metadata-decision side) — the 1.0 sentinel.** Was ALREADY FIXED on the metadata side
  (1.6.15/1.6.23/1.6.24) via `tagging.py::_calibration_is_from_metadata`, which reads
  `pixel_size_source` provenance. **This session finished it on the gate/scale-bar side** — see below.

## Fixed THIS session

- **#9 — genuine 1.0 um/px treated as missing (gate + scale-bar).** The in-dock gate
  (`field_status.py::_valid_scale`, and the prompt-skip check) and the µm-vs-px scale-bar
  (`napari_adapter.py::_is_calibrated`) still used `abs(mpp-1.0)>1e-9` as the *primary* test, so a
  genuine 1.0 um/px scale — especially one the user *typed* — re-prompted forever / showed a "px"
  bar. Now decided from provenance (`pixel_size_from_metadata` OR a new `pixel_size_confirmed` flag
  set on explicit user entry), value-guess only as a no-provenance fallback. **Byte-identical for
  every scale != 1.0** (test asserts this). Core test `test_pixel_size_sentinel.py` (8 assertions,
  verified passing headless).

---

## Findings deliberately NOT actioned, with rationale

### #5 — "preserve native dtype; stop the float32 conversion" → **WON'T-FIX (would break correctness)**
This is the most important entry in this doc. The audit reads the reader-side float32 conversion as
naive dtype coercion and recommends removing it. **In PyCAT that recommendation is wrong and
dangerous** — exactly the "uint algebra biting us" hazard.

The conversion is `stack_access.to_unit_float32`, which is **not** a bare `astype(float32)` — it
brings every frame into a **[0, 1] normalized float32**, and this is a load-bearing contract:
- **17 toolbox functions declare `[0,1]` in their docstrings**, including real condensate
  measurements (`partition_coefficient_field`, `fit_bimodal_intensity`).
- `skimage.exposure.equalize_adapthist` **raises** outside [-1,1]; the preprocessing path needs it.
- `img_as_uint` (the save converter) **raises** on raw counts.
- It is **byte-identical to `skimage.img_as_float32`** (the 2-D loader's path) *on purpose*, so the
  lazy stack path and the 2-D path agree. Verified this session: max abs diff 0.00e+00 vs skimage;
  output correctly in [0,1]; ratio consumers (partition coefficient) scale-invariant and unchanged.

Removing it would reintroduce the exact "same pixels 65535× apart depending on which loader ran" bug
the current code was written to fix, and make `equalize_adapthist`/`img_as_uint` raise. The audit's
*memory* concern (float32 doubles RAM vs uint16) is real but is already mitigated by the lazy
wrappers converting **per frame on access**, not materializing the whole stack. **Do not implement
audit #5.** (If a native-dtype "raw counts" access is ever wanted for a specific quantitative
method, add it as an explicit separate accessor — never by changing the default reader contract.)

### #23 — "narrow the 60 broad excepts" → **core hazard already fixed; remainder appropriately scoped**
The audit's dangerous case (metadata failure rerouting to eager pixel load) is fixed (see above). Of
the ~60 `except Exception` blocks in file_io.py, ~18 are silent (`pass`); inspection shows each
guards a genuinely-optional UI/display operation (file-handle cleanup, view-fit, scale-bar,
contrast-limit "flat/black display" guard, PNG-export folder resolution). None silently swallow a
*scientific* failure (wrong dimensions / lost calibration). Blindly narrowing them would risk
breaking working fallbacks for no correctness gain. **No blanket change; revisit only if a specific
block is shown to hide a scientific failure.**

---

## Findings NOT yet addressed (real, open — need Gable's design/GUI judgment)

These are genuine and remain open; none are the "already fixed" false alarms above. Most are larger
architecture, not solo-safe:

- **#2** — one backend-neutral `ImageSource` protocol (read_plane/read_block/axes/dtype). Current
  `ImageSource` is a *lifecycle holder* (ours), not this protocol; the 8 divergent wrappers
  (`_ZarrTYX`, `_ImsReader{TYX,ZYX,TZYX}`, `_TiffPageStack`, `_ZarrTZYX{,_generic}`, `_ZarrZYX`)
  still coexist. This is the audit's central redesign; large.
- **#3** — pixel transport vs dimensional interpretation conflated in one load function.
- **#6** — TIFF fast-path page-order assumption (`t*C+c`) unvalidated for non-interleaved / TZCYX /
  multi-series / tiled layouts. Real silent-mis-index risk; needs test data with known layouts.
- **#7** — multifile OME-TIFF drops missing planes → collapses the coordinate system.
- **#8** — TZYX synchronously transcoded to Zarr in full on open (blocking).
- **#10 / #11** — anisotropic calibration reduced to one scalar; axis provenance partly global not
  per-layer. Matters for Z-stack morphology/volume.
- **#12** — metadata extraction lacks a structured confidence model.
- **#13 / #14 / #15** — save path can materialize whole lazy stack; labels→PNG default; silent
  float→int rescaling on save.
- **#16** — no atomic-write strategy (partial file on crash).
- **#17 / #19** — files reopened/reparsed several times; storage probe reads before first display.
- **#20** — contrast estimation inconsistent across T vs Z vs TZ branches.
- **#21** — `file_io.py` is 3076 lines / ~18 responsibilities (the god-class).
- **#22** — backend terminology only partly migrated; partial `readers/`/`writers/` split exists
  (`napari_adapter.py`, `tagging.py`, `image_reader.py`, `writers/`) but not the full decomposition.
- **Testing gaps** — the audit's suggested lazy-loading-contract, dimensional-correctness (pixel
  identity at coordinates), metadata round-trip, and resource-behavior tests are largely not present.
  Several are `core`-markable and buildable without the GUI when there's appetite.

---

## Honest bottom line

The audit is a good, deep review — but it describes a **larger file-I/O architecture redesign**, and
it was written against an **older snapshot**. Of its 24 findings: ~4 are already fixed (including the
two highest-priority correctness hazards, #1 and the #23 eager-reroute), #9 is now fully closed, #5
should **not** be implemented as written (it would break the [0,1] contract), and the remaining ~17
are real-but-open and mostly need design decisions or the GUI. The reader-retention refactor we
completed was one finding (#18), done well; the audit's central proposal (the single enforced
`ImageSource`/`DatasetDescriptor` boundary) is ~15% built and is a multi-week effort, not a
solo-session task.
