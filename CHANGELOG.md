## [1.6.99] - 2026-07-18
### Fixed — **VPT plot-click: the real loop, the offset trajectory, and the too-bold highlight (from viewer testing).**
Real-viewer feedback surfaced three bugs the headless tests could not — exactly the "verified by
simulation, not by eye" gap that was flagged all along.

**The click still looped through many tracks — and it was NOT the 1.6.83 re-entrancy loop.** Every MSD
line is drawn with `set_picker(5)`, and the curves all fan out from near the origin, so a single click
there lands within the pick radius of dozens of lines. matplotlib fires a *separate* `pick_event` for
each, so one press became dozens of genuine selections + reveals — which drained (visibly, in the
terminal) long after the window was closed. The re-entrancy guard could not catch this because the
picks are not re-entrancy; they are real, separate events. `_debounce_picks` now collapses the many
picks from one click into ONE, on the line **closest** to the click (measured in pixels, so it is
correct on a log-log plot): one click, one track.

**The picked trajectory was offset from the bead.** The reveal drew `y_um`/`x_um` — the
**drift-corrected** positions — while the base "Bead Trajectories" layer draws `y_um_raw`/`x_um_raw`,
the raw positions that sit on the actual beads. Drift correction subtracts the centre-of-mass motion,
so the corrected path is shifted, and the highlight traced that shift instead of the bead. It now
prefers the raw coords, exactly as the base layer does, so it lands on the bead it highlights.

**The picked trajectory was too bold and buried the detail.** Its width was `0.12 / mpp` — inverse
pixel size — so at a fine pixel size it ballooned (mpp 0.05 → 2.4 px). It is now a thin fixed width in
pixel units (`_PICKED_TRACK_WIDTH_PX`, the one knob), so it stays a thin trace at any magnification and
the eye goes to the pulsing ring.
### Notes
- The debounce collapse is unit-tested (N picks from one click → one selection on the nearest line);
  the real "one click = one track" behaviour needs confirmation at a viewer, as does the trajectory
  now sitting on the bead. The re-entrancy guard's tests still pass — they cover a different failure
  that also exists.
- **Session loading is separately broken** (loading a saved session does not restore the working
  state) and blocks fast verification — reported, not yet diagnosed; it needs the specific symptom and
  a session folder to debug.

