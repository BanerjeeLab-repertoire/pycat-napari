"""
**What shape is this file? Ask once, and carry the answer.**

── The dispatch chain asked the same question three times ───────────────────────────────

    _add_image_or_mask_single   -> open_image()  "is this an image or a mask?"
    _open_image_auto_single     -> open_image()  "2D or a stack?"  <- reads dims + scenes
      -> open_stack             -> open_image()  <- reads dims + scenes AGAIN
         OR open_2d_image       -> open_image()  <- and again

``_open_image_auto_single`` **already knows** the answer. It opens the file, reads ``.dims`` and
``.scenes``, decides 2-D versus stack — **and then throws all of it away.** The next function opens
the file and works it out again.

The 1.6.6 reader cache made the *re-opening* free. **It did not make the re-inspection free** —
and on a CZI, ``.dims`` walks the subblock directory. *The cache hid the design flaw rather than
fixing it.*

── And the cache introduced a correctness bug of its own ────────────────────────────────

**A cached reader is shared, and ``set_scene()`` mutates it.** Two call sites hold the *same
object*, so a site that moves to scene 2 leaves the next caller's reader **parked on scene 2** —
reading **the wrong field of view**, with ***nothing about the image looking broken.***

*(That is patched in ``open_image``, which rewinds a cached reader to its first scene. But the
deeper answer is not to have several functions independently reaching for a reader at all.)*

── What this is ─────────────────────────────────────────────────────────────────────────

A plain record of what an inspection found. **Inspect once at the top of the chain, pass it down.**
Nothing downstream needs to re-derive it, and nothing downstream can disagree with it — *which is
its own kind of bug, and one that would be very hard to see.*
"""

from __future__ import annotations

import os


class ImageStructure:
    """**What an inspection of the file found.** Immutable by convention.

    ``is_stack`` is the decision the whole dispatch turns on, and it is made **here, once** —
    rather than by each function, from its own re-reading of the file, with its own subtly
    different rule.
    """

    __slots__ = ('path', 'extension', 'n_t', 'n_z', 'n_c', 'n_scenes', 'dtype', 'parsed')

    def __init__(self, path, extension='', n_t=1, n_z=1, n_c=1, n_scenes=1,
                 dtype=None, parsed=False):
        self.path = path
        self.extension = extension
        self.n_t = int(n_t or 1)
        self.n_z = int(n_z or 1)
        self.n_c = int(n_c or 1)
        self.n_scenes = int(n_scenes or 1)
        self.dtype = dtype
        # `parsed` is False when the reader could not tell us anything — a broken file, a missing
        # plugin. **A structure that admits it does not know is worth more than one that guesses.**
        self.parsed = bool(parsed)

    @property
    def is_stack(self):
        """More than one plane along **any** axis, or more than one scene."""
        return self.n_t > 1 or self.n_z > 1 or self.n_scenes > 1

    @property
    def is_multiscene(self):
        return self.n_scenes > 1

    def __repr__(self):
        return (f"ImageStructure({os.path.basename(str(self.path))}: "
                f"T={self.n_t} Z={self.n_z} C={self.n_c} S={self.n_scenes} "
                f"{'stack' if self.is_stack else '2D'}"
                f"{'' if self.parsed else ' UNPARSED'})")


def inspect_image(image, path):
    """**Read the shape once.** Never raises — an unreadable file gets ``parsed=False``.

    *A structure that admits it does not know is worth more than one that guesses* — and the caller
    can then fall back deliberately, rather than acting on a fabricated ``1``.
    """
    extension = os.path.splitext(str(path))[1].lower()

    try:
        dims = image.dims
        n_t = int(getattr(dims, 'T', 1) or 1)
        n_z = int(getattr(dims, 'Z', 1) or 1)
        n_c = int(getattr(dims, 'C', 1) or 1)
    except Exception:
        return ImageStructure(path, extension, parsed=False)

    try:
        scenes = image.scenes
        n_scenes = len(scenes) if scenes is not None else 1
    except Exception:
        n_scenes = 1

    try:
        dtype = image.dtype
    except Exception:
        dtype = None

    return ImageStructure(path, extension, n_t=n_t, n_z=n_z, n_c=n_c,
                          n_scenes=n_scenes, dtype=dtype, parsed=True)
