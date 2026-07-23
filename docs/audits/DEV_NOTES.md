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

### GPU / OpenGL canvas corruption on some NVIDIA configs (BLOCKED — not abandoned)
**Suggested action for affected users: update the GPU driver to the latest, and/or roll back
to a previous stable driver.**

**Why this is blocked rather than deprioritized (2026-07-20):** the two machines where this
reproduces are institution-managed, NOT user-maintained — driver updates and rollbacks require
IT/admin action and cannot be performed by the user. The prime-suspect fix (driver rollback from
595.97) is therefore not something we can currently execute or verify on either affected machine.
This is a LOGISTICS block, not a decision to stop: the diagnosis points at the driver, the
confirming test needs a driver change, and we do not have admin rights to make that change. If/when
IT performs a driver update or rollback on one of these machines, retest plain napari + load image +
click/scroll to confirm, and only then decide whether a PyCAT-side `--safe-rendering` fallback flag
is worth adding. Until admin access or a driver change is available, no further PyCAT-side progress
is possible — it is waiting on infrastructure we do not control, not on engineering effort.

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

## RESOLVED: molecular counting — it was THREE errors, and one of them was my summary statistic (1.5.501)

**The "corrections do not compose" puzzle had three separate causes, and the last one was not a
bug at all.**

### 1. The through-origin fit is right ONLY when there is no noise floor
The old path estimated the read variance and ``p`` **separately**, combined them into a floor
``s^2*(1+p^2)``, subtracted it, and fitted through the origin. Each estimate carries its own error
and **they multiply** — ``p`` appears in BOTH axes of the regression. *That is why the corrections
fought each other.*

**A free intercept collapses it into one fit**: the line ``y = nu*x + b`` has the noise floor AS
``b``. Nothing is estimated separately, so nothing multiplies.

**But it is not universally better.** On a NOISELESS trace there IS no floor, and forcing the line
through zero is **correct information** — a free intercept there adds a parameter that soaks up real
signal (slope **76.7** against a true 100, versus **86.7** through the origin).

**The tail variance MEASURES which regime you are in** (0.0 clean; 210 at read sd 15; 1496 at sd
40), so the fit is chosen by measurement rather than by argument. ``nu`` on the pathological case:
**+21 % → −3 %.**

### 2. `y[fast]` was never the signal
``N = y[fast]/nu`` used frame ``fast`` — but **``fast`` exists to skip transients when building the
VARIANCE PAIRS, and was never meant to index the signal.** By frame 4, four rounds of bleaching
have happened.

**``y[0]`` is exact**: over 300 clean traces it measured **1000.0 ± 0.0** against a true 1000.
Averaging more frames biases it DOWN (3 frames: 967.6; 10 frames: 871.4).

*(A first attempt scaled ``y[fast]`` back by ``p^fast``. Wrong twice over: the bleaching fit does
not return ``p`` at all — ``_p_typ`` was silently falling back to a hardcoded 0.97 — and ``y[fast]``
is ONE noisy sample of a stochastic process, not its expectation.)*

### 3. **The MEAN was the wrong summary — and that was never a bug**
After both fixes, the mean N on the worst trace was still **+73 %**. Instrumenting it:

    signal at t=0:  998.7 +/- 40.7   (true 1000)   -- unbiased
    pedestal:       800.0 +/-  7.4   (true 800)    -- unbiased

**Both inputs are unbiased.** But ``N = signal/nu`` is a **ratio of two noisy quantities**, and
``E[A/B] != E[A]/E[B]`` — Jensen's inequality biases the mean **upward**, and a few traces with a
near-zero ``nu`` blow it up entirely:

===================  ==========  ==============
trace                mean N      **MEDIAN N**
===================  ==========  ==============
clean                10.67       11.46
read 15 + ped 500    10.95       11.26
**read 40 + ped 800**  **183.55**  **10.30**
===================  ==========  ==============

**The median is 10.30 where the mean is 183.55.** The estimator is sound; **the mean is the wrong
statistic**, and the module's own docstring already said so: *"the per-trace estimate is inherently
noisy... use ``count_molecules_pooled`` for a population estimate rather than relying on one
trace."*

**I spent this entire investigation measuring a statistic the module tells you not to use.**

### What is still open
``count_molecules_pooled`` **errors** on the stacked-trace input used here. It is the estimator the
user is told to reach for, and it should be exercised against this simulation — that is the next
step, and it is a small one.

---

## RESOLVED: `topo_n_basins` — the information was not in the envelope (1.5.502)

**It was a constant.** ``peak_local_max`` with only a ``min_distance`` accepted every local
maximum, however small. A **flat field with nothing but noise** reported **6.3 basins** — at a
noise sd of 5, 20 and 60 **alike**. It was measuring how many points of separation
``min_distance`` fit inside the mask, and *"we found 7 chromatin domains"* was a statement about
the image dimensions.

### What works: TOPOLOGICAL persistence
**How far does a peak rise above the SADDLE that separates it from a higher peak?** A watershed
**is** a persistence computation: flood downward, and when two basins meet, the lower peak **dies**
at that level. Its persistence is ``peak − saddle``.

That is **local and scale-free**, and — crucially — **it cannot be excluded by its own presence**,
which is what killed the global median gate (*real structure raises the median, and the raised
median then excludes the structure*)::

    FLAT     [20.0, 5.5, 3.2, 3.1, 0.6]
    6 peaks  [294.6, **42.7, 39.4, 38.7, 38.1, 33.2**]

**Real peaks are ~100× more persistent than noise bumps.**

### THREE gates failed before the right one, and they failed for the same reason
* a MAD-derived threshold — **the MAD grows with the structure** (0.12 flat → 4.6 with six peaks)
* a fraction of the envelope's range — **a flat field's range IS its noise**
* the second-largest persistence as a fraction of the range — **0.37 flat vs 0.14 real**, the
  wrong way round

**A flat field's envelope is scale-free noise. Its persistence distribution looks EXACTLY like a
real field's, only scaled down — and no ratio can separate them, because that is what scale-free
means.**

Worse, the MAD of the **envelope's** local differences measures **the smoothing**, not the noise:
``range/noise`` came out at **167 on a flat field and 64 on a real one** — *anti*-correlated with
structure.

### The fix: the noise is a property of the RAW IMAGE
**The envelope is a smoothed version, and smoothing destroys the noise by construction.**
``topology_metrics`` could not answer the question with the information it was given.

``estimate_image_noise(image)`` is now computed on the raw image and passed through. With it, the
separation is an **order of magnitude**:

    FLAT field (any noise level)     range/noise = **0.7**
    6 real peaks, heavy noise        range/noise = **5.3**
    3-9 real peaks, normal noise     range/noise = **9-13**

============================  ==========  ==========
field                         BEFORE      AFTER
============================  ==========  ==========
FLAT (noise 5 / 20 / 60)      **6.3**     **0**
3 peaks                       ~6          **3**
6 peaks                       ~6          **6**
9 peaks                       ~6          **9**
6 peaks, heavy noise          ~6          **5.8**
6 peaks, dim (amp 300)        ~6          **5.8**
============================  ==========  ==========

*When the caller does not supply the noise, the field is assumed to have structure and the result
is flagged (``topo_noise_known``) rather than quietly trusted.*

---

## OPEN: cloud-point detection on LOW-QUALITY data (1.5.489)

**The pipeline is VALIDATED on good data.** Gable confirmed accurate cloud points on real
temperature-ramp acquisitions with ``entropy_corrected`` + ``baseline``. **Those defaults stand.**

### The retraction that produced this note
1.5.488 changed the defaults on the strength of a simulation showing entropy returning the start
of the ramp. **The simulation was wrong.** Every scene gave the "clear" sample an intensity spread
of sd = 15, which already fills the histogram — entropy started at **7.1 out of a theoretical
maximum of 8.0** and had nowhere to rise:

    CLEAR sample, tiny noise (sd 2):        entropy **7.189**
    TURBID sample, strong scatter (sd 120): entropy **6.948**

``entropy_turbidity_curve`` bins each frame against its **own** intensity range, and a Gaussian
binned to its own spread has nearly the same entropy whatever its width. **The metric was never
given a chance to respond.** Reverted in 1.5.489.

**The lesson is the 1.5.453 one again: check the simulation before the code.** And when the
person who ran the real experiment says it works, that is data — test the simulation against
*their* result, not the other way round.

### What is actually open
How the turbidity signals behave when the acquisition is **degraded**:

* **Focus drift across the ramp.** ``focus_score`` is in the table and would move with defocus
  as well as with droplets — and defocus also blurs the histogram, which moves entropy. A ramp
  that drifts out of focus could produce a spurious transition, or mask a real one.
* **Illumination instability.** A lamp that warms with the stage changes ``image_mean`` and the
  histogram width independently of the sample.
* **Bubbles / debris entering the field.** A step change in the histogram that is not a phase
  transition.
* **Which signal survives which degradation.** ``entropy_corrected`` is the validated one; whether
  it is the most *robust* one is a different question and is not known.

### What a test of this needs
A **degradation model on real turbidity data**, not a synthetic phase transition — the synthetic
route is exactly what produced the wrong conclusion above. The ``tests/imaging_realism.py`` harness
(1.5.464) has the pieces (``blur``, ``illumination_gradient``, ``photobleach``), and the right shape
is: take a ramp Gable has already validated, degrade it by a known amount, and ask **at what
degradation the cloud point moves by more than the experimental uncertainty.**


---

## PINNED: two audit findings that change numbers in EXISTING data (2026-07-12)

**The audit's value is only realised if the corrected code touches the data.** Two findings
change results already produced. Both are shipped and tested; what remains is **re-running the
affected analyses.**

*(A third — the VPT linker gap default, 1.5.477 — is NOT pinned: Gable notes the automated linkers
exist only as a backup to TrackMate, which is what he actually uses, and the corrected default
(gap=2 -> eta 8.54 vs the 8.325 TrackMate reference) has achieved sufficient similarity. Recorded
in the changelog; no re-run needed.)*

---

### PIN 1 — touching condensates were ALWAYS counted as one (1.5.482)

**What was wrong.** ``split_touching_objects`` ran a watershed, computed the correct split, and
**threw the labels away** — rebuilding a BOOLEAN mask by subtracting Sobel edges. **A boolean mask
cannot express a split.** The two halves stayed 8-connected through the corner of the one-pixel
cut, so ``label()`` on the output returned **ONE object at every overlap** — including at zero
overlap, where the discs merely *touch* and were **already two separate components on the way in**.
**It merged them.**

**What it affects.** Everything downstream of the object count on a field with any touching
condensates:

* **condensate counts** (two are reported as one)
* **size distributions** (a merged pair reads as one large object — this SHIFTS THE MEAN SIZE UP
  and inflates the tail)
* **any per-object measurement**: partition coefficient, intensity, circularity, area
* **the coarsening exponent**, if it is read from a size distribution
* **cluster/spatial statistics** built on object centroids

**How to tell if a given dataset is affected.** Run ``skimage.measure.label`` on the segmentation
and compare against the object count that was reported. If any mask has two distance-transform
maxima with a deep neck between them, it was merged.

**What to do.** Re-segment with ``assess_and_split_touching`` (1.5.489), which additionally
distinguishes **two droplets in contact** (deep neck -> split) from **arrested fusion** (shallow
neck -> ONE object, and the arrest IS the finding). The old ``split_touching_objects`` now returns
labels; ``return_mask=True`` restores the old boolean output for any caller that needs it.

**Guards:** ``tests/test_group_c_geometry.py::test_touching_objects_are_actually_split`` and
``::test_genuinely_merged_objects_are_not_split``.

---

### PIN 2 — `ccf_sigma` was a 13x UNDERESTIMATE of the correlation length (1.5.481)

**What was wrong.** ``_extract_fit_results_2d`` in ``correlation_func_analysis_tools`` reported

    ccf_sigma_x = np.std(ccf_values[peak_row, :])

That is the **standard deviation of the correlation COEFFICIENTS** along a slice — a number in
correlation units, bounded by the [-1, 1] range of a Pearson coefficient. **It is not a length.**
It came out at **0.33** on data whose true correlation length is **4.24 px**, and **it would have
been 0.33 for ANY structure size** — the number carried no information about the image at all.

**And the real sigma was computed and thrown away.** ``curve_fit`` fits
``gaussian_2d(xy, amplitude, x0, y0, sigma_x, sigma_y)``, and ``popt[3]``/``popt[4]`` ARE the
widths, in pixels, on the same axes the peak position was already being reported in.

**What it affects.** Any reported **correlation length** or **cluster size** from the CCF/ICS path:

* ``process_ccf`` -> ``ccf_sigma`` (the direct consumer)
* anything that read ``ccf_sigma`` as a length scale — a "cluster size" or "domain size" from
  spatial correlation analysis
* **the sign of the effect is severe and CONSTANT**: everything reads ~0.33 px regardless of the
  true structure, so a comparison BETWEEN conditions would have shown **no difference** where a
  real one existed. *This is a false-negative generator, not a false-positive one.*

**What is NOT affected.** The **CCF peak position** — the inter-channel SHIFT — was always correct
(audited: exact at every offset tested). Chromatic-shift measurements are fine.

**What to do.** Re-run any CCF/ICS analysis that reported a correlation length. After the fix it is
within **3-9 %** of the analytic truth across the range.

**Guard:** ``tests/test_group_a_moments.py::test_ccf_sigma_was_the_std_of_the_VALUES_not_the_peak_width``.

---

### The related ACF finding (1.5.481) — worth knowing, less severe

``spatial_acf_tools``'s Gaussian had **no baseline offset**, and a spatial ACF **does not decay to
zero** — so the Gaussian **widened to reach the floor**, inflating sigma by **+43 % at blur 6** and
+37 % at blur 8. This is a *bias*, not a constant, so a comparison between conditions would
partially survive it — but the absolute correlation lengths were wrong. Fixed; now -1 % to -9 %
across the range.


---

## A diagnostic that can run in the wrong place is worse than none (2026-07-13)

``numba_arm64_diag.py`` produced a **confident, definite, wrong verdict twice** — and both times
the failure was the script's own:

| version | what it printed | what it had actually proved |
|---|---|---|
| **v1** | *"The parallel backend is broken."* | that ``@njit(cache=True)`` **cannot cache code passed as a string** (`python -c`). Both the cached test AND the parallel test failed on that, because both used ``cache=True``. **Neither ever reached the backend.** |
| **v2** | *"Numba is broken at a basic level."* | that the **``(base)`` conda environment has no numpy.** It was run from the wrong env, every test failed on the import, and it announced a conclusion about numba. |

**Both verdicts were garbage, and both were stated with total confidence.**

### The pattern, and it is the SAME ONE the code audit keeps finding
* an ``except: pass`` that lets the pixel-size gate vanish → **a guard that can fail silently is not
  a guard**
* a bbox check satisfied by a **comment** → **a guard a comment can satisfy is not a guard**
* a diagnostic that runs in an environment where its test cannot possibly work → **a check that can
  pass (or fail) for the wrong reason tells you nothing, and tells it loudly**

**In every case the check RAN. It just was not checking what it claimed to.**

### The fix, and it generalises
**Prove the preconditions, and REFUSE rather than guess.** v3 verifies numpy/numba/llvmlite import
at all, prints the conda env, and **exits 1 without running a single test** if they do not — rather
than running six tests that cannot work and drawing a conclusion from the wreckage.

*The verification of a verification is not optional. I "strengthened" the bbox guard and it was
exactly as weak — and I only found out because I deleted the real line and watched it still pass.*


---

## OPEN: Apple Silicon parallel Numba — the policy, and why auto-detection needs a SUBPROCESS

**Status: parallel Numba is OFF on macOS. That is a caution, not a finding.**

### What is established (Meet's M2, ``numba_arm64_diag.py`` v3, correct environment)
* plain ``@njit`` — PASS
* ``@njit(cache=True)`` — PASS
* ``@njit(parallel=True)`` — PASS
* **``@njit(parallel=True, cache=True)`` — PASS** *(exactly what PyCAT's decorator says)*
* ``workqueue`` threading layer — PASS
* ``TBB`` — not installed (expected; unrelated to the crash)

**So the parallel backend is NOT categorically broken on Apple Silicon.**

### What is NOT established
**That the launch crash was the Qt race.** v3 announced that it was, and **that was an overclaim —
the third in a diagnostic whose entire header is about not overclaiming.**

The test was a **64×64 float32 toy**. The real crash was in ``rescale_intensity_fast`` with real
microscopy data, inside a running Qt app with napari, torch, OpenCV and BLAS loaded. A segfault in
a parallel kernel is consistent with **either** an initialisation-order race **or** a
kernel/data/backend-specific native failure the toy did not reproduce. **Standalone-parallel-works
does not distinguish them.**

**Defensible:** parallel Numba works in isolation, so the failure needs the PyCAT launch context or
the real kernel — making an initialisation-order interaction the **leading hypothesis, not a proven
diagnosis.**

### To settle it
1. ``probe_real_kernel.py`` — the **real** ``rescale_intensity_fast``, on representative arrays
   (uint16 camera data, non-contiguous views, constant images, 2048×2048, single row/column), each
   in a **subprocess**.
2. Then ``PYCAT_NUMBA_PARALLEL=1 run-pycat`` on the M2 — **launch, load, filter, close, five
   times.** That is the test that actually decides.

### The policy point, and it is a real constraint on the design
**A SIGSEGV cannot be caught in-process.** ``try/except`` around a Numba call does not protect
against it — the process is gone. So **PyCAT cannot auto-detect a working parallel backend by
trying it and catching the failure.**

If parallel is ever to be enabled *conditionally* on Darwin, the check must be a **subprocess
probe at first launch** — run the real kernel in a child process, and enable the backend only if
the child exits 0. Anything else is a guard that cannot fire.

**Meanwhile the safe policy stands:**
* **keep the deferred warm-up permanently** — it costs nothing and removes a concurrency the code
  itself identifies as fatal on arm64
* **``PYCAT_NUMBA_PARALLEL=1``** re-enables parallel for anyone who wants it
* **``NUMBA_THREADING_LAYER=workqueue``** is the fallback if OpenMP specifically is the problem —
  and the v3 run shows workqueue works on that machine


---

## TODO ON CLOSE: document the Apple Silicon debugging (Gable's request, 2026-07-13)

**Everything below is written up in this file already; this is the map of WHERE it needs to land in
the USER-facing docs.**

### The two macOS segfaults are DIFFERENT, and the docs currently describe only one
Both README.md and ``docs/source/installation.rst`` already carry a **Mac M1/ARM warning** — about
Python running **x86_64 under Rosetta**, which causes Intel MKL warnings and crashes **Cellpose**.
That is a *real and separate* bug.

**The Numba crash is a second, distinct cause**, and a user hitting it will find the existing note,
check ``platform.machine()``, get ``arm64``, and conclude their install is fine — *while PyCAT
still segfaults at launch.* **The docs must distinguish them by their SYMPTOM**, because the fix is
completely different.

| symptom | cause | fix |
|---|---|---|
| segfault, **Intel MKL warnings**, dies in Cellpose | Python is x86_64 under **Rosetta** | reinstall arm64 Miniforge |
| segfault **at launch**, last line is ``OMP: Info #276...``, faulthandler points at ``numba_utils.py`` | Numba's **OpenMP** runtime initialising alongside **Qt** | fixed in **1.5.503** — just upgrade |

### 1. `docs/source/installation.rst` — a TROUBLESHOOTING section
There is currently **no troubleshooting page at all**. Add one, keyed on the **symptom the user
actually sees**, not on the cause (they do not know the cause):

* *"`run-pycat` segfaults immediately at launch"* → **which of the two is it?** The tell is the
  last line before the crash: ``OMP: Info #276...`` means Numba; ``Intel MKL`` means Rosetta.
* the ``python -X faulthandler $(which run-pycat)`` recipe — **this is what actually located the
  bug**, and it should be the first thing we ask a user for
* ``PYCAT_NUMBA_PARALLEL=1`` and ``NUMBA_THREADING_LAYER=workqueue`` as documented escape hatches

### 2. `README.md` — the Mac section
The existing *"one quick check"* box gets **a sibling**: *"If PyCAT still segfaults after the arm64
check passes, upgrade to 1.5.503+ — a separate Numba/Qt initialisation crash was fixed there."*

**Keep it short.** The README's job is to get someone installed; the detail belongs in the
troubleshooting page.

### 3. `docs/source/development/` — the investigation itself
The **process** is worth writing down, because it is the transferable part:

* the fault handler is the *first* tool, not the last
* **a segfault cannot be caught in-process** — ``try/except`` does not protect against SIGSEGV, so
  any probe of a native crash must be a **subprocess**
* **three diagnostics in a row produced a confident, wrong verdict** (cache-from-a-string;
  wrong-conda-env; a 64×64 toy standing in for the real kernel). *A diagnostic that can run in the
  wrong place and still print a conclusion is worse than no diagnostic* — it must **prove its
  preconditions and refuse.**

### 4. Not yet settled — do not document as fixed
Whether the crash was the **Qt race** or an **OpenMP/kernel-specific** failure is **still open**
(see the previous DEV_NOTES entry). The docs should say *"fixed in 1.5.503"* — which is true — and
**not** claim which cause it was.


---

## The probe had a FOURTH bug — and what Meet's run still proves (2026-07-13)

``probe_real_kernel.py`` printed its f-strings **literally**::

    numba available={NUMBA_AVAILABLE}  parallel={NUMBA_PARALLEL}
    {name} shape={str(arr.shape):14} ...

**Cause:** the child script was written with **doubled braces** (``{{NUMBA_PARALLEL}}``) — the
escaping you use when a string goes through ``.format()``. **It never does.** It is written
straight to a file, so the doubled braces survived, and an f-string renders ``{{X}}`` as the
literal ``{X}``.

***I escaped for a `.format()` that never happens.***

### But the run still proves what it claimed — and the exit code is why
The child contains::

    if not NUMBA_PARALLEL:
        print('*** PARALLEL IS OFF ***')
        sys.exit(2)

**That line has no braces**, so it was written correctly and it ran. **The child exited 0** — the
parent only prints *"ALL REAL-KERNEL CASES PASSED"* on ``returncode == 0``. Had parallel been off,
it would have exited 2.

**So parallel WAS on.** The real kernel was tested in parallel mode on **all seven array shapes**
(including uint16 camera data, non-contiguous views, constant images, single row/column), plus
**20 repeat calls**, under **both** the default layer and workqueue — and **none of it
segfaulted.**

*The critique's worst case — "the corrected probe reveals parallel=False" — is **ruled out by the
exit code**, not by the broken print. The result stands.*

### The ONE thing genuinely unknown, and it is the crux
**Which threading layer the default run selected.**

That is not a formatting nicety. **If numba's default on this M2 is already ``workqueue`` rather
than ``omp``**, then:

* the ``OMP: Info #276`` banner in the crash came from **somewhere else** — torch? BLAS? — and
* *"disable parallel Numba"* fixed a **symptom, not a cause.**

**If it says ``omp``, the OpenMP hypothesis holds. One line of output decides it.**

### Fixed
* the probe's f-strings are real f-strings, **verified by actually executing the child** — the step
  that was skipped in all three previous versions
* ``run_pycat`` now prints the warm-up's **thread**, its **timestamp**, and the **threading
  layer** — *the whole arm64 hypothesis is that the warm-up runs concurrently with Qt, and the only
  way to know is to make the app say what it actually did*


---

## The probe did not reproduce the crash — and I presented it as though it had (2026-07-13)

Gable asked directly: *"does this actually separate out the processes in a way that will lead to
the diagnosis, or did you give me an incomplete or flawed tool again?"*

**Incomplete. The tool was answering a different question than the one I claimed.**

### The flaw
``probe_real_kernel.py`` runs the real kernel **on the main thread of a clean process, with no Qt
at all.**

**The original crash had Numba warming up ON A WORKER THREAD while Qt initialised on the MAIN
thread.**

***The race needs the concurrency — and the probe removed the very thing being tested.***

It can **refute** *"the kernel is broken on arm64"* (and did). It **cannot** reproduce the crash,
and it **cannot** distinguish:

* **H1** — Numba's OpenMP coming up concurrently with **Qt** (the race)
* **H3** — **torch's** libomp (torch ships its own) against Numba's, with Qt innocent and the
  ``OMP:`` banner belonging to torch

because it loads **neither Qt nor torch.**

### And the app test does not diagnose either
``PYCAT_NUMBA_PARALLEL=1 run-pycat`` passing five times confirms **the fix works.** It does **not**
say *why* — it cannot separate H1 from H3, because it loads everything at once. **I have been
calling it "the test that decides." It is not.**

### `reproduce_arm64_crash.py` — the experiment that actually separates them
Five subprocesses, **adding one ingredient at a time**, and — crucially — **recreating the original
concurrency**: Numba compiling on a background thread while the main thread brings something up.

    A  numba on a worker, nothing else          baseline
    B  numba on a worker + **Qt** on main       **THE RACE, exactly**
    C  numba on a worker + **torch** first      torch's libomp vs numba's
    D  numba on a worker + Qt + torch           both
    E  numba on the **MAIN** thread, after Qt   **what 1.5.503 actually does**

**Case E is the most important thing in the script.** If E segfaults, **1.5.503 does not fix it** —
running on the main thread after Qt is exactly what the release does.

Every case **hard-fails (exit 2) if ``NUMBA_PARALLEL`` is False**, so a serial run cannot pass
meaninglessly. Verified: all five children compile, case B genuinely overlaps the warm-up with
``QApplication()``, case E is main-thread-only with Qt first.

### The limit that remains, and it must be stated
**If nothing segfaults, the crash cannot be reproduced outside the app.** Then something else in
PyCAT's startup — cellpose? OpenCV? a napari plugin? — is part of it, and **this script cannot see
it.**

In that case the app is the **only** instrument: it can tell us whether the fix **holds**, not
**why**. *That is a real limit, and it should be said plainly rather than dressed up as a
diagnosis.* **Four diagnostics in a row have now drawn confident, wrong conclusions from this
crash.**


---

## SOLVED (mostly): the arm64 crash is **TORCH**, not Qt (2026-07-13)

``reproduce_arm64_crash.py`` on Meet's M2. Each case in its own subprocess, each recreating the
**original concurrency** — numba compiling on a **worker thread**:

| case | what | result |
|---|---|---|
| A | numba on a worker, nothing else | **OK** |
| B | numba on a worker + **Qt** on the main | **OK** — ***Qt is INNOCENT*** |
| C | numba on a worker + **torch** first | **SEGFAULT** |
| D | numba on a worker + Qt + torch | **SEGFAULT** |
| E | numba on the MAIN thread, after Qt | **OK** |

**torch ships its own libomp.** Two OpenMP runtimes in one arm64 process is a classic way to die,
and the ``OMP: Info #276`` banner in the original crash was **torch's**. Numba was the bystander
that happened to be running when it blew up.

***The Qt race was a hypothesis I formed from a coincidence — the OMP banner appearing next to a
threading call — and I ran with it for three diagnostics. It was wrong.***

### And this changes WHY 1.5.503 works
``run_pycat`` calls ``_prewarm_cellpose_model()`` at line ~294 — **which imports torch** — and
starts the warm-up thread at line ~495. **That is case C, exactly.**

1.5.503 does two things on Darwin: **defers the warm-up** and **disables parallel Numba**. I
claimed the first was the fix. **The matrix says it is the second** — the crash is inside a
*parallel* kernel, and ``parallel=False`` is what actually protects Meet.

**Deferring the warm-up only moves the compile to first use.** If torch+numba also dies on the main
thread, that is **worse**: the crash would land **mid-analysis** rather than at startup.

### THE MISSING CELL — E vs C changed TWO variables
    C: torch **present**, numba on a **worker**  -> segfault
    E: torch **absent**,  numba on the **main**  -> survived

**So E may have survived only because torch was never imported.** Two variables moved; one can be
the cause. ``reproduce_arm64_crash.py`` (2nd version) isolates it:

* **F** — torch + numba on the **MAIN** thread
* **G** — torch + worker, with ``KMP_DUPLICATE_LIB_OK=TRUE``
* **H** — torch + worker, with ``NUMBA_THREADING_LAYER=workqueue``

**F segfaults** → the thread is irrelevant; it is libomp vs libomp, and the fix must stop two
OpenMP runtimes loading.
**F survives** → the worker thread is the trigger, and parallel Numba can be re-enabled provided
the warm-up stays on the main thread.

### The likely real fix: `NUMBA_THREADING_LAYER=workqueue`
Numba's **own pure-Python thread pool**. It does **not** load an external libomp, so it **cannot
collide with torch's**. Already known to work on that M2 standalone (diag case 6) and with the real
kernel (probe case 2) — **the torch case is exactly what H tests.**

If H passes, this is **strictly better than ``parallel=False``**: the same safety, and the kernels
**actually run in parallel**. (Workqueue is slower than OpenMP and has no nested parallelism, but
PyCAT's kernels are flat per-pixel loops — neither costs anything here.)

**It is documented but NOT enabled.** *I have shipped a fix on an untested hypothesis four times in
this investigation. Not a fifth.*


---

## The Intel-Mac bug and the Apple-Silicon bug are the SAME BUG (2026-07-13)

Gable asked whether the arm64 crash relates to the Intel-Mac problems. **It is the same bug.**

| | collision | victim |
|---|---|---|
| **Intel Mac** | MKL's ``libiomp5`` + torch's ``libomp`` | **cellpose** |
| **Apple Silicon** | torch's ``libomp`` + numba's ``libomp`` | **numba** |

**Same mechanism: two OpenMP runtimes in one macOS process.** They share a symbol table and stomp
each other's thread state, and **whichever library happens to be running when that happens is the
one that dies.**

***The victim is incidental. The loader is the bug.***

### The evidence was already in the codebase
* ``run_pycat`` **already sets ``KMP_DUPLICATE_LIB_OK=TRUE``** — a variable that **exists for
  exactly this**, and which **predates Apple Silicon by years. It was created for Intel Macs.**
* ``run_pycat:270`` already warns that a cellpose native crash *"usually means the installed
  PyTorch is not compatible with this CPU/architecture"* and recommends
  ``conda install pytorch nomkl``.
* **``nomkl``'s entire function is to remove a competing OpenMP runtime** (MKL drags in
  ``intel-openmp``/``libiomp5``). The README's advice was **right, and for a reason it did not
  state.**

**And on Intel it can be WORSE** — MKL is present there and absent on arm64, so there can be
**three** runtimes, not two.

### KMP_DUPLICATE_LIB_OK does NOT fix it — and may make it worse
Intel's own docs: *"you can set KMP_DUPLICATE_LIB_OK=TRUE to allow the program to continue to
execute, **but that may cause crashes or silently produce incorrect results**."*

**It does not make two runtimes safe. It makes them TOLERATED.**

* **without** it → a clean, diagnosable ``OMP: Error #15`` abort
* **with** it → the process continues and **segfaults deeper in**

***It converts a diagnosable failure into a mysterious one — and PyCAT sets it, and Meet's machine
crashed anyway.***

*(Kept, because a tolerated duplicate still beats an unconditional abort for the many users whose
libraries happen not to collide. But it is a **mitigation, not a fix**.)*

### The comment in the code said "arm64", and that sent me down the wrong path
``run_pycat``'s OpenMP block was headed *"On Apple Silicon (arm64)"*. **It is a macOS problem, not
an architecture problem** — the duplicate-dylib symbol interposition is identical on x86_64. That
one word framed three diagnostics around a Qt race that never existed.

### `openmp_audit.py`
Counts the OpenMP runtimes **actually loaded**, and says **which package brings which** — by
walking the process's dyld images, and by finding the dylibs **on disk** (a ground truth that needs
no macOS API).

**Two distinct libomp files in one environment is the condition**, whatever the CPU.

### The fixes, in order
1. **``NUMBA_THREADING_LAYER=workqueue``** — numba loads **no libomp at all** and **keeps its
   parallelism.** Cleanest.
2. **``nomkl``** — removes MKL's libiomp5. *Already the README's Intel advice.*
3. **``PYCAT_NUMBA_PARALLEL=0``** — numba avoids OpenMP. Safe; loses parallelism. *(Current macOS
   behaviour.)*


---

## SOLVED: the macOS crash is torch's libomp vs numba's libomp (2026-07-13, FINAL)

**The complete matrix, each case in its own subprocess on Meet's M2:**

| case | | result |
|---|---|---|
| C | torch + numba on a **worker** | **SEGFAULT** |
| F | torch + numba on the **MAIN thread** | **SEGFAULT** — *the thread is irrelevant* |
| G | torch + numba + **`KMP_DUPLICATE_LIB_OK=TRUE`** | **SEGFAULT** — *the flag PyCAT already sets does NOTHING* |
| H | torch + numba + **`NUMBA_THREADING_LAYER=workqueue`** | **OK** |

*(and from the previous run: **B — numba + Qt — OK.** Qt was innocent all along.)*

### Three things this settles
1. **The thread does not matter.** F segfaults too — so **1.5.503's deferred warm-up was never the
   fix.** It only moved the crash to **first use, mid-analysis**, which is worse. *The only thing
   that protected macOS users was `parallel=False`.*
2. **`KMP_DUPLICATE_LIB_OK` does not work** — and **PyCAT already sets it.** Exactly as Intel's own
   documentation warns: it makes the duplicate **tolerated**, not safe.
3. **`workqueue` works** — numba's own pure-Python thread pool loads **no libomp at all**.

### The fix, shipped in 1.5.517
* ``NUMBA_THREADING_LAYER=workqueue`` on Darwin, **set in `run_pycat` before the first native
  import** — because numba can be pulled in indirectly by cellpose or a napari plugin, and setting
  it in `numba_utils` would be **too late**.
* **Parallel Numba is RE-ENABLED on macOS.** The speed is back.
* Forcing ``omp`` back on **warns**, rather than letting the user discover it as a segfault.
* ``KMP_DUPLICATE_LIB_OK`` is **kept** — useless for the numba/torch pair, but the only mitigation
  available for the **other** pair: MKL's libiomp5 against torch's libomp, which is the documented
  **Intel-Mac "PyTorch segfaults Cellpose"** bug. *Same mechanism, different colliders.*

## 8. Environment fragility after the BioIO migration (2026-07-13)

Full record: `bioio_migration_2026-07-13.md`. **This section is the part that will bite someone, not
the part that is interesting.**

### aicsimageio and BioIO cannot coexist, and the failure is disguised

`aicsimageio` is frozen in 2023 and pins `zarr<2.16`, `tifffile<2023.3.15`, `fsspec<2023.9`,
`lxml<5`. BioIO needs the modern stack. **Installing one alongside the other leaves both broken.**

The observed failure is:

```
AttributeError: '_TIFF' object has no attribute 'RESUNIT'
```

***Which sends a scientist looking at their microscope.*** It is `aicsimageio` reading a `tifffile`
three years newer than it supports, and **nothing in that traceback says so.**

**The startup environment check now names the package, the version, the requirement, and the fix.**
It reads the pins **from the installed metadata**, walking the dependency tree — *not from a
hardcoded list, which would go stale the moment the pins move.*

### tifffile's zarr error message is a lie

```
ValueError: zarr 3.2.1 < 3 is not supported
```

***3.2.1 is not less than 3.*** The real error is one frame up: `cannot import name
'RegularChunkGrid'` — **zarr 3.2 renamed it.** `tifffile` catches **any** ImportError out of its
zarr-3 module and blames the version.

**If anyone reports this, do not go looking for an old zarr.** TIFF pixels now bypass BioIO entirely
(`tiff_planes.read_tiff_plane`), so it should only surface on **Z or T+Z TIFF stacks**, which still
go through BioIO. *(See the roadmap.)*

### A user can break PyCAT by installing a napari plugin, and there is no way to stop them

`pip` has no *"conflicts-with"* field. napari discovers plugins from whatever is installed, and its
plugin manager makes installing one a single click.

**PyCAT cannot prevent the damage — it can only refuse to pretend nothing happened.** That is what
the startup check is for, and it is worth understanding as a *design position* rather than a feature.

### The conda lockfiles were deleted, and should not come back

`config/pycat-napari-env-*.yaml` were **exported conda lockfiles pinned to Python 3.9** — and PyCAT
requires `>=3.12`. **They could not have worked.** They also pinned `aicsimageio=4.10.0`,
`numpy=1.23.5`, `tifffile=2023.2.28`, and **the README told developers to build from them.**

***An exported lockfile is a second source of truth by construction.*** If a conda environment is
genuinely needed, generate it from `pyproject.toml`. **`test_install_routes_agree` fails the build if
they return.**

### Lessons worth keeping

**A guard whose scope is narrower than the bug will certify the half that was fixed.** 1.6.3 fixed
three of nine `__array__` methods, and the guard only looked at the file containing those three.

**A malformed call inside a `try/except` is invisible.** `_TiffPageStack(file_path)` — one argument
where five are required — raised `TypeError`, was caught, and **fell through to the eager read
anyway.** It compiled, tests were green, and the fix never once ran.

**A guard that cannot tell code from prose will eventually flag its own explanation.** Three times in
this arc a guard matched a *comment* rather than the code — and once it flagged the docstring
explaining why the bug was dangerous. **The fix is not to stop explaining.** All of them now walk the
AST.

**Correctness testing cannot see a performance regression.** The migration was validated on shape,
dtype, dimension order, pixel size, scenes, and a **checksum of the pixels** — 31 files identical,
0 different — while a loader read the *entire scene* to fetch *one plane*. ***The freeze was invisible
to it by construction.*** `test_one_plane_reads_one_plane` is the answer, and it measures **peak
allocation**, because bytes-read is blind to the page cache and to memory-mapping.

---

## OPEN: multi-scene switcher — BUILT + headless-tested, awaiting in-app verification (needs generated multi-position data) (2026-07-19, 1.6.130)

**Status: the whole feature is implemented and every headless-testable part is green. The viewer-coupled
behaviour cannot be verified here — and verifying it needs a multi-position CZI/IMS test file that Gable
must generate (or point at one on the acquisition machines). Do NOT release until that pass is done.**

### What shipped (spec `claude_code_spec_scene_switcher_2026-07-18.md`, all four parts)
- **`_SceneStack`** (`file_io/lazy_sources.py`) — lazy (T,Y,X) wrapper for ONE scene; re-pins its scene
  on every read, so a shared stateful reader can never serve a frame from another position. Switching
  builds a fresh wrapper → no cross-scene plane cache → no stale frame by construction.
- **`file_io/scenes.py`** (new, Qt-free) — `build_scene_stack`, `tag_scene_layer`/`scene_of`,
  `list_scenes`/`scene_index`.
- **Routing** (`file_io._open_stack_generic`) — a multi-position file loads exactly ONE scene, lazily,
  and tags each layer with its position. Single-scene files untouched.
- **Per-scene calibration** (`data_modules.update_metadata`) — reads the CURRENT scene, not scene 0.
- **Switcher dock** (`ui/scene_switcher.py`, new; File menu → "Switch Position / Scene") — dropdown
  rebinds layers in place, off-thread first read (`run_with_progress`), re-reads calibration, stamps
  derived layers with the position they were computed on.

### What is headless-tested (green)
`test_scene_stack.py` (wrapper contract, one-plane read, scene provenance/anti-stale),
`test_scenes.py` (helpers + per-scene metadata), `test_scene_switcher.py` (rebind/re-tag/stale-derived —
`run_with_progress` runs synchronously with no event loop). The AST eager-read guard auto-covers the new
wrapper. Full `-m core` green at 1.6.130.

### What NEEDS in-app verification — and the DATA required
**Gable must generate/point at a genuine multi-position file** (a CZI or IMS with ≥2 scenes; ideally two
positions with *different* pixel sizes to exercise the per-scene calibration re-read). Then check:
1. Opening it loads ONE position, lazily and fast; the layer name shows `[position]` and carries a
   `scene` tag.
2. File menu → "Switch Position / Scene" opens the dock; the dropdown lists the positions.
3. Switching shows a progress dialog (no "Not Responding"), shows the NEW position's pixels (NOT a stale
   frame), and updates the pixel size if the positions differ.
4. A mask/labels computed on the old position gets tagged `computed_on_scene` + a warning.

### The most likely thing to need a fix (runtime-dependent, un-verifiable here)
**Reader discovery** in `SceneSwitcherWidget._reader_for`: it looks for the scenes-capable reader among
the layer's retained `metadata['pycat_image_source'].readers` (the generic loader retains `(reader,dask)`
tuples), falling back to re-opening the file via `open_image`. If the dropdown shows NO positions on a
real multi-scene file, the retained object is not the `BioImage` — capture what IS retained
(`type(...)`, `hasattr(set_scene/scenes)`) and wire `_reader_for` to it. That single fact is what a real
file will settle. Then update `roadmap.rst` (the multi-scene rubric) to RESOLVED.

---

## FINDINGS: filter_sensitivity increment 3 survey — live scale risks NOT yet pinned (2026-07-19, 1.6.131)

Increment 3 added `partition.client_enrichment.background` (offset; K collapses from 30 to 5.83 at a
500-count pedestal with the default `background=0.0` — warned but still returned) as a validated harness
case. The survey also turned up two LIVE scale risks that were **not** added — they are the same scale
shape already covered by `segmentation.local_ring_geometry`, and they are user-facing px controls rather
than hidden constants, so they are findings to report, not new machinery. Recorded here so a future pass
does not re-discover them from scratch:

- **`segmentation.min_spot_radius=2` (scale).** Doubles as a DoG sigma AND a `min_area = ceil(pi*r^2)`
  (~13 px) size gate; `is_undersized = area < min_area` drops puncta below it from the **reported
  count**. It is raw pixels and NOT derived from the measured/pixel-size-aware value (unlike
  `ball_radius`), so at a coarser pixel size a real punctum spanning <13 px is silently dropped. Only
  *partially* guarded: `_report_refinement_drops` surfaces the drop reasons and warns "check
  min_spot_radius against the pixel size" when ≥80% are dropped — but the count itself is still biased
  and a reader of just the number wouldn't know. A proper fix would derive the floor from the pixel size
  (like `ball_radius`); that is a production change with its own spec, not a test-only harness case.
- **`partition.client_enrichment_per_condensate` `shell_px=5` (scale).** A fixed-px local dilute ring
  around each object → the dilute reference is sampled at a pixel-size-dependent standoff; unguarded.
  Same scale shape; a fix would scale the ring to the object (as `local_ring_geometry` did).

~35-110 other filter defaults remain; the audit's view stands — they are not equal, and the next
increment is another prioritisation call, not a sweep.

## SWEEP: cross-session dropped-thread audit (2026-07-20, 1.6.168)

A deliberate pass over the full PyCAT chat history looking for things IDENTIFIED but never
tracked in a durable doc. Most large threads were already captured (roadmap rubrics, sections
1-8 above). The items below are the genuine gaps — recorded here so they stop living only in
chat transcripts. Each is tagged with its state as of 1.6.168.

### Paused bugs not in the Known-Issues section (section 3)
- **RESOLVED (2026-07-20): drag-drop onto the napari CANVAS works** — verified loading CZI, TIFF,
  and IMS by drop. (Prior investigation retained below for history.) ORIGINAL: napari 0.7.1;
  the canvas widget has `acceptDrops=False`, so Qt rejects the drag at the door and no
  DragEnter/Drop event fires. The app-level `_FileDropFilter` only catches drops on non-canvas
  widgets. Multiple fixes attempted through 1.5.329 (force `setAcceptDrops` on QtViewer+canvas+
  children; a layer-insertion backstop watching `layers.events.inserted`) — STILL red-slash per
  Gable's test: the drop is rejected entirely, so the backstop never fires. `dnd_diag.py`
  delivered; resume by getting its output to pin the exact 0.7.1 widget/accessor. Dropping onto
  non-canvas areas works, so this is low-severity but real and user-visible. Belongs in the
  public known-issues note eventually.
- **PAUSED INDEFINITELY: GPU/OpenGL canvas corruption on some NVIDIA configs** — lives in the
  Known-Issues section (section 3). Suggested user action: **update the GPU driver to the latest,
  and/or roll back to a previous stable driver.** No further PyCAT-side investigation planned; the
  `--safe-rendering` flag remains an optional future idea, not committed.

### Latent-pattern decision never formalized
- **DECISION NEEDED: the ~79 `np.asarray(layer.data)` sites.** The frame-0-collapse landmine is
  DEFUSED (the lazy `__array__` now REFUSES via `lazy_guard` rather than silently returning frame
  0), so any remaining offender is a LOUD crash, not a silent wrong-answer. But ~79 sites remain
  and there is no recorded decision on whether to triage them proactively or leave them to fail
  loudly if hit. Most are safe 2D-only workflows. This is a judgment call per site (does this
  workflow ever receive a time-series?), not a blind sweep — and it currently lives only in a
  point-in-time audit doc, not as a live task. Recommendation: leave defused, fix opportunistically
  when a site is touched for other reasons; do NOT open a dedicated sweep.

### Delivered-but-unlanded specs (in docs/audits/ as .md, not yet shipped, not in roadmap)
These have written specs but no roadmap/DEV_NOTES home, so their rationale would be lost if the
.md files were cleared:
- **Exception conversion increment 2** (`claude_code_spec_exception_conversion_2_*.md`) — convert
  scientific-path broad handlers in `toolbox/` (ratchet still 514) to typed errors; annotate the
  legitimate Qt-teardown ones `# broad-ok:`. Convert by CONSEQUENCE, not count.
- **UI-builder split** (`claude_code_spec_ui_builder_split_*.md`) — the five 400-638 line `_add_*`
  widget constructors; the low-risk half of the complexity ratchet-down.
- **Science-function split increments 2+** (`claude_code_spec_science_function_split_*.md`) — 1 of 6
  done (`fit_anomalous_diffusion`, 1.6.168); `partition_coefficient_local` (394, covered, ready),
  `run_timeseries_condensate_analysis` (362, uncovered — needs characterization test first), and
  the long `timeseries_condensate_tools` functions remain. Coverage-gated: no test, no split.

### Older deferred follow-ons never converted to roadmap rubrics
- **Topology follow-ons** (chromatin topology map shipped 1.5.137, foundation exists in
  `topology_tools.py`): the two utilities that build on it were never started —
  (1) over-segmentation sanity check (objects-per-basin), (2) wetting/connectedness metrics
  (ridge-bridging, percolation). Gable wanted to eyeball the topology map on real data before
  building these; that gate was never explicitly closed or reopened.
- **Maximize-on-open** — RESOLVED/non-issue (2026-07-20): the app does not maximize on opening; no
  inconsistent-maximize behaviour to fix.
- **Session-load audit** — does "Load Previous Session" restore line/intermediate layers created
  before a Save-and-Clear? Raised as a non-verify pending item during the grid/menu work; never
  audited. Worth a one-time check.
- **Same-channel grid grouping** — the managed grid tiles in canonical/layer order, not grouped by
  channel across images. Raised as a possible enhancement; never specced.
- **Partial-volume default** (verify against current tree before acting): the Cell Analyzer image
  dropdown still defaults to `name_hint='Upscaled Fluorescence'` (`ui_analysis_mixin.py`). Earlier
  work added an amber warning but left the DEFAULT on the upscaled image, so the flawed measurement
  path may still be the path of least resistance. Confirm whether later work changed the default;
  if not, this is a live measurement-correctness nudge worth finishing.

### Manuscript-track items (never in a doc, only in memory/chat)
- Nature Methods Brief Communication prep as an explicit track: benchmarking (now partly served by
  the validation suite, 1.6.166), ground-truth validation, biological validation across systems,
  figure polish (now partly served by publication figure refinement, 1.6.167). The remaining gap
  is cross-system biological validation — not a code task, but it should be a named roadmap line so
  it is not forgotten.

None of the above is urgent. The point of the sweep is that they now have a home other than a
transcript. The two genuinely user-facing ones (drag-drop, GPU corruption) are the known-issues
candidates; the rest are backlog.

## SWEEP ADDENDUM: full 12-chat read (2026-07-20) — non-private items only

The cross-session sweep was extended to a full read of all 12 PyCAT chats. Manuscript-strategy
content found in three of them is PRIVATE and has been kept OUT of the repo (see the local-only
file on Gable's machine, not tracked here). The repo-appropriate findings:

### Delivery-workflow rule that was never written down
From the 1.5.335/336 session (the changelog-staleness incident): **always include CHANGELOG.md and
pyproject.toml in the delivery zip; if the sandbox copy might be stale, upload-and-merge (or ASK)
before zipping — never ship a partial bundle that could overwrite live changelog/version history.**
Root cause was a sandbox worktree extracted from an older tag, so its CHANGELOG lagged the live one
and would have clobbered real entries on extract.

### Chats read in full, confirmed to hold NO untracked code threads
- "Debugging code / Pip not installing updated git code" — GPU/torch cu118 install; covered by GPU notes.
- "Styling GUI file buttons" — cosmetic QGroupBox title styling; no durable thread.
- "Publishing conda package updates" — packaging mechanics; no dropped item.
- "PyCAT batch processing workflow automation" — workflow-checklist replay-on-activate; shipped.
- Three manuscript-strategy chats — content is private, captured in Gable's local-only file.

## Brushing/plotting audit → spec coverage map (2026-07-20)

The brushing/plotting/publication audit (identity + brushing + figures) was fully converted to specs
in docs/audits/. Every audit section now has a home so none is forgotten:

| audit section | spec | priority |
|---|---|---|
| §7 two FigureSpecs (largest design issue) | claude_code_spec_figurespec_merge | 1 |
| §1 identity not universal (next milestone) | claude_code_spec_auto_identity_stamping | 2 |
| §5 plot lifecycle / >20 open figures | claude_code_spec_plot_lifecycle | 3 |
| §2 dataset identity = path (breaks on move) | claude_code_spec_dataset_identity_uuid | 4 |
| §2 identity/location divergence | claude_code_spec_entity_registry | 5 |
| §4 backend parity (seaborn/pyqtgraph/plotly) | claude_code_spec_backend_parity | 6 |
| §8 missing general publication features | claude_code_spec_publication_features | 7 (after merge) |
| §9 Explore→Refine→Export UX | claude_code_spec_explore_refine_export | 8 (after merge) |

**Dependency order:** figurespec_merge FIRST (publication_features + explore_refine_export both depend
on the unified spec). auto_identity_stamping before entity_registry (registry populates at the stamping
finalization point) and dataset_identity_uuid feeds the registry's durable `dataset` field. plot_lifecycle
and backend_parity are independent and can slot anywhere.

**Recommended sequence:** merge → auto-stamp → lifecycle → dataset-uuid → entity-registry → backend-parity
→ publication-features → explore-refine-export. The first three are the audit's own top-3; the identity
pair (uuid + registry) is the deepest correctness work; the last two are the publication-workstation build.

## Built-but-unwired modules (2026-07-20 orphan scan)

An import scan found modules that are **shipped and unit-tested but imported by nothing in `src/`** —
built, correct, and invisible/unreachable. Verified via exact-import grep (0 real importers each). This
is the concrete "unwired due to needing unblocking" list. Most map onto the feature-registry /
navigator-UI visibility work; a few are integration follow-ons whose backing thread stalled.

| module | what it is | why unwired / next step |
|---|---|---|
| `utils/reliability.py` | MRI reliability index | needs UI surface (feature-registry card) + wire as the quality-gate's reliability input |
| `utils/feature_provenance.py` | per-feature provenance record | no consumer wires it into exported tables yet — needs the sidecar/export hook from its spec |
| `utils/analysis_presets.py` | workflow preset objects | preset picker never added to the workflows — needs the UI from its spec |
| `utils/cohort_targets.py` | histogram-bin + aggregate-row cohort emitters | the emitters exist but no plot wires its clicks to them — wire in the plot adapters |
| `toolbox/condensate_modes.py` | 2D/3D/timeseries `CondensateMode` gating | never wired into the invitro workflow — the mode selector + output gating from its spec |
| `toolbox/clean_spot_detection_tools.py` | CLEAN spot detection | **INTENTIONALLY UNWIRED — not a loose end.** A validated detector held back on purpose until the future smFISH pipeline it was written for lands (CLEAN assumes model-PSF spots, true for smFISH, so it must not be offered for arbitrary images). The intent is documented in the file's own header. Do NOT wire, remove, or re-flag it. |
| `ui/brushable_table.py` | entity-stable brushable table | the audit's strong table impl — confirm it's actually mounted where tables render (may be wired via a name my grep missed; verify at GUI) |
| `file_io/czi_seam.py` | CZI seam metric (`column_seam_score`) | the seam regression test uses it but the READER doesn't call it as a live check — wire as an optional load-time QC per the seam spec |

**Common thread:** most of these are the "shipped capability with no UI" set the feature-registry +
navigator-UI beginner-mode spec is designed to surface. Wiring the feature registry is the single move
that makes `reliability`, `analysis_presets`, `condensate_modes`, and `feature_provenance` reachable.
`cohort_targets` and `czi_seam` are narrower integration hooks (plot-adapter wiring; reader QC hook).
`clean_spot_detection_tools` is INTENTIONALLY unwired (documented in-file — held for the future smFISH
pipeline; not a loose end). `brushable_table` should be verified at the GUI before assuming it's orphaned.

**Action:** don't mass-wire blindly. The feature-registry spec covers the visibility set; the two
integration hooks (cohort_targets → plot adapters, czi_seam → reader) are small dedicated wirings; and
clean_spot_detection_tools needs a decision, not a wiring.

## Engineering audit → spec coverage map (2026-07-20)

The second engineering audit (release engineering + finishing architectural transitions) mapped to
specs. Coverage:

| audit item | spec / status |
|---|---|
| #5 Python 3.13 blocked | claude_code_spec_python313_enablement (STAGED + COLLABORATIVE — Gable verifies real-data no-regression before the ceiling flips) |
| #7 ruff correctness advisory | claude_code_spec_release_engineering (Part 1: make blocking) |
| #6 pytest needs PYTHONPATH | release_engineering (Part 2: pythonpath + wheel-install lane) |
| #8 core marker not minimal | release_engineering (Part 3: core/base/gui/... markers) |
| #9 qt_api warning | release_engineering (Part 4: pytest-qt in [test]) |
| #13 stale dep comments | release_engineering (Part 5) + python313 Stage 0 (coordinate — do once) |
| #14 Production/Stable classifier | release_engineering (Part 6: → Beta) |
| #12 free-form result dicts | claude_code_spec_typed_result_models |
| #10 file_io ownership boundaries | **covered** by the existing fileio decomposition specs + the audit's own "architectural test rejecting new low-level fns in file_io.py" — add that guard test as part of the next file_io touch |
| #11 VPT + timeseries still monolithic | **partially specced** — detect_beads_split (VPT) exists; the FULL VPT/timeseries domain split (vpt/trajectories,drift,msd,fitting,viscosity,... and timeseries/frame_access,preprocessing,...) is NOT yet specced — the biggest remaining decomposition, gated on coverage like the science-split programme |
| #1-4,#10 (transitions) | ongoing via the decomposition specs already delivered |

**Not yet specced (flagged so it isn't forgotten):**
- **The full VPT scientific-domain split** (vpt_tools 2791 lines → trajectories/drift/msd/fitting/
  viscosity/calibration/uncertainty/execution/result_models). detect_beads_split is one function; the
  domain split is the whole module. Coverage-gated (VPT has the ~8.325 baseline + equivalence guards,
  so it is splittable safely — but it's a large arc, needs its own spec.)
- **The full timeseries_condensate_tools split** (2828 lines → frame_access/preprocessing/segmentation/
  tracking/colocalization/coarsening/fusion/photobleaching/result_models). Some functions covered by
  characterization tests; others uncovered (need tests first, per the science-split discipline).
- **The file_io "no new low-level functions" architectural guard test** (#10) — a test that fails if a
  new reader/metadata function is added to file_io.py, enforcing the ownership table. Small; fold into
  the next file_io change.

These three are the remaining engineering-audit work beyond the three specs written. VPT and timeseries
domain splits are the large ones and should be their own coverage-gated specs when picked up.

---

## 8. Descoping discipline — when a spec defers an alternative, say "do not test X" (2026-07-23)

Three consecutive CI failures were **test defects, not product bugs**: a test's own numeric assumption was
wrong (`test_unmixing::negative_fraction`); Qt-requiring tests marked `core` in a lane without pytest-qt
(4× `qtbot` collection errors); and a test asserting a dock-reflow mode (`collapse`) the module had
deliberately *deferred* and never declared in `VALID_MODES`.

The `collapse` case originated in a spec that named **tabify** as the decision and **collapse** as a possible
follow-on — the implementation honoured that (tabify only; `VALID_MODES = ('tabify', 'stack')`), but the
tests for collapse were written anyway, so they failed against a mode the module correctly did not have.

**Discipline:** when a spec descopes an alternative, state it explicitly in the spec's *test* section — e.g.
"do not write tests for `collapse`; it is a deferred follow-on." A decision named only in prose ("we chose
tabify") is not enough; the test section is what the test author reads.

**Guards that catch what slips through** (added 1.6.304, `collapse_mode_and_test_guards` spec):
- **Guard A** (`test_dock_space::test_guard_A_*`): every entry in a `VALID_*` vocabulary must be settable,
  reachable from its planner, and match the preference-registry options exactly — no declared-but-dead or
  implemented-but-undeclared option.
- **Guard B** (`test_ci_dependencies::test_no_test_exercises_an_option_the_module_does_not_declare`): a test
  passing a string literal to a declared-vocabulary option setter (e.g. `set_reflow_mode`) must use a value
  in the module's `VALID_*` set, unless the call is inside `with pytest.raises(...)` (a legitimate rejection
  test). Narrow and mechanical — scoped to setters with a `VALID_*` constant, so it does not become a broad
  "tests must match specs" check that would false-positive and get disabled.

Neither guard replaces the descoping discipline; they catch the cases where the discipline was not followed.
