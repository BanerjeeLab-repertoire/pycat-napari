"""
**The last step of every stack load, wherever the pixels came from.**

``_finalise_stack_load`` is what the IMS loader and the generic loader **both** call once their
layers are in the viewer: write the calibration into the data repository, tag the layers, fit the
camera, enable the scale bar, record the batch step, and — if the pixel size is still unknown —
**ask.**

── Why it can come out now, and could not before ────────────────────────────────────────

It depended on **five** methods of its 3,108-line host. *All five have since been extracted*:

    _enable_auto_scale_bar            -> napari_adapter
    _fit_view_to_layer                -> napari_adapter
    _add_diameter_annotation_layers   -> napari_adapter
    _tag_loaded_layer                 -> tagging
    _prompt_pixel_size_if_needed      -> tagging

**This is the split working.** Take what depends on nothing; the next layer then depends on nothing,
and comes out free. *Nothing here was clever — the previous five moves simply removed every reason
for this one to stay.*
"""

from __future__ import annotations

import numpy as np

from pycat.file_io.napari_adapter import (_add_diameter_annotation_layers,
                                          _enable_auto_scale_bar,
                                          _fit_view_to_layer)
from pycat.file_io.tagging import (_calibration_is_from_metadata,
                                   _prompt_pixel_size_if_needed,
                                   _tag_loaded_layer)
from pycat.utils.frame_interval import record_time_axis
from pycat.utils.general_utils import debug_log


def _finalise_stack_load(viewer, central_manager, H, W, microns_per_pixel,
                         channels_to_load, n_t, n_z, file_path, source='generic'):
    """Update data repository and record batch step after any stack load."""
    dr = central_manager.active_data_class.data_repository

    # Both stack loaders funnel through here, and `n_t` is already a parameter — so this is the
    # one place that sees every stack, IMS and generic alike. See `record_time_axis`: the
    # frame-interval warning must not fire on an image that has no time axis.
    record_time_axis(dr, n_t)

    dr['object_size']       = H // 20
    dr['cell_diameter']     = H // 8

    # ── A corrupt resolution tag must NOT silently satisfy the pixel-size gate ──
    #
    # An ImageJ Substack export can write a physically-impossible pixel size (a 32-bit resolution
    # overflow → e.g. 2.3e-7 µm/px, ~picometres). The 2D loader already screens this, but the STACK
    # loaders (IMS + generic + the tifffile fallback) all funnel through here and did NOT — so the
    # corrupt value flowed straight into `microns_per_pixel_sq` with `pixel_size_from_metadata=True`,
    # which SATISFIED the gate: the warning printed but the Set-Scale dialog never appeared and the
    # field stayed hidden. A wrong scale that looks resolved is worse than a missing one — every
    # length, area and diffusion coefficient downstream is then computed from a fabricated number.
    #
    # So the same optics-based plausibility screen runs here: implausible → fall back to the 1.0
    # sentinel with `pixel_size_from_metadata=False` (and confirmed cleared), which is exactly the
    # state the gate fires on. Say WHY, in microscopist terms.
    from pycat.utils.pixel_size import is_physically_plausible, implausible_reason
    if is_physically_plausible(microns_per_pixel):
        dr['microns_per_pixel_sq'] = microns_per_pixel ** 2
        # Provenance for the Set-Scale overwrite warning and the pixel-size gate. Derived from
        # `pixel_size_source`, NOT from whether the value happens to be 1.0 — see the helper.
        dr['pixel_size_from_metadata'] = _calibration_is_from_metadata(dr, microns_per_pixel)
    else:
        # `update_metadata` (the OME/ImageJ path) may have ALREADY detected and rejected this same
        # corrupt tag, setting the sentinel + from_metadata=False and warning once. Only warn here if
        # that has not happened — the invalidation below is idempotent and always runs, so the gate
        # ends up in the firing state either way; this just avoids a duplicate warning.
        _already_rejected = (dr.get('microns_per_pixel_sq') in (1, 1.0)
                             and dr.get('pixel_size_from_metadata') is False)
        if not _already_rejected:
            try:
                _reason = implausible_reason(microns_per_pixel)
                from napari.utils.notifications import show_warning as _sw
                _sw("The pixel size in this file is not physically possible: "
                    f"{_reason}\n\nPyCAT will ask you to enter the correct scale.")
            except Exception:
                pass
        # ── Why 1, and why it is TAGGED, not arbitrary ────────────────────────────────
        #
        # 1 is the "unknown scale" PLACEHOLDER, not a guess about the optics. A napari image layer
        # needs a positive, finite `layer.scale` to render and to draw a scale bar (napari_adapter
        # reads `microns_per_pixel_sq` to set it) — 0 / NaN / None would give the layer a degenerate
        # transform. 1 maps it at 1 µm/px, which is renderable and harmless.
        #
        # The two provenance flags are the "real-scale tag" that keeps this HONEST: a real
        # calibration sets `pixel_size_from_metadata` (from the file) or `pixel_size_confirmed` (the
        # user), so the placeholder is the one state with BOTH False. That is exactly the check in
        # `pixel_size.has_real_pixel_size()` and the field_status gate, and it is why the analysis
        # accessor `pixel_size.pixel_size_um()` returns NaN here rather than a fake 1-µm measurement.
        # The tag CLEARS automatically the moment metadata supplies a scale or the user confirms one.
        dr['microns_per_pixel_sq'] = 1
        dr['pixel_size_from_metadata'] = False
        # Clear any stale explicit-confirmation so the gate is not held shut by a prior file.
        dr['pixel_size_confirmed'] = False

    # The pixel size has just been set from this file's metadata (or fallen
    # back to 1.0). A plain load does not switch the data class, so notify
    # any registered gates (e.g. the pixel-size gate) to re-evaluate now,
    # otherwise the gate would keep its pre-load state and never appear.
    try:
        central_manager.notify_data_changed()
    except Exception:
        pass
    _prompt_pixel_size_if_needed(central_manager)

    _add_diameter_annotation_layers(viewer)

    # Label the non-spatial slider axes so they read "T"/"Z" instead of the
    # default "0"/"1". napari shows one slider per axis beyond the displayed
    # two (Y, X); giving them names makes multi-dimensional browsing legible.
    try:
        ndim = 2
        if n_t and n_t > 1:
            ndim += 1
        if n_z and n_z > 1:
            ndim += 1
        if ndim > 2:
            # Axis order for the loaded stacks is (T, Z, Y, X) with whichever
            # of T/Z are present; build labels to match.
            labels = []
            if n_t and n_t > 1:
                labels.append('T')
            if n_z and n_z > 1:
                labels.append('Z')
            labels += ['Y', 'X']
            if len(labels) == viewer.dims.ndim:
                viewer.dims.axis_labels = labels
    except Exception:
        pass

    # Open on the FIRST frame/slice, not napari's default centre. Most image
    # viewers open a stack on index 0; napari initialises each slider to the
    # middle of its axis, so a freshly-loaded time series or z-stack would
    # otherwise start mid-movie. Set every non-displayed (slider) axis to 0.
    # The last two axes are the displayed Y,X plane and are left untouched.
    try:
        if viewer.dims.ndim > 2:
            step = list(viewer.dims.current_step)
            for ax in range(viewer.dims.ndim - 2):
                step[ax] = 0
            viewer.dims.current_step = tuple(step)
    except Exception:
        pass

    # Auto scale bar for the freshly-loaded stack.
    _enable_auto_scale_bar(viewer, central_manager)

    # ── Tag the freshly-loaded stack layers ──────────────────────────────
    # Populate the structured tag store from the load context (role, the
    # dimensionality just parsed, scale calibration, provenance) so downstream
    # autopopulation can query typed facts rather than matching names. Tag
    # only Image layers that are not yet tagged (i.e. the ones just added);
    # channel identity is left to metadata-driven naming already applied.
    try:
        import napari as _np_napari
        from pycat.utils import layer_tags as _LT
        for _lyr in viewer.layers:
            if _lyr.__class__.__name__ != 'Image':
                continue
            if _LT.get_tag(_lyr, 'role') is not None:
                continue  # already tagged (not freshly added)
            _tag_loaded_layer(central_manager, 
                _lyr, role='image', n_t=n_t, n_z=n_z,
                microns_per_pixel=microns_per_pixel, file_path=file_path,
                channel=getattr(_lyr, 'name', None), provenance='raw')
    except Exception as _e:
        debug_log("file_io: stack layer tagging failed", _e)

    # Fit the canvas to the newly-loaded image. Deferred long enough that the
    # scale bar has been applied and all layer-insert scale-alignment events
    # have flushed — otherwise the fit reads a stale extent and the image
    # opens tiny (whereas pressing Home later, once settled, fits correctly).
    try:
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(400, lambda: _fit_view_to_layer(viewer, central_manager))
    except Exception:
        _fit_view_to_layer(viewer, central_manager)

    bp = getattr(central_manager, '_pycat_batch_processor', None)
    if bp:
        bp.record('open_stack', {
            'file_path': file_path,
            'source': source,
            'channels': channels_to_load,
            'n_timepoints': n_t,
            'n_z': n_z,
        })
