# The BioIO migration and the eager-read arc — 1.6.0 → 1.6.13

**2026-07-13.** Written to be reviewed and revisited, not to be admired. Where a decision was wrong,
it says so and says what it cost.

---

## 1. What this was

`aicsimageio` is in **maintenance mode**, frozen in 2023, and its maintainers name `bioio` as the
**compatible successor**. It pins:

```
zarr<2.16    tifffile<2023.3.15    fsspec<2023.9    lxml<5
```

***Those pins are what held `numpy<2` and `zarr<3` in place.*** They are not an obstacle to the
migration — **they are the reason for it.**

So the migration is a **removal**, not an addition. Removing `aicsimageio` *frees* the pins rather
than fighting them.

---

## 2. The sequence, and why it was staged

| stage | release | what it established |
|---|---|---|
| **Reader seam** | 1.5.529 | one place constructs the reader; `PYCAT_IMAGE_READER` switches it |
| **Prove BioIO reads identically** | *(probe)* | 38 files, two environments, compared offline |
| **zarr version-agnostic** | 1.5.533 | `DirectoryStore` → `LocalStore`; ships while `zarr<3` still pinned |
| **Cut over** | **1.6.0** | remove aicsimageio, flip the default, free the pins |

**v1.5.532 is the last wholly BioIO-free release** — the revert point. It is tagged.

### The two libraries cannot coexist

Installing `bioio` alongside `aicsimageio` pulled in **numpy 2.5.1, zarr 3.2.1, tifffile 2026.6.1**,
*uninstalled the pinned ones*, and broke **cellpose, numba and the image loader in one command.**

That is why the probe runs each library **in its own environment** and compares **offline, on JSON**.

---

## 3. The probe result — and why it was insufficient

**38 files. 31 identical. 0 different.** Shape, dtype, **dimension order**, **physical pixel size**,
scenes, **and a SHA-256 of the pixels** — including the Zeiss CZI, `3.30 hr_1_MMStack_Pos0`, every
OME-TIFF, every in-vitro TIFF, every batch output.

*(6 `.ims` "failures" were a non-finding: **neither** library reads them, and **PyCAT does not use
either** — it routes `.ims` to `imaris_ims_file_reader`, its own HDF5 reader. The probe tested a path
PyCAT does not take.)*

### The thing it could not see

> ***It measured correctness and nothing about cost.***

A loader that reads the **entire scene** to fetch **one plane** passes every one of those checks
while **freezing the application** — the pixels that come back are perfectly right.

**The freeze was invisible to it by construction**, and it took Gable saying *"it lags"* to find it.

The external audit named this exactly, and it was the most important paragraph in it:

> *"…did not compare bytes read during construction; peak resident memory; time to first plane;
> number of source-file reads; **whether a one-plane request materializes a scene.** That is exactly
> why the migration passed correctness testing while regressing user experience."*

---

## 4. The eager-read arc

### 4.1 `get_image_data()` loads the entire scene

**Both libraries document it in the same words:**

> *"The `.get_image_data` function will **load the whole scene into memory** and then retrieve the
> specified chunk."*

`get_image_dask_data()` is the lazy one.

**PyCAT called the eager one in EIGHT places** — including to read a *single plane* in order to
**classify** a file. On a large 4-D acquisition that reads the whole scene **to look at one frame**,
and it happens **more than once per file**, because the reader was constructed 3–4 times before
anything was displayed.

**All eight now go through `read_plane()`.** *(The audit found 3; there were 8 — it missed
`open_2d_mask`, `ui_modules`, and `batch_step_registry`.)*

#### This is NOT a BioIO regression, and the distinction matters

**aicsimageio documented the same eager semantics.** ***The calls were wrong in 1.5.x too.***

What the migration did was **expose** them: `bioio-czi` uses `pylibczirw` rather than
`aicspylibczi`, and a different TIFF reader — **the same mistake can cost very differently.**

*Chasing "what did BioIO break?" would have been chasing a phantom.*

### 4.2 `__array__` quietly stacked every frame

`np.asarray(layer.data)` on a lazy stack has now cost this project **three bugs**:

- **N&B** told users their movie was 2-D
- **SpIDA** silently analysed frame 0 while they looked at frame 40
- **the IMS scrubbing lag** — *and this one was documented in PyCAT's own source, months ago:*

  > *"napari auto-estimates contrast (and builds the thumbnail) by calling `np.asarray()` on the
  > layer — which for a lazy (T,Y,X) wrapper triggers `__array__` and **loads EVERY frame from
  > disk**. On a USB-HDD IMS stack **that is the real cause of the multi-second stalls**."*

**All ten `__array__` methods now refuse**, through one shared `lazy_guard.refuse_implicit_full_read()`.

#### The mistake worth remembering

**1.6.3 fixed THREE of NINE** — the three in `multidim_io` — and **the guard only looked at
`multidim_io`**, so it passed while six identical landmines sat in `file_io`, *including all three
IMS wrappers, which are the ones that lag.*

> ***A guard whose scope is narrower than the bug will certify the half that was fixed.***

### 4.3 The TZYX branch transcoded the whole file before showing anything

```python
for t in range(n_t):
    for zi in range(n_z):
        z[t, zi] = np.asarray(dask_arr[t, zi])
```

**Every (t, z) plane, decoded and written to a temporary zarr, on the synchronous path, before the
first pixel reached the screen.** *It was not accidentally eager — it was a deliberate full-file
copy, and the note beside it said "nothing pre-loaded beyond this write pass", which was true and
which was the whole problem.*

**The dask array was already lazy.** `_LazyArraySource` wraps it directly.

### 4.4 A metadata defect cost a gigabyte

`except Exception` caught **everything** — a channel name, a pixel size, a scene entry — and dropped
PyCAT into `tifffile.imread(file_path)`, **reading the whole file into memory.**

**And the 1.6.4 "fix" for this never ran.** It called `_TiffPageStack(file_path)` with **one
argument where five are required** — it raised `TypeError`, was caught by the surrounding `except`,
and **fell straight through to the eager read anyway.**

> **It compiled. The tests were green. The eager read happened every single time.**
>
> ***A malformed call inside a `try/except` is invisible.***

Gable caught this by asking *"is item 8 fixed?"*. It is now, and the **arity of every
`_TiffPageStack(...)` call is checked statically.**

---

## 5. TIFF does not go through BioIO

### The error message is a lie

```
ValueError: zarr 3.2.1 < 3 is not supported
```

***3.2.1 is not less than 3.*** The real failure is **one frame up, where nobody looks**:

```
ImportError: cannot import name 'RegularChunkGrid' from 'zarr.core.chunk_grids'
```

**zarr 3.2 renamed that class.** `tifffile`'s zarr store catches **any** ImportError from its zarr-3
module and **blames the version** — so a user chasing this goes looking for an old zarr that is not
there.

### And PyCAT's own lazy-read fix is what walked into it

| | |
|---|---|
| **before 1.6.3** | `get_image_data()` → decodes the page directly → *tifffile's zarr store is never touched* |
| **after 1.6.3** | `get_image_dask_data()` → `tif.aszarr()` → **boom** |

> ***The old path worked precisely because it was doing the wrong thing.***

### The fix: keep TIFF off BioIO's pixel path entirely

`tifffile` seeks a single page **directly** — no zarr, no dask graph, no OME plane-map walk. **It is
faster than the BioIO path even when BioIO works**, which is why `_TiffPageStack` was written in the
first place.

**BioIO still supplies dimensions, scenes, channel names and pixel size for TIFF.** *None of that
goes near the zarr store.*

*Pinning zarr would re-pin the stack the migration existed to free — and it would be a guess: nobody
knows which zarr 3.x `tifffile 2026.6.1` was built against.*

### The dead end inside the fix

`read_tiff_plane` first **declined** on a multi-file OME set, on the reasoning that *"the caller
falls back to BioIO."*

> ***But for TIFF, BioIO is exactly what is broken.***

**The decline handed the file to a path that cannot work**, and the 1200-frame MMStack came back as
`ValueError: zarr 3.2`.

*A fallback that does not exist is not a fallback. It is a dead end with a comment explaining why it
is safe.*

**tifffile resolves the multi-file set itself** — `series` walks the OME-XML, finds the companions,
and exposes one page list spanning them. **Multi-file comes for free.**

---

## 6. The pixel-size gate: the file was lying

Gable: *"the pixel size gate does not fire on an image I know lacks a proper scale."*

**It was firing correctly. The FILE was lying.**

```
XResolution    = 2147054150 / 4999   ->  0.0023 nanometres per pixel
```

***Smaller than a hydrogen atom.*** And **`2147054150` is a hair under `2³¹`** — a **signed-integer
overflow in ImageJ's Substack export**, which is exactly the operation that produced the file.

PyCAT saw a number that was **not `None`** and **not the `1.0` sentinel**, concluded the file carried
a real scale, and hid the gate. *(Which is why the **parent** TIFF and the **bead** TIFF **did** fire
it — they carry no resolution tag at all.)*

**The gate now asks *"could a microscope have produced this?"*** — bounds from **Abbe and Nyquist**,
deliberately loose (**1 nm to 1 mm per pixel**). Every real instrument passes; **the corrupt file
misses by 400×.**

**It warns and prompts. It does not block.** A corrupt tag is treated **exactly like a missing one** —
the only difference is that the warning **names the ImageJ overflow** rather than saying *"resolution
data incomplete"*, ***which would be a lie of its own.***

---

## 7. Two subsystems, one pydantic trap

**napari's `Viewer` is a pydantic model.** `setattr(viewer, 'add_image', ...)` is **rejected** —
pydantic permits only **declared fields**, and `add_image` is a *method on the class*.

> ***And the whole layer-tagging system was silently dead.***

`run_pycat` wraps the install in `except Exception: debug_log(...)`, so PyCAT started with **no tag
hook at all.** Every layer went **untagged**. The registry, the resolver, the binding table, the Tag
Inspector, the autopopulation groundwork — ***all inert***, and the only sign was a traceback that
read like a napari bug.

**`object.__setattr__`** bypasses validation and writes to the instance `__dict__`.

**The status bar had the same trap**, and a race besides: PyCAT wrote `viewer.status` on mouse-move,
**and so does napari** — whichever ran last won, and the bar **alternated between two strings.**
*Racing napari's writer cannot be won.* The readout now wraps the layer's `get_status()`, which is
where napari **sources** the string.

> **The same bug, twice, in two subsystems.**

---

## 8. The reader cache, and the bug it introduced

A single drag-and-drop constructed the reader **three to four times.** **Construction is not free** —
it parses OME-XML, walks the TIFF series, **reads the CZI subblock directory.**

**Cached in the seam: 4 opens → 1 construction.** All seven call sites benefit; none had to change.

### But BioIO readers are STATEFUL

`set_scene()` **mutates** the reader. With a cache, two call sites hold the **same object** — so a
site that moved to scene 2 left the next caller's reader **parked on scene 2**, reading **the wrong
field of view**, with ***nothing about the image looking broken.***

> ***I introduced that in 1.6.6, while fixing something else.***

A cached reader is now **rewound to its first scene** before it is handed out, or **dropped and
rebuilt** if it cannot be.

---

## 9. Packaging: six install routes still shipped aicsimageio

`requirements-base.txt`, `meta.yaml`, and **four conda lockfiles.**

**The lockfiles were worse than stale.** They were **exported conda environments pinned to Python
3.9** — and PyCAT requires **`>=3.12`. They could not have worked.** They also pinned
`aicsimageio=4.10.0`, `numpy=1.23.5`, `tifffile=2023.2.28`.

***And the README told developers to build from them.***

> **Until this was fixed, no performance report from any user was interpretable.**

Deleted. `config/README.md` records what went and why. **Guarded.**

---

## 10. The measurement — and three badly-posed thresholds

The audit's closing warning is closed with a **measurement**, not an argument from the code.

### Bytes-read does not work, and I found that out by testing it

The OS **page cache** serves a warm file from RAM, and `tifffile` **memory-maps** — pixels arrive by
*page fault*, not by `read()`:

```
lazy  read:  0 bytes
EAGER read:  0 bytes      <- the WHOLE SCENE, and the counter saw NOTHING
```

***A metric that reports zero for the bug it exists to catch is worse than no metric.*** `psutil`'s
I/O counters are blind to it too.

**Peak ALLOCATION is immune.** An eager read **must allocate the whole scene**, and *the cache cannot
hide an allocation.*

### The three thresholds, in order

**1. `amplification < 3x`** — ***vacuous.*** For a `T=1 Z=1 C=1` file, **one plane IS the whole
scene**, so the ratio is **1.0× by construction.** **30 of 32 real files could not fail**, and the
green result hid the two that were talking.

**2. `fraction of scene < 15%`** — flagged a **57 KB plane in a 600-frame file at 3.7×**, which is
**0.6% of a 34 MB scene.** *Fixed overhead — page tags, OME-XML, a numpy temporary — reads as 3.7× on
a small plane and 0.01× on a big one.*

**3. …and that same fix then flagged the 3-CHANNEL files.** **One plane out of a 3-plane scene
necessarily IS 33% of it.**

> ***A correct loader must hit the floor, and I was calling the floor a failure.***

### The framing that needs no invented constant

The **file** sets both bounds:

```
floor   = one plane        (what a CORRECT loader allocates)
ceiling = the whole scene  (what a BROKEN one allocates)
```

**Where peak sits between them** is scale-free and plane-count-free.

**And it only has power when the bounds are far apart.** With N planes, a correct read allocates 1/N
of the scene — at N=3 that is 33%, and *the whole window is 3×, which overhead alone can cross.*
**Below 10 planes the metric says "cannot tell"** rather than grading. ***Pretending otherwise is
exactly how the last two thresholds went wrong.***

### The harness measured ONE of FOUR lazy paths

Gable: *"since we have ims loading lazily why are we not trying to time them in the same way? the
issues with lazy not being so lazy were there for everything."* **He was right.**

| format | lazy wrapper | tested? |
|---|---|---|
| `.ims` | `_ImsReaderTYX/ZYX/TZYX` | **NO — skipped entirely** |
| `.tif` | `_TiffPageStack` | **NO** |
| `.czi` | `_LazyArraySource` | **NO** |
| any | *(none — `read_plane`)* | **YES** — *only this* |

> ***And the bug he actually FELT — the IMS scrubbing lag — lived in `_ImsReaderTYX.__array__`,
> which none of that ever touched.***

*I measured the path I had fixed, not the path that broke.*

### The result

Measured on real files, on real disk:

| file | planes | of scene | per frame |
|---|---|---|---|
| **polyA MMStack** | 214 | **2.3%** | 0.006 s |
| **post_1_0.5.ims** | 600 | **0.5%** | 0.013 s |
| **post_1_0.5_1.tif** | 600 | **0.5%** | 0.007 s |

**Scrubbing one frame allocates one frame** — on the IMS file that was lagging, and on the MMStack
that was throwing `zarr 3.2` two runs earlier.

***This is the first evidence in the whole arc that is not an argument from the code.***

---

## 11. Guards, and what each one exists to stop

| guard | stops |
|---|---|
| `test_no_eager_reads` | `get_image_data()` anywhere; any `__array__` that materialises; any lazy layer without pinned contrast limits |
| `test_one_plane_reads_one_plane` | a one-plane request allocating the scene — **through the reader AND through the wrapper**; *and it tests that the metric CATCHES an eager read* |
| `test_tiff_planes` | TIFF pixels going through BioIO; a wrong page mapping; **a `_TiffPageStack(...)` call with too few arguments** |
| `test_install_routes_agree` | any route shipping aicsimageio; any route missing `bioio-czi`; the Python-3.9 lockfiles returning |
| `test_reader_cache` | a stale reader after the file changed; **a cached reader handed out on someone else's scene** |
| `test_tag_hook_installs` | `setattr` on the pydantic Viewer — *which silently killed the entire tag system* |
| `test_zarr_compat` | a `DirectoryStore` class-check that breaks on zarr 3 |
| `test_pixel_size_plausibility` | a physically impossible pixel size being trusted |
| `test_no_stale_reader_names` | `use_aicsimage`; comments describing current behaviour as "AICSImage" |
| `test_numpy2_ready` | any numpy-2-removed API |

---

## 12. A pattern worth naming

**Three times in this arc a guard checked a comment rather than the code**, and once it flagged
**its own docstring** — the one explaining why the bug was dangerous.

> ***A guard that cannot tell code from prose will eventually flag its own explanation — and the fix
> is not to stop explaining.***

All of them now walk the **AST**.

---

## 13. Still open

See `roadmap.rst` (**Loader & I/O**) and §*Known issues* in these notes.

- **`pycat_perf.py` cannot build a wrapper for the four multichannel `optofuspld` IMS files**
  (`OSError: Can't synchronously read data`). **The files open and scrub correctly in PyCAT**, so it
  is a bug in the diagnostic script's reader construction, **not in PyCAT.** *Recorded rather than
  dismissed: it means the harness is not exercising the multichannel IMS wrapper, and that is a real
  gap in coverage even though it is not a product bug.*
- **Item 4 was CACHED, not RESTRUCTURED.** The audit asked for the reader to be passed through the
  dispatch chain. The cache makes the re-opening free; `ImageStructure` carries the inspection. But
  seven call sites still independently reach for a reader. **Cheap, not clean** — and worth saying so.
- **Z and T+Z TIFF stacks still go through BioIO**, and therefore through the broken zarr path.
  `_TiffPageStack` handles **TYX** natively; it does not handle Z or T+Z. **They now report the real
  cause** instead of tifffile's misleading message, but they do not work.
