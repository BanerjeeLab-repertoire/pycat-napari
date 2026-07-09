# PyCAT Per-Method Audit — 2026-07-09

Audit of all analysis methods across four axes:
1. **Workflow / tool chain** — what the method does and whether the choices are sound
2. **Performance / redundant I/O** — repeated materialization, re-reads, recomputation
3. **Autopopulation** — dropdown pre-fill / auto-refresh logic
4. **UEX status circles** — required/optional guides wired correctly

Findings are tagged `[PERF]`, `[LOGIC]`, `[AUTOPOP]`, `[UEX]`, `[CONSISTENCY]` with
file:line evidence. Priority: **P1** = act now (correctness / clear waste), **P2** =
worth doing, **P3** = cosmetic / nice-to-have.

A companion changelog entry records which findings were fixed in the follow-on release.

---

## Cross-cutting findings (affect many methods)

### CC-1 `[PERF][P1]` temperature_ui re-materializes the same stack 4×
`temperature_ui.py` materializes `self.viewer.layers[sname].data` independently in
four button handlers (lines 439 `_on_guess_clear_frame`, 465, 607, 665). Each click
re-decodes the entire lazy stack from disk. For a multi-frame temperature series this
is repeated full-stack I/O with no caching. **Fix:** materialize once, cache on the UI
instance keyed by layer name, invalidate when the stack dropdown changes.
→ **FIXED** (see changelog): added `_get_stack()` cache.

### CC-2 `[CONSISTENCY][P3]` `create_layer_dropdown` reimplemented 9×
Nine toolbox UIs define their own `create_layer_dropdown` (vpt, temperature,
invitro_fluor, zstack, brightfield, frap, fusion, invitro_bf, timeseries_invitro_fluor)
plus the base in `ui_modules`. **Verified NOT a bug:** all nine are thin delegators to
`central_manager.toolbox_functions_ui.create_layer_dropdown`, which carries the real
auto-refresh (events.inserted/removed) + name_hint auto-select wiring. So autopopulation
works everywhere. The redundancy is cosmetic — the delegators could be removed via a
shared mixin — but there is no correctness issue. Left as-is (low value, some churn risk).

### CC-3 `[UEX][P2]` Seven methods have zero status circles
`timeseries_invitro_fluor`, `temperature`, `fusion`, `fd_curve`, `data_qc`,
`contrast_cascade`, `advanced_analysis` use no `button_with_circle`. Of these:
- **Legitimately exempt:** `data_qc`, `contrast_cascade` (exploratory/scribble tools,
  no fixed required-input contract), `advanced_analysis` (dispatcher shell).
- **Gap worth closing:** `temperature`, `fusion`, `timeseries_invitro_fluor`,
  `fd_curve` run real analyses with layer dropdowns whose "is a valid input selected"
  state would benefit from the same required/optional circle guides used elsewhere.
  → Pinned for a UEX-consistency pass (not auto-fixed — needs per-field required/optional
  assignment, which is a judgement call).

### CC-4 `[AUTOPOP][P2]` Coloc layer hand-off is the only *smart* pre-selection
Only the new `ColocalizationAnalysisUI` scores upstream layer names to pre-select
sensible defaults. Every other method relies on `name_hint` exact-substring auto-jump
(fires only when a NEW layer is inserted whose name contains the hint). That's fine for
in-pipeline sequential work, but methods entered *after* a separate analysis (e.g. jumping
into FRAP/coloc with layers already present) don't get smart pre-selection. Candidate to
generalize the coloc `_suggest_layers` scorer into a shared helper. Pinned.

---

## Per-method audit

### CondensateAnalysisUI (ui_modules) — core 2D cellular fluorescence
- **Workflow:** measure-line → upscale → pre-process → Cellpose → cell analysis →
  subcellular object segmentation → puncta analysis. Sound; this is the validated
  reference pipeline the other fluor methods mirror.
- `[PERF][P3]` Under-covered by progress bars for the per-cell subcellular loop (only 3
  progress refs in the whole ui_modules). Per-cell segmentation is measurable → determinate
  bar candidate. Pinned to progress-bar rollout.
- **Autopop / UEX:** uses base dropdowns + circles correctly.

### TimeSeriesCondensateUI (ui_modules)
- **Workflow:** sound; uses global-range normalization + per-frame streaming (the
  validated TS path). No redundant materialization found (streams via iter_frames).
- `[UEX][P3]` Circle coverage lighter than 2D condensate; acceptable given the workflow
  differences.

### condensate_physics_ui — MSD / viscosity / coarsening / intensity decomposition
- **Workflow:** `_on_msd` (MSD → anomalous fit → viscosity), `_on_hist` (intensity
  decomposition), `_on_coarsen` (coarsening stats). Tool choices sound; runs on
  `_PhysicsWorker` threads (UI stays responsive). Materializes mask stack (308) and QC
  stack (413) in *different* handlers — not redundant.
- `[LOGIC][P2]` The MSD viscosity math is proven correct (golden-master tests, 1.5.308);
  the real-data discrepancy is upstream in linking, not here. No change to this method.
- **UEX:** 11 circle uses — good coverage.

### frap_ui — fluorescence recovery
- **Workflow:** shapes/Lumicks load → recovery-curve fit. Materializes recovery (410) +
  optional prebleach (490) — two *different* stacks in one analyze, legitimate.
- `[PERF][P3]` No worker thread (thread=0): a large recovery stack fit runs on the main
  thread → UI freeze during fit. Candidate for worker-thread offload + progress. Pinned.
- **UEX:** 4 circles; adequate.

### fusion_ui — droplet fusion relaxation
- **Workflow:** build signal (from forces or aspect-ratio) → fit relaxation. Sound.
- `[PERF][P3]` No worker thread; aspect-ratio signal over a stack runs on main thread.
- `[UEX][P2]` **Zero status circles** despite having image/mask dropdowns and a real
  run button. Inconsistent with sibling methods. → pinned (CC-3).

### temperature_ui — temperature-series labeling & analysis
- `[PERF][P1]` **CC-1**: 4× re-materialization of same stack. → FIXED.
- **Workflow:** CSV sync → build temperature labels → per-temperature analysis. Sound.
- `[UEX][P2]` Zero circles (CC-3). Has a stack dropdown + several action buttons that
  would benefit. Pinned.

### vpt_ui — video particle tracking / microrheology
- **Workflow:** host (segment/infer/none) → detect beads → link → drift → MSD → viscosity.
  Extensively hardened this session (drift modes, out-of-plane control, threshold
  recording, double-100% progress fix). Streams (no redundant materialization).
- `[UEX]` 10 circles; good. **No new findings** — most-audited method already.

### invitro_fluor_ui — 2D in-vitro fluorescence
- **Workflow:** pre-process (gaussian/LoG/full) → segment → field summary → dynamics/QC.
  Sound; mirrors the condensate reference. Materializes label + image stacks in the same
  `_on_run` (798/804 — different layers, legitimate) and a QC stack elsewhere (978).
- `[PERF][P2]` Highest heavy-op count (38); runs on `_IVFWorker` threads (good). The
  dynamics/QC stacks could show materialization progress (pinned to rollout).
- **UEX:** 14 circles — strong.

### invitro_bf_ui / brightfield_ui — brightfield condensate
- **Workflow:** preprocess_brightfield → segment_bf_condensates → metrics → field summary.
  Sound for the brightfield regime. Multiple materializations are in *different* handlers
  (not redundant). Worker threads present.
- `[UEX]` 13 / 26 circles — strongest coverage of any methods.
- `[LOGIC][P3]` Auto object-size estimation is (correctly) NOT applied to brightfield
  (validity guard) — matches the 1.5.313 scoping. Brightfield edge/texture estimator is
  stubbed & flagged for validation.

### zstack_segmentation_ui — 3D
- **Workflow:** 3D bg removal → 3D Cellpose → 3D subcellular → 3D metrics. Sound; worker
  threads; streams. No redundant materialization.
- **UEX:** 6 circles; adequate for a 4-step linear flow.

### spatial_metrology_ui
- **Workflow:** neighborhood / spatial-stats metrics. Worker threads (thread=4). No
  materialization (operates on masks/tables already in memory). Sound.
- **UEX:** 5 circles.

### fibril_ui — fibril morphology
- **Workflow:** per-segment length/tortuosity/curvature/persistence. Light (heavy=1);
  operates on an existing skeleton/mask. No perf concern.
- **UEX:** 5 circles — good for its size.

### advanced_analysis_ui — dispatcher for SpIDA / N&B / molecular counting / morphology
- **Workflow:** shell that routes to sub-analyses. 4 bars, 8 worker refs — the heavy
  sub-analyses (SpIDA, N&B) are threaded.
- `[UEX][P3]` Zero circles at the shell level (CC-3) — acceptable, it's a dispatcher; the
  sub-analyses carry their own guards.

### nb_ui / spida_ui — molecular counting
- **Workflow:** N&B (per-pixel number/brightness) and SpIDA (intensity distribution).
  `[PERF][P2]` **thread=0** for both — these are compute-heavy (per-pixel statistics,
  distribution fitting) and appear to run on the main thread → UI freeze on large data.
  Candidate for worker offload. Pinned (needs care — molecular counting math is sensitive).
- `[UEX]` 2 / 3 circles — light but the input contract is simple.

### fd_curve_ui — force-distance curves
- **Workflow:** load → segment cycles → fit. heavy=20 but the work is 1D signal processing
  (fast). `[PERF][P3]` No bars, but likely fast enough not to need them.
- `[UEX][P2]` Zero circles (CC-3) despite a multi-step load→segment→fit flow. Pinned.

### data_qc_ui — quality-control scan
- **Workflow:** run QC panel → save. Exploratory; circle-exempt (CC-3).
- `[PERF][P3]` heavy=4, no bar — a QC scan over many frames could show progress. Minor.

### contrast_cascade_ui — scribble-based contrast enhancement
- **Workflow:** paint scribbles → learn local-contrast/ridge model → apply. Exploratory
  (scribble-driven); circle-exempt. heavy=13, no bar → a "learning/applying" bar would
  help on large images. Pinned to progress rollout.

---

## Priority summary

**P1 (act now):**
- CC-1 temperature_ui 4× re-materialization → **FIXED this release**.

**P2 (worth doing, pinned):**
- CC-3 UEX circles for temperature / fusion / timeseries_invitro_fluor / fd_curve.
- CC-4 generalize coloc's smart layer pre-selection into a shared helper.
- nb_ui / spida_ui worker-thread offload (heavy compute on main thread).
- frap_ui / fusion_ui worker-thread offload.

**P3 (cosmetic / nice-to-have):**
- CC-2 dedupe the 9 delegator `create_layer_dropdown`s via a mixin.
- Progress-bar rollout to core condensate per-cell loop, contrast_cascade, data_qc
  (tracked in the existing progress-bar roadmap rubric).

## What was NOT a problem (verified, so we don't chase ghosts)
- Autopopulation is **not** broken in the delegator UIs (CC-2) — auto-refresh is inherited.
- frap / invitro_fluor / brightfield multiple materializations are **different layers or
  different handlers**, not redundant re-reads (only temperature was genuinely redundant).
- VPT is thoroughly audited already; no new findings.
- MSD/viscosity math is correct; not a method-logic bug.
