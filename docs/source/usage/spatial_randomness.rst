Spatial randomness: what Moran's I can and cannot tell you
==========================================================

This page exists because a plausible-looking statistic in PyCAT was reporting
confident nonsense, and the reason is subtle enough to be worth writing down.

The short version
-----------------

* **Moran's I is saturated on condensate images and reports nothing.** On a bright
  image of extended objects it sits at 0.99+ and cannot move, no matter how the
  objects are arranged.
* **It is genuinely useful on SMLM / single-molecule data**, where localisations are
  near-point and sparse. That is the regime it was designed for.
* PyCAT now **measures whether it can move at all** (its *headroom*) and refuses to
  interpret it when it cannot.
* The question most users actually have — *"is there real structure here, or just
  blur?"* — is answered by :func:`structure_beyond_optics`, not by Moran's I.

What Moran's I measures
-----------------------

Moran's I is a spatial autocorrelation statistic: it asks whether neighbouring pixels
are more similar than a random rearrangement of the same pixel values. It is the one
statistic in this module sensitive to **arrangement at a fixed intensity histogram**.

That capability is real and exclusive. Two images with the *identical* intensity
histogram — the same bright pixels, clustered into blobs versus scattered at random —
have **identical kurtosis** (22.345 in both cases), because kurtosis is a property of
the value distribution and cannot see where the values sit. Moran's I separates them
cleanly: 0.913 versus 0.006.

So the statistic is not junk. The problem is where it is applied.

Why it fails on condensate images
---------------------------------

Moran's I of a real image is a **blend** of the signal's autocorrelation and the
noise's:

.. math::

   I \;\approx\; f_{\text{signal}} \times I_{\text{signal}}

where :math:`f_{\text{signal}}` is the fraction of the variance carried by the signal.

For any **extended** object, :math:`I_{\text{signal}}` is close to 1 — every pixel
inside a droplet looks like its neighbour *regardless of where the droplet sits*. So on
a bright image of extended objects, I is pinned near 1 and **has no room left to
respond to anything**. Rearranging the objects entirely cannot move it.

Measured across 63 combinations of object size and SNR, comparing an image of dispersed
objects against the *same* objects aggregated into a clump:

.. list-table::
   :header-rows: 1

   * - headroom (1 − I)
     - n
     - median gap
     - max gap
   * - **< 0.02**
     - 6
     - 0.0043
     - **0.0093**
   * - 0.02 – 0.15
     - 18
     - 0.018 – 0.041
     - 0.158
   * - > 0.15
     - 39
     - 0.0853
     - 0.297

Below a headroom of 0.02, the difference between *fully dispersed* and *fully
aggregated* **never exceeded 0.009**. The statistic is dead. Any value it reports is a
property of the object size and the image brightness, not of the arrangement.

.. warning::

   **The threshold is on headroom, not on object size.** An earlier attempt to give a
   size rule — "useless above about 2 pixels" — was **wrong**, and it is worth saying so
   plainly: the same object size flips between usable and saturated depending on SNR,
   because it is *noise* that dilutes I away from 1 and gives it room to move. A
   simulation-derived size threshold was really a measurement of the SNR that happened
   to be simulated.

   Headroom captures size and SNR together, and — unlike either alone — it is measurable
   from the single image in hand, with no ground truth required.

Where it *does* work: SMLM
--------------------------

Single-molecule localisation data is the regime Moran's I was built for: near-point
emitters, sparse, on a dark field. There the pixels genuinely differ from their
neighbours, I is far from its ceiling, and arrangement moves it.

.. list-table::
   :header-rows: 1

   * - image
     - Moran's I
     - headroom
     - usable?
   * - SMLM, random localisations
     - 0.655
     - 0.345
     - **yes**
   * - SMLM, clustered localisations
     - 0.772
     - 0.228
     - **yes**
   * - condensates (8 px, bright)
     - 0.999
     - 0.001
     - **no — saturated**

The discriminating gap on SMLM data is **0.117**, against roughly **0.002** on condensate
images. Same statistic, two orders of magnitude difference in usefulness, decided entirely
by what is in the image.

:func:`morans_I_headroom` makes this call automatically. Nothing is hard-coded about
"SMLM" or "condensates" — the guard simply measures whether the statistic has room to
respond, and says so.

The question you probably meant to ask
--------------------------------------

If what you want to know is *"is there real spatial structure here, or is this just
noise through a lens?"*, **Moran's I cannot answer it**, and neither can any null model
you pair it with.

The reason is structural. The correct null for that question preserves the image's
autocorrelation (so that the microscope's blur is present in the null too) and destroys
only the real structure. But **Moran's I is a function of the autocorrelation** — so any
null that preserves the autocorrelation preserves Moran's I *by construction*. Against
such a null, Moran's I has a correct 4 % false-positive rate and **0–12 % power**: it is
blind, not miscalibrated.

Use :func:`structure_beyond_optics` instead. It compares the image's **kurtosis** against
a **phase-randomised surrogate** — one with the identical amplitude spectrum (hence the
identical autocorrelation, by Wiener–Khinchin) but randomised phases, which is where real
structure lives. It is calibrated (0 % false positives on an empty field) and detects
condensates reliably from about SNR 4 upward.

.. note::

   Building a null model is not enough — **the null has to be checked**. The first
   phase-randomisation attempt here enforced Hermitian symmetry incorrectly and produced
   surrogates with a kurtosis of ~650 against the data's ~0. That biased null made every
   test fire, at a 100 % false-positive rate, and it looked like a working detector. It
   was caught only by comparing the surrogate's own moments against the data's. A null
   whose statistics do not match the data is not a null.
