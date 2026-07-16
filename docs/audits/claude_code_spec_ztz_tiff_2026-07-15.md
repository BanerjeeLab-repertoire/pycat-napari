# Claude Code spec — Z-stack & T+Z TIFF native reader, consistent across all I/O

## ✅ STATUS — Parts A/B/D DONE, shipped in 1.6.71 (executed against the 1.6.70 tree). Part C SPLIT OUT.
Gap reproduced first (both ZYX and TZYX built a `_LazyArraySource` over a BioIO dask array and died
with `zarr 3.2.1 < 3 is not supported` on the first read), then fixed and re-verified through the real
`_open_stack_generic`. `pytest -m core`: **598 passed, 2 skipped** (was 589). Roadmap item marked
resolved.

**Part C (anisotropic Z scale) was NOT done as written — its premise is false.** The spec says the
TIFF layers must set `layer.scale` with the z-step *"exactly as the IMS path does"*. **The IMS path
does not do this; no loader does.** `napari_adapter._enable_auto_scale_bar` writes only `sc[-1]`/
`sc[-2]` (Y, X) and leaves leading axes at 1.0. Meanwhile the spec's actual goal — *"a ZYX TIFF and a
ZYX IMS of the same specimen produce the SAME voxel volume"* — **is already true**: voxel volume reads
`pixel_size.z_step_um` from the repository, which `metadata_extract` fills from
`physical_pixel_sizes.Z` for any format (verified end-to-end: an OME-TIFF's `PhysicalSizeZ=0.30`
arrives as `0.30`). Implementing Part C literally would have made TIFF **diverge** from IMS — the
exact failure this spec exists to prevent. What shipped instead is a test pinning that path
(`test_ztz_readers_agree.py::test_a_TIFF_z_step_reaches_the_voxel_volume_path_and_is_NaN_when_UNKNOWN`).

Per Gable's decision, applying Z to `layer.scale` **for every format via one shared chokepoint** is
the next piece, and it is a real project, not a wire-up — **there is no per-layer record of axis
order**, and a shared Z-scale cannot exist without one (axis 0 is Z for `ZYX`, T for `TYX`). Details
and the live `get_tags` bug are recorded in `roadmap.rst`.

**Two more spec instructions that had to be overridden (both verified, not judgement calls):**
1. **"Back it with `tiff_planes.read_tiff_plane(...)`" (contract item 6) — no.** It reopens the file
   and rebuilds `series[0]` (re-walking the OME-XML) on **every call**. Measured: **3.61 ms/plane vs
   0.17 ms/plane** with a cached handle — **21x**, widening with OME-XML size. napari reads a plane
   per slider tick, so this would have made the new wrappers slower than the BioIO path they replace.
   The wrappers cache the handle (the contract `_TiffPageStack` already states) and reuse only the
   *index arithmetic*.
2. **"`_page_and_slice` / `_legacy_geometry`'s `frame=((t*n_z)+z)*channels+c`" — that conflates two
   paths.** The formula is **only** `_legacy_geometry`, the fallback for a file declaring no axes.
   The primary map `_page_and_slice` is a mixed-radix fold over the axis order **the file declares**.
   Hardcoding the formula silently returns the wrong plane for a Z-major file — pinned now by
   `test_the_page_map_follows_the_FILE_declared_axis_order_not_a_formula` (a `ZTYX` fixture).

Also worth knowing: `test_stack_layer_builders_extraction.py::test_zarr32_error_is_translated_for_tiff`
**asserted that a TIFF z-stack always raises** — the test suite encoded the bug as the contract. It now
pins the translation only for the path it still applies to. And the agreement test compares the **real**
IMS wrappers (driven by a numpy stand-in — they need only `.shape`/`__getitem__`, so no `.ims` file and
no `imaris_ims_file_reader`), which is stronger than the spec's suggested "stub matching its interface".

**Date:** 2026-07-15 · **Target tree:** assumes `lazy_sources.py` has LANDED (the
`claude_code_spec_lazy_sources_2026-07-15.md` extraction). Verified against the 1.6.67 tree. Builds
the missing TIFF Z/TZ wrappers **in `lazy_sources.py`** and — the point of this spec — makes Z/TZ
behave IDENTICALLY across TIFF, IMS, and the generic path, then proves it with a cross-reader test.

## The gap (verified)
`roadmap.rst`: *"`_TiffPageStack` handles the TYX case natively… It does not handle Z or T+Z. Those
paths report the real cause but do not work. A z-stack TIFF would fail today."* Confirmed:
- `_TiffPageStack` (now in `lazy_sources.py`) is hardcoded `ndim=3`, `shape=(frames,H,W)` — **TYX
  only.**
- `read_tiff_plane` (`tiff_planes.py:216`) ALREADY accepts `z=`/`n_z=` and computes the Z/TZ page
  index — `_legacy_geometry:202`: `frame = ((t*n_z)+z)*channels+c`. **The plane-read arithmetic is
  done; only the wrapper shape + loader routing + scale are missing for TIFF.**
- IMS already works: `readers/ims_reader.py` has `_ImsReaderZYX` (ndim=3, shape=(Z,Y,X)) and
  `_ImsReaderTZYX` (ndim=4, shape=(T,Z,Y,X)). **These are the REFERENCE CONTRACT** the TIFF wrappers
  must match.

## Consistency is the requirement, not just "make TIFF work"
There are already multiple Z/TZ wrapper families (IMS `_ImsReader*`, `_ZarrZYX`/`_ZarrTZYX`,
`_ZarrTZYX_generic`). Adding a fourth TIFF-only shape that behaves differently is the failure mode.
**Every Z/TZ wrapper — regardless of source format — must present the SAME contract**, so downstream
code (segmentation, 3D volume, measurement, scrubbing, brushing) never has to know which reader made
the layer:

The shared Z/TZ contract (read it off `_ImsReaderZYX`/`_ImsReaderTZYX` — match exactly):
1. **Shape/ndim:** ZYX → `ndim=3`, `shape=(Z,Y,X)`; TZYX → `ndim=4`, `shape=(T,Z,Y,X)`.
2. **`dtype = np.dtype('float32')`**, values normalized via `to_unit_float32` (NOT raw counts — the
   intensity-semantics contract; `_TiffPageStack` already does this for TYX, reuse it).
3. **`__getitem__` squeeze semantics:** an integer Z-select drops the Z axis; an integer T-select
   drops the T axis; `arr[0,0]`→(Y,X), `arr[0,:]`→(Z,Y,X), etc. **Copy the exact squeeze logic from
   `_ImsReaderTZYX.__getitem__`** (ims_reader.py:228–247) so the two are behaviourally identical.
4. **`__array__` REFUSES** via `refuse_implicit_full_read` (the no-eager-read guard) — same as every
   other lazy wrapper.
5. **`__len__`** returns the leading-axis length.
6. **A single lazy plane read per (t,z)** — never materialize the stack. Back it with
   `tiff_planes.read_tiff_plane(path, t=t, c=c, z=z, n_channels=…, n_z=…)`.

## Part A — the two TIFF wrappers (in `lazy_sources.py`)
Add `_TiffPageStackZYX` and `_TiffPageStackTZYX` next to `_TiffPageStack`, satisfying the contract
above. They wrap the same page-map / `read_tiff_plane` machinery `_TiffPageStack` uses; the only
differences from `_TiffPageStack` are the axis count and the `__getitem__` squeeze. Keep them
Qt/napari-free (the whole reason `lazy_sources.py` exists) — no viewer, no notifications. Re-export
from `file_io.py` alongside `_TiffPageStack` (mirror that re-export).

## Part B — loader routing (file_io.py generic TIFF branch)
The IMS branch already shows the pattern (`file_io.py` ~2049 "Pure z-stack (Z,Y,X)" and ~2082 "Nested
T+Z"). Add the parallel TIFF branch: after the structured reader gives `n_t`/`n_z`/`n_c`, route TIFF:
- `n_z>1, n_t==1` → `_TiffPageStackZYX`
- `n_z>1, n_t>1`  → `_TiffPageStackTZYX`
- `n_z==1`        → `_TiffPageStack` (unchanged TYX path)
Attach `metadata['pycat_image_source']` (the ImageSource ownership the generic loader now uses — same
as the cleanup gave the other branches) and `pycat_layer_id` flows from the tag hook. Do NOT route
TIFF Z/TZ to the BioIO/zarr path — that's the broken path this replaces.

## Part C — anisotropic Z scale (consistency of PHYSICAL units too)
"Consistent across all I/O" includes the z-scale, not just the array shape. Verified contract:
`z_step_um` comes from `physical_pixel_sizes.Z` and drives anisotropic voxel volume
(`test_anisotropic_voxel.py`), NaN when unknown (never a plausible lie). So:
- The TIFF Z/TZ layers must set `layer.scale` with the **real z-step from TIFF metadata** when present
  (ImageJ/OME `spacing`/`PhysicalSizeZ`), exactly as the IMS path does — so a ZYX TIFF and a ZYX IMS
  of the same specimen produce the SAME voxel volume.
- When the z-step is absent, follow the existing honest-unknown rule (NaN / the unknown-scale
  placeholder + provenance flag), NOT a guessed 1.0. Reuse `pixel_size.z_step_um` /
  `z_step_um_or_default` — do not invent a second z-scale path.

## Part D — the cross-reader consistency test (the deliverable that proves "consistent")
Add `tests/test_ztz_readers_agree.py` (mark `core`). This is the point of the spec:
1. **Same-contract test:** for a synthetic Z-stack written BOTH as a TIFF and read via the TIFF ZYX
   wrapper AND constructed via the IMS ZYX wrapper (or a stub matching its interface), assert identical
   `shape`, `ndim`, `dtype`, `__len__`, and identical `__getitem__` results for: `[0]`, `[2]`,
   `[1:3]`, `[:, y0:y1, x0:x1]`. Same for TZYX across the T and Z select combinations
   (`[0]`,`[0,0]`,`[0,:]`,`[:,0]`).
2. **Bit-identical plane test:** a TIFF Z-stack plane read through `_TiffPageStackZYX[z]` equals the
   direct `read_tiff_plane(path, z=z, n_z=Z)` (mirror `test_tiff_planes.py::…BIT_IDENTICAL…`).
3. **No-eager-read test:** `np.asarray` on either TIFF Z/TZ wrapper REFUSES (the guard), like every
   other lazy wrapper (mirror `test_no_eager_reads.py`).
4. **Anisotropy test:** a TIFF with a known PhysicalSizeZ yields the same `z_step_um` (and thus voxel
   volume) the IMS path would; an unknown one yields NaN (extend `test_anisotropic_voxel.py`'s
   contract to the TIFF reader).

## Steps
1. Add `_TiffPageStackZYX` + `_TiffPageStackTZYX` to `lazy_sources.py`, contract-matched to
   `_ImsReader*`. Re-export from `file_io.py`.
2. Add the TIFF Z/TZ routing branch in the generic loader; attach ImageSource + scale.
3. Wire the TIFF z-step into `layer.scale` via the existing `z_step_um` path; honest-unknown otherwise.
4. Add `tests/test_ztz_readers_agree.py` (the four sub-tests). Extend `test_anisotropic_voxel.py` if
   cleaner than a new case.
5. Full `pytest -m core` green — especially `test_tiff_planes.py`, `test_no_eager_reads.py`,
   `test_anisotropic_voxel.py`, `test_one_plane_reads_one_plane.py`, and the lazy_sources headless
   test (the two new wrappers must not drag in Qt either).
6. Update `roadmap.rst`: mark "Z-stack and T+Z TIFF go through BioIO's broken zarr path" RESOLVED —
   TIFF now reads Z/TZ natively via `lazy_sources.py`, consistent with the IMS contract.
7. Ship: own version + PyPI push + commit (EXPLICIT filenames: lazy_sources.py, file_io.py, the loader
   scale wiring, the tests, roadmap.rst, pyproject, CHANGELOG) + CHANGELOG entry.

## Definition of done
- A z-stack TIFF and a T+Z TIFF LOAD and SCRUB (they fail today).
- The TIFF Z/TZ wrappers are byte-for-byte contract-identical to the IMS ones (the agreement test
  proves it): same shape/ndim/dtype/getitem-squeeze/len/refusal.
- Z physical scale matches IMS for the same geometry; unknown z-step is NaN, never guessed.
- No wrapper materializes the stack; no Qt dragged into `lazy_sources.py`.
- Full `pytest -m core` green; roadmap item marked resolved.

## Cautions
- **Match the IMS contract exactly** — copy the `__getitem__` squeeze from `_ImsReaderTZYX`, don't
  reinvent it. A subtly different squeeze is the kind of inconsistency this spec exists to prevent.
- Normalize through `to_unit_float32`, never raw `astype(float32)` (intensity semantics — the same
  trap the cleanup's item 5 guarded).
- Keep the wrappers Qt-free; they live in `lazy_sources.py` precisely so they're headlessly testable.
- Do NOT touch the TYX `_TiffPageStack` behaviour — it's the validated time-series path. Add alongside.
- The generic loader branch must attach ImageSource (ownership) like the other branches — don't leave
  the new Z/TZ layers relying on any removed `_stack_lazy_refs`.
- If the structured reader can't determine n_z reliably for a given TIFF, fall through to the existing
  "ask the user: time-series / z-stack / separate images" dialog rather than guessing — that
  disambiguation path already exists (file_io ~1700).
