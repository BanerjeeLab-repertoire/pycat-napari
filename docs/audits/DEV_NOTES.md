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
