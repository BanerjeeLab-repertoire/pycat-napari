## [1.6.93] - 2026-07-17
### Fixed — **CI red: a `core` test reached napari through the data layer.**
`test_set_data_new_key` (1.6.91) is marked `core` — it must run on the headless CI runner, which has
no napari. It exercises `BaseDataClass.set_data`, and `pycat.data.data_modules` imported
`napari.utils.notifications` **at module scope**, so all five of its tests died on the runner with
`ModuleNotFoundError: No module named 'napari'`. The logic under test is pure dict handling; the
napari coupling was incidental (two notification calls).

`data_modules` now uses the `pycat.utils.notify` shim — forwards to napari when a UI is present,
prints otherwise — the same pattern `condensate_physics_tools` has used since 1.5.378. napari was the
*only* GUI dependency in the entire `pycat.data` package, so this makes the data layer importable with
no GUI stack. Verified by reproducing the CI condition exactly (import + construct + `set_data` with
napari blocked in `sys.modules`).

**Why it reached CI at all:** the headless-import contract (`test_headless_science`) is enforced only
over `src/pycat/toolbox/`. `data_modules` lives in `pycat.data`, so nothing watched it. New guard
`test_data_layer_is_headless` closes that gap — static (no module-scope GUI import) and dynamic
(constructs with napari blocked) — and is verified to fail against the pre-fix module.
### Notes
- `conftest`'s auto-skip could not have caught this: it scans **module-scope** imports, and the test
  reaches `data_modules` through a function-local import. The right fix was decoupling the module, not
  skipping the test — the logic genuinely is core-able, and now is.
- Reviewed the rest of 1.6.91/1.6.92 (the `robust_focus_energy` trimmed-mean focus metric and its
  rollout across the Brenner/Tenengrad/Laplacian sites, the `set_data` existence-before-class fix):
  sound, no changes needed.

