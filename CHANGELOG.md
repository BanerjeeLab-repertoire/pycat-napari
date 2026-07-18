## [1.6.105] - 2026-07-18
### Changed — **The picked-track highlight is a Tracks layer at 2× the base width.**
From the viewer: after zooming to the bead, the picked-track line was still too thick to read the
trajectory's detail. The cause was a unit mismatch — the highlight was a Shapes path whose width is in
**data units**, so it ballooned as the new zoom-to-bead magnified the view, while the base "Bead
Trajectories" layer (a napari Tracks layer) has its width in **screen pixels** and stays constant.

- **The picked track is now a Tracks layer**, the same type as the base, so its `tail_width` is in
  screen pixels and no longer fattens at deep zoom. The width is exactly **2× the base**
  (`_PICKED_TRACK_TAIL_WIDTH = 2 · _BASE_TRACK_TAIL_WIDTH`, both new constants) — bold enough to stand
  out, thin enough to read the detail — which is what the user asked for by eye.
- **Still orange, still a separate overlay.** It colours via a registered flat-orange colormap
  (`#ff8c00`) rather than recolouring the base layer, so a user's own track colouring is never
  clobbered by a pick. `tail_length`/`head_length` span the whole track so it draws fully at any
  frame, including the bead's first frame. Falls back to a thin Shapes path only if `add_tracks` is
  unavailable.
### Notes
- Headless-tested: the picked track is a Tracks layer at 2× the base width, orange, and spans its full
  frame range. **The zoom-stable feel is UI-coupled** — confirm the line reads well at the zoom-to-bead.

## [1.6.104] - 2026-07-18
### Changed — **A VPT plot click now goes to the bead; the pulse is gone.**
From the viewer, on the picked track: the opacity slider oscillated continuously with no visible glow,
the highlight line was too bold to see detail through, and a click should take the stack to the bead's
z-slice and zoom in. Three fixes.

- **A plot click navigates to the bead — on by default.** `_navigate_to_bead` steps to the bead's
  frame, centres on it, and **zooms** so a small window (`_BEAD_ZOOM_WINDOW_PX = 80 px`) around it fills
  the view. Navigation was gated off while the plot-click loop existed; with one `button_press` per
  click (1.6.100) and the `_revealing` re-entrancy guard, the camera move is safe, so going to the bead
  — what the user asked a click to do — is the default now. VPT's now-unused `_follow_enabled` wrapper
  was removed; the generic brushing path keeps its own for the `follow_selection`/double-click case.
- **The pulsing ring was removed.** `_pulse_layer` armed a QTimer that oscillated the ring's
  size/opacity. But the ring is per-frame — present only on the bead's own frame — so scrubbing away
  left nothing to pulse while the opacity slider churned on for nothing. The ring is a static hollow
  marker now (`size=12, opacity=0.9`); the zoom-to-bead navigation is what draws the eye.
- **The picked-track highlight was thinned**, `_PICKED_TRACK_WIDTH_PX` 1.0 → 0.4, so the trace no
  longer obscures the trajectory detail underneath it.
### Notes
- Headless-tested: the pick navigates (steps + centres) and marks the track, the reveal stays
  re-entrant-guarded so navigating cannot loop, the ring is static with no timer armed, and the removed
  symbols are recorded in `_DELIBERATE`. **The zoom-to-bead feel is UI-coupled and needs a viewer** —
  confirm a plot click lands on the bead at a sensible zoom and the thinner line reads well.

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

