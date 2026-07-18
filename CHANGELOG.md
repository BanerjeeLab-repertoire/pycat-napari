## [1.6.100] - 2026-07-18
### Fixed — **VPT plot-click: replace the fragile debounce with one button-press per click (audit-driven).**
An audit of 1.6.99 found the debounce **intrinsically fragile**, and correctly. It assumed every
matplotlib `pick_event` from one mouse press arrives before `QTimer.singleShot(0)` fires — not a safe
contract: with redraws and queued Qt/napari callbacks the resolve can run *between* groups of picks, so
one click still cycled through several tracks. Its unit tests passed only because they drove the
resolver synchronously, never reproducing the real interleaving — the "verified by simulation" gap,
one level deeper.

**The mechanism is replaced, not patched.** matplotlib fires exactly ONE `button_press_event` per
physical click, so there is nothing to batch. The MSD lines are made **non-pickable**; a single
canvas-level handler (`_connect_nearest_curve_click`) scans the visible curves once, ranks them by
**point-to-segment** distance in display pixels (a click can lie on the drawn edge of one curve yet be
nearest a sampled *vertex* of a neighbour — segments are what the eye sees), and selects the single
nearest within a threshold. One event, one selection, by construction.

Also from the audit:
- **Ambiguity is refused, not guessed.** Where the two nearest curves are within a few pixels of each
  other (the convergence zone near the origin), it does not pick an effectively-arbitrary track — it
  asks for a click farther along a curve. Honest beats confidently-wrong.
- **The two MSD plots now behave the same.** The standalone plot used to re-fire a selection when the
  already-selected curve was re-clicked; the consolidated panel did not. Both now suppress it.
- Clicks outside the axes, in empty space (beyond the radius), or with a non-left button are ignored.
### Notes
- Tested through the **real matplotlib event path** — an actual figure with an actual `MouseEvent`
  processed by the canvas callbacks, which is the coverage the audit noted was missing. One press
  yields at most one selection; a convergence-zone click does not cascade. Twelve focused tests in
  total (segment distance, empty space, outside-axes, re-click, ambiguity refusal, real events).
- The `pick_event`-based mechanism (`_debounce_picks`, `_on_pick`, `_pick_pixel_distance`) is gone;
  the removal is recorded in `test_nothing_was_dropped`'s deliberate list.
- Deferred from the audit (larger, follow-up): consolidating both MSD render modes behind one
  `MSDSelectionController` (it recommends this as the durable architecture), and having the
  SelectionService own the accepted selection rather than the UI setting it provisionally. The
  immediate bug is fixed within matplotlib, as the audit concluded it could be.

