## [1.6.101] - 2026-07-18
### Fixed — **VPT plot-click: always select the nearest track, and cycle through overlaps on re-click.**
1.6.100's ambiguity refusal was over-eager for real data: MSD curves overlap essentially *everywhere*,
so "click farther along a curve to pick one" never resolved — nothing ever got selected, which is a
worse experience than the original cascade. Reported straight from the viewer.

The dense-data model is the opposite, and correct: **a click near a curve ALWAYS selects the nearest,
and repeated clicks at the same spot cycle through the stack of overlapping tracks there** — the user
drives the disambiguation by clicking again, rather than being asked to find an uncrowded pixel that
does not exist. A click at a new spot starts a fresh stack from its nearest curve; re-clicking a lone
track is a no-op. The "N tracks overlap here — click again to cycle" hint shows once per spot, not on
every click.
### Notes
- Verified through the real matplotlib event path: repeated clicks at the convergence zone select
  tracks one at a time, all distinct, cycling — not a cascade (the first bug) and not zero (the
  over-eager refusal). Fifteen focused tests including the real-`MouseEvent` cases.
- Still one `button_press_event` per physical click (the 1.6.100 architecture); only the choose-among-
  candidates policy changed, from refuse to cycle.

