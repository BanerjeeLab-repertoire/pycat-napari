# Changelog
All notable changes to PyCAT-Napari will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.107] - 2026-07-03
### Changed
- **Upscaling set to 2×** (linear) to match v1.0.0. This corrects 1.5.106, which
  had changed it to 4× based on a miscommunication: v1.0.0 used 2×, and it is the
  newer 4× that was the regression. Images ≥ 2048px are left unscaled as before.
## [1.5.106] - 2026-07-03
### Changed
- **Upscaling restored to 4×** (linear) in the fluorescence pipeline, matching
  v1.0.0 behaviour — the 1.5.0 performance refactor had reduced it to 2×. Because
  4× multiplies the pixel count 16×, the factor now steps down automatically
  (to 3×/2×/1×) only when 4× would exceed a memory-safe 4096px output bound:
  e.g. 512→2048 and 1024→4096 at full 4×, a 1500px image falls back to 2×.
  All downstream micron sizes, object/cell diameters, and ball radius scale from
  the actual upscale ratio, so they stay consistent at any factor.
## [1.5.105] - 2026-07-03
### Fixed (critical — 2-D fluorescence pipeline regression)
- **"Yellow field / everything in one bin" background-removal output.** CLAHE
  (`equalize_adapthist`) requires float input in [0, 1], but the enhanced
  RB-Gauss background removal (and the preprocessing step) fed it the
  background-subtracted image in the ORIGINAL intensity scale (values in the
  thousands). On skimage ≥ 0.26 that raises; on older skimage it clips every
  pixel to the maximum, collapsing the image to a near-uniform field. All four
  CLAHE calls now go through a `_safe_equalize_adapthist` wrapper that min-max
  normalises to [0, 1] first, restoring structured output. This also fixes the
  over-aggressive intensity removal in preprocessing (same root cause).
## [1.5.104] - 2026-07-03
### Added
- **Batch phase diagram** (temperature workflow, Step 5): after a batch, PyCAT
  parses the TIFF filenames for the swept variable (ignoring constant buffers)
  and replicates, then plots T_cloud vs that variable with temperature on the
  y-axis. The **2-phase region is shaded** with sharp edges at the plot borders
  and a smooth **Akima** interpolation of the cloud points as the boundary; LCST
  (above) / UCST (below) selectable. If the filenames can't be parsed
  unambiguously (no varying token, or more than one), it warns and asks for
  manual specification instead of guessing.
### Fixed
- **Turbidity transition arrows** now point in the temperature-sweep direction
  (heating branch → up, cooling branch → down) instead of a fixed layout.
- **Scale bar** now shows microns whenever a valid pixel size is known — including
  one entered by the user in the pixel-size gate — not only when it came from
  metadata; the on-screen bar refreshes when the pixel size is set.
- **Horizontal scrollbars** removed from the workflow docks (set always-off on
  all dock scroll areas) so long buttons/labels fit instead of overflowing.
- **Step 1 (file I/O)** in the bottom workflow checklist now auto-completes when a
  workflow is opened with an image already loaded (previously stayed pending).
## [1.5.103] - 2026-07-03
### Changed
- Contrast Cascade: the focus-vs-growth **dim threshold** and **blur threshold**
  are now adjustable fields in the diagnostic panel (and flow through to the
  plot), so the below-focus/growth cutoff can be calibrated to real data.
## [1.5.102] - 2026-07-03
### Added — Contrast Cascade
New tool (Toolbox → Image Segmentation → Contrast Cascade) for images with large
object-to-object brightness swings, e.g. a bright condensate body that grows much
dimmer fibers. Three parts:
- **Visualise**: split the intensity range into a cascade of bands, each shown as
  a coloured napari layer with its own contrast, plus a log/CLAHE tone-mapped
  view — so bright and dim structure are visible at once.
- **Segment**: a Random Forest trained on brightness-INVARIANT features
  (local-contrast normalisation + ridge/tubeness filters), so it can separate
  body / fiber / background across the brightness swing — unlike the single-
  intensity RF, which only learns a threshold.
- **Diagnose**: for each object, compare edge sharpness AND brightness to the
  body to tell WHY dim objects are dim — dim+blurry ⇒ likely below focus,
  dim+sharp ⇒ likely nucleation/growth. Uses a size- and intensity-invariant
  edge-steepness measure; shows a sharpness-vs-intensity plot and a table.
## [1.5.101] - 2026-07-03
### Changed (UI/UX audit)
- **Data QC report redesigned to teach, not just score**: an overall verdict
  banner, a "what good data looks like / how to improve" line under every metric,
  and a "how it is measured" caption under each diagnostic panel — so the report
  guides users to better data instead of only reporting numbers.
- **Tooltips**: added descriptive tooltips across the Condensate Physics,
  Plotting Widget, and Advanced Analysis panels (coverage ~37% → ~50%).
- Verified all 65 menu actions resolve to defined handlers (no dead menu items).
## [1.5.100] - 2026-07-03
### Added (tables → graphs, continued)
- **Spatial metrology** multi-panel plot: NND distribution, Ripley's L(r)−r
  (>0 = clustered), pair-correlation g(r) (>1 = clustered), and radial
  localisation density — each showing per-cell curves with the mean overlaid.
- **Morphological complexity**: per-object metric distributions (fractal
  dimension, lacunarity, tortuosity, orientation) as small-multiple histograms.
- New generic `plot_distributions` helper for per-object metric histograms.
## [1.5.99] - 2026-07-03
### Added (tables → graphs, continued) & Fusion tab
- **Molecular counting** plot: the step-variance vs intensity line through the
  origin (slope = single-fluorophore brightness ν) plus the molecule-count
  distribution. `count_molecules_pooled` now returns the pooled variance pairs.
- **Fusion relaxation tab is now functional**: a new `extract_fusion_relaxation`
  detects merge events and follows the merged droplet's aspect ratio as it
  relaxes; the tab fits it, takes a characteristic length R (auto-uses the
  droplet's equivalent radius if left at 0), reports η/γ = τ/R, and plots the
  relaxation curve with the fit.
- **Intensity profiles**: line-scan and radial profile plots (radial shows every
  centre faint with the mean solid).
- **Client enrichment / partition**: per-condensate enrichment histogram with the
  median and the 1× (no-enrichment) reference marked.
## [1.5.98] - 2026-07-03
### Added (tables → graphs)
- **MSD trajectory plot**: per-track MSD curves (semi-transparent) with the
  solid ensemble mean + SEM band and the fitted power law, log-log — in both the
  VPT and Condensate Physics MSD steps.
- **Viscoelastic moduli G′/G″** (microrheology) via the Mason GSER from the MSD,
  plotted vs frequency with the crossover marked (VPT).
- **FRAP recovery curve** with the fitted model, mobile-fraction plateau, t½,
  and R² — replaces the FRAP results table as the primary output.
- **Coarsening kinetics** plot (radius vs time with the fitted t^1/3 / t^1/2
  curves) and **Kaplan–Meier survival** step curve.
- New `analysis_plots.py` module and `per_track_msd_curves` / `compute_moduli_gser`
  helpers.
## [1.5.97] - 2026-07-03
### Changed (scientific accuracy)
- **MSD uncertainty** is now computed per-track (tracks are the independent
  unit), adding `msd_sem` and `n_tracks`; the old pooled-pairs `msd_std`
  understated uncertainty because overlapping displacement pairs are correlated.
  Removed the vestigial unused `microns_per_pixel` argument.
- **Anomalous-diffusion fit** now uses a weighted direct non-linear fit of
  MSD = 4Dτ^α (seeded by log-log), removing the log-transform bias and
  down-weighting noisy large-lag points. Recovers D/α more accurately.
- **Coarsening**: removed dead code that made `arrested_r2` meaningless; added a
  `mechanism_confidence` and `mechanism_caveat` (t^1/3 vs t^1/2 are hard to
  separate over short ranges), plus `radius_change_frac`. The UI now warns when
  confidence is low.
- **Ripley's L** now uses the rigorous **border-method** edge correction when
  per-point boundary distances are available (supplied by the spatial-metrology
  UI from the cell mask), instead of a crude isotropic-weight approximation.
- **Fusion relaxation** accepts a characteristic length R and returns η/γ = τ/R
  (inverse capillary velocity) in addition to τ.
- **Partition coefficient** now reports dense and dilute intensities (raw and
  background-subtracted) and the background explicitly; stopped clipping (which
  biased means near background); clarified that "background" is the instrument
  offset from a signal-free region — never the dilute phase.

## [1.5.96] - 2026-07-03
### Fixed
- Time-Series workflow: the Start/End frame range no longer resets to the full
  stack when a downstream step adds a layer. The range is now locked as soon as
  the user sets it (edits a spinbox or ticks "Restrict to frame range"), while
  programmatic refreshes are still allowed to update the bounds.

## [1.5.93] - 2026-07-03
### Added
- Data Quality Control dashboard: **Save Report (PNG + CSV)** button — saves the
  report figure and a self-documenting metric table (value, status, how each
  metric is measured, and what good data looks like).

## [1.5.92] - 2026-07-03
### Added
- **Data Quality Control dashboard** (Toolbox → Data Visualization → Data
  Quality Control): a teaching-oriented acquisition-quality report. Each metric
  returns a colour-coded status, the value, how it is measured, and what good
  data looks like, with a diagnostic plot per metric.
  - CORE (absolute thresholds): saturation/clipping, focus/sharpness, SNR/noise,
    vignetting/flat-field, ghosting (double image, via the cepstrum), and lateral
    drift (phase cross-correlation).
  - ADVISORY (heuristic or need input): spherical aberration (through-focus
    asymmetry on a z-stack), Nyquist spatial sampling (pixel vs λ/4·NA), temporal
    sampling, mechanical vibration, and chromatic aberration.

## [1.5.91] - 2026-07-03
### Added
- Temperature workflow: **Save Results (CSV)** and **Clear Results** buttons —
  saves the transition summary (T_cloud, T_clear, hysteresis, branches) and the
  full turbidity curve; restores the data output that the plot had replaced.
- Adjustable **onset threshold** (% of baseline-to-peak amplitude) for the
  baseline transition-detection method.
### Changed
- Dropped `focus_score` from the stored turbidity curve so it is not offered as
  a Plotting Widget Y-axis option (it is collinear with turbidity).
- Removed the redundant **Random Forest Classifier** menu item (it opened the
  same unified segmentation widget as Cellpose; RF is still available there).

## [1.5.90] - 2026-07-03
Consolidated summary of changes since 1.5.39 (many iterative point releases).

### Added
- **Temperature-Dependent Microscopy** workflow: entropy-based turbidity curve,
  automatic clear-frame detection (coefficient-of-variation), and a pop-up
  transition plot with the heating branch in red and cooling in blue.
- **Transition-temperature detection** with two selectable methods: baseline
  departure/return (onset of appearance / completion of dissolution) and
  steepest-point midpoint. T_cloud/T_clear are assigned by signal direction, so
  it is correct for both LCST and UCST systems.
- **Gray-preserving static-pattern (dust/scratch) correction**
  (`corrected = frame - reference + mean(reference)`), available as a selectable
  napari layer and as an export option in the interactive and batch steps. The
  reference frame is rebuilt from its neighbours so it is not a flat outlier.
- **Calibration-frame background correction** tool (flat-field division and
  background subtraction) under Toolbox → Image Processing.
- **Auto scale bar at load** (from image metadata) and a **Home** menu action
  that fits the view to the selected layer.
- **Batch annotated-MP4 export** and **batch pattern-corrected TIFF export** for
  the temperature workflow.
- **PyCAT branding**: Windows taskbar icon, PyCAT logo on the napari welcome
  screen, and a "PyCAT <ver> • napari <ver>" version line.

### Changed
- The app now launches **maximized** (robustly, via a double-shot timer).
- **Focus-drift correction defaults OFF** for turbidity: the focus metric is
  collinear with condensate formation, so regressing it out over-corrects.
- Cross-workflow **UI compactness**: layer dropdowns, long buttons, long
  checkboxes/radio buttons, and text fields now shrink instead of forcing the
  dock wider than its slot; long description labels word-wrap; long group titles
  shortened. This removes the horizontal scrollbar from the workflow docks.
- Merged the temperature CSV / folder inputs into a single auto-detecting field.

### Fixed
- **Condensate segmentation quality regression**: `segment_subcellular_objects`
  now defaults to whole-image processing (byte-for-byte PyCAT 1.0.0). The
  bounding-box crop starved the gaussian-background and CLAHE context; it is now
  opt-in via `crop_to_cell=True`.
- **Scale-bar black-canvas bug** in the temperature workflow: setting
  `Layer.units` on a lazy 3-D stack triggered a black render on napari 0.7.1.
  PyCAT now drives the scale bar from `Layer.scale` + `scale_bar.unit` only.
- **Duplicated Cell Segmentation widget** in the condensate, object-coloc and
  pixel-coloc workflows (a backward-compat Random-Forest shim re-added the
  unified widget) — removed the redundant call.
- Entropy reference-frame **outlier spike** and CPU-only Cellpose slowness now
  surface a one-time in-app warning with GPU install guidance.


### Changed
- **Minimum Python is now 3.12** (supported range `>=3.12,<3.14`). Python 3.9
  reached end-of-life on 2025-10-31 and core dependencies (NumPy, napari, and
  others) have dropped it; moving the floor keeps installs resolvable and the
  toolchain current. The upper bound is a tested-ceiling promise and will be
  widened once 3.14 wheels are verified across the stack.
- Updated build metadata (classifiers) and black/ruff `target-version` to py312.
### Added (recent analysis modules)
- Force-Distance Curve (DNA tethering) workflow with ssDNA/ssRNA FJC model and
  rip/unzip (G-quadruplex) detection.
- Molecular Counting by Photobleaching (step-noise / Mutch method).
- Gaussian Spot Localization (sub-pixel centre + PSF width, 2D/3D).
- VPT bead quality-fit classification (singlet / aggregate / out-of-plane) with
  aggregates routed to a secondary tracked population.
- Client Partition / Enrichment (second-channel recruitment into condensates).
- Intensity Profiles (line-scan + radial, interface-width estimation).
- Exposed Morphological Complexity metrics (fractal dimension, lacunarity,
  tortuosity, orientational order) via a Toolbox panel.
- Spatial Randomness, FFT bandpass, manual threshold, best-slice, and
  temperature-dependent condensate tools.
### Note
- Install into a Python 3.12 environment first, e.g. `mamba create -n pycat-env python=3.12`, then install PyCAT.

## [1.0.0] - 2024-11-22
### Added
- Initial public release of PyCAT-Napari
- Complete GUI interface built on Napari viewer
- Core functionalities for biomolecular condensate analysis:
  * Image processing and analysis tools
  * Fluorescence image analysis capabilities
  * Condensate feature detection and measurement
  * Data visualization tools
  * Colocalization and Correlation analyses 
- Command-line interface via `run-pycat` command
- Python API for programmatic access to analysis tools
- Support for multiple imaging file formats
- Integration with popular scientific Python libraries
- Platform support for Windows, macOS (including Apple Silicon), and Linux
- Comprehensive error handling and user feedback
- Basic documentation and usage examples

### Dependencies
- Compatible with Python 3.9+
- Core dependencies include:
  * napari
  * numpy
  * opencv-python-headless
  * scikit-image
  * scipy
  * torch
  * And other scientific computing libraries

### Fixed
- (List any bug fixes here)

### Changed
- (List any changes to existing features here)

### Deprecated
- (List any features that are deprecated and will be removed in future releases here)

[1.0.0]: https://github.com/BanerjeeLab-repertoire/pycat-napari/releases/tag/v1.0.0

## [1.0.2] - 2025-06-26
### Added
- Batch processing module (`batch_processor.py`): session config recording,
  JSON export/import, and batch runner with folder picker and progress dialog
- Spatial ACF analysis module (`spatial_acf_tools.py`): per-cell LIR-cropped
  SACF, drawn-rectangle mode, and whole-image mode with Gaussian sigma fitting
  and cluster diameter output per slice
- `largestinteriorrectangle` added as a core dependency

### Fixed
- Cellpose v4 compatibility: `model_type` → `pretrained_model` argument
- GPU acceleration: CUDA PyTorch install documented as primary method

## [1.5.0] - 2026-07-01
### Added
- **New analysis pipelines**: Cellular Condensate Analysis (Brightfield), In Vitro
  Condensate Analysis (Fluorescence & Brightfield), Time-Series Condensate Analysis,
  and Z-Stack (3D) Condensate Analysis, each with its own workflow checklist and
  batch-replay support
- **Multi-dimensional file I/O** (`file_io/multidim_io.py`): lazy 4D (T,Z,Y,X)
  loading for nested time-series-with-Z-stack acquisitions in both IMS and
  OME-TIFF/CZI, replacing prior behavior that silently discarded one dimension
  when both T>1 and Z>1; multi-position/multi-scene detection and selection
  dialog for IMS sibling files and AICSImage scenes
- **Z-stack (3D) condensate segmentation** (`zstack_segmentation_tools.py`):
  3D background removal, 3D cell segmentation (per-slice Cellpose stitched
  across Z via IoU overlap linking), 3D condensate segmentation (per-slice 2D
  pipeline merged into true 3D objects via 3D connected-component linking),
  and volumetric metrics (volume, sphericity via marching-cubes, ellipsoid
  axis lengths, anisotropic Z-step handling)
- **Pseudo-3D (tri-planar) linear filtering** (`pseudo3d_tri_planar_filter`):
  runs Gaussian/Gabor/DoG filters along XY, XZ, and YZ planes (or XY/XT/YT for
  time series) and averages the result, exploiting genuine correlation between
  adjacent Z-slices or oversampled frames; applied to Z-stack background
  removal and, with a frame-to-frame correlation regime check
  (`estimate_temporal_correlation`) to avoid misuse on undersampled time
  series, to time-series preprocessing
- **TrackMate integration** (`trackmate_bridge.py`): optional bridge to real
  TrackMate (Jaqaman LAP tracker, Kalman tracker) via an embedded headless
  Fiji/ImageJ2 instance (pyimagej). PyCAT's own condensate/cell detections are
  injected directly as TrackMate spots, bypassing TrackMate's detection step;
  results convert back to PyCAT's standard trajectory schema for use by all
  downstream biophysics tools. New `trackmate` optional-dependencies extra
- **Time-series pipeline**: keyframe Cellpose segmentation with nearest-keyframe
  propagation, phase-correlation drift correction, per-frame spatial metrology,
  frame-range/XY-ROI selection, and lazy zarr-backed stack preprocessing
- **Trajectory tracking and dynamics** (`dynamic_spatial_tools.py`): greedy
  nearest-neighbour and Bayesian (Hungarian/LAP) trajectory linking with
  velocity-assisted prediction and gap closing, merge/fission detection,
  cluster lifetime analysis, neighbourhood persistence, growth/shrinkage
  kinetics
- **Condensate biophysics** (`condensate_physics_tools.py`): MSD/anomalous
  diffusion fitting, bimodal intensity decomposition and Csat estimation,
  fusion relaxation and coarsening-mechanism fitting, Kaplan-Meier survival
  analysis, unified frame-quality diagnostics distinguishing photobleaching
  from focal drift
- **Spatial analysis suite** (`spatial_metrology_tools.py`,
  `morphological_complexity_tools.py`, `organizational_metrics_tools.py`):
  nearest-neighbour distance, Ripley's L, pair correlation function, Voronoi/
  Delaunay metrics, convex hull metrics, fractal dimension, lacunarity,
  tortuosity, orientation order, spatial entropy, DBSCAN clustering,
  inter-condensate spacing, per-cell occupancy
- **Brightfield and in-vitro toolboxes** (`brightfield_tools.py`,
  `invitro_tools.py`): optical density metrics, contact-angle measurement,
  field-level statistics (volume fraction, number density, size distribution),
  Csat estimation via lever-rule fitting on dilution series
- **Fibril pipeline additions**: binary-mask labeling step, morphological
  complexity and organizational metrics integration
- Session reload from a previous output folder without re-running analysis
  (`file_io/session_loader.py`)

### Changed
- **Time-series analysis loop parallelized**: per-frame condensate segmentation
  (previously fully serial) now dispatches across a `ProcessPoolExecutor`,
  giving roughly 6-8x wall-clock speedup on multi-core machines; frames read
  directly from filesystem zarr stores rather than being pickled through IPC
- **Combined single-pass stack preprocessing**: preprocessing and background
  removal, previously two sequential full-stack `ProcessPoolExecutor` passes,
  now run as one combined pass, roughly halving I/O and pool-startup overhead
  when both are enabled (the default)
- **Eliminated redundant double connected-components labeling** in the
  time-series analysis loop (same array labeled twice per frame/cell iteration)
- **Keyframe Cellpose memory footprint** reduced ~20x via a lazy
  nearest-keyframe view (`_KeyframeMaskStack`) instead of materialising a full
  duplicated-frame `(T,H,W)` array
- **Algorithm-level speedups** in `image_processing_tools.py`: white tophat
  (square footprint, ~8.5x), LoG→DoG reformulation (~1.3x), parallel Gabor
  bank (ThreadPoolExecutor, ~3.3x), GPU-accelerated rolling ball via CuPy when
  available, faster CLAHE tiling (~5.5x)
- **Segmentation bounding-box crop optimisation** in
  `segment_subcellular_objects`: expensive per-cell operations (background
  removal, Felzenszwalb, Niblack/Sauvola) now run on a cropped ROI rather than
  the full image, ~5-20x speedup for typical multi-cell fields

### Fixed
- IMS and generic (TIFF/CZI) stack loaders no longer silently discard Z-stack
  data when a file has both T>1 and Z>1 — previously forced a single-timepoint
  choice (IMS) or picked T-xor-Z as "the" stacking dimension (generic loader),
  losing an entire dimension of acquired data in either case
- Multi-position selection dialog now correctly defaults to the file the user
  actually opened rather than the numerically-lowest position after sorting
- Z-stack pipeline `ball_radius` no longer silently diverges between the
  background-removal step (user-set via spinbox) and the condensate-
  segmentation step (previously read an unpopulated `data_repository` key and
  always fell back to a hardcoded default)
- Cellpose v4 compatibility: `model_type` → `pretrained_model` argument
  (previously silently ignored)
- GPU acceleration: CUDA PyTorch install documented as primary method,
  verified safe for CPU-only machines
- Spatial metrology and merge/fission detection call-signature mismatches
  across brightfield and in-vitro UIs (wrong arguments/nonexistent kwargs)
  found and fixed via cross-module audit
- Batch step registry coverage gaps for several recorded-but-unregistered
  pipeline steps

[1.5.0]: https://github.com/BanerjeeLab-repertoire/pycat-napari/releases/tag/v1.5.0
