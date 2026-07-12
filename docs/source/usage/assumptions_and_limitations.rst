Assumptions and Limitations
===========================

Every measurement in PyCAT rests on assumptions. Most are reasonable; some are
invisible; a few can quietly invalidate a result if the data does not match them.
This page lists the ones we know about, what they mean, and when they break.

This is deliberately a page about **what PyCAT cannot tell you**. It exists because
a tool that only advertises what it can do is a tool that helps you publish a
mistake.

.. seealso::

   :doc:`measurement_guidance` covers the two effects with the widest blast radius —
   the **size-dependent intensity bias** and the consequences of **upscaling**. If
   you read only one page, read that one.

.. contents::
   :local:
   :depth: 1


Volume fraction from a 2D image is a projection proxy, not a volume
--------------------------------------------------------------------

**Where it applies:** In-Vitro Fluorescence and Brightfield workflows; any
"volume fraction Φ" reported from a single 2D plane.

A 2D image of a flow cell shows a *projection*. The "volume fraction" computed from
it is the **area fraction of a focal plane**, which is not the same quantity as the
bulk volume fraction of the sample.

The two disagree in a way that depends on your focal depth:

* Droplets **settle**. A plane near the coverslip over-represents them; a plane in
  the bulk under-represents them.
* Large droplets are more likely to intersect any given plane than small ones, so
  the size distribution seen in-plane is **biased toward large objects**
  (a stereological effect, not a segmentation error).

**What to do:** treat 2D Φ as a *relative* measure — comparable between images
acquired identically, not a physical volume fraction. For a true volume fraction,
use the **Z-Stack (3D) Object Analysis** workflow, which segments in 3D and reports
genuine volumetric quantities.

PyCAT displays this caveat in the in-vitro workflow at the point of use.


Automatic object-size estimation is only valid for 2D fluorescence
-------------------------------------------------------------------

**Where it applies:** the automatic object-size / ``ball_radius`` estimator used in
batch and headless runs.

The estimator works by isolating bright compact objects (white top-hat), thresholding
them (Otsu), and taking the median equivalent diameter. That procedure assumes
**discrete high-intensity objects on a thresholdable background.**

It is therefore enabled **only** for:

* Cellular fluorescence condensate analysis
* In-vitro fluorescence analysis

and deliberately **disabled** for:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Workflow
     - Why automatic sizing is invalid
   * - **Brightfield**
     - Contrast is edge/phase-based. There is no intensity hierarchy for a threshold
       to find — bright and dark halos surround every object.
   * - **Time series**
     - Object size *drifts* as condensates grow, coarsen, or fuse. A single median
       diameter for the whole movie is wrong by construction.
   * - **Z-stacks**
     - A projected diameter is not the 3D object size. Objects that are large in *z*
       but small in-plane are systematically mis-sized.

In these workflows you must supply the object size yourself. This is not a missing
feature; it is a refusal to produce a number that would not mean anything.


Thresholding assumptions differ between fluorescence and brightfield
---------------------------------------------------------------------

Several segmentation fallbacks (multi-Otsu class separation, intensity-hierarchy
methods) rest on the premise that **object and background separate in intensity**.

* In **fluorescence**, this generally holds: even a weak fluorophore leaves a
  meaningful intensity ordering between dense phase, dilute phase, and background.
* In **brightfield**, it does not. An object may be *darker* than background in its
  centre and *brighter* at its halo. Applying an intensity-hierarchy method to
  brightfield data can produce a mask that looks plausible and is wrong.

PyCAT provides brightfield-specific preprocessing (halo suppression, local contrast,
optical-density conversion) for this reason. Prefer the brightfield workflows for
brightfield data rather than forcing a fluorescence method onto it.


Tracking assumes objects move less than they are far apart
-----------------------------------------------------------

Frame-to-frame linking (greedy nearest-neighbour, Bayesian/LAP) assumes that between
consecutive frames an object moves **less than the distance to its neighbours**. When
that fails, the linker cannot know which object is which, and no amount of parameter
tuning fixes it — the information is not in the data.

This is a property of your **acquisition**, not of the software: it is set by frame
rate versus object speed.

**PyCAT can tell you whether it holds, before you track anything.** The
**Motion Scale Estimator** (Toolbox ▸ Data Visualization) measures per-frame
displacement from a short max-projection — no tracking pass required — and reports a
**trackability verdict**:

* motion ≪ object size → linking is reliable
* motion ≈ object size → workable, watch for mislinks in dense fields
* motion ≳ object size → **linking is unreliable at this frame rate.** Acquire
  faster, or use a gap-closing linker, and treat trajectories with suspicion.

Run it before a tracking experiment, not after.


Derived quantities inherit every upstream assumption
------------------------------------------------------

Viscosity from video particle tracking is a good example of how deep a dependency
chain can run:

.. code-block:: text

   pixel size  →  detection  →  linking  →  MSD  →  diffusion fit  →  Stokes-Einstein

A wrong **pixel size** produces a wrong viscosity even when every subsequent step is
flawless. A wrong **frame interval** does the same. Mis-linked trajectories inflate
the MSD and drag the fitted diffusion coefficient — and therefore the viscosity —
without producing any visible error.

**Consequences to internalise:**

* **Confirm the pixel size and frame interval before trusting any physical unit.**
  PyCAT reads them from metadata where possible and gates workflows behind an explicit
  pixel-size entry when it cannot.
* **A number can be precise and wrong.** Tight error bars on a mis-linked ensemble are
  tight error bars on a mis-linked ensemble.
* **Sanity-check against a known sample.** For microrheology, a bead-in-glycerol
  calibration (a medium of known viscosity) is the single most valuable control you
  can run — PyCAT provides a "no host / full frame" VPT mode specifically for this.


Segmentation quality bounds everything downstream
---------------------------------------------------

No measurement can be better than the mask it was measured through. PyCAT provides
several ways to interrogate this rather than assume it:

* **Pipeline Step Diagnostics** — inspect the intermediate image at every stage.
* **Pipeline SNR Analysis** — is there enough signal to segment at all?
* **Segmentation Benchmark** — compare methods against ground truth, or against each
  other, and see parameter sensitivity.
* **Data Quality Control** and **Frame Quality / Focus QC** — is this data usable
  before you analyse it?

If a measurement matters, look at the mask that produced it.
