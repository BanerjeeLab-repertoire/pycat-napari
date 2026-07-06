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

  * Implement features from the `PunctaTools <https://github.com/stjude/punctatools>`_ analysis pipeline, or collaborate with them to integrate it into PyCAT fully.

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