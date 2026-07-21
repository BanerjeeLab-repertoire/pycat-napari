# PyCAT backlog spec index (verified against 1.6.203, 2026-07-20)

This is the consolidated, ready-to-run backlog. Each entry is a self-contained mini-spec verified
against the 1.6.203 tree. Items are grouped and ordered by dependency. Where a full standalone spec was
written previously, its content is captured here so nothing depends on a lost file. Standing rules
apply to every item: **move/wire don't rewrite where possible; additive; one concern per commit;
`pytest -m core` green; version + PyPI push + commit (explicit filenames) + CHANGELOG per code change.**

---

## TIER A — the navigator + visibility + settings arc (largest capability gap; nothing blocks it)

### A1. General user-settings persistence  *(prerequisite for A3, and reusable everywhere)*
**Verified missing:** no `QSettings`/`user_config`/`first_run` anywhere.
Build `utils/user_settings.py`: a process-wide `UserSettings` singleton persisting **namespaced** keys
to one **atomic** JSON file in the OS user-config dir (`platformdirs.user_config_dir('pycat')`), with
registered defaults, typed accessors (`get_bool`/`get_int`), and `subscribe(key, cb)`. Corruption →
fall back to defaults, **never crash startup**. General mechanism, **zero feature knowledge** — the
navigator toggle, acquisition pixel-size profiles, plot-backend preference, and dismissed-QC-warnings
all consume it. Tests: round-trip across a fresh instance (cross-session), typed coercion, default
resolution, subscription fires, corruption-safety, atomicity (mid-write failure leaves prior file
intact), namespace non-collision.

### A2. General object-quality gate + wire the navigator's measurement half  *(the generator unblock)*
**Verified:** navigator catalog is **79 ops, all image→objects; zero measure/interpret/tracking**.
Two joined pieces:
1. **`utils/quality_gate.py`** — `evaluate_quality(objects, requirement, *, context) -> GateResult`
   composing signals that already exist (`calibration.check_calibration_validity`, `reliability.py`,
   `biological_qc_tools`, `measurement_stability`) into **block / warn / downgrade** with reasons.
   Feature-agnostic — used by the navigator, batch, QC advisor, and measurement UIs. Rules: block ≠ warn
   ≠ downgrade; an unassessed signal is *not* a passing signal; report don't silently drop; invent no
   new metric.
2. **Wire measurement ops into `operation_catalog.json`** — bind each to its real `public_api` symbol
   (`condensate_physics_tools.fit_coarsening`, `partition_enrichment_tools.*`, `spatial_metrology_tools.*`,
   `frap_tools.*`, coloc metrics, tracking) with `requires`/`provides`/`target` **plus a
   `QualityRequirement`**. `planner.compile` emits a terminal measurement step as *runnable* only when
   `evaluate_quality` passes, and surfaces the reason when blocked ("ΔG needs a calibrated pixel size").
   Then the canonical oracle reproduces all 13 workflows **end-to-end** (currently only the segmentation
   spine). `operation_id` must come from the spec, not hard-coded strings.

### A3. Navigator UI + general feature surfacing + beginner-default mode  *(depends on A1, A2)*
**Verified:** zero `AnalysisIntent`/`QuestionEngine`/`Planner` refs outside `navigator/`; and many
features have **0 UI refs** (biological_qc, measurement_stability, ontology, feature_provenance,
analysis_presets, scan_qc, figure_spec).
Three general pieces:
1. **`utils/app_mode.py`** — `AppMode` (BEGINNER/ADVANCED) backed by user-settings (`app.mode`), default
   BEGINNER on first run, runtime-switchable with a change signal. General — any UI consults
   `current_mode()`; nothing owns it.
2. **`utils/feature_registry.py`** — every currently-invisible capability registers a `FeatureCard`
   (title, one-line summary, category, entry callable, docs anchor, `min_mode`). The single answer to
   "what can PyCAT do and where." A future feature registers a card and is instantly discoverable.
3. **The beginner home dock** — navigator question-flow (`HybridQuestionEngine.next_question` →
   `planner.compile` → editable generated plan with quality-gate reasons inline) + capability cards +
   a prominent mode toggle. Default surface on first run (`is_beginner()`); advanced mode restores
   today's menu-first view with the navigator one card away. Mount via `viewer.window.add_dock_widget`.
   Guide, don't cage: generated workflows stay editable; every step shows *why* it's there. First-run =
   beginner; every later run = last choice; no mock UI.

---

## TIER B — correctness & loose ends (small, high-value)

### B1. Session pixel-size + user-settings persistence  *(CORRECTNESS — a silent calibration loss)*
**Verified missing:** `session_manifest.py` stores pixel-size *flags* but not the user-entered *value*.
A reloaded session whose file lacks metadata scale drops back to "using 1.0" — corrupting every
physical-unit measurement even though the user already typed the real scale.
Persist, in the manifest, a `pixel_size` block **only when user-entered** (value + `source:user_entered`
+ z-step), and restore it on load (satisfying the gate, marked user-provided — not laundered to
metadata). Also persist user-entered workflow parameters by serializing the existing
`batch_processor.record` params (one source of truth, not a parallel capture). **Persist user intent,
re-read file facts** — metadata-derived values are re-read, never saved. Backward-compatible (old
manifests load). *(The prior `session_persist_settings` spec is in-tree; this is its verified summary —
it has NOT landed as of 1.6.203.)*

### B2. Wire the actionable orphaned modules  *(finished work, invisible)*
> **◐ PARTIAL (updated 2026-07-21, tree 1.6.223):** `condensate_modes` is now **WIRED** (shipped 1.6.223) —
> both in-vitro whole-field-summary emitters (`invitro_fluor_ui`, `invitro_bf_ui`) call the new pure
> `condensate_modes.annotate_summary_table`, so every emitted table carries its `condensate_mode` and a
> 2D volume-fraction-refusal note (additive; byte-identical `field_summary` untouched). `cohort_targets` and
> `feature_provenance` each gained an importer since this index was written (partially wired — verify what
> remains). `czi_seam` is now **WIRED** too (shipped 1.6.224) — the streaming CZI open path runs a
> non-blocking, per-frame-sampled mosaic-seam QC (`warn_seam_qc`) that warns on a persistent tile seam
> without materialising the movie. **Remaining B2:** verify the residual hooks for the two partially-wired
> modules (`cohort_targets` plot-click wiring, `feature_provenance` export sidecar).
**Verified unwired (at index time):** `cohort_targets` (0 refs in plots), `condensate_modes` (0 refs in invitro),
`feature_provenance` export hook, `czi_seam` (1 ref — partially wired, verify). Each already exposes the
exact hook needed:
- `cohort_targets.attach_histogram_brushing` / `select_aggregate_row` → the histogram + aggregate-row
  draw paths in `analysis_plots.py` (only where entity ids exist; else leave as-is).
- `czi_seam.persistent_seam_columns` → the CZI open path as a **sampled, non-blocking** load-time QC
  warning (a handful of frames, never the whole movie).
- `feature_provenance.write_provenance_sidecar` → table-export points (sidecar JSON, consolidated table
  first).
- `condensate_modes.resolve_condensate_mode` → the invitro workflow: refuse `volume_fraction` in 2D
  (NaN+reason), emit in 3D, add the `condensate_mode` column.
Wire don't rewrite; additive (no existing number/pixel changes); degrade honestly. **Do NOT touch
`clean_spot_detection_tools`** — intentionally unwired, documented in-file.

---

## TIER C — capability (compose with landed platform pieces)

### C1. Spectral / bleed-through unmixing
**Verified missing:** no `unmix`/`linear_unmix`. `toolbox/unmixing_tools.py`:
`estimate_mixing_matrix(single_label_controls, *, background)` + `unmix(channels, M)`. The science is in
the rules: **M from single-label controls, never from the mixed data** (circular); background-subtract
before estimating M; **refuse an implausible/singular matrix with a reason** rather than inverting
garbage; report the negative fraction (the honesty check); clip for display only. Linear 2–4 channel
crosstalk only (not lambda-stack). Feeds honest coloc/ratio. Complements the landed ratiometric module.

### C2. Biological object model, increment 1
**Verified:** `EntityRef` has `parent_keys` + `make_entity_id` (parent-aware); analyses emit
`cell_label`. But no `BiologicalObject`/`ObjectGraph`. Build `utils/object_graph.py`:
`BiologicalObject` (key, entity_type, measurements, parent, children, provenance, qc_flags) +
`ObjectGraph` (get/children_of/parent_of/descendants/ancestors/filter) + `build_object_graph(tables)`.
**Read-only view** assembled from tables PyCAT already produces — reuses `EntityKey` exactly (no parallel
id scheme), changes no table, re-runs no analysis. Flat tables → flat graphs; orphans → explicit
unrooted bucket. Increment 1 = record + graph only; not the linked-navigation/state-vector vision.

---

## TIER D — engineering / release (the second engineering audit)

### D1. Python 3.13 enablement  *(STAGED + COLLABORATIVE — Gable verifies no regression)*
**Verified:** `requires-python=">=3.12,<3.13"`; only 3.12 classifier; `arm-mac` pins
`torch>=2.2.0,<2.3.0` with a **stale comment** citing a removed `numpy<2.0` pin.
- **Stage 0 (Claude Code, docs-only):** fix the stale ARM/numpy comment; document real base/extra/
  transitive/tested constraints.
- **Stage 1 (Claude Code):** documented 3.13 env recipe + base install + `pytest -m core`; deliver a
  report, not a merge.
- **Stage 2 (GABLE, real data/GPU):** Cellpose CPU+GPU vs 3.12 baseline, torch cu118 on the 1080s,
  numba recompile, **VPT viscosity vs ~8.325 (the canary)**, all file formats. Gable confirms match.
- **Stage 3 (Claude Code, only after confirmation):** flip to `<3.14`, add 3.13 classifier, adjust ARM
  pin per Stage-0 findings, add a 3.13 CI lane.
Gate for flipping: no validated numerical result differs between 3.12 and 3.13. Keep 3.12 supported.

### D2. Release-engineering hardening
**Verified:** ruff correctness has `|| true`; no pytest `pythonpath`; classifier Production/Stable;
`core` marker not minimal (pulls full deps).
- Make the **correctness ruff selection blocking** (remove `|| true` once observed green — the code's own
  comment says to); split blocking-correctness vs advisory-style/F841.
- Add `pythonpath=["src"]` **plus** a wheel-install CI lane (convenience backed by a packaging check).
- **Precise markers:** `core` (minimal/headless) / `base` (all base deps) / `gui` / `integration` /
  `optional` / `slow` / `gpu`; move `pywt`-class tests out of `core` into `base`.
- Add `pytest-qt` to `[test]` (kills the `qt_api` warning).
- Synchronize dependency comments (base vs extra-local vs transitive vs tested) — the ARM + BioFormats
  notes read as global but are extra-local.
- Classifier → `4 - Beta` until the install matrix + 3.13 lane are green.

### D3. Typed result models
**Verified missing:** `utils/result_models.py`. Frozen `AnalysisResult` (operation_id, source_layer_ids,
entity_type, measurements, artifacts, provenance, calibration) + `BatchStepResult`
(status Literal, outputs, warnings, error: PyCATError|None). **Envelopes composing existing types**
(FeatureProvenance, calibration record, PyCATError) — invent none. Produced at the result-finalization
chokepoint (shared with identity stamping). Adopt incrementally behind `to_dict()`/`from_dict()`;
migrate brushing, batch replay, publication export first. Validate at construction; frozen only.

---

## TIER E — decomposition / rigor (the fresh-scan specs, delivered separately 2026-07-20)
Already written as standalone specs this session: **vpt_decomposition**, **timeseries_decomposition**,
**scientific_exceptions**, plus the still-open **ui_builder_split** (the five 400–638-line `_add_*`
builders — verified 0 `_build_` helpers, untouched) and **science_function_split inc2+**. These drive
the complexity ratchet (126) and close the engineering audit's #11.

---

## Recommended sequence
1. **B1 session pixel-size** — correctness, small, standalone.
2. **A1 user-settings** — unblocks A3 and is broadly reusable.
3. **A2 quality-gate + navigator wiring** — makes the generator functional (the thing you asked to unblock).
4. **A3 navigator UI + feature registry** — makes the generator AND all invisible features visible.
5. **B2 orphan wiring** — finished work made reachable (some overlaps A3's feature registry).
6. **D1 Python 3.13 Stage 0** (quick) then the collaborative stages; **D2 release engineering** alongside.
7. **E decomposition specs** — interleave as appetite allows (safe, coverage-gated).
8. **C1 unmixing, C2 object graph, D3 typed models** — capability/architecture, as bandwidth permits.

Tier A is the highest-value cluster: it turns the built-but-headless workflow generator into something a
user can see and use, and simultaneously solves the invisible-features problem via the feature registry.
