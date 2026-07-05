# Changelog
All notable changes to PyCAT-Napari will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.188] - 2026-07-05
### Fixed (auto-home on image load — direct camera set instead of reset_view)
- **Images now reliably fill the canvas on load.** The 1.5.184 implementation used
  `viewer.reset_view()` via a 150ms QTimer, which silently did nothing if napari had
  not yet finished computing the layer extent. The new implementation uses the same
  direct camera-set approach as the Home button: reads the canvas pixel dimensions
  from ``viewer.window._qt_viewer.canvas.size``, computes
  ``zoom = min(ch/H, cw/W) * 0.9`` from the known image H and W, then sets
  ``viewer.camera.center`` and ``viewer.camera.zoom`` directly. This is independent
  of napari's internal extent computation timing and matches exactly what pressing
  Home does. Falls back to ``reset_view()`` if the canvas size cannot be read.

## [1.5.187] - 2026-07-05
### Fixed (hardware-dependent segmentation — GPU/CPU algorithm inconsistency)
- **`compute_rolling_ball_background` now uses the same algorithm on GPU and CPU.**
  Previously, the GPU path used morphological opening (grey erosion + dilation with a
  disk footprint of radius ``ball_radius``) while the CPU path used
  ``skimage.restoration.rolling_ball`` — a genuinely different algorithm that treats
  pixel intensity as a literal extra spatial dimension and is sensitive to the image's
  numeric range in a way plain morphological opening is not. This caused different
  segmentation outcomes on identical data depending on which hardware ran, a silent
  reproducibility failure confirmed by a user on CPU-only hardware. Both paths now use
  the same morphological-opening algorithm (disk of radius ``ball_radius``). Also
  removed the redundant ``ndi.gaussian_filter`` inside this function: the caller
  ``rb_gaussian_bg_removal_with_edge_enhancement`` already applies the same Gaussian
  to the background estimate, so the previous code was smoothing twice with the same
  sigma, spreading the background estimate into real signal and causing over-subtraction.
- **Reverted the 1.5.183 change** (``bg_removed_crop = proc_crop`` fallback). The
  zeros fallback is correct — the algorithm-consistency fix above is what actually
  resolves the segmentation failure on CPU-only machines.

## [1.5.186] - 2026-07-05
### Documentation
- Added "USB HDD lazy-loading latency" to Known Issues in roadmap. Lazy-loading IMS
  or large TIFF/HDF5 files from a USB 2.0 spinning HDD causes ~250–300 ms per-frame
  lag when scrubbing Z/T sliders (~8 MB/frame at ~30 MB/s). Documented the per-bus
  bandwidth breakdown (USB 2.0 / 3.0 / 3.1), three immediate workarounds (check port
  colour/SS label, copy locally first, pre-load the relevant range), and noted that the
  planned LRU frame cache (already on the roadmap) is the primary software-side
  mitigation for repeated scrubbing of already-visited frames.

## [1.5.185] - 2026-07-05
### Fixed (IMS loading — singleton axis squeeze + robust indexing)
- **`_ImsReaderTYX/ZYX/TZYX`: singleton axes from `imaris_ims_file_reader` now
  stripped correctly.** With `squeeze_output=False`, direct reads such as
  `reader[0, c, 0, :, :]` may return shape `(1, 1, 1, Y, X)` instead of `(Y, X)`,
  causing `ValueError: axes don't match array` in napari. New helper `_ims_frame_2d`
  calls `np.squeeze` and validates the result is exactly 2-D before returning.
- **Robust `__getitem__` for all three classes.** New `_ims_indices` helper converts
  any selector (int, slice, list, Ellipsis) to a concrete list; each class reads
  frame-by-frame and stacks, returning a scalar-indexed plane or a stacked array
  exactly as napari expects.
- **`_ImsReaderTZYX` z-squeeze fix over the submitted patch.** The patch's original
  `arr[:, 0]` squeeze for a scalar Z selector on a `(1, 1, Y, X)` array after T-squeeze
  produced `(1, X)` instead of `(Y, X)`. Fixed to squeeze Z before T, so all three
  indexing modes (`[t, z]` → (Y,X), `[t, :]` → (Z,Y,X), `[:, :]` → (T,Z,Y,X))
  produce the correct shapes. Validated numerically.

## [1.5.184] - 2026-07-05
### Fixed (five UX issues from user report)
- **Overlay image rendered as a green stripe.** Two compounding bugs: (1) the green
  channel was converted with `dtype_conversion_func(..., 'uint16')` which calls
  `img_as_uint` on a float32 image with values outside [-1,1], collapsing it to a
  flat array; (2) the final `dtype_conversion_func(sbs_overlay, 'uint16')` ran on the
  uint8 RGB output of `create_overlay_image`, rescaling 0–255 to 0–65535 and
  destroying the composite. Both fixed: `_to_uint16_safe` is now used for the green
  channel, and the second conversion is removed entirely.
- **Images open small — no auto-zoom.** `_finalise_stack_load` now calls
  `viewer.reset_view()` (deferred 150 ms via QTimer so the layer extent is computed
  first), equivalent to pressing the Home button after every file open.
- **Napari notifications persist through Clear.** `_clear_everything` now clears the
  notification manager's record list so stale "Processing cell 3 of 3" messages from
  the previous session don't persist into the next.
- **Status circles (red/yellow) don't turn green when a layer is selected via
  auto-populate.** `_layer_row` now also connects `_update_circle` to the
  `layers.events.inserted` signal (deferred via QTimer so the dropdown index has
  already updated). Previously, if auto-selection via `name_hint` landed on index 0
  with no index change, Qt suppressed `currentIndexChanged` and the circle stayed red.
- **Dropdown auto-population corrected per step:**
  - Step 6 (Cellpose): hints to `'Upscaled Segmentation'` image
  - Step 7 (Cell Analyzer): hints to `'Upscaled Fluorescence'` image
  - Step 8 pre-processed input: hints to `'Enhanced Background Removed'`
  - Step 8 fluorescence input: hints to `'Upscaled Fluorescence'`
  - Step 9 puncta image: hints to `'Upscaled Fluorescence'`

## [1.5.183] - 2026-07-05
### Fixed ("Cell X has low contrast" on dim images even after 1.5.179)
- **Root cause: `perform_bg_removal = False` set `bg_removed_crop` to a zero array.**
  When the Cell Analyzer measures a cell's `gaussian_snr_estimate < 1.0` (common on dim
  images such as the GFP test image with int16 max ~1280), the segmentation code skipped
  background removal and assigned `bg_removed_crop = np.zeros_like(orig_crop)`. A zero
  array trivially passes `check_contrast_func` as "no contrast", producing "Cell X has
  low contrast, likely has no puncta" and 0 objects — even when real condensates are
  visible. Fixed: when background removal is skipped due to low SNR, `bg_removed_crop`
  now falls back to `proc_crop.astype(float32)` (the pre-processed image directly),
  matching what the `_already_enhanced` branch already does. This gives Felzenszwalb
  segmentation a real signal to work with rather than zeros, preserving any genuine
  puncta in dim cells.

## [1.5.182] - 2026-07-05
### Fixed (IMS loading — direct reader replaces broken zarr-store adapter)
- **IMS files now load via the direct ``imaris_ims_file_reader.ims`` reader for all
  three lazy cases (T,Y,X / Z,Y,X / T,Z,Y,X), bypassing the zarr-store adapter
  entirely.** The adapter's ``__getitem__`` could raise ``KeyError: '0.0.0.0.0'`` for
  valid IMS chunk keys when the file lives on Box Drive, a network share, or is held
  open by Imaris (via ``h5py`` raising ``OSError: Can't synchronously read data``).
  New classes — ``_ImsReaderTYX``, ``_ImsReaderZYX``, ``_ImsReaderTZYX`` — have the
  same external interface (shape, dtype, ndim, __getitem__, __len__, __array__,
  transpose) so napari treats them identically and laziness is fully preserved: only
  the frame the user scrubs to is read from disk. Patch authored externally; applied
  on top of the defensive wrapper added in v1.5.177.
- Added ``import hdf5plugin`` before ImsReader instantiation, registering bundled HDF5
  compression filters needed by some IMS files to decode pixel data.
### Known issue noted
- The direct-reader path has no internal chunk cache (the zarr adapter had one). For
  interactive use this is imperceptible; for batch workflows that re-read the same
  frames in a tight loop it may add I/O overhead. Flagged in roadmap as a future
  LRU-cache addition to the ``_ImsReader*`` classes.

## [1.5.181] - 2026-07-05
### Fixed (SyntaxError preventing startup)
- **`segmentation_tools.py` caused a SyntaxError on startup in Python 3.12 on
  Windows.** Two issues combined: (1) em-dash characters (`—`) in comments and
  docstrings are valid UTF-8 but Python 3.12's default tokeniser on Windows rejected
  them without an explicit encoding declaration; added `# -*- coding: utf-8 -*-` to
  the file header. (2) An earlier str_replace that inserted the `run_segment_subcellular
  _objects` guard block only replaced the function signature line, leaving the old
  docstring body (Parameters / Raises / Notes sections) orphaned as unreachable text
  *after* a `return` statement, with its closing `"""` creating an unmatched
  triple-quote that caused an "unterminated triple-quoted string literal" SyntaxError
  detectable only at runtime. Both issues now fixed; full tree compiles clean.

## [1.5.180] - 2026-07-05
### Changed (Clear now returns to true blank state; opt-in measurement persistence)
- **Clear and Save & Clear now restore the true initialization state** — ball_radius,
  object_size, and cell_diameter reset to their constructor defaults (75, 50, 100)
  exactly as `_initialize_repository` specifies. The 1.5.178 unconditional
  measurement-preservation was reverted; it was addressing the wrong root cause
  (the real bug was the `img_as_uint` clipping fixed in 1.5.179).
- **"Remember measurements across clears" checkbox added to the Measure Line widget.**
  Off by default (true reset). When ticked, ball_radius, object_size, and cell_diameter
  are preserved across Save & Clear and Clear, so users processing a batch of images
  from the same experiment don't need to re-measure each time. The flag lives on
  CentralManager (survives individual clears, resets on restart), following the same
  pattern as "Keep this pixel size for the session" on the pixel-size gate.

## [1.5.179] - 2026-07-05
### Fixed (connected bugs: "Cell X has low contrast" + "0 objects after refinement" on second run)
- **Root cause: `sk.util.img_as_uint` clips float32 values outside [-1, 1].** Background-
  removed and CLAHE-processed images are float32 with values e.g. [0, 1500] — well outside
  the [-1,1] range `img_as_uint` requires. When passed through `dtype_conversion_func(...,
  'uint16')`, all values collapse to the uint16 floor/ceiling, producing a flat array.
  Three downstream effects all trace to this single root cause:
  1. **`check_contrast_func`** received the flat uint16 array → `max - min ≤ 2` → returned
     `True` (no contrast) → `"Cell X has low contrast, likely has no puncta"` even on images
     with clear condensates. **Fixed**: `check_contrast_func` now works directly on the raw
     float values with a relative contrast threshold (range < 0.1% of magnitude), never
     calling `img_as_uint`.
  2. **`puncta_refinement_filtering_func`** and **`_fast` variant** built `original_image_16`
     / `processed_image_16` with the same broken conversion → `np.std(local_pixels) < 2` →
     every object dropped before kurtosis/SNR checks even ran → 0 objects. **Fixed**: a new
     `_to_uint16_safe` helper normalises any float image to [0, 1] before conversion,
     preserving relative intensity differences while satisfying `img_as_uint`'s contract.
     Verified: `std` goes from ~0 to ~24 000 on a [0, 1500] float image.
  3. **`apply_watershed_labeling`** had the same broken conversion. **Fixed** with
     `_to_uint16_safe`.
- Both reported symptoms — "Cell 3 is a low contrast image" on a new image after Clear,
  and "0 objects after refinement filtering" on a second run of the same image — are the
  same bug hitting at different stages depending on how early the flat array is encountered.

## [1.5.178] - 2026-07-05
### Fixed (condensate segmentation fails with 0 objects after Save & Clear)
- **Root cause identified and fixed: `ball_radius` resets to 75 after Save & Clear,
  which is ~10× too large for typical condensates.** `reset_values(clear_all=True)`
  restores `ball_radius=75`, `object_size=50`, `cell_diameter=100` — the hardcoded
  constructor defaults. When the user re-runs Step 8 (Condensate Segmentation) on a
  second image without first re-doing Step 2 (Measure Line), the CLAHE kernel
  (`4 × ball_radius = 300 px`) and local threshold window (`ball_radius = 75 px`) are
  tuned for objects ~100 px in diameter — ~10× larger than real condensates — causing
  the segmentation to produce 0 objects before refinement even runs. The
  "0 objects after refinement filtering" warning then lists threshold tuning as the
  likely cause, which is misleading when the real issue is an un-reset ball_radius.
  Two fixes:
  1. **`ball_radius`, `object_size`, `cell_diameter`, `microns_per_pixel_sq`, and
     `pixel_size_from_metadata` are now preserved across Save & Clear and Clear.**
     These are measurement-derived and should persist when loading a second image
     from the same experiment, so re-measuring is no longer required each time.
  2. **Guard in `run_segment_subcellular_objects`**: if both `ball_radius` and
     `object_size` are exactly at their constructor defaults (75 and 50), the function
     aborts with a clear actionable message pointing to Step 2 (Measure Line) rather
     than running and producing 0 objects with a misleading threshold-tuning suggestion.
  Reported by user running the `In Cell 1-GFP.tif` test image a second time after
  Save & Clear: first run segmented correctly, second run produced 0 objects.

## [1.5.177] - 2026-07-05
### Fixed (IMS loader — Box Drive / network share read failure)
- **IMS files on Box Drive or network shares no longer crash the loader.** The probe
  read (`lazy_tyx[0]`, `lazy_zyx[0]`) that was done eagerly at load time to set
  default diameter estimates could raise an `OSError: Can't synchronously read data`
  (h5py) → `KeyError: '0.0.0.0.0'` (imaris_ims_file_reader) when the HDF5 file is
  not fully materialised locally (Box Drive stub), held open by Imaris, or on a slow
  network share. Previously this aborted the entire load and showed the opaque message
  `"Failed to open stack: '0.0.0.0.0'"` — the layer never appeared at all.
  Now: the probe read is wrapped in a try/except; on failure a clear warning is shown
  (`"ensure it is fully downloaded locally — right-click → Make Available Offline in
  Box Drive"`) and `channel_data` falls back to a zero array of the correct spatial
  size (derived from IMS metadata, which always loads first). The napari layer is still
  added lazily and will load correctly when the user scrubs to a frame. Reported by
  Shamli Manasvi (file: T=5, C=4, Z=1, 2048×2048 IMS on Box Drive).

## [1.5.176] - 2026-07-05
### Fixed (missing patches applied to uploaded 1.5.175 base)
- **ImportError on startup** — `spida_tools.py` and `spida_ui.py` were missing
  from the repository while `ui_modules.py` imported them; PyCAT would not start.
  Both files now present.
- **NameError `QSizePolicy` in `spatial_randomness_tools.py:359`** — used but not
  in the local import block. Added.
- **NameError `napari_show_info` in `image_processing_tools.py:619`** — only
  `show_warning` was imported; upscaling success notification would crash.
  Added `from napari.utils.notifications import show_info`.
- **Mean and Additive multi-merge produce identical results** (`layer_tools.py`) —
  per-result min-max normalisation cancelled the ÷N factor. Fixed to clip to the
  input dtype's range and scale by a fixed maximum.
- **Missing builder methods** `_add_run_expand_labels` and `_add_run_mask_logic_merge`
  in `ui_labels_mixin.py` — both were in the Labeled Mask Tools menu but no builder
  existed, causing an AttributeError when opened. Added builders; added
  `run_expand_labels` and `run_mask_logic_merge` to the label_and_mask_tools import.
### Added (features carried from the audit session)
- **Fibril Analysis** (`fibril_tools.py` + `fibril_ui.py`): four-panel analysis
  (bead-on-fibril detection, morphometry, before/after registration, crossing-node
  graph theory). Added to Toolbox → Spatial Metrology menu.

## [1.5.175] - 2026-07-05
### Added (Number & Brightness — camera / widefield counterpart to SpIDA)
- **New molecular-counting method: Number & Brightness (N&B)** (Toolbox → Advanced
  Analysis → Molecular Counting → Number & Brightness). The camera/time-series
  counterpart to SpIDA (Digman et al., Biophys. J. 94:2320, 2008), for
  widefield / TIRF / spinning-disk / sCMOS data where SpIDA's confocal assumptions
  don't hold.
  - `nb_tools.py`: per-pixel temporal mean/variance → brightness (ε = σ²/⟨I⟩) and
    number (n = ⟨I⟩²/σ²) maps, with scalar detector correction
    (ε = (σ²−σ²_read)/(S·(⟨I⟩−offset))). Validated against synthetic time-series:
    <2% recovery of known number and brightness.
  - **Global bleaching detrend** (multiplicative frame rescaling that preserves
    per-pixel fluctuations — the correct N&B correction, not per-pixel subtraction).
    No-bleach control recovers exactly; mild bleaching within ~10%.
  - Outputs per-pixel **brightness and number maps** as new layers plus an ROI (or
    whole-frame) summary, with optional oligomeric-state readout against a monomer
    reference. Scalar gain/offset/read-variance now (suited to the lab's Kinetix
    sCMOS); a per-pixel variance-map correction is a documented future extension.
  - Guardrails: warns on 2D (non-time-series) input, too few frames, and apparent-
    (uncorrected) brightness; notes the exchange-between-frames and bleaching
    assumptions.

## [1.5.174] - 2026-07-05
### Added (SpIDA modality guardrail)
- **SpIDA now has an acquisition-modality selector and guardrail.** A "Acquisition
  modality" dropdown (Confocal / TIRF / Widefield) drives `check_modality()`:
  widefield raises a strong warning that SpIDA's density/brightness are not valid on
  unsectioned camera data (no beam focal volume, out-of-focus light distorts the fit
  variance, PMT noise model doesn't apply) and points to Number & Brightness as the
  camera/time-series alternative; TIRF is allowed with a camera-noise caveat. A
  data-driven heuristic also flags a high flat background floor (typical of widefield
  haze) even when confocal is selected. Modality warnings are echoed into the result
  summary.

## [1.5.173] - 2026-07-05
### Added (SpIDA — Spatial Intensity Distribution Analysis)
- **New molecular-counting method: SpIDA** (Toolbox → Advanced Analysis →
  Molecular Counting → SpIDA). Estimates fluorescent particle **density** (N,
  particles/beam-area) and **quantal brightness** (epsilon) from the pixel-intensity
  histogram of a confocal-image ROI, and — after a monomer calibration — reports the
  **oligomeric state** (epsilon / epsilon_0; ~1 monomer, ~2 dimer).
  - `spida_tools.py`: the histogram model is a direct port of the authors' reference
    MATLAB implementation (Godin et al. 2011, `SpIDA_Functions.m`) with its three
    numerical regimes (Gaussian for N>70, generalized-Poisson for N>6, blended 6–7)
    and the moment-based fit initialisation from `fit_SpIDA_histo.m`
    (epsilon0 ≈ var/mean, N0 ≈ mean²/var). Validated against images simulated by the
    reference method: R² 0.99 and <10% recovery error on N and epsilon; a 2×-brightness
    sample is correctly identified as a dimer (state 1.90×).
  - **Calibration step** measures the monomeric reference epsilon_0 from a control ROI;
    without it, density and brightness are still reported but no oligomeric state
    (rather than a misleading number).
  - **Assumption guardrails** (`check_assumptions`): warns on small ROI / undersampling,
    saturation-clipping (linear-response violation), and low signal-to-background —
    surfacing conditions that make the numbers untrustworthy instead of returning them
    silently. Reporting is Image → Assessment → Interpretation, per PyCAT's
    anti-black-box philosophy.
  - New "Molecular Counting" submenu under Advanced Analysis groups SpIDA with the
    existing Photobleaching Step Counting tool.

## [1.5.172] - 2026-07-05
### Added (roadmap items)
- **Expand Labels** (Toolbox → Labeled Mask Tools → Expand Labels): grows each label
  outward by a chosen pixel distance using `skimage.segmentation.expand_labels`, which
  preserves label identity and does NOT merge touching objects — addresses the
  roadmap's "segments too small" item. New `run_expand_labels` in
  `label_and_mask_tools`.
- **Mask Layer Operations (AND / OR / XOR)** (Toolbox → Layer Operations → Mask
  Operations): boolean set operations on two masks — AND = overlap, OR = union,
  XOR = symmetric difference. Inputs are binarized so both binary and labeled masks
  work. New `run_mask_logic_merge`. Verified numerically.
### Changed (documentation)
- Pruned `roadmap.rst`: added a "Recently Completed" section (VPT, batch, 3D/Z-stack,
  time-series, watershed, top-hat, Cellpose model selection, progress bars/threading,
  the two new label ops, and the workflow scaffolding), an "Outstanding & Noted"
  section (status-marker completion, remaining step enumeration, BioIO migration, QC
  advisor, 3D rendering presets, kymographs), and marked the individual shipped items
  and the fixed merge Known Issue inline.

## [1.5.171] - 2026-07-05
### Fixed (code audit)
- **`NameError: QSizePolicy` in `spatial_randomness_tools._add_spatial_randomness`.**
  `QSizePolicy` was used (line ~359) but omitted from the local PyQt5 import; the widget
  would crash when built. Added it to the import. (Same moved/missing-import class the
  mixin guard catches.)
- **`NameError: napari_show_info` in `image_processing_tools.run_upscaling_func`.** Only
  `show_warning` was imported; the success-notification path called an unimported
  `napari_show_info`. Added `from napari.utils.notifications import show_info as
  napari_show_info`.
- **Known Issue resolved — "Mean and Additive multi-merge produce identical results."**
  `run_simple_multi_merge` min-max-normalized the result per-merge; since Mean =
  Additive / N, that normalization cancelled the constant and made the two modes
  byte-identical. Now the merged result is clipped to the input dtype's range and
  scaled by that fixed maximum, so Additive can saturate (its intent) while
  Mean/Max/Min keep distinct scales. Verified numerically (Additive max 0.766 vs Mean
  0.383 on the same inputs).
### Changed (consistency)
- Consolidated the 27 scattered inline `from pycat.ui.field_status import
  button_with_circle as _bwc` statements (added across the status-marker rollout) into a
  single top-level import per file, removing the awkward mid-line
  `form.addRow(prog); from ...` imports.

## [1.5.170] - 2026-07-03
### Fixed / Added
- **Status markers are now painted circles.** Replaced the CSS-styled dot (which a
  global stylesheet could flatten to a square) with a directly-painted antialiased
  circle in `StatusCircle.paintEvent`, so it stays round regardless of app styling.
- **Toolbox widgets no longer open duplicate instances.** `_add_widget_to_layout_or_dock`
  now checks whether a dock with the same name is already open; if so it shows an
  "Already open" dialog (OK) and does not add a second copy. Uses napari's
  public/fallback `dock_widgets` registry (keyed by name).
- **Status-marker style extended to the standalone workflows.** Run/action buttons in
  In-Vitro Fluorescence, In-Vitro Brightfield, Cellular Brightfield, FRAP, Video
  Particle Tracking, and Z-Stack now carry red (required) / yellow (optional) circle
  markers, with required-vs-optional taken from each workflow's checklist definition
  ([opt] tags). Their dropdowns already carried markers via `label_with_circle`.

### Needs-attention (flagged, intentionally NOT auto-marked)
- Some buttons were left unmarked because their required/optional status or step
  mapping was ambiguous; see the session notes. Notably: the Z-Stack per-section
  generic action button (`QPushButton(label)` built dynamically), and any
  Dynamics/Phase-diagram/Frame-Quality actions whose checklist step is optional but
  whose in-widget grouping spans multiple analyses. These should be reviewed and
  marked by hand.

## [1.5.169] - 2026-07-03
### Fixed (status markers — circular shape + placement by the dropdown)
- **Status markers render as circles again.** The marker stylesheet was being flattened
  to a square (a global QLabel style could override the corner radius); it now uses an
  explicit `QLabel { … border-radius }` rule with fixed min/max size so it stays round
  regardless of app-wide styles. Affects every status marker (dropdown rows and button
  squares).
- **Markers now sit next to the dropdown, not the label.** In `_layer_row` (used by
  Steps 6–9) the marker was on the label row with the dropdown on a separate row below.
  The label is now on its own line and the marker sits inline to the left of the
  dropdown it applies to, so it reads as belonging to the input. (Spatial Metrology's
  form rows already place the marker beside the dropdown via the form layout.)

## [1.5.168] - 2026-07-03
### Added (per-input status squares + optional-section reveals in Condensate)
- **New `button_with_circle` helper** (field_status): puts a red (required) or
  yellow (optional) status square left of an action button; if given the dropdowns it
  depends on, the square turns green once they all have a real selection.
- **Status squares on the required/optional inputs and actions across the Condensate
  workflow:**
  - Step 2 Measure Line(s): red button square. Step 3 Run Upscaling: yellow (optional).
    Step 4 Pre-process: red. Step 14 Save & Clear: red.
  - Step 6 Cell Segmentation: red square already on the image-layer dropdown; added a
    red square on Run Segmentation (green once a layer is chosen).
  - Step 7 Cell Analyzer: red square on Run Cell Analyzer (wired to the required mask +
    image dropdowns).
  - Step 8 Subcellular Segmentation: red squares already on both dropdowns; added a red
    square on Run Condensate Segmentation, and **Refinement Parameters is now hidden
    behind an off-by-default "Show refinement parameters" checkbox**.
  - Step 9 Condensate Analysis: both dropdowns now carry red status squares (converted
    to the status-row helper) and Run Condensate Analyzer has a red square.
- **Optional sections now have reveal checkboxes (off by default) with yellow squares:**
  - Spatial Metrology (Step 10): "Show spatial metrology (optional)" checkbox; when
    shown, its two dropdowns and the Run button carry yellow squares.
  - Condensate Biophysics: "Show condensate biophysics (optional)" checkbox; when
    shown, its per-tab fit/run buttons carry yellow squares.

## [1.5.167] - 2026-07-03
### Fixed (step enumeration reached the QGroupBox/button-titled builders)
- **Spatial Metrology (Step 10) and Save & Clear (Step 14) now show their step
  numbers.** The `_stage_step` mechanism only reached builders whose title came from
  `add_text_label(bold=True)`; Spatial Metrology renders its title as a `QGroupBox`
  title and Save & Clear had no title label at all, so both were silently dropping the
  staged prefix. Added a shared `_consume_step_label()` helper: Spatial Metrology now
  renders a matching 14px rich-text "Step 10 —" header above its box (with the box
  title repurposed as a short description), and Save & Clear gained a "Step 14 —"
  header. Every numbered checklist step in the Condensate workflow now shows its
  number.
- **Condensate Biophysics title enlarged to match.** It rendered at the small 10px
  `add_text_label` size; now a 14px header matching the other sections. It carries no
  step number by design (it is not a step in CONDENSATE_PIPELINE).
### Changed (Condensate Biophysics — time-aware tabs)
- **Time-dependent biophysics tabs are hidden for 2D input.** MSD/Diffusion, Kinetics,
  QC/Bleach, and Survival all need a (T,H,W) stack; they are now added/removed
  dynamically based on time-stack presence (re-checked on layer add/remove), leaving
  only the static Intensity/Csat tab for plain 2D data.

## [1.5.166] - 2026-07-03
### Changed (Advanced Analysis — optional-by-default + time-aware tabs)
- **Advanced Analysis (condensate Steps 11–13) is now hidden behind an off-by-default
  checkbox.** The block is fully optional, so it now shows only a "Show advanced
  analysis (optional)" checkbox by default; ticking it reveals the tabbed
  Morphological / Dynamic / Organizational analyses.
- **The Dynamic Spatial Phenotyping tab is hidden when the input has no time channel.**
  Dynamic analysis needs a (T,H,W) stack; the tab is now added/removed dynamically
  based on whether a time stack is loaded (re-checked when the block is shown and on
  layer add/remove), so 2D-only inputs don't see an inapplicable tab.
### Changed (condensate step numbering — match the checklist)
- Enumerated titles now reflect merged/bundled steps: Pre-process is labelled
  **"Steps 4–5 —"** (it produces both the pre-processed and background-removed layers,
  merged in 1.5.136), Advanced Analysis is **"Steps 11–13 —"** (Morphological, Dynamic,
  Organizational in one tabbed block), and Save & Clear now carries its **"Step 14 —"**
  label (previously unlabelled). This resolves the apparent gaps at steps 5, 12, 13, 14.

## [1.5.165] - 2026-07-03
### Changed (Step 1 mechanism + global font)
- **Step 1 now uses the same header mechanism as the other steps.** Previously Step 1's
  title was a `QGroupBox` title while Steps 2+ were rich-text labels, so they never
  quite matched in size/weight no matter how the stylesheets were tuned. Step 1 now
  renders its "Step 1 — Load Image / File" header as the same rich-text label
  (prefix at weight 800, title at 600, 14px) — matching the enumerated steps by
  construction. The groupbox-title position is repurposed as a grey italic one-line
  description ("Load an image to begin — completes automatically"), removing the
  duplicate step name. The Pixel-size gate's title reverts to plain styling (it's a
  conditional gate between steps, not a numbered step).
- **Global sans-serif UI font.** Set an application-wide `QFont("Arial")` with a
  SansSerif style hint (falls back to the platform sans-serif if Arial is absent) at a
  larger 10pt base, so default text reads at a clearer size instead of the small Qt
  default.

## [1.5.164] - 2026-07-03
### Changed (step-header consistency)
- **Step 1 and Pixel-size block titles now match the enumerated step headers.**
  Previously the Step 1 / Pixel-size groupbox titles were un-bold and a different size
  than the "Step 2 — …" section headers. Both now render at 14px bold (via a per-widget
  title stylesheet that also repeats the global title positioning so no clipping is
  reintroduced), and the enumerated step labels were bumped to 14px to match. Chose the
  surgical approach — only the two step-level groupboxes are restyled — so sub-section
  groupboxes inside a step (e.g. "Segmentation method", "Refinement Parameters") stay
  light and subordinate rather than competing with their parent step header.

## [1.5.163] - 2026-07-03
### Changed (step-title readability)
- **Enumerated section titles are now larger and the "Step N" prefix is emphasized.**
  The shared-builder section titles rendered at 10px, making the enumerated steps look
  subordinate to the Step 1 block. Stepped titles now render at 13px as rich text with
  the "Step N —" prefix at font-weight 800 (heavier than the title's 600), so the step
  number anchors the eye and the section reads as a primary header. Only titles that
  receive a staged step label are affected; all other `add_text_label` calls keep their
  existing size/weight.

## [1.5.162] - 2026-07-03
### Added (step enumeration — mechanism + Condensate reference)
- **Parameterized step enumeration for shared widget builders.** The built-in
  workflows (condensate, time-series, coloc, general, fibril) build their sections
  from shared `_add_*` builders that are reused across pipelines at *different* step
  numbers (e.g. Upscale is step 3 in Condensate but step 2 in Fibril), so a step
  number can't be hardcoded in the builder. Added a staging mechanism: a workflow
  calls `self._stage_step("Step N — ")` immediately before a shared builder, and
  `add_text_label` prepends that prefix to the builder's first bold title, then
  clears it. One-method change (`add_text_label`) rather than threading a parameter
  through ~30 builders; verified the prefix attaches to the first bold title only,
  clears correctly, and re-stages per call.
  - **Condensate workflow enumerated** as the reference, matching CONDENSATE_PIPELINE
    numbering (Step 2 Measure → Step 11 Advanced). The 7 standalone workflows
    (in-vitro fluor/bf, z-stack, FRAP, VPT, brightfield, temperature) already carry
    correct "Step N" titles in their own groupboxes and were left as-is.
  - Remaining built-in workflows (time-series, coloc, general, fibril) will be
    enumerated in a follow-up using the same mechanism.

## [1.5.161] - 2026-07-03
### Changed (workflow checklist — optional steps no longer gate progress)
- **Optional steps pass progress through to the next mandatory step and keep their
  own colour.** Refines the 1.5.160 colour logic: the "current" (red) marker is now
  the first incomplete *required* step, computed by skipping optional steps entirely
  — an untouched optional step in the middle of the list no longer blocks the red
  marker from advancing to the next mandatory step. When an optional step IS used it
  turns **blue** and stays blue; it never turns green and never participates in the
  required-step progression. Required steps become available once all *required*
  predecessors are done, regardless of intervening optional steps. The detail-label
  highlighting uses the same required-only "current" logic. Verified by simulation:
  with steps 1–2 done, the red marker sits on the next required step whether the
  intervening optional step is untouched (grey) or used (blue).

## [1.5.160] - 2026-07-03
### Changed (pixel-size gate — data-switch behavior + persist option)
- **Pixel-size gate now re-evaluates when the active data class switches.**
  `CentralManager.set_active_data_class` fires registered callbacks, and each gate
  registers its `refresh`. Switching to data that has no scale of its own re-shows the
  gate (previously it only re-checked on file load / manual scale entry, so a switch to
  unscaled data left the gate hidden).
- **New off-by-default "Keep this pixel size for the session" checkbox.** When checked
  and a valid pixel size has been entered, switching to other unscaled data
  automatically re-applies the remembered value instead of re-prompting. Off by
  default so each dataset's scale is set explicitly.

### Changed (workflow checklist — colour logic)
- **Checklist pills now follow the workflow boxes' red→yellow→green→blue logic.**
  Previously: grey (future) / orange (current) / green (done). Now: an available
  required step that still needs doing is **red** (was orange), the active optional
  step keeps the **yellow** highlight, a completed required step is **green**, and a
  completed **optional** step (tagged `[opt]`/`[optional]` in its label) turns **blue**.
  Steps whose predecessor isn't finished remain **grey** (locked), preserving the
  greyed-until-previous-step-complete gating. Optional-vs-required is detected from the
  existing `[opt]` label tags, so no pipeline definitions changed.

## [1.5.159] - 2026-07-03
### Fixed (UI consistency — checklist + pixel-size gate)
- **Object Colocalization workflow now activates its checklist.** It was the only one
  of the 13 pipelines not calling `workflow_checklist.activate('coloc')`; added.
- **Pixel-size gate restored/added to every imaging workflow that takes a pixel
  size.** The disappearing "Pixel size" box (shown only when the image metadata gave
  no scale, hidden once a valid µm/px scale is read or entered) was present on the
  built-in workflows (condensate, time-series, general, fibril) and temperature, but
  missing from the standalone workflows. Added `add_pixel_size_gate` — with the
  same auto-hide behavior keyed on `pixel_size_from_metadata` / `microns_per_pixel_sq`
  — to In-Vitro Fluorescence, In-Vitro Brightfield, Z-Stack (3D), FRAP, Video
  Particle Tracking, and Cellular Brightfield, plus `include_pixel_gate=True` on
  Object Colocalization. Every imaging workflow now shows the gate when needed and
  hides it once a scale exists, matching the Condensate reference.

### Note
- Step-title enumeration (making every widget-box title show a "Step N" that
  corresponds to the checklist) is the planned second pass and is NOT in this release.

## [1.5.158] - 2026-07-03
### Fixed (critical — regression from 1.5.157)
- **`NameError: QSizePolicy is not defined` in `add_step1_file_io`.** The Step 1
  block used `QSizePolicy` but `field_status.py` never imported it at the scope of
  that function (it was only imported locally inside a *different* function). This
  broke the Step 1 block everywhere: workflows that wrap the call in try/except
  silently showed NO Step 1 (explaining "there are no step 1s still"), and the
  Temperature-Dependent Microscopy workflow — which calls it directly — crashed its
  entire `setup_ui` with the NameError. Fixed by adding `QSizePolicy` to the import.
  Restores Step 1 across all workflows and un-breaks the temperature dock.

## [1.5.157] - 2026-07-03
### Fixed (UI — Step 1 consistency)
- **Hybrid Step 1 block, applied consistently.** `add_step1_file_io` now takes an
  optional `instruction_html`: it renders the red/green "image loaded" status marker
  on top (as before) and a workflow-specific load instruction beneath it — the layout
  requested (marker + status, then the Open/Save→Open Image Stack style text below).
- **Missing Step 1 added to three workflows.** The In-Vitro Fluorescence, In-Vitro
  Brightfield, and Z-Stack (3D) docks jumped straight to "Step 2" with no Step 1
  block. Each now opens with the hybrid Step 1 (status marker + a load instruction
  appropriate to the workflow — fluorescence/brightfield image, or Open Image Stack
  for the Z-stack).
- **Time-Series double Step 1 removed.** The time-series dock showed two competing
  "Step 1"s — a standalone instruction label AND the workflow header's file-I/O block.
  Merged into a single hybrid Step 1 at the top (status marker + the
  "Open/Save File(s) → Open Image Stack (T/Z / IMS)" instruction), with the reference-
  frame selector following as Step 2.

## [1.5.156] - 2026-07-03
### Fixed (UI layout)
- **Right-side clipping in the In-Vitro Fluorescence, In-Vitro Brightfield, and
  Z-Stack (3D) analysis docks.** These three docks never called `_relax_min_widths`
  / `_apply_scroll_guard` on their root widget, so long buttons/labels reported a
  wide minimum width and pushed controls (e.g. "Preprocess", "Segment Droplets",
  "Compute Field Summary") off the right edge when the dock was narrower than their
  hint. Added both calls (deferred import, matching the temperature_ui pattern) right
  before each dock is shown — content now shrinks to the dock width instead of
  clipping.
- **GroupBox title clipping (global sweep).** Raised the global `QGroupBox` title
  clearance (margin-top 16→22px, padding-top 8→10px, title `top: 2px`) so titles sit
  clear of the first content row everywhere. Also bumped two specific groupboxes whose
  own tight top content-margins let the title overlap the first control regardless of
  the global style: the "XY Region of Interest" box (time-series ROI; 8→20px) and the
  "Method" box (time-series Cellpose; 4→20px). Swept all UI files for titled
  groupboxes with top content-margins < 18px; these were the only two at risk beyond
  what the global style covers.

## [1.5.155] - 2026-07-03
### Changed (refactor — no behaviour change) — SPLIT COMPLETE
- **`ui_modules.py` split, step 6 (final): basic image-operation widgets.** Moved the
  4 pure image-transform widget builders (rescale intensity, invert, upscaling,
  rolling-ball + Gaussian background removal) into a new `ui/ui_imageops_mixin.py`
  (`_ImageOpsWidgetsMixin`), grouping them with the other image-processing widgets.
  The `__init__`-coupled base I/O (open, save/clear, measure line, pre-process,
  calibration correction, plotting) deliberately STAYS in `ToolboxFunctionsUI`, since
  those are the core lifecycle operations that belong next to `__init__` — organizing
  by concern rather than chasing line count. The import-resolution guard flagged a
  needed `QCheckBox` import up front (added before shipping); both guard and
  structural tests pass clean.

### Summary of the ui_modules.py refactor (steps 1–6)
- `ui_modules.py`: **4,555 → 2,835 lines**. `ToolboxFunctionsUI`: **~2,140 → 411
  lines** (now just `__init__` + 6 base-I/O/core methods). ~90 widget-builder methods
  relocated into six domain mixins, each inherited via the MRO with zero behaviour
  change: `_DiagnosticsWidgetsMixin`, `_FilteringWidgetsMixin`,
  `_SegmentationWidgetsMixin`, `_AnalysisWidgetsMixin`, `_LabelsMasksWidgetsMixin`,
  `_ImageOpsWidgetsMixin`. Guarded by `tests/test_ui_structure.py`,
  `tests/test_ui_smoke.py`, and `tests/test_mixin_imports.py`. The god-object that was
  the codebase's main merge-conflict/blast-radius surface is gone.

## [1.5.154] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 5: labels / masks / merge widgets.** Moved the 8
  label- and mask-tool widget builders (convert labels↔mask, measure region
  properties, update labels, label/measure binary mask, binary morphology, simple
  multi-merge, advanced two-layer merge) verbatim into a new `ui/ui_labels_mixin.py`
  (`_LabelsMasksWidgetsMixin`). `ui_modules.py` now 2,915 lines (from 4,555 — 36%
  smaller across five steps). The import-resolution guard (1.5.153) caught two
  potential runtime errors BEFORE shipping: a `guard_wheel` reference (fixed with the
  deferred-import pattern) and a missing `QRadioButton` import (added) — both would
  have been NameErrors when opening the affected widgets. Both guard and structural
  tests now pass clean; no circular imports (label_and_mask_tools / layer_tools don't
  import the UI layer). Class bases now include `_LabelsMasksWidgetsMixin`.

## [1.5.153] - 2026-07-03
### Added
- **`tests/test_mixin_imports.py` — automated guard for the mixin refactor.** A
  static (ast, no Qt) test that walks every method in each `ui_*_mixin.py` and
  confirms every loaded name resolves from module imports/defs, names bound anywhere
  in the method (all nested closures pooled, so legit `_run`/`_preview` handlers
  don't false-positive), sibling methods, builtins, or self. This catches the exact
  bug class that surfaced during the split — a moved method referencing a
  module-level name (`math`, `guard_wheel`, `QSizePolicy`) that wasn't carried into
  the mixin — at test time instead of when the widget is opened. Verified: passes on
  all three current mixins, and confirmed to FLAG the bug when `import math` is
  removed. Parametrized over `ui_*_mixin.py`, so every future extraction is guarded
  automatically.

### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 4: analysis widgets.** Moved the 7 feature/
  correlation/coloc analysis widget builders (cell analysis, puncta analysis,
  spatial autocorrelation, cross-correlation function, pixel-wise correlation,
  object-based coloc, Manders) verbatim into a new `ui/ui_analysis_mixin.py`
  (`_AnalysisWidgetsMixin`). `ui_modules.py` now 3,138 lines (from 4,555 — ~31%
  smaller across four steps). First extraction with the new import-resolution guard
  run BEFORE shipping: both it and the structural test pass clean. Class bases now
  `(BaseUIClass, _DiagnosticsWidgetsMixin, _FilteringWidgetsMixin,
  _SegmentationWidgetsMixin, _AnalysisWidgetsMixin)`.

## [1.5.152] - 2026-07-03
### Fixed
- **Segmentation AND filtering mixins: `NameError: name 'math' is not defined`.**
  `_add_run_local_thresholding` (segmentation) and the WBNS widget (filtering) use
  `math.ceil(...)` but the new mixin files didn't `import math`. Added to both. Found
  the filtering one via a systematic import-resolution scan of all mixins (which also
  confirmed `guard_wheel` and the others are now resolved), before it could surface
  as a runtime error when opening WBNS.
- **Diagnostics widgets: `ModuleNotFoundError: pycat.toolbox.pipeline_snr_tools`.**
  The Pipeline SNR Analysis / Pipeline Step Diagnostics widgets delegate to
  `pipeline_snr_tools.py` and `pipeline_diagnostic_tools.py`. These modules exist in
  the source tree but were evidently not present in the installed package on the
  target machine. Both are included directly in this patch to guarantee they land on
  reinstall. (Root cause is a packaging-inclusion gap for these tool modules, not a
  code error — shipping the files sidesteps it.)

## [1.5.151] - 2026-07-03
### Fixed
- **Segmentation mixin: `NameError: guard_wheel is not defined`.** The
  segmentation widgets moved into `ui_segmentation_mixin.py` in 1.5.150 call
  `guard_wheel` (the wheel-scroll guard helper), which lives in `ui_modules.py`.
  A top-level import would create a cycle (`ui_modules` imports the mixin), so
  `guard_wheel` is now imported deferred inside the two methods that use it
  (`_add_run_local_thresholding`). Audited all three mixins (diagnostics,
  filtering, segmentation) for other unresolved `ui_modules`-scope helpers — only
  segmentation used `guard_wheel`; the other two are clean.
- **Gaussian localization: `UnboundLocalError: QSizePolicy` (pre-existing latent
  bug, surfaced during refactor testing).** `_add_gaussian_localization` in
  `gaussian_localization_tools.py` used `QSizePolicy` at ~line 351 but only
  imported it inside a later `else` branch (~line 462). Because Python treats a
  name imported anywhere in a function as function-local, `QSizePolicy` was unbound
  at the earlier use. Fixed by importing `QSizePolicy` once at the top of the
  function and removing the redundant nested import. Unrelated to the mixin split;
  fixed while it was exposed.

## [1.5.150] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 3: segmentation widgets.** Moved the 5 segmentation
  widget builders (Felzenszwalb + merging, Cellpose, random-forest classifier, local
  thresholding, subcellular condensate segmentation) plus the
  `_run_stardist_segmentation` helper verbatim into a new
  `ui/ui_segmentation_mixin.py` (`_SegmentationWidgetsMixin`), now inherited by
  `ToolboxFunctionsUI`. `ui_modules.py` drops to 3,336 lines (from 4,555 at the start
  of the split — ~27% smaller across three steps). Verified: compiles; 0 dangling
  references; all methods + the stardist helper in the mixin, none left behind; class
  bases now `(BaseUIClass, _DiagnosticsWidgetsMixin, _FilteringWidgetsMixin,
  _SegmentationWidgetsMixin)`; no circular import (segmentation_tools imports only
  ui_utils, a leaf, and that dependency pre-existed the refactor). Steps 1-2
  confirmed working live.

## [1.5.149] - 2026-07-03
### Changed (refactor — no behaviour change)
- **`ui_modules.py` split, step 2: preprocessing/filtering widgets.** Moved the 12
  image preprocessing/filtering widget builders (enhanced RB-Gaussian bg removal,
  WBNS, wavelet noise subtraction, bilateral, CLAHE, FFT bandpass, im2bw, best
  slice, peak/edge enhancement, morphological Gaussian, DPR, Laplacian-of-Gaussian)
  verbatim into a new `ui/ui_filtering_mixin.py` (`_FilteringWidgetsMixin`), now
  inherited by `ToolboxFunctionsUI`. `ui_modules.py` drops 4,555 → 3,688 lines
  across the two refactor steps so far (~19%). Verified: both files compile; UI
  structural test reports 0 dangling references; all 12 methods in the mixin, none
  left behind; class bases `(BaseUIClass, _DiagnosticsWidgetsMixin,
  _FilteringWidgetsMixin)`; and no circular-import risk (the tool modules the mixin
  imports do not import back from the UI layer). Step-1 diagnostics mixin (1.5.148)
  confirmed working live — app launches and the moved widgets open.

## [1.5.148] - 2026-07-03
### Changed (refactor — no behaviour change)
- **Began splitting the 4,555-line `ui_modules.py` into domain mixins (step 1 of
  several).** The oversized `ToolboxFunctionsUI` class is the main
  merge-conflict/blast-radius surface in the codebase (see the code audit). First
  extraction: the 7 self-contained diagnostic/tuner widget builders
  (`_add_pipeline_snr_analysis`, `_add_pipeline_diagnostics`,
  `_add_foreground_suppression_tuner`, `_add_segmentation_speed_comparison`,
  `_add_chromatin_topology`, `_add_nucleolus_void_estimator`,
  `_add_display_diagnostics`) moved verbatim into a new
  `ui/ui_diagnostics_mixin.py` (`_DiagnosticsWidgetsMixin`), which
  `ToolboxFunctionsUI` now inherits. Methods resolve identically via the MRO, so
  behaviour is unchanged. `ui_modules.py` drops from 4,555 → 3,937 lines.
  - Started with the lowest-risk cluster (recent, self-contained widgets) to
    validate the mixin mechanism before touching load-bearing preprocessing/
    segmentation code. Verified: both files compile; the UI structural safety-net
    test reports 0 dangling references; all 7 methods live in the mixin class body;
    `ToolboxFunctionsUI` bases are `(BaseUIClass, _DiagnosticsWidgetsMixin)` with a
    correct MRO. Run `test_ui_smoke.py` on a full install to confirm live
    construction.

## [1.5.147] - 2026-07-03
### Added
- **Nucleolus / chromatin-void estimator** (Toolbox → Image Processing → Nucleolus /
  Void Estimator; core in `topology_tools.py`). Detects rounded DNA-excluding voids
  in a DAPI channel from its chromatin-density envelope — nucleoli and other
  DNA-excluding bodies appear as low-intensity voids the raw channel is often too
  noisy to threshold, but the smoothed envelope reveals them as coherent low basins.
  - **Two-tier classification:** each enclosed void is labeled `nucleolus-like`
    (round + compact + convex: circularity + solidity gates) or `irregular-void`.
    Deliberately framed as WEAK INFERENCE — a round solid void is only *likely* a
    nucleolus — so downstream analysis can weight confidence rather than treat it as
    a hard call.
  - **Optional condensate channel → partition inference:** with a condensate channel
    supplied, each void gets a partition call (`partitioning` / `excluded` /
    `ambiguous`) from the ratio of condensate signal inside the void vs. a
    surrounding ring. This gives a supporting guess for whether condensates enter or
    are excluded from nucleoli when no nucleolar marker channel is available.
  - **Live tuner UI** (like the foreground-suppression tuner): density-percentile,
    circularity/solidity gates, envelope sigma, and min-area exposed as sliders to
    calibrate against real DAPI, with results overlaid as napari label layers and
    per-cell void counts written to cell_df. Validated on real DAPI+GFP (correctly
    separates round nucleolus-like voids from irregular low-density regions and flags
    partitioning ratio 3.06 vs excluded 0.49).
  - Detection is envelope-first (not raw-threshold), which is what makes it work on
    dim/low-contrast DAPI where a hard threshold merges voids into chromatin.

## [1.5.146] - 2026-07-03
### Added
- **UI refactor safety net** — two new test layers to protect the upcoming
  `ui_modules.py` cleanup/split against the recurring "a change silently broke a
  menu/widget" failure mode:
  - `tests/test_ui_structure.py` (static, `ast`-based, no Qt/napari — runs anywhere):
    asserts the module parses, every `toolbox_functions_ui._add_X` referenced in a
    menu/workflow registration resolves to a method defined somewhere (as `def`,
    lambda-bound, or in a sibling ui/tool module), each workflow layout attribute is
    still assigned, and the core UI classes still exist. Validated to PASS on current
    code (0 dangling references) and to FIRE on a simulated method rename — so it
    catches a moved/renamed/dropped widget method at test time instead of at
    click time.
  - `tests/test_ui_smoke.py` (headless Qt via `QT_QPA_PLATFORM=offscreen`): actually
    constructs CentralManager / toolbox UI / MenuManager to catch mixin-composition /
    MRO errors, missing-attribute-at-construction, and import cycles a static parse
    can't. Auto-skips where PyQt5/napari aren't installed.
  - `tests/README.md` documents the recommended before/after-each-step workflow for
    the refactor.

## [1.5.145] - 2026-07-03
### Fixed
- **Maximize-on-start made durable (event-driven, no longer a timing race).** The
  maximize-on-startup has regressed repeatedly across releases: it was done either
  synchronously before the event loop (silently discarded — the startup relayout
  re-shows the window at default size) or on a fixed timer delay (120 ms → 200/500 ms
  → …), and every fixed delay was eventually out-grown as later UI changes lengthened
  the startup relayout, so the maximize fired and was then clobbered by a late
  relayout. This session it had regressed back to the pre-fix synchronous-before-loop
  form. Replaced the whole approach: maximize is asserted after the loop starts, then
  a lightweight 100 ms poll re-asserts it for a ~2.5 s settling window and stops
  itself once stable. This catches a relayout that un-maximizes the window regardless
  of *when* it happens during startup, so future UI growth can't re-break it by
  shifting the timing. (Verified by simulation: relayouts that un-maximize mid-startup
  are re-maximized within one poll tick, and the watcher self-stops.)

## [1.5.144] - 2026-07-03
### Added
- **Analysis regression-test framework** (addresses the code-audit "narrow test
  coverage" finding). Previously the test suite covered infrastructure (imports,
  app boot, file I/O, data manager) plus three low-level utilities, but none of the
  scientific analysis. New tests cover the core analyses on deterministic synthetic
  data (`tests/fixtures_synthetic.py`):
  - `test_coloc_metrics` — Pearson known-answers (identical→1.0, anti→−1.0,
    independent→~0, symmetry invariant).
  - `test_frap_fitting` — recovery-model endpoints/half-time (exact), and fit
    recovering a known mobile fraction & half-time from a noise-free curve.
  - `test_partition` — K = dense/dilute known-answer, unity on uniform input,
    background-subtraction behavior, non-negativity invariant.
  - `test_segmentation_refine` — locks in the fast-vs-original refinement
    bit-for-bit equivalence (making permanent the manual `np.array_equal` check from
    the 1.5.134 optimization), plus within-cell and subset invariants.
  - **Empirical/golden values are intentionally left as `TODO(maintainer)`
    placeholders** (`EMPIRICAL_PARTIAL_OVERLAP_PEARSON`, `NOISY_FIT_*`,
    `GOLDEN_SEGMENTATION_OBJECT_COUNT`) — those tests skip until the maintainer fills
    the validated reference value, so the framework is ready but the "correct answer"
    for realistic data is decided later. See `tests/README.md`.
  - Framework validated: all known-answer assertions and fixtures were checked
    against the real numeric libraries (identical→+1.0000, anti→−1.0000,
    independent→−0.003; FRAP I(0)=0.2, I(τ½)=0.55; partition K=5.000).

## [1.5.143] - 2026-07-03
### Added
- **PyCAT logging layer** (`utils/logging_utils.py`, `get_logger`). Gives PyCAT
  proper logging — level control, optional file capture, the ability to silence or
  raise verbosity — WITHOUT changing default output. By default it writes plain-format
  messages to stdout, so existing console output looks the same. `PYCAT_DEBUG=1`
  raises the level to DEBUG (same env var that drives the swallowed-exception
  `debug_log` helper), and `PYCAT_LOG_FILE=/path` additionally writes a timestamped,
  level-tagged log — so a user reporting a bug can attach a full run log instead of a
  scrollback screenshot.

### Changed
- **Adopted the logger in `run_pycat.py`** as the reference migration: Cellpose
  model-prewarm progress → `log.info`, setup/icon errors → `log.warning`. This is a
  deliberately partial adoption. The audit's 224 `print()` calls are mostly NOT stray
  debugging — 91 are the intended batch-run progress narrative (`[PyCAT Batch] …`),
  which is correct as visible console output and left as-is. Remaining modules can
  migrate incrementally (error/warning → `log.warning`, info → `log.info`, verbose →
  `log.debug`); batch progress can stay `print` or move to `log.info` with identical
  visible output. No default behavior changes.

## [1.5.142] - 2026-07-03
### Added
- **`debug_log` helper for surfacing swallowed exceptions** (`general_utils.py`).
  The codebase has many `except Exception: pass` guards; most are legitimate (they
  protect optional UI niceties), but when one fires in a data path the failure is
  invisible and undiagnosable. `debug_log(context, exc)` prints the context and full
  traceback ONLY when the `PYCAT_DEBUG` env var is set (following the existing
  `PYCAT_REFINE_DEBUG` / `PYCAT_FORCE_CPU` convention), and is a silent no-op
  otherwise. It does not change control flow — the caller still passes/continues/
  falls back — it only makes the swallow observable for debugging.

### Changed
- **Wired `debug_log` into the highest-value data-integrity swallow sites** (the ones
  where a silent failure corrupts results rather than skipping a cosmetic UI step):
  - `file_io`: physical-pixel-size read (silent failure falls back to 1.0 µm/px,
    which would silently corrupt every downstream micron measurement).
  - `file_io`: AICSImage→tifffile fallback (silent failure loses scene/T/Z metadata).
  - `frap_io`: frame-interval read (affects recovery timing).
  - `frap_io`: bleach center_point_um read (affects ROI placement).
  Run with `PYCAT_DEBUG=1` to see these if a load/measurement looks wrong. The
  remaining cosmetic-guard excepts are unchanged and can adopt `debug_log`
  incrementally.

## [1.5.141] - 2026-07-03
### Documentation
- **Authorship updated.** Gable Wadsworth added alongside Christian Neureuter in the
  README Citation and Acknowledgments sections and listed first; `pyproject.toml`
  authors reordered to match (Wadsworth, then Neureuter). BibTeX key/author field
  updated accordingly.
- **Documented the NumPy < 2 and Zarr < 3 version constraints** in the README (new
  "Dependency Version Constraints" subsection under the Cellpose section). Explains
  that `numpy<2.0` is a downstream consequence of the deliberate `cellpose<4` / numba
  choice (PyCAT's own code is NumPy-2.0-clean and runs at full speed under NumPy 2.x),
  and that `zarr<3.0` is required by the time-series cache's use of the removed
  `DirectoryStore`; migrating to Zarr 3 `LocalStore` would be small and have no
  performance impact (identical one-plane-per-chunk local disk I/O), but is deferred
  because it yields no benefit while Cellpose 3 holds the environment at NumPy 1.x.

## [1.5.140] - 2026-07-03
### Changed
- **Annotated MP4 export rebuilt in pixel space (no more matplotlib figure /
  white padding).** `render_annotated_mp4` (temperature time-lapse export)
  previously rendered each frame through a matplotlib figure, which reserved
  figure margins (the white border), put the temperature/time in a separate title
  band, drew a plot-style scale bar, and held every frame in memory before
  encoding. It now composites annotations directly onto the RGB frame with PIL and
  streams frames one at a time:
  - **Edge-to-edge image** — no white figure padding or title band.
  - **Temperature/time**: black text on a white box, squared into the TOP-LEFT
    corner (equal inset from the top and left edges) for guaranteed legibility.
  - **Scale bar**: solid white bar with the "N µm" label centred above it, squared
    into the BOTTOM-RIGHT corner (bar's bottom-right corner equidistant from the
    right and bottom edges); label carries a thin dark outline so it reads on light
    regions.
  - **Streamed encoding** (one frame in memory at a time) instead of stacking all
    frames, so long time-lapses no longer balloon memory.
  - New optional `colormap` parameter (default `'gray'`, matching the previous
    look); both existing callers are unchanged and remain backward-compatible.

## [1.5.139] - 2026-07-03
### Added
- **Show/hide-all-layers toggle** in the PyCAT toolbar (next to Batch Run / Save
  Config). One click flips every layer's visibility together, so managing a large
  stack no longer requires clicking each layer's eye individually (the workaround of
  dragging layers to reorder for top priority is no longer needed for visibility
  management). The button reads live state each click — if any layer is visible it
  hides all, otherwise it shows all — so it self-corrects even if individual layer
  eyes are toggled in between. The icon/tooltip reflect the next click's action.
  (Note: this is a deliberate all-together toggle; "show all" turns every layer on
  and does not restore a prior per-layer hidden state. It is separate from napari's
  layer-list dock collapse control — the eye in the layer widget's title bar — which
  hides the whole layer list.)

## [1.5.138] - 2026-07-03
### Added
- **Display Diagnostics tool** (Toolbox → Image Processing → Display Diagnostics) to
  investigate "layer controls (contrast/gamma) appear to do nothing." Reports, for
  the active layer: napari version, layer type/dtype/shape, data min/max, current
  `contrast_limits` and `contrast_limits_range`, colormap, RGB flag, visibility, and
  whether the selected layer is actually the top visible layer (a common cause — a
  layer drawn opaque on top hides changes to the layer you're adjusting). Includes a
  live probe that nudges `contrast_limits` and confirms whether the change registers
  on the layer object, distinguishing a data/RGB/version issue from a rendering
  (GPU/OpenGL) or wrong-layer issue.

### Fixed
- **`refresh_viewer_with_new_data` crash for image layers.** The Image branch called
  `add_image_with_default_colormap(viewer, updated_data, ...)` with `data` and
  `viewer` swapped (the signature is `(data, viewer, ...)`), which raised whenever an
  image layer was refreshed in place. Arguments corrected.

### Notes
- Audit of the contrast/layer-control report: PyCAT does not pin `contrast_limits`,
  monkeypatch napari's layer controls, or block slider events (the wheel guard only
  consumes `Wheel` events on PyCAT dock controls, and the drop filter only handles
  drag/drop). `napari` is currently unpinned in `pyproject.toml`, so display-control
  behaviour can vary with the installed napari version. The Display Diagnostics tool
  above is intended to localise the cause on a specific machine/session.

## [1.5.137] - 2026-07-03
### Added
- **Chromatin Topology Map** (new `topology_tools.py`; Toolbox → Image Processing →
  Chromatin Topology Map). Exposes the rolling-ball *background* envelope — normally
  subtracted and discarded — as a structural signal: on a nuclear channel it
  suppresses fine puncta and traces the large-scale chromatin/nucleoplasm topology
  (the connected-network appearance observed on DAPI). This is the shared foundation
  for planned over-segmentation and wetting/connectedness utilities.
  - **Two envelope modes:** `rolling_ball` (morphological envelope, finer chromatin
    texture) and `gaussian` (low-pass at ball_radius scale, smoother percolation
    read). Selectable per run.
  - **Two output layers:** raw envelope ("Chromatin Topology [name]", brightness
    comparable across cells) and mask-normalised ("Chromatin Topology (norm) [name]",
    shape comparable across cells).
  - **Per-cell metrics** written to cell_df when a Labeled Cell Mask is present:
    `topo_cov` (envelope coefficient of variation — how structured), `topo_roughness`
    (std of normalised envelope), `topo_n_basins` (distinct intensity maxima at the
    structural scale — seed for the over-segmentation check), `topo_n_components`
    and `topo_largest_frac` (connectivity of the above-percentile network — seed for
    wetting/percolation, →1 = percolating, →0 = fragmented), and `topo_high_area_frac`.
  - Validated on real DAPI: rolling-ball and gaussian modes give distinct, sensible
    readings (rolling-ball CoV 1.01 / 3 basins / 6 components vs gaussian CoV 0.23 /
    2 basins / 2 components), reproducing the connected-chromatin-network appearance.

## [1.5.136] - 2026-07-03
### Changed
- **Pre-processing and background removal merged into one button.** The separate
  "Pre-process Image" and "Remove Background" buttons are now a single
  "Pre-process Image" button that produces BOTH the "Pre-Processed [name]" and
  "Enhanced Background Removed Pre-Processed [name]" layers in one click. Applied
  across all four workflows that had the two-step structure (Condensate, Object
  Colocalization, General, Fibril). The standalone "Background Removal w/ Edge
  Enhancement" tool remains available in Toolbox → Image Processing for independent
  use. Both the `preprocessing` and `background_removal` batch steps are recorded,
  so batch replay and the workflow checklist (which tracks a `background_removal`
  step) continue to work unchanged.
- **Batch `background_removal` now matches the interactive result.** Batch replay
  previously always ran the destructive `rb_gaussian_bg_removal_with_edge_enhancement`
  on the already-suppressed preprocessed image, diverging from the GUI runner which
  (since 1.5.128) detects already-preprocessed input and applies the non-destructive
  `soft_foreground_suppression` instead. `replay_background_removal` now uses the
  same detection and honours the recorded suppression params, so GUI and batch
  produce identical 'Enhanced Background Removed' output on both the segmentation and
  fluorescence channels.

## [1.5.135] - 2026-07-03
### Fixed
- **Workflow checklist was crashing on activation.** `WorkflowChecklistManager.activate`
  ended with `self._widget.mark_step(step_key)` — but `step_key` is not defined in
  `activate` (its parameter is `pipeline_name`), so every switch into an analysis
  mode raised `NameError` and the checklist never appeared/updated. Additionally the
  manager had **no** `on_step_recorded` method, yet `ui_modules` calls
  `workflow_checklist.on_step_recorded(...)` on the manager to replay recorded steps
  — an `AttributeError`. Removed the stray line and added the missing
  `on_step_recorded` delegator. The step keys in `PIPELINE_DEFS` already match the
  recorded batch-step names, so pills now check off correctly as steps complete.
- **Dock content pushed off-screen (buttons/controls clipped on the right).**
  Horizontal scrolling is disabled on the analysis/toolbox docks (by design, so
  content fits the width), but buttons, combo boxes, line edits and long labels
  reported wide minimum-size hints that forced rows wider than the dock and clipped
  the right edge. Added `_relax_min_widths`, which recursively sets a 0 minimum
  width and a shrinkable (Preferred) horizontal size policy on those controls and
  enables word-wrap on labels, so content compresses to the dock width instead of
  overflowing. Applied to the generic separate-widget dock path (covers every
  toolbox tool) and to all six main analysis docks (Condensate, Time-Series,
  Object Coloc, Pixel-Wise, General, Fibril). The generic dock path also now sets
  `main_widget.setMinimumWidth(0)` to match the main docks.

## [1.5.134] - 2026-07-03
### Optimised
- **Per-object refinement loop vectorised via windowing (~12× faster, identical
  output).** Profiling showed the refinement loop — not cropping — dominates
  segmentation runtime: it ran ~5 full-array `binary_erosion`/`binary_dilation`
  calls per object on the whole cell crop, for ~100 objects × 2 passes × N cells.
  New `puncta_refinement_filtering_func_fast` performs each object's morphology and
  pixel-population statistics inside that object's own padded bounding-box
  sub-window (bbox + 4 px). Since morphology and indexing are local operations, the
  result is bit-for-bit identical to the original while touching a ~15×15 patch
  instead of a 700×700 array per object. Measured: full `puncta_refinement_func`
  (both passes + watershed) 5641 ms → 461 ms (12.2×) on a 120-object scene; the
  filter alone ~13×. Enabled by default (`_PYCAT_REFINE_FAST = True`); the original
  is retained and selectable for verification.

### Added
- **Segmentation Speed Comparison widget** (Toolbox → Image Processing). Runs
  condensate segmentation twice — original vs fast refinement — on the selected
  pre-processed and original layers, times each, checks the refined masks are
  identical, and reports timing, speedup, and equivalence. Adds the fast-path
  result layers, plus a "Fast vs Slow DIFF" layer if any pixel differs. Backed by a
  new viewer-free `_segment_core` so the interactive runner and the comparison share
  one code path and cannot drift.

## [1.5.133] - 2026-07-03
### Optimised
- **Condensate segmentation now crops to each cell's bounding box (major speedup
  for multi-cell images).** Previously every cell ran the full pipeline
  (Felzenszwalb + CLAHE + background removal + thresholding + refinement) on the
  *whole frame* masked to that one cell, so an N-cell image did ~N× redundant
  whole-frame work. `segment_subcellular_objects` now defaults to `crop_to_cell=True`
  with a **6·ball_radius** context margin. Estimated ~6× faster on a typical 5-cell
  image (more with 10–15 cells); the exact gain depends on how much of the cost is
  the per-object refinement loop (which scales with object count, not area, and is
  unaffected by cropping).
- **Output verified numerically identical inside the cell.** On real GFP data the
  padded crop matches whole-frame processing to machine precision within the cell
  (max pixel diff 0.0000, correlation 1.00000 at pad=6·ball_radius), versus
  measurable edge error at the old 1·ball_radius margin — which is why cropping was
  previously left off by default. The larger margin removes that concern. The crop
  is guaranteed to fully contain each cell (only distant background is trimmed), so
  all cell-relative statistics (cell area, background mean/std, kurtosis/SNR gates)
  are preserved.

## [1.5.132] - 2026-07-03
### Fixed
- **Condensate segmentation object count was always reported as "1".**
  `total_refined_puncta_mask` is a boolean OR-accumulation across cells, so
  `int(total_refined_puncta_mask.max())` returned 1 whenever any object existed —
  it reported "at least one pixel set", not the number of objects. The count now
  uses connected-component labeling (`sk.measure.label(...).max()`), and the
  "Total Puncta Mask" / "Total Refined Puncta Mask" layers are added as labeled
  arrays (each object a unique id) instead of a single-label binary cast.
  Downstream analysis is unaffected (the analyzer re-binarizes the mask).

### Added
- **Refinement rejection diagnostic.** When enabled (set
  `segmentation_tools._PYCAT_REFINE_DEBUG = True` or export `PYCAT_REFINE_DEBUG=1`),
  `puncta_refinement_filtering_func` prints why each object is dropped — object
  area, mean intensity, and the specific condition(s) that fired (local_intensity,
  cell_intensity, kurtosis, area, ellipticity, gradient, local_snr, global_snr).
  This turns "why did that bright condensate get dropped?" from guesswork into a
  logged answer for a specific image. Off by default (no output).

## [1.5.131] - 2026-07-03
### Fixed
- **Upscaling no longer produces two identical layers from one image.** Root cause:
  in `run_upscaling_func`, the scaled `add_image_with_default_colormap` call and the
  `napari_show_info` notification were inside the same `try` block, with an `except`
  that re-added the layer. If the notification raised (e.g. `_src_scale[-1]`
  formatting when `layer.scale` had an unexpected length), the layer — already
  added — was added a *second* time by the except handler, yielding two identical
  "Upscaled ..." layers. The add is now performed exactly once via mutually
  exclusive scaled/unscaled branches, and the notification is isolated in its own
  best-effort `try/except` that cannot trigger an add. Added two further guards:
  selected layers are de-duplicated by identity before the loop, and any layer whose
  "Upscaled {name}" output already exists is skipped with a warning.

## [1.5.130] - 2026-07-03
### Fixed
- **Bright condensates no longer dropped or partially segmented due to a hollow-ring
  area miscount.** Root cause: local (Niblack/Sauvola) thresholding hollows out
  large bright flat cores into rings (the flat centre isn't brighter than its local
  window, so only the rising edge thresholds). `opencv_contour_func` then measured
  each object with `cv2.contourArea` — the area *enclosed by the outer polygon*, not
  the lit pixel count — so a hollow ring reported the whole enclosed disc. That
  inflated area tripped `max_area` (`cell_area/4`), rejecting or partially filling
  genuine bright condensates even when their true pixel footprint was far below the
  cap. Smaller dim puncta stayed solid and passed, which is why only the bright
  objects were affected. Two coordinated fixes:
  - `opencv_contour_func` now gates on **filled pixel count** (rasterise the filled
    contour and count pixels) instead of `cv2.contourArea`, making the area test
    consistent with pixel-based area measurement used elsewhere.
  - `fz_segmentation_and_binarization` now applies `ndi.binary_fill_holes` after the
    contour fill, guaranteeing solid objects so bright cores are not left partially
    segmented.
  Verified on synthetic ring/C-shape objects and realistic condensate sizes: a
  bright object at ~2.5% of cell area that the old `contourArea` path over-reported
  is now retained and filled solid; genuinely oversized objects (>25% of cell) are
  still capped as intended.

### Note
- Fully-enclosed hollow rings fill solid; a ring with a real gap in its boundary
  (open C-shape) is retained as-is rather than force-closed, to avoid merging
  adjacent distinct puncta. Raise this if open-boundary partials persist on real
  data — a small morphological closing before the fill can bridge them.

## [1.5.129] - 2026-07-03
### Fixed
- **Foreground suppression no longer erodes condensate borders.** The attenuation
  dimmed the intensity falloff at object edges, so segmentation thresholding
  clipped borders and produced condensates slightly smaller than desired. Added a
  border-protection step to `_realness_weight`: the high-confidence keep region
  (surviving cores, post size-gate) is dilated by `border_grow` pixels and the
  weight is lifted back toward full within the grown band, but only where genuine
  signal exists. Isolated noise, having no high-confidence core, is unaffected, so
  borders are recovered without reintroducing noise.

### Added
- **`border_grow` parameter** (default 2 px) in `FOREGROUND_SUPPRESSION_DEFAULTS`,
  `_realness_weight`, and `soft_foreground_suppression`, exposed as a fifth slider
  in both the Pre-process "Adjust foreground suppression" panel and the Foreground
  Suppression Tuner dock, and recorded/replayed by the batch processor. 0 disables
  border protection (pre-1.5.129 behaviour); higher values restore thicker borders.
  Validated on real GFP data: object footprint at a fixed threshold grows from
  3441 px (border_grow=0) to 4807 px (=2) to 5493 px (=4) while peaks and the noise
  floor are unchanged.

## [1.5.128] - 2026-07-03
### Changed
- **Foreground suppression is now part of core preprocessing.** `pre_process_image`
  applies `soft_foreground_suppression` as a final step (after CLAHE) by default,
  so every consumer — the Pre-process button, batch replay, and the internal
  preprocessing inside subcellular segmentation — receives the corrected output.
  This restores usable preprocessing for condensate detection: the prior CLAHE
  output left the diffuse noise tier at full strength. Two new optional args,
  `suppress_foreground=True` and `suppression_params=None`, allow opting out or
  overriding; existing callers are unaffected (defaults preserve the new behaviour).

### Added
- **Composite 'realness weight' suppression** (`_realness_weight` +
  rewritten `soft_foreground_suppression`). Replaces the single intensity
  smoothstep with a product of four cues so real puncta are kept and noise
  fluctuations eliminated: blob-shape (separable-LoG response), local-contrast
  (value above a larger-σ surround), intensity floor, and a size gate that knocks
  down sub-`min_area` specks. Parameters: `strength`, `log_p`, `con_p`, `min_area`.
- **Tuned defaults** in `FOREGROUND_SUPPRESSION_DEFAULTS`
  (`strength=0.8, log_p=10, con_p=4, min_area=3`), chosen interactively on real GFP
  condensate data against hand-annotated ground truth (strongly-visible objects
  kept, acceptable objects lightly attenuated, noise fluctuations removed).
- **"Adjust foreground suppression" checkbox** on the Pre-process Image widget.
  Unchecked by default (button behaves as before, using the tuned defaults);
  checking it reveals four editable sliders that override the defaults. Overrides
  are stored in the data repository and recorded in the `preprocessing` batch step
  only when changed, keeping unmodified configs clean and forward-compatible.
- **Foreground Suppression Tuner dock** (Toolbox → Image Processing). Live sliders
  over the four parameters with an in-place "Suppression Preview" layer, plus
  "Apply as session default" and "Reset to tuned defaults" buttons. Mirrors the
  Pipeline Diagnostics dock pattern.
- **Batch replay** (`replay_preprocessing`) now honours recorded
  `suppress_foreground` and `foreground_suppression_params`, applying them to both
  the segmentation and fluorescence channels. Legacy configs default to suppression
  ON with tuned defaults.

### Fixed
- The Remove Background button (`run_enhanced_rb_gaussian_bg_removal`) now uses the
  session suppression params instead of a hardcoded `strength=0.6`. Since core
  preprocessing already applies suppression as of this release, a second pass on a
  freshly-preprocessed layer is near-idempotent rather than double-destructive.

## [1.5.127] - 2026-07-03
### Fixed
- **Remove Background button no longer destroys the nucleoplasm baseline on
  preprocessed images.** The "Remove Background" button
  (`run_enhanced_rb_gaussian_bg_removal`) called
  `rb_gaussian_bg_removal_with_edge_enhancement` directly, applying the full
  destructive rolling-ball + Gaussian subtraction chain. On a preprocessed
  condensate image (`/max → separable LoG → WBNS → morph → Gaussian → CLAHE`)
  that subtraction collapses the IQR noise floor to zero: it removes the
  nucleoplasm baseline that condensates sit on top of, leaving only the
  brightest peaks and erasing the diffuse signal dim candidate condensates live
  in. The 1.5.126 bypass only guarded the internal call inside
  `segment_subcellular_objects`; the standalone button hit the bad path
  directly. `run_enhanced_rb_gaussian_bg_removal` now detects whether the active
  layer is already preprocessed (median of non-zero pixels < 0.05 after
  normalisation — the same heuristic as the `segment_subcellular_objects`
  bypass) and, in that case, applies a new non-destructive
  `soft_foreground_suppression` refinement instead.

### Added
- **`soft_foreground_suppression(image, ball_radius, strength=0.6)`** in
  `image_processing_tools.py`. Softly attenuates the dim, diffuse foreground
  tier (dim candidate condensates and low-contrast texture) via a smoothstep
  attenuation weight computed over a structure-sized Gaussian intensity
  reference (σ = ball_radius × 0.27). The weight is ~0 below the 40th-percentile
  intensity anchor and ~1 above the 90th-percentile anchor, and is blended in by
  `strength` so the baseline is preserved rather than zeroed. Result: dim
  candidates are dimmed but remain visible, the nucleoplasm baseline (non-zero
  IQR floor) is preserved, and bright condensate peaks are left intact.
  Verified numerically: peak intensity retained exactly, IQR floor preserved
  (non-zero), overall image dimmed. Output keeps the
  `Enhanced Background Removed [name]` layer name so downstream widgets and batch
  steps that reference it continue to work. A genuinely raw (not-yet-preprocessed)
  image still receives the original enhancement path.

## [1.5.126] - 2026-07-03
### Optimised
- **LoG speedup: separable float32 implementation (1.54× faster, quality identical).**
  The blob-detection step in `pre_process_image` now uses a separable LoG:
  Gaussian(σ) in float32 followed by a discrete axis-wise Laplacian, instead
  of `ndi.gaussian_laplace` on a float64 cast. Validated on 2048×2048 images
  at ball_radius=15 and ball_radius=50:

  | Method | Speedup | SNR | Pixel corr |
  |---|---|---|---|
  | gaussian_laplace f64 (old reference) | 1.00× | 430 | 1.000 |
  | gaussian_laplace f32 | 1.15× | 430 | 1.000 |
  | **separable LoG f32 (adopted)** | **1.54×** | **429** | **0.9999** |
  | DoG fixed σ=2.0,3.2 (old speedup) | 1.37× | 224 | 0.904 |
  | DoG scaled br×0.15,×0.25 | 1.43× | 268 | 0.948 |

  The old DoG speedups are confirmed harmful: the fixed σ=2.0/3.2 DoG drops SNR
  by 48% at ball_radius=15 and would be far worse at ball_radius=50 (σ mismatch
  grows with radius). The scaled DoG (br×0.15,×0.25) drops SNR by 38%.
  Both are discarded. Separable LoG f32 is 1.54× faster with corr=0.9999.

### Fixed
- **segment_subcellular_objects: internal BG removal bypassed when input is
  already LoG-preprocessed.** `segment_subcellular_objects` was calling
  `rb_gaussian_bg_removal_with_edge_enhancement` on the pre_process_image
  output, which destroyed the SNR gains from LoG (collapses IQR to 0).
  Now detects preprocessed input (median of non-zero pixels < 0.05 after
  normalisation) and applies only a light CLAHE pass instead, preserving
  the ×360 within-nucleus SNR from preprocessing.
- **Preprocessing + BG removal chain optimisation results (real GFP data):**
  The correct chain for condensate puncta detection is confirmed as:
  `/max → separable LoG(σ=br×0.27) → WBNS → morph_clean → Gauss(σ=1) → CLAHE`
  No background subtraction step improves on this. Full RB or Gaussian
  subtraction before or after LoG all collapse the IQR noise floor to 0.
  Light partial subtraction (RB f=0.5 at large radius) gives +1-7% marginal
  gain but adds tunable parameters with failure modes on flat-background images.
## [1.5.125] - 2026-07-03
### Fixed
- **Multi-Otsu cell segmentation fallback: wrong threshold + no watershed.**
  Three issues corrected across `batch_roi_tools.py`, `ts_cellpose_tools.py`,
  and `batch_step_registry.py`:

  1. **Wrong threshold class.** `ts_cellpose_tools` used `thresholds[-1]` (the
     highest class — condensate/bright-puncta level) as the cell body boundary.
     The correct threshold is `thresholds[0]` (the lowest class), which captures
     the full cell body including cytoplasm and nucleus. The reasoning: GFP and
     other fluorophores are weakly persistent throughout the cytoplasm, so the
     three-class histogram is: background | cytoplasm+nucleus | bright condensates.
     The lowest threshold separates cell from not-cell. This is the same criterion
     that makes the fallback valid on fluorescence channels but not on brightfield
     (which has no such monotone intensity hierarchy).

  2. **Simple connected-components instead of watershed.** `sk.measure.label` on
     the binary mask merges touching cells into one label. Replaced with distance
     transform + watershed seeded from local maxima spaced by `cell_diameter // 2`,
     which separates touching cells at their midpoints — matching Cellpose output.

  3. **Fixed minimum object size and seed spacing.** Both were hardcoded (64px²,
     20px). Now derived from `cell_diameter`: min object size = `(cell_diameter/2)²`,
     seed spacing = `max(10, cell_diameter // 2)`. The `cell_diameter` parameter
     is now passed through from the data repository in all three call sites.

  4. **Pre-smoothing before thresholding.** A Gaussian smooth (σ = cell_diameter×0.1)
     is applied before `threshold_multiotsu` so condensate puncta above `t[0]`
     outside the cell body don't fragment the foreground mask.
## [1.5.124] - 2026-07-03
### Fixed (critical — preprocessing SNR regression)
- **Replaced white-top-hat + fixed-sigma DoG pipeline with scaled LoG.**
  Quantitative SNR measurement on real condensate data (GFP channel, DAPI
  channel, within-nucleus metric) showed:

  | Step | Within-nucleus SNR |
  |---|---|
  | raw /max | 8 |
  | old pipeline (RB sub 0.75 → DoG fixed σ) | ~20 |
  | new pipeline (LoG, σ = ball_radius × 0.27) | **2917** |

  Root cause of the regression: the white-top-hat × DoG multiplicative step
  suppressed the nucleoplasm baseline that condensate puncta sit ON TOP of.
  Combined with rolling-ball subtraction (which hard-clips background to 0),
  this made nucleoplasm-level condensates indistinguishable from the noise
  floor. The LoG applied directly to the /max-normalised image avoids both
  problems: it enhances blob-like structures at the condensate scale without
  removing the local baseline they sit on.
- **LoG sigma now scales with ball_radius** (σ = ball_radius × 0.27).
  At ball_radius=15 → σ≈4 (optimal for this dataset); at ball_radius=50
  → σ≈14 (appropriate for upscaled images). This restores the radius-
  scaling that v1.0.0's `apply_laplace_of_gauss_enhancement(σ=3)` had
  implicitly via its call-site, and extends it correctly.
- **Pipeline step diagnostics widget updated** to reflect the new pipeline.
## [1.5.123] - 2026-07-03
### Added
- **Pipeline SNR Analysis widget** (Toolbox → Image Processing → Pipeline SNR
  Analysis). Scans the viewer for all diagnostic step layers produced by the
  Pipeline Step Diagnostics widget and computes per-step SNR, displayed as a
  colour-coded table: green = gain, dark red = NaN (background hard-zeroed,
  step is destructive), orange = regression.
  - SNR metric: mean(top 2% non-zero pixels) / std(IQR 25th-75th percentile).
    The IQR noise region is used because subtraction steps hard-clip background
    to 0, collapsing a bottom-50% std to 0 and masking the destruction.
  - Δ SNR column shows change relative to the first step (raw input) of each
    pipeline (current vs v1.0.0 tracked separately).
  - Summary note identifies the best step and flags how many steps collapse
    the noise floor to 0.
### Findings from real data (DAPI + GFP condensate images, ball_radius=15)
  - Rolling-ball subtraction at any scaling factor hard-zeros the background
    on both DAPI and GFP channels → NaN SNR → the step is counterproductive
    for condensate segmentation.
  - LoG(σ=3) alone gives 5× SNR on DAPI, 6.8× on GFP vs raw.
  - DoG with sigmas scaled to ball_radius gives nearly identical gains.
  - The rolling-ball BACKGROUND itself (not the subtraction) carries useful
    chromatin topology for DAPI — it should be exposed as a named output layer.
## [1.5.122] - 2026-07-03
### Fixed
- **Pipeline diagnostics "Could not read layer: name 'np' is not defined".** 
  `numpy` was not imported inside the `_run` closure in `_add_pipeline_diagnostics`.
  Added `import numpy as np` at the top of `_run`.
- **Maximize on startup unreliable.** `showMaximized()` was called via a
  120 ms `QTimer`, which is a race condition — on slower machines the relayout
  hasn't settled, and on faster ones the call can land before or after the
  event loop is ready, producing inconsistent results. Fixed by calling
  `_maximize()` synchronously before `napari.run()` (which starts the event loop).
  Qt's window-state flag is set immediately and honoured on the first show event
  regardless of when the event loop starts. Style and branding remain deferred
  (they touch live widgets that need the event loop running).
## [1.5.121] - 2026-07-03
### Fixed
- **Dock widget too wide / right side clipped at default size.** Description
  and subtitle QLabels with `setWordWrap(True)` still reported their full
  one-line width as the minimum size hint, forcing the dock (and its scroll
  area) to be wider than the napari pane allows. Fixed by adding
  `setSizePolicy(Ignored, Minimum)` after every `setWordWrap(True)` call
  across all workflow modules, and by calling `setMinimumWidth(0)` on the
  inner widget of every QScrollArea dock so the container can compress
  freely. The "Pixel size (no scale in metadata)" group-box title was also
  shortened to "Pixel size" to reduce the minimum title-bar width it imposes.
## [1.5.120] - 2026-07-03
### Fixed
- Missing QProgressBar import inside _add_pipeline_diagnostics (NameError on open).

## [1.5.119] - 2026-07-03
### Fixed
- **Upscaling produces duplicate layers when multiple layers are selected.**
  `viewer.layers.selection` is a live set — napari auto-selects each newly added
  layer, mutating the set mid-iteration so each upscaled output was immediately
  upscaled again. Fixed by snapshotting the selection to a plain list (filtered
  to `napari.layers.Image` only) before the loop.
- **Scale bar does not update when switching to an upscaled layer.** The scale
  bar was set once at file-load time and stayed frozen regardless of which layer
  was active. A `viewer.layers.selection.events.changed` listener now fires
  `_update_scale_bar_for_active_layer()`, which reads `layer.scale[-1]` on the
  topmost selected Image layer and sets `scale_bar.unit` to `'um'` or `'px'`
  accordingly. Upscaled layers carry `scale = source_scale / 2`, so the bar
  correctly reflects their (smaller) physical pixel size on the same FOV.
- **Clarified upscaling notification.** The toast now explains that both layers
  cover the same physical field of view (same µm extent, finer pixel grid) and
  that the scale bar updates when you click a different layer.
## [1.5.118] - 2026-07-03
### Added
- **Pipeline Step Diagnostics widget** (Toolbox → Image Processing → Pipeline Step
  Diagnostics). Two tabbed panels — "Current (1.5.x)" and "v1.0.0 reference" — each
  add a named napari layer for every sub-step of pre_process_image AND
  rb_gaussian_bg_removal_with_edge_enhancement, so the exact step where the two
  pipelines diverge is visible. Known labelled differences shown in the widget:
  ① /max normalisation (current only); ② square vs disk structuring element;
  ③ DoG (fixed σ=2.0/3.2) vs LoG (σ=3, radius-implicit).
## [1.5.117] - 2026-07-03
### Fixed
- Missing `label_with_circle` import in `invitro_fluor_ui` (NameError on open)
  and duplicate import in `invitro_bf_ui` / `brightfield_ui` — left over from
  the scrollbar/import fix pass that stripped a `try/except` block.

## [1.5.116] - 2026-07-03
### Fixed (critical — 2-D fluorescence preprocessing regression)
- **Aggressive signal suppression in preprocessing and background removal for
  dim fluorescence images.** `dtype_conversion_func` uses `img_as_float32`,
  which divides by 65535 (the full uint16 range). A typical condensate
  fluorescence image with a true max of ~2000–3000 counts therefore arrives
  at `pre_process_image` and `rb_gaussian_background_removal` as float32 in
  the range [0, 0.046] instead of [0, 1]. Every subsequent multiplicative
  step — the white-top-hat rescale, the DoG envelope, and the WBNS wavelet
  thresholding — is calibrated for [0, 1] input; at 0.046 scale they all
  over-suppress the signal and produce a near-blank output. Both functions
  now normalise to [0, 1] by the actual image maximum immediately after the
  dtype conversion, before any processing begins.
## [1.5.115] - 2026-07-03
### Fixed
- **"Upscaling didn't work" visual confusion.** The upscaled layer is scaled to
  `source_scale / 2` so both layers occupy the same world-space field of view
  (correct for alignment). But this meant the 2x extra resolution was invisible —
  both layers appeared identical in the napari canvas until you zoomed in. Now a
  napari notification confirms success and explains: "Upscaled X: WxH → W2xH2 px
  (2× linear). Both layers occupy the same field of view — zoom in to see the
  extra resolution in 'Upscaled X'."
### Note
- The "aggressive preprocessing / yellow field" report is fixed by the CLAHE
  range normalisation in 1.5.105 (`_safe_equalize_adapthist`). Users still
  experiencing this should update to >= 1.5.105. All four CLAHE call sites in
  `image_processing_tools.py` route through the safe wrapper that min-max
  normalises to [0, 1] before CLAHE, preventing the near-zero collapse.
## [1.5.114] - 2026-07-03
### Fixed
- **`QSizePolicy` NameError crashing Time-Series, FRAP, VPT, Z-Stack, and other
  workflows on open.** `ts_cellpose_tools.py` imported it as `_QSP` but used the
  bare `QSizePolicy` name — fatal NameError on any pipeline that invokes
  `_add_run_ts_cellpose`. Fixed by exporting both names from the local import.
- **Pixel-size gate removed from non-imaging workflows.** `_add_workflow_header`
  was injecting the pixel-size QGroupBox into every pipeline including FD-Curve,
  Droplet Fusion, and Colocalization. Now gated behind `include_pixel_gate=True`
  (only set on Condensate, Time-Series, General, and Fibril imaging pipelines).
- **Title clipping** fixed in all 7 separate workflow modules (brightfield,
  in-vitro ×2, FRAP, VPT, FD-curve, z-stack) — all QFormLayout instances now
  get a `setContentsMargins(9, 20, 9, 6)` top margin so the group-box title
  never sits on top of the first content row.
### Changed
- **Status-circle UEX corrected to match the temperature-module design.** The
  `StatusComboBox` inline-dot approach (wrong — inside the widget) is removed.
  Key layer-selector fields now use `_layer_row` (in the condensate pipeline
  tools) or `label_with_circle` (in the separate workflow modules), placing the
  dot as a column to the *left* of the field label — exactly as designed.
  Circles correctly show red (required) / yellow (optional) → green on selection.
  A new `label_with_circle()` helper in `field_status.py` makes this available
  to any `QFormLayout.addRow()` call with one line.
## [1.5.113] - 2026-07-03
### Added (field-status circles — rollout)
- **The field-status circle is now on the key input of every step in every
  workflow.** `create_layer_dropdown` (the shared layer-selection widget used
  across all pipelines and tools) now returns a `StatusComboBox` — a QComboBox
  that paints a small status dot at its left edge: red when no valid layer is
  selected, green once a real layer is chosen. Because it is still a QComboBox,
  every existing call site works unchanged, so this rolls the required-input
  indicator out universally with no per-form edits. Combined with the Step 1
  file-I/O block and pixel-size gate added to each pipeline header, the key
  required inputs (file loaded, pixel size, layer selections) all carry the
  red→green status indicator — the ~80% scope from the original design.
## [1.5.112] - 2026-07-03
### Fixed / Changed (workflow UI/UEX)
- **Horizontal scrollbars removed from every workflow.** The 7 separate workflow
  modules (brightfield, in-vitro fluor & BF, FRAP, VPT, force-distance, z-stack)
  created their scroll areas without the always-off horizontal policy, and the
  standalone-tool dock path had no scroll area at all. All dock paths now disable
  the horizontal scrollbar so content fits the width (vertical scroll only).
- **Field-status header rolled out to the main pipelines** (Cellular Condensate
  fluorescence, Time-Series, Object/Pixel Colocalization, General, Fibril): each
  now opens with the Step 1 file-I/O status block (green once an image is loaded)
  and the conditional pixel-size gate — the same UEX pattern as the temperature
  module — via a shared `_add_workflow_header` helper. Layout spacing tightened
  for a more compact dock.
## [1.5.111] - 2026-07-03
### Fixed (µm scale consistency across all layers)
- Every layer now preserves the micron scaling of the primary image, so masks,
  processed images, upscaled layers, and overlays all occupy the same field of
  view and stay aligned (previously only the source image carried the µm scale,
  so derived layers — like the upscaled image — rendered at the wrong size).
  Implemented as a single `inserted`-event listener in FileIO plus a re-align
  when the reference scale is set: Image/Labels layers are aligned by field of
  view (so an upscaled 2× mask gets half the reference pixel size), Shapes/Points
  overlays inherit the reference per-pixel scale, and any layer that already
  carries a deliberate non-unit scale is left untouched. No per-call-site changes
  were needed — it covers all ~100 layer-creation calls centrally.
## [1.5.110] - 2026-07-03
### Changed
- **Upscaling interpolation switched from bicubic spline to Akima.** The bicubic
  `RectBivariateSpline` overshoots at sharp intensity edges, producing ringing
  halos and negative values around bright puncta (hundreds of counts below
  background, then clipped). A separable 2-D Akima interpolant is local and
  shape-preserving: on a puncta test it produced zero negative/ringing pixels vs
  52 for bicubic. Falls back to bicubic if Akima is unavailable.
### Fixed
- **Upscaled layer now aligns physically with its source** (scale set to the
  source scale ÷ the upscale ratio). Previously the upscaled layer was added at
  scale 1 while the source could carry a µm scale, so the source appeared
  "embedded" as a small image inside a larger upscaled frame. The final
  multiplication remains 2× — each "Upscaled X" is 2× of its own source X, with
  no nested/compounding upscales.
## [1.5.109] - 2026-07-03
### Fixed
- Silenced the napari `Window.qt_viewer` FutureWarning (deprecated public
  access, removed in napari 0.8). The two places that read the Qt canvas size now
  prefer the private `_qt_viewer` attribute and suppress the warning on the
  public fallback — no behaviour change, just no console warning.
## [1.5.108] - 2026-07-03
### Fixed (crash on Home / reset view)
- **"cannot convert float NaN to integer" crash when pressing Home.** The
  \'Object Diameter\' and \'Cell Diameter\' line-annotation layers were created
  empty on every image load, and an empty Shapes layer reports a NaN extent in
  this napari build. reset_view (Home) then computed a NaN camera zoom, which the
  scale-bar overlay hit with floor(log(NaN)) once the bar was in µm mode. Fix:
  the diameter layers are now seeded with a single invisible near-zero-length
  line so their extent is finite; measurement skips the seed (it reads the last
  non-degenerate line), so results are unchanged.
- Hardened both scale-bar paths against non-finite/zero pixel sizes
  (`_enable_auto_scale_bar` validates the scale is finite and positive;
  `draw_custom_scale_bar` rejects NaN/inf inputs), so no scale-bar code can put a
  NaN into the world extent.
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
