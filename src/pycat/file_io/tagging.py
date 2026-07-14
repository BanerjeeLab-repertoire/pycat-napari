"""
**What do we KNOW about this layer, and how do we know it?**

Layer tagging at load: role, dimensionality, calibration, provenance, modality, channel — plus the
tags a PyCAT-saved TIFF carries **inside** it, which must survive a round trip and **override** a
fresh inference, because *a user's answer outranks the loader's guess.*

── The 1.0 sentinel lives here now ──────────────────────────────────────────────────────

``_calibration_is_from_metadata`` comes with the tagger, because *calibration provenance is a fact
about the layer* — and it was called from nowhere else but the two functions in this module.

**It exists because `1.0 µm/px` was doing two jobs**: a real value and a missing-value sentinel. The
old test was ``abs(mpp - 1.0) > 1e-9``, on the reasoning that a real microscope pixel size is
essentially never exactly 1.0.

***"Essentially never" is not never.*** A downsampled, low-magnification, derived or synthetic image
can have a **genuine** 1.0 µm/px — and PyCAT would throw that calibration away and prompt for a
scale it had already been told.

*The same sentinel was found and fixed in four separate places (1.6.15, 1.6.23, 1.6.24 ×2). This is
the one function that now answers the question, by reading `pixel_size_source` — **where the number
came from** — rather than guessing from what it is.*
"""

from __future__ import annotations

import numpy as np

from pycat.file_io.routing import _read_pycat_tags
from pycat.file_io.writers import _apply_saved_tags_to_layer
from pycat.utils.general_utils import debug_log


def _calibration_is_from_metadata(dr, microns_per_pixel) -> bool:
    """**Provenance comes from WHERE the number came from, not WHAT it is.**

    The stack loader used to decide this from the value::

        dr['pixel_size_from_metadata'] = (abs(float(microns_per_pixel) - 1.0) > 1e-9)

    with the comment *"a real microscope pixel size is essentially never exactly 1.0 µm/px, so
    treat 1.0 as the no-metadata fallback."*

    ***"Essentially never" is not never.*** A downsampled, low-magnification, derived, or synthetic
    image can have a **genuine** 1.0 µm/px — and PyCAT would throw that calibration away and prompt
    for a scale it had already been told. **1.0 was doing two jobs — a real value and a
    missing-value sentinel — and no code downstream could tell which one it was holding.**

    **The honest answer already exists.** ``metadata_extract`` records ``pixel_size_source``
    (``'ims_extents'``, ``'tiff_tags'``, ``'ome_metadata'``, or ``None`` when nothing was found),
    it is populated on every load, and it was **only ever displayed.** Read it.

    *(The 2-D path in ``data_modules`` was already doing this correctly — it sets the flag from
    whether the tag was PRESENT. Only the stack path guessed from the value.)*
    """
    try:
        source = ((dr.get('file_metadata') or {}).get('common') or {}).get('pixel_size_source')
    except Exception:
        source = None

    if source:
        return True

    # No source recorded — either the extractor found nothing, or this load path did not run it.
    # Fall back to the old value-based guess rather than silently declaring the image uncalibrated:
    # a wrong `True` suppresses the gate, but a wrong `False` only asks a question that can be
    # answered.
    return abs(float(microns_per_pixel) - 1.0) > 1e-9


def _prompt_pixel_size_if_needed(central_manager):
    """After a load, show the modal pixel-size dialog if the freshly-loaded
    image has no valid physical scale. Separate from the in-dock gate; both
    read/write the same data_repository scale so they stay consistent."""
    try:
        from pycat.ui.field_status import prompt_pixel_size_on_load
        prompt_pixel_size_on_load(
            lambda: central_manager.active_data_class.data_repository,
            central_manager=central_manager)
    except Exception as _prompt_exc:
        # **This is the last line of defence for an uncalibrated image.** If the prompt does not
        # appear, the image keeps its 1.0 µm/px default and *every length, area and diffusion
        # coefficient is silently in pixels while the column header says microns.*
        #
        # It was wrapped in `except Exception: pass`. **A failure here produced no dialog, no
        # message, and a perfectly normal-looking load.**
        from pycat.utils.general_utils import report_guarantee_failure
        report_guarantee_failure("file_io: pixel-size prompt on load", _prompt_exc)

def _tag_loaded_layer(central_manager, layer, role=None, n_t=1, n_z=1, n_p=1,
                      microns_per_pixel=None, file_path=None,
                      modality=None, channel=None, provenance='raw'):
    """Populate tags on a freshly-loaded layer from what the load path already
    knows — dimensionality, scale calibration, role, provenance, and (when
    available) modality/channel. Also re-applies any tags saved inside the
    file (PyCAT-saved TIFFs embed their tag store), with saved user overrides
    taking precedence over freshly-inferred tags.

    This is the single load-time tagging entry point; call it once per layer
    after it is added to the viewer. No new detection is performed — it
    captures inferences the loaders already made into the structured tag store
    so autopopulation can query typed facts instead of matching names.
    """
    if layer is None:
        return
    try:
        from pycat.utils import layer_tags as _LT
    except Exception:
        return

    # 1. Inferred tags from load context.
    try:
        if role:
            _LT.tag_layer(layer, 'role', role, source='inferred')

        # Dimensionality from the axis sizes the loader parsed.
        if n_p and n_p > 1:
            dim = 'multi-position'
        elif n_t and n_t > 1:
            dim = '2d+t'
        elif n_z and n_z > 1:
            dim = 'z-stack'
        else:
            dim = '2d'
        _LT.tag_layer(layer, 'dimensionality', dim, source='inferred')

        # Scale calibration: a real pixel size is essentially never exactly
        # 1.0 µm/px, so 1.0 means "no metadata / uncalibrated". Viscosity and
        # any physical measurement depend on this, so it is a first-class tag.
        if microns_per_pixel is not None:
            # Provenance from WHERE the number came from, not WHAT it is. The old test was
            # `abs(mpp - 1.0) > 1e-9`, on the reasoning that a real pixel size is essentially
            # never exactly 1.0 — but **"essentially never" is not never**, and a downsampled or
            # synthetic image with a genuine 1.0 µm/px had its calibration thrown away.
            # (Same sentinel fixed in `_finalise_stack_load`; this copy was still live.)
            _dr_now = central_manager.active_data_class.data_repository
            calibrated = _calibration_is_from_metadata(_dr_now, microns_per_pixel)
            _LT.tag_layer(layer, 'scale',
                          'calibrated' if calibrated else 'uncalibrated',
                          source=('from_metadata' if calibrated else 'inferred'))

        # ── The stack axis belongs to the LAYER, not to the session ─────────────────
        #
        # `stack_axis_label` lives in `data_repository`, which is **one dict shared by every
        # layer**. PyCAT can add a second file without clearing — "Open Image (Add)", and
        # multi-select in the file dialog, which *"loads each subsequent file with
        # clear_first=False"*.
        #
        # So: open an undeclared movie, label it **T**. Add an undeclared z-stack, label it
        # **Z**. ***The second load overwrites the first's label.*** An MSD on the movie now
        # reads "Z" — and `warn_if_assumed_axis` warns about the wrong thing, on the layer the
        # user labelled correctly.
        #
        # **T and Z load identically**, so there is nothing on screen to reveal it.
        #
        # The tag is per-layer and travels with it. `source='user_set'` because the user was
        # *asked* — this is not an inference, it is an answer, and it must not be silently
        # overwritten by the next file's answer.
        try:
            _dr_axis = central_manager.active_data_class.data_repository
            if _dr_axis.get('stack_axis_assumed') and (n_t > 1 or n_z > 1):
                _LT.tag_layer(layer, 'stack_axis',
                              str(_dr_axis.get('stack_axis_label') or '?').upper(),
                              source='user_set')
        except Exception as _axis_e:
            debug_log("file_io: could not tag the stack axis", _axis_e)

        _LT.tag_layer(layer, 'provenance', provenance, source='inferred')

        if modality:
            _LT.tag_layer(layer, 'modality', modality, source='inferred')
        if channel:
            _LT.tag_layer(layer, 'channel', channel, source='from_metadata')
    except Exception as _e:
        debug_log("file_io: load-time tagging failed", _e)

    # 2. Re-apply any tags saved inside the file (overrides win). Applied
    #    AFTER inference so a saved user_set tag locks over a fresh inference.
    try:
        if file_path:
            saved = _read_pycat_tags(file_path)
            if saved:
                _apply_saved_tags_to_layer(layer, saved)
    except Exception as _e:
        debug_log("file_io: reapplying saved tags failed", _e)
