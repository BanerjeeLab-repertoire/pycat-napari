Measurement Guidance: Trusting Your Numbers
===========================================

This page documents measurement effects that **change how you should interpret your
results**, and that are not obvious from the software's output. They are not bugs —
most are consequences of optics and sampling that apply to *any* image analysis
tool. PyCAT's position is that you should know about them, and that the software
should quantify them rather than hide them.

Every claim here is backed by a numerical experiment; the details are in
``docs/audits/``.

.. contents::
   :local:
   :depth: 2


.. _upscaling-guidance:

Upscaling: what it does and does not do
---------------------------------------

Several PyCAT workflows upscale an image before segmenting it. This is legitimate,
but it is easy to misunderstand what it buys you.

**Upscaling adds no information.**
Interpolation is a deterministic function of the pixels you already had. It cannot
resolve anything the optics did not capture. In testing, upscaling **never** split
two objects that native-resolution segmentation merged, at any separation — the
**point-spread function (PSF), not the pixel grid, sets the resolution limit**. If
the optics blurred two condensates together, no amount of upscaling recovers them,
and a "gap" that appears at 4× is interpolation, not signal.

**The one legitimate reason to upscale** is to satisfy a segmentation model's
learned object-scale prior. Cellpose, for example, performs best on cells around 30
pixels across. If your cells are 8 pixels across, the network's features are
scale-mismatched — not because the information is missing, but because the *model*
cannot read it at that scale. Upscaling re-presents identical information at a scale
the algorithm was trained on. **This is a property of the algorithm, not of the
data.**

.. warning::

   **Do not measure intensities on an upscaled image.** It is not a small
   inaccuracy; it corrupts your statistics in two specific ways:

   * **Pseudoreplication.** A 4× upscale gives 16× more "samples" and zero new
     photons. The reported standard error comes out roughly **1.5× smaller than the
     true uncertainty**, so every error bar and *p*-value is falsely confident.
   * **Size-dependent bias.** Interpolation blurs background into boundary pixels,
     diluting small objects more than large ones.

   PyCAT warns you when a measurement is about to read an upscaled layer.

**What to do instead.** Segment at whatever resolution works, then measure on the
**original pixels** using
:doc:`Partial-Volume Measurement <../features>` (Toolbox ▸ Cell and Object Analyses).
It maps the high-resolution mask onto the native pixel grid as *fractional coverage
weights* and computes weighted statistics on the original detector values, with an
effective sample size so the error bars stay honest.

.. note::

   **Why not simply downscale the mask?** Because binarising destroys information.
   A native edge pixel sitting at intensity 60, between a background of 20 and an
   object of 100, genuinely encodes *"I am about 50% covered."* Forcing it to 0 or 1
   throws that away — and measurably makes small objects **worse** than the status
   quo. Partial-volume weighting keeps that information without ever reading an
   interpolated pixel.


.. _size-bias-guidance:

The size–intensity bias (read this before comparing conditions)
----------------------------------------------------------------

This is the most important item on this page, because **it survives every
improvement to the software** and it can produce a confident, statistically
significant, completely false result.

**The effect.** A pixel at an object's boundary physically integrates a *mixture* of
object and background photons, because the optics blur across the edge. Small
objects have proportionally more boundary pixels than large ones, so **small objects
measure dimmer than they truly are, and the bias shrinks as objects grow.**

This is **optical, not computational**. It is present when you measure a perfect
mask on the original pixels. It cannot be removed by better segmentation, better
masking, or partial-volume weighting — only by deconvolution / PSF modelling.

**Why it is dangerous.** Consider three objects with **identical true intensity**:

.. list-table::
   :header-rows: 1
   :widths: 20 25 30

   * - Object radius
     - Measured intensity
     - Predicted optical bias
   * - 3 px
     - **72.9**
     - −52 %
   * - 9 px
     - **91.4**
     - −19 %
   * - 15 px
     - **94.9**
     - −11 %

The truth is that all three are equally bright. The measurement shows a clean,
convincing **intensity-versus-size correlation that does not exist.**

**The consequence for comparative work.** A uniform bias would cancel when you
compare two conditions — that is the usual and correct intuition. But this bias is
**not uniform: it is a gradient in object size.** So:

.. warning::

   **A treatment that changes only object SIZE will fabricate an apparent change in
   INTENSITY.**

   In simulation, two populations with *identical* true intensity but different sizes
   (radius 3 px vs 8 px) produced an apparent **+12 % intensity difference** with
   Cohen's *d* > 20 and *p* ≈ 10\ :sup:`−83`. Nothing about the analysis was wrong.
   The effect is entirely optical.

**What PyCAT does about it.** Because the bias cannot be removed, PyCAT
**quantifies** it for your own imaging conditions:

* The PSF width is estimated **from your image**.
* Every object in a Partial-Volume Measurement carries a ``predicted_bias_pct``
  column and a ``sub_resolution`` flag.
* A field-level advisory reports the bias at your smallest, median, and largest
  object.
* A **size-confound warning** fires when the objects in a field span a wide enough
  size range that an intensity-versus-size trend cannot be distinguished from the
  artefact.

**What you should do**

* **Report size distributions alongside intensities.** Always.
* **Compare size-matched subsets** when testing an intensity difference between
  conditions whose size distributions differ.
* **Prefer intensity as a function of size** over a single mean, so a reader can see
  whether an effect survives at matched sizes.
* **Treat sub-resolution objects with great suspicion.** If an object's radius is
  comparable to the PSF, its absolute intensity is dominated by background mixing
  and is not trustworthy by *any* method. PyCAT flags these.

.. note::

   None of this means small-object intensities are useless. It means the *absolute*
   value is biased in a **predictable, size-dependent** way. Comparisons at matched
   size remain sound, and ratios computed within the same object (for example a
   partition coefficient, where numerator and denominator share the object) are far
   less exposed than a raw mean.


.. _bit-depth-guidance:

Saved data: dtype, compression, and what is worth keeping
----------------------------------------------------------

* **Masks are saved compressed.** A 1024×1024 label mask compresses roughly 100–160×
  losslessly. Earlier versions wrote masks uncompressed.
* **Bit-depth is right-sized.** A binary mask is stored as 8-bit, not 16-bit; a label
  image with fewer than 256 objects is 8-bit. Images are never up-cast, and float
  images are never silently rescaled (an earlier version multiplied normalised floats
  by 65535, fabricating precision).
* **Upscaled layers are flagged as reconstructable in the save dialog** and are
  unticked by default. An upscaled image is a pure interpolation of its source — it
  can be recreated from the original and the scale factor, so saving it costs 4× or
  16× the pixels for no new information. A *mask segmented at high resolution* is
  **not** redundant and is never flagged as disposable.


Further reading
---------------

The full investigations, including the numerical experiments and the approaches that
were tested and **rejected**, are in the repository under ``docs/audits/``:

* ``upscaling_and_measurement_audit.md`` — the upscaling / partial-volume / size-bias
  investigation.
* ``mask_storage_findings.md`` — why run-length encoding and keyframe deltas were
  measured and *not* adopted.
