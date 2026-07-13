# PyCAT — Private Development Notes

> **PRIVATE / NOT PUBLISHED.** This file lives in `docs/audits/` which is *outside*
> the Sphinx source tree (`docs/source/`) and is not in any `toctree`, so it is never
> built into the public documentation. It intentionally contains lab-specific detail
> (instrument inventory, hardware-gated scoping, machine/collaborator-specific bug
> notes) that must **not** appear in anything scraped or shown externally. Keep
> equipment names and internal scoping here, not in `roadmap.rst`.

---

## 1. Lab / instrument base (scoping reference — keep private)

Informs which advanced analysis modules are worth building; scope modules to real
hardware. **Do not list this equipment in published docs.**

- **LUMICKS C-TRAP** — optical-tweezers + confocal, 3 lasers. Source instrument for
  the existing FRAP / droplet-fusion / force-distance modules; Lumicks `.h5` files.
- **ISS Q2** — laser-scanning confocal with APDs on 3 channels; does FLIM and
  point-detector FCS/FCCS. Native instrument for single-point FCS/FCCS and FLIM.
- **Andor Dragonfly** — spinning-disk confocal with TIRF/HILO, Micropoint, Mosaic,
  two pinhole discs (25 & 40 µm). Two cameras: Andor iXon 888 EMCCD (1024×1024,
  back-illuminated) and Andor Zyla sCMOS; both fast (~1000 fps with cropping). For
  camera-FCS the **Zyla (sCMOS)** is correct (no EM gain, well-characterized per-pixel
  noise); the iXon EMCCD is weaker for FCS (EM gain adds multiplicative excess noise
  ~√2 on variance). Dragonfly TIRF/HILO gives a defined thin axial volume, so
  **Dragonfly TIRF/HILO + Zyla is a viable present-day imaging/camera-FCS testbed**
  (spinning-disk is a messier volume model). Unconfirmed: can Dragonfly TIRF route to
  the Zyla port? sCMOS imaging-FCS also needs a per-pixel offset/gain/variance
  calibration map (the N&B documented hook).
- **Leica Stellaris** (campus access) with LIGHTNING deconvolution + a STED path.
- **Zeiss Airyscan 2** — incoming access (super-resolution detector-array confocal).
- **Photometrics Kinetix sCMOS** — currently on a Zeiss Axio Observer with LED
  widefield (epi, no defined axial slice → not ideal for FCS). Future: Kinetix moves
  to a custom ring-TIRF/HILO scope, with planned PolScope (polarization/birefringence
  orientation) and FLIM on the other illumination path.
- Confocal imaging source for the 2D/TS condensate pipelines: Zeiss/ZEN Plan-Apo
  63×/1.40 Oil, 405 + Argon 488, 3 PMTs, 0.0264 µm/px, DAPI/EGFP/transmitted.

**Implications:** single-point FCS/FCCS and FLIM are native to the ISS Q2 (PyCAT does
phasor/lifetime *downstream* analysis + import, not raw TCSPC); PolScope/orientation
pairs with the future Kinetix polarization path.

---

## 2. Advanced spectroscopy / imaging module roadmap (hardware-scoped)

All are **import-and-analyze** modules: PyCAT quantifies what the specialized
instrument produces; it does not reimplement acquisition. Ordered by data-availability.

**Buildable now (data exists today):**

- **FCS/FCCS analysis module** — native to the ISS Q2 (APDs, 3 channels). Import
  point-detector correlation curves / photon streams; fit diffusion models
  (single/multi-component, triplet, anomalous); FCCS cross-correlation amplitude ratio
  → bound fraction. Composes with the fluctuation-spectroscopy family (MSD/VPT
  viscosity, N&B, SpIDA). *Prereq:* confirm what the Q2 exports (correlation curves vs
  raw photon stream vs `.fcs`). Extend `correlation_func_analysis_tools.py` (already
  has spatial ACF/CCF + Gaussian-peak fitting, ICS-style); N&B (`nb_tools.py`) already
  flags per-pixel sCMOS variance/offset correction.
- **FLIM phasor + downstream module** — native to ISS Q2. Import-based (no raw TCSPC).
  Import lifetime/decay → phasor-plot analysis (model-free lifetime separation) →
  segmentation-linked biology (per-condensate mean lifetime, lifetime vs partition
  coefficient, lifetime shift across a phase transition). The distinctive "PyCAT owns
  the downstream biology" play.
- **RICS / STICS** (raster & spatiotemporal image correlation spectroscopy) —
  highest-leverage near-term. Works on current laser-scanning confocals (ISS Q2,
  C-Trap) with no new hardware; extracts diffusion maps AND flow/velocity fields.
  Reuses the existing spatial-correlation backend. Smaller lift than camera
  imaging-FCS. *Prereq:* confirm the Q2 / C-Trap scanned-image-series export format +
  whether pixel dwell / line time is in metadata (RICS depends on scan timing).

**Future (hardware-gated):**

- **Imaging / multipoint camera-FCS** — flagship but future. Tie to the Kinetix's
  future ring-TIRF/HILO scope, NOT the current Axio Observer + LED widefield (no
  defined axial slice → per-pixel FCS volume not well-posed). Per-pixel temporal
  autocorrelation of a fast sCMOS stream → diffusion/concentration maps. Hard parts:
  per-pixel sCMOS noise/variance/offset correction, frame-timing calibration,
  bleaching, TIRF/HILO axial volume model.
- **PolScope / orientation analysis** — future, pairs with the Kinetix polarization
  path. Import orientation/birefringence/anisotropy maps → orientational order
  parameters, anisotropy distributions, orientation-vs-condensate-structure.
- **SMLM / localization-table analysis** — three super-res-capable instruments
  (Dragonfly TIRF single-molecule, campus STED, incoming Airyscan 2) but zero
  localization-table analysis today. Import localization tables (PALM/STORM/PAINT);
  the Ripley/PCF cluster-analysis machinery already exists and applies directly; add
  localization precision + drift correction. LOCAN ecosystem interop lands here.
- **Fluorescence anisotropy / homo-FRET** (steady-state polarization) — precursor to
  PolScope; buildable sooner if any current instrument has a parallel/perpendicular
  channel pair. Two-channel ratiometric-style computation.

*Caveat for all:* some capabilities are inferred from instrument class — confirm the
specific unit configuration (does the Dragonfly config do true SMLM? does any current
instrument have polarization optics installed?) before committing.

---

## 3. Known issues (may name specific machines/configs — keep private)

### GPU / OpenGL canvas corruption on some NVIDIA configs (ACTIVE, not resolved)

**Symptom:** after loading an image, clicking the canvas renders the view
tilted/sheared (diagonal split); scroll/zoom still work (input + data fine — a
display/framebuffer-presentation problem). Earlier empty-canvas clicks showed
triangular tearing / swirling moiré + UI vanishing. Constant flickering appears only
on dim/out-of-focus objects.

**Confirmed NOT a PyCAT bug** — reproduces in plain napari (no PyCAT).

**Environment where seen** (collaborator machine, NVIDIA T400 4GB): Win11, Python 3.12,
napari 0.7.1, vispy 0.16.2, Qt 5.15.2/PyQt5, **NVIDIA driver 595.97**, OpenGL 4.6,
1920×1080 @ 100% single monitor, 75Hz HDMI. Ruled out (via `gpu_diag.py`):
wrong-GPU-selection, integrated graphics, Qt high-DPI scaling, multi-monitor.

**Three OpenGL backends all fail differently:** native NVIDIA → tearing/tilt on click;
`QT_OPENGL=angle` → crashes at startup (GLError 1282 on `glGetParameter(GL_MAX_TEXTURE_SIZE)`,
GL context broken at creation); `LIBGL_ALWAYS_SOFTWARE=1` → opens clean but slow to
start, then tears after repeated clicks.

**Prime suspect:** NVIDIA driver 595.97 (newest; only recently-changed variable; a
tilt/shear = framebuffer stride/row-alignment presentation bug — a classic driver-build
regression that hits OpenGL apps while leaving the desktop compositor fine).

**Next steps (awaiting):** clear env vars (`set QT_OPENGL=` / `set LIBGL_ALWAYS_SOFTWARE=`),
roll back the NVIDIA driver to the prior Studio branch (clean-install checkbox), reboot,
retest plain napari + load image + click/scroll. Open question: is this the only machine
affected, or do others on the same napari/vispy fail? (only → isolate this box's
GPU/driver, rollback is the fix; several → consider pinning napari/vispy.)

**When the fix is confirmed:** write a *public-facing* known-issues note (symptom →
confirm via plain napari → `gpu_diag.py` → resolution = driver rollback; software
rendering as an imperfect last resort). A possible PyCAT `--safe-rendering` launch flag
(sets `LIBGL_ALWAYS_SOFTWARE` before napari init) is opt-in first, but software
rendering was not fully stable here (tore after repeated clicks) so it's an imperfect
stopgap, not a clean fix. `gpu_diag.py` delivered (one-shot GPU/OpenGL/Qt/scaling report
+ opens plain napari for the reproduce test).

### Multi-file OME-TIFF with a missing linked member (hardened 1.5.330)

A multi-file OME-TIFF that references a missing sibling (e.g. a `...MMStack_Pos0.ome.tif`
referencing an absent later-timepoint file) makes the loader zero/truncate the missing
data, and a degenerate 1-D frame could reach `score_beads_template`, crashing with
`ValueError: not enough values to unpack (expected 2, got 1)`. 1.5.330 guards this
(squeeze + skip frame with a warning). Root fix for a *complete* analysis: ensure all
linked `.ome.tif` files are present, or use a self-contained single-file export.

---

## 4. VPT calibrated-thermodynamics scoping notes (lab-specific framing)

The calibrated intensity → concentration → ΔG_transfer workflow (see public roadmap for
the capability description) is scoped against the lab's Csat / phase-diagram /
quantitative-measurement program. This is the flagship manuscript differentiator:
converts PyCAT from "image analysis" to "biophysical parameter extraction." PyCAT today
has intensity-based ratios (partition coefficient, client enrichment) but zero
`delta_g` / standard-curve-concentration code — that gap is the build.

---

## 5. Speculative pre-materialization (hide materialization latency behind config time)

**Motivation.** In several pipelines the *materialization* step (reading/decoding the lazy
stack into a concrete array) is roughly half the wall-clock runtime. The interaction
sequence is: select method → choose layers/options → configure params → click Run →
**materialize** → analyse. The configuration phase is dead time (10-60 s of the user
picking layers, ROIs, thresholds, Cellpose params) during which the required array could
already be loading in the background. Done right, this hides most materialization latency
without touching any algorithm — it's the "time to first result" principle applied to the
wait *after* Run. Fits the responsiveness / data-local thesis directly.

**Trigger on input commitment, NOT panel-open.** Opening a method panel must not kick off a
multi-GB load the user was only browsing (this would also defeat the lazy-by-default
principle). Readiness progression: method opened → nothing; input layer selected → metadata
only; **ROI / frame-range / channel selected → begin background materialization**; params
edited → continue unless they change the required array; Run → consume the prepared array
(or wait only for the remainder, or compute synchronously if prefetch never started).

**Staleness protection is LOAD-BEARING (this is the dangerous part for us).** A stale
prefetch publishing its array after the user changed ROI/channel/layer is exactly the
"looks like the right array but isn't" trap we've hit twice (`_TiffPageStack.__array__`
returning frame 0 only — temperature UI 1.5.253, VPT 1.5.273). And tonight's pixel-size
episode showed how catastrophic a silent-wrong *input* is (a 10× pixel-size slip squared
into a 100× viscosity error with no other symptom). So: a request key must include
everything that determines the concrete array — `(layer_identity, layer_data_version,
slice_selection, roi_bounds, frame_range, channel_selection, target_dtype)`. Method params
(e.g. Cellpose diameter) do NOT invalidate; ROI/layer/channel changes DO. A key mismatch
must force a synchronous recompute and MUST NEVER serve a stale array. Generation IDs +
cancellation tokens; one active materialization per method instance.

**The prefetched object must be the EXACT object the method consumes.** If the method then
independently calls `np.asarray(lazy_data)` again, the array is duplicated and the benefit
is erased. The clean seam already exists: `file_io.materialize_stack()` is the single
chokepoint. Make it prefetch-aware — check a shared manager for a ready array matching the
request key, return it if present, compute synchronously otherwise. Then every existing
method benefits with no per-method changes.

**Memory amplification is the real danger.** Prefetch does not reduce peak memory; it moves
the allocation earlier. If the method then makes a float32 copy + probability maps + label
arrays, several copies stack. Gate the prefetch on estimated headroom:
`prefetched_input + expected_working_set + current_process_RSS < safe_fraction(available_RAM)`
(psutil). If it won't fit: prefetch only the selected ROI/frame block, materialize
incrementally, evict another prefetched result, or skip. On low-RAM boxes (e.g. Meet's
CPU-only machine) aggressive prefetch could push into swap and be *slower* — so headroom-gated,
low-priority, off the Qt main thread, subordinate to any explicit user action.

**Recommended scope (two stages, low-risk first):**
1. **Batch double-buffering** — materialize file N+1 while processing file N. Cleanest,
   highest-value, lowest-risk: the "next file" is unambiguous, so there's no stale-key or
   user-changes-inputs-mid-flight problem, and batch throughput is a manuscript-relevant
   number. Provided memory permits holding active + next dataset.
2. **Interactive method-level prefetch** — start after layer + ROI/frame selection, cancel
   on input change, Run consumes the prepared array. Higher value, higher risk (all the
   staleness/cancellation machinery above).

**Profile first.** Confirm per-pipeline that materialization is actually the wall-clock cost
*and* the user spends real configuration time — the win only exists where both hold. A
method with instant materialization or auto-run gains nothing.

**Architecture.** A shared `MaterializationManager` (request key → task state → cancellation
token → completed array → estimated bytes → last access), states NOT_REQUESTED / QUEUED /
MATERIALIZING / READY / FAILED / CANCELLED / STALE. Optional subtle "Preparing input…"
status to explain background disk/CPU activity, but the work can be silent. This is closer
to *anticipatory evaluation of a lazy computation graph* than to conventional array
pre-warming — and PyCAT's guided step-by-step workflow structure makes it unusually well
suited to it. (Distinct from the earlier, rejected "pre-warm reusable work buffers" idea:
allocation churn is NOT the bottleneck; materialization latency is.)

---

## 6. Pixel-size sensitivity of VPT viscosity (2026-07-10 finding — cautionary)

Viscosity from Stokes-Einstein scales as **1 / pixel_size²** (D ∝ pixel_size², η ∝ 1/D), so a
pixel-size error is *squared* into the viscosity. Tonight: the automated linkers + revised
detection gave ~0.1 Pa·s against a validated ~8.3 reference — a 75× gap that was NOT in
detection, linking, gap-closing, track selection, or mislinks (all independently ruled out:
clean full-length tracks, max step 0.74 px well under the linking ceiling, no track subset
yields 8.3). The whole gap was the **pixel size: 0.067 µm/px, not 0.67** — a 10× decimal slip
→ 100× viscosity factor. With 0.067, both automated linkers land at ~8.4 Pa·s (Greedy/Bayesian
gap=1), matching the reference within a couple percent, through the automated path with no
TrackMate and no manual pruning — i.e. the "opt-out-of-TrackMate" goal is met once the scale
is right. (Pixel size to be confirmed against the actual acquisition, not inferred from the
fact that it matches 8.3.) IMPLICATIONS: (1) strongest possible argument for the pixel-size
acquisition-profiles feature + the load-time gate — a scale slip produces a huge physics error
with no other visible symptom; (2) VPT should warn prominently that viscosity ∝ 1/pixel_size²
and surface the pixel size used in the results; (3) this is the concrete case behind the
"silent wrong input is catastrophic" caution in the pre-materialization staleness notes above.

**Optical-train derivation of the true pixel size (the provenance the file lacked).** This
data has NO pixel size in its metadata, so the scale must be reconstructed from the acquisition
optics rather than read from the file. The rule: `pixel_size_at_sample = camera_pixel_pitch /
total_magnification`. Setup: Zeiss 100× Plan-Apochromat 1.2 NA objective on a Zeiss Primovert,
imaged through a 3D-printed ~10 cm top-path relay that preserves 1:1, onto a FLIR Blackfly USB3
(Sony Pregius-family sensor, ~6.5-6.9 µm pitch). So `~6.5-6.9 µm / 100× = 0.065-0.069 µm/px` —
i.e. **~0.067 µm/px**. The 0.67 that had been used would require a 67 µm camera pixel (10× larger
than any sensor ever made) — physically impossible, confirming 0.67 was a decimal-place slip.
Crucially this derivation is INDEPENDENT of the 8.3 target: the optics give ~0.067 on their own,
and *then* that value makes the automated pipeline reproduce ~8.4 Pa·s — two independent lines of
evidence converging, not a tuned fit. Remaining uncertainty is only in the exact sensor pitch
(0.058 / 0.065 / 0.069 depending on the specific Blackfly model), and since η ∝ 1/px² even a
0.065-vs-0.069 difference is a ~13% shift in the absolute viscosity — so read the exact model /
sensor pitch off the camera (or SpinView/Spinnaker) to pin the absolute value to a few percent.
GENERAL LESSON: when acquisition metadata omits the scale, the pixel size is recoverable from
`objective_mag × relay_mag` and `camera_pixel_pitch` — this is exactly the information the
pixel-size acquisition-profiles feature should let a user store per-instrument (a named profile
like "Primovert-100x-1:1relay-Blackfly" encodes the optical train once).

---

## 7. Audit follow-ups that are lab-specific (2026-07 external code audit)

The public, methodological backlog from this audit lives in
`docs/source/development/roadmap.rst` under *"Scientific validity backlog"*. The items
below reference **our** instruments, samples and workflow, so they stay here.

### 7.1 The glycerol standard is the single highest-value validation we can run

The audit's strongest recommendation is a three-tier validation scheme (analytical →
imaging-realistic simulation → experimental standard). `ValidationLevel` in
`pycat.utils.measurement` can now *declare* a level; nothing yet *earns* one.

For VPT, the experimental tier is cheap and we already have the machinery:

* A **glycerol/water dilution series** spans ~1 mPa·s to ~1 Pa·s with viscosities known
  from published tables to better than a few percent, at a stated temperature.
* VPT's **"no host / full frame"** mode was built precisely for this (bulk medium, no
  condensate to segment).
* It validates the *entire* chain at once — pixel size → detection → linking → MSD →
  D → Stokes-Einstein — which is exactly the chain where a single wrong input (a pixel
  size, a frame interval, a bead radius) silently propagates.

This would move VPT from `EXPERIMENTALLY_VALIDATED` as an *assertion* (currently backed
only by agreement with a hand analysis at ~8.3 Pa·s) to a fact with a quoted accuracy.
**Do this before publishing any absolute viscosity.**

Temperature matters here: glycerol viscosity is steeply temperature-dependent, and `kT`
sits in Stokes-Einstein. Record the stage temperature, not the room temperature.

### 7.2 Bead radius provenance — our actual practice

Recorded so the code's guard rails match reality:

* We take the bead radius from the **manufacturer's specification** (this is now the
  default `radius_source` in the VPT panel).
* We **do** compare the apparent imaged size against that specification as a sanity
  check — it catches a wrong vial, aggregation, or a mis-set pixel size.
* We would **never** feed an image-derived radius into Stokes-Einstein. The imaged blob
  is the bead convolved with the PSF; for our 200 nm beads at ~1.2 NA the PSF is
  comparable to the bead itself, so the apparent size is dominated by the optics. Doing so
  would bias η low.

The `physical_probe_radius` assumption in `viscosity_measurement` encodes this. It flags
`FITTED` as a warning (not a fatal error), because the *check* is good practice — it is
using it as the *input* that is wrong.

### 7.3 Active microrheology (C-Trap) — the correct tool for the crossover

Our condensates run from roughly water to well past honey. In that regime, at the lag
times a camera can reach, the material is **viscous-dominated**: G′ is genuinely ≈ 0, and
noise pushes it negative routinely (on a synthetic η = 7 Pa·s medium, 11 of 20 G′ points
came out negative and 19 of 20 bootstrap CI bands straddled zero — *correct physics*, not
error).

**Passive VPT cannot resolve a G′/G″ crossover for these materials.** The moduli plots now
say so explicitly (1.5.380) and point at active microrheology.

The follow-through is the **optical-tweezers active-microrheology module** on the LUMICKS
C-Trap: drive a trapped bead at known frequency and amplitude, measure the phase lag and
amplitude response, and get G′/G″ directly across a frequency range passive tracking cannot
reach. This is the module the plot is telling users they need.

### 7.4 Sample-specific null models for spatial statistics

The audit's point that "uniform randomisation inside a cell is usually not an adequate
null" is particularly true for our systems: nuclear condensates are excluded from
nucleoli, constrained by chromatin territories, and often boundary-preferring. A CSR null
will report significant clustering for a spatial arrangement that is entirely explained by
the compartment geometry. Any spatial-statistics claim we publish needs a
compartment-constrained null, not CSR.

### 7.5 Segmentation sensitivity as a coarsening confound

The audit flags that a fitted coarsening exponent cannot distinguish Ostwald ripening from
Brownian coalescence from **changing segmentation sensitivity over time**. That last one is
ours to worry about: as condensates grow and brighten, a fixed threshold detects *more* of
them and detects them *larger* — which mimics a growth law. Any coarsening exponent we
report should be accompanied by the supporting signatures (number density, total dense-phase
area, mass conservation) and by evidence that the segmentation did not drift.


---

## VPT viscosity: the settled parameters, and an apparent regression (2026-07-12)

**This section exists because the same facts were lost twice and re-derived wrongly both
times. They are recorded here so that does not happen again.**

### The settled acquisition parameters

| parameter | value | how it was established |
|---|---|---|
| **pixel size** | **0.067 µm/px** | Reasoned through from the optics with Gable. An earlier value of 0.67 µm/px was an **error** and appears in older notes and transcripts — it is wrong by 10×, which is **100× in the MSD** (distance squared) and therefore 100× in D and in the viscosity. |
| **frame interval** | **0.1 s** | Settled with Gable. |
| bead radius | 0.100 µm | 200 nm beads. |
| temperature | 24 °C | |
| reference viscosity | **8.325 Pa·s** | Validated by an experienced user through PyCAT detection → **TrackMate** linking at v1.5.329. |

### The metadata for this file cannot be trusted — at any depth short of per-frame timestamps

``3_30_hr_1_MMStack_Pos0_ome2.tif`` is a MicroManager acquisition that was **re-saved through
ImageJ**, which **stripped the per-image metadata**. ``tifffile`` reports
``is_micromanager = False``. What survives is a 1070-byte summary blob containing **two
different, both-wrong answers and no right one**:

* ``"Interval_ms": 0.0`` — the field that is *supposed* to hold the cadence. It is **zero**.
* ``"Acquisition comments: 500ms interval"`` — a **free-text human note**. It *reads* as
  authoritative, it is the only number in the file that looks like an interval, and **it is
  wrong**: the true cadence is 100 ms.
* ``"CustomIntervals_ms": []`` — empty.

**A plausible-looking interval from a summary field or a comment is not evidence.** Reading
500 ms where the truth is 100 ms inflates the reported viscosity **five-fold**. This is now
documented in ``_extract_frame_interval_s`` (``metadata_extract.py``), which correctly returns
``(None, None)`` rather than guessing. *Do not relax that.*

(Note: the file Gable uploaded is a substack made for upload-size reasons. The original file's
metadata was dumped in an earlier session. The trap above may be an artifact of the substack —
but the **lesson stands regardless**, because the substack is what a user would hand the tool.)

### The measured chain — two days ago vs now

**2026-07-10** (transcript ``2026-07-10-21-50-11-pycat-vpt-tagging-tools``), automated linkers,
after the classifier-flicker fix:

| linker | gap | tracks | D (µm²/s) | **α** | **η (Pa·s)** |
|---|---|---|---|---|---|
| GREEDY | **1** | 90 | 0.0003 | **0.928** | **8.452** |
| BAYES | **1** | 91 | 0.0003 | **0.927** | **8.443** |
| GREEDY | 0 | 182 | 0.0002 | 1.029 | 10.862 |
| BAYES | 0 | 183 | 0.0002 | 1.009 | 10.769 |

**The two automated linkers reached 8.44–8.45 Pa·s with α ≈ 0.93 — matching the 8.325
reference.** ``gap=1`` was the winning setting; ``gap=0`` gave 10.8.

**2026-07-12** (this session), same file, same settled parameters, current HEAD:

| linking distance | drift | D (µm²/s) | **α** | **η (Pa·s)** |
|---|---|---|---|---|
| 0.05 µm | off / on | 0.00018 / 0.00017 | 1.17 / 1.10 | **12.06 / 12.80** |
| 0.10 µm | off / on | 0.00013 | 1.23 / 1.09 | **16.29 / 16.28** |
| 0.30 µm | off / on | 0.00013 | 1.28 / 1.14 | **17.41 / 17.62** |

*(all at ``gap=0`` — see below)*

### RESOLVED — there was no regression. The 8.3 result reproduces on current HEAD.

**2026-07-12, current HEAD, with the configuration recovered from the record:**

| linker | gap | tracks | D (µm²/s) | **α** | **η (Pa·s)** |
|---|---|---|---|---|---|
| GREEDY | **1** | 118 | 0.000273 | **0.930** | **7.969** |
| BAYES | **1** | 118 | 0.000273 | **0.930** | **7.969** |
| GREEDY | 0 | 243 | 0.000215 | 1.052 | 10.135 |
| BAYES | 0 | 243 | 0.000215 | 1.052 | 10.135 |

**η = 7.97 against the 8.325 reference — a 4 % difference — and α = 0.930 against 0.928 two
days ago.** The scientific-audit work of releases 398–462 (lag-window gate, localisation offset,
identifiability, drift) **did not regress the VPT chain.**

### The two things that produced the false alarm — both mine

1. **I linked ALL detections instead of the SINGLETS.** The 2026-07-10 script did
   ``sing = select_bead_population(det, 'singlet')`` and linked ``sing``. I linked ``det``.
   That folds ``out_of_plane`` (2 579 detections, 17 %) and ``aggregate`` (108) into a viscosity
   measurement they do not belong in, and it is the whole of the 12–17 vs 8.0 gap. **Singlet
   selection is not optional — it is part of the measurement.**

2. **I did not search the record.** The pixel size (0.067), the frame interval (0.1 s), the
   linking distance (0.3 µm), the gap (1), and the singlet filter were **all in the transcripts
   and the memory notes.** Every wrong turn came from running before searching.

### `gap=1` still matters, and that IS a real finding

There was an **off-by-one** in the gap check (``t - last_frame <= max_gap_frames``), fixed to
``<= max_gap_frames + 1``, so that **``gap=0`` now means "link consecutive frames"**. It would
have been reasonable to assume ``gap=1`` was therefore no longer needed.

**It is.** On current HEAD, ``gap=0`` → 243 tracks, α = 1.05, **η = 10.1**; ``gap=1`` → 118
tracks, α = 0.930, **η = 7.97**. Bridging a single missing frame nearly halves the track count
and moves the viscosity from 10.1 to 7.97. **The detection still drops beads, and gap-closing
still recovers the tracks.** That is the ~15 % dropout documented in the 2026-07-09 notes, and
it is not fixed — it is *bridged*.

### The settings that reproduce the reference

```
pixel size            0.067 µm/px
frame interval        0.1 s
bead radius           0.100 µm
temperature           24 °C
population            select_bead_population(det, 'singlet')   <- NOT optional
max_displacement_um   0.3
max_gap_frames        1                                        <- NOT 0
linker                greedy or bayesian (identical here)
-> eta = 7.97 Pa·s, alpha = 0.930   (reference: 8.325)
```

### Standing goal (Gable's, unchanged) — MET

> Do not chase TrackMate's 8.325. TrackMate needs manual trajectory pruning — that is the
> expertise-dependent step PyCAT exists to eliminate. **Make the two automated linkers good
> enough that users who opt out of TrackMate do not get trash.**

**Both automated linkers give 7.97 Pa·s fully automatically, against 8.325 from
TrackMate-with-manual-pruning.** The bar is met.

### Open, and worth returning to

* **α = 1.05 at gap=0** — the drift signature (1.5.456). At gap=1 it drops to 0.930, so the
  apparent superdiffusion at gap=0 is **fragmentation**, not drift. Worth confirming.
* **The ~15 % detection dropout is bridged, not fixed.** Gap-closing recovers the tracks, but a
  detector that did not drop stable beads would not need it.
* **The immobile-reference drift mode** (in the VPT UI, ``_drift_mode``) has not been tested on
  this data. COM subtraction removes real collective motion along with stage drift.


---

## The external audit — where we stand (2026-07-12)

The external audit (fed in as chunks 1–6 plus a final "Validation framework" chunk) is the
guiding document. **Its recommendations, checked against the code, not from memory.**

### "Immediate: before adding more analysis modules" — 5 of 7 done

| # | item | status |
|---|---|---|
| 1 | Decouple scientific functions from napari/PyQt | **DONE** — 24 → 3 coupled; all 3 remaining are pure UI (zero analysis functions) |
| 2 | Rename 2D "volume fraction" → projected area fraction | **DONE** |
| 3 | Replace size-distribution histogram R² model selection | **DONE** — MLE + Vuong (1.5.379), and **wired in** (1.5.421, which found it was never being called) |
| 4 | Native-resolution and PSF-aware measurement standard | **DONE** — partial-volume weighting, measure-on-native (1.5.382–385) |
| 5 | Physical outputs record assumptions + calibration provenance | **PARTIAL** — the `Measurement` framework exists and is wired into viscosity and partition. **Not** into FRAP, coarsening, moduli, N&B. |
| 6 | Uncertainty on FRAP, MSD, viscosity, coarsening, partition | **MOSTLY** — FRAP (446), MSD (447), viscosity (448), fusion τ (449), photobleach (451). **Coarsening and partition still lack intervals.** |
| 7 | Method-specific validity states, not just numbers | **DONE** — `fit_adequate`, `identifiable`, `is_true_kp`, `brightness_kind`, `number_kind`, `intensity_semantics` |

### "Next scientific release" — 3 of 7 done

| # | item | status |
|---|---|---|
| 1 | Probabilistic puncta candidate scoring | not started |
| 2 | Acquisition-aware FRAP model selection + identifiability | **DONE** (446) — and 455 found the acquisition-bleaching bug this was pointing at |
| 3 | VPT spatial heterogeneity + boundary dependence | not started |
| 4 | Camera-calibrated N&B + SpIDA validation | **PARTIAL** — N&B calibration path exists and is labelled (453); **SpIDA untouched** |
| 5 | Spatial null models + Monte Carlo envelopes | **DONE** (397, 419, 420) |
| 6 | Native scale-space persistent topology | not started |
| 7 | Hierarchical result structure (object → cell → field → experiment) | not started |

### The validation framework — this is the weak axis, and it is now being built

The audit asked every method to declare one of four states. Measured:

* **Analytically validated** — ~13 of 48 science modules have a ground-truth test.
* **Simulation validated** — **`tests/imaging_realism.py` (1.5.464) is this layer.** The audit's
  eleven degradations are now a composable harness, and **eight of them had already broken a
  real measurement** — each found one bug at a time rather than systematically. *I was
  rediscovering the auditor's list instead of building it.*
* **Experimentally validated** — **one**: VPT against the 8.325 bead standard (1.5.463). The
  audit also names *glycerol/water viscosity standards*, *monomer/dimer N&B controls* and
  *dual-colour bead registration* — all of which the lab has instruments for, and none of which
  are done.

### The audit's closing line, and whether we have answered it

> *"PyCAT's breadth is no longer the limiting factor. The strongest next step is to convert it
> from a large, capable analysis toolbox into a **measurement-aware scientific system**."*

**That is substantially what releases 372–464 did.** The remaining gap is not more measurement
awareness — it is **coverage**: 13 of 48 modules genuinely validated, and one method
experimentally validated.

### What is next, by the audit's own ordering

1. **Extend `imaging_realism` coverage** to the methods that have no ground-truth test at all.
   The dark modules include `data_qc_tools` (four bugs fixed in 403–406, **zero tests**, and it
   is the manuscript's enabling layer), `spatial_randomness_tools`, `topology_tools`,
   `temperature_tools`, `dynamic_spatial_tools`, `zstack_segmentation_tools`.
2. **Motion blur, pixelation and object overlap** — the three degradations in the harness that
   have not yet been shown to break anything. *Not yet shown ≠ harmless.*
3. **Experimental validation beyond VPT** — glycerol viscosity standards and monomer/dimer N&B
   controls are the two the lab can do now.


---

## OPEN: the QC report overlap guard does not work under SubFigure (2026-07-12)

**Status: the LAYOUT is fixed and verified. The mechanical GUARD is not, and was not shipped.**

### What was fixed
``plot_qc_report`` was rebuilt on ``SubFigure`` + ``constrained_layout`` (1.5.475). The scorecard
is a text **list** and the panels are a plot **grid**; forcing them into one coordinate system
caused every overlap, and ten attempts to hand-tune ``height_ratios`` / offsets each fixed one
report size and broke the other.

Under the rebuild, overlap is **structurally impossible** rather than tuned away:
* the scorecard gets its own unconstrained subfigure and is laid out as a list;
* the diagnostic grid gets ``constrained_layout``, which packs tick labels and titles correctly
  by construction;
* the per-panel captions are folded into the **x-label**, so the layout engine can see them (an
  ``ax.text`` at a negative y is invisible to it);
* the footer moved onto the scorecard subfigure (a ``fig.text`` at a fixed y is also invisible
  to the engine, and it was being packed onto by the panels).

**Verified visually at both report sizes** (2-D: 12 checks / 6 panels; time series: 12 checks /
9 panels). Clean.

### What does NOT work, and why
A mechanical overlap test — compare every text artist's ``get_window_extent`` and flag
intersecting boxes — **found the real bugs** while the geometry was hand-tuned, including a
**65 px scorecard-vs-tick collision that looked fine by eye**.

**It stops working under ``SubFigure``.** ``get_window_extent`` returns boxes that do not resolve
correctly for artists inside a subfigure: it reports the footer (display y 907–918) as
intersecting the histogram's ``10^5`` tick (y 906–920), and **cropping those exact pixels shows
the footer alone, with no tick anywhere near it**. A second ``canvas.draw()`` does not resolve it.

So the guard now reports **2 false positives on the 2-D report and 4 on the stack**, and **a guard
that cries wolf will be disabled by whoever trips over it next.** It was therefore not shipped.

### To pick up later
1. **Find the right extent call under SubFigure.** Candidates: forcing the tight-bbox machinery
   (``fig.get_tightbbox``), or resolving each artist's transform against the PARENT figure
   explicitly rather than trusting ``get_window_extent``.
2. **Or test the pixels.** Render the figure, render it again with one text artist hidden, and
   diff — sound but slow. A cheaper variant: render each text artist's ink to its own mask and
   check for intersecting non-zero pixels.
3. **The guard is worth having.** It found bugs I could not see, and the display bugs it would
   catch are exactly the ones that recur (every hand-tuned offset fix in this session broke a
   different report size).

**Do not re-enable the box-intersection version as-is.** It is wrong under the current layout,
and passing it would require re-introducing the geometry it was written against.


---

## OPEN: molecular counting — the two corrections do not COMPOSE (2026-07-12, 1.5.480)

**Status: each correction works alone. Together they under-perform, and I do not yet know why.**

``count_molecules_single`` fits ``nu`` as the slope of the binomial bleaching variance against
the mean, and ``N = y[fast] / nu``. Two independent contaminations, in **opposite directions**,
which **partly cancelled** — the worst case, because the combined error looked acceptable while
each half was badly wrong.

Against the correct (binomial-thinning) simulation, TRUE nu = 100, N = 10:

===========================  ==========  ==========  =============
trace                        BEFORE      AFTER       verdict
===========================  ==========  ==========  =============
clean                        −0 %        **−0 %**    sound
read noise (sd 15)           −23 %       **−11 %**   improved
pedestal (500)               **+79 %**   **−17 %**   improved
read 15 + pedestal 500       +30 %       −24 %       **not better**
read 40 + pedestal 800       −14 %       −34 %       **worse**
===========================  ==========  ==========  =============

### What was fixed and is solid
1. **The pedestal must come off BEFORE ``_variance_pairs``.** Both axes carry I(t). Subtracting
   it from ``y[fast]`` afterwards fixes the numerator and leaves **nu at 49.0 against a true
   100**. Recovered from the post-bleach plateau (497.7 against a true 500) — no dark reference.
2. **The read-noise floor is ``s²·(1 + p²)``, not ``s²``.** The y-axis is
   ``(I(t+1) − p·I(t))²``, which carries noise from BOTH frames. At p = 0.97 that is
   **1.94 × s²** — a first version subtracted half the bias.

### What is NOT resolved
The two corrections **do not compose**. Both improve their own case and the combination is worse
than either. Hypotheses, untested:

* The plateau variance measures ``s²`` **plus** any residual molecular signal, so at high read
  noise the estimate of s² is itself contaminated — and the ``(1 + p²)`` scaling then amplifies
  the error.
* ``p`` is fitted from the bleaching curve, and read noise **biases that fit too**. A wrong p
  scales the noise floor wrongly, and p appears in both axes.
* The through-origin constraint may be the wrong model once an offset has been subtracted — an
  intercept term might be more honest than forcing the line through zero.

### To pick up
Fit ``nu`` with a **free intercept** and compare: the intercept IS the noise floor, and letting
the fit find it avoids estimating s² and p separately. If that works, both corrections collapse
into one and the composition problem disappears. **Test against the binomial-thinning simulation
in ``tests/test_group_a_moments.py``, which is the correct one** — the first attempt used
deterministic bleaching and had no binomial fluctuation for the estimator to fit at all.
