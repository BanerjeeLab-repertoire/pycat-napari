# CZI reader bake-off — libCZI vs BioFormats (2026-07-15)

**Machine:** `C:\Users\Gable\Documents\pycat-napari`, env `pycat` (Python 3.12, numpy 2.0.2).
**Readers:** `bioio-czi` (libCZI, the default `.czi` path) vs `bioio-bioformats` (the `[bioformats]` extra).
**Files:** all 4 in `C:\Users\Gable\Desktop\A pycat test data\`.

Fulfils Task 1 of `claude_code_spec_czi_2026-07-15.md`. **It also overturns two of that spec's
premises** — read "Two premises that no longer hold" below before building Task 2.

## Results

| file | dims / shape | libCZI open | libCZI read | BioFormats (see notes) |
|---|---|---|---|---|
| `Image 28.czi` (confocal, C=4) | TCZYX (1,4,1,512,512) | 0.78 s | ✅ 0.5 s, mean 12 | n/a — libCZI is fine |
| `Image 5.czi` (confocal, C=3) | TCZYX (1,3,1,1024,1024) | 0.06 s | ✅ 0.009 s, mean 188 | n/a — libCZI is fine |
| `ntr wt 50mM Mg4.czi` (**widefield, 1 subblock**) | TCZYX (1,1,1,1460,1936) | 0.05 s | ✅ 0.02 s, mean 2526 | n/a — libCZI is fine |
| `Movie 5 …tphase40-004.czi` (**streaming, T=15766, 8.1 GB**) | TCZYX (15766,1,1,500,500) | 0.07 s | ❌ `RuntimeError: not implemented` | ✅ reads (see perf) |

## The discriminator result (decides the routing rule)

**libCZI reads the widefield single-subblock file (`ntr wt`) perfectly.** So the trigger for the
libCZI failure is **NOT** "widefield" and **NOT** 12-bit-Gray16 — it is the **streaming / many-subblock
layout** specifically (the 15,766-frame movie). Confocal and widefield-single-frame CZI all read fast
with no JVM.

**→ Routing rule (a) is correct:** for `.czi`, try libCZI first; on a pixel-read failure
(`not implemented`) fall back to BioFormats when the `[bioformats]` extra is installed. This keeps
fast, no-JVM reads for the common case (3 of these 4 files, and all normal confocal CZI) and only pays
the BioFormats cost for the streaming layout libCZI genuinely cannot decode. Do **not** route all CZI
to BioFormats.

## Two premises that no longer hold (READ BEFORE BUILDING TASK 2)

The spec/audit's BioFormats plan was validated on `bioio-bioformats 2.0.0` on numpy 2.0.2, reading via
`get_image_dask_data(...).compute()` at ~0.03 s/plane. **Neither reproduces today:**

### 1. `bioio-bioformats 2.0.0` is numpy-incompatible now
`bioio-bioformats 2.0.0` → `bffile 0.1.1` → **`numpy>=2.1.0`**. PyCAT pins `numpy<2.1` (cellpose +
numba), so 2.0.0 is **uninstallable** without breaking the base env. Only `bioio-bioformats 1.3.2` is
numpy-safe, and it hard-pins Java **BioFormats 6.7.0**, which reports *"does not support"* this CZI.

### 2. bioio's dask read is ~1000× too slow here; the direct reader is fast
Two things had to be worked around to read the streaming file at all, then to read it fast:

- **Maven resolution:** BioFormats' `woolz:JWlz:jar:1.4.0` transitive dep is only in OME's
  artifactory, not Maven Central/scijava.public. Register it before the JVM starts:
  `scyjava.config.add_repositories({'ome': 'https://artifacts.openmicroscopy.org/artifactory/maven'})`.
- **BioFormats version:** 6.7.0 (what 1.3.2 pins) can't read the file; **8.1.1 can.** Start the JVM
  ourselves with `scyjava.config.endpoints.append('ome:formats-gpl:8.1.1')` before `bioio_bioformats`
  appends its 6.7.0 (a no-op once the JVM is already up).
- **Read path:** with 8.1.1, `img.get_image_dask_data("YX", T=t).compute()` took **50–80 s/plane**
  (a bioio-1.3.2 dask-wrapper artifact, not BioFormats). The **direct Java reader is 10,000× faster**:

  ```
  loci.formats.ImageReader().setId(path)      -> 32.9 s   (one-time: parse 15,766 subblock offsets)
  reader.openBytes(reader.getIndex(z,c,t))    -> 0.004–0.009 s / plane   (min 0, max 65535, mean ~13000)
  ```

  This mirrors what PyCAT already does for TIFF (`tiff_planes.read_tiff_plane` seeks the page directly
  rather than going through bioio's broken/slow `aszarr()`).

**→ Revised build for Task 2:** for a streaming CZI, read pixels via a lazy wrapper backed by
`loci.formats.ImageReader.openBytes` (numpy-safe, ~5 ms/plane), **not** bioio's dask. Use `bioio-czi`
for dims/metadata (it opens the streaming file fine) or the loci reader's own sizeT/C/Z + metadata
store. Configure the OME repo + `formats-gpl:8.1.1` before JVM start. The ~33 s `setId` is the
one-time open cost Task 3's worker thread must hide; per-plane reads are already scrubbable, so Task 4
(prefetch/cache) is a nice-to-have, not required, on this path.

## Environment notes
- `bioio-bioformats 1.3.2` installed; numpy held at 2.0.2. ✅
- Java 1.8 present; scyjava/jgo downloads its own JDK + BioFormats jars on first use.
- Bake-off scripts: `$CLAUDE_JOB_DIR/tmp/bakeoff1.py`, `bf_newver.py`, `bf_probe_speed.py`.
