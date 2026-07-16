# PyCAT — Science & Engineering Audit

**Date:** 2026-07-16 · **Tree:** 1.6.70 · Grounded in a fresh systematic pass over the 1.6.70 source
(94.5k LOC src, 20.8k LOC tests, 166 modules, 116 test files). This is an assessment of *state and
risk*, not a task list — the specced work already in `docs/audits/` addresses several items.

---

## Executive summary
PyCAT is a **scientifically rigorous, unusually well-tested** research platform whose **engineering
structure is its main liability** — a handful of god-files and five parallel registries make feature
insertion a cross-cutting exercise. The science posture is the standout strength: the test suite
encodes *correctness invariants* (pedestal-invariance, NaN-not-a-lie, no-silent-gates, known-answer
recovery) that most academic software never attempts. The right strategic posture is **preserve the
science rigor, chip at the engineering coupling incrementally** — not a big-bang rewrite, which would
risk the manuscript-critical correctness for structural elegance.

---

## SCIENCE — strong, with specific and known gaps

### What's genuinely excellent
- **Correctness-invariant testing.** The suite doesn't just check "does it run" — it checks "is the
  answer *right and un-foolable*": `imaging_realism` (partition survives a camera pedestal, saturation
  is refused not reported), `test_pixel_size_plausibility`/`_sentinel` (a corrupt scale is caught, an
  unknown one is NaN not a plausible 1.0), `test_spatial_nulls` (clustering/coloc have honest null
  models with the right false-positive rate), `test_no_silent_scientific_gates`. This is
  *reviewer-grade rigor* and the strongest single asset for the Nature Methods angle.
- **Physical honesty as a coded contract.** Anisotropic voxel volume is NaN when z-step is unknown
  (`test_anisotropic_voxel`), viscosity carries the lag-window interval its MSD fit supports, N&B
  labels an uncalibrated number "apparent." The anti-black-box philosophy is enforced by tests, not
  just intended.
- **The filtering-defaults awareness.** Two silent filters were caught INVERTING results
  (`r2_min=0.999` → mean 77 vs true 44; SNR-ratio on a pedestal → all-untransfected), both fixed, and
  a sensitivity harness is now specced. Recognising this failure *class* is more valuable than the two
  fixes.

### Science gaps (verified, ranked by scientific risk)
1. **Filtering defaults not yet swept.** ~39–115 pre-analysis filter defaults across `*_tools.py`;
   only 2 proven-and-fixed. The SNR/R² select-for-measured-quantity class (segmentation
   `local/global_snr_threshold=1.0` appears ~10×; `bleach_r2_min`) is the highest-risk untested group.
   *(Harness specced: `claude_code_spec_filter_sensitivity_2026-07-16.md`.)*
2. **Focus-quality picks the sharpest DEBRIS.** `analyse_frame_quality`/`bf_analyse_frame_quality`
   score focus over the whole frame, so dust beats an in-focus condensate. Correctness bug; fix is
   mask-restricted, multi-metric focus. *(Roadmap; not yet specced.)*
3. **VPT detection dropout (~15%) bridged, not fixed.** The flagship viscosity chain works to the
   validated 8.325 baseline via TrackMate, but PyCAT's own linkers shatter on detection dropout. The
   science is proven correct (golden-master D/α/viscosity); the *robustness* of the automated path is
   the open item. This is the real scientific blocker for the "opt out of TrackMate" story.
4. **The flagship differentiator has zero code.** Calibrated thermodynamics (intensity → concentration
   → ΔG_transfer) — the strongest manuscript claim — has intensity ratios but no standard-curve / ΔG
   implementation. This is a *build*, not a fix.
5. **Dark modules (truly 0 test references):** `batch_roi_tools`, `general_image_tools`,
   `pipeline_diagnostic_tools`, `video_export_tools`. NONE are core science (they're ROI/UI/export
   plumbing), so the coverage gap is low-scientific-risk — but `general_image_tools` (1085 lines) is
   large enough to warrant at least smoke coverage. *(Note: most "untested-looking" science modules —
   condensate_physics 11 files, timeseries_condensate 8, label_and_mask 7 — ARE well covered via
   GROUP test files; the coverage picture is much healthier than a same-name-file scan suggests.)*

---

## ENGINEERING — functional and well-guarded, but structurally coupled

### What's healthy
- **The test surface is large and honest** (20.8k LOC, 95 core-marked files) and includes
  *meta-tests* that guard the guards: `test_the_runner_actually_runs`, `test_complexity_budget` (a
  downward-only ratchet), `test_headless_science` (science modules import without Qt),
  `test_no_undefined_names`, `test_nothing_was_dropped`. This is rare discipline.
- **The file-I/O decomposition largely succeeded.** `file_io.py` came off its peak; readers extracted
  into `readers/`; lazy wrappers into a Qt-free `lazy_sources.py` with a subprocess-verified headless
  guard. The recent arc is a model of incremental, test-guarded refactoring.
- **Exception hygiene is better than the raw count suggests.** 829 `except Exception` total, but 120
  route through `debug_log` (accountable) and the truly-silent ones are guarded by
  `test_no_silent_scientific_gates` / `test_silent_fallbacks` from swallowing *scientific* gates.
  Still, **443 `except: …pass`** is a lot of surface where a non-scientific failure can vanish.

### Engineering liabilities (verified, ranked by leverage)
1. **God-files.** `ui_modules.py` (5464), `timeseries_condensate_tools.py` (2828), `file_io.py`
   (2690), `image_processing_tools.py` (2669), `vpt_tools.py` (2627), `segmentation_tools.py` (2526).
   The complexity ratchet (135 functions >120 lines, ceiling holding) is the right pressure valve —
   it prevents *growth* without demanding a rewrite. `ui_modules.py` is the standout: 5464 lines is a
   review blind spot.
2. **Five parallel registries = the feature-insertion tax.** Adding one analysis operation can touch
   `ui_modules.py` (UI), `batch_step_registry.py` (1613, batch replay), `navigator/op_catalog.py` +
   `modules.py` (Navigator), and `tag_registry.py` (tags). These drift independently. *(OperationSpec
   increment 1 shipped — a typed view + drift guard — the safe foundation for consolidating this
   without a big-bang.)*
3. **Untyped `data_repository` dict.** 147 string-keyed accesses across the codebase; a mutable dict
   holding calibration + DataFrames + metadata + segmentation params. It couples everything by string
   key. **Concrete defect still live** (`data_modules.py:131`): `set_data` accesses
   `self.data_repository[key].__class__` BEFORE the `elif key not in self.data_repository` check —
   so adding a genuinely new key raises `KeyError`. A 5-line reorder fixes it; the typed-session
   rewrite is a bigger, deferrable project.
4. **Broad CentralManager coupling.** 27 toolbox modules reach `central_manager`/`self.viewer`
   directly. Diffuse, not urgent for a small team, but it's why headless testing needs the import
   guards it has.
5. **Synchronous work on the Qt thread.** The session-load freeze (just specced) and the
   materialization freezes (progress-rollout specced) share a root: heavy work runs on the main
   thread. The progress specs make it *visible*; true responsiveness (worker threads) is a larger,
   separate posture change.
6. **Latent `np.asarray(layer.data)` frame-0 collapse.** 78 sites; most are safe (2D workflows), but
   the pattern silently collapses a lazy time-series to frame 0 where it bites (the 1.5.273 VPT bug,
   the brushing `crop_for_ref` bug just found). Needs per-site judgment, not a blind sweep — a
   standing hazard rather than an active fire.

---

## Cross-cutting assessment

**The central tension:** the science layer is disciplined and test-guarded; the engineering layer is
functional but coupled. The *good* news is that the engineering debt is **contained by tests** — the
ratchet stops complexity growth, the headless guards stop import rot, the silent-gate guards stop
scientific corruption. So the debt is *managed*, not metastasizing. That is a fundamentally healthier
position than "clean architecture, untested science" — the inverse, which is far more common in
academic software and far more dangerous.

**Strategic recommendation (unchanged from the platform-consolidation analysis):**
- **Tier 1 — cheap, do now:** the `set_data` KeyError reorder; adopt `OperationResult`/`FitResult`
  additively on new code; finish the specced UX/correctness fixes (session loader, progress, focus
  debris, filter harness).
- **Tier 2 — high-value, between-manuscript:** grow OperationSpec from validate-first to generate-one
  subsystem; the brushing SelectionService/EntityRef arc (already specced 1–5); calibrated
  thermodynamics (the flagship build).
- **Tier 3 — defer, happening incrementally:** god-file decomposition (ratchet-driven), typed session
  model, CentralManager → services. None should be a big-bang pre-manuscript.

**Bottom line:** PyCAT is in the *right shape for a methods paper* — the rigor is real and enforced.
The engineering coupling is the tax on future features, and the correct response is the one already in
motion: consolidate the identity/operation seams incrementally (OperationSpec, tags, EntityRef) so
that adding the next module is a bounded insertion, while never trading the science-correctness
invariants for structural elegance. Keep the ratchets; keep shipping small.
