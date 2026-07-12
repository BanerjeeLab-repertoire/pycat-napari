====================
PyCAT-Napari Roadmap
====================

This document outlines the desired features, improvements, and issues to address in future stable releases or PyCAT 2.0. The content is organized into categories for clarity and ease of reference.

Basic plans/outlines are included where I have done some brainstorming, and additional information/references are included where I have found relevant resources.


Recently Completed (as of v1.5.x)
---------------------------------

Many items originally listed below have since shipped and are noted here so the
open sections stay accurate:

* **Video / time-series & particle tracking** — VPT (Video Particle Tracking) with
  trajectory linking and MSD/microrheology; dedicated time-series condensate workflow
  with lazy (zarr-backed) stack preprocessing and keyframe Cellpose.
* **Batch / video processing framework** — configurable step registry and batch
  processor that replays a workflow across a folder or a stack.
* **3D / Z-stack support** — 3D background removal, 3D cell + condensate segmentation,
  and 3D metrics.
* **Watershed splitting** — ``split_touching_objects`` separates touching objects.
* **Top-hat filters** — white/black top-hat available in image processing.
* **Cell segmentation model selection** — Cellpose model dropdown plus StarDist and a
  Random Forest pixel classifier as alternative methods.
* **Progress bars & background threading** — long analyses run on worker threads with
  progress indicators, keeping the UI responsive.
* **Expand Labels** — grow labels without merging touching objects
  (``skimage.segmentation.expand_labels``); Toolbox → Labeled Mask Tools.
* **Mask Layer Operations (AND / OR / XOR)** — boolean set operations on two masks;
  Toolbox → Layer Operations. (Image merge modes were also fixed so Mean and Additive
  are no longer identical.)
* **Workflow scaffolding** — per-workflow step checklists (with required/optional
  status colouring), enumerated step titles, a disappearing pixel-size gate, and
  status circles on required/optional inputs and actions.
* **SpIDA** — spatial intensity distribution analysis (density, quantal brightness,
  oligomeric state) for confocal data; an exact port of the reference MATLAB with a
  monomer calibration, acquisition-modality guardrails, and ground-truth validation.
  Toolbox → Advanced Analysis → Molecular Counting.
* **Number & Brightness (N&B)** — the camera / time-series counterpart to SpIDA
  (Digman 2008) for widefield / TIRF / sCMOS data: per-pixel number and brightness
  maps plus an ROI summary, with scalar gain/offset/read-variance detector correction
  and a global bleaching detrend. Toolbox → Advanced Analysis → Molecular Counting.


Outstanding & Noted (near-term, worth tackling)
------------------------------------------------

Concrete, mostly self-contained items surfaced during recent audits:

.. rubric:: FIJI-style independent multi-image comparison (architectural — pinned)

PyCAT can now load images without clearing (add-without-clear) and toggle napari's grid
mode for side-by-side viewing in ONE viewer (shipped) — but those layers still share one
camera and one set of dim sliders. True FIJI-style comparison, where multiple images live
in independent windows each with their own zoom, dims, and analysis state, cuts against
PyCAT's single-``active_data_class`` architecture (the whole analysis pipeline assumes one
active dataset, and loads auto-clear by default). Delivering it properly means letting
``central_manager`` hold multiple datasets and letting analyses target a chosen one — a
significant, cross-cutting change to evaluate carefully rather than rush. Pinned as an
architectural project; the grid-view + add-without-clear path covers most same-modality
comparison needs in the meantime.

.. rubric:: Multi-scene / position scene-switcher (follow-up to the context-aware opener)

The context-aware opener parses X/Y/Z/C/T/P and routes multi-position files to the stack
loader, which already handles the sibling-file position case (separate .ims per position →
tagged layers). What remains is a true SCENE SWITCHER for a single multi-scene file (e.g. a
CZI with SizeS>1): a dropdown to lazily load one scene/position at a time (low-memory,
consistent with the data-local thesis) rather than overlaying or scrubbing positions. Needs
a scene-aware lazy wrapper + a small UI control; deferred to its own focused pass so it
doesn't destabilise the loader.

.. rubric:: Progress-bar audit (materialization-progress helper shipped; per-method rollout pending)

A reusable phased-progress mechanism now exists (``PhasedProgress`` in ``ui_utils`` +
``progress_callback`` on ``materialize_stack`` / ``as_full_array``) that maps a
materialization phase and a work phase onto ONE continuous 0→100% bar, fixing the
"bar reaches 100% twice" confusion. It was applied first to VPT bead detection (the
parallel-detection pass now fills 0→70% and the scoring pass 70→100%). Remaining work,
to be done per-method (not as a blanket sweep):

* Wire the materialization ``progress_callback`` into the seven UIs that materialize a
  stack synchronously before a worker starts (frap, condensate_physics, fusion,
  invitro_bf, brightfield, invitro_fluor, temperature) so large stacks show a
  "Materializing…" indication instead of a frozen 0%.
* Add progress bars to the zero-bar UIs that do slow work: ``contrast_cascade_ui``,
  ``fd_curve_ui``, ``data_qc_ui``.
* Audit the core cell/condensate runners in ``ui_modules`` (currently under-covered) and
  add determinate bars where per-cell / per-object loops make progress measurable.
* Sweep for any remaining indeterminate/fake bars that could be determinate.

.. rubric:: Documentation audit (user feedback — docs drifting from current GUI)

Tester feedback (Abhradeep) that the instruction docs no longer match the software:

* "Measure lines" cannot be found from the current instructions — findability / renamed.
* Instruction-page images do not match the current test data (stale screenshots).
* Module names differ between the docs site and the GUI, e.g. "Condensate segmentation"
  (docs) vs "sub cellular object segmentation" (GUI) — reconcile naming across docs and UI.

These are documentation fixes (not code); tracked here so they are not lost.

.. rubric:: Time Series In Vitro platform — specialised per-condensate analyses (foundation shipped)


The Time Series In Vitro Object Analysis (Fluorescence) module provides the 2D+t
foundation: per-frame segmentation, fusion-aware condensate linking, per-condensate
temporal *object records* (size/intensity/shape vs time), and whole-field trajectories
(Φ, partition, C_sat vs time). Each object record is the durable structure the following
specialised analyses attach their own time-series to (build each as its own module
against the foundation; all are import-and-analyse, per the "own the downstream
quantification" positioning):

* **Interior bubbling** — detect and track void/vacuole formation and motion *inside* a
  condensate over time (interior morphodynamics distinct from the outer boundary).
* **Catalysis kinetics** — per-condensate intensity-vs-time of a fluorescent reporter
  whose signal is driven by catalysis; fit reaction kinetics per object.
* **Internal flow** — bead diffusion *within* a condensate tracking interior flow fields
  (RICS/STICS-adjacent; composes with the fluctuation-spectroscopy roadmap family and
  the VPT machinery).
* **Fiber growth off the interface** — nucleation and elongation of fibers from the
  condensate boundary; length/rate per object over time.
* **Contrast cascade** — the contrast-cascade method, applied per tracked object.

Design note: these are *views* on the shared per-condensate object record (the biological
object model direction), not disconnected pipelines — each new method enriches the same
temporal object rather than emitting an isolated CSV.

.. rubric:: Temporal-projection dynamics proxy (cheap pre-tracking mobility screen)

A short-window time-projection encodes motion into a measurable width: a MAX (or STD)
projection over a window of frames smears each object by how far it moved, so the projected
width, broadened beyond the single-frame footprint, *is* a motion estimate. Recovered in
quadrature: ``motion_sigma = sqrt(sigma_projected**2 - sigma_singleframe**2)``. Validated in
VPT (2026-07) as the basis for the auto linking-distance estimate — it measures per-frame
bead motion **without linking any tracks** (short window → per-frame step; all frames → the
total displacement envelope).

The trick generalises to the time-series / condensate pipelines as a near-free mobility
screen *before* any tracking: stationary objects stay sharp, mobile ones blur, giving a
per-object "is there dynamics here and roughly how much" readout. A STD/variance projection
is especially natural — its per-pixel value *is* temporal variability (the same signal the
VPT hot-pixel mask reads, just used for the opposite purpose). Caveat: it is a *proxy*,
valid when motion << object size per window (small blur perturbation on the footprint); for
fast movers or long windows it saturates into the total envelope, so it complements tracking
rather than replacing it — a cheap first pass, not a substitute for MSD. Before building:
sweep the time-series tools for any existing std/variance-over-time projection rather than
assuming none exists.

.. rubric:: Audit: acquisition parameters derived per data type and metadata structure

Frame interval, pixel size, exposure, Z step and bit depth are now captured at the
load layer into ``data_repository['file_metadata']['common']`` from the file's real
metadata (for MicroManager files, the authoritative per-frame ``ElapsedTime-ms`` deltas,
with median + IQR + the full delta array preserved). The next step is to audit every
method that consumes an acquisition parameter and confirm it derives that parameter
**correctly for the specific data type and that type's metadata structure**, reading
from the single ``file_metadata`` source rather than re-deriving or re-defaulting:

* **Frame interval** — VPT Step 5 (microrheology), time-series condensate, FRAP, any
  kymograph/tracking path. Confirm each pulls ``frame_interval_s`` from
  ``file_metadata['common']`` and that the value is right for the file's format (OME
  ``TimeIncrement`` vs per-plane ``DeltaT`` vs MicroManager ``ElapsedTime-ms`` deltas
  vs nominal ``Interval_ms``). Never parse free-text OME ``<Description>`` for timing.
* **Per-frame (non-uniform) cadence** — with the full ``frame_deltas_s`` array now
  captured, add a VPT toggle to feed the true per-frame deltas into the MSD lag-time
  axis instead of a single median, and validate it against Gable's reference notebook
  (``VPT_04062022_XML``). Currently deltas are captured and displayed only.
* **Pixel size** — confirm the µm/px used by each spatial measurement matches the
  file's metadata / user gate for that data type.
* **Exposure, Z step, bit depth** — confirm any method that assumes these reads them
  from ``file_metadata`` rather than hard-coding a default.

Goal: one source of truth per acquisition parameter, correctly extracted per data
type, with provenance visible in the metadata panel. Do this as a careful pass so no
method's current behaviour is silently changed without validation.

.. rubric:: In-vitro brightfield segmentation — generalize across regimes (awaiting test data)

The texture (local-std + watershed) method added in v1.5.231 was optimized against ONE
image regime: dense small condensates, some out-of-focus and ring-like. Brightfield
condensate images span several regimes that need testing before we commit a segmentation
strategy for each. Do NOT implement until representative test data is supplied for each:

* **Sparse + large droplets** — likely fine with either intensity or texture; verify the
  texture window and watershed don't over-fragment big smooth droplets.
* **Small + sparse** — verify sensitivity (texture threshold) doesn't miss faint droplets.
* **Large + dense, semi-overlapping due to focus** — the hard case; watershed splitting and
  ring-fill behaviour need tuning so overlapping defocused droplets separate correctly
  without either merging or shattering.
* **Fractal structures / irregular aggregates** — round-object and circularity assumptions
  break here; these need a different acceptance filter (the current min-circularity /
  solidity gates would reject legitimate irregular aggregates). Probably a separate
  "irregular / aggregate" mode that keeps connected components without imposing roundness.

* **"Guess the condition" button** (planned): inspect the image and recommend a
  method + parameter preset (sparse/dense × small/large × regular/irregular). Candidate
  signals: object density, size distribution, defocus/ring fraction, and
  texture-energy vs intensity-contrast ratio. Build only after the per-regime methods are
  validated on real data for each condition.

.. rubric:: Platform consolidation (external architecture review, 2026-07)

An external review found PyCAT has crossed from "condensate segmentation tool" to
"quantitative microscopy workbench," and that the remaining work is consolidation, not
new features: the implementation is ahead of the architecture. The unifying insight is
that three separately-diagnosed problems are the same refactor — **make analysis modules
self-describing.** A module that declares
``name -> category -> required layers -> parameters -> run function -> output schema ->
replay function`` in one place simultaneously resolves the UI monolith, the batch-registry
monolith, and the output-schema gap. Sequenced by cost and dependency:

* **[DO NOW, cheap] Spatial Phenotyping menu family.** Group the five spatial modules
  (``spatial_metrology``, ``spatial_acf``, ``organizational_metrics``,
  ``dynamic_spatial``, ``morphological_complexity``) under one visible conceptual
  category in the UI and manuscript. Files stay separate (correct modular design); this
  is a naming/grouping and discoverability fix only. Caveat: group them as "methods that
  characterise spatial organisation," NOT as interchangeable — SACF (correlation length),
  Ripley's K (clustering), and fractal dimension (boundary roughness) answer different
  biological questions. UX grouping good; implying equivalence in the paper is wrong.

* **[DO NOW, cheap] Stability tiers in README/docs.** Label every module
  Validated / Experimental / Developer preview. Maps directly to test coverage:
  known-answer regression tests (Pearson, FRAP, partition K, refinement bit-identical)
  mark Validated; no-numerical-validation modules are Experimental; anything added
  without golden-master fixtures (e.g. fibril suite) is Developer preview until tested.
  Protects against overclaiming — the failure mode reviewers punish.

* **[DO NOW, ongoing] Biological-relevance tooltips.** Most analysis widgets lack tooltips
  explaining the biological use case. A tooltip must answer *what question does this
  answer / what does the output mean / when NOT to use it* — not restate the method name.
  Consistent with PyCAT's anti-black-box teaching philosophy. Author as structured text
  (dict/docstring convention) not inline ``setToolTip`` strings, so the same content
  feeds the future module registry's ``description`` field. Domain writing only the
  authors can do; runs parallel to the manuscript (same use-case articulation as the
  methods section). Not a blocker.

* **[START NOW, foundational] Shared output schema — highest-value item.** Standardise a
  long/tidy results format so every phenotyping axis merges into one "phenotype
  fingerprint":
  ``file_id | condition | channel | frame | z | cell_id | object_id | metric_name |
  metric_value | unit | module | parameters_hash``.
  Long format (one row per measurement) not wide, so a new metric never forces a schema
  change. ``parameters_hash`` should key into the batch JSON record — closing the loop
  between output schema and batch replay. Add a documented pivot to widen it into a
  per-object feature matrix for stats/clustering (the fingerprint is the pivot, not the
  tall CSV). This is the foundation the registry stands on (schema is the hard part,
  dispatch is easy once outputs are uniform) and a strong standalone manuscript
  reproducibility claim.

* **[DEFER to post-publication] Self-describing module registry.** Replace the two
  monoliths (``ui_modules.py`` widget hub, ``batch_step_registry.py`` replay hub) with a
  registry where each module declares GUI action + headless replay + parameter schema +
  output schema in one place. Correct mature-platform architecture, but a large invasive
  refactor with no user-visible payoff — wrong to attempt right before the Nature Methods
  submission. The completed mixin split was the correct intermediate step. Build the
  output schema (above) first; it is the registry's hardest prerequisite.

.. rubric:: Test expansion (highest-value next tests)

The external reviewer could not complete a full suite run (GUI/scientific-stack imports
stall without a display — expected; also motivates a headless CI config with
``QT_QPA_PLATFORM=offscreen``). Highest-value additions, several of which double as
manuscript evidence:

* Lazy IMS frame-access correctness (``[0,0]`` -> (Y,X), ``[0,:]`` -> (Z,Y,X),
  ``[:,:]`` -> (T,Z,Y,X)). We fixed the singleton-axis bug this cycle; a golden-master
  test would prevent regression and reuses the validation logic already written.
* Batch JSON replay == GUI output — tests the reproducibility guarantee directly; this
  test *is* the evidence for the replayable-workflow claim.
* Bounding-box segmentation == whole-image reference (we assert max diff 0.0 at
  6·ball_radius pad but have no standing test).
* QC metrics on synthetic pass/warn/fail images — doubles as the "bad data gallery"
  fixtures.
* Spatial metrics on known centroid arrangements (regular grid -> known Ripley's K;
  Poisson field -> known SACF).
* Video export produces a valid MP4 from a lazy stack.

.. rubric:: Release hygiene (FIXED in v1.5.190)

* The hand-zipped working snapshots contained ``.git/``, ``__pycache__/``, ``.coverage``,
  ``.DS_Store``, ``dist/``, ``PKG-INFO`` etc. Root cause: the project migrated to
  **hatchling**, which ignores ``MANIFEST.in`` (a setuptools mechanism) — so the careful
  ``global-exclude`` rules there were dead. Fixed by adding an explicit
  ``[tool.hatch.build.targets.sdist]`` section with ``include``/``exclude`` lists, so
  every ``python -m build`` now produces a clean tarball by construction. The wheel was
  already clean. Verify the GitHub release tarball is clean before the paper release.

* **Status-marker completion** — a few action buttons were left unmarked because their
  required/optional status was ambiguous: the Z-Stack per-section generic run button
  (built dynamically with a reused label), any single "Run" button that spans multiple
  analyses (Dynamics / phase-diagram / frame-quality style), and the per-workflow
  Spatial Metrology sub-run-buttons inside the standalone workflows. These need a
  required-vs-optional decision, then wrapping with ``button_with_circle``.
* **Step-title enumeration for the remaining built-in workflows** — Condensate is the
  completed reference; time-series, colocalization, general, and fibril still need the
  ``_stage_step`` treatment against their pipeline numbering. (Note: the mechanism must
  handle both title styles — ``add_text_label(bold=True)`` and ``QGroupBox``-titled
  builders via ``_consume_step_label``.)
* **Toolbar / menu-bar redesign (candidate)** -- PyCAT's menus (Analysis Methods,
  Toolbox, Open/Save, Clear, Home, Metadata) currently live on napari's native menu bar
  with a "PyCAT" section marker as the divider (v1.5.195). A fuller redesign could move
  them onto a dedicated PyCAT toolbar (one already exists for Batch Run / Save Config /
  Layers in batch_processor.py) for cleaner separation, and reconsider whether the three
  dropdowns should become something other than popup menus. Low urgency; the marker
  resolves the immediate confusion.
* **BioIO migration** — still on AICSImageIO (see the File I/O section below); a larger
  infrastructure change, best triggered by a concrete new-format need.
* **Image Quality Advisor / QC module** — an in-app quality assessment layer that
  reports *interpretation and recommendation*, not just raw metrics (dynamic range,
  noise, focus/PSF, illumination uniformity, photobleaching, segmentation readiness).
  A ``pycat/qc/`` module could back both a batch scanner and the live advisor from one
  source of truth, doubling the labelled examples as golden-master test fixtures.
* **3D volume rendering presets** — expose/configure napari's native 3D view
  (volume/MIP/iso-surface, clipping planes, rotation-movie export) with
  publication-oriented presets; mostly configuration plus PyCAT value-adds.
* **Analysis-aware kymographs** — beyond classic line-scan: colocalization,
  object-tracking (diameter/intensity/partition vs time), FRAP, and phase-boundary
  kymographs for maturation / non-equilibrium dynamics.

.. rubric:: Known issues

* **Scale bar uses napari's built-in overlay on the main load path (low priority).**
  On image/stack load, ``_enable_auto_scale_bar`` turns on napari's built-in
  ``viewer.scale_bar`` and sets its unit via ``scale_bar.unit``. This works today only
  because the code deliberately avoids the one call (``Layer.units``) that used to black
  out the canvas on lazy stacks. Two forces make this fragile long-term:

  1. **Deprecation.** napari is deprecating ``scale_bar.unit`` (PR #9007); the unit label
     is moving to being derived from ``Layer.units`` — which is exactly the API that
     triggered the black-canvas refit here. So the supported path forward reintroduces the
     risk category PyCAT is currently side-stepping.
  2. **Coupling to auto-fit.** The built-in path assigns ``layer.scale`` and fires
     alignment/units events on load, which has repeatedly interfered with the
     open-to-fit-canvas camera logic (see the auto-fit troubleshooting, versions
     1.5.210–1.5.213).

  PyCAT already ships a self-contained alternative, ``draw_custom_scale_bar``
  (``ui_utils.py``): a Shapes-layer rectangle drawn in data coordinates that is immune to
  both the black-canvas bug and the ``scale_bar.unit`` deprecation (a bar of N data pixels
  always represents ``N × pixel_um`` µm, independent of napari's unit machinery). It is
  currently wired only into the temperature/movie-export workflow. **Deferred decision:**
  unify on the custom scale bar across the main load path too. Benefits: removes the
  deprecation exposure, eliminates the ``Layer.units`` black-canvas category entirely, and
  decouples the scale bar from the auto-fit machinery. Tradeoff: the custom bar is a real
  entry in the layer list (``PyCAT Scale Bar``) rather than a canvas overlay, so it must be
  excluded from analysis-layer dropdowns and cleaned up via ``remove_custom_scale_bar`` on
  reload. Low priority — the current built-in path is functional; migrate before adopting
  a napari version that removes ``scale_bar.unit``.

.. rubric:: Super-resolution data processing workflows

Super-resolution (SR) is a natural extension: for the image-based methods the input
contract and lazy-loading infrastructure are largely shared with PyCAT's existing raster
pipeline. **The critical distinction — and the thing to get right so this doesn't
reproduce the incoherence the naming/methods audits target — is that "super-resolution"
spans two fundamentally different data models.** They must be handled as two separate
categories, not lumped together:

**Category A — image-based / raster-grid SR (drop-in compatible).**
These consume a conventional diffraction-limited image *sequence* on a pixel grid and
produce an *enhanced image on a (usually finer) pixel grid*. The output is still a raster
image, so it flows into every downstream PyCAT tool (segmentation, phenotyping, spatial
metrology) unchanged — it is simply better-resolved. Candidates:

* **Deconvolution** (Richardson-Lucy, Wiener) — PSF-based sharpening of a single image or
  stack. The lowest-barrier entry point; no blinking or special probes required. A good
  first SR feature because it is broadly applicable and the algorithm is well-established.
* **SRRF (Super-Resolution Radial Fluctuations)** — computes radial symmetry
  ("radiality") per frame across a short sequence of *conventional* fluorophores, then
  temporally analyses the stack. Works on standard dyes/FPs and standard widefield/TIRF
  hardware, which makes it attractive for condensate work where photoswitchable probes are
  impractical. Output is a super-resolved raster image. Reference: NanoJ-SRRF (Henriques
  lab).
* **SOFI (Super-resolution Optical Fluctuation Imaging)** — computes higher-order temporal
  cross-cumulants of independently blinking emitters over the image sequence as a whole.
  Tolerates high labelling density and needs far fewer frames than localization methods
  (hundreds–thousands vs tens of thousands), at the cost of lower ultimate resolution.
  nth-order cumulant narrows the effective PSF by ~sqrt(n). Output is a raster image.
* **Structured Illumination (SIM) reconstruction** — if raw SIM stacks are ever a target;
  reconstruction produces a raster image. Lower priority (needs specific acquisition).

For Category A the PyCAT-side work is mostly: an SR-reconstruction step that takes an
image/stack layer and emits an enhanced image layer, wired through the same batch-record /
replay and cache infrastructure as any other preprocessing step. These are, in effect,
advanced preprocessing methods.

**Category B — localization-table SR (genuinely different data model).**
PALM / STORM / (d)STORM and the PAINT family (DNA-PAINT, and PAINT variants) do NOT
produce images. They analyse a long sequence of frames in which sparse single molecules
blink, fit each to sub-pixel precision, and emit a *localization table* — a list of
``(x, y, [z], intensity, uncertainty, frame, ...)`` coordinates. Treating this table as an
image is a category error: rendering to a pixel grid is a *visualization choice* applied
*after* the fact, not the native representation. Supporting this well means:

* A localization-table data type (import from common formats — ThunderSTORM CSV, Picasso
  HDF5, etc.), distinct from the image layer type.
* Localization-native operations: drift correction, filtering by uncertainty/photons,
  grouping/merging of repeated localizations, and cluster analysis (DBSCAN, Ripley's K on
  points) — which connects naturally to PyCAT's existing spatial-phenotyping suite, since
  those spatial statistics are *already point-based* and would apply directly to
  localizations.
* Rendering to a raster image (histogram or Gaussian-blur render) as an *export/
  visualization* path, at which point the result can re-enter Category A's raster pipeline
  if desired.

**Sequencing and scope note.** Category A (especially deconvolution, then SRRF/SOFI) is the
low-friction, high-value near-term target: it reuses the existing raster pipeline, lazy
loading, and batch/replay machinery, and directly benefits condensate imaging with
conventional probes. Category B is a larger architectural addition (a new data model plus
its own operations) and should be scoped separately — likely post-publication, and only if
a real user presents localization data, mirroring the OME-Zarr "conditional future add"
stance. The spatial-phenotyping overlap is the strongest argument for eventually
supporting Category B, because PyCAT's point-based spatial statistics are already most of
what localization-cluster analysis needs.


Scientific validity backlog (from the 2026-07 external code audit)
------------------------------------------------------------------

An external audit reviewed the scientific methods module by module. The **bugs** it
surfaced were verified against ground truth and fixed in 1.5.378-1.5.385 (mis-named
quantities, a FRAP mobile fraction that reported a fully mobile protein as "70 %
immobile", moduli plots that manufactured elasticity from noise, an SNR diagnostic that
told users background subtraction destroyed their data, distribution selection by R-squared
on a histogram, a C_sat fit that could return a negative concentration, and scientific
modules that could not be imported without a GUI so their tests never ran).

What remains are the audit's **architectural** recommendations: real, but builds rather
than bug fixes. They are recorded here so they are not lost, roughly in the audit's own
priority order.

.. note::

   One audit claim was tested and **rejected**: that ``compute_msd``'s IQR fence selects
   tracks on the outcome variable (MSD -> D -> viscosity). Measured on a synthetic mixed
   population it rejects honest *long* tracks at 2 % (the noise-tail rate of any IQR
   fence), honest *short* tracks at 22 %, and *mis-linked* tracks at 70 % — i.e. it does
   what it claims. On clean Brownian data it biases D by +1.1 %. No change was made; the
   reasoning is in the 1.5.380 changelog so it is not "fixed" later by someone reading the
   audit.

.. rubric:: How to resume any item below

Each item names **where** (file:line), **what done looks like** (an acceptance test that
can be written before the code), and **what is already settled** (so a question is not
re-litigated). Where an item lacks an acceptance test, that is itself the first task:
*write the failing test, then fix it.*

Standing acceptance criteria for the whole backlog:

* Every fix ships with a **ground-truth test** — synthetic data where the right answer is
  known by construction. This is how every bug in 1.5.378-1.5.385 was caught and how each
  fix was verified; several "obvious" fixes were **wrong on first attempt** and only the
  ground-truth test revealed it.
* Every new scientific function must be **importable with no napari and no Qt**
  (``tests/test_headless_science.py`` enforces this for guarded modules; add new ones to
  its list).
* A number that can be wrong in a *named* way should say so — see
  ``pycat.utils.measurement``.

.. rubric:: BUG (not architecture) — puncta SNR is the same un-subtracted ratio fixed in 1.5.379

**Where:** ``segmentation_tools.py:1342-1344`` in ``puncta_refinement_filtering_func``::

    local_snr  = img_dilated_object_mean / img_local_bg_std     # no bg subtraction
    global_snr = img_dilated_object_mean / cell_bg_std          # same

This is the **identical** defect corrected in ``pipeline_snr_tools`` (1.5.379): the
background mean is not subtracted, so the ratio is inflated by the camera pedestal. There,
it was a misleading *diagnostic*. Here it is a **rejection criterion deciding which puncta
survive** — so ``local_snr_threshold`` means something different on every camera, and
something different again after a background-subtraction step. Measured on a synthetic
image with a real contrast of 50 over noise sigma 5: pedestals of 0/100/500/2000 counts
gave "SNR" of 28/78/282/1049 — the same image.

**Fix:** use ``(object_mean - background_mean) / background_std`` (contrast-to-noise), and
recalibrate the default thresholds against it — they were tuned to the inflated quantity,
so changing the formula without re-tuning will change which puncta pass.

**Acceptance:** on a synthetic punctum field with known positions and a known contrast,
detection must be **invariant to an added constant pedestal**. Currently it is not.


.. rubric:: Immediate — finish the decoupling and the provenance model

* **Decouple the remaining 20 scientific modules from napari/Qt.** 28 of 48 are clean and
  ``tests/test_headless_science.py`` guards 13 of them.

  **Where** (as of 1.5.385) — ``clean_spot_detection_tools``,
  ``correlation_func_analysis_tools``, ``data_viz_tools``, ``fd_curve_tools``,
  ``fft_bandpass_tools``, ``fusion_tools``, ``gaussian_localization_tools``,
  ``general_image_tools``, ``intensity_profile_tools``, ``layer_tools``,
  ``molecular_counting_tools``, ``obj_based_coloc_analysis_tools``, ``segmentation_tools``,
  ``spatial_acf_tools``, ``spatial_randomness_tools``, ``temperature_tools``,
  ``timeseries_condensate_tools``, ``ts_cellpose_tools``, ``two_channel_coloc_tools``,
  ``video_export_tools``.

  **Recipe** (proven on 8 modules already): notifications -> ``pycat.utils.notify``;
  ``napari.layers`` isinstance checks -> a lazy ``_napari()`` accessor called inside the
  viewer-facing function; Qt widgets -> ``try: from PyQt5 ... except ImportError`` with a
  stub class that raises only if the dialog is actually opened; ``pycat.ui.*`` helpers ->
  imported at call time. The coupling is **transitive**, so start at the leaves of the
  import graph (``segmentation_tools`` and ``timeseries_condensate_tools`` block the most).

  **Acceptance:** add each module to ``SCIENTIFIC_MODULES`` in
  ``tests/test_headless_science.py`` and the test passes. Done when all 20 are listed.
* **Extend the ``Measurement`` model beyond viscosity.** ``pycat.utils.measurement``
  (1.5.384) gives a value its parameters (with ``ParameterSource`` provenance), its
  assumptions (``HOLDS`` / ``VIOLATED`` / ``UNCHECKED``), its uncertainty and a
  ``ValidationLevel``. ``viscosity_measurement`` demonstrates it.

  **Where** the same pattern is owed (file:line as of 1.5.385):

  * ``invitro_tools.py:860`` ``partition_coefficient_field`` — assumptions: neither phase
    saturated; background subtracted before the ratio; dilute phase measured locally, not
    from a global percentile.
  * ``invitro_tools.py:702`` ``estimate_phase_boundary`` — assumptions: Phi is a true volume
    fraction (it is **not**, from a 2-D image — see 1.5.378); the concentration axis is
    calibrated; the series spans the boundary.
  * ``frap_tools.py:187`` ``fit_frap_recovery`` — assumptions: the normalisation is recorded
    (1.5.381); the bleach was fast relative to recovery; the plateau was reached.
  * ``condensate_physics_tools.py:1341`` ``compute_moduli_evans`` — assumptions: 2D-to-3D
    isotropy; the frequency band is within the supported range (see the VPT rubric).
  * N&B brightness in ``nb_tools.py`` — assumptions: camera calibration supplied; a
    monomeric reference exists. Without both, the output is **apparent** brightness.

  **Acceptance:** each returns a ``Measurement`` whose ``interpretability`` becomes
  ``NOT_INTERPRETABLE`` in a constructed case where an assumption genuinely fails (e.g. a
  saturated dense phase for the partition coefficient), and ``INTERPRETABLE`` when it does
  not.
* **Uncertainty everywhere a number is reported.** Present in the MSD fit and the new phase
  boundary; missing from partition coefficients, coarsening exponents, FRAP plateaus and
  N&B outputs.

.. rubric:: Validation framework — the tier that would earn the labels

``ValidationLevel`` currently *asserts* a level. Nothing yet *earns* it. The audit's
three-layer scheme is the right one and would convert an assertion into a fact:

1. **Analytical** — Brownian tracks with known D; anomalous tracks with known alpha;
   Newtonian and Maxwell MSD curves with known moduli; FRAP curves from known parameters;
   images with known puncta positions/intensities/PSF; controlled colocalization overlap;
   N&B movies with known N and B; droplet masks with known size evolution.
2. **Imaging-realistic simulation** — add Poisson noise, sCMOS read-noise and offset maps,
   blur and axial defocus, illumination gradients, photobleaching, drift, finite-exposure
   motion blur, pixelation, saturation, object overlap, segmentation error.
3. **Experimental standards** — fluorescent beads (PSF, localization); glycerol/water
   viscosity standards (**VPT** — the single most valuable control PyCAT could adopt, and
   the "no host / full frame" mode exists precisely for it); monomer/dimer controls (N&B,
   SpIDA); immobilised dual-colour beads (registration, colocalization); known diffusion
   standards (FRAP).

**Where:** a new ``tests/validation/`` tier, plus a synthetic-data generator module (the
existing ``tests/fixtures_synthetic.py`` is the seed). ``ValidationLevel`` in
``pycat.utils.measurement`` is the label each method would then *earn* rather than assert.

**Start here — the cheapest, highest-value single test:** ``fit_anomalous_diffusion`` +
``viscosity_from_diffusion`` against Brownian tracks with a **known D**. It exercises the
whole chain (MSD -> D -> Stokes-Einstein), the truth is known by construction, and it takes
an afternoon. Then add: known alpha (anomalous), a Maxwell fluid with known moduli, FRAP
curves from known parameters, and puncta images with a known PSF.

**Acceptance (per method):** recovers the known parameter within a stated tolerance, and the
*reported uncertainty covers the truth at the stated rate* (a 95 % interval must contain the
truth ~95 % of the time across repeated realisations — an interval that is merely *reported*
but not *calibrated* is worse than none, because it invites confidence). Only then may the
method claim ``ANALYTICALLY_VALIDATED``.

**Then layer 2:** re-run every layer-1 test with Poisson noise, an sCMOS offset/variance map,
blur, drift and bleaching switched on. A method that passes layer 1 and fails layer 2 is a
method that works on paper and not on a microscope — that is exactly the gap worth knowing
about.

This is the highest-leverage remaining item: it is what separates "the code runs" from "the
number is right".

.. rubric:: Puncta detection — staged, probabilistic

The current refinement applies a sequence of hard thresholds (kurtosis, local/global SNR,
gradient magnitude, intensity-width, local thresholding, size filters). A biologically real
punctum can fail any one of them for several unrelated reasons — low expression, local
background, defocus, axial motion, saturation, genuinely diffuse morphology — and the
interaction of the rules makes the final selection hard to interpret.

Recommended architecture::

    candidate generation (permissive)  ->  per-candidate feature table
      ->  probabilistic classification  ->  resolution-aware reporting

with a feature table per candidate (peak and integrated intensity, local background and
noise, fitted PSF width, fit residual, eccentricity, edge distance, local contrast,
temporal persistence, saturation overlap, axial-defocus evidence), classified by an
interpretable logistic model, a calibrated random forest, or explicit quality tiers —
rather than deleting candidates sequentially with hard cut-offs.

**Where:** ``segmentation_tools.py:1192`` ``puncta_refinement_filtering_func`` (the
cascade), and its SNR bug at 1342-1344 (see the BUG rubric above — fix that first, since
the thresholds are calibrated against the wrong quantity).

**Acceptance:** on a synthetic punctum field with known positions, intensities and PSF,
report a precision/recall curve. The staged detector must **dominate** the current cascade
(equal or better recall at every precision) — if it does not, do not ship it. Each rejected
candidate must carry a *reason*, so a user can see why a punctum they can see was dropped.

.. rubric:: Resolution-aware measurement, made mandatory

``partial_volume_tools`` already provides PSF estimation, sub-resolution detection,
effective sample size, weighted measurement and the size-dependent bias model. It is not
yet *central*. The main condensate pipelines should automatically attach
``equivalent_radius / PSF_radius`` and classify each object as **unresolved**, **marginally
resolved** or **adequately resolved** — reporting integrated intensity and localisation for
unresolved objects rather than apparent morphology or concentration. Upscaling must never
change the classification.

**Where:** the consumers are ``feature_analysis_tools.py`` (``cell_analysis_func``,
``puncta_analysis_func``) and the 7 remaining ``regionprops(..., intensity_image=...)`` call
sites listed in ``docs/audits/upscaling_and_measurement_audit_2026-07-10.md``. The engine
(``partial_volume_tools``) is done.

**Acceptance:** an object of radius ~1 PSF sigma is classified **unresolved**, and its
morphology columns are ``NaN`` rather than a number; the classification is *identical*
whether or not the image was upscaled first (this is the test that catches the whole
class of error).

.. rubric:: FRAP — model identifiability

The reaction-diffusion model jointly fits mobile fraction, bound fraction, D and k_off. A
single recovery curve commonly cannot identify all four, and a numeric Hessian does not
detect *structural* non-identifiability — it will happily return standard errors for a
parameter the data cannot constrain. Needed: profile likelihood; a parameter-correlation
matrix; fixed-k_off / fixed-D / pure-diffusion alternatives; AICc or cross-validation;
simulation-based identifiability checks using the *actual* acquisition schedule; and an
explicit **"descriptive only"** state when the models are indistinguishable.

Also: normalisation-aware bleach-depth estimation (the 30-point quadratic extrapolation is
unstable and sampling-rate dependent), reference-trace quality scoring for the photofading
correction, and ROI-geometry checks for the Soumpasis (circular) and rectangular models.

**Where:** ``frap_tools.py`` — the reaction-diffusion fit and its numeric Hessian.

**Acceptance:** generate FRAP curves from **known** (mobile fraction, bound fraction, D,
k_off) using the *actual* acquisition schedule (frame interval, bleach duration, number of
pre-bleach frames). The fit must either (a) recover all four within a stated tolerance, or
(b) report ``descriptive_only`` — and it must **not** return four confident numbers when the
data cannot constrain them. Construct at least one deliberately non-identifiable case (fast
binding, slow sampling) and assert that it is *detected*, not fitted.

.. rubric:: VPT — heterogeneity, reference frame, identifiability

* **Bulk-sampling is assumed, not demonstrated.** Interface exclusion helps but does not
  prove homogeneous bulk sampling. Needed: viscosity vs distance from the interface;
  D vs local condensate intensity; D vs bead brightness / aggregate class; spatial
  variation across the host; trajectory confinement; immobile/stuck fractions. Report a
  single viscosity **only** if these are reasonably homogeneous. (The ``Measurement``
  assumption ``bulk_sampling`` exists and is currently accepted on trust.)
* **Drift correction conflates reference frames.** Centre-of-mass correction can subtract
  genuine coherent material motion. Offer explicit modes — stage drift from an external
  reference, global image registration, host-condensate translation, median probe
  displacement, none — and **store which physical frame of reference was used**.
* **D-alpha-sigma_loc covariance.** Over a short lag range these are strongly correlated.
  Add profile-likelihood or bootstrap intervals and a correlation diagnostic. Do not infer
  anomalous diffusion from an unconstrained point estimate of alpha — in viscous-dominated
  media the true alpha *is* 1, so a fitted alpha far from it usually indicates linking
  artefacts, not physics.
* **Evans conversion.** The plots no longer hide negative moduli (1.5.380). Still owed:
  explicit output classes (supported / edge-affected / sign-inconsistent / insufficiently
  constrained frequency ranges) and acknowledgement that the 2D-to-3D isotropy conversion
  is an assumption.
* **Active microrheology module** (optical tweezers, C-Trap). This is the *correct*
  technique when the G'/G'' crossover is the question — passive VPT cannot reach it for
  condensate-range viscosities. The moduli plot now says so; the module is the follow-through.

**Where:** ``vpt_tools.py`` (``viscosity_measurement`` already declares the
``bulk_sampling`` assumption and currently accepts it **on trust** — that is the hook);
``condensate_physics_tools.py:1341`` for the Evans output classes; ``vpt_ui.py`` for the
drift-mode selector.

**Acceptance (bulk sampling):** the ``bulk_sampling`` assumption must be *computed*, not
passed in. Concretely: correlate per-track D against distance-from-interface and against
bead brightness; if either correlation is significant, mark the assumption ``VIOLATED`` and
the viscosity ``NOT_INTERPRETABLE``. Construct a synthetic case where beads near the
boundary genuinely diffuse more slowly and assert it is caught.

**Acceptance (alpha):** on Brownian tracks with a known alpha = 1, the fitted alpha must not
be reported as anomalous. Add a profile-likelihood or bootstrap interval for alpha and assert
it **contains 1** — the current point estimate can wander from 1 through D-alpha-sigma
covariance alone, and in viscous-dominated media that is *always* an artefact.

**Acceptance (drift):** applying centre-of-mass drift correction to a synthetic dataset in
which the host condensate genuinely translates must **not** remove the real motion silently —
the chosen reference frame must be recorded with the result.

.. rubric:: Colocalization — question-first, with honest nulls

* **Classify the question before offering coefficients**: intensity covariation
  (Pearson/Spearman/ICQ); fractional enrichment (thresholded Manders); binary overlap
  (Jaccard/Dice); object association (centroid/interface distance with a null model);
  spatial displacement (cross-correlation and lag). At present a user can generate a large
  table of correlated metrics without knowing which one answers their question.
* **Threshold sensitivity.** Manders without a defensible threshold is dominated by
  background. Report M1/M2 over a threshold grid and quantify stability; distinguish masks
  derived independently from masks derived from the same images.
* **Null models.** Pixel scrambling violates spatial autocorrelation and inflates
  significance. Block scrambling is better, but the block size should be **inferred from the
  measured spatial correlation length**, not typed in. For object-based coloc, use spatial
  nulls constrained by cell shape, compartment, forbidden regions, object-size distribution
  and boundary preference — uniform randomisation inside a cell is usually not adequate.
* **Replication unit.** Pixels and objects within one cell are not biological replicates.
  Output hierarchical tables (pixel/object -> cell -> field -> experiment) and identify the
  correct inferential unit.

**Where:** ``pixel_wise_corr_analysis_tools.py`` (coefficients + the scrambling null),
``obj_based_coloc_analysis_tools.py``, ``two_channel_coloc_tools.py``.

**Acceptance:** on two channels that are **independent by construction** but each spatially
autocorrelated (smoothed noise), the null must return a non-significant p-value. Pixel
scrambling **fails this test** — that is the whole point. Block scrambling with a block size
inferred from the measured correlation length must pass it.

.. rubric:: N&B and SpIDA — calibration before absolute claims

* **N&B**: support per-pixel sCMOS offset and variance maps; account for gain and excess
  noise factor; estimate temporal autocorrelation and the *effective* independent frame
  count; detect immobile structure and slow drift; flag saturated and digitisation-limited
  pixels; consider covariance-based / time-lagged variance to reduce shot-noise bias.
  Until a camera calibration map **and** a monomeric reference are supplied, label the
  output **apparent brightness**. Replace the 4-frame minimum with explicit adequacy tiers
  (cannot compute / computes but unreliable / recommended / well sampled) — 4 frames is
  mathematically sufficient and scientifically useless.
* **SpIDA**: validate against synthetic images generated with the *same* PSF, detector
  noise, pixelation and occupancy assumptions; add exact or numerically-convolved
  single-particle basis functions; fit by likelihood/deviance rather than least squares on
  histogram counts; propagate background, gain and PSF uncertainty; reject multimodal or
  spatially heterogeneous ROIs. Return an assumptions/QC table alongside the fitted states.

**Where:** ``nb_tools.py`` (the 4-frame minimum is at lines 104 and 168), ``spida_tools.py``.

**Acceptance (N&B):** simulate a movie of *known* N and B with Poisson shot noise, an sCMOS
offset/variance map and a known gain; recover N and B within a stated tolerance. Then verify
the frame-count tiers are honest: at 4 frames the tool must say **"computes but unreliable"**
and demonstrate (by repetition) that the variance estimate is not stable.

**Acceptance (SpIDA):** recover a known monomer/dimer mixing ratio from images synthesised
with the same PSF, detector noise, pixelation and occupancy. Until it does, label the output
exploratory.

.. rubric:: In-vitro thermodynamics and dynamics

* **Partition coefficient**: estimate the dilute phase from an **annular / local** region
  per droplet, excluding the interface, neighbouring droplets, illumination gradients and
  out-of-sample background. Subtract the background offset **before** forming the ratio.
  **Detector saturation should invalidate the coefficient, not lower-bound it** — a
  saturated dense phase makes the ratio meaningless, not merely conservative. (Small,
  self-contained, and worth doing early.)
* **Coarsening**: a fitted radius-time exponent cannot distinguish Ostwald ripening,
  Brownian coalescence, sedimentation-driven coalescence, active growth, or *changing
  segmentation sensitivity* — that last one is the insidious case. Report the supporting
  signatures (number density vs time, total dense-phase area, size distribution,
  disappearance without contact, fusion-event rate, centroid velocity, sedimentation
  direction, mass/intensity conservation) and label the exponent **descriptive** unless
  they are consistent.
* **Fusion**: verify that a fusion actually occurred (two objects became one; no third
  entered; segmentation stayed stable; the post-fusion object reached a plateau; enough
  early-time samples exist) before fitting a relaxation time, and do not convert it to an
  inverse capillary velocity unless the geometric and material assumptions hold. Merge/
  fission classification from overlap alone confuses true fusion with axial crossing,
  threshold-induced joining, projection overlap and transient segmentation failure — use
  intensity conservation, contour evolution and pre/post topology to assign an event
  confidence.

**Where:** ``invitro_tools.py:860`` ``partition_coefficient_field`` (saturation + local
background — **small, self-contained, do this first**); coarsening and fusion fits in the
same module and ``fusion_tools.py``.

**Acceptance (partition):** on a synthetic image where the dense phase is **clipped at the
detector maximum**, the coefficient must be returned as *invalid*, not as a number. A
saturated ratio is not a lower bound — it is meaningless, because the numerator has been
truncated by an unknown amount.

**Acceptance (coarsening):** generate droplet populations under Ostwald ripening and under
Brownian coalescence with the *same* radius-time exponent, and show that the supporting
signatures (number density, total area, mass conservation) distinguish them. If they do not,
the exponent must be labelled descriptive.

.. rubric:: Spatial statistics and topology

* **Edge correction and null models** for Ripley's K/L, pair correlation and
  nearest-neighbour distance: irregular cell boundaries, spatially varying available area,
  object exclusion radius, inhomogeneous density, finite object size, multiple cells and
  fields. Report **Monte Carlo envelopes** from the relevant null, not just the observed
  statistic. An inhomogeneous Poisson or compartment-constrained null is usually more
  appropriate than complete spatial randomness.
* **Voronoi/Delaunay** metrics are unstable near boundaries: exclude or correct boundary
  objects and report the affected fraction.
* **Persistent topology** — the audit's view (and ours) is that this is the most
  *differentiating* direction available, and that threshold dependence is the blocker. The
  stronger design is ``image -> physically calibrated scale space -> filtration ->
  persistence -> summary`` rather than ``image -> one normalised threshold series ->
  topology``. Add persistence diagrams and landscapes, bootstrap stability, intensity- and
  scale-based filtration, explicit mask-boundary treatment, topology on synthetic nulls,
  separation of object topology from nuclear-territory topology, and a PSF-aware minimum
  feature persistence.

**Where:** ``spatial_randomness_tools.py``, ``spatial_acf_tools.py``, ``topology_tools.py``.

**Acceptance (nulls):** place objects with a *known* clustering strength inside an irregular
compartment. A CSR null must report significant clustering for objects that are, in truth,
uniformly distributed *within the available area* — that is the failure mode. The
compartment-constrained null must not.

**Acceptance (topology):** the persistence summary of a synthetic structure must be
**stable** under a change of intensity scaling and under added noise at the PSF scale.
Threshold dependence is the current blocker; this is the test that demonstrates it is
solved.

.. rubric:: Force-distance

Model fitting should account for force- and extension-calibration uncertainty, trap
compliance, baseline drift, loading-rate dependence and correlated residuals; select between
extensible and inextensible forms; and check persistence- vs contour-length identifiability.
Rip detection should return a confidence and its sensitivity to the smoothing parameters,
and smoothing should be defined relative to the physical loading rate and bandwidth rather
than a point count. Contour increments converted to nucleotide counts should **propagate
uncertainty** rather than round a point estimate.

**Where:** ``fd_curve_tools.py``.

**Acceptance:** on synthetic FD curves with a known contour-length increment, the reported
nucleotide count must come with an interval that **contains the truth**, and the rip-detection
confidence must fall when the smoothing window is varied across a reasonable range (i.e. the
sensitivity must be *reported*, not hidden).

.. rubric:: Performance (none of these affect correctness — except the last)

* **Fuse the repeated frame passes.** Global min/max, temporal-correlation estimation,
  preprocessing and QC each currently touch every frame. One streaming pass: read frame ->
  update intensity stats -> update QC -> preprocess -> write.
* **Bound the process-pool submission** to ~2-4x ``n_workers`` rather than one future per
  frame (memory on long movies).
* **Remove the remaining full-stack zarr materialisations** (temporal smoothing, tri-planar
  operations); use overlapping temporal chunks and write incrementally.
* **Choose zarr chunk shape by access pattern** and select compression by benchmark rather
  than globally.
* **Do not parallelise tiny frames indiscriminately** — process startup, serialisation and
  disk contention can exceed the compute saving. Auto-select serial/thread/process/GPU by
  workload.
* **Cache identity must include the code version and every scientifically relevant
  parameter.** ``ts_cache_manager`` is a good base, but a cache keyed on incomplete identity
  is a **correctness** hazard, not a performance one: it will silently serve a result
  computed with different parameters. This belongs in the correctness bucket.

.. rubric:: Silent exception swallowing (measured — 400 dangerous sites)

**Where:** an AST classification of every broad handler in the codebase:

.. list-table::
   :header-rows: 1

   * -
     - logged
     - re-raised
     - **silent (pass)**
     - **silent (other)**
   * - core (non-UI)
     - 76
     - 5
     - **199**
     - **201**
   * - UI
     - 82
     - 0
     - 208
     - 105

**876 broad ``except Exception`` handlers, of which 400 are SILENT in non-UI code.**
``file_io.py`` alone holds **101** of them. This is not a style complaint: it is *why* the
orphaned ``file_path`` block (1.5.386) raised ``NameError`` on **every tagged layer load**
for an unknown length of time without anyone noticing — its own ``except Exception:
return False`` ate the evidence.

**The infrastructure already exists.** ``pycat.utils.general_utils.debug_log(context, exc)``
prints the context and traceback when ``PYCAT_DEBUG=1`` and is otherwise completely silent.
It is simply not applied to the 400 sites.

**Recipe:** replace ``except Exception: pass`` with
``except Exception as e: debug_log('<module>: <what was attempted>', e)``. Behaviour is
unchanged in normal use; the failure becomes diagnosable on demand. Start with
``file_io.py`` (101), ``metadata_extract.py`` (19), ``run_pycat.py`` (21).

**Do NOT sweep this mechanically.** Attempted, and it broke ``file_io.py`` twice: an
``except Exception:`` whose body is on the *same line* is not amenable to naive line
insertion, and the two-pass edit shifted line numbers under itself. This needs a proper
CST-preserving rewrite (``libcst``) or careful hand editing, file by file, with a compile
check after each — not a regex or a line-index loop. It is a **bounded, mechanical, but
genuinely fiddly** job and deserves its own session.

**Longer term** (the audit's rule, and the right one)::

    core scientific / file operation
      -> catch EXPECTED exceptions only
      -> raise a typed error

    UI boundary
      -> catch the typed error
      -> log the traceback
      -> show a concise notification

**Acceptance:** ``PYCAT_DEBUG=1`` on a deliberately corrupted file must print a traceback
for every failure that is silently absorbed today; and no bare ``except Exception: pass``
remains in ``file_io.py``.

.. rubric:: Stale / unused variables (review individually — do NOT bulk-delete)

The audit lists ``proc_source``, ``proc_zarr_ref``, ``from_meta``, ``is_lazy``, ``ndim``,
``thresholds``, ``cell_diameter``, ``strategy_dd``, ``otsu_classes_spin``. Unused variables
are usually harmless, but several of these sit in **stateful loading and time-series** code,
where they may be the residue of logic that was partially removed while downstream behaviour
still assumes it exists. That makes them a *symptom* worth reading, not a lint to clear.

**Where:** run ``ruff --select F841`` (unused local) and review each hit against the
surrounding logic. The question for each is not "is it used?" but "**was something meant to
use it, and does the code silently behave as if it did?**"

.. rubric:: Packaging

``requires-python = ">=3.12,<3.13"`` blocked the auditor's 3.13 environment. Decide whether
that upper pin reflects a real dependency constraint or is merely conservative.



Near-term UX & interaction
--------------------------

Small, mostly self-contained quality-of-life items that improve responsiveness and
keep the scientist engaged with their data. None are architectural; each can ship
independently.

.. rubric:: Default to the first frame on load

Most image viewers open a stack on frame 0; PyCAT currently opens on the middle
frame. Change the default so a freshly loaded stack shows frame 0 (set the viewer's
current step on the T axis to 0 after load), matching viewer convention.

.. rubric:: Materialization progress on stack-consuming methods

Where a method must read/decode a whole stack before working (e.g. bead detection),
the progress indication should read as one continuous 0→100% pass rather than
reaching 100% twice (once for materialization, once for the work). The reusable
``PhasedProgress`` helper already exists; the remaining work is rolling it out to the
other stack-consuming workflows so a materialization wait is always labelled and never
double-counts. Useful app-wide, since materialization is a common shared wait.

.. rubric:: Pixel-size acquisition profiles

Users repeatedly image files from the same microscope with the same missing-metadata
flaw (no pixel size, often no magnification either), so re-entering the scale every
load is friction. The robust design is **named acquisition profiles**: the user
creates and names a profile (storing its pixel size), saved to a small JSON on disk,
and selects it (or it is remembered as default) at load time. This sidesteps the
metadata problem by having the human supply the key the file cannot. When camera +
magnification *are* present in metadata, auto-fill the pixel size as a bonus. Scoped
as an additive picker on the existing load-time pixel-size dialog, composing with the
existing "persist through session" option — not a rewrite. (Metadata-only
auto-recovery was considered and rejected as the primary mechanism, because the exact
scopes this targets omit the metadata needed to compute the scale.)

.. rubric:: Command palette / search bar

A search bar (command-palette pattern, à la VS Code Ctrl+Shift+P or Spotlight) to
find and jump to functionality by name, in three scopes of increasing difficulty:
(1) open a method/widget by name — easy, a fuzzy filter over the existing
analysis-method registry calling the same launch function the menu does; (2) find a
layer by name — trivial, a thin wrapper over the viewer's layer list; (3) find a
step/process within a widget by name — harder, requires steps to be enumerable and
addressable (name → widget + the control that runs it + a scroll-to/highlight). Ship
1 + 2 first as a simple palette; extend to steps once step-addressing infrastructure
firms up (the same "steps as first-class addressable things" requirement also serves
the pipeline-step-diagnostics dropdown and the linked-navigation object model).

.. rubric:: VPT/MSD plot ↔ layer brushing (first instance of linked navigation)

A concrete, near-term instance of the linked multiscale navigation idea (see the
biological object model section): click a trajectory in the MSD plot → highlight that
bead and its track in the canvas and surface its data row, so browsing the plot leads
back to the data in the image. This is a small build for VPT specifically because the
identity plumbing already exists end-to-end — every MSD line carries a track id, and
the tracks layer holds those trajectories keyed by the same id. The missing piece is
narrow: make the (currently static) MSD plot pickable, map the picked line → track id,
then select/centre that track in the viewer and surface its row. Once proven here it
generalizes to the other plots (partition-coefficient scatter → condensate;
enrichment plot → cell).


Calibrated Thermodynamic & Quantitative Condensate Reporting
------------------------------------------------------------

A cross-evaluation of PyCAT against the Punctatools pipeline (a 3D Cellpose-ROI
+ LoG puncta detection + per-cell quantification tool with a thermodynamic
reporting notebook) found that PyCAT is already architecturally broader in nearly
every dimension (QC, batch replay, benchmarking, condensate physics, FRAP/MSD/
fusion, brightfield, spatial statistics, morphological complexity, client
enrichment, time-series, tracking bridges). The conclusion is **not** to adopt its
pipeline, but to add a small set of *calibrated, physically-interpretable*
reporting capabilities that PyCAT currently lacks. The biggest of these converts
PyCAT from an image-analysis tool into a **biophysical-parameter-extraction** tool
— a strong manuscript angle consistent with PyCAT's quantitative-measurement,
physical-interpretation philosophy.

.. rubric:: Calibrated fluorescence-to-concentration thermodynamics (highest value)

PyCAT already computes intensity-based partition and enrichment (partition
coefficient field, per-condensate and per-cell client enrichment, bimodal
intensity fitting, dense/dilute intensity estimates). What it lacks is the
*calibration to physical units* and the *free-energy* step:

* **Calibration-curve manager** — load a standard curve from a purified
  fluorescent protein, assign it to a fluorophore/channel, and convert measured
  intensity to an apparent molar concentration (µM).
* **Real-unit K_p and transfer free energy** — from the calibrated dense and
  light (dilute) phase concentrations, compute the partition coefficient in real
  units and the transfer free energy ΔG_transfer = −RT ln(K_p).
* This is pure downstream analysis and composes with the existing partition /
  enrichment machinery; no acquisition changes are required.

.. rubric:: Condensate thermodynamics report (per-cell export preset)

A consolidated, manuscript-friendly per-cell export table (depends on the
calibration step above), with columns such as: ``cell_id``, ``n_condensates``,
``cell_volume_or_area``, ``total_condensate_volume_or_area``,
``condensate_volume_fraction``, ``mean_dense_intensity``, ``mean_light_intensity``,
``dense_concentration_uM``, ``light_concentration_uM``, ``Kp``,
``dG_transfer_kcal_mol``, ``mean_condensate_distance_to_border``,
``client_enrichment``, and colocalization metrics. This should build on the shared
tidy/long output schema (see the platform-consolidation notes) rather than a
bespoke format.

.. rubric:: Explicit 2D / 3D / time-series condensate modes

PyCAT has 3D building blocks (Z-stack segmentation, 3D spot fitting, stack I/O),
but the main *cellular condensate* workflow is still 2D-centric — and the in-vitro
fluorescence workflow already carries an explicit caveat that its volume fraction
is a 2D-projection proxy, not a true 3D volume fraction. As Punctatools notes, a
2D projection gives inaccurate volume-fraction and partition estimates. The
roadmap item is to make three explicit condensate-analysis modes — **2D**, **3D
z-stack**, and **time-series** — each with appropriate outputs and warnings, so a
true 3D volume fraction and partition coefficient are available when a Z-stack is
supplied.

.. rubric:: Explicit background-mode selector (UI surfacing of existing capability)

The partition/enrichment backend already supports a scalar instrument-offset
background, a user-supplied signal-free background mask, and local-background
measurement. This capability is not yet surfaced as an explicit UI choice. Exposing
a background-mode picker — global field background / per-cell or per-nucleus
background / local shell around each condensate / user-supplied background mask —
would make partition and enrichment measurements more transparent and defensible.
Low effort (mostly UI), since the computation already exists.

.. rubric:: Positive/negative-control validation workflow

Extends the existing segmentation benchmark harness (which already has a
ground-truth-validation mode) toward the control-based parameter validation that
quantitative condensate work needs: run segmentation on a positive control and a
negative / diffuse control, compare false positives, recommend a safe parameter
range, and export a validation report. This aligns with the general benchmarking
direction and the practice of testing parameters on representative and negative
controls before committing to a batch run.

Biological Object Model & Linked Multiscale Navigation
------------------------------------------------------

A cross-evaluation of a cloud-first, petabyte-scale microscopy platform
(NimbusImage, Nat. Methods 2025) against PyCAT concluded: do **not** adopt its
cloud/data-movement architecture (PyCAT's data-local, interactive, quantitative
philosophy is a deliberate strength), but extract three related concepts that
together point at one architectural addition — a navigable biological object model.
These are unusual roadmap items in that the *data* to support them already exists
scattered across PyCAT's modules; what is missing is the unifying structure.

.. rubric:: Formalize the analysis hierarchy (semantic scale pyramid)

Rather than an image pyramid, think an *analysis* pyramid: Image → Cell →
Organelle → Condensate → Punctum → Single molecule, where each level carries its
own measurements, QC, and statistics. This hierarchy already exists implicitly —
the puncta analysis already associates each punctum with its parent cell
(``cell_label``), producing cell-labelled puncta — but it is not formalized as an
explicit, navigable structure. The item is to make the parent/child scale
relationships first-class rather than per-analysis DataFrame columns.

.. rubric:: Linked multiscale navigation (bidirectional brushing)

The highest-value concept, and one that recurs independently: make analysis
outputs and image layers *mutually navigable*. Select a data point in a plot (a
partition-coefficient scatter point, an MSD track, an enrichment value) and jump
to exactly that object in the image layers — the right channel, frame, or Z-slice
— and the reverse: click an object in the viewer and highlight its row across all
plots and tables. Traverse scales too: punctum → parent condensate → parent cell →
whole field, or back down. The prerequisite identity links already exist (each
object row carries a ``label`` tying it to its mask region, and ``cell_label``
tying it to its parent); what is missing is the interactive traversal bridge. The
plots are currently static matplotlib renders with no selection events, so the
work is the linking layer, not the underlying identity. This embodies PyCAT's
anti-black-box philosophy: every number in a plot becomes clickable back to the
pixels it came from. It also pairs naturally with the provenance DAG.

.. rubric:: Context-aware analysis (inherited hierarchy)

Downstream analysis should inherit spatial context: not merely "segment puncta"
but "segment puncta inside nucleus inside cell inside tissue region", so every
measurement carries where it sits in the hierarchy. This makes results
context-aware by construction rather than by post-hoc joining.

.. rubric:: The unifying idea — an internal biological object model

The three concepts above are three views of one addition: give every detected
object a rich, persistent identity — an object graph — rather than treating it as
a mask label plus a DataFrame row. Each object would carry its characteristic
scale, persistence/topology, material state, spatial-neighborhood degree, parent
object, and QC/provenance. Critically, almost none of these quantities come from a
neural network — they come from combining analysis modules PyCAT already has
(characteristic scale from blob/LoG sizing; persistence/topology from the dynamic-
spatial and force-distance work; material state from the condensate-physics MSD/
FRAP/viscosity modules; neighborhood from spatial metrology; parentage from
``cell_label``). No unified object model class exists today — the pieces are
computed independently and never assembled onto a single entity. Building that
entity is the larger, longer-term innovation, and it is a far stronger
contribution than another segmentation algorithm. It also directly serves future
FISH work: an RNA punctum could report its scale, persistence, parent condensate,
nearest chromatin boundary, material environment, and neighborhood-graph degree as
properties of one object.

Concretely, instead of every module independently taking masks and returning its
own table, each detected object would internally carry a standardized record::

   Object
   ├── Geometry
   ├── Intensity
   ├── Scale-space signature
   ├── Topology
   ├── Material-state metrics
   ├── Spatial relationships
   ├── QC flags
   ├── Provenance
   └── Parent/child relationships

The QC module, benchmarking, spatial statistics, DoH scale-space analysis, FRAP,
MSD, and future FISH analyses then become different *views* of the same underlying
biological object rather than isolated analyses that each emit a disconnected CSV.
This is the threshold that would make PyCAT feel less like a collection of tools
and more like a *scientific operating system for microscopy*, where every new
method enriches a shared representation of the biology rather than producing
another standalone output. Given the direction of the DoH scale-space work and the
future FISH platform, this architecture is expected to scale well.

.. rubric:: A related organizing principle

The same source reinforces organizing PyCAT around scientific *questions* rather
than analysis *methods*: "measure expression / condensates / transport / morphology
/ material state / topology", with each workflow then selecting the appropriate
algorithms. This is a more scientist-oriented abstraction than a menu of methods.

Reproducibility, Measurement Reliability & the Measurement Reliability Index
----------------------------------------------------------------------------

A cross-evaluation of a Nature Methods reproducibility paper (2025) against PyCAT
found strong alignment with PyCAT's rigor / anti-black-box direction — the paper is
about making quantitative image analysis reproducible and comparable rather than
introducing a new segmentation algorithm. Do not copy its pipeline; adopt a cluster
of six related capabilities that all answer one question: *how much should a
scientist trust this number, and why?* Several already have partial foundations in
PyCAT. They culminate in a single unifying construct (the Measurement Reliability
Index) that ties QC, segmentation confidence, parameter sensitivity, benchmarking,
and provenance together.

.. rubric:: Feature provenance (elevate existing batch recording to per-feature)

Every numerical feature should be traceable to the raw image, preprocessing,
segmentation, measurement definition, and software version. PyCAT already records
the workflow step chain (the batch recorder captures ordered, parameterized steps
with layer snapshots, shown in the "Recorded Steps" viewer). The gap is attaching
that chain to each *output feature* as provenance — e.g. ``mean intensity`` derived
from ``Image 4 → flatfield → rolling-ball → Cellpose cyto2 → ROI 17 → measurement``.
This reinforces the previously-discussed provenance DAG.

.. rubric:: Feature / parameter stability (per-measurement sensitivity)

Reproducibility is not just re-running an algorithm; it is whether a measurement
stays stable under realistic variation. The benchmark harness already has a
parameter-sensitivity mode that sweeps a segmentation parameter and compares the
resulting masks. The extension is *per-measurement* stability: for each reported
feature, report how much it changes under a small parameter perturbation and flag
trustworthy vs fragile measurements (e.g. "area: threshold ±5% → changes 1.2%,
stable" vs "circularity: ±5% → changes 37%, unstable"). Few microscopy packages
expose this.

.. rubric:: Measurement confidence (combine QC + segmentation + benchmarking)

A per-measurement confidence annotation combining image QC, segmentation
stability, and benchmark agreement into one score with an explanation ("stable
segmentation, high SNR, little boundary ambiguity"). PyCAT already attaches
confidence in isolated places (e.g. coarsening-mechanism discrimination reports a
high/low mechanism confidence); the item is to generalize this to every output.

.. rubric:: PyCAT Validation Suite (standing per-release regression benchmark)

A standardized internal validation suite (Cells / Condensates / Puncta / FISH /
Fibers / Brightfield) run automatically every release, tracking metric drift across
versions ("v1.3: puncta Dice up, runtime down, QC unchanged"). PyCAT already has a
``tests/`` directory with synthetic fixtures and a benchmark harness (Dice /
overlap); this elevates them into a release-gating regression tracker, and dovetails
with the golden-master QC fixtures.

.. rubric:: Measurement ontology (definition / equation / units / reference registry)

A structured registry defining each measurement rigorously: definition, equation,
units, and literature reference (e.g. partition coefficient = I_dense / I_dilute,
dimensionless, Brangwynne 2009). Today these live in scattered docstrings. A
structured ontology makes Methods-section and figure-legend generation nearly
automatic — directly serving the reproducibility story — and fits PyCAT's
rigorously-defined, literature-grounded measurement philosophy.

.. rubric:: Metadata awareness (metadata travels with every measurement)

Acquisition metadata and software versions should ride along with every output
table without user intervention: pixel size, NA, magnification, bit depth,
exposure, camera, PyCAT version, Cellpose version. PyCAT already embeds pixel size
in measurement tables and can export normalized acquisition metadata as JSON; the
item is to make the full set automatic and complete on every exported table.

.. rubric:: The unifying construct — Measurement Reliability Index (MRI)

The six items above converge on a single per-quantity **Measurement Reliability
Index**: every reported value carries a reliability score derived from image QC,
segmentation confidence, parameter sensitivity, benchmark agreement, and biological
plausibility. For example, ``cell area 215 µm² → 0.98``, ``condensate circularity
0.74 → 0.63``. Clicking a score explains why it is high or low ("boundary
ambiguous", "low SNR", "high sensitivity to threshold", "methods disagree"). No
mainstream microscopy software treats measurements this way. It composes with the
QC module, the benchmarking framework, and the provenance system, and it embodies
PyCAT's distinguishing philosophy: not just generating measurements, but
communicating how much confidence scientists should place in them. Framing note:
PyCAT is evolving into Image → Analysis Engine → Measurements → Scientific
Interpretation → Publication; most software stops at measurements, and the
interpretation layer (QC, benchmarking, provenance, physics, statistics) is the
distinguishing direction to keep building rather than adding more segmentation
models.

Feature Families, Biological QC & the Measurement Platform Identity
-------------------------------------------------------------------

A cross-evaluation of a Cell Painting / image-based profiling review (Nature
Methods 2024) against PyCAT. Cell Painting's core is phenotypic profiling —
measure thousands of features and treat them as a fingerprint of cell state, then
hand off to machine learning and a latent space. PyCAT should **not** adopt that
measure-everything → ML → latent-space → interpret-later direction; it runs against
PyCAT's hypothesis → measurement → mechanism → physics → biology philosophy. But
several concepts fit well, and some restate ideas already captured in the
biological-object-model section (state vectors, feature families, and the
Experiment → Field → Cell → Nucleus → Condensate → Punctum hierarchy are the
profiling view of that same object model — each object's standardized record, read
as a vector, is a state fingerprint, and the record's top-level branches are the
feature families). The genuinely new items:

.. rubric:: Feature families instead of flat feature lists

Features are currently emitted as flat DataFrame columns. Organize them into named
biological families — Geometry, Intensity, Texture, Dynamics, Material state,
Spatial organization, Topology, Scale-space, QC — so a wide export becomes
interpretable groups rather than an undifferentiated list of columns. This is the
near-term, buildable schema piece that makes the state-vector abstraction usable,
and it maps directly onto the object record's branches.

.. rubric:: Biological QC (a second QC layer beyond imaging QC)

The QC module today measures *imaging* quality (focus, SNR, Nyquist, bleaching). A
complementary *biological* QC layer would flag biological outliers rather than
imaging problems: cell touching the field edge, oversegmented nucleus, abnormal
aspect ratio, extreme intensity, low transfection, condensate outside the
cytoplasm, likely-dead cell, mitotic cell. This is a natural extension of the
planned QC/Image-Quality-Advisor module, adding an object-level biological-outlier
pass to the existing field-level imaging assessment.

.. rubric:: Feature redundancy / correlation reporting

PyCAT computes many descriptors, and many are highly correlated (area, convex
area, equivalent diameter, major axis all track size). A module could report
correlated feature groups and recommend a minimal non-redundant set ("keep area,
drop three redundant features"). Valuable for downstream statistics and machine
learning; more of a statistics-support utility than core to the mechanistic story.

.. rubric:: Analysis presets (unify the scattered preset idea)

Some per-experiment presets already exist (e.g. the time-series condensate UI has a
preset row). Generalize this into a first-class, workflow-level preset system —
Condensate / FISH / FRAP / Tracking / Morphology presets — each bundling
recommended preprocessing, QC thresholds, benchmark candidates, and default
outputs, tied to the existing pipeline-definition structure.

.. rubric:: Structural profiling (the DoH/FISH complement to phenotypic profiling)

The reframe worth keeping: Cell Painting does *phenotypic* profiling ("what
phenotype does this cell resemble?"). The DoH scale-space and future FISH work
could do *structural* profiling ("how is the molecular architecture organized
across spatial scales?") — an RNA-organization → scale-space signature → topology →
spatial statistics → material-state → *structural state vector*. This is a
different, complementary axis of biology from phenotypic profiling, and a
distinctive positioning for the DoH/FISH platform.

.. rubric:: Feature Explorer (borrow almost directly)

Rather than presenting a flat spreadsheet, expose an interactive browser where each
measurement carries a clear biological interpretation ("what does this measure?"),
its mathematical definition, units, expected range, sensitivity to preprocessing/
segmentation, correlated measurements, and example images showing low / medium /
high values. This is the unifying *interface* over several other roadmap items —
the measurement ontology (definitions/units/references), feature stability
(sensitivity), feature redundancy (correlations), and the QC gallery (example
images) — and it fits the educational direction already taken with the QC module,
reinforcing PyCAT's identity as a platform that helps scientists understand and
trust measurements, not merely generate them.

.. rubric:: Framing — from image-analysis package to measurement platform

The overarching realization: with provenance, benchmarking, scale-space, topology,
and material-state analysis in place, segmentation becomes almost incidental and
PyCAT shifts from answering "where are the objects?" (image analysis) to "what do
these objects tell us about biology?" (a measurement platform). That is the
identity to keep building toward.

Core Functionalities
--------------------

File I/O
^^^^^^^^

**Napari Integrated File Opening**

* Explore integrating PyCAT's file I/O with Napari's native file I/O for seamless operations.

**Expanded File Support**

* 3D Image/Z-Stack
* Time Series
* Video

**Migration from AICSImageIO to BioIO**

* Replace ``imsave`` with ``BioIO``'s ``BioIO.save``
* `BioIO GitHub Repository <https://github.com/bioio-devs/bioio>`_
* Utilize ``BioIO`` for expanded metadata handling.

Steps for Migrating to BioIO
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Install Required BioIO Packages**

* Identify the file formats you need and install the corresponding BioIO plug-ins

   .. code-block:: bash

      pip install bioio bioio-czi bioio-imageio bioio-lif bioio-tifffile bioio-ome-zarr bioio-ome-tiff bioio-nd2

2. **Update Code to Use BioIO**

* Replace all imports of AICSImageIO with BioIO. For example

   .. code-block:: python

      from bioio import BioImage

* Update any AICSImage object creation with BioImage. Example

   .. code-block:: python

      image = BioImage("file.czi")

* Check the migration guide for detailed API changes.

3. **Test for Compatibility**

* Test application with all supported file formats to ensure BioIO behaves as expected.
* Validate that all the necessary dependencies are correctly installed for your use case.

4. **Update Environment Files**

* Update ``requirements.txt`` or ``environment.yaml`` files to reflect the new dependencies.

   .. code-block:: yaml

      dependencies:
        - bioio
        - bioio-czi
        - bioio-imageio
        - bioio-lif
        - bioio-tifffile
        - bioio-ome-zarr
        - bioio-ome-tiff
        - bioio-nd2

5. **Document Changes**

* Update the package's documentation to note the switch from AICSImageIO to BioIO, including installation instructions for the required plug-ins.
* `BioIO GitHub Repository <https://github.com/bioio-devs/bioio>`_
* `BioIO Migration Guide <https://bioio-devs.github.io/bioio/MIGRATION.html>`_
* `BioIO Overview <https://bioio-devs.github.io/bioio/OVERVIEW.html>`_

Image Segmentation
^^^^^^^^^^^^^^^^^^

**Configurable Segmentation Parameters**

Add inputs for

* Minimum Object Size
* Maximum Object Size  
* Point Spread Function (PSF) Size
* WBNS Noise Level
* Use these inputs throughout analyses to eliminate magic numbers.

**Segmentation Enhancements**

* **Watershed Splitting**

  * Separate function to split touching objects using OpenCV's watershed on binary masks.

* **Replace Watershed Labeling**

  * Use ``skimage.segmentation.random_walker`` as an alternative to watershed labeling, see more at `Random Walker Segmentation Documentation <https://scikit-image.org/docs/stable/auto_examples/segmentation/plot_random_walker_segmentation.html>`_

**Improved Puncta Detection**

* Address issue where PyCAT segments are too small.

  * Reduce over-opening.
  * Apply dilation (e.g., ``dilation=1``) before returning puncta mask.

* Separate Condensate/Object Filter

  * Make the condensate/object filter a separate, configurable function and base its local region on the size of the objects (e.g., small objects look at 1 or 2 pixel perimeter, large condensates maybe 3-5 px).

**Expand Labels**  *(DONE — Toolbox → Labeled Mask Tools → Expand Labels)*

* Utilize ``skimage.segmentation.expand_labels`` for efficient label growth.
* Example usage - ``skimage.segmentation.expand_labels(label_image, distance=1)``

**Cell Segmentation Options**

* Model Selection for CellPose

  * Allow users to select different CellPose models via a dropdown menu.

* Universal Cell Segmentation

  * Possibly incorporate other advanced segmentation methods

    * `cellSAM Preprint <https://www.biorxiv.org/content/10.1101/2023.11.17.567630v2>`_ and Segment Anything Models (SAM) from Meta
    * `Nature Article 1 <https://www.nature.com/articles/s41592-024-02254-1>`_
    * `Nature Article 2 <https://www.nature.com/articles/s41592-024-02233-6>`_



Thresholding Methods
^^^^^^^^^^^^^^^^^^^^

**Local Thresholding Enhancements**

* Add various local thresholding methods.
  
  * `Local Otsu <https://sharky93.github.io/docs/dev/auto_examples/plot_local_otsu.html>`_
  * `Adaptive Gaussian Thresholding <https://medium.com/geekculture/image-thresholding-from-scratch-a66ae0fb6f09>`_
  * Implement AND/OR operations for combining threshold methods.


**Skimage Thresholding Tools**

* Incorporate ``skimage.filters.try_all_threshold``, then the user could select which method to use, much like Fiji.

  * **Available Methods:**

    * Isodata
    * Li
    * Mean
    * Minimum 
    * Otsu
    * Triangle
    * Yen

Background Removal
^^^^^^^^^^^^^^^^^^

**Gaussian Background Removal**

* Separate the functions for

  * Gaussian Background Removal
  * Rolling Ball (RB) Background Removal
  * Support mask use in BG removal so that in-painting can be used to avoid the 'rim' that is left by traditional Rolling Ball BG removal algorithms.

**Top Hat Filters**

* Implement a function to apply black/white top-hat filters with selectable parameters

  * Select layer
  * Choose between black vs. white top-hat filter
  * Define size (e.g., ball radius)
  * Add the filtered output to the viewer.


Performance Improvements
------------------------

**Speed & Efficiency Enhancements**

* **Bounding Box Cropping**

  * Implement the bounding box cropping function for all masked or per-cell analyses instead of processing the entire image.

* **Parallel Processing & GPU Acceleration**

  * Explore parallel processing techniques.
  * Utilize GPU acceleration where applicable.


**Visual Indicators**

* **Progress Bars**

  * Add progress bars or visual indicators for functions that are slower.
  * Utilize Napari's built-in tools for progress visualization.

**Multitasking**

* Allow users to perform other tasks while a slow function is running.
* Implement threading or asynchronous programming to offload heavy processing.
* Example Implementation

.. code-block:: python

   import threading
   
   def start_processing_thread(unique_labels):
       # Create a thread that runs the process_cells function
       processing_thread = threading.Thread(target=process_cells, args=(unique_labels,))
       processing_thread.start()


Advanced Analysis Tools
-----------------------

Colocalization & Correlation Analysis
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Costes Significance Testing**

* Modify the Costes method to scramble pixels in blocks roughly the size of the PSF since within that region they are not truly independent of each other.

**Plotting CFs**

* Improve handling of labeled masks by

  * Plotting only the first labeled object to avoid multiple pop-ups.
  * Potentially refining the plotting logic for better usability.
  * Could store plots for each label in a labeled/masked analysis.


**CCF Fitting**

* Implement fitting for 1D CCF and return fit results, including offsets for 2D analyses.

**Colocalization Filters Using Skimage Metrics**

* Incorporate metrics such as

  * ``skimage.metrics.mean_squared_error(image0, image1)``
  * ``skimage.metrics.structural_similarity(im1, im2, ...)``
  * ``skimage.metrics.normalized_mutual_information(image0, image1, ...)``
  * ``skimage.metrics.normalized_root_mse(image_true, image_test, ...)``


Plotting Tools
^^^^^^^^^^^^^^

**Improved Plotting Widget**

* Refactor the existing plotting widget for better usability.
* The plotting widget was incredibly difficult to make; it may require refactoring using an observer pattern or similar. Although it does mostly work now, it needs updating to function more expansively and intuitively.



Batch & Video Processing
------------------------

Framework Overview
^^^^^^^^^^^^^^^^^^

The framework I envision would not be all that dissimilar to CellProfiler. The goal should be, like the rest of the program, to keep it modular, expandable, customizable, and user-friendly. The user will have to determine their optimal segmentation algorithm on an example image or frame, measure the object sizes, PSF input, etc., then go into the video/batch UI.

1. **User Configurable Workflow**

* Allow users to determine the optimal segmentation algorithm on an example image or frame.
* Provide a series of dropdown menus organized for

  * Pre-processing steps
  * Upscaling/Deconvolution
  * Cell Segmentation
  * Analysis steps

2. **Modular/Expandable Design**

* Ensure each processing step is optional.
* Facilitate adding multiple pre-processing, upscaling, and analysis steps as needed.

3. **Execution**

* Implement a "Run on All" button to apply the configured workflow to

  * All images in a folder
  * All frames in a video/time series

Video Integration
^^^^^^^^^^^^^^^^^

**TrackPy for Particle Tracking**

* Integrate `TrackPy <http://soft-matter.github.io/trackpy/v0.6.1/>`_ for advanced particle tracking in videos.
* Link video segmentation to TrackPy by

  * Segmenting every frame like a batch process.
  * Formatting results into a DataFrame.
  * Passing the formatted DataFrame to TrackPy for particle 'linking' and tracking.

* Napari's built-in file I/O handles videos quite well and displays them in an intuitive and ideal way in the viewer, further reinforcing that PyCAT FileIO should be integrated directly into Napari (e.g., by forking the repo).

Machine Learning Integration
----------------------------

**ML-Based Classification/Segmentation**

* Develop machine learning classifiers for

  * Segmentation and detection tasks (e.g., identifying the presence of condensates).
  * Potentially incorporate ML for enhancing segmentation accuracy and efficiency.
  * Use the annotated output from PyCAT as sets of training and validation data

    * Incorporate human-in-the-loop analyzed data, user-free analyzed data, and synthetic data for more robust training and reinforcement.

Data Management & Output
------------------------

**Metadata Handling**

* Store metadata as a DataFrame.
* Provide options to save metadata alongside image data.
* Enable exporting images with updated metadata attached.

**Data Frame Organization**

* Organize DataFrame features/columns better.
* Consider rounding data or maintaining float precision based on analysis needs.

Miscellaneous Enhancements
--------------------------

**Error Handling**

* Implement improved and more informative error messages to assist users in troubleshooting.

**Additional Tools**

* PunctaTools

  * Rather than integrating the PunctaTools pipeline directly, adopt the small set of calibrated-reporting concepts it does well — see the *Calibrated Thermodynamic & Quantitative Condensate Reporting* section above. PyCAT is already architecturally broader; the value is in the calibrated thermodynamics report, not the pipeline.

* Line Plots Functionality

  * Implement functionality for generating line plots from data in the plotting widget.

* Cytoplasm Analysis

  * Simplify and improve cytoplasm analysis methods.

* Partition Coefficients

  * Support bi-phasic and multi-phasic partition coefficients for more detailed analyses.

**Texture Analyses**

* Use Gaussian blur of minimum object size (e.g., 2 or 3 px) then analyze to reduce the effect of noise.

**LayerDataframeSelectionDialog**

* Default layer and DataFrame names could be passed to ``LayerDataframeSelectionDialog`` (for Save and Clear) based on the analysis method chosen.

**Mask Layer Operations Merging Functions (and, or, xor)**  *(DONE — Toolbox → Layer Operations → Mask Operations (AND/OR/XOR))*

* Make Mask merging functions similar to image merging operations for combining masks using AND, OR, XOR methods.

Future Features & Research Integration
--------------------------------------

Advanced Methods
^^^^^^^^^^^^^^^^

**SpIDA (Spatial Intensity Distribution Analysis)**  *(DONE — Toolbox → Advanced Analysis → Molecular Counting → SpIDA)*

* Implemented as a direct port of the authors' reference MATLAB model, with a
  monomer-calibration step, oligomeric-state readout, and acquisition-assumption
  guardrails. Validated against reference-simulated images (R^2 ~0.99, <10% error).
* `PNAS Article <https://www.pnas.org/doi/10.1073/pnas.1018658108>`_

**Support for Advanced Analysis Types**

* Add support for

  * Time Series Analysis
  * Fluorescence Correlation Spectroscopy (FCS)
  * Fluorescence Cross-Correlation Spectroscopy (FCCS)
  * 3D Support and Z-Stacks
  * Video Analyses, Video Particle Tracking (VPT), Particle Motion Tracking (pMOT)
  * Integrate other Banerjee Lab code/analyses

Denoising & Morphological Operations
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Invariant Denoising**

* Implement invariant denoising techniques.
* `Invariant Denoising Example <https://scikit-image.org/docs/stable/auto_examples/filters/plot_j_invariant.html#sphx-glr-auto-examples-filters-plot-j-invariant-py>`_

**Morphological Reconstruction**

* Utilize morphological reconstruction methods.
* `Morphological Reconstruction Guide <https://www.mathworks.com/help/images/understanding-morphological-reconstruction.html>`_

**Anisotropic Diffusion Filters**

* Implement Anisotropic Diffusion (Perona-Malik Filter).

**Miscellaneous Skimage Functions**

* Potentially integrate the following Skimage functions for enhanced processing

  * ``skimage.util.view_as_blocks(arr_in, block_shape)`` - could be useful for Costes blocks.
  * ``skimage.segmentation.find_boundaries(...)``
  * ``skimage.segmentation.random_walker(...)``
  * ``skimage.filters.apply_hysteresis_threshold(...)``


Documentation & User Support
----------------------------

**Comprehensive Documentation**

* Continuously improve documentation to cover

  * How to use PyCAT features.
  * Explanations of underlying theories and methods.

**User Guides & Tutorials**

* Develop detailed user guides and tutorials to assist users in leveraging PyCAT's full capabilities.

**Background Information**

* Provide background information on key topics such as

  * Image processing techniques.
  * Colocalization analysis.
  * Particle tracking.

Known Issues
------------

**run_simple_multi_merge**  *(FIXED in v1.5.171)*

* Mean and Additive previously produced the same result — the per-result min-max
  normalization cancelled the ÷N factor between them. Now the merged result is clipped
  to the input dtype range and scaled by that fixed maximum, so the modes stay distinct.

**IMS / large-file lazy loading from USB HDDs — frame-scrub latency**

* Scrubbing through Z or T sliders on lazily-loaded IMS (or large TIFF/HDF5) files is
  noticeably laggy when the file lives on a USB-attached spinning hard drive at USB 2.0
  speeds (~25–40 MB/s sustained). Each slider step triggers a read of one
  uncompressed 2048×2048 uint16 plane (~8 MB), so the per-frame latency tracks USB
  bandwidth directly:

  * **USB 2.0 (~30 MB/s):** ~250–300 ms per frame — perceptible lag, effectively
    unusable for rapid scrubbing.
  * **USB 3.0 (~300 MB/s):** ~25–30 ms per frame — near-interactive.
  * **USB 3.1/3.2 or NVMe (~500 MB/s+):** <10 ms — indistinguishable from local SSD.

  This is a physical I/O constraint, not a PyCAT bug — the data simply cannot arrive
  faster than the bus allows. Workarounds and guidance to surface to users:

  1. **Check the port first.** USB 3.0 ports are often labeled with a blue insert or
     "SS" (SuperSpeed). Plugging a USB 3.0 drive into a USB 2.0 port silently caps
     throughput; a single port swap can give a 10× improvement.
  2. **Copy the file locally before opening.** Even a short analysis session is faster
     if the file is on an internal SSD first. PyCAT's lazy loading is optimised for
     local NVMe/SSD storage.
  3. **Pre-load the relevant range.** If only a few Z slices or timepoints are needed,
     load and materialise just those via the Z-stack tools rather than lazy-loading
     the full volume.
  4. **Future: LRU frame cache.** A thin in-memory cache keyed on ``(t, c, z)`` in the
     ``_ImsReaderTYX`` / ``_ImsReaderTZYX`` classes (see existing Known Issue above)
     would make repeated scrubbing of already-visited frames instantaneous regardless
     of storage speed. This is the primary software-side mitigation; it is already on
     the roadmap.

  **Recommended user guidance:** for live analysis, keep data on an internal SSD or
  networked fast storage; external USB HDDs are fine for archiving and transfer but not
  for interactive Z/T scrubbing of large volumes.

* As of v1.5.182, IMS files are loaded via the imaris_ims_file_reader ``ims`` object
  directly (bypassing the zarr-store adapter that caused ``KeyError: '0.0.0.0.0'`` on
  files from Box Drive / network shares). The direct-reader path has no internal chunk
  cache — the zarr adapter previously cached decoded chunks across reads of the same
  frame, which could benefit tight loops that re-read frames rapidly (e.g. batch
  processing that scrubs all timepoints in a loop). For interactive use this is
  imperceptible (napari caches rendered frames). For batch workflows processing many
  frames repeatedly from large IMS files, this may add I/O overhead.
  **Roadmap:** add a thin LRU frame-cache to ``_ImsReaderTYX`` / ``_ImsReaderTZYX``
  (keyed on ``(t, c, z)``) so repeated reads of the same frame hit memory rather than
  disk, matching the behaviour of the old zarr adapter without restoring the broken path.



Local Thresholding - Work In Progress (WIP)
-------------------------------------------

Adaptive Gaussian Threshold Function
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import numpy as np
   from scipy import ndimage as ndi
   from skimage.filters import gaussian

   def adaptive_thresholdGaussian(img, block_size, c):
       # Check that the block size is odd and nonnegative
       assert block_size % 2 == 1 and block_size > 0, "block_size must be an odd positive integer"
       
       # Calculate the local threshold for each pixel using a Gaussian filter
       threshold_matrix = gaussian(img, sigma=block_size//2)
       threshold_matrix = threshold_matrix - c
       
       # Apply the threshold to the input image
       binary = np.zeros_like(img, dtype=np.uint8)
       binary[img >= threshold_matrix] = 255
       
       return binary

Adaptive Gaussian Threshold Function (Detailed)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   import numpy as np
   from scipy import ndimage as ndi
   from skimage.filters import gaussian

   def adaptive_gaussian_threshold(image, blockSize, C):
       """
       Performs adaptive Gaussian thresholding on a grayscale image.
       
       Parameters:
       - image: numpy array, the input grayscale image.
       - blockSize: int, size of the local region to calculate the Gaussian weighted mean (must be an odd number).
       - C: int, a constant subtracted from the Gaussian weighted mean to calculate the threshold.
       
       Returns:
       - numpy array, the thresholded binary image.
       """
       # Ensure the blockSize is odd
       if blockSize % 2 == 0:
           raise ValueError("blockSize must be an odd number.")
           
       # Generate a Gaussian kernel
       kernel_size = blockSize
       sigma = 0.3 * ((kernel_size - 1) * 0.5 - 1) + 0.8
       gauss_kernel = gaussian(image, sigma=sigma, truncate=(kernel_size//2)/sigma)
       
       # Image dimensions
       rows, cols = image.shape
       
       # Pad the image to handle borders
       padded_image = np.pad(image, blockSize // 2, mode='edge')
       
       # Output image
       thresholded_image = np.zeros_like(image)
       
       for i in range(rows):
           for j in range(cols):
               # Calculate the local weighted mean
               local_sum = np.sum(padded_image[i:i+blockSize, j:j+blockSize] * gauss_kernel[i:i+blockSize, j:j+blockSize])
               local_mean = local_sum / np.sum(gauss_kernel[i:i+blockSize, j:j+blockSize])
               
               # Apply the threshold
               if image[i, j] > local_mean - C:
                   thresholded_image[i, j] = 255
               else:
                   thresholded_image[i, j] = 0
                   
       return thresholded_image



.. note::
   This roadmap is a living document and will be updated as development progresses and new requirements emerge. 
   If you'd like to contribute, please visit our :doc:`contributing` page to help work on implementing any of these or other useful features. 