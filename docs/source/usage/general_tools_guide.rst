General Tools: When and Why
===========================

PyCAT's toolbox contains several general-purpose tools that are easy to overlook,
either because their value is not obvious from the name or because the technique
itself is unfamiliar. This page explains **when you would reach for each one, and
what problem it solves.**

Most of these were originally buried inside a single analysis method, even though
they apply to almost any data. They are now standalone Toolbox tools.

.. contents::
   :local:
   :depth: 1


Motion Scale Estimator
----------------------

**Toolbox ▸ Data Visualization ▸ Motion Scale Estimator**

*Measure how far your objects move between frames — without tracking anything.*

**The problem it solves.** Every particle-linking algorithm asks you for a maximum
displacement (a "search radius", "max linking distance", or similar). Almost nobody
knows this number. It is usually guessed, and the guess is usually wrong:

* Guess **too large** and the linker connects a bead to its *neighbour* instead of to
  itself, producing mislinked trajectories that inflate the MSD and corrupt any
  derived diffusion coefficient or viscosity.
* Guess **too small** and every track shatters into fragments.

Worse, a wrong guess does not announce itself. You get trajectories, they look
plausible, and the resulting physics is wrong.

**How it works.** Take a short **maximum-intensity projection** through time. Each
object smears into a blob whose width is its single-frame width *broadened by how far
it moved*. Subtract the single-frame width in quadrature and the motion falls out:

.. code-block:: text

   motion = sqrt( sigma_projected^2  -  sigma_single_frame^2 )

No linking. No trajectories. One projection.

**What you get:**

* The **per-frame motion scale**, in µm.
* A **suggested maximum linking distance**, derived from your data rather than
  guessed.
* The projection itself is added as a layer, so you can *see* the smear the estimate
  came from.
* A **trackability verdict** — which is arguably the most valuable output:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Motion vs object size
     - Verdict
   * - motion ≪ object size
     - Frame-to-frame linking should be reliable.
   * - motion ≈ object size
     - Workable, but watch for mislinks in dense fields.
   * - motion ≳ object size
     - **Linking is unreliable at this frame rate.** The information needed to
       decide which object is which is not in your data. Acquire faster, or use a
       gap-closing linker — and treat the trajectories with suspicion.

**When to run it:** *before* a tracking experiment, not after. It can tell you that a
dataset is untrackable in seconds, which is far cheaper than discovering it from a
nonsensical viscosity three hours later.

.. note::

   It is an **estimate**, not an exact measurement. Fitting a Gaussian to a projected
   envelope slightly under-estimates the true spread (~25 % low on a synthetic random
   walk with a known step size), which is what the margin factor *k* absorbs. Treat
   the output as a well-grounded starting value, not a precise displacement.


Partial-Volume Measurement
--------------------------

**Toolbox ▸ Cell and Object Analyses ▸ Partial-Volume Measurement**

*Measure objects segmented on an upscaled image, using the original pixels.*

**When you need it:** any time you upscaled an image in order to segment it (common
with Cellpose, which expects objects around 30 px across) and now want intensities.

**Why the obvious approach is wrong.** Measuring on the upscaled image reads
*interpolated* pixels, which pseudoreplicates your statistics (16× the "samples", zero
new photons — reported error bars come out too small) and biases small objects.

**What this tool does.** It converts the high-resolution mask into **fractional
coverage weights** on the native pixel grid and computes weighted statistics on the
**original** image. A native pixel that is 37 % inside the object contributes 37 %.
Every photon is counted exactly once.

**What you get, per object:** weighted mean and integrated intensity, fractional area,
an SEM built from an effective sample size (so it is not inflated by partially-covered
edge pixels), plus:

* ``predicted_bias_pct`` — the **size-dependent optical bias** for that object.
* ``sub_resolution`` — a flag for objects at or below the resolution limit, whose
  absolute intensity is not trustworthy by *any* method.

Plus a field-level **size-confound warning**.

.. important::

   Read :doc:`measurement_guidance` before interpreting intensities from this or any
   other tool. The size-dependent bias it reports is **optical, not computational** —
   it cannot be removed by better software, and it is the reason a treatment that
   changes only object *size* can fabricate an apparent change in *intensity*.


Frame Quality / Focus QC
------------------------

**Toolbox ▸ Data Visualization ▸ Frame Quality / Focus QC**

*Which frames of this stack are actually usable?*

Scores every frame of a time series or z-stack for:

* **Sharpness** (normalised Brenner gradient) — is this frame in focus?
* **Information content** (Shannon entropy of the intensity histogram).

It flags out-of-focus frames against a threshold, identifies the **sharpest frame**,
and plots both curves so you can see drift as it happened.

**When to run it:** before analysing any stack. Focal drift during a long acquisition
is common and is easy to miss by eye, and a handful of blurred frames can quietly
distort a time-series result.


Photobleach Correction
----------------------

**Toolbox ▸ Image Processing ▸ Photobleach Correction**

Fits an exponential decay to the stack's mean intensity over time and divides it out,
so later frames are not artificially dim. Reports the bleach time constant τ and plots
the measured trace, the fitted decay, and the corrected result together — so you can
judge whether the bleaching was real.

.. note::

   If the fit does not converge, the tool **declines to correct** rather than applying
   a meaningless factor. A trace that is not an exponential decay should not be
   "corrected" as though it were.


Detrend Stack
-------------

**Toolbox ▸ Image Processing ▸ Detrend Stack**

Removes the slow temporal trend (bleaching, drift) from a stack.

**Why it matters:** this is a **prerequisite for any fluctuation measurement** —
Number & Brightness, temporal correlation, camera-based FCS. Those techniques measure
*variance over time*. An undetrended slow decay contributes to that variance and is
mistaken for signal, corrupting the result.

Two methods: ``boxcar`` (moving average of the global trace) and ``linear`` (a straight
line fit).

**Detrending is not the same as bleach correction.** Bleach correction rescales the
intensities so they are comparable across frames. Detrending removes the trend so it
does not pollute a variance measurement. Use bleach correction when you care about
*intensity*; use detrending when you care about *fluctuations*.


Image Registration (subpixel)
-----------------------------

**Toolbox ▸ Image Processing ▸ Image Registration**

Aligns one image to another with subpixel accuracy (phase cross-correlation). Adds the
registered image plus a difference image, and reports the shift it found.

**Use it for:** channel alignment (chromatic offsets between channels are real and
will corrupt a colocalization measurement), drift correction between timepoints, and
before/after comparisons where the field moved.

.. tip::

   If you are about to measure colocalization between two channels, check their
   alignment first. A one-pixel chromatic shift can substantially change a Pearson or
   Manders coefficient, and it is invisible unless you look for it.


Colocalization Over Time
------------------------

**Analysis Methods ▸ Colocalization Analysis ▸ Colocalization Over Time**

*How does colocalization evolve during an event?*

Runs colocalization frame-by-frame across a time series and plots the coefficient
against time — so you can see colocalization **change** during fusion, maturation, or
recruitment, rather than reporting a single number for a dynamic process.

Two forms:

* **Pixel-wise** — Pearson, Spearman, Manders overlap, etc. per frame for the whole
  field.
* **Object-based (per-cell)** — condensate colocalization computed per cell, per
  frame, so each cell's trajectory can be followed individually. Cell identity comes
  from the labelled mask: the same label means the same cell, so a labelled mask stack
  tracks moving cells without a separate tracking step.

Clicking a point on the trace jumps the viewer to that frame.

.. note::

   Per-cell trajectories are only meaningful if your segmentation assigns the **same
   label to the same cell across frames**. A static 2D mask (reused for every frame)
   satisfies this trivially. If your per-frame segmentation relabels independently,
   the trajectories will be scrambled.


Stack / Time-Series Tools
-------------------------

**Toolbox ▸ Image Processing ▸ Stack / Time-Series Tools**

Stack-wide (T, H, W) versions of tools whose 2D counterparts already existed:

* **Upscale Stack**
* **Pre-Process Stack (lazy)** — streams frames rather than loading the whole movie
* **Cellpose Segmentation (stack)** — keyframe segmentation with propagation

These operate on whole stacks rather than a single plane and were previously reachable
only from inside the Time-Series Condensate pipeline.
