# Changelog
All notable changes to PyCAT-Napari will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
