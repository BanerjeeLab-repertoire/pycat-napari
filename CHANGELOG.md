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

