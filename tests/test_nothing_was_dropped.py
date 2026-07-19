"""
**Did this change DELETE something?**

Gable, after the spurious-puncta incident:

    *"how do we make sure you don't throw away good code while doing these audits — the rationale
    was even in the code and you dropped it. We need some mechanism in this workflow to track these
    drops, because for all I know every module we've validated has truncated features away."*

**The concern is exactly right, and the failure mode is real.** Every edit in this workflow is a
**whole-file rewrite** — there is no diff, no merge, no three-way. If a rewrite emits fewer lines
than it read, **the difference is simply gone**, and:

* the file still **compiles**
* every test still **passes**
* the function still **exists**, just with fewer parameters

***A capability can disappear and nothing anywhere notices.*** That is exactly what happened:
``segment_subcellular_objects`` lost ``punctate_gate``, ``image_stats``, ``punctate_gate_sigma`` and
``punctate_gate_abs_sigma`` — **four safety parameters** — and spurious puncta came back with a
green test suite.

Why a diff against the last version is NOT enough
--------------------------------------------------
A first version of this guard compared the tree against the most recent snapshot. It reported
**"nothing dropped"** while the punctate gate was **entirely missing** — because **the baseline was
itself regressed.**

***A tool that compares against a broken baseline reports ALL CLEAR while everything is gone.***
That is the same failure it exists to prevent, one level up.

So the baseline is a **HIGH-WATER MARK**: for every function ever seen in **any** snapshot, the
**largest parameter set** and the **longest body** it has ever had. A capability that disappeared
three versions ago is **still missing today**, and this still says so.

``.pycat/high_water_mark.json`` — 1,825 functions, built from nine repo snapshots spanning
1.5.304 → 1.5.517, plus the working file Meet sent.

Every hit is a QUESTION, not a verdict
---------------------------------------
**A legitimate deletion looks exactly like an accidental one.** Moving a function to another module
is fine — that is what happened to the five stack helpers in 1.5.517, and ``file_io`` re-exports
them.

**The guard's job is to make sure the question gets asked.** When a deletion is deliberate, it goes
in ``_DELIBERATE`` *with a reason* — and that list is itself the record of what was removed and why.
"""

import ast
import json
import pathlib

import pytest


_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MARK = _ROOT / ".pycat" / "high_water_mark.json"

_SHRINK_THRESHOLD = 0.70


# ── Deletions that were DELIBERATE. Each needs a reason. ──────────────────────────────────
#
# This list is not an escape hatch — it is **the record of what was removed and why.** A future
# reader should be able to check every entry.
_DELIBERATE = {
    # 1.6.100 — the MSD plot's `_on_pick` (per-line pick_event handler) was removed with its whole
    # mechanism. An audit found the pick_event-plus-debounce approach intrinsically fragile (it
    # assumed all of a click's pick_events arrive before a zero-delay timer — not a safe contract),
    # so one click still cycled through many tracks. Replaced by a single canvas `button_press_event`
    # handler (`_connect_nearest_curve_click`) that fires once per click and selects the nearest
    # curve — there is nothing to debounce. `_on_pick` has no successor by name; the capability moved
    # to `_connect_nearest_curve_click` + `_apply_pick`.
    'analysis_plots.py::_on_pick',

    # 1.6.120 — the MSD spaghetti background became ONE LineCollection and selection an OVERLAY
    # (interaction-layer Gap 4). `_render_consolidated` (the VPT panel) shrank because its bespoke
    # blit + apply-pick + connect were replaced by a call to the shared `_msd_overlay_hooks`, which the
    # standalone `plot_msd_trajectories` also uses — so the panel and the standalone brush identically
    # from ONE implementation instead of two divergent copies. Nothing was dropped; the logic moved
    # into `_msd_overlay_hooks` (+ the coords hit-tester `_connect_nearest_curve_click_coords`).
    'analysis_plots.py::_render_consolidated',

    # 1.6.106 — session load moved OFF the Qt thread. `load_session` was one 149-line function that
    # read/decoded every file AND created the napari layers in one loop — so it could not be run on a
    # worker (layer creation off the main thread is a crash). It is split: `_read_session_payload`
    # (pure decode + CSV reads, no viewer — the slow half that now runs on a QThread) and
    # `_apply_session_payload` (the `viewer.add_*` + repository writes, on the caller's thread).
    # `load_session` is now the thin orchestrator that runs the read via `qt_worker.run_with_progress`
    # and applies the result. Nothing was dropped — the whole body moved into the two helpers, and the
    # synchronous round trip is pinned by `test_session_load_threading` and the existing
    # `test_session_load_lazy_image` suite.
    'session_loader.py::load_session',

    # 1.6.106 — `_prog`, the inline progress-callback closure in `_open_session_loader._on_load`, was
    # removed. It drove an in-dialog QProgressBar from the (blocked) main thread — the bar the 1.6.81/82
    # rollout added, which advanced while the window still said "Not Responding". The worker now owns a
    # modal QProgressDialog that keeps the window painting, so a second in-dialog bar would be two bars
    # for one operation (the UX trap the roadmap flagged); the inline bar and its `_prog` driver are
    # retired together.
    'ui_modules.py::_prog',

    # 1.6.104 — the picked-bead PULSE was removed. `_pulse_layer` armed a QTimer that oscillated the
    # ring's size/opacity to draw the eye. But the ring is per-frame — present only on the bead's own
    # frame — so scrubbing away from that frame left NOTHING to pulse while the opacity slider churned
    # on for nothing (reported from the viewer). Zoom-to-bead navigation draws the eye now; the ring is
    # a static marker. No successor — the pulse mechanism is simply gone, along with `_PULSE_MS`/
    # `_PULSE_STEPS`.
    'vpt_ui.py::_pulse_layer',

    # 1.6.104 — `_follow_enabled` (VPT) went dead when a plot click became "always navigate to the
    # bead" (the user asked for it, and it is safe now the click-loop is fixed — see `_on_pick` above).
    # The reveal no longer consults a follow preference, so this wrapper had no caller. The GENERIC
    # brushing path still has its own `pycat.utils.brushing._follow_enabled` for the double-click/
    # follow_selection case — this was only VPT's now-unused copy.
    'vpt_ui.py::_follow_enabled',

    # 1.5.517 — de-duplicated. These were defined TWICE, byte-identically, in file_io.py AND
    # stack_access.py. `stack_access` now owns them and `file_io` RE-EXPORTS, so every one of the
    # 25 existing `from pycat.file_io.file_io import materialize_stack` call sites still works.
    # Verified at the time and again here.
    'file_io.py::materialize_stack',
    'file_io.py::iter_frames',
    'file_io.py::layer_is_stack',
    'file_io.py::extract_2d_plane',
    'file_io.py::warn_if_assumed_axis',

    # 1.6.5 — the status-bar flicker. `_on_mouse_move` appended a `mouse_move_callbacks` handler
    # that wrote `viewer.status`. **But napari writes `viewer.status` on the same event**, so both
    # fired and whichever ran last won — the bar alternated between two strings as the mouse moved.
    #
    # **Racing napari's writer cannot be won.** The readout now wraps the layer's `get_status()`,
    # which is where napari SOURCES the string — one writer, one string, no order to depend on.
    # `_on_mouse_move` is gone because the approach it embodied was wrong.
    'coordinate_readout.py::_on_mouse_move',

    # 1.6.9 — `_ZarrTYX_generic` was DELETED, and its `__getitem__(self, idx)` went with it.
    #
    # **It was named after the wrong thing.** It is not zarr-specific: it received zarr arrays,
    # numpy arrays AND BioIO dask arrays — and the name told every reader it could rely on zarr
    # semantics it does not have. *Worse, the TZYX branch transcoded the whole file into a
    # temporary zarr before showing anything, purely so it would have a zarr to wrap.*
    #
    # `_LazyArraySource.__getitem__(self, index)` replaces it, and was verified to behave
    # IDENTICALLY on every indexing pattern napari uses on a (T, Y, X) layer: stack[t],
    # stack[t, :, :], stack[t, y0:y1, :], stack[t0:t1].
    #
    # The parameter is not lost — it is `index` rather than `idx`. **A rename, not a removal.**
    'file_io.py::__getitem__',

    # 1.6.15 — `transpose()` DELETED from `_ZarrTYX`, `_TiffPageStack` (file_io.py) and
    # `_ZarrStack` (timeseries_condensate_tools.py). All three read::
    #
    #     def transpose(self, *axes):
    #         return self.__getitem__(0)[np.newaxis]
    #
    # **Whatever axes you asked for, you got frame 0**, shaped (1, Y, X), and nothing about the
    # result looked wrong. It is the same lie `__array__` was fixed for in 1.6.3 — and it survived
    # that fix because the guard checked `__array__` and nothing else.
    #
    # **Absence is the honest implementation, and it is proven.** The three `_ImsReader*` wrappers
    # have never defined `transpose`, and one of them carries the 600-plane IMS file that scrubs at
    # 0.5% of scene. napari duck-types for the method; not having it is a path napari already takes
    # every time it touches an IMS layer.
    #
    # A caller that genuinely needs a transposed stack must say so: `materialize_stack(...)`.
    'file_io.py::transpose',
    'timeseries_condensate_tools.py::transpose',

    # 1.6.15 — RENAMED, not removed. Both were named after a LIBRARY that is no longer used, which
    # obscures which behaviour belongs to the shared structured-reader interface and which is
    # genuinely backend-specific — the exact question the whole 1.6 migration turned on.
    #
    #     extract_aicsimage_metadata        → extract_reader_metadata
    #     extract_channel_info_from_aicsimage → extract_channel_info
    #
    # Every call site was updated in the same change (4 and 4 respectively, all internal).
    # 1.6.29 — EXTRACTED, not deleted. **The cascade, again.**
    #
    # `load_into_viewer` -> `file_io/viewer_load.py`. It is what the 2-D loader, the mask loader and
    # BOTH stack loaders call once they have an array. It is a dependency of FIVE other methods, and
    # it depended on two — `_enable_auto_scale_bar` and `_tag_loaded_layer`, both extracted in the
    # previous two releases. **Taking it now unblocks the tier above it.**
    #
    # `_auto_clear_before_load` + `clear_all_without_saving` -> `file_io/session.py`, with the
    # `_clear_everything` they both call.
    #
    # `determine_file_format_and_process_data` -> `viewer_load.py`: a ten-line legacy shim that
    # touched `self` for nothing at all.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched — including
    # `batch_processor`, which calls `clear_all_without_saving(viewer, confirm=True)` with a
    # POSITIONAL viewer.)
    'file_io.py::load_into_viewer',
    'file_io.py::_auto_clear_before_load',
    'file_io.py::clear_all_without_saving',
    'file_io.py::determine_file_format_and_process_data',

    # 1.6.28 — EXTRACTED, not deleted. **The cascade.**
    #
    # `_tag_loaded_layer` + `_prompt_pixel_size_if_needed` -> `file_io/tagging.py`
    # (`_calibration_is_from_metadata` went with them — calibration provenance is a fact about the
    # LAYER, and nothing else called it).
    #
    # `_finalise_stack_load` -> `file_io/stack_load.py`. **It could not have come out before this
    # release.** It depended on FIVE methods of its host — and all five had been extracted by the
    # previous moves:
    #
    #     _enable_auto_scale_bar / _fit_view_to_layer / _add_diameter_annotation_layers -> napari_adapter
    #     _tag_loaded_layer / _prompt_pixel_size_if_needed                              -> tagging
    #
    # *Take what depends on nothing; the next layer then depends on nothing, and comes out free.*
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_tag_loaded_layer',
    'file_io.py::_prompt_pixel_size_if_needed',
    'file_io.py::_finalise_stack_load',
    'file_io.py::_calibration_is_from_metadata',

    # 1.6.27 — EXTRACTED, not deleted.
    #
    # `_clear_everything` -> `file_io/session.py`. **It is not doing I/O, it is UNDOING it** —
    # removing layers, emptying the repository, dropping cached readers and their open handles. It
    # depends on `viewer` and `central_manager` and nothing else.
    #
    # `_add_diameter_annotation_layers` -> `file_io/napari_adapter.py`. It takes **only `viewer`**
    # and creates napari layers. It was never file I/O.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_clear_everything',
    'file_io.py::_add_diameter_annotation_layers',

    # 1.6.26 — EXTRACTED to `file_io/dialogs.py`, not deleted.
    #
    # **Asking the user is not reading the file.** Two of these kept their memory on `self` —
    # `self._multipage_axis_choice` ("remember my answer this session") and `self._local_cache_files`
    # — and **neither was ever read by another method.** They were scratch variables that happened to
    # be spelled as attributes of a 3,108-line class; they are now module-level, which is what they
    # always were.
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_ask_copy_to_local',
    'file_io.py::_copy_to_local_with_progress',
    'file_io.py::_ask_multipage_axis',

    # 1.6.25 — EXTRACTED to `file_io/routing.py`, not deleted.
    #
    # **Four methods that never touched `self`.** They took `(self, file_path)` and used the `self`
    # for *nothing at all* — static functions wearing method clothes, wedged into a 3,108-line class
    # between the loaders, the dialogs and the lazy wrappers.
    #
    # They answer a question about a **path**: does this file carry real imaging metadata? did PyCAT
    # write it? does it carry an embedded tag store? is it an undeclared multipage TIFF? *No viewer,
    # no repository, no reader.*
    #
    # (`FileIOClass` keeps a delegating stub for each, so every caller is untouched.)
    'file_io.py::_file_has_imaging_metadata_safe',
    'file_io.py::_read_pycat_signifier',
    'file_io.py::_read_pycat_tags',
    'file_io.py::_tiff_multipage_undeclared',

    # 1.6.24 — EXTRACTED to `file_io/writers.py`, not deleted.
    #
    # **Writing files is not reading them, routing them, or showing them.** `_save_layer` is 243
    # lines and depended on exactly ONE thing from its 3,108-line host: `self.central_manager`.
    # `_apply_saved_tags_to_layer` depended on **nothing at all**.
    #
    # `atomic_write` moved with them — it *is* a writer concern, and leaving it behind would make
    # `writers.py` import its former host, which is a cycle. **`file_io` imports it back**, because
    # the other save paths still use it.
    #
    # (`FileIOClass` keeps a delegating stub for each method, so every caller is untouched.)
    # ...and the seven helpers NESTED INSIDE `_save_layer`, which moved with it. They are defined
    # inside the function body, so the guard tracks them as `file_io.py::<name>` — but they now
    # live in `writers.py::_save_layer`, unchanged.
    'file_io.py::_frame',
    'file_io.py::_frames',
    'file_io.py::_mask_frames',
    'file_io.py::_minimal_label_dtype',
    'file_io.py::_pycat_tag',
    'file_io.py::_to_label_array',
    'file_io.py::_to_uint16',

    'file_io.py::_save_layer',
    'file_io.py::_apply_saved_tags_to_layer',
    'file_io.py::atomic_write',

    # 1.6.60 — EXTRACTED to `file_io/readers/ims_reader.py`, not deleted (god-class decomposition
    # #3). The three lazy IMS wrapper classes (`_ImsReaderTYX`/`ZYX`/`TZYX`) and their pure helpers
    # moved out of `file_io.py`; `_open_stack_ims` is unchanged and `file_io` IMPORTS the classes +
    # `_suppress_ims_chunk_prints` + `_ims_pixel_size_um` back, so every caller is untouched.
    #
    # These six are the free functions / methods that moved with them and so no longer parse as
    # `file_io.py::<name>`:
    #   _suppress_ims_chunk_prints, _ims_indices, _ims_pixel_size_um  -> module-level in ims_reader.py
    #   _ims_frame_2d                                                 -> module-level (the classes'
    #        only caller; leaving it in file_io would have been an import cycle)
    #   _read_plane                                                   -> method of _ImsReaderZYX /
    #        _ImsReaderTZYX, now in ims_reader.py
    #   _to_float                                                     -> nested inside
    #        ims_reader.py::_ims_pixel_size_um, unchanged
    'file_io.py::_suppress_ims_chunk_prints',
    'file_io.py::_ims_indices',
    'file_io.py::_ims_pixel_size_um',
    'file_io.py::_ims_frame_2d',
    'file_io.py::_read_plane',
    'file_io.py::_to_float',

    # 1.6.24 — EXTRACTED to `file_io/napari_adapter.py`, not deleted.
    #
    # **The camera, the scale bar, and the layer-scale alignment are napari DISPLAY. They are not
    # file I/O** — they read the viewer and write the viewer, and they touch **no file, no reader,
    # no path.** They were sitting in the middle of a 3,108-line `FileIOClass` whose other 31
    # methods open, route, tag and save images.
    #
    # These four were the cleanest cut in the class: they depend on `viewer` and `central_manager`
    # and *nothing else*. They come out as plain functions with no loss, and what is left behind is
    # 237 lines smaller and one responsibility lighter.
    #
    # *The bodies did not shrink. They MOVED — and the guard's real question, "did the rationale in
    # the deleted lines survive somewhere?", is answered: `napari_adapter.py`.*
    #
    # (`FileIOClass` keeps a 3-line delegating stub for each, so every caller is untouched.)
    'file_io.py::_align_layer_scales',
    'file_io.py::_enable_auto_scale_bar',
    'file_io.py::_update_scale_bar_for_active_layer',
    'file_io.py::_fit_view_to_layer',

    # 1.6.62 — SHRUNK by extraction, not truncated (god-class decomposition #5). `_open_stack_generic`
    # went 313 → 186 lines because its metadata-read head and its per-branch lazy-wrapper construction
    # were lifted into pure modules, NOT because logic was dropped:
    #   * the head (structured reader → dims/scenes/pixel size, else the tifffile-page fallback)
    #     → `readers/stack_metadata.py::read_stack_structure`;
    #   * the four lazy branches (tifffile-fallback / time-series / z-stack / T-Z, incl. the zarr-3.2
    #     shim + multi-file OME handling) → `readers/stack_layer_builders.py`;
    #   * their shared tail (retain + contrast-pin + add_image + projection + announce) → the new
    #     `_add_lazy_stack_layer` method.
    # Every branch's behaviour — the wrappers, the retention (incl. the T-Z branch retaining nothing),
    # the contrast pinning — is preserved; the controller now orchestrates rather than inlines.
    'file_io.py::_open_stack_generic',

    'metadata_extract.py::extract_aicsimage_metadata',
    'channel_naming.py::extract_channel_info_from_aicsimage',

    # 1.6.67 — complexity-ratchet unblock (147 -> 135). These pure-Qt UI BUILDERS shrank >30%
    # because a contiguous block of widget construction / signal wiring was EXTRACTED INTO A HELPER
    # IN THE SAME FILE — not deleted. Every line survives (in `_build_*` / `_present_*` helpers);
    # same widgets, same order, same signals, zero science touched. The shrink is the move, not a
    # truncation. (The other functions split in the same pass shrank <30% and don't need an entry.)
    'frap_ui.py::_add_analysis',                                    # -> _build_fit_model
    'pipeline_snr_tools.py::_add_pipeline_snr_analysis',            # -> _build_snr_panel_widgets
    'spatial_randomness_tools.py::_add_spatial_randomness',         # -> _build_spatial_randomness_form
    'timeseries_condensate_tools.py::_add_ts_upscale_stack',        # -> _build_ts_upscale_check_ui
    'ts_cellpose_tools.py::_on_finished',                           # -> _present_transfection_filter

    # 1.6.70 — MOVED, not removed: `file_io.py` -> `lazy_sources.py`.
    #
    # `_TiffPageStack` and `_LazyArraySource` sat beside two `QDialog` subclasses in a module that
    # imports PyQt5 at module scope, so **reaching a TIFF lazy wrapper dragged in the whole GUI
    # stack** and the wrappers could not be exercised headlessly — which is precisely what a perf
    # harness or a CI perf gate needs to do. Their bodies never needed Qt; only their address did.
    #
    # The bodies moved VERBATIM. `file_io.py` re-exports both class names (plus the two OME
    # helpers), so every existing `from pycat.file_io.file_io import _TiffPageStack` caller —
    # `test_vpt_gpu_equivalence.py` does it twice — still resolves, exactly as with the five stack
    # helpers above. `tests/test_lazy_sources_headless.py` pins the new module's Qt-free contract
    # and re-checks the re-export identity.
    #
    # `_page_index` / `_get_handle` / `_read_frame` / `as_full_array` / `close` are
    # `_TiffPageStack` methods and travelled inside the class.
    'file_io.py::_page_index',
    'file_io.py::_get_handle',
    'file_io.py::_read_frame',
    'file_io.py::as_full_array',
    'file_io.py::close',
    # The OME file-set helpers are `_TiffPageStack`'s multi-file machinery and have no other
    # caller, so they moved with it — `lazy_sources` cannot import them back from `file_io`
    # (that would be a hard circular import, since `file_io` now imports `lazy_sources`).
    'file_io.py::resolve_ome_file_set',
    'file_io.py::build_ome_page_map',
}

# Qt widget plumbing. A `__init__` losing `parent`, or a callback losing an index, is a Qt idiom
# change — not a lost scientific capability. Kept separate from the list above because the risk is
# different in kind.
_QT_PLUMBING = {
    'label_and_mask_tools.py::__init__',
    'pixel_wise_corr_analysis_tools.py::__init__',
    'two_channel_coloc_tools.py::__init__',
    'two_channel_coloc_tools.py::_cb',
    'ui_utils.py::__init__',
    'file_io.py::add_image_or_mask',
    'file_io.py::open_image_auto',
    'file_io.py::_file_has_imaging_metadata',
}

_ALLOWED = _DELIBERATE | _QT_PLUMBING


def _current_signatures():
    found = {}
    for path in (_ROOT / "src" / "pycat").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            found[f"{path.name}::{node.name}"] = dict(
                lines=(node.end_lineno or node.lineno) - node.lineno,
                params=set(a.arg for a in node.args.args + node.args.kwonlyargs))
    return found


@pytest.mark.core
def test_no_SCIENTIFIC_PARAMETER_has_been_dropped():
    """**A lost parameter is a lost capability, not a refactor.**

    ``punctate_gate`` disappearing from ``segment_subcellular_objects`` is the difference between
    *"this cell is empty"* and *"this cell's noise has been stretched to look like signal."*
    """
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing — run tools/check_for_dropped_code.py to build it")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    dropped = []
    for key, best in high_water.items():
        if key in _ALLOWED or key not in current:
            continue

        lost = set(best['params']) - current[key]['params']
        if lost:
            dropped.append(f"{key}  LOST: {sorted(lost)}")

    assert not dropped, (
        "these functions have LOST PARAMETERS they once had:\n  "
        + "\n  ".join(sorted(dropped))
        + "\n\n**A lost parameter is a lost CAPABILITY.** The code still compiles and the tests "
          "still pass — that is exactly how `punctate_gate` disappeared and spurious puncta came "
          "back.\n\n"
          "If the removal was deliberate, add the key to `_DELIBERATE` **with a reason**."
    )


@pytest.mark.core
def test_no_FUNCTION_has_vanished():
    """A function that was there and is not is either a **deliberate move** or a **truncated
    rewrite**. *The guard cannot tell which, and should not try — it asks.*"""
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    vanished = sorted(k for k in set(high_water) - set(current)
                      if k not in _ALLOWED and not k.split('::')[1].startswith('__'))

    assert not vanished, (
        "these functions existed once and do not now:\n  " + "\n  ".join(vanished)
        + "\n\nIf a function was MOVED, does the old import still work? (That is what happened to "
          "the five stack helpers — `file_io` re-exports them.) Add it to `_DELIBERATE` **with a "
          "reason**."
    )


@pytest.mark.core
def test_no_FUNCTION_BODY_has_been_truncated():
    """**The signature of a truncated rewrite:** the function survives, its parameters survive, and
    its **body is a third shorter.**

    ``cell_mask_stretching`` went from **146 lines to 85** and lost its gain ceiling — *and its
    signature still had two of its four parameters, so a signature check alone would have missed
    it.*
    """
    if not _MARK.exists():
        pytest.skip(f"{_MARK} is missing")

    high_water = json.loads(_MARK.read_text(encoding='utf-8'))
    current = _current_signatures()

    truncated = []
    for key, best in high_water.items():
        if key in _ALLOWED or key not in current:
            continue

        was, now = best['lines'], current[key]['lines']
        if was >= 25 and now < was * _SHRINK_THRESHOLD:
            truncated.append(f"{key}:  {was} -> {now} lines  (-{100 * (was - now) // was}%)")

    assert not truncated, (
        "these function bodies have SHRUNK by more than 30%:\n  " + "\n  ".join(sorted(truncated))
        + "\n\nThat is the signature of a rewrite that dropped code. **Did the rationale in the "
          "deleted lines survive somewhere?** If the shrink was deliberate, add the key to "
          "`_DELIBERATE` **with a reason**."
    )
