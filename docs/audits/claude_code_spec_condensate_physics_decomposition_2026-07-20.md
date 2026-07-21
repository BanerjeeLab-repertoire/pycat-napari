# Claude Code spec — Decompose `condensate_physics_tools.py` by physics domain

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. **2,470 lines**,
20 test files, and it holds the manuscript-facing material-properties physics (MSD, moduli, coarsening,
FRAP-adjacent fits). Several of its functions were already split by phase (`fit_anomalous_diffusion`,
`fit_coarsening`, `fit_fusion_relaxation` in 1.6.168–180), proving the file decomposes safely. This
completes the job by domain.

## Verified state
```
41 functions, 5 over 120 lines:
  228  fit_coarsening
  171  analyse_frame_quality
  165  compute_moduli_evans
  159  compute_msd
  130  fit_aspect_ratio_relaxation
  114  compute_moduli_evans_bootstrap
  100  _classify_msd_motion
   99  fit_anomalous_diffusion   (already phase-split)
```
20 test files — including the golden-master MSD/viscosity chain that recovers D to 1.1% and viscosity
to 3.2%. The characterization net for the physics is strong (this is the module whose numbers are most
directly checked).

The functions cluster by **physical quantity**: MSD/diffusion, viscoelastic moduli, coarsening/ripening,
shape relaxation (fusion/aspect-ratio), and frame-quality assessment.

## Target — a `condensate_physics/` package by quantity
```
toolbox/condensate_physics/
    msd.py           # compute_msd, fit_anomalous_diffusion, _classify_msd_motion
    moduli.py        # compute_moduli_evans, compute_moduli_evans_bootstrap (viscoelasticity)
    coarsening.py    # fit_coarsening (ripening/growth)
    relaxation.py    # fit_aspect_ratio_relaxation, fusion-relaxation fits
    frame_quality.py # analyse_frame_quality
    (viscosity stays with VPT — see note)
```
`condensate_physics_tools.py` becomes a thin re-export shim.

**Note on overlap with VPT:** viscosity-from-diffusion (Stokes-Einstein) lives in the VPT split
(`vpt/viscosity.py`). `compute_msd`/`fit_anomalous_diffusion` here are the general condensate-physics
versions. Keep them distinct; do not merge the two MSD paths in this spec — note the relationship so a
later increment can reconcile deliberately if warranted.

## Method — the golden-master chain is the net
1. **The MSD → D → viscosity golden-master** (D to 1.1%, α to 0.1%, viscosity to 3.2%) is the
   characterization net for the MSD/diffusion functions. It must pass **unmodified** after
   `compute_msd`/`fit_anomalous_diffusion`/`_classify_msd_motion` move to `msd.py`.
2. **Moduli** (`compute_moduli_evans` + bootstrap) — confirm a test pins the modulus output on a known
   input before moving; the Evans method is exact physics, a reordered computation could shift the last
   digits.
3. **Coarsening / relaxation fits** — already phase-split and byte-identical; moving whole functions to
   their domain module is lower-risk, but still pin-then-move.
4. **Move, don't rewrite** — no reassociated sums, no "cleaner" fits. The golden-master tolerances are
   tight enough to catch a floating-point reassociation, which is the point.

### Hard rules
- One quantity per commit; golden-master + `pytest -m core` green between each.
- No test edited to make a move pass.
- Re-export shim for every previously-public name; grep callers first (VPT, timeseries, and the
  dynamics UIs call into this).

## Why now
- Manuscript-facing physics — a focused `moduli.py`/`msd.py`/`coarsening.py` is far easier to cite and
  verify in a Methods section than a 2,470-line file.
- Already partially decomposed (three fits phase-split) — the pattern is proven on this exact file.
- Strong golden-master coverage — the split is safe.
- Completes the "big scientific files" decomposition alongside VPT + timeseries + segmentation.

## Tests
- The MSD/D/viscosity golden-master passes unmodified after the MSD move.
- Moduli output pinned and identical after its move.
- Coarsening/relaxation fits byte-identical (they already are).
- All 20 test files pass unmodified.
- Re-export shim resolves every previously-public name.
- Lower `_MAX_LONG_FUNCTIONS` / per-file ratchet.

## Steps
1. Create `toolbox/condensate_physics/`; move `msd.py` (compute_msd + fits + classify); run golden-master.
2. Move `moduli.py` (Evans + bootstrap); run tests.
3. Move `coarsening.py`; run tests.
4. Move `relaxation.py` (aspect-ratio + fusion); run tests.
5. Move `frame_quality.py`; run tests.
6. `condensate_physics_tools.py` → re-export shim; lower ratchets.
7. Full `pytest -m core` + golden-master green after each step.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG before/after.

## Definition of done
- `condensate_physics_tools.py` is a thin shim; physics lives in `toolbox/condensate_physics/` by
  quantity.
- The MSD/D/viscosity golden-master and moduli pins pass unmodified.
- All 20 test files pass unmodified; no numerical output changes.
- Ratchets lowered.

## Cautions
- **The golden-master tolerances are the net** — D to 1.1%, viscosity to 3.2%. If they move after a
  "structural" split, a computation changed. Revert, don't loosen.
- **Don't merge the two MSD paths** (this module's vs VPT's) in this spec — note the overlap, reconcile
  later on purpose.
- **Move, don't improve** — no reassociated arithmetic; the tight tolerances will catch it, which is
  why they exist.
- Re-export shim mandatory; VPT/timeseries/dynamics UIs import this — grep every caller.
- One quantity per commit.
