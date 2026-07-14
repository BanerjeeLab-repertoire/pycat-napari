"""
**An implicit full-stack read is never what the caller meant.**

── The landmine that has now cost this project three bugs ────────────────────────────────

``np.asarray(layer.data)`` on a lazy stack has already produced:

* **N&B** telling users their movie was 2-D *(it read frame 0 and reported the shape)*
* **SpIDA** silently analysing frame 0 while the user looked at frame 40
* **the IMS scrubbing lag** — and this one was documented in PyCAT's own source, months ago::

      "napari auto-estimates contrast (and builds the thumbnail) by calling np.asarray()
       on the layer — which for a lazy (T,Y,X) wrapper triggers __array__ and loads EVERY
       frame from disk. On a USB-HDD IMS stack that is the real cause of the multi-second
       stalls."

The fix for the first two was ``materialize_stack()``: **an explicit, named, deliberate full
read.** But ``__array__`` did the opposite — it **quietly stacked every frame**, so any thumbnail,
plugin, layer refresh, contrast estimate, or stray numpy operation could pull an entire acquisition
into memory *without anyone asking, and without anything saying so.*

── Why a shared function rather than nine copies ─────────────────────────────────────────

There were **nine** lazy wrappers. 1.6.3 fixed the **three** in ``multidim_io`` and left the **six**
in ``file_io`` — *including all three IMS wrappers, which are the ones that lag.* The guard passed,
because it only looked at ``multidim_io``.

***A fix applied to some of the instances of a bug is a fix that will be undone by the ones it
missed.*** One function, called from all nine, is the only shape that cannot rot.
"""

from __future__ import annotations


def refuse_implicit_full_read(wrapper):
    """**Raise.** Name what was attempted, and both things the caller might have meant.

    Called from every lazy wrapper's ``__array__``. Takes the wrapper so the message can carry its
    shape — *"a full read of 600×2048×2048"* lands very differently from *"a full read"*.
    """
    shape = getattr(wrapper, 'shape', None)
    kind = type(wrapper).__name__

    raise RuntimeError(
        f"An implicit full-stack read was attempted on {kind} "
        f"{f'of shape {tuple(shape)}' if shape is not None else ''}.\n\n"
        f"This pulls the ENTIRE acquisition into memory, one frame at a time off disk, and it is "
        f"almost never what was intended. It is the cause of the multi-second stalls when "
        f"scrubbing a large stack: napari calls np.asarray() on the layer to estimate contrast or "
        f"build a thumbnail, and every frame is read.\n\n"
        f"If a full read IS what you want, say so:\n"
        f"    from pycat.file_io.stack_access import materialize_stack\n"
        f"    array = materialize_stack(layer)\n\n"
        f"If you wanted ONE frame, index it:\n"
        f"    frame = stack[t]\n\n"
        f"If this fired inside napari, the layer needs explicit contrast_limits — see "
        f"_lazy_contrast_limits()."
    )
