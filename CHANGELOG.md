## [1.6.92] - 2026-07-17
### Changed — **`set_data` now WARNS-and-STORES on a type mismatch (resolving the flagged decision).**
The reorder in 1.6.91 fixed the new-key `KeyError` but deliberately *pinned* the type-mismatch branch
as reject-and-keep-old, flagging the store-vs-reject choice as a separate decision. This resolves it
in the setter direction: on a type change `set_data` now warns (advisory) and still stores the new
value. The old reject was its own silent failure — a key seeded as `int` (`microns_per_pixel_sq`,
`object_size`) rejected a legitimate `float` update, keeping the stale int while the caller believed
it had updated. `set_data` is a setter; it always sets. Tests updated: a mismatch warns-and-stores; an
int-seeded key accepts a float update.

### Docs — **README tail cleanup: current install, correct test command, no stale literal, followable dev setup.**
- `[dev]`/extras install now carries a ⚠️ warning that a bare `pip install "pycat-napari[dev]"` can
  silently backtrack to an old 1.5.x resolution; steer to install-first-then-extras or the editable
  install.
- Blank Development step 2 filled with real env-creation commands; env name unified to `pycat-env`
  throughout (the outlier `pycat-16`/`pycat-napari-env` names removed).
- Wrong test command `pytest --cov=pycat_napari tests/` → `pytest` (the package is `pycat`; coverage
  is already configured in pyproject addopts).
- Stale `Current Version: 1.5.357` literal → point to PyPI / `pip show pycat-napari`.

## [1.6.91] - 2026-07-17
### Fixed — **`set_data` crashed on any genuinely new key.**
`BaseDataClass.set_data` read the stored value's class (`self.data_repository[key].__class__`) BEFORE
checking whether the key existed. `data_repository` is a plain dict, so a new key raised `KeyError`
on the first line, before the "new key" branch could run. Masked only because the repository is
pre-seeded with the common keys. Reordered so existence is checked first.
- **The type-mismatch branch is UNCHANGED on purpose.** The audit spec asked to store-anyway there,
  citing "current store-anyway behaviour" — but the current behaviour *rejects* (warns and keeps the
  old value), so storing would be a semantic change, not a preservation. Left as a deliberate,
  separate decision (see the note), pinned so it is chosen rather than drifted into.
### Fixed — **"Best frame" could be the sharpest speck of DUST, not the sample (science).**
Every whole-frame focus/quality metric — Brenner, Tenengrad, normalised variance, Laplacian variance,
gradient energy — scores sharpness by a plain `mean`/`var` of a per-pixel magnitude. That aggregate
is dominated by its largest values, so a **bright speck of debris on a different focal plane** (dust
has its own focus curve) can, at *its* focus frame, contribute a handful of extreme-gradient pixels
that outscore a genuinely in-focus but spatially-extended sample. The argmax lands on the junk frame,
and nothing says so. Documented in `bf_focus_metric`'s own docstring; reproduced here on a synthetic
z-sweep (plain metric picks the debris frame, robust metric picks the sample).
- `math_utils.robust_focus_energy` — the maskless defence: drop the top ~1% of per-pixel
  contributions before averaging. **Spatial extent is the discriminator, not magnitude** — a real
  in-focus object lights up *many* pixels (99% survive the trim); a speck lights up *few* (all
  trimmed). On clean data, trimming 1% of a smooth distribution does **not move the chosen frame**.
- Wired into `bf_focus_metric` (maskless path), the three metrics in `bf_analyse_frame_quality`, and
  `analyse_frame_quality`'s Laplacian variance + gradient energy. Entropy is left alone (histogram-
  based, a speck barely moves it). Verified: on a debris z-sweep the real functions now score the
  sample sharpest; on a clean sweep the chosen frame is unchanged; the masked `bf_focus_metric` path
  is exact. Mutation-checked — disabling the trim turns the debris tests red.
### Notes — where the audit spec's Fix 2 did not survive the tree
- The spec's Fix 2 said to thread a segmentation `mask=` through the focus scorers. **It targeted
  the wrong functions and a mask that is never available:** `bf_analyse_focus_series` (its named
  target) has **zero callers**; the live path `bf_analyse_frame_quality` computes its metrics inline,
  not via `bf_focus_metric`; and **no focus-QC caller has a mask** (those panels have only an image
  dropdown, and QC usually runs before segmentation exists). Threading `mask=None` everywhere would
  have added inert parameters that change nothing and leave the bug intact. Per Gable's call, the fix
  is the **maskless robustification** above — which helps every current caller, none of whom can pass
  a mask. `bf_focus_metric`'s existing mask path is kept exact for the day a mask source is wired.
- `bf_analyse_frame_quality` min-max normalises each frame independently, which already partly
  counters a bright speck, so its robustification is defence-in-depth (no regression) rather than a
  demonstrated flip — recorded in the test rather than overclaimed.
- **Open decision (from Fix 1):** whether a type-mismatched `set_data` should overwrite or reject,
  and the related quirk that numeric keys seeded as `int` (`object_size`, `microns_per_pixel_sq`)
  reject a `float` update with a warning. Left for a deliberate call.

## [1.6.90] - 2026-07-17
### Fixed — **The three live bugs the audit verified and left: all three, and one was worse than recorded.**

**A per-cell grouping in every puncta plot never fired.** `analysis_plots._grouped` gated on
`'cell_label'`; `puncta_analysis_func` writes `'cell label'` — with a **space** — and is the only
producer in the codebase that does. Recorded as a silently-dead branch; it is worse than that. The
`else` it always took runs one `ax.plot` over a **pooled multi-cell frame**, connecting points
*across* cells into a single line — a zigzag between unrelated objects, drawn as though it were a
trajectory. The picture said something untrue. Now resolved through `object_ref.cell_label_column`,
which accepts both spellings in one place.

**The column is deliberately NOT renamed.** It is user-visible in results tables and CSVs; renaming
it would silently change files a user has already saved. That decision was made in 1.6.74 (for
`ObjectRef.from_row`), and its comment already named this exact site as the remaining live mismatch —
so this finishes an existing decision rather than making a new one.

**Two different uuids identified the same layer.** `layer_tag_hook` stamps `pycat_layer_id`
(`uuid4().hex`, 32 chars) — what `ObjectRef.source_layer_id` carries and the brushing arc keys on;
`layer_tags.layer_tag_id` minted `pycat_tag_uid` (`uuid4().hex[:12]`) — what `partial_volume_tools`
uses and what `tags_for_plot` records. Two values, one layer, so matching a plot's recorded id against
a ref could never succeed. `pycat_layer_id` now wins (it is the one with consumers) and
`pycat_tag_uid` is kept as an **alias holding the same value**, so existing readers keep working and
now agree with the refs. A legacy-only `pycat_tag_uid` is adopted rather than replaced; a *stale* one
alongside a `pycat_layer_id` is replaced, because it could not have matched anything anyway.

**The tifffile/zarr shim was not dead code — it was a fix nobody installed, for a LIVE bug.** The
audit recorded it as *"dead code that looks like a live workaround"* and suggested deleting it.
Reproduced on this tree (zarr 3.2.1, tifffile 2026.4.11) with no shim:

```
from zarr.core.chunk_grids import RegularChunkGrid   -> ImportError
tifffile ... .aszarr()                               -> ValueError: zarr 3.2.1 < 3 is not supported
```

(tifffile blames the version for *any* ImportError out of its zarr-3 module; 3.2.1 is not < 3.) Per
the shim's own docstring, that breaks **every read that falls to the BioIO dask path — multi-channel
TIFFs and all CZI — from loading lazily.** It is now installed in `pycat/file_io/__init__.py`: the
package that owns those reads, and early enough to land before `tifffile.zarr` is first imported. The
call is idempotent, no-ops when the symbol exists, swallows a missing zarr, and declines to install a
broken stand-in.
### Notes
- **`test_zarr_compat` was silently disabling lazy reads for every test that ran after it.** It fakes
  `zarr` in `sys.modules`, saved *two* modules and deleted *all* of them, so `zarr.core.chunk_grids`
  never came back. Worse: `_reload_compat` imports `pycat.file_io` transitively **during the fake
  window**, so the shim ran against a fake zarr, installed nothing, and never retried — the import is
  cached. Verified: `TIFFFILE_ZARR_READY` is False afterwards and restoring the real zarr does not
  undo it. That is why `test_zarr_shim_is_installed` failed in the full suite and passed alone. The
  test now restores every zarr module and re-installs the shim: a test that fakes a global module owns
  the damage.
- `tests/test_tifffile_zarr_shim.py` already proved the shim *works* when called; nothing proved it
  was *called*. The distance between those two tests is where this bug lived, and
  `test_zarr_shim_is_installed.py` closes it.

