## [1.6.103] - 2026-07-18
### Added — **Session auto-restore: a load reopens the analysis method and rebuilds its view.**
Loading a session restored the dataframes into the repository but left an empty panel — the user had
to reopen the method and re-Compute by hand. Now a load lands back at the working state.

- **The active method is recorded on save.** The manifest gains `active_method` (the open analysis
  UI's class name), written by `write_session_outputs`.
- **The loader surfaces it**, and `_on_load` reopens that method via its `_switch_to_*` handler.
  Switching methods **preserves the data repository**, so the reopened method sees the restored data.
  A session saved before this was recorded has no `active_method`; the method is then inferred from a
  signature dataframe (`vpt_tracks` → VPT), so existing sessions restore too.
- **The reopened method rebuilds its view.** `VideoParticleTrackingUI.restore_session_view` rebuilds
  the trajectory + pickable layers and calls `_on_rheology` — the exact handler the **Compute MSD &
  Viscosity** button runs, which reads `vpt_tracks` from the repository — so the MSD/moduli plots come
  back through the one real render path, not a divergent copy. The slow part of VPT (detection +
  linking) is not redone; recomputing the MSD from the restored tracks is seconds.
### Notes
- Headless-tested: the manifest records/surfaces `active_method`, back-compat returns None (inferred
  from data), the method registry wires VPT correctly, and the restore hook exists. **The end-to-end
  reopen → rebuild → plots is UI-coupled and needs a viewer** — this is the part to confirm: load the
  session and check the VPT method reopens with its tracks clickable and its plots drawn.
- Parameters return at their defaults (frame interval auto-fills from the source metadata); a user who
  needs the session's exact bead radius/temperature sets them and re-Computes. Restoring the exact
  recorded parameters is a later refinement.
- Only VPT has a `restore_session_view` so far; other methods reopen (data preserved) and show a
  "reopen to rebuild" toast until they gain the same hook — additive, method by method.

