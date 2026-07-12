Loading and Saving Data
=======================

This page explains PyCAT's file-handling behaviour, including the dialogs it may show
you and why, and what it does with your data when it saves.

.. contents::
   :local:
   :depth: 1


"Is this a time series or a z-stack?"
-------------------------------------

**When you see this:** you opened a multi-page TIFF that carries no axis metadata.

A plain multi-page TIFF is just a stack of images. Unless it was written by ImageJ or
carries OME metadata, **the file does not record whether those pages are timepoints or
z-slices** — the axis is genuinely unlabelled (the TIFF standard leaves it as an
unknown axis, which readers report as ``Q``).

The two load identically, so the distinction does not matter for viewing. It matters a
great deal for **analysis**:

* Treating z-slices as time produces a meaningless diffusion coefficient.
* Treating timepoints as z produces a meaningless volume.

So PyCAT asks, rather than guessing. Your options:

* **Time series (T)** — the pages are timepoints.
* **Z-stack (Z)** — the pages are optical sections.
* **Separate 2D images** — the pages are unrelated fields, not a stack.

You can tick *remember this choice* to avoid being asked repeatedly in a session.

**PyCAT remembers that the axis was assumed.** If you later run an analysis that
depends on the axis type — VPT, FRAP, and Droplet Fusion treat frames as *time*;
z-stack 3-D metrics treat the axis as *depth* — it will remind you once that the label
was your assumption, not the file's.

.. tip::

   If you control the acquisition or export, save with real axis metadata (OME-TIFF, or
   an ImageJ-style TIFF). It removes an entire class of ambiguity.


"Copy this file to local storage first?"
----------------------------------------

**When you see this:** you opened a file that lives on a network share, a removable
drive, or cloud storage that has not been downloaded.

PyCAT probes where a file lives before loading it. Reading a large movie repeatedly
across a slow link is painful, and some analyses read frames many times. If the storage
is slow, PyCAT offers to copy the file to fast local temporary storage first and load
from the copy.

* The copy runs behind a **progress bar with a Cancel button** (the copy *is* the slow
  part, so this doubles as a load progress indicator).
* A file already cached locally is **reused**, not re-copied.
* Cached copies older than a day are cleaned up automatically.
* You can choose *always* or *never* for the session.

Loading from fast local storage is silent — you will not see this dialog for files on
your own SSD.


What PyCAT writes when you save
-------------------------------

**Layers are compressed.** Masks compress enormously (a 1024×1024 label mask goes from
about 2 MB to roughly 13 kB, losslessly). Images compress far less because they carry
real noise, which is expected.

**Bit depth is right-sized.** A binary mask is saved as 8-bit rather than 16-bit; a
label image with fewer than 256 objects is 8-bit. Images are **never up-cast**, and
float images are never silently rescaled.

**Stacks declare their axis.** Saved stacks record whether they are ``TYX`` or ``ZYX``,
so reopening one in PyCAT does not re-trigger the "is this time or z?" question.

**Upscaled layers are flagged as reconstructable.** In the save dialog, each layer shows
its estimated size, and layers that are **pure upscales of another layer** are marked
and **unticked by default**:

* An **upscaled image** is an interpolation of its source. It carries no new
  information and can be recreated from the original plus the scale factor — so saving
  it costs 4× or 16× the pixels for nothing.
* A **mask segmented at high resolution** is **not** redundant. Its boundaries are real
  information, and it is never flagged as disposable.

.. seealso::

   :doc:`measurement_guidance` — why an upscaled image adds no information, and why you
   should not measure intensities on it.
