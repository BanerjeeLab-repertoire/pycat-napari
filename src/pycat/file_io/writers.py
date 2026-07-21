"""
**Writing files. Not reading them, not routing them, not showing them.**

``_save_layer`` is **243 lines** and depended on exactly one thing from its 3,108-line host class:
``self.central_manager``. ``_apply_saved_tags_to_layer`` depended on **nothing at all.** Neither
had any business living beside the loaders, the router, the dialogs and the lazy wrappers.

``atomic_write`` comes with them, because it *is* a writer concern — and because leaving it in
``file_io`` would make this module import its former host, which is a cycle.

── What this preserves ──────────────────────────────────────────────────────────────────

**A half-written file that opens is worse than no file at all.** Every save writes to a sibling
temp and ``os.replace``s on success — atomic on Windows and POSIX — so an interrupted export leaves
the destination *untouched* rather than a truncated TIFF that opens perfectly and shows however
many frames got written.

*And the temp name keeps the real extension* (``result.partial-a3f1.png``, not
``result.png.partial``): ``skimage.io.imsave`` picks its format **from the extension**, and given an
unknown one it silently falls back to **TIFF** — producing a file that is a TIFF called ``.png``,
round-trips fine inside PyCAT, and is mislabelled for everyone else.
"""

from __future__ import annotations

import contextlib
import os

import numpy as np
import skimage as sk

from pycat.utils.general_utils import debug_log, dtype_conversion_func


@contextlib.contextmanager
def atomic_write(final_path):
    """**A half-written file that opens is worse than no file at all.**

    Every save in PyCAT wrote **straight to the destination.** Interrupt it — a crash, a full disk,
    a network share that blinks, a user closing the app on a 600-frame export — and what is left on
    disk is a **truncated TIFF that opens perfectly**, showing however many frames got written,
    with no indication that the rest are missing.

    *A file that fails to open announces itself. A file that opens short does not.* The scientist
    analyses 340 frames of a 600-frame acquisition and nothing anywhere says so.

    So: write to a sibling temp file, and **rename only on success.** ``os.replace`` is atomic on
    both Windows and POSIX — the destination either holds the old file or the complete new one, and
    never a partial one. If the write raises, the temp file is removed and **the destination is
    left untouched**, which is the other half of the guarantee: a failed save does not destroy the
    previous good save.

    Usage::

        with atomic_write(out_path) as tmp:
            tifffile.imwrite(tmp, frames, ...)
    """
    final_path = str(final_path)

    # ── The temp name MUST keep the real extension ──────────────────────────────────────
    #
    # The obvious name is ``result.png.partial``. **It silently corrupts the output.**
    #
    # ``skimage.io.imsave`` picks its format **from the extension**. Hand it ``.partial`` and it
    # does not fail — it falls back to **TIFF**, writes ``II*\x00``, and returns cleanly. Rename
    # that to ``.png`` and the file on disk is *a TIFF called .png*. It round-trips through skimage
    # (which sniffs content on read) and so looks fine from inside PyCAT — while **every other
    # tool**, and every collaborator, gets a mislabelled file.
    #
    # *This was caught by checking the magic bytes, not by checking that the save "worked".*
    #
    # So the suffix goes **before** the extension: ``result.partial-a3f1.png``. The writer sees
    # ``.png`` and writes a PNG.
    root, ext = os.path.splitext(final_path)
    tmp_path = f"{root}.partial-{os.getpid():x}{ext}"

    # A leftover temp from a previous crash is worthless by definition — nothing ever reads one.
    try:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except OSError:
        pass

    try:
        yield tmp_path
    except BaseException:
        # Includes KeyboardInterrupt and SystemExit — an interrupted save is exactly the case this
        # exists for, and it must not leave the temp file behind either.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

    # Atomic on Windows and POSIX. The destination is the old file or the new one, never a mix.
    os.replace(tmp_path, final_path)


def _save_layer(central_manager, data, layer_type: str, save_name: str, safe_name: str,
                tag_store=None):
    """
    Save a layer to disk, handling zarr-backed lazy stacks, regular
    numpy arrays, and label/shape layers.

    For 3D stacks (T, H, W) — whether backed by zarr, numpy, or any
    other lazy array — frames are written one at a time as a multi-page
    TIFF so the full stack is never held in RAM simultaneously.  This
    is essential for 600-frame 2048×2048 stacks that would otherwise
    require ~5 GB of RAM just for the save operation.

    tag_store : optional dict — the layer's pycat_tags store
    ({'tags':[...], 'edges':[...]}). Embedded in the TIFF description JSON
    alongside the image/mask signifier so tags survive save→reload.

    Naming convention
    -----------------
    2D image          → <save_name>_<layer>.tiff
    3D image stack    → <save_name>_<layer>_stack.tiff   (multi-page)
    Labels (2D)       → <save_name>_<layer>.png
    Labels (3D stack) → <save_name>_<layer>_masks.tiff  (multi-page)
    """
    import tifffile
    import json as _json

    # PyCAT signifier: a small JSON tag embedded in the TIFF ImageDescription
    # so a saved file can be re-loaded with its type known exactly (image vs
    # label mask) instead of guessing from pixel statistics. Read back by
    # add_image_or_mask / _read_pycat_signifier on load. The layer's tag store
    # (role/modality/lineage/etc.) rides in the same blob so tags persist.
    def _pycat_tag(kind):
        try:
            from pycat import __version__ as _ver
        except Exception:
            _ver = 'unknown'
        blob = {'pycat': True, 'kind': kind, 'pycat_version': _ver}
        if tag_store:
            try:
                blob['pycat_tags'] = {
                    'tags': list(tag_store.get('tags', [])),
                    'edges': list(tag_store.get('edges', [])),
                }
            except Exception:
                pass
        return _json.dumps(blob, default=str)

    is_lazy = hasattr(data, '_z') or hasattr(data, 'store')  # _ZarrStack or zarr.Array

    # Materialise only what we need
    def _frame(t):
        f = data[t]
        return np.asarray(f).astype(np.float32) if layer_type == 'Image' else np.asarray(f)

    def _minimal_label_dtype(arr):
        """Smallest lossless integer dtype for a label/mask array.

        Everything used to be force-cast to uint16, which wastes headroom: a
        BINARY mask needs 1 bit and a 40-cell label mask needs 6. Compression
        hides most of that (the high byte is all zeros), but not all of it —
        measured ~1.3x on masks — and it is free to just not waste it.
        """
        a = np.asarray(arr)
        try:
            mx = int(a.max()) if a.size else 0
        except Exception:
            return np.uint16
        if mx <= 1:
            return np.uint8       # binary mask (TIFF has no 1-bit path here)
        if mx <= 255:
            return np.uint8       # up to 255 labels
        if mx <= 65535:
            return np.uint16
        return np.uint32          # >65k objects: don't silently wrap!

    def _to_label_array(arr):
        return np.asarray(arr).astype(_minimal_label_dtype(arr))

    def _to_uint16(arr):
        """Convert an IMAGE for saving without inventing precision.

        The previous version rescaled anything with max<=1.0 by 65535 and
        min-max stretched anything above 65535 — i.e. it CHANGED THE PIXEL
        VALUES, fabricating 16-bit precision for 8-bit data and silently
        renormalising floats. That is a correctness problem, not just a size
        one. Now: integer data is preserved as-is in the smallest lossless
        integer type, and float data is only converted when it is safe to do
        so (float images that are genuinely outside integer range keep their
        float dtype, handled by the caller).
        """
        a = np.asarray(arr)
        if np.issubdtype(a.dtype, np.integer):
            mx = int(a.max()) if a.size else 0
            if mx <= 255:
                return a.astype(np.uint8)      # don't upcast 8-bit sources
            if mx <= 65535:
                return a.astype(np.uint16)
            return a.astype(np.uint32)
        # Floating point: preserve values; only narrow when lossless.
        af = a.astype(np.float32)
        finite = af[np.isfinite(af)] if af.size else af
        if finite.size and float(np.min(finite)) >= 0:
            mx = float(np.max(finite))
            # Integral-valued floats (e.g. a mask or a counted image) can be
            # stored exactly as ints.
            if np.all(finite == np.rint(finite)):
                if mx <= 255:
                    return af.astype(np.uint8)
                if mx <= 65535:
                    return af.astype(np.uint16)
        # Genuine continuous float data — keep float32 rather than quantising
        # it (quantising is lossy and the old code did it silently).
        return af

    if layer_type in ('Labels',):
        if hasattr(data, 'shape') and len(data.shape) == 3:
            # 3D label stack (e.g. TS Cell Masks) → compressed multi-page TIFF.
            #
            # Masks are the bulk of a PyCAT project's disk usage and they
            # compress enormously (a 1024² uint16 label mask: 2.1 MB raw →
            # 13 kB zlib, ~160×, lossless, for ~7 ms). The stack MUST be
            # written in one imwrite call with the axis DECLARED:
            #   * per-frame writes with compression lose the series structure
            #     (imread collapses the stack to a single plane);
            #   * a whole-stack write without `axes` metadata produces an
            #     UNDECLARED 'Q' axis — the very case that makes PyCAT prompt
            #     "is this T or Z?" when reopening its own file (see 1.5.351).
            out_path = f"{save_name}_{safe_name}_masks.tiff"
            _axes = 'TYX'
            try:
                _dr = central_manager.active_data_class.data_repository
                _lbl = str(_dr.get('stack_axis_label') or 'T').upper()
                _axes = 'ZYX' if _lbl.startswith('Z') else 'TYX'
            except Exception:
                pass
            _n, _h, _w = (int(data.shape[0]), int(data.shape[1]),
                          int(data.shape[2]))
            # Right-size the label dtype from the GLOBAL max across frames —
            # deciding from frame 0 alone would silently WRAP labels if a later
            # frame has more objects (e.g. 300 cells in frame 40 vs 200 in
            # frame 0). One streaming pass over max() is cheap next to the I/O.
            _gmax = 0
            for t in range(_n):
                try:
                    _gmax = max(_gmax, int(np.asarray(data[t]).max()))
                except Exception:
                    _gmax = 65535
                    break
            _dt = (np.uint8 if _gmax <= 255
                   else np.uint16 if _gmax <= 65535 else np.uint32)

            def _mask_frames():
                for t in range(_n):
                    yield np.asarray(data[t]).astype(_dt)

            with atomic_write(out_path) as _tmp:
                tifffile.imwrite(
                    _tmp, _mask_frames(),
                    shape=(_n, _h, _w), dtype=_dt,
                    compression='zlib',
                    metadata={'axes': _axes},
                    description=_pycat_tag('mask'),
                    bigtiff=True)
            print(f"[PyCAT] Saved 3D label stack → {out_path} "
                  f"(compressed, axes={_axes}, {np.dtype(_dt).name}, "
                  f"max label {_gmax})")
        else:
            # 2D label mask → PNG (already compressed), right-sized: a binary
            # mask or a <256-object label image is uint8, not uint16.
            arr = _to_label_array(data)
            out_path = f"{save_name}_{safe_name}.png"
            with atomic_write(out_path) as _tmp:
                sk.io.imsave(_tmp, arr)

    elif layer_type == 'Shapes':
        arr = dtype_conversion_func(np.asarray(data), 'uint16')
        with atomic_write(f"{save_name}_{safe_name}.png") as _tmp:
            sk.io.imsave(_tmp, arr)

    elif layer_type == 'Image':
        ndim = data.shape[0] if hasattr(data, 'shape') else len(data)
        # Check if this is a (T, H, W) stack
        shape = data.shape if hasattr(data, 'shape') else None

        if shape is not None and len(shape) == 3 and not (
            shape[2] in (3, 4) and shape[0] < 10
        ):
            # 3D grayscale stack — compressed multi-page TIFF with the axis
            # DECLARED (see the label-stack note above: per-frame compressed
            # writes lose the series, and an undeclared axis reopens as 'Q').
            # Images compress far less than masks (typically 1.3–2×, since
            # they carry real noise), but it is free correctness and still a
            # saving; the mask paths are where the big win is.
            n_t = shape[0]
            out_path = f"{save_name}_{safe_name}_stack.tiff"
            print(f"[PyCAT] Saving {n_t}-frame stack to {out_path} …")
            _axes = 'TYX'
            try:
                _dr = central_manager.active_data_class.data_repository
                _lbl = str(_dr.get('stack_axis_label') or 'T').upper()
                _axes = 'ZYX' if _lbl.startswith('Z') else 'TYX'
            except Exception:
                pass
            # Stream the frames: imwrite accepts a generator with shape=/dtype=,
            # so we get compression + a declared axis WITHOUT materialising the
            # whole movie in RAM (the original per-frame writer streamed too).
            # The dtype is decided from the FIRST frame (right-sized, never
            # upcast) and used for the whole stack.
            _h, _w = int(shape[1]), int(shape[2])
            _probe = _to_uint16(_frame(0))
            _dt = _probe.dtype

            def _frames():
                yield _probe
                for t in range(1, n_t):
                    yield _to_uint16(_frame(t)).astype(_dt, copy=False)

            with atomic_write(out_path) as _tmp:
                tifffile.imwrite(
                    _tmp, _frames(),
                    shape=(n_t, _h, _w), dtype=_dt,
                    compression='zlib',
                    metadata={'axes': _axes},
                    description=_pycat_tag('image'),
                    bigtiff=True)
            print(f"[PyCAT] Saved stack → {out_path} "
                  f"(compressed, axes={_axes}, {_dt})")
        else:
            # 2D image or RGB
            arr = np.asarray(data)
            if arr.ndim == 2:
                out_path = f"{save_name}_{safe_name}.tiff"
                with atomic_write(out_path) as _tmp:
                    tifffile.imwrite(_tmp, _to_uint16(arr),
                                     compression='zlib',
                                     description=_pycat_tag('image'))
            else:
                out_path = f"{save_name}_{safe_name}.png"
                with atomic_write(out_path) as _tmp:
                    sk.io.imsave(_tmp, dtype_conversion_func(arr, 'uint8'))
    else:
        # Unknown — save raw (compressed: .npz costs nothing vs .npy)
        with atomic_write(f"{save_name}_{safe_name}.npz") as _tmp:
            np.savez_compressed(_tmp, data=np.asarray(data))

def _write_session_manifest(session_dir, central_manager, source_path,
                            manifest_layers, manifest_dfs, stem):
    """Write the restore manifest, recording the OPEN ANALYSIS METHOD alongside the data.

    The source image is REFERENCED by path, never copied. ``active_method`` — the UI class currently
    open — is what lets Load Session reopen that method and rebuild its view (plots/tables), instead of
    dropping the restored dataframes into an empty panel.
    """
    from pycat.file_io import session_manifest as _sm
    _cur = getattr(getattr(central_manager, 'analysis_methods_ui', None),
                   'current_analysis_ui', None)
    active_method = _cur.__class__.__name__ if _cur is not None else None

    _extra = {'stem': stem, 'active_method': active_method}
    # Comparative-phenotyping condition/metadata (increment 1, Part C): if the user tagged images
    # in-app, those tags live in the data repository — carry them into the manifest so they travel
    # with the session. Absent → `_extra` is unchanged, so an untagged session's manifest is
    # byte-identical to before (back-compat both ways).
    try:
        from pycat.utils.sample_metadata import tags_to_manifest_extra
        _tags = central_manager.active_data_class.data_repository.get('sample_metadata')
        if _tags:
            _extra.update(tags_to_manifest_extra(_tags))
    except Exception as _sme:
        from pycat.utils.general_utils import debug_log
        debug_log('writers: could not attach sample_metadata to the manifest', _sme)

    # User-entered workflow parameters (session_persist_settings Part 2): the batch processor already
    # records each step's params as the ONE parameter record — carry that same record into the manifest
    # so a reloaded session reproduces the analysis the user set up. No recorded steps → no `workflow`
    # block, so an un-recorded session's manifest is byte-identical to before.
    try:
        _bp = getattr(central_manager, '_pycat_batch_processor', None)
        if _bp is not None:
            _extra.update(_sm.workflow_to_manifest_extra(getattr(_bp, 'config', None)))
    except Exception as _wf:  # broad-ok: persisting the workflow is best-effort; never fail a save over it
        from pycat.utils.general_utils import debug_log
        debug_log('writers: could not attach the recorded workflow to the manifest', _wf)

    _sm.write_manifest(
        session_dir, source_path=source_path,
        data_repository=central_manager.active_data_class.data_repository,
        layer_entries=manifest_layers, dataframe_entries=manifest_dfs,
        extra=_extra)
    print(f"[PyCAT] Session saved to {session_dir}")


def write_session_outputs(central_manager, layers_by_name, selected_layers,
                          selected_dataframes, dataframes, file_metadata,
                          save_name, session_dir, source_path, stem):
    """Write a session's output files — the pure, Qt-free half of Save & Clear.

    Takes already-decided inputs (which layers/dataframes, the final in-session
    ``save_name``, the created ``session_dir``) and does the actual disk writes:

    * each selected layer → :func:`_save_layer` (right-sized, atomic),
    * each selected dataframe → an ``_<key>.csv`` via :func:`atomic_write`
      (a truncated CSV is the worst failure — it opens, parses, and is short),
    * the acquisition ``file_metadata`` → ``_metadata.json`` (provenance),
    * the session manifest → ``pycat_session.json`` (what makes Load Session work;
      the source image is REFERENCED by path, never copied).

    It does NOT touch the viewer, dialogs, clearing, or the batch recorder — that
    orchestration stays in the controller (``save_and_clear_all``).

    Parameters
    ----------
    central_manager
        The controller's central manager (used only to resolve the stack axis
        label inside ``_save_layer`` and the data repository for the manifest).
    layers_by_name : dict
        ``{layer.name: layer}`` for every layer currently in the viewer.
    selected_layers, selected_dataframes : iterable of str
        The names the user chose to save.
    dataframes : dict
        ``{key: DataFrame}`` of all available dataframes (order preserved).
    file_metadata : dict or None
        Normalised acquisition metadata to export alongside the results.
    save_name : str
        The FINAL in-session path stem (already inside ``session_dir``).
    session_dir : pathlib.Path or None
        The created session folder; if ``None`` the manifest is skipped.
    source_path : str or None
        Path to the source image (referenced by the manifest, never copied).
    stem : str
        Base filename stem recorded in the manifest ``extra``.

    Returns
    -------
    dict
        ``{'manifest_layers': [...], 'manifest_dfs': [...]}`` — the manifest
        entries written, for logging and tests.
    """
    import json as _json
    import warnings

    from pycat.file_io import session_manifest as _sm

    manifest_layers = []
    manifest_dfs = []

    # Suppress specific skimage warnings (wraps the skimage save warnings).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=UserWarning)

        # Save only the selected layers based on their names
        for layer_name in selected_layers:
            if layer_name in layers_by_name:
                layer = layers_by_name[layer_name]
                layer_data = layer.data
                layer_type = type(layer).__name__
                safe_name = layer_name.replace(' ', '_').lower()
                # Pull the layer's tag store so tags persist through the save.
                _tag_store = None
                try:
                    _md = getattr(layer, 'metadata', None)
                    if isinstance(_md, dict):
                        _tag_store = _md.get('pycat_tags')
                except Exception:
                    _tag_store = None
                _save_layer(central_manager, layer_data, layer_type,
                            save_name, safe_name, tag_store=_tag_store)
                manifest_layers.append({
                    'name': layer_name, 'layer_type': layer_type,
                    'safe_name': safe_name})

        # Save only the selected dataframes
        for df_name, df_value in dataframes.items():
            if df_name in selected_dataframes:
                # A truncated CSV is the worst of all: it opens, it parses, and it is short.
                # Nothing about 340 rows of an 800-row results table looks wrong.
                with atomic_write(save_name + f'_{df_name}.csv') as _tmp:
                    # The user's results, not PyCAT's bookkeeping: the `_pycat_*` identity
                    # columns are dropped on the way out. Nothing reads them back from a CSV —
                    # session restore goes through the manifest.
                    from pycat.utils.entity_ref import without_identity
                    without_identity(df_value).to_csv(_tmp, index=True)
                manifest_dfs.append({
                    'key': df_name,
                    'file': os.path.basename(save_name + f'_{df_name}.csv')})

        # Export the file's normalised acquisition metadata alongside the
        # results, for provenance/reproducibility. Written once per save.
        try:
            if file_metadata:
                with open(save_name + '_metadata.json', 'w', encoding='utf-8') as _mf:
                    _json.dump(file_metadata, _mf, indent=2, default=str)
        except Exception as _mde:
            debug_log("file_io: metadata JSON export failed", _mde)

        # Write the session manifest so Load Session can restore this state.
        try:
            if session_dir is not None:
                _write_session_manifest(session_dir, central_manager, source_path,
                                        manifest_layers, manifest_dfs, stem)
        except Exception as _me:
            debug_log("file_io: session manifest write failed", _me)

    return {'manifest_layers': manifest_layers, 'manifest_dfs': manifest_dfs}


def _apply_saved_tags_to_layer(layer, tag_store):
    """Re-apply a saved tag store to a freshly-loaded layer via the tag
    engine, preserving each tag's original source/confidence. Edges are
    restored as-is (their targets are tag-ids that resolve once all layers
    of a session are loaded)."""
    if not tag_store or layer is None:
        return
    try:
        from pycat.utils import layer_tags as _LT
        for t in tag_store.get('tags', []):
            try:
                _LT.tag_layer(layer, t.get('key'), t.get('value'),
                              source=t.get('source', 'inferred'),
                              confidence=t.get('confidence'),
                              overwrite=True)
            except Exception:
                pass
        # Restore edges directly into the canonical store.
        md = getattr(layer, 'metadata', None)
        if isinstance(md, dict):
            store = md.setdefault('pycat_tags', {'tags': [], 'edges': []})
            store.setdefault('edges', [])
            for e in tag_store.get('edges', []):
                if e not in store['edges']:
                    store['edges'].append(e)
    except Exception as _e:
        debug_log("file_io: applying saved tags failed", _e)
