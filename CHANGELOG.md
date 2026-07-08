# Changelog
All notable changes to PyCAT-Napari will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.208] - 2026-07-05
### Fixed (overlay stripe — the ACTUAL root cause: a scale mismatch)
- **The Overlay Image now inherits the source image layer's scale.** Confirmed via git
  diff that in v1.0.0 the "Upscaled Fluorescence Image" was added with NO scale (default
  1.0), and the overlay (also no scale) matched it — so the (H, 2W) side-by-side rendered
  correctly. A later change gave the upscaled layer an explicit physical µm/px `scale`
  (~0.049) to align it with its source, but the overlay was never updated to match, so it
  stayed at scale 1.0 — a ~20× coordinate mismatch that rendered the overlay as a giant
  stripe extending far past the scaled data (the "µm at the data, mm at the overlay"
  scale-bar symptom). The overlay is now added with the same scale as its source image
  layer, putting both back in one coordinate space. This is the real fix; the previous
  reverts addressed the wrong layer of the problem.
### Improved
- **Overlay PNG contrast.** The exported `_puncta_overlay.png` blew out the bright cell
  body because the percentile stretch was computed over the whole frame (mostly black
  background, dragging the window down). It now computes the stretch window over the
  signal pixels (non-near-zero) with a high upper percentile (99.8), preserving bright
  detail instead of clipping it to white.

## [1.5.295] - 2026-07-08
### Added / Fixed (context-aware multi-file OME-TIFF handling; stop per-frame companion warning)
- **Multi-file OME-TIFF sets (e.g. Micro-Manager MMStacks split across sibling files) are now
  resolved up front, lazily, without materialising the stack.** A new resolver reads the OME
  metadata to see which linked files it references and checks which are actually present on disk,
  then builds a global frame->(file, page) map spanning the present files. Two cases are handled:
  (1) all companions present -> frames are read across the linked files transparently; (2) some
  companions missing (a file copied out of its set) -> only the frames that physically exist are
  loaded, with a clear warning, instead of silently zero-filling absent planes. The single-file fast
  path is unchanged.
- **Fixed a warning that spammed the terminal once per frame during parallel bead detection.** The
  VPT parallel workers re-open the file per frame; on a multi-file OME set whose companions were
  missing, tifffile printed "OME series failed to read ... Missing data are zeroed" on every read
  (thousands of lines). Workers now read via the resolved page map (or, for single files, with the
  tifffile OME warning silenced) and match the serial reader frame-for-frame.

## [1.5.294] - 2026-07-08
### Fixed (batch replay ran steps on the wrong channel; Measure Line values were ignored)
- **Batch replay of the Condensate Analysis pipeline could run steps on the wrong image layer, and
  ignored the measurements made with the Measure Line tool.** Contributed by a user who diagnosed
  the failures. Three fixes: (1) replay now resolves the actual layer name the GUI recorded for each
  step (a new _resolve_image_layer helper) instead of assuming a fixed channel/stage — previously
  Cellpose could run on the foreground-suppressed segmentation channel instead of the fluorescence
  channel and segment 0 cells. It honours both which channel (segmentation / fluorescence / a named
  extra channel from 3+ fluorophore files) and which stage (raw vs preprocessed / background-
  removed) the recorded name encodes. (2) The Measure Line step now applies the recorded
  cell_diameter / ball_radius / object_size instead of being a no-op; leaving the stale open_image
  ball_radius in place had, after upscaling, produced an oversized rolling-ball element and a
  MemoryError in condensate segmentation, and gave Cellpose the wrong cell diameter. (3) Replay now
  skips cell/condensate analysis gracefully (with an explanatory message) when segmentation yields
  0 cells or no puncta, instead of crashing inside pandas. Preprocessing and background removal also
  now act only on the layer that was active when the step was recorded, matching the interactive
  tool. Non-condensate replay paths (time-series, brightfield, in-vitro) are unchanged.

## [1.5.293] - 2026-07-08
### Added (VPT: CPU-parallel bead detection)
- **Fast-mode bead detection now runs across a process pool when possible**, cutting the time to
  reach the tracking step on multi-core machines. Per-frame blob detection (the expensive,
  embarrassingly-parallel part) is dispatched to worker processes that re-open the source file and
  read their own frame; template building, scoring and classification stay in the main process. It
  activates automatically (parallel=auto) for fast mode on a file-backed stack with more than one
  frame and more than one worker, and falls back cleanly to serial detection for anything else (a
  non-file-backed stack, a single frame, or any worker error). Results are unchanged: a regression
  test (tests/test_vpt_parallel_equivalence.py) asserts the parallel path produces bead coordinates
  identical to serial on every frame. GPU-accelerated detection is a separate, later addition built
  on this foundation.

## [1.5.292] - 2026-07-08
### Changed (restrict to Python 3.12 to prevent accidental 3.13 installs)
- **requires-python tightened to >=3.12,<3.13**, and the 3.13 classifier and the contradictory
  "supported range 3.12-3.13" README note were corrected to 3.12-only. Some users following the
  install steps ended up on Python 3.13, which is not yet validated against the native dependency
  stack (PyQt5/torch/numba/cellpose on arm64) and contributed to launch instability. pip will now
  refuse to install on 3.13 with a clear version error instead of installing and crashing. 3.13
  support can be re-enabled after deliberate testing.

## [1.5.291] - 2026-07-08
### Fixed (launch segfault on Apple Silicon / arm64 Macs)
- **Multiple native arm64 (Apple Silicon) macOS users hit a segmentation fault at launch**, right
  after startup finished (the OMP: Info #276 omp_set_nested banner printed first). This is the
  signature of a duplicate OpenMP runtime: PyTorch, Numba, MKL and Cellpose can each load their own
  copy of libomp, and on arm64 two copies in one process can abort at the C level. Two mitigations,
  both applied before any native library is imported: (1) KMP_DUPLICATE_LIB_OK=TRUE (plus capped
  OMP thread counts) so the OpenMP runtime tolerates the duplicate instead of crashing; these are
  no-ops on machines without the conflict, so they are safe everywhere. (2) On macOS the background
  startup thread no longer imports PyTorch — that import raced with Qt/CentralManager initialising
  on the main thread (a concurrent native-init crash the code already warned about), and the check
  it performed (CUDA availability) is meaningless on Apple Silicon anyway. Torch now loads on first
  actual use instead of during launch.

## [1.5.290] - 2026-07-08
### Fixed (VPT bead classification flickered frame-to-frame: aggregates and dim detections)
- **A large aggregate alternated between the aggregate (red) and singlet (green) class across
  frames, and very dim detections dropped in and out.** Both were frame-to-frame instability from
  classification thresholds sitting right where borderline objects live. Two fixes, validated on
  test data: (1) the aggregate mass gate moved from the 99.5th to the 99.3rd percentile — the 99.5
  cut landed inside the top mass cluster, so a genuine aggregate whose mass fluctuates a few percent
  kept crossing it; 99.3 sits just below that cluster, so the true aggregates stay above it every
  frame. (2) the template-match (NCC) floor moved from 0.50 to 0.55 — dim detections whose match
  score hovered at 0.50 were flipping between kept and rejected each frame; the firmer floor keeps
  that borderline-noise population consistently out. Aggregate classification is now stable across
  frames and the dim in/out flicker is roughly halved. NOTE: a bead whose intensity GENUINELY
  crosses the threshold in a given frame can still change class there; fully eliminating that needs
  temporal consistency (holding an object's class once it is tracked), a separate future refinement.

## [1.5.289] - 2026-07-08
### Fixed (in-dock pixel-size gate now hides after the scale is set via the load-time popup)
- **Setting the pixel size in the load-time popup did not hide an already-open method panel's
  in-dock pixel-size gate.** The popup and the gate share the same scale value, but two links were
  missing: the popup did not notify the gate to re-check after writing the scale, and the gate only
  hid on a scale it had confirmed itself or one from metadata — not on a valid scale set elsewhere.
  The popup now fires the data-changed notification after setting the scale, and the gate hides
  whenever the repository holds a valid scale that was set externally (its own field still empty),
  while still not hiding mid-typing in the dock field. Popup and dock gate now stay consistent.

## [1.5.288] - 2026-07-08
### Added (VPT: physical bead size, Airy-model template, and ring/multi-scale de-duplication)
- **Bead detection can now use the physical bead size and merge duplicate detections.** Large
  (non-diffraction-limited) beads image as an Airy disk and can trigger several detections on one
  bead — at multiple blob scales, or on the Airy ring. Three additions address this. (1) A **bead
  size** input (nm, default 200) is converted to pixels via the loaded pixel size to set the
  detection template patch size and the de-duplication radius. (2) A **de-duplication** step (on by
  default) merges detections that fall within about one bead radius, keeping the brightest (the bead
  centre), so one bead yields one point. (3) A **template type** selector offers the empirical PSF
  (measured from the data, default) or an **Airy model** (analytic Bessel J₁ disk + ring) for data
  where beads show a resolved ring, so the bead matches as a single object rather than the ring
  being detected separately. On test data whose beads showed no resolved ring the empirical template
  remains the better fit; the Airy option is for scopes/beads that do show rings.
### Changed
- Pixel-size load dialog: darkened the explanatory text for readability.

## [1.5.287] - 2026-07-08
### Added (dedicated load-time pixel-size dialog, separate from the in-dock gate)
- **A modal pixel-size dialog now appears on load when an image has no scale in its metadata.** This
  replaces the earlier attempt to make the in-dock gate double as a popup (which flickered as a
  transient window). The dialog is a proper top-level modal — no embedding/parenting subtleties —
  and appears only for the hygiene case (pixel size fell back to 1.0 and did not come from
  metadata). It includes a short explanation of why pixel size matters (it sets the physical scale
  for all downstream measurements — sizes, distances, diffusion, viscosity), an input field, and
  Set/Skip buttons. Skip leaves the scale unset (the in-dock gate still lets the user set it later).
  The dialog writes the same data_repository scale the in-dock gate reads, so the two stay
  consistent. The in-dock gate no longer refreshes at construction time (which caused its own
  pre-dock flicker); it now updates only on real triggers (data switch, post-load notify) once it
  is docked.

## [1.5.286] - 2026-07-08
### Fixed (removed a transient flickering pixel-size window)
- **A pixel-size window briefly flickered and vanished on load.** The gate de-duplication
  coordinator added in 1.5.283 iterated a global registry that still held gates from previously-
  removed analysis panels; briefly toggling one of those stale, unparented gates visible produced a
  flash of a window that immediately disappeared. The coordinator turned out to be unnecessary: the
  real cause of the earlier duplicate/floating windows was that the gate was not embedded in its
  panel layout (fixed in 1.5.283), and PyCAT only ever docks one analysis panel at a time, so there
  is only ever one gate. The coordinator has been removed and each gate simply shows/hides itself
  within its own panel. The in-panel gate still appears correctly when a scale is needed; the stray
  flickering window is gone.

## [1.5.285] - 2026-07-08
### Fixed (pixel-size gate never appeared — missing show signal in the refactored coordinator)
- **The gate coordinator introduced in 1.5.283 was missing the call that marks a gate as wanting to
  be shown.** When the gate visibility logic was refactored to route through the shared coordinator,
  the hide branches were updated but the visible branch never set its want-to-show flag, so the
  coordinator always saw zero gates wanting display and the gate never appeared on load or method
  selection. Added the missing signal in the visible branch. The gate now appears when a scale is
  needed, still as a single embedded panel (no floating or duplicate windows).

## [1.5.284] - 2026-07-08
### Fixed (pixel-size gate stopped appearing after the 1.5.283 de-duplication change)
- **The 1.5.283 gate coordinator was too strict and suppressed the gate entirely.** It only showed
  a gate whose widget already reported a parent, but that check could be false at the moment the
  gate re-evaluated (before the panel finished attaching), so the gate never appeared on load or
  method selection. The floating-window problem is already prevented by embedding the gate in its
  panel layout (added in 1.5.283), so the extra parent check was redundant and has been removed. The
  coordinator now simply shows the first gate that needs a scale and hides the rest — restoring the
  gate on load while keeping single-window, no-orphan behaviour.

## [1.5.283] - 2026-07-08
### Fixed (pixel-size gate floated as separate windows / persisted after close; auto-clear now covers stacks)
- **The pixel-size gate appeared as one or more floating windows that persisted even after the PyCAT
  GUI was closed.** Root cause: the gate group box was never added to its panel layout, so Qt
  rendered the parentless widget as a top-level window. Because each analysis panel builds its own
  gate, several could float at once (the "3 windows"), and being unparented they outlived the main
  window. The gate is now embedded in its panel layout and starts hidden. A shared coordinator
  ensures at most one gate is visible at a time and never shows a gate that lacks a parent window,
  so duplicates and orphan windows can no longer occur.
- **Auto-clear on load now also applies to T/Z and multi-dimensional stacks.** The previous release
  reset existing layers before loading a new 2-D image, but the stack loader (open_stack, used for
  time-series / z-stack / OME-TIFF / Imaris) did not, so loading a stack over an existing image
  still produced the confusing frame-count overlap. The same confirm-then-reset now runs for stack
  loads.

## [1.5.282] - 2026-07-08
### Fixed (pixel-size gate now appears after loading an image whose metadata lacks a scale)
- **The pixel-size gate stopped appearing after a file load, which could let analyses run with the
  fallback scale of 1.0 um/px.** The gate re-evaluates its visibility only when notified of a data
  change, and those notifications previously fired only on an active-data-class SWITCH (e.g.
  changing analysis method) — not on a plain image load. So opening a file whose metadata has no
  pixel size (which falls back to 1.0 and should prompt the user) left the gate in its pre-load
  state and it never appeared. This matters because the pixel size feeds physical-unit conversions
  (e.g. VPT microrheology viscosity via Stokes-Einstein): a silent 1.0 fallback yields wrong-scaled
  results. Added CentralManager.notify_data_changed(), which fires the registered gate callbacks
  without switching the data class, and called it at the end of every image-load path (2-D images
  and T/Z/multi-dimensional stacks, including OME-TIFF and Imaris) once the freshly-loaded pixel
  size is in the data repository. The gate now correctly appears for files without a real scale and
  stays hidden for files whose metadata supplies one. Mask loading is unaffected.

## [1.5.281] - 2026-07-08
### Fixed (loading a new image now clears the previous one first, avoiding confusing overlaps)
- **Loading a new image while a previous one was still present caused confusing display behaviour.**
  For example, loading a 300-frame stack over an existing 1000-frame stack made the new image look
  like it had failed to load: the frame slider still spanned 1000 frames, and scrubbing past frame
  300 showed the old layer (or nothing) because the new image had no data there. Opening an image
  now resets to the workflow start state BEFORE the new dataset is added, so it loads clean. If
  existing layers are present (potentially-unsaved work), a confirmation prompt appears first
  — matching the Clear button's safety behaviour — so analysis is never discarded silently. The
  reset reuses the same _clear_everything logic the Clear button uses (layers, data repository,
  dataframes, workflow checklist, and batch recording). Mask loading is unaffected (masks are meant
  to overlay the current image, so they still add rather than replace).

## [1.5.280] - 2026-07-08
### Fixed (VPT fast-mode bead classification recalibrated for Airy-disk beads; garbage now rejected)
- **Fast-mode bead classification was mismapped for large (non-diffraction-limited) beads, and never
  rejected non-bead detections.** Two problems, both fixed. (1) The singlet-vs-aggregate split was
  drawn on brightness, which is inverted for 200 nm-2 um beads that image as bright Airy disks: a
  real single bead is bright and high-mass, so it was wrongly called an aggregate (the previous
  logic labelled ~220 aggregates per frame when only ~2 exist). Aggregate now requires a bead to be
  BOTH high-mass (top ~0.5% tail) AND bright/compact, which matches a hand-labelled ground-truth
  frame (~2-3 aggregates/frame, the large majority singlets). Dim-but-large out-of-focus blobs are
  now flagged as a distinct "ambiguous" class (blue) rather than forced into singlet/aggregate,
  since they cannot be confidently classified. (2) Detections that poorly match the empirical PSF
  template (low normalised cross-correlation) — Airy-ring fragments, hot pixels, and noise — are now
  REJECTED (dropped) instead of being labelled and displayed, so a marked point is a real bead.
  Aggregate and ambiguous beads are routed to the secondary population (kept out of the primary
  microrheology set, since their size/uncertainty would bias Stokes-Einstein viscosity) rather than
  dropped. NOTE: Airy-ring de-duplication (merging multiple detections around one bead) is a further
  planned refinement; this release rejects poorly-matched ring fragments but does not yet merge
  well-matched ones.

## [1.5.279] - 2026-07-08
### Fixed (VPT Link Trajectories crashed with AttributeError: no _fit_quality)
- **Both VPT trajectory linkers (TrackMate LAP and Bayesian/Hungarian) raised
  AttributeError: 'VideoParticleTrackingUI' object has no attribute '_fit_quality'.** The
  _fit_quality checkbox was replaced by the detection-mode dropdown in 1.5.277, but the linking
  step (_on_link) still referenced it when deciding whether to route aggregates to a secondary
  population. Since all detection modes now classify beads (a bead_class column is always
  produced), the stale _fit_quality guard was removed — aggregate routing is now gated solely on
  the "Route aggregates" checkbox and the presence of the bead_class column. Detection itself was
  unaffected (fast-mode detection ran correctly end-to-end); this only blocked the linking step.

## [1.5.278] - 2026-07-07
### Fixed (VPT Detect Beads crashed opening the long-run warning dialog)
- **The >2-minute detection warning dialog raised a TypeError and aborted Detect Beads.** The
  QMessageBox.question() call passed the VPT UI object as its parent, but that class is a plain
  controller (not a QWidget), which PyQt5 rejects. Fixed by passing None as the dialog parent
  (matching the established pattern elsewhere in the codebase). Detect Beads now runs; the warning
  appears correctly before long (precise-fit) runs.

## [1.5.277] - 2026-07-07
### Changed (VPT bead detection is far faster: fast template mode by default, with a visible progress bar)
- **VPT bead detection now defaults to a fast empirical-PSF template method instead of a per-bead
  Gaussian fit, cutting a long-movie run from hours to minutes, and the progress bar now actually
  moves.** The old default fit a 2D Gaussian to every bead in every frame (bounded curve_fit,
  maxfev=10000): on a ~1000-frame movie with ~800 beads/frame that is ~3 hours and looked frozen.
  Detection now offers three modes: "Fast (template match)" (default) builds one empirical PSF
  template from the cleanest beads and scores every bead by normalised cross-correlation + peak SNR
  + radial symmetry (~microseconds/bead), giving the overlay and singlet/aggregate/out-of-plane
  classification in ~10-15x less time; "Fast fit" runs a bounded Gaussian with a tight iteration
  cap; "Precise fit" is the full Gaussian for when localisation precision matters. Added a
  "Sub-pixel centres" toggle (cheap intensity-centroid refinement) and a "Rebuild PSF template per
  frame" option (adapts to focus drift / SMLM-like data; default builds one template per stack).
  The progress bar is now determinate (0..n_frames) and advances per frame instead of sitting as an
  indeterminate spinner, and a confirmation warning appears before runs estimated to exceed ~2
  minutes. New template functions build_bead_template()/score_beads_template() and a fast= option on
  fit_gaussian_2d_spot(). (Further speedup via across-frame parallelism is planned separately.)

## [1.5.276] - 2026-07-07
### Changed (VPT bead detection now streams frame-by-frame; host inference uses keyframes)
- **VPT bead detection no longer materialises the whole movie in memory, and "Infer Host from
  Beads" no longer processes every frame.** Two related performance fixes: (1) detect_beads_stack
  now STREAMS frames one at a time via a new iter_frames() helper, reading each frame from the lazy
  layer on demand instead of building a full (T, H, W) array up front. Memory stays flat regardless
  of movie length, and the earlier frame-0-collapse class of bug is now impossible by construction
  (frames are indexed individually, never via np.asarray on the whole wrapper). (2) Host inference
  (Mode C) previously ran blob detection on every frame just to build a bead-density map, which on
  a long movie (e.g. ~1000 frames) took minutes and froze the UI. Because the host is treated as
  stationary, it now samples up to 8 evenly-spaced keyframes — empirically this reproduces the
  all-frames inferred host to within a few percent IoU while cutting the work by ~100x. Added a
  frame_indices parameter to detect_beads_stack for keyframe subsetting (original frame indices are
  preserved in the output). New iter_frames() streaming helper in file_io alongside
  materialize_stack().

## [1.5.275] - 2026-07-07
### Added (VPT can infer an unlabelled host condensate from the bead distribution — Mode C)
- **New "Infer from beads" host mode for VPT.** When the condensate is real but unlabelled (no
  companion host channel), PyCAT can now synthesise a host mask from where the beads are. The
  method combines three stages: (1) a bead-density map is thresholded and split with a
  distance-transform watershed, so touching condensates are separated rather than merged; (2) each
  region is validated by its internal bead content; and (3) a physical size gate keeps only
  condensates large enough for beads to sample bulk (boundary-free) diffusion — beads in a
  condensate that is too shallow feel the interface and do not report bulk viscosity, so small
  condensates are discarded. Condensates clipped by the frame edge have their true radius estimated
  by fitting a circle to their visible (non-border) interface arc, so a large condensate that is
  only partly in frame is still retained. The minimum condensate radius is user-adjustable (µm,
  physically grounded, default 5 µm). The result is reported explicitly as an INFERRED boundary
  (it follows the bead distribution, not a directly imaged condensate edge). This method was
  selected by comparing it against a bead-geometry/clustering alternative on real data with a
  hand-annotated boundary: the density+watershed+physics approach recovered the annotated central
  condensate substantially better (IoU ~0.73 vs ~0.50) and, unlike proximity clustering, did not
  collapse neighbouring condensates into one region. Exposed as new tools infer_host_from_beads()
  in vpt_tools and an "Infer Host from Beads" action in the Step 2 panel.

## [1.5.274] - 2026-07-07
### Added (VPT host-condensate segmentation is now optional — no-host / full-frame mode)
- **VPT no longer requires a companion host-condensate channel.** Not all microrheology data has a
  separate channel that labels the host phase — for example beads-in-glycerol viscosity controls,
  or any bulk-medium experiment with no condensate boundary at all. Previously the bead-detection
  step hard-blocked with "run Step 2 first" whenever no host mask was present, making these
  datasets impossible to analyse. Step 2 now offers a Host mode selector: "Host channel" (the
  existing behaviour, default), "No host (full frame)" (skip host masking and track every bead
  across the whole field), and "Infer from beads" (reserved for a future release, disabled for
  now). In no-host mode the host-segmentation controls are greyed out and bead detection proceeds
  with no inclusion mask. The end-to-end run_vpt_analysis() helper likewise accepts host_image=None
  and skips host segmentation. The underlying detection already treated a missing host mask as
  "keep all beads", so this change is purely about exposing that path in the UI. Validated on a
  real 5-frame bead substack: all ~780-820 beads per frame are detected and tracked across the full
  field with no host channel. (Full-frame control support also enables pipeline self-validation:
  beads in a known-viscosity medium like glycerol are how VPT microrheology is calibrated.)

## [1.5.273] - 2026-07-07
### Fixed (VPT particle tracking only saw the first frame of a time-series)
- **The VPT (video particle tracking) pipeline silently collapsed any multi-frame time-series to
  its FIRST frame, so bead detection, track linking, and the scroll-through detection overlay all
  behaved as if the movie were a single image.** The bead-detection step loaded the stack with
  `np.asarray(layer.data)`, but for an OME/ImageJ TIFF time-series PyCAT wraps the data in a lazy
  `_TiffPageStack` whose `__array__` deliberately returns only frame 0 (to keep napari’s incidental
  array requests cheap). So `detect_beads_stack` received a single 2D frame, detected beads on
  frame 0 only, and produced no linkable trajectories — and the red/yellow/green "Bead Detections"
  points layer only had frame-0 points, so it appeared correct on the first frame but went empty
  when scrolling. Fixed by loading the bead stack with `materialize_stack()` (the same helper used
  by the temperature workflow), which reads every frame into a real (T, H, W) array and passes
  plain arrays through unchanged. Validated on a user-provided 5-frame Blackfly uint8 substack:
  ~780-820 beads detected per frame across all frames, tracks link across the full stack, and the
  detection overlay now updates correctly as the user scrolls through time. (uint8 input is handled
  correctly — detection min-max normalizes per frame, so bit depth does not affect thresholds.)

## [1.5.272] - 2026-07-07
### Fixed (object-based colocalization could report impossible overlap values > 1.0)
- **The object-based colocalization coefficients (Manders M1/M2, Jaccard, Sørensen-Dice) could
  return values greater than 1.0 and were biased by arbitrary object-ID numbers.** These
  coefficients are only valid on boolean (0/1) masks, but the two object masks were passed in as
  LABEL maps (object 1 = 1, object 2 = 2, ...). The overlap math (`sum(mask1 * mask2) /
  sum(mask1)`, etc.) therefore multiplied and summed label VALUES, so an object labelled 3
  counted three times as much as one labelled 1 — producing overlaps above 1.0 (impossible for a
  fraction) and making identical experiments disagree purely because objects were numbered
  differently. Fixed by binarising both object masks (`mask > 0`) at the start of each of the four
  overlap functions, before the ROI is applied. Verified on the reviewer’s worked example: the
  buggy path gave M1 = 1.5 / Dice = 1.2; the fixed path gives the correct M1 = 0.5 / Dice = 0.667.
  The object-distance analysis is deliberately left untouched: it re-labels internally
  (`skimage.measure.label`) and legitimately needs the label maps, so binarising only the overlap
  steps fixes the coefficients without breaking distance measurements. (Reported in an independent
  scientific/code review, Finding 1.)
- Also includes the deferred one-time "Loading Cellpose model weights from cache into memory"
  log breadcrumb that had been documented under 1.5.267 but not fully committed.

## [1.5.270] - 2026-07-07
### Docs (roadmap: biological object model & linked multiscale navigation)
- **Added a roadmap section capturing concepts from cross-evaluating the NimbusImage paper**
  (Nat. Methods 2025), a cloud-first petabyte-scale platform. Conclusion: don’t adopt the cloud/
  data-movement architecture (PyCAT’s data-local, interactive, quantitative philosophy is a
  deliberate strength), but extract three converging concepts: (1) formalize the implicit analysis
  hierarchy (Image→Cell→Organelle→Condensate→Punctum) that already exists via the cell/puncta
  parentage; (2) linked multiscale navigation — bidirectional brushing between plots and image
  layers so selecting a data point jumps to that object in the viewer and vice versa (the identity
  links already exist; the interactive bridge does not); (3) context-aware analysis that inherits
  spatial hierarchy. These unify into an internal biological object model where each object carries
  scale, persistence/topology, material state, neighborhood, and parentage — quantities PyCAT
  already computes in separate modules but never assembles onto one entity. In this model, each
  object carries a standardized record (geometry, intensity, scale-space signature, topology,
  material state, spatial relationships, QC, provenance, parent/child), and the existing modules
  (QC, benchmarking, spatial stats, DoH, FRAP, MSD, future FISH) become views of one object rather
  than isolated analyses — moving PyCAT toward a "scientific operating system for microscopy".
  Verified against the codebase. Documentation only.
- **Added a roadmap section capturing a reproducibility/measurement-reliability cluster** from
  cross-evaluating a Nature Methods 2025 reproducibility paper (strongly on-thesis with PyCAT's
  QC/rigor direction). Six related items, each verified against existing foundations: (1) feature
  provenance (elevate the existing batch step-recording to per-feature traceability; reinforces the
  provenance DAG); (2) per-measurement parameter-stability reporting (extends the existing
  benchmark parameter-sweep from masks to derived measurements); (3) a general measurement-
  confidence score combining QC + segmentation + benchmarking; (4) a standing per-release PyCAT
  Validation Suite (built on the existing tests/ fixtures + benchmark harness); (5) a measurement
  ontology (definition/equation/units/reference registry that makes Methods generation nearly
  automatic); and (6) automatic metadata + software-version travel on every output table. These
  converge on a unifying Measurement Reliability Index (MRI): every reported value carries a
  reliability score with a clickable explanation of why it's high or low. Documentation only.
- **Added a roadmap section from cross-evaluating a Cell Painting / image-based profiling review**
  (Nature Methods 2024). Conclusion: don't adopt its measure-everything → ML → latent-space
  direction (against PyCAT's hypothesis → mechanism → physics philosophy); several concepts restate
  the biological object model (state vectors, feature families, object hierarchy = the profiling
  view of it). Genuinely new items captured: feature-family grouping of outputs (currently flat
  columns); a biological-QC layer flagging biological outliers (edge cells, oversegmentation, dead/
  mitotic cells) as a second layer beyond imaging QC; correlation-based feature-redundancy
  reporting; a unified workflow-level analysis-preset system; the "structural profiling" reframe for
  the DoH/FISH work (complementary to phenotypic profiling); and a Feature Explorer — an interactive
  measurement browser (interpretation, definition, units, range, sensitivity, correlations, example
  images) that unifies the measurement ontology, feature stability, redundancy, and QC gallery into
  one interface. Also frames PyCAT's shift from image-analysis package to measurement platform.
  Documentation only.

## [1.5.269] - 2026-07-07
### Docs (roadmap: calibrated thermodynamic & quantitative condensate reporting)
- **Added a roadmap section capturing five capabilities identified by cross-evaluating PyCAT
  against the Punctatools pipeline** (verified against the codebase, not taken at face value).
  Conclusion: don’t adopt the pipeline (PyCAT is already broader), but add: (1) a calibration-
  curve manager converting fluorescence intensity to molar concentration plus real-unit Kp and
  ΔG_transfer = −RT ln(Kp) — the flagship, turning PyCAT into a biophysical-parameter-extraction
  tool; (2) a consolidated per-cell Condensate Thermodynamics Report export preset; (3) explicit
  2D / 3D-z-stack / time-series condensate modes (the in-vitro workflow already flags its volume
  fraction as a 2D-projection proxy); (4) a background-mode UI selector surfacing the scalar /
  mask / local-background support the backend already has; and (5) a positive/negative-control
  validation workflow extending the existing benchmark harness. The stale "integrate PunctaTools"
  note was updated to "adopt the concepts, not the pipeline."

## [1.5.268] - 2026-07-07
### Fixed (macOS startup segfault: torch/Numba warmup raced Qt init)
- **PyCAT could segfault on launch right after "Running PyCAT"** (seen on Apple Silicon macOS),
  after a clean, correct install (native arm64, torch 2.2.x, Cellpose cached successfully). The
  crash was a native-library race: a background thread imported torch and ran Numba JIT warmup at
  the same moment napari/Qt was initialising on the main thread, and those native libraries are
  not safe to initialise concurrently on macOS. Fixed by creating the napari viewer FIRST on the
  main thread, then starting the warmup thread only after Qt has finished its main-thread setup.
  Also added a `PYCAT_SKIP_WARMUP=1` environment variable to disable the background warmup
  entirely as an escape hatch. Note: this is the most-likely fix based on the crash signature;
  confirm on the affected machine.

## [1.5.267] - 2026-07-07
### Changed (clearer Cellpose cache messaging — it was never re-downloading)
- **Reworded the Cellpose model messages so it is obvious the model is downloaded only once and
  cached persistently.** The model was already cached on disk (`~/.cellpose/models`) and reused
  across launches — but the terminal wording ("skipping download", "downloading now") made it look
  like it might re-download every time. Now: a cache hit says the model was found locally and no
  download is needed; a cache miss says the download is a ONE-TIME setup saved for all future
  launches; and the post-download message confirms it won’t happen again. Added a distinct
  "Loading Cellpose model weights from cache into memory (first use this session)" breadcrumb when
  the model is actually loaded during segmentation, so loading-from-disk is clearly separate from
  downloading. No functional change to caching — messaging only.

## [1.5.266] - 2026-07-07
### Fixed (arm-mac install failed with ResolutionImpossible on Python 3.12)
- **`pip install "pycat-napari[arm-mac]"` failed with `ResolutionImpossible` / "no matching
  distribution for torch" on Apple Silicon.** The `[arm-mac]` extra pinned `torch==2.1.2`, but
  torch 2.1.2 has no Python 3.12 wheel (torch added cp312 support in 2.2.0) — while PyCAT itself
  requires Python >=3.12. So the exact pin could never resolve on any supported Python: pip found
  no installable torch and aborted. Changed the pin to `torch>=2.2.0,<2.3.0` (has cp312 arm64
  wheels, and stays within the torch range compatible with the `numpy<2.0` pin). This was a
  packaging bug, not a user error — affected users were on correct native-arm64 Python 3.12
  environments. Surfaced during a multi-user install test.

## [1.5.265] - 2026-07-07
### Changed (Cellpose prewarm: keep it, but guard against the environment that crashes)
- **The Cellpose prewarm is preserved (good first-run UX) but now skips itself only in the
  specific broken state that caused the segfault** — x86_64 Python running under Rosetta
  emulation on an Apple Silicon Mac — rather than being removed. A new architecture guard checks
  `sysctl.proc_translated` (Rosetta flag) and `hw.optional.arm64` vs `platform.machine()`; when a
  mismatch is detected it skips the prewarm with a clear message pointing the user to a native
  arm64 environment. On every healthy environment (native arm64 Mac, genuine Intel Mac, Windows,
  Linux) the prewarm runs as before.
- **The prewarm now selects the model via PyCAT’s version-aware builder**
  (`_build_cellpose_model(default_cellpose_model())`) instead of a hardcoded
  `pretrained_model='cyto2'`. This matters: on Cellpose <4 (the pinned default, fast `cyto2` CNN)
  the correct API is `model_type`, while `pretrained_model` is only a legacy fallback — so the old
  prewarm was using the wrong API path for the common case. The cache-existence check is now
  version-aware too (`cyto2` on Cellpose <4, `cpsam` on >=4), so it no longer always re-downloads
  on Cellpose 4. The subprocess isolation from 1.5.262 is retained as a second safety net for any
  other native crash (e.g. an older CPU without AVX).

## [1.5.264] - 2026-07-07
### Docs (Miniforge installer: tell Mac/Linux users how to run the .sh file)
- **The install steps now explain how to actually run the Miniforge installer per platform.**
  The conda-forge download page hands macOS/Linux users a `.sh` script (e.g.
  `Miniforge3-MacOSX-arm64.sh`), which non-technical users did not know what to do with —
  double-clicking a `.sh` does not run it. Step 2 (README) and a new "Installing Miniforge"
  subsection (installation.rst) now cover: Windows → double-click the `.exe`; macOS/Linux → open
  Terminal and run `bash <path-to-.sh>` (with the tip to drag the file from Finder into the
  Terminal to fill in the path), follow the prompts, then open a fresh terminal. Surfaced during
  the multi-user install test.

## [1.5.263] - 2026-07-07
### Docs (captured multi-user install-test debugging: Mac architecture + failure modes)
- **Added a Mac architecture check and troubleshooting for the issues surfaced during a group
  install test** (README + installation.rst). Key addition: on Apple Silicon, check
  `python -c "import platform; print(platform.machine())"` returns `arm64` (not `x86_64`) before
  installing — an `x86_64` result means Python is the Intel build under Rosetta emulation, which
  causes Intel MKL warnings and Cellpose segfaults. Notes that `uname -m` is unreliable here (it
  can report `arm64` while Python is x86). Also documented, with causes and fixes: the
  Homebrew-conda `libarchive.19.dylib` solver error (use Miniforge), the `llvmlite needs CMake
  tools to build` failure (install llvmlite/numba from conda-forge first), and the "every version
  rejected" symptom (wrong Python version). Updated the platform-support table with real test
  results (Intel Mac now Tested/Works; Apple Silicon note points to the native-arm64 guidance).

## [1.5.262] - 2026-07-07
### Fixed (Cellpose prewarm could segfault the whole app at startup)
- **PyCAT could crash to desktop on launch with a segmentation fault while pre-caching the
  Cellpose model** (`Cellpose model not found in cache ... zsh: segmentation fault`). Loading
  Cellpose pulls in PyTorch / native math libraries that can crash at the C level on some
  machines — notably older Intel CPUs without AVX, where the default AVX-assuming PyTorch/MKL
  binaries hit an unsupported instruction. A C-level crash is not a Python exception, so the
  existing try/except could not catch it, and because the prewarm runs before the QApplication is
  created, the whole app died before the GUI opened. The model load now runs in a SEPARATE
  SUBPROCESS, so a native crash only kills that subprocess — PyCAT still launches. On a
  signal-kill (e.g. SIGSEGV) a clear message explains the likely cause (incompatible PyTorch for
  this CPU) and notes the other segmentation methods (Multi-Otsu, StarDist, Random Forest) still
  work, with a pointer to `conda install -c conda-forge pytorch nomkl`. Known limitation: this
  makes startup crash-proof; if Cellpose is CPU-incompatible, clicking "Run Cellpose" in the GUI
  can still crash (in-process) — isolating that path is a follow-up.
### Fixed (PyCAT branding could silently fall back to napari’s on some installs)
- **The app icon and napari welcome-logo replacement could silently no-op**, leaving napari’s
  default branding — reported across multiple Macs. Both captured a path inside an
  `importlib.resources.as_file()` block but used it after the block exited; as_file() may delete
  its extracted temp file on exit (zipped installs), so the path could be invalid when Qt used it
  — especially the welcome logo, whose QSS `image: url(...)` is read lazily long after startup.
  The window icon is now loaded into a QPixmap inside the as_file() block; the welcome logo is
  copied to a stable per-session temp file (cleaned up at exit) so the QSS url stays valid.

## [1.5.261] - 2026-07-07
### Fixed (reconciled a caller/callee mismatch from out-of-order patching)
- **The time-series pipeline would crash with a `pre_process_image() got an unexpected keyword
  argument norm_max` error.** The working tree had the newer time-series code (which calls
  `pre_process_image(..., norm_max=...)`) but an older `image_processing_tools.py` whose
  `pre_process_image` did not yet accept `norm_max` — the 1.5.242 change was documented in the
  changelog but the code had not fully landed. Re-applied the `norm_max` parameter to
  `pre_process_image` (None = original per-frame 2D behaviour, unchanged; a fixed value = the
  stack global scale for time-series), and the 1.5.249 minimal recorded-step breadcrumb in
  `batch_processor.py`, so caller and callee agree again.

## [1.5.260] - 2026-07-07
### Changed (napari's native menus collapsed behind a toggle, hidden by default)
- **napari's own top-level menus (File / View / Plugins / Window / Help / Layers) are now hidden
  by default and collapsed behind a single leftmost "☰ napari" toggle.** Supersedes the
  File-only hide from 1.5.257. The PyCAT workflow doesn't need napari's native menus, and several
  test users lost their session by loading data through napari's File → Open (which bypasses
  PyCAT's channel-assignment / metadata pipeline and crashes the workflow). Now:
  - **Nothing napari-native is visible on open** — only PyCAT's controls.
  - **The menus are hidden, not removed** — clicking the leftmost **☰ napari** toggle reveals them
    (some napari layer operations are genuinely useful), and clicking again hides them. The toggle
    label shows a ▾ affordance when revealed.
  - **★ Open/Save File(s) is now the first PyCAT menu** (moved ahead of Analysis Methods /
    Toolbox), since loading data is the workflow's entry point. The visible bar reads
    `☰ napari  ◆ PyCAT ▸  ★ Open/Save File(s)  Analysis Methods  Toolbox  …`.
  - **napari's Open* actions stay disabled even when the menus are revealed**, so data always
    loads through PyCAT's reader regardless.
  - Fully defensive: identifies napari-native menus by title, never touches PyCAT's own menus
    (verified no title overlap), and never raises if napari changes its menu layout.

## [1.5.259] - 2026-07-07
### Fixed (ReadTheDocs build was pinned to Python 3.9)
- **`.readthedocs.yaml` build environment updated from Python 3.9 to 3.12.** The docs build does
  `pip install .` (the API reference uses autodoc, so PyCAT must be importable), but the build
  Python was still 3.9 while the package now requires `>=3.12,<3.14` — so the docs build would fail
  to install PyCAT, the same way a user on 3.9 can't. The build now runs on Python 3.12, matching
  `pyproject.toml`. Needed for the corrected installation docs (1.5.256 / 1.5.258) to actually
  publish to the live site.

## [1.5.258] - 2026-07-07
### Docs (Mac Apple Silicon: avoid the llvmlite source-build failure)
- **Added Apple-Silicon install guidance to install `llvmlite` / `numba` from conda-forge before
  pip-installing PyCAT** (README and installation.rst). On some Macs, `pip` can't find a prebuilt
  `llvmlite` (a `numba` dependency) and falls back to compiling it from source, which fails with
  `llvmlite needs CMake tools to build` when compiler tools aren't installed. Installing
  `llvmlite` and `numba` from conda-forge first (they ship prebuilt Apple-Silicon binaries) avoids
  the build entirely:
  `conda install -c conda-forge llvmlite numba` then `pip install "pycat-napari[arm-mac]"`. The
  note also documents the `cmake` fallback (`conda install -c conda-forge cmake llvmlite numba`)
  for the rare case the source build is still attempted. Surfaced during a multi-user install
  test.

## [1.5.257] - 2026-07-07
### Superseded by 1.5.260
- (Hid only napari's File menu. Replaced by the collapsible "☰ napari" toggle in 1.5.260, which
  hides all napari-native menus by default while keeping them reachable. This version was held and
  not released.)

## [1.5.256] - 2026-07-07
### Fixed (stale Python 3.9 references in docs — caused users to build a 3.9 environment)
- **The ReadTheDocs installation guide and conda recipe still instructed users to create a Python
  3.9 environment**, contradicting the actual requirement (`pyproject.toml`: `>=3.12,<3.14`) and
  the main README (which was already correct at 3.12). A multi-user install test surfaced this: a
  user who followed the docs ended up in a Python 3.9 environment. Updated to Python 3.12
  (supported range 3.12–3.13) everywhere:
  - `docs/source/installation.rst` — platform table, minimum-requirements, the compatibility
    warning, `conda create -n pycat-env python=3.12`, and all `python --version` checks.
  - `docs/source/development/support.rst` — troubleshooting "verify Python 3.12 installation".
  - `docs/source/conf.py` — intersphinx now points at the Python 3.12 docs.
  - `meta.yaml` (conda recipe) — `python >=3.12,<3.14`.
  The only remaining "3.9" mentions are the intentional "3.9 is no longer supported as of v1.5.39"
  notes.
### Note
- Unrelated to PyCAT: a Mac user in the same test saw a `conda-libmamba-solver` / `libarchive.19.dylib`
  error from a Homebrew-installed Miniconda (a known Homebrew-conda library-versioning breakage on
  Apple Silicon), while PyCAT itself imported successfully. The recommended path is the Miniforge
  install flow in the README rather than Homebrew's Miniconda.

## [1.5.255] - 2026-07-07
### Docs (generalized the spectroscopy roadmap section for public release)
- **Rewrote the "Advanced Spectroscopy, Correlation & Orientation Methods" roadmap section to be
  hardware-agnostic.** The 1.5.254 version named specific lab instruments and a future
  custom-microscope design; since the roadmap is public-facing (ReadTheDocs), those details were
  replaced with capability-based framing (e.g. "a fast sCMOS + TIRF/HILO," "point-detector
  confocal," "polarization optics," "a FLIM-capable instrument") rather than instrument names or
  future build plans. The technical content, sequencing (by data availability), reuse-of-existing-
  machinery notes, and manuscript framing are unchanged.

## [1.5.254] - 2026-07-07
### Docs (roadmap: advanced spectroscopy / correlation / orientation methods)
- **Added a dedicated "Advanced Spectroscopy, Correlation & Orientation Methods" section to the
  roadmap** (`docs/source/development/roadmap.rst`), capturing the instrument-scoped plan for a
  family of quantitative fluorescence techniques PyCAT doesn't yet analyse. Organized around the
  positioning that PyCAT is the downstream quantification layer for specialised acquisition
  instruments (import-and-analyse, don't reimplement acquisition), and scoped to the lab's actual
  instrument base (Lumicks C-Trap, ISS Q2, Andor Dragonfly + iXon 888 EMCCD / Zyla sCMOS, campus
  Stellaris/STED, incoming Airyscan 2, Kinetix). Covers: FCS/FCCS (Q2), RICS/STICS (scanning
  confocals, highest near-term leverage), imaging camera-FCS (sCMOS/Zyla or future Kinetix; notes
  why the EMCCD is the weaker FCS detector), FLIM phasor downstream (Q2), ratiometric/spectral,
  fluorescence anisotropy/homo-FRET, PolScope orientation, and SMLM localization-table analysis
  (cross-referenced to the existing Super-resolution Category B rubric). Sequenced by data
  availability today vs. future hardware, with the "what composes with existing modules" note for
  each. The existing FCS/FCCS stub under Advanced Methods now cross-references the new section.

## [1.5.253] - 2026-07-07
### Fixed (lazy TIFF wrapper broke analysis that materialises the whole stack)
- **Regression from the 1.5.245 OME-TIFF scrubbing fix.** The lazy `_TiffPageStack` reader's
  `__array__` deliberately returns only the FIRST frame (so napari's incidental array/thumbnail
  requests don't materialise the whole stack — that truncation, plus pinned contrast_limits, is
  what made scrubbing smooth). But analysis code that did `np.asarray(layer.data)` to get the
  full `(T, H, W)` stack then silently received a single 2D frame — so shape checks saw `ndim==2`
  and bailed. This broke the temperature workflow's **"guess reference frame"** ("Reference-frame
  guessing needs a (T, H, W) stack") and the same pattern in its sync / pattern-correction /
  analysis steps.
  - Added `_TiffPageStack.as_full_array()` (reads every frame, one at a time) and a module-level
    `materialize_stack()` helper that safely turns any stack-like layer data (lazy wrapper, dask,
    or plain array) into a real `(T, H, W)` array — the correct call for analysis that needs all
    frames.
  - The temperature UI's four stack-reading sites now use `materialize_stack()` instead of
    `np.asarray()`. `__array__` still returns one frame, so napari display stays fast.
  - Verified: `np.asarray(wrapper)` gives `ndim==2` (the bug) while `materialize_stack(wrapper)`
    gives the correct `ndim==3` stack, byte-identical to the source; plain arrays pass through.
### Note
- The pixel-size regression on the same file was addressed in 1.5.253's companion fix (stale
  `pixel_size_from_metadata` flag, see 1.5.252). A separate, deeper issue was noticed for
  follow-up: the file_io load path computes a pixel size (with TIFF-tag recovery) but then
  `update_metadata()` re-reads `physical_pixel_sizes` independently and can overwrite it with the
  1.0 fallback for Micro-Manager OME-TIFFs — the two metadata paths should be reconciled.

## [1.5.252] - 2026-07-07
### Fixed (pixel-size gate hidden on an unscaled image after a stale metadata flag)
- **The pixel-size gate now correctly appears when an image loads without a real physical pixel
  size** (e.g. a Micro-Manager OME-TIFF whose resolution metadata is incomplete, where the
  loader falls back to 1 µm/px² and warns "Resolution data incomplete, using default value of
  1"). The metadata-provenance flag `pixel_size_from_metadata` was set correctly on the normal
  and incomplete-metadata paths, but the two exception fallbacks in `update_metadata` set the
  default scale **without clearing the flag** — so a `True` left over from a previously-loaded,
  properly-scaled image made the gate think this image had a real scale and stay hidden. All
  fallback paths now set `pixel_size_from_metadata = False`, so an unscaled image always prompts
  for the pixel size. (`_valid_scale()` already treated a bare 1.0 as invalid; the bug was purely
  the stale provenance flag.)

## [1.5.251] - 2026-07-07
### Changed (README Miniforge download link)
- **The Miniforge install step now links to the official [conda-forge download
  page](https://conda-forge.org/download/)** (per-platform installer picker) as the primary
  download, with the [Miniforge GitHub page](https://github.com/conda-forge/miniforge#miniforge3)
  kept as an alternative for the installers and detailed instructions.

## [1.5.250] - 2026-07-07
### Added (Segmentation Benchmark harness)
- **New "Segmentation Benchmark" diagnostic tool** (Image Processing menu) — a general
  comparison harness for manuscript preparation. Runs several segmentation candidates on the
  same image and reports metrics as a pasteable markdown table plus in-app side-by-side mask
  layers (`bench: <name>`). One framework covers three uses:
  - **Method comparison** — run built-in methods (Otsu, Multi-Otsu, Sauvola, Felzenszwalb,
    watershed, Cellpose) on one image; compare object count, area, runtime, and pairwise overlap.
  - **Ground-truth validation** — mark any candidate as ground truth; the others are scored
    against it.
  - **Parameter sensitivity** — supply the same method at different parameters as candidates and
    read the trend.
  - **External / uploaded masks are first-class candidates.** Any Labels layer (a mask exported
    from another tool, or a manual annotation) can be included in the comparison, so PyCAT's
    segmentation can be benchmarked directly against other tools on identical data — useful for
    puncta segmentation comparisons in particular.
  - **Two metric families shown side by side, without privileging either:** pixel-overlap
    (Dice / IoU) and matched-detection (precision / recall / F1 via Hungarian centroid matching,
    plus mean localisation error). This matters for puncta: two tools can agree on *which* spots
    exist (high F1) while their pixel masks differ (lower Dice) due to sub-pixel offset — both
    columns tell the honest story.
  - **Match tolerance** for detection metrics is either auto-scaled to a fraction of the mean
    object radius (default) or a fixed pixel radius.
  - New module `benchmark_tools.py` (candidates, both metric families, three modes, markdown
    report); verified on synthetic puncta that detection F1 and pixel Dice correctly diverge for
    a spatially-offset detector, and end-to-end with built-in method runners.
### Note
- The harness counts connected components, so touching puncta merge into one object (affects all
  methods equally in a comparison). Runs on a single 2D image (pick one frame/plane).

## [1.5.249] - 2026-07-07
### Fixed (recorded-steps list didn't reset on Clear)
- **The batch recorded-steps list now resets on the plain Clear button**, not just Save & Clear.
  Previously `_clear_everything` (the shared reset used by the top-bar Clear and by Save &
  Clear's discard option) reset layers, dataframes, and the workflow checklist but left the
  batch recording intact, so a new dataset started with the previous dataset's recorded steps
  still listed. It now calls `clear_recording()`, which empties the recorded steps, flips the
  record toggle back to OFF (red), and resyncs the toolbar. Save & Clear still offers to export
  the config first before resetting.
### Changed (quieter recording output)
- **Removed the verbose per-step recording dump from the terminal.** Each recorded step used to
  print its full parameter dict (including layer snapshots) to the console; now that the "☰
  Recorded Steps" viewer shows the step name, parameters, and snapshots, that dump was redundant
  noise. Recording now prints a short one-line breadcrumb per step (`Recorded step N: <name>`)
  so the recorder isn't silent, and the full detail lives in the viewer.

## [1.5.248] - 2026-07-07
### Changed (README reorganized for low/no-code users)
- **Reworked the README install flow to reduce friction for non-technical users** (ahead of a
  group test-installation session):
  - **Miniforge-first, single top-to-bottom path.** "Getting Started" is now the one install
    path, as four numbered steps: Install Miniforge → create a workspace → install PyCAT →
    launch. A new user can't skip setup by clicking a separate "Installation" link.
  - **Removed the standalone "Installation" table-of-contents entry** that let impatient users
    jump past the Python/terminal setup; the TOC now points everyone into the guided Getting
    Started steps (with Miniforge, workspace, install, and launch as sub-items).
  - **`run-pycat` promoted to Step 4**, immediately after install and **before** GPU
    acceleration, with a page break after it, so the first thing a user reaches is a working
    launch — not optional speed tuning.
  - **Advanced/optional material is now collapsible** (`<details>` blocks): GPU acceleration,
    optional add-on packages, Cellpose model choice, dependency pin rationale, alternative
    install, and verification — so the main path isn't visually overwhelming, but the detail is
    one click away.
  - Added beginner-friendly explanations (what an environment is, how to confirm each step
    worked, a reminder to `mamba activate` next time) written for readers who don't know Python
    or the terminal.

## [1.5.247] - 2026-07-07
### Changed (time-series first-run speedup — skip the source pre-copy)
- **Time-series analysis no longer pre-copies the input stack to a temp zarr when the source is
  a TIFF or an existing filesystem zarr (e.g. IMS).** Previously, before any processing, every
  frame was read from the source and written to a temporary float32 zarr so the parallel workers
  could open it by path — then each worker re-read those frames. On a first run (the debugging
  case) that meant reading every frame twice and writing it once purely as copy overhead, before
  real work began. Workers now read frames directly:
  - **TIFF** (via the `_TiffPageStack` reader): each worker opens its own `tifffile` handle and
    seeks to its page — no whole-stack copy.
  - **Filesystem zarr / IMS-derived**: used directly, as before.
  - **Other sources** (numpy, dask, non-seekable): still materialised to a temp zarr (unchanged).
  - The global-range normalisation the copy used to apply is preserved — computed once up front
    (a cheap frame-at-a-time min/max pass) and applied inside each worker, so intensity trends
    across time are still preserved. Verified byte-equivalent to the old copy-then-read path on a
    synthetic brightening-focus stack.
  - **Safe fallback:** if a direct TIFF read fails mid-run (locked file, network hiccup,
    unexpected page layout), the run materialises the source to a temp zarr and retries once.
  - The preprocessed and background-removed output stacks are still written and shown as layers
    exactly as before — only the redundant *input* copy is removed.
  - Pseudo-3D temporal pre-pass (opt-in) still materialises a zarr when enabled, since it needs
    the whole stack as an array anyway.
- This is the first module to get the source-copy skip; other modules can adopt the same
  `_source_descriptor` pattern later.

## [1.5.246] - 2026-07-07
### Fixed (TIFF lazy reader crashed on slice indexing)
- **"Failed to open stack: int() argument must be … not 'slice'"** — the new `_TiffPageStack`
  lazy TIFF reader (1.5.245) assumed the time index was always a scalar and did `int(t_idx)`,
  which crashed when napari or downstream code indexed the T axis with a slice (`[:]`, `[10:15]`)
  or a list/array. It now handles all indexing patterns: scalar int (the fast single-page
  scrubbing path), numpy integer types, slices (reads the requested frame range), fancy
  list/array indices, and any of these combined with a spatial sub-index. Verified against the
  full set of napari access patterns.

## [1.5.245] - 2026-07-07
### Fixed (laggy scrubbing through TIFF/OME-TIFF time-series — corrected approach)
- **TIFF/OME-TIFF (incl. Micro-Manager MMStack) time-series now scrub smoothly, staying fully
  lazy.** Two independent causes were fixed, keeping the intended design (open the file once,
  read exactly one frame per slider move — no eager copy, no materialisation):
  - **Whole-stack read on every slider move (main cause):** the generic-stack layers were added
    without pinned `contrast_limits`, so napari auto-estimated the display range by calling
    `np.asarray()` on the lazy wrapper — which read the ENTIRE stack — on each frame change. The
    TIFF/CZI paths now pin `contrast_limits` from the first frame (the IMS path already did
    this), so navigation never triggers a whole-stack read.
  - **Slow per-frame reads through AICSImage:** a Micro-Manager OME-TIFF read via AICSImage's
    dask reader walks the OME plane-map on every frame, so scrubbing a large MMStack lags even
    when only one frame is requested. TIFF time-series now read frames straight from the
    multipage TIFF via a new lazy `_TiffPageStack` wrapper (`tifffile` per-page seek — one page
    per read, no dask graph, no copy), matching the smooth per-frame behaviour of the native IMS
    zarr path. The wrapper prefers the OME **series** page sequence so it spans multi-file
    MMStack sets (`_1.ome.tif`, `_2.ome.tif`, …); it falls back to the AICSImage reader if the
    page layout is ambiguous (e.g. an unmodelled multi-channel order) or tifffile can't open the
    file. CZI keeps the AICSImage path.
### Reverted
- The v1.5.244 approach (materialising the whole stack to a local float32 zarr on load) is
  removed: it defeated the lazy-loading design, and for an 8-bit 3800-frame MMStack it would
  have written ~23.6 GB (4x the 5.9 GB source) to disk up front. The corrected fix above keeps
  reads lazy and one-frame-at-a-time.

## [1.5.244] - 2026-07-07
### Superseded by 1.5.245
- (Materialise-to-zarr approach — replaced by the lazy `_TiffPageStack` reader + pinned
  contrast_limits in 1.5.245.)

## [1.5.243] - 2026-07-07
### Added (Temporal Enhancement Optimizer)
- **New "Temporal Enhancement Optimizer" diagnostic widget** (Image Processing menu) that
  competes temporally-aware enhancement strategies against a loaded time-series and picks the
  one that best preserves the true intensity trend across frames. Motivation: per-frame
  CLAHE/LoG normalization is per-frame adaptive — consistent across XY but not across time — so
  in a correlated time-series a brightening focus can appear to dim, and dim condensates drop
  out once a brighter one enters the field.
  - Strategies competed: `per_frame` (baseline control), `pooled_stats` (nn/nnn — scale from the
    pooled temporal window, enhance each frame's own pixels), `windowed_mean` (temporally-
    weighted average then enhance), and `triplanar` (tri-planar XY/XT/YT coupling).
  - Each is scored by trend preservation (Spearman rank correlation and direction-of-change
    agreement between the raw and enhanced per-frame condensate signal), with a light cost
    penalty so the cheapest method that does the job wins ties. Results are shown as a ranked
    table and the winning enhanced stack is added as a layer for inspection.
  - Window is optimized against the data by default (competes ±1 and ±2); a "Set window
    manually" checkbox reveals a spin box to override.
  - A validity warning notes temporal enhancement is only valid with adequate frame-to-frame
    correlation; a "Check temporal correlation" button runs the estimator and hides the warning
    if the data is in a correlated (oversampled/moderate) regime.
  - "Apply winner as session default" stores the choice; a tri-planar/windowed winner is honored
    by the time-series preprocessing step via the existing pseudo-3D temporal path.
  - New module `temporal_enhancement_tools.py` (methods + scoring); verified that the scoring
    correctly ranks trend-preserving enhancement above per-frame normalization on a synthetic
    growing-focus stack.
### Note
- Full pipeline integration of the per-frame-worker strategies (`pooled_stats`, non-triplanar
  windowed) is staged for a follow-up; the optimizer itself runs standalone and produces the
  enhanced layer plus the winning configuration now.

## [1.5.242] - 2026-07-06
### Fixed (time-series: preprocessing re-normalized every frame per-frame, dimming later frames)
- **The preprocessing/background-removal worker no longer re-normalizes each frame by its own
  min/max.** Even after 1.5.240 put the *source* frames on one global [0,1] scale,
  `pre_process_image` still divided each frame by its own max internally, and the worker did a
  second per-frame min/max normalization before background removal. Both reintroduced the
  intensity-trend distortion: as condensates brighten over time, the per-frame max (the
  denominator) rises, so later frames appear DIMMER in the preprocessed/enhanced-background
  stack even though the raw condensates are brighter (the reported "frame 4 dimmer than frame 3"
  and dim condensates dropping out once something brighter appears).
  - `pre_process_image` gained an optional `norm_max` parameter. When `None` (all 2D callers),
    behavior is byte-identical to before. The time-series worker passes the stack's global max,
    so every frame is normalized by the same scale.
  - Removed the redundant second per-frame normalization in the worker.
  - Verified: 2D path unchanged (norm_max=None); time-series frames now share one scale,
    preserving the true intensity trend across time.

## [1.5.241] - 2026-07-06
### Fixed (time-series puncta segmentation now matches the 2D fluorescence path)
Puncta detection in the time-series workflow was weaker than the validated 2D path because two
steps differed. Both are now aligned (segmentation correctness before speed):
- **Per-cell contrast stretching (`cell_mask_stretching`) is now applied in the time-series
  path**, as it is in 2D. The 2D puncta pipeline computes
  `CMS_img = cell_mask_stretching(preprocessed, cell_masks)` and segments puncta on that
  stretched image; the time-series path was passing the plain preprocessed frame instead. Both
  the parallel and serial time-series workers now compute the same per-cell stretched image per
  frame (using the per-frame cell mask) and pass it to `segment_subcellular_objects`, so puncta
  detection matches 2D.
- **`min_spot_radius` is no longer ignored during refinement.** `segment_subcellular_objects`
  accepted a `min_spot_radius` argument but then called `puncta_refinement_func(...,
  min_spot_radius=2)` with a hardcoded 2, so the UI/parameter value was silently dropped during
  the refinement step. It now passes the actual `min_spot_radius` through. **This is
  output-preserving at the default:** every UI ships `min_spot_radius = 2`, and passing 2
  through is byte-for-byte identical to the old hardcoded 2 (verified across all four internal
  uses — the two Gaussian sigmas, the gradient-magnitude sigma, and the min-area computation;
  int 2 and float 2.0 give identical results). Behavior only changes for users who deliberately
  set a non-default value, where the parameter now takes effect as intended (this applies to
  both 2D and time-series).
### Notes
- This deliberately does NOT re-enable the earlier "make TS refinement like 2D" change or touch
  Cellpose model handling. The cell/body mask path (e.g. cyto2 without refinement) is unchanged;
  only the puncta path was aligned. Cellpose is not used for puncta.
- Drift-correction vs per-frame-mask interaction and transfection-filter ordering are noted for
  follow-up but not changed here.

## [1.5.240] - 2026-07-06
### Fixed (time-series: per-frame normalization erased/inverted intensity trends)
- **Time-series frames are now normalized against ONE global range, not per-frame.** The
  per-frame min/max normalization in `_read_source_frame` made a growing focus appear to
  plateau or decay: as foci brighten over time, the per-frame max (the normalization
  denominator) rises, shrinking the normalized value of a focus even as its raw intensity
  increases. On real data (diffuse mCherry that condenses into foci which grow brighter/bigger)
  this produced a spurious "peak at frame 3, decay in frames 4–5" instead of the true monotonic
  increase.
  - Added `_compute_stack_global_range()` (reads one frame at a time — never holds the whole
    stack in RAM) and a `global_range=` option on `_read_source_frame()`.
  - The source-zarr materialization (feeding preprocessing → background removal → analysis),
    the general stack→zarr helper, and the upscale step now all normalize against the stack's
    global min/max, preserving true intensity trends over time.
  - Verified on a simulated growing focus: per-frame normalization flattened it to a constant;
    global normalization recovered the correct increasing trend.
  - Frame-to-frame temporal-correlation reads are left per-frame (correlation is scale-invariant
    there, so it's unaffected).

## [1.5.239] - 2026-07-06
### Fixed (time-series "Check if upscaling is needed" crash + plot event-loop warning)
- **"Check if upscaling is needed" no longer crashes** with ``AttributeError: 'ToolboxFunctionsUI'
  object has no attribute '_dr'``. The upscale step (added in 1.5.229) used ``ui._dr()`` /
  ``ui._mpx()`` helpers that only exist on the in-vitro UI classes, but in the time-series flow
  the UI is ``ToolboxFunctionsUI``. Switched to the correct
  ``central_manager.active_data_class.data_repository`` access (and read
  ``microns_per_pixel_sq`` from there for the upscaled layer's scale). The check, factor
  recommendation, and lazy upscale now work in the time-series workflow.
- **Quieted the "QCoreApplication::exec: The event loop is already running" warning** from the
  time-series condensate-fraction plot: ``plt.show()`` → ``plt.show(block=False)`` so it doesn't
  try to start a second Qt event loop inside napari's running one.
### Note
- The ``RuntimeWarning: Mean of empty slice`` / ``invalid value in divide`` messages during
  analysis are benign — they come from cells with no puncta (the "low contrast, likely has no
  puncta" cells), where per-cell statistics are legitimately NaN. The analysis completes
  correctly; these are console noise, not errors.

## [1.5.238] - 2026-07-06
### Fixed (pixel-size gate appeared on Clear with no image)
- **The pixel-size gate no longer pops up after Clear when no image is loaded.** The gate is
  only meaningful when an image lacking scale metadata is present; after a Clear there are no
  image layers, so it now stays hidden. The gate checks the viewer for any Image layer before
  showing (failing open if it can't determine, so it never hides when actually needed).

## [1.5.237] - 2026-07-06
### Changed (recording toggle — colored status circle)
- **The batch recording toggle now shows a colored circle** reflecting its state: 🔴
  "Record" when idle (off, ready to start) and 🟢 "Recording" when actively capturing steps.

## [1.5.236] - 2026-07-06
### Changed (batch recording toggle moved to the PyCAT toolbar)
- **The start/stop recording toggle is now in the PyCAT toolbar** (left of "Save Config"),
  not buried in the Batch dialog — so you can turn recording on before clicking through your
  workflow. It shows "⏺ Record" when off and "⏺ Recording" (checked) when on, and stays in
  sync after a Save & Clear resets recording to off.
- **The PyCAT toolbar is now grouped into labelled sections**: a **Batch:** section (Batch
  Run, Record, Save Config) is separated from a **Layers:** section (show/hide Layers, Gray/
  Viridis colormap toggle) by a divider, so the batch controls are visually distinct from the
  layer-view controls.

## [1.5.235] - 2026-07-06
### Changed (batch recording — off by default, opt-in)
- **Batch recording now starts OFF** and is opt-in via the start/stop toggle, so exploratory
  clicking isn't captured before the user decides to record a workflow. Recording also resets
  to OFF after a Save & Clear (dataset boundary) — the user re-enables it with the toggle when
  they want to record again. (Matches the normal usage of recording a workflow once per
  session, then batch-replaying it.)
### Added (Recorded Steps viewer)
- **New "☰ Recorded Steps" menu-bar panel** (next to Metadata). Shows the batch workflow
  recorded so far as an expandable tree: each step (number, name, timestamp) expands to reveal
  the layers/parameters it captured, with the internal layer-snapshots shown separately at the
  end. Includes a recording-status indicator and expand/collapse-all controls, so the user can
  review exactly what will be replayed before running a batch.

## [1.5.234] - 2026-07-06
### Fixed (Save & Clear crash — UnboundLocalError, regression from 1.5.225)
- **Save & Clear (and saving images generally) no longer crashes** with
  ``UnboundLocalError: cannot access local variable 'QFileDialog'``. The batch export-prompt
  added in 1.5.225 did a local ``from PyQt5.QtWidgets import QFileDialog`` inside
  ``save_and_clear_all``; because Python scopes that name as local for the whole function, the
  earlier ``QFileDialog.Options()`` call failed before the local import ran. Removed the
  redundant local imports (``QFileDialog``, ``QMessageBox``, ``QCheckBox`` are all imported at
  module level), restoring Save & Clear and image saving.
### Added (batch recording start/stop toggle)
- **Start/stop recording toggle in the Batch dialog.** A button pauses/resumes step recording
  without clearing what's already recorded — useful for skipping exploratory steps that
  shouldn't be part of the saved workflow. Reflects and drives the existing
  ``recording_enabled`` flag (which ``record()`` already honors).

## [1.5.233] - 2026-07-06
### Added (In Vitro Brightfield — "Invert + reconcile" segmentation)
- **New "Invert + reconcile" method** for the dense/out-of-focus regime, from a tester's
  suggestion to invert the image before processing. Brightfield/phase condensates flip contrast
  depending on which side of focus they're on — some are bright-centred, others dark-centred —
  so a single polarity misses roughly half. This method runs a polarity-specific detector
  (white top-hat) on BOTH the image and its inversion, **unions** the two masks to catch
  condensates of either contrast, watershed-splits, then **drops oversized objects** (merged
  background/debris) using the Max diameter setting.
  - Verified on real dense brightfield data: the inverted polarity surfaced ~27% additional
    droplet area that the original polarity missed entirely.
  - Note: the texture (local-std) and DoG methods are already polarity-invariant (variance /
    |difference| based), so inversion doesn't change them — the reconcile trick specifically
    helps the intensity/top-hat family, which is what this method uses.

## [1.5.232] - 2026-07-06
### Fixed (In Vitro Fluorescence — absurd per-droplet partition coefficients)
- **Per-droplet partition coefficients no longer blow up to ~1e8.** `partition_coefficient_field`
  estimated the bulk (dilute-phase) intensity as the 10th percentile of background, which
  collapses to ~0 on dark fluorescence backgrounds; every per-droplet partition was then
  `intensity / ~0`. It now uses a robust bulk (falls back to the background mean when the
  percentile is degenerate, with a final divide-by-zero floor), putting per-droplet values on
  the same sensible scale as the field-level partition (which already used the mean).

### Changed (In Vitro Brightfield — segmentation consistency)
- **Texture method now uses a LOCAL-ADAPTIVE threshold** instead of a single global Otsu on the
  texture map. The global threshold made segmentation inconsistent across regions of identical
  texture — dense areas fused into one giant blob while others dropped out entirely. A local
  threshold judges each neighbourhood against its own surroundings, so uniform-texture regions
  break into individual droplets consistently. (No more giant merged blobs on the test image.)
- **New "Blob detection (DoG)" method.** Difference-of-Gaussians responds to individual
  droplet-scale blobs rather than thresholding connected high-texture regions, so it cannot
  produce the "one giant blob" undersegmentation and gives the most consistent per-droplet
  output. Sigmas scale with the expected droplet radius.
- Both texture and DoG share watershed splitting for touching droplets.
### Fixed (deprecation)
- **`remove_small_objects` no longer triggers the `min_size` deprecation warning.** A
  version-compatible helper uses the new `max_size` argument (skimage ≥ 0.26) with a fallback
  to `min_size` for older versions.
### Docs
- Recorded the brightfield-segmentation cross-regime generalization task (sparse+large,
  small+sparse, large+dense semi-overlapping, fractal/irregular aggregates) and the planned
  "guess the condition" button in the roadmap — to be implemented only once representative test
  data is supplied per regime.

## [1.5.231] - 2026-07-06
### Added (In Vitro Brightfield — texture-based segmentation for dense/defocused droplets)
- **New "Texture (edges/rings)" segmentation method for brightfield droplets**, optimized
  against real dense small-condensate data. Brightfield/phase droplets — especially
  out-of-focus ones — appear as rings (dark rim + bright centre) with little net brightness
  difference from the mid-grey background, so the legacy intensity threshold merges background
  or misses them (measured median solidity ~0.6 with one giant merged-background blob). The
  texture method segments by local intensity variation (local standard deviation): high
  wherever there's a droplet edge/ring, thresholded, hole-filled (ring → disc), and optionally
  watershed-split for dense touching droplets. On the test image this gave clean per-droplet
  masks (median solidity ~0.92) capturing both in-focus spots and defocused rings.
  - New `segment_bf_condensates(method='texture'|'intensity', texture_window, split_touching)`;
    default in the UI is now **Texture** (Intensity remains available for preprocessed
    bright-blob images).
  - UI: method dropdown in Step 3 with texture window + watershed-split controls (shown only
    for the texture method).
### Note
- Optimized on a single dense-defocused-droplet image; the texture method is the better
  default for that regime, but the intensity method is kept for images where droplets are
  uniformly brighter than background after preprocessing.

## [1.5.230] - 2026-07-06
### Fixed (pixel-size gate — premature hide + no reappear after Clear)
- **The pixel-size gate no longer vanishes mid-entry.** It previously auto-applied and hid the
  instant a valid number appeared, so it disappeared while you were still typing (e.g. at
  "0.0" before you finished "0.0957"). It now shows a confirmation — "Is xx.xx µm/px the
  correct scale?" — with the value editable, and only hides after you press **Confirm pixel
  size**. Changing the value after confirming re-arms the prompt so the new value must be
  reconfirmed. The "Keep this pixel size for the session" checkbox remains in the panel.
- **The pixel-size gate reappears after Clear.** Clearing wipes the scale from the data
  repository, but the gate wasn't re-evaluating, so it stayed hidden. Clear now resets the
  gate: with "keep for session" unchecked it reappears for the next dataset; with it checked
  the remembered value is re-applied and the gate stays hidden (as intended).

## [1.5.229] - 2026-07-06
### Added (time-series: standalone early upscale step)
- **The time-series workflow now has an optional early "Upscale Stack" step**, placed before
  preprocessing to match the 2D cellular order (load → ROI → upscale → preprocess → segment
  nuclei → segment condensates). Previously upscaling only happened inside the Cellpose call
  and was rescaled away, so downstream analysis ran at original resolution.
  - **Lazy / zarr-backed**: frames are upscaled one at a time into a zarr store on disk and
    presented as a lazy `_ZarrStack`, so the result is snappy (frames read on demand) like the
    rest of the TS pipeline, and the full upscaled stack is never held in RAM.
  - **Optional and gated**: a "Check if upscaling is needed" button compares the current cell
    diameter against Cellpose's ~30 px preferred minimum and recommends a factor (or says
    upscaling isn't needed if the data already meets it).
  - Downstream `cell_diameter` and `ball_radius` are scaled by the upscale factor so
    Cellpose and background-removal parameters stay correct.
  - Added to the workflow checklist as step 4 (subsequent steps renumbered).

## [1.5.228] - 2026-07-06
### Fixed (time-series condensate analysis crash: empty per-frame cell mask)
- **`IndexError: index 0 is out of bounds for axis 0 with size 0` in
  `segment_subcellular_objects` is fixed.** When the cell-label set is taken as the union
  across all frames (from a (T,H,W) mask), a given cell can have zero pixels in a particular
  frame; the crop optimisation then ran `np.where(rows)[0][[0, -1]]` on an all-False mask and
  crashed. Now:
  - `segment_subcellular_objects` guards the empty-mask case and returns empty results instead
    of indexing into an empty array.
  - The time-series analysis loop (both parallel and serial paths) skips cells with no pixels
    in the current frame, so absent cells are cleanly ignored rather than segmented.
  This is independent of upscaling — the crash could occur whenever a cell was missing from a
  frame, regardless of the preprocessing path.

## [1.5.227] - 2026-07-06
### Fixed (In Vitro Fluorescence — tester feedback)
- **Random Forest no longer produces empty masks.** Root cause: the RF classifier runs CLAHE
  (`equalize_adapthist`), which requires float input in [-1, 1], but the raw fluorescence
  image is in raw intensity units — CLAHE raised "Images of type float must be between -1 and
  1", was swallowed by the worker, and surfaced as an empty mask. The image is now normalized
  to [0, 1] before the RF call. Verified RF then produces a proper droplet mask.
### Changed (In Vitro Fluorescence)
- **Step 2 preprocessing is now optional with gentler methods.** Rolling-ball background
  subtraction could hollow out large droplets (the donut problem). The step is now labeled
  optional and offers Gaussian blur (default — keeps interiors solid), LoG edge enhancement,
  or rolling-ball (legacy). Segmentation can run directly on the raw image if preprocessing is
  skipped.
- **Steps 7 (Dynamics) and 9 (Frame Quality / bleaching) are hidden unless a stack is loaded.**
  These only apply to 2D+t or 3D data; they're shown/hidden automatically based on whether any
  loaded image layer has ≥3 dimensions, re-evaluated on layer changes.
- **Step 4 "volume fraction" clarified as an area fraction.** Φ is the fraction of the imaged
  *plane* covered by droplets, not a true 3D volume fraction — in a flow cell, droplets settle
  into the bottom few µm of a ~200 µm channel, so single-plane Φ doesn't represent bulk volume
  fraction. The step note now says this explicitly.
- Sauvola remains available but non-default (it's noise-sensitive on clean in-vitro fields,
  producing irregular fragments in dark background); the min-object-size and optional
  round-object filters help suppress that debris.

## [1.5.226] - 2026-07-06
### Changed (In Vitro (Fluorescence) — simplified droplet segmentation)
- **Step 3 segmentation redesigned around a radio-button method selector**, showing only the
  chosen method's parameters (via a stacked panel) instead of exposing all six at once. Based
  on optimization against real FUS-PLD in-vitro data (clean, well-separated droplets), where a
  simple global threshold gives round, well-segmented objects (solidity ~0.95) and the heavy
  rolling-ball/kurtosis/SNR pipeline is unnecessary. Methods:
  - **Threshold (Otsu)** — default, zero-parameter (with an optional sensitivity ×multiplier).
    Matches what the data wants and what the user asked for.
  - **Multi-level threshold (Multi-Otsu)** — choose number of classes + cut at lower (inclusive)
    or upper (bright cores) boundary; good for core/halo droplets.
  - **Local threshold (Sauvola)** — window + k, with better defaults (win=35, k=0) than before.
  - **Random Forest** — with a **"Draw Scribbles" button** that creates/selects a labels layer
    and arms the paint tool. (Paint 1 = background, 2 = droplet, matching the classifier's
    label handling.)
  - **Advanced: spot detection (kurtosis / SNR)** — the original rolling-ball pipeline, preserved
    but tucked behind its own radio option so it's out of the way.
- **Shared post-filters**: a single "min object size (px²)" control (replacing the confusing
  "min spot radius") and an optional "reject non-round objects (solidity < 0.85)" filter suited
  to droplet data.

## [1.5.225] - 2026-07-06
### Fixed (batch recording — structural fixes, adapted from Christian's audit patch)
- **Save-and-Clear now ends the batch recording** instead of letting the next dataset's
  steps accumulate onto the previous one. Because the batch config is only written when you
  click "Save Config", Save-and-Clear first checks for unsaved recorded steps and — unless
  silenced — prompts to export the config (with a "Don't ask again this session" checkbox),
  then resets the recorder. This prevents both the "steps bleed across datasets" bug and
  accidental loss of an unexported recording.
- **Split-channel file loads are recorded and replayed correctly.** When a workflow is
  recorded by opening two separate files as channels (e.g. `cell01_DAPI.tif` +
  `cell01_GFP.tif`), the open step now records `source_files` and each channel's
  `source_stem`/`source_suffix`. Batch mode detects the split-file workflow, processes only
  the primary file per sample (instead of double-counting every file), and during replay
  derives each companion file for the current sample (`cell17_DAPI` → `cell17_GFP`),
  raising a clear error if a companion is missing.
- **`.ims` added to the batch-supported extensions.**
- **Recorded steps now carry a layer snapshot** (`_active_layer_at_record`,
  `_all_layers_at_record`) to help diagnose steps that captured the wrong dropdown layer
  name.
- Added a `recording_enabled` guard on the recorder.
### Note (not yet addressed)
- Some GUI callbacks still record dropdown layer names *after* the operation has changed
  viewer state; those per-widget captures need individual fixes (the layer snapshot above is
  the diagnostic aid for finding them). Applied manually rather than via `git apply` — the
  patch didn't apply cleanly against the current tree (which has drifted), and its
  Save-and-Clear hunk needed the export-prompt guard added to avoid wiping unsaved
  recordings.

## [1.5.224] - 2026-07-06
### Fixed (1.5.222 regression — ImportError on startup)
- **Restored `_add_run_ts_cellpose`**, which was accidentally deleted when the transfection
  filter functions were added before it in 1.5.222 (the insertion consumed the function's
  `def` line, leaving an orphaned body). The file still compiled — valid syntax — so the
  missing symbol only surfaced at import time as
  ``ImportError: cannot import name '_add_run_ts_cellpose'`` when launching. The function is
  back at module scope alongside the transfection helpers; verified by AST symbol check, not
  just a compile check.

## [1.5.223] - 2026-07-06
### Fixed (hollow "donut" segmentation of very large condensates — contributed by Christian Neureuter)
- **Large condensates (e.g. SS18 PLD) are no longer segmented as hollow rings.** The
  upstream ball_radius-scale enhancement is a band-pass that suppresses the flat interior of
  condensates much larger than the puncta scale, leaving only a fragmented rim; local
  Niblack/Sauvola thresholding then captured only a broken "necklace" ring. Four coordinated
  changes (merged from Christian's updated ``segmentation_tools.py`` +
  ``image_processing_tools.py``, both based on the current tree so no recent work was
  reverted):
  - **Absolute-brightness rescue** in ``fz_segmentation_and_binarization``: an Otsu
    whole-image threshold is OR-combined with the local threshold to recover the flat,
    saturated interior of large condensates that local contrast-based thresholding misses.
    OR-only, so it never reduces small/medium puncta sensitivity.
  - **Rim bridging**: a small, FIXED-scale morphological closing (``rim_close_radius=5``,
    deliberately NOT scaled with ball_radius) bridges the fragmented rim into a continuous
    ring so hole-filling can recover the full object — gated by ``rim_close_min_result_area``
    (150 px) so it only applies to genuinely large bridged rims and never fuses nearby small
    puncta.
  - **Permissive max area**: the hard 25% cap is relaxed to 90%, so genuine large condensates
    aren't rejected purely for size.
  - **Solidity-aware rejection** in ``puncta_refinement_filtering_func`` (serial + parallel):
    large objects are rejected only if they're *also* irregular (solidity < 0.85), which
    catches erroneous merges while keeping real compact large blobs.
  - **Large-object rescue** in the foreground-suppression pass
    (``image_processing_tools.py``): sufficiently large, contiguous, clearly-bright regions
    have their realness weight forced to 1, so the puncta-scale peakiness gates stop
    progressively dimming and dropping large coarsened condensates.

## [1.5.222] - 2026-07-05
### Added (transfection filter for transiently-transfected time-series)
- **Optional per-cell transfection filter in the time-series cell-segmentation step.** For
  transiently transfected samples, not every Cellpose-detected cell has usable signal. When
  the new "Filter untransfected cells" checkbox is on, after segmentation each cell is
  scored by fluorescence SNR (mean cell intensity ÷ background) on the reference frame of a
  chosen fluorescence channel — the same channel that will be analysed, not the DAPI
  segmentation channel. Cells below the SNR threshold are dropped.
  - Produces a separate **"Transfected Cells"** mask (the full mask is preserved).
  - Reports a **transfection-efficiency** estimate (fraction of cells above threshold) and
    stores a per-cell kept-vs-dropped stats table (`transfection_stats`) in the data
    repository for comparison/histograms.
  - **Off by default** — Csat-type experiments deliberately leverage low/untransfected
    cells, so the filter is opt-in. Threshold and fluorescence channel are user-selectable.
  - This is a coarse "is this cell worth analysing" gate, not puncta segmentation.

## [1.5.221] - 2026-07-05
### Fixed (time-series condensate analysis rejected the (T,H,W) cell mask)
- **Time-series condensate analysis now accepts a (T,H,W) cell-mask stack**, not just a 2D
  mask. The step hard-rejected anything non-2D with "Labels layer must be 2D" — but the
  keyframe Cellpose step correctly produces a (T,H,W) mask so that each frame's own cell
  boundaries (which move over time) are used. The analysis now:
  - Uses a (T,H,W) mask per-frame (each frame analysed against its own mask), in both the
    parallel and serial paths.
  - Accepts a 2D mask and propagates it to all frames, with a warning that this assumes the
    sample is temporally stationary.
  - Computes the cell-label set from the union across frames, so a cell present in only some
    frames is still analysed where it exists.
  - Warns (rather than failing) if a (T,H,W) mask's frame count doesn't match the image,
    falling back to the reference frame's mask.

## [1.5.220] - 2026-07-05
### Added (Cellpose "Refine masks" checkbox — raw vs refined, user's choice)
- **The 2D Cellpose segmentation (Cell Segmentation widget, used by Cellular Object Analysis
  and the colocalization pipelines) now has a "Refine masks" checkbox.** The same
  destructive post-processing found in the time-series audit (binarize → watershed →
  morphological opening → relabel) was also running on the 2D / coloc Cellpose output.
  Rather than change validated behaviour silently, it's now a toggle:
  - **ON (default)** — legacy refine pipeline; preserves the existing validated 2D result.
  - **OFF** — use Cellpose's instance masks directly (usually better when Cellpose already
    segments the image well).
  The choice is stored (`cellpose_refine`), recorded in the batch step, and honoured by the
  headless batch replay so runs reproduce. Untick it to compare raw Cellpose against the
  refined output on your own data.
### Unchanged (deliberately)
- Time-series Cellpose stays raw (`postprocess=False`, from 1.5.219). Z-stack Cellpose stays
  refined (no checkbox yet — a candidate for the same toggle later). The `cellpose_segmentation`
  function default remains `postprocess=True` so any caller not passing the flag is unchanged.

## [1.5.219] - 2026-07-05
### Fixed (time-series Cellpose segmentation — audit)
- **Time-series Cellpose now uses Cellpose's masks directly instead of destroying them.**
  `cellpose_segmentation` post-processed every result by binarizing (`masks > 0`, throwing
  away Cellpose's instance labels), re-splitting with a generic watershed, applying **7
  iterations of morphological opening**, and relabeling — which demolishes Cellpose's
  learned per-object boundaries and degrades otherwise-good output. Added a
  `postprocess=True` parameter; the time-series path now passes `postprocess=False` to use
  Cellpose's instance masks as-is. The legacy 2D path keeps `postprocess=True` (unchanged,
  its downstream steps expect the refined masks).
- **Instance labels are preserved through the upscale/downscale round-trip.** The upscaled
  branch previously did `measure.label(mask > 0)` after downscaling, re-binarizing and
  merging touching cells Cellpose had separated. It now downscales the label image with
  nearest-neighbour interpolation, keeping each cell's Cellpose ID.
- **Removed the misleading segmentation-channel hint.** The "Seg. channel" dropdown hinted
  `Enhanced Background Removed` (a condensate-optimized layer) while its own tooltip says a
  DAPI/nuclear channel is preferred — nudging users toward the wrong layer for cell
  segmentation. The hint is now cleared so it doesn't auto-pick the processed condensate
  image.
- **Keyframe progress count is now correct.** `n_kf` didn't include the final frame that
  gets appended as an extra keyframe when it isn't a natural interval boundary, so the
  progress read "x / N" against a too-small N. Both count sites now include the appended
  last frame.
### Not changed (flagged, needs separate testing)
- The 2D path's 7-iteration morphological opening is left as-is; reducing it (as suggested)
  would affect the validated 2D condensate workflow and should be tested independently.
- StarDist and Random-Forest time-series paths still `label(> 0)`; the audit targeted the
  Cellpose path only.

## [1.5.218] - 2026-07-05
### Fixed (menu label rendering)
- **"Cell & Object Analysis" → "Cell and Object Analyses".** In Qt menus an ``&`` marks the
  next character as a keyboard mnemonic and isn't rendered, which made the label display
  oddly. Spelling out "and" avoids the mnemonic entirely. Applied to both the Analysis
  Methods submenu and the matching Toolbox submenu (the latter also dropped "Condensate").

## [1.5.217] - 2026-07-05
### Fixed (method-1 status markers — second pass, items 6–9)
- **Step 2 — "Measure Line(s)" now turns green when run** and reverts to red on Clear,
  unless "Remember measurements across clears" is on (then the measurement and its done
  state carry over). Uses the new `button_with_circle` completion state.
- **Step 3 — "Run Upscaling" now turns blue when run** (it's an optional step, so blue =
  "you did this optional thing"), and reverts to yellow on Clear since its upscaled output
  layers are removed.
- **Step 7 — the cell-analysis mask dropdown now auto-greens correctly.** Its name hint was
  `Labeled Cell Mask`, but that layer is this step's *output*; the layer that actually
  feeds it is the Cellpose segmentation, named `Cellpose Segmentation on …`. The hint is
  now `Cellpose Segmentation`, so auto-population turns the circle green. Dropdown circles
  also now distinguish GREEN (selection matches the suggested/auto-filled layer) from BLUE
  (you deliberately picked a different layer, or set an optional no-hint dropdown like
  "Select Mask Layer to Omit" away from its default) — previously a user override showed
  green instead of blue.
- **Step 1 — the pixel-size marker now updates with image load/clear.** The "Image loaded"
  marker was wired to layer events, but the pixel-size gate only re-evaluated on field
  edit / data switch, so its status went stale on load/clear. Its refresh is now also
  wired to layer insert/remove, so both Step 1 markers update together.

## [1.5.216] - 2026-07-05
### Changed (method-1 UI naming + layer auto-selection — first pass)
- **Dropped "Condensate" from the analysis method titles** for branding and accuracy (these
  workflows apply to membrane-bound objects and objects from processes other than
  condensation). Analysis Methods menu: submenu "Condensate & Cell Analysis" → "Cell &
  Object Analysis"; the five entries "Cellular/In Vitro/Time-Series/Z-Stack Condensate
  Analysis" → "… Object Analysis". Method-1 panel: section "Condensate Analysis" → "Object
  Analysis" and its dock "Condensate Analysis Dock" → "Object Analysis Dock". The top
  "Cell/Nuclei Analysis" section title is unchanged. These are display-label changes only;
  internal wiring/keys are untouched.
- **Steps 7–9 now auto-select the plain "Upscaled Fluorescence Image", not a derivative.**
  After pre-processing, dropdowns that want the plain upscaled image were auto-populating
  with "Pre-Processed Upscaled Fluorescence Image" because the `Upscaled Fluorescence`
  name-hint substring-matched the longer derived name. `_hint_matches` now also rejects the
  `pre-processed`/`preprocessed` leading prefixes (alongside the background-removed ones),
  so the "Select Image for Cell Analysis" (step 7), "Select Fluorescence Image to Process"
  (step 8), and "Select Image for Puncta Measurement" (step 9) dropdowns pick the plain
  upscaled image, while step 8's pre-processing dropdown (whose hint names the modifier)
  still matches its intended layer.
- **Step 2 simplified to just "Measure Line(s)".** Removed the separate "Draw Line(s)"
  button; line drawing now auto-arms when the step is shown (the diameter Shapes layer is
  activated in add-line mode), so there's one button instead of two.
### Added (status-marker groundwork)
- `button_with_circle` can now reflect completion: a required action turns its circle green
  once run, an optional action turns it blue, and it exposes `reset()` for per-step / Clear
  reversion. (Wiring this into specific steps' run/clear behaviour — steps 2, 3, 7 — and
  the step-1 marker resets is the next pass.)

## [1.5.215] - 2026-07-05
### Fixed (images open tiny — the REAL cause: the 2-D load path never called the fit)
- **`open_2d_image` now calls the auto-fit.** The debug build (1.5.213) printed nothing
  because the fit was never invoked for 2-D images: `open_2d_image` → `load_into_viewer`
  enables the scale bar but never called `_fit_view_to_layer` — only the stack path
  (`_finalise_stack_load`) did. So plain 2-D TIFFs opened tiny and Home was the only way to
  fill the canvas, and all the earlier scale-aware fit work (1.5.210–1.5.213) simply didn't
  run for them. The fit is now called at the end of `open_2d_image` (deferred 400 ms, after
  the channel-assignment dialog and diameter-layer inserts settle), matching the stack
  path. Both single- and multi-channel 2-D loads are covered (the channel dialog is modal,
  so channels are in the viewer before the fit fires).

## [1.5.214] - 2026-07-05
### Docs
- **Recorded the scale-bar migration as a known issue + low-priority backlog item**
  (``docs/source/development/roadmap.rst``). Captures that the main image/stack load path
  uses napari's built-in ``viewer.scale_bar`` (via ``scale_bar.unit``), which works only
  because the code avoids the ``Layer.units`` call that black-outs the canvas — and that
  this is fragile against napari's ``scale_bar.unit`` deprecation (PR #9007, which moves
  the unit to ``Layer.units``) and is coupled to the auto-fit machinery. The self-contained
  ``draw_custom_scale_bar`` (a Shapes rectangle in data coords, immune to both) already
  exists but is wired only into the temperature/movie workflow; unifying on it across the
  load path is deferred as low priority, to be done before adopting a napari version that
  removes ``scale_bar.unit``.

## [1.5.213] - 2026-07-05
### Diagnostic (image-opens-small — instrument the fit)
- **Added `PYCAT_DEBUG=1` logging to the auto-fit.** Prior fixes (world-extent math, longer
  delay, mirroring Home) didn't resolve the image opening small, so the fit now logs the
  layer name, its transform-aware world extent, the canvas size, the zoom before/after the
  fit, and — via a 600 ms follow-up — whether the zoom gets changed back afterwards. This
  will show definitively whether the fit is computing the wrong zoom, not running, or being
  reset by a later event (e.g. a scale-alignment or napari auto-reset on layer insert),
  rather than guessing. No behavioural change.
### Note
- Clarified for reference: a plain 2-D image load uses napari's built-in `viewer.scale_bar`
  (via `_enable_auto_scale_bar`); the custom Shapes-based `draw_custom_scale_bar` is used
  only by the temperature/movie-export workflow.

## [1.5.212] - 2026-07-05
### Fixed (auto-fit at load — now matches the working Home button)
- **Images open fitted, not tiny.** Key diagnostic: the manual Home button fit the image
  correctly, but the auto-fit at load did not — so the math was fine and the problem was
  timing/state. The auto-fit recomputed the extent by hand (`shape × scale`), which can
  disagree with napari's real extent right after load (the µm/px scale was just assigned
  and the transform/extent cache may not have updated when the deferred fit fires).
  `_fit_view_to_layer` now reads `layer.extent.world` — the exact transform-aware extent
  the Home button uses — and the fit is deferred a little longer (400 ms) so the scale bar
  and all layer-insert scale-alignment events have settled first. Auto-fit and Home now
  behave identically.

## [1.5.211] - 2026-07-05
### Fixed (overlay X-compression — side-by-side squished into one image's width)
- **The side-by-side Overlay Image now renders at true proportions.** After the stripe
  fix, the overlay (an (H, 2W, 3) side-by-side of the plain and red-overlaid image) was
  being fit to the *reference image's* field of view, which compressed its 2W pixels into
  one image's worth of world width — squishing it ~2× in X. The overlay's pixels are the
  same physical size as the source image's (the hstack just adds columns), so it now
  inherits the source layer's per-pixel scale explicitly at creation, and
  `_align_layer_scales` gives RGB overlays the reference per-pixel scale (not FOV/shape) as
  a fallback. Each half of the side-by-side now aligns 1:1 with the data pixel size.

## [1.5.210] - 2026-07-05
### Fixed (images open tiny — auto-fit ignored the layer's µm/px scale)
- **Newly-opened images now fill the canvas.** Same class of bug as the overlay stripe:
  PyCAT sets each image layer's scale to µm/px, so a 2048-px image at 0.098 µm/px has a
  world extent of only ~201 units — but the auto-fit computed zoom from the raw 2048 pixel
  count, ending up ~10× too zoomed out (the image spanned ~88 px on a ~900 px canvas). A
  new `_fit_view_to_layer` fits from the WORLD extent (shape × scale), handles RGB layers
  (channel axis excluded), and retries until the canvas is laid out. It replaces the old
  pixel-based fit in `_finalise_stack_load`, so images open at a sensible size without
  needing to press Home. (The manual Home button was already scale-correct — it fits from
  `layer.extent.world` — so it's unchanged.)

## [1.5.209] - 2026-07-05
### Fixed (overlay stripe — the TRUE root cause: RGB channel axis treated as spatial)
- **`_align_layer_scales()` no longer treats an RGB image's channel axis as a spatial
  dimension.** This is the actual cause of the stretched "Overlay Image", found by
  analysing the scale-alignment pass rather than the overlay array (which was always
  correct). The overlay is `(H, 2W, 3)`; the alignment code used `shape[-2:]` = `(2W, 3)`,
  so it treated the 3-channel axis as X and assigned the overlay a massive x-scale
  (~16.7 world-units/px vs the data's ~0.024 — a ~680× blow-up), rendering it as a long
  stripe extending far past the data. The alignment now detects RGB/RGBA image layers
  (`layer.rgb` with a trailing axis of 3 or 4) and uses the two axes *before* the channel
  axis as the spatial shape. This function didn't exist in v1.0.0, which is why the
  overlay rendered correctly then — the overlay code was never the problem.
- The overlay is now added as `uint8` with `rgb=True`, so napari and the alignment pass
  both unambiguously recognise it as a colour image.
### Improved
- **Overlay PNG contrast** (carried from the in-progress 1.5.208): the exported
  `_puncta_overlay.png` computes its contrast-stretch window over the signal pixels
  (non-near-zero) with a high upper percentile (99.8), so the bright cell body keeps its
  detail instead of blowing out to white.

## [1.5.207] - 2026-07-05
### Fixed (overlay stripe — root cause found via git diff against 1.0.0, and reverted)
- **Restored the v1.0.0 "Overlay Image" code exactly.** A git diff of the overlay path
  against the 1.0.0 release showed `create_overlay_image` and the caller were UNCHANGED in
  the committed code — the stretched-stripe regression was introduced *during this
  session's* earlier "green stripe" fix, which dropped the final
  `dtype_conversion_func(sbs_overlay, 'uint16')` conversion and added `rgb=True`. The
  original sequence converts the (H, 2W, 3) uint8 array to uint16 and adds it WITHOUT
  `rgb=True`; napari auto-detects a (H,W,3) *uint8* array as RGB but not a *uint16* one, so
  the uint16 array renders as a normal multi-plane 2-D image at correct proportions.
  Reverting to the exact 1.0.0 lines fixes the stripe.
### Kept
- The two requested enhancements remain on top of the restored overlay: after analysis the
  Step 9 fluorescence image and the puncta mask are brought to the top of the layer list
  (mask on top, both visible), and a flat merged grayscale+red PNG is written to the source
  folder as `<base_name>_puncta_overlay.png`.

## [1.5.206] - 2026-07-05
### Changed (replaced the in-viewer Overlay Image with layer reordering + PNG export)
- **No more "Overlay Image" layer.** Every attempt to add a blended overlay as a napari
  image layer mis-rendered as a stretched strip (napari's RGB/axis handling), so the
  in-viewer overlay is gone. Instead, after Condensate Analysis:
  - **The two relevant layers are brought to the top of the layer list**, both made
    visible: the Step 9 "Select Image for Puncta Measurement" fluorescence image, with the
    selected puncta mask directly above it. This reproduces the mask-over-image overlay
    using napari's own compositing (no custom RGB layer), which always aligns and scales
    correctly.
  - **A merged grayscale + red-puncta PNG is written to the source folder** as
    `<base_name>_puncta_overlay.png` — a flat, shareable overlay (image contrast-stretched
    so dim data is visible, puncta blended in red). This is a file, so napari never renders
    it and the stretch bug can't recur.
- File path and base name are now stored in the data repository at load time so the export
  lands next to the original image.

## [1.5.205] - 2026-07-05
### Fixed (overlay image — replaced the side-by-side with an in-place overlay)
- **The Overlay Image is now a single same-size (H, W) RGB layer** with puncta painted
  red directly on the fluorescence image, instead of the old side-by-side `np.hstack`
  that produced an (H, 2W) layer. The doubled-width layer sat in napari's shared
  coordinate space alongside all the (H, W) layers and stuck out past them — the "green
  stripe extending beyond the data" that no amount of squeezing fixed, because the shape
  was working as (mis)designed. The new overlay shares the exact footprint of every other
  layer, so it aligns on the data and toggles cleanly.
- **The overlay is now visible on dim images.** The source "Upscaled Fluorescence Image"
  can be a float scaled by 1/65535 (max ≈ 0.02), which rendered nearly black. The overlay
  now contrast-stretches on the 1st–99th percentile before display, so the cell structure
  is visible with the puncta highlighted. The old `create_overlay_image` (side-by-side)
  is no longer used by the puncta workflow.

## [1.5.204] - 2026-07-05
### Diagnostic (overlay "green stripe" — instrumenting the real cause)
- **Added `PYCAT_DEBUG=1` logging to the overlay path.** Pixel-level analysis of the
  reported screenshot showed the overlay is actually ~2:1 aspect (a correct side-by-side
  shape), NOT the 4-D "stripe" the earlier squeeze fix targeted — the visible content is
  mostly black with a bright green horizontal band, which points to the *input image*
  being wrong (e.g. an over-subtracted/near-black layer, or a green-channel normalisation
  blow-out) rather than a dimensional bug. The overlay now logs the image layer name, raw
  and squeezed shapes, dtype, min/max, and non-zero fraction, plus the mask non-zero
  count, so the next run pins down exactly what is being visualised. No behavioural change
  to the overlay itself.

## [1.5.203] - 2026-07-05
### Fixed (line/ROI drawing does nothing when the layer is hidden)
- **Arming line or ROI drawing now makes the target layer visible first.** napari silently
  ignores the drawing tool on a hidden Shapes layer, so after toggling layers off the
  "Draw Line(s)" / "Add ROI Drawing Layer" actions appeared to do nothing. Both now set
  `layer.visible = True` (and restore a usable opacity for the diameter layer) before
  activating draw mode.
### Added (nuclei segmentation model for Step 5)
- **"Use nuclei model" checkbox under Cellpose in the time-series Step 5.** The default
  Cellpose model (cyto2 / cpsam) is a CYTOPLASM model; on a nuclear stain like DAPI it
  merges all nuclei into one giant region because there's no cytoplasm structure to bound
  them (the reported "DAPI segments into one giant area"). The checkbox routes Cellpose to
  its dedicated 'nuclei' model, which is the correct choice for DAPI/Hoechst. Threaded
  through `run_keyframe_cellpose` → `cellpose_segmentation(model_name='nuclei')`. Shown
  only when Cellpose is the selected method; diameter is unchanged for now (test the model
  effect in isolation first). On Cellpose 4 (where the nuclei CNN doesn't exist as a
  separate model) the user is warned and the default model is used, with a pointer to
  install cellpose<4 for a dedicated nuclei model.
### Notes
- The GFP channel returning no segmentation on untransfected cells is expected biology,
  not a bug: GFP only marks transfected cells, so a GFP-based segmentation can only find
  those. Segment on a channel that labels all cells (a nuclear stain, or brightfield) to
  capture every cell.

## [1.5.202] - 2026-07-05
### Fixed (four issues from user testing)
- **Home / fit-to-view at file open now works reliably.** The auto camera-fit fired once
  at a fixed 100 ms delay; if the canvas wasn't laid out yet (dock still arranging) it
  read a zero size and fell back to `reset_view()`, which the code itself notes is
  unreliable — so the image often opened not fitted. It now retries with growing delays
  until the canvas has a real size, calls `reset_view()` first (correct for 2D and 3D/T
  stacks), then tightens center/zoom from the known spatial dimensions.
- **Overlay "wide green stripe" is now impossible to add.** In addition to the earlier
  squeeze fix, the overlay is now added with `rgb=True` (so napari treats the last axis
  as RGB channels, never as a 3-slice stack) and a final shape guard: if the composited
  array isn't a clean `(H, W, 3/4)` image it is skipped with a warning rather than added
  as a malformed layer. Analysis results are unaffected either way.
- **Downstream dropdowns no longer grab the wrong derived layer.** Auto-selection matched
  `name_hint` as a plain substring, so a hint of `Upscaled Fluorescence` also matched
  `Enhanced Background Removed Upscaled Fluorescence Image` — causing the
  background-removed layer to auto-populate dropdowns that wanted the plain upscaled
  image. New `_hint_matches` rejects a layer that carries an EXTRA leading modifier prefix
  (`Enhanced Background Removed`, `Background Removed`) the hint didn't ask for, while
  still matching when the hint itself names that modifier.
- **Status circles no longer turn green prematurely.** A dropdown defaults to its first
  item (a real layer), which made the row's status circle read as satisfied before the
  user chose anything — and green on the wrong layer via the substring bug above. The
  circle now turns green only when the selection actually matches the row's `name_hint`
  OR the user deliberately picked an item (tracked via `QComboBox.activated`, which
  doesn't fire on the implicit index-0 default). Dropdowns without a hint are unchanged.

## [1.5.201] - 2026-07-05
### Fixed (real cause of the multi-second stall when adding an ROI layer to a lazy IMS stack)
- **Lazy IMS layers are now added with explicit `contrast_limits` computed from their
  first frame.** The stall was NOT the world-extent recompute (that's cheap shape
  arithmetic and never touches pixels). It was napari auto-estimating contrast limits and
  building the layer thumbnail by calling `np.asarray()` on the lazy `(T,Y,X)` wrapper,
  which triggers `__array__` and loads EVERY frame from disk — slow on a USB-HDD IMS
  stack, and re-triggered whenever the layer list refreshes (such as when an ROI Shapes
  layer is added). Passing `contrast_limits` up front (from the single first frame, which
  is already read) stops napari from probing the whole stack. Applied to all three lazy
  IMS paths (T,Y,X / Z,Y,X / T,Z,Y,X). The first frame is reused from the existing
  probe-read for channel 0, so no extra disk reads for that channel. Users can still
  adjust contrast normally afterwards.
### Notes
- Deliberately did NOT change the wrappers' `__array__` to return a single frame: that
  method loading the full stack is *correct* for genuine full-array operations, and
  short-circuiting it would silently make real analyses operate on one frame. The fix
  targets only the incidental thumbnail/contrast probe, via `contrast_limits`.

## [1.5.200] - 2026-07-05
### Fixed (line drawing tool becomes unusable after clicking elsewhere)
- **Added a "✏ Draw Line(s)" button that re-arms line drawing.** Clicking an image
  layer's eye icon (napari default) makes that image the active layer, which silently
  disables line drawing on the diameter Shapes layer even though it still looks selected
  in the layer list — `update_tool` only sets `add_line` mode on a selection *change*, so
  re-selecting doesn't always re-fire. The new button deterministically activates the
  correct diameter Shapes layer (preferring one with no lines drawn yet) and sets
  `add_line` mode, so drawing is always one click away regardless of what selection
  detour happened.
### Improved
- **"Add ROI Drawing Layer" no longer freezes on the button press.** On a large lazy IMS
  stack, adding a Shapes layer makes napari recompute the world extent, which took a
  noticeable moment mid-click. The layer creation is now deferred by one event-loop tick
  with a wait cursor, so the click feels responsive. (The extent recompute itself is
  napari-internal; this removes the frozen-button feel rather than the underlying cost.)
### Housekeeping
- Removed a duplicated `_add_widget_to_layout_or_dock` call in `_add_measure_line` that
  added the measure widget twice.

## [1.5.199] - 2026-07-05
### Fixed (overlay image rendered as a wide green stripe — the real root cause)
- **The overlay now squeezes its input to 2-D before compositing.** The 1.5.184 fix
  addressed float-clipping (`img_as_uint`) but not the actual cause of the stripe on
  processed data: the "Upscaled Fluorescence Image" layer carries a leading singleton
  axis (shape `(1, H, W)`) from the loader's T/C dimension handling. Passing that to
  `create_overlay_image` produced a 4-D array `(1, H, 2W, 3)`, which napari renders as a
  wide, short green stripe instead of a side-by-side overlay. `run_puncta_analysis_func`
  now `np.squeeze`s the image (and the puncta·cell mask) to a plain 2-D plane first,
  falling back to the first plane for any genuine multi-frame input and to a zero mask on
  a post-squeeze shape mismatch. Validated: `(1,1024,1024)` input now yields a correct
  `(1024, 2048, 3)` overlay instead of the malformed 4-D stripe.

## [1.5.198] - 2026-07-05
### Fixed (RuntimeError spam: "wrapped C/C++ object of type QComboBox has been deleted")
- **Layer dropdowns no longer fire callbacks after their widget is destroyed.**
  `_layer_row` (status-circle rows) and `create_layer_dropdown` both connect handlers to
  the viewer-level `layers.events.inserted` / `removed` signals, which outlive the
  dropdown. When a workflow was torn down and its `QComboBox` deleted, a subsequent layer
  insertion/removal still invoked those handlers against the dead C++ object, raising
  `RuntimeError: wrapped C/C++ object of type QComboBox has been deleted` — repeatedly,
  flooding the console. Fixed two ways (belt and suspenders): (1) each dropdown now
  disconnects its viewer-signal handlers on `destroyed`, and (2) `_update_circle`,
  `_on_inserted`, and `update_dropdown_items` guard their `QComboBox` access with
  `except RuntimeError` so any stale call that still slips through is a harmless no-op.
  Also removed dead introspection code (`for conn in ...events.inserted._slots: pass`)
  left in `_layer_row`.

## [1.5.197] - 2026-07-05
### Added
- **Colormap reset toggle on the PyCAT toolbar.** A new "🎨 Gray / Viridis" button
  flips every image layer between grayscale and viridis in one click. IMS/multichannel
  loads assign per-channel colors (blue/green/red/magenta) which are harder to read for
  intensity inspection; this gives a one-click neutral view. Label and mask layers are
  left untouched (their colormaps are categorical). The button label reflects the
  colormap the next click will apply.

## [1.5.196] - 2026-07-05
### Fixed (IMS channel names fell back to generic positional colors)
- **IMS channel identity now read from the HDF5 `DataSetInfo/Channel N` group
  attributes.** `extract_channel_info_from_ims` previously scanned the unreliable
  `reader.metaData` dict, which for real Fusion/Imaris files omits per-channel info —
  so every IMS channel fell through to the positional fallback (C0-blue, C1-green,
  C2-red, C3-far_red), which is wrong whenever the acquisition order isn't the standard
  blue/green/red/far-red. It now reads each channel's stored `Name` (e.g.
  `405_DAPI_CF40um_z`, `488_GFP_CF40um_z`, `594_mCherry_CF40`, `BFPreAm`) and
  `LSMExcitation/LSMEmissionWavelength` directly from the h5py handle, per index, then
  runs them through the existing three-tier identifier. Falls back to the metaData scan
  and then position only if the handle read yields nothing.
- **Channel-name matching fixed for underscore/digit-delimited names.** The fluorophore
  patterns used `\b` word boundaries, which do NOT fire between an underscore and a
  letter (underscore is a word char) — so `488_GFP_CF40um_z` and `594_mCherry_CF40`
  matched nothing. Patterns now use non-letter delimiters `(?:^|[^a-z])…(?:[^a-z]|$)`,
  correctly matching the fluorophore token wherever it sits in the name. Added
  `BFPreAm`/`BFPreAmp` and bare `BF` as brightfield/transmitted patterns, plus mScarlet
  and tdTomato.
- Added `raw_name` to the channel-identification result and a debug log line
  (`PYCAT_DEBUG=1`) reporting the resolved name/label/bucket per IMS channel, so a
  name↔index mismatch (stored acquisition name not matching the physical sample) can be
  diagnosed vs. a PyCAT parsing bug.

## [1.5.195] - 2026-07-05
### Changed (menu-bar clarity — distinguish PyCAT menus from napari's)
- **Added a bold "◆ PyCAT ▸" section marker** on the menu bar, immediately before
  PyCAT's menus (Analysis Methods, Toolbox, ★ Open/Save File(s), Clear, Home, Metadata).
  PyCAT's menus are appended to napari's native menu bar (File/View/Plugins/Window/Help),
  and without a visual break users couldn't tell where napari ended and PyCAT began. The
  marker is a non-clickable, bold, accent-coloured divider so everything to its right
  reads clearly as PyCAT. The menus themselves are unchanged (kept as dropdowns), so no
  wiring is affected. Noted as a candidate for a fuller toolbar redesign later.

## [1.5.194] - 2026-07-05
### Added (unified metadata extraction, viewer widget, and results export)
- **New `pycat/file_io/metadata_extract.py`** — a single normalisation layer that
  extracts acquisition metadata from any supported format (TIFF/OME-TIFF via tifffile,
  CZI/OME via AICSImage, IMS via HDF5 attributes) into a consistent record with a
  curated `common` block (pixel size + source, dimensions, bit depth, channels,
  timepoints, Z, objective, numerical aperture, modality, excitation/emission
  wavelengths, acquisition date, software) and a `raw` block containing every
  key/value the file exposes. Every extractor is defensive — missing fields yield
  None rather than raising.
- **IMS metadata is no longer discarded.** Previously `update_metadata` was only
  called on the AICSImage path, so all IMS acquisition metadata (objective, NA,
  modality, wavelengths, recording date, gain) was thrown away. Both load paths now
  store a normalised record in `data_repository['file_metadata']`. On the multichannel
  IMS test file this recovers 63x objective, NA 1.4, Spinning Disk Confocal, 521 nm
  ex/em, 0.0957 µm/px.
- **Metadata viewer** — a new "ⓘ Metadata" menu-bar action opens a dialog showing the
  curated fields, with a "Show all raw metadata" checkbox that reveals the full dump,
  and an "Export JSON…" button.
- **Metadata exported with results.** `save_and_clear_all` now writes
  `<name>_metadata.json` alongside the results CSVs, tying acquisition provenance to
  every analysis output (supports the reproducibility story).
### Housekeeping
- Removed stray `file_io.py.orig` / `file_io.py.rej` patch artifacts that were sitting
  in the source tree.

## [1.5.193] - 2026-07-05
### Fixed (IMS pixel size not read from spatial extents)
- **Pixel size is now recovered robustly from IMS `ExtMax0`/`ExtMin0` extents.** The
  old code called `reader.read_numerical_dataset_attr('ExtMax0')` inside a bare
  `except: pass`, which silently swallowed failures — including on files whose extents
  are stored as fixed-length ASCII char arrays with negative stage coordinates (e.g.
  `b'-42107.8'`), a case the reader's accessor mishandles. New helper
  `_ims_pixel_size_um` reads the `DataSetInfo/Image` extents directly from the h5py
  handle (`reader.hf`), decodes the char array to a float explicitly, and computes
  `(ExtMax0 - ExtMin0) / width`, falling back to the reader accessor only if the handle
  route fails. On the multichannel time-series test file this correctly recovers
  0.0957 µm/px (196 µm across 2048 px). Unitless/absurd values are rejected.
### Notes
- Confirmed the time-series condensate analysis and the 2D condensate method share the
  same segmentation engine (`segment_subcellular_objects`) and the same preprocessing
  (`pre_process_image` + `rb_gaussian_bg_removal_with_edge_enhancement`) — the science
  has not forked. The one intentional difference is that the time-series path passes
  `cell_df=None` (so the per-cell low-SNR background-removal-skip branch never fires;
  every cell gets background removal). A fuller methods-coherence review of this
  difference is deferred to the planned methods audit.

## [1.5.192] - 2026-07-05
### Fixed (TIFF pixel size not read from resolution tags)
- **Pixel size is now recovered from baseline TIFF resolution tags when AICSImage
  misses it.** AICSImage's `physical_pixel_sizes` reads only OME-XML and ImageJ
  metadata, not the standard `XResolution`/`YResolution`/`ResolutionUnit` tags. Many
  microscope-exported TIFFs (confirmed on real GFP/DAPI test files) store pixel size
  ONLY in those baseline tags, so AICSImage returned None and PyCAT fell back to
  1.0 µm/px, forcing the user to enter the scale manually in the pixel-size gate. New
  helper `_tiff_pixel_size_um` reads the tags directly: XResolution is a RATIONAL
  (pixels per unit), ResolutionUnit 3 = cm / 2 = inch. On the real test files this
  correctly recovers 0.097656 µm/px (a 50 µm field of view across 512 px). Wired into
  both the AICSImage path (as a fallback when it returns 1.0) and the direct-tifffile
  fallback path. Unitless tags (ResolutionUnit = 1) and absurd values are rejected so a
  bad tag can't set a nonsense scale.

## [1.5.191] - 2026-07-05
### Documentation
- Added a super-resolution data processing workflows section to the roadmap. Structured
  around the critical scientific distinction that "super-resolution" spans two different
  data models: **Category A — image-based / raster-grid SR** (deconvolution, SRRF, SOFI,
  SIM reconstruction) that consumes an image sequence and emits an enhanced raster image,
  which is drop-in compatible with PyCAT's existing pipeline as advanced preprocessing;
  and **Category B — localization-table SR** (PALM/STORM/dSTORM, DNA-PAINT/PAINT family)
  that emits a coordinate list, not an image, and needs its own data type and
  localization-native operations. Notes the sequencing (Category A near-term, especially
  deconvolution then SRRF/SOFI, reusing lazy loading + batch/replay; Category B a larger
  post-publication addition scoped only if a real user presents localization data) and the
  strongest integration argument (PyCAT's point-based spatial-phenotyping statistics
  already cover most of what localization-cluster analysis needs).

## [1.5.190] - 2026-07-05
### Fixed (release hygiene — clean sdist)
- **Source tarballs no longer include VCS/cache/build cruft.** The project uses hatchling,
  which ignores `MANIFEST.in` (a setuptools mechanism) — so the `global-exclude` rules
  there (`.DS_Store`, `.pytest_cache/`, `__pycache__/`, etc.) were never applied to the
  sdist. Added an explicit `[tool.hatch.build.targets.sdist]` section with `include` and
  `exclude` lists, so `python -m build` now produces a clean tarball by construction
  (no `.git/`, `__pycache__/`, `.coverage`, `.DS_Store`, `dist/`, `PKG-INFO`). The wheel
  was already clean.
### Documentation
- Recorded the external architecture review (2026-07) in the roadmap: platform
  consolidation sequencing (spatial-phenotyping menu grouping, stability tiers,
  biological-relevance tooltips, shared output schema, deferred module registry), the
  highest-value test additions, and this release-hygiene fix. Key insight: the UI
  monolith, batch-registry monolith, and output-schema gap are one refactor —
  self-describing modules — and the shared output schema is the foundational piece to
  build first.

## [1.5.189] - 2026-07-05
### Fixed (macOS support — Apple Silicon GPU + platform-aware messages)
- **Cellpose now uses the Apple Silicon GPU (Metal/MPS) when available.**
  `_get_cellpose_gpu()` previously checked only `torch.cuda.is_available()`, which is
  always False on M1/M2/M3 Macs, forcing CPU even when a Metal-capable PyTorch was
  installed. It now also checks `torch.backends.mps.is_available()` and returns True
  for MPS, so `CellposeModel(gpu=True)` is passed on Apple Silicon and Cellpose uses
  the GPU automatically. The detected backend ('cuda' / 'mps' / None) is cached in
  `_CELLPOSE_GPU_BACKEND`.
- **CPU-fallback warning is now platform-aware.** On Mac it no longer tells users to
  `pip install torch --index-url .../cu118` (a Windows/Linux-only CUDA wheel that does
  not exist for macOS). Mac users are instead told that installing an MPS-capable
  PyTorch enables the Apple GPU automatically, and that there is no CUDA on Mac.
### Notes (Mac install guidance — no code change)
- On Apple Silicon, install via conda-forge rather than pure pip: `simpleitk` and
  `numba` (llvmlite) arm64 wheels are on conda-forge but not reliably on PyPI, so a
  pip-only install can fail at dependency resolution before PyCAT ever runs.

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
