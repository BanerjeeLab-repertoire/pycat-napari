# Session save/load architecture + interop-extension recipe

**Date:** 2026-07-15
**Shipped in:** 1.6.52 (session redesign) + 1.6.53 (fallback scanner VPT support)
**Purpose:** capture the session architecture so extending interoperability to other methods
(FCS, FCCS, FLIM-phasor, RICS/STICS, SMLM, …) is a short checklist, not a re-derivation.

---

## TL;DR

The session save/load infrastructure is **fully general and method-agnostic**. Adding a new method
to it is small and mechanical. There is exactly **one** kind of thing a new method may need to add:
a *layer-rebuild hook*, and only if that method's layers are **derived from a DataFrame** rather than
saved as an image/label file. Everything else (folder consolidation, manifest, source-as-reference,
smart save defaults, manifest-first load, acquisition-state restore, dataframe restore) is shared and
requires **no** per-method work.

---

## What a "session" is

A session is one folder next to the data:

```
session_<image-stem>_<timestamp>/
  pycat_session.json          ← the MANIFEST (source of truth)
  <stem>_<layer>.tiff/.png    ← derived image/label layers (real files)
  <stem>_<dfkey>.csv          ← analysis dataframes
  <stem>_metadata.json        ← normalised acquisition metadata (provenance)
```

The **source image is NOT in the folder** — the manifest references it by path. It is already on
disk and is the largest file; a session points at it rather than copying it.

`pycat_session.json` records:
- `source_image.path` (+ an `exists` check) — the referenced, uncopied original.
- `acquisition` — `microns_per_pixel_sq`, `pixel_size_from_metadata`, `pixel_size_confirmed`,
  `frame_interval_s`. Restored on load so downstream analysis is calibrated exactly as it was.
- `layers` — `[{name, layer_type, safe_name}, …]` for each saved derived layer.
- `dataframes` — `[{key, file}, …]` mapping each repository DataFrame key to its CSV file.

---

## The general machinery (do NOT re-touch per method)

| concern | where | notes |
|---|---|---|
| module | `file_io/session_manifest.py` | manifest read/write, default selection, source-image identification, dataframe restore |
| save flow | `file_io.py::save_and_clear_all` | creates the session folder, writes layers+dataframes into it, writes the manifest. Runs for **every** save, no method gating. |
| smart save defaults | `file_io.py::LayerDataframeSelectionDialog.__init__` | pre-ticks every DERIVED layer + ALL dataframes; unticks the source image and reconstructable upscales. Method-agnostic. |
| load flow | `session_loader.py::load_session` | **manifest-first**: opens the referenced source image via `_load_source_image_into_viewer` (PyCAT's own loader → correct lazy type + scale), restores acquisition state, restores every recorded dataframe. Falls back to the suffix scan for manifest-less (older) folders. |
| load UI + stem picker | `ui_modules.py::_open_session_loader` | groups files by image stem so a mixed folder lets the user choose which image to load. |
| fallback classification | `session_loader.py::_BATCH_RULES` + `classify_file` | suffix → (layer_type, df_key). Older loose folders (no manifest) still load. |

### The source-image identification heuristic

`session_manifest._is_source_image_layer(layer, source_stem)` decides which layer is the original
(so it is excluded from the save and referenced instead). It is **best-effort**: it matches the
loaded-image name pattern / tags and excludes names carrying derived markers
(`pre-processed`, `enhanced`, `background`, `upscaled`, `overlay`, `picked`). If a future method
names its source-derived layers in a way that trips this, tighten the heuristic there — it is the
one place that judgement lives.

---

## The ONE method-specific seam: derived-from-DataFrame layers

Two ways a method's *layers* get persisted:

1. **Saved as image/label files** (masks, processed images, label maps). These are written as real
   `.tiff`/`.png` by `writers._save_layer` and **reload directly** through the general load path.
   **No per-method work.** This covers cell / puncta / condensate / brightfield / most methods.

2. **Derived from a DataFrame** (napari **Tracks**, and any future **correlation/lifetime map** that
   is stored as a DataFrame rather than an image). The layer cannot be reloaded from a file because
   it was never saved as one — it must be **rebuilt from its DataFrame** on load. This is the only
   case that needs a per-method hook.

VPT is currently the sole case-2 method. Its hook:

- **rebuild method:** `vpt_ui.py::VideoParticleTrackingUI._rebuild_track_layers(tracks_df)` — builds
  the Tracks layer + the pickable Points layer from the `vpt_tracks` DataFrame. Shared by the linker
  *and* the loader, so a loaded session gets identical brushable layers to a fresh run.
- **load-time trigger:** `ui_modules.py::_open_session_loader`, after `load_session` returns:

  ```python
  if 'vpt_tracks' in result["loaded_dfs"]:
      _vpt = getattr(self, 'current_analysis_ui', None)
      if _vpt is not None and hasattr(_vpt, '_rebuild_track_layers'):
          _vpt._rebuild_track_layers(result["loaded_dfs"]['vpt_tracks'])
  ```

  `current_analysis_ui` is the active analysis widget (set in `ui_modules._switch_analysis`), so the
  hook only fires when that method's panel is open.

---

## Recipe: add a new method to session interop (worked example: FCS)

Assume FCS produces (a) a source image/stream, (b) a per-ROI correlation-curve **DataFrame**
`fcs_curves`, and (c) a fitted-parameters DataFrame `fcs_fits`. Suppose the correlation-map overlay
is drawn from `fcs_curves` (case 2), while any masks are saved as label files (case 1 — nothing to do).

1. **Save** — automatic. `save_and_clear_all` already writes every repository DataFrame
   (`get_dataframes()` picks up any `pd.DataFrame` in `data_repository`) into the session folder and
   records it in the manifest. So `data_repository['fcs_curves']` and `['fcs_fits']` are saved with
   **zero** new code, and pre-ticked by the smart defaults.

2. **Manifest load** — automatic. `load_session` restores every dataframe in the manifest into the
   repository. `fcs_curves`/`fcs_fits` come back with **zero** new code.

3. **Fallback classification (older/loose folders)** — add the suffixes to `_BATCH_RULES` in
   `session_loader.py`, most-specific first (so a longer suffix isn't shadowed):

   ```python
   ('_fcs_fits',   'dataframe', 'fcs_fits'),
   ('_fcs_curves', 'dataframe', 'fcs_curves'),
   ```

   (Batch-rule dataframes already carry `df_key` since 1.6.53, so the restored table lands under the
   correct repository key.)

4. **Layer rebuild (only if a layer is derived-from-DataFrame)** —
   a. add a `_rebuild_<method>_layers(df)` method to the FCS UI that reconstructs the overlay from
      `fcs_curves` (mirror `_rebuild_track_layers`; share it between the compute path and the loader).
   b. add a trigger in `_open_session_loader` next to the VPT one:

      ```python
      if 'fcs_curves' in result["loaded_dfs"]:
          _ui = getattr(self, 'current_analysis_ui', None)
          if _ui is not None and hasattr(_ui, '_rebuild_fcs_layers'):
              _ui._rebuild_fcs_layers(result["loaded_dfs"]['fcs_curves'])
      ```

   If FCS has **no** derived-from-DataFrame layer (its outputs are masks + tables + plots), skip
   step 4 entirely — save/load already works.

That is the whole extension. Steps 1–2 are free; step 3 is a two-line list entry; step 4 exists only
for the Tracks-like case.

---

## Design decisions worth not re-litigating

- **Source referenced, never copied.** It is on disk and is the biggest file. A session is a pointer
  + the derived state, not a duplicate of the raw data.
- **Smart defaults over user curation.** PyCAT knows what a session needs (derived layers + all
  dataframes); the user unticks/adds only to override. The old per-layer/per-dataframe ticklist made
  the user do PyCAT's job.
- **One consolidated folder.** Flat prefixes scattered artifacts among the user's data; a session is
  a unit and lives in its own folder.
- **Manifest-first, suffix-scan fallback.** New saves are robust (explicit mapping); old loose
  folders still load (best-effort classification). Don't remove the fallback — it is how
  pre-1.6.52 saves reload.
- **Plots/tables are not persisted as objects.** They are regenerated from the dataframes (e.g. click
  "Compute MSD & Viscosity" after a VPT load). The DataFrame is the source of truth; the view is
  derived. Any new method should follow the same rule — persist the data, regenerate the view.

---

## Known limitations (current, honest)

- **Loose (manifest-less) folders don't restore the source image** — the manifest is what holds the
  source path. For old folders, load the image normally first, then Load Session for the derived
  state. New saves don't have this limitation.
- **Source-image heuristic is name/tag based** — see the identification note above; tighten in
  `_is_source_image_layer` if a method's naming trips it.
- **VPT is the only derived-from-DataFrame method today** — the hook pattern is proven on it; the
  recipe above generalises it.
