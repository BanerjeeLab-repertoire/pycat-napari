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

    'metadata_extract.py::extract_aicsimage_metadata',
    'channel_naming.py::extract_channel_info_from_aicsimage',
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
