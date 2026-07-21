## [1.6.236] - 2026-07-21
### Changed — **vpt decomposition step 2: the ensemble drift-correction domain moves out (byte-identical).**
`drift_correct_com` (subtracts common-mode stage/sample drift so it is not read as bead diffusion) and
`reclassify_by_temporal_stability` moved **verbatim** into `toolbox/vpt/drift.py`. `vpt_tools.py`
re-exports both; no number changed, drift tests pass unmodified.

- `drift_correct_com` is a registered navigator operation, so its catalog source updated from
  `vpt_tools.py` to `vpt/drift.py` — the generated `operation_catalog.json` was regenerated (2 lines).
- `vpt_tools.py` dropped **2585 -> 2429** lines; the per-file ceiling ratcheted to 2429. Moved keys in the
  drop-guard's `_DELIBERATE`.

## [1.6.235] - 2026-07-21
### Changed — **vpt decomposition step 1: the Stokes-Einstein viscosity domain moves to its own module (byte-identical).**
Begins decomposing the 2,834-line `vpt_tools.py` by domain. `viscosity_measurement`,
`viscosity_from_diffusion`, `viscosity_interval_from_diffusion` (and the `_K_BOLTZMANN` constant) moved
**verbatim** into the new `toolbox/vpt/viscosity.py`, alongside the existing VPT adapters.

- **`vpt_tools.py` re-exports** all three for every caller (vpt_ui, the viscosity-chain tests). Move, not
  rewrite — no number changed.
- **Byte-identical:** the golden-master viscosity chain (viscosity to 3.2%) and the route-equivalence tests
  pass unmodified; moved keys recorded in the drop-guard's `_DELIBERATE`.
- `vpt_tools.py` dropped **2834 -> 2585** lines; a new per-file ceiling established there that ratchets down
  as the remaining domains (detection, host, linking, drift, analysis) move out.

## [1.6.234] - 2026-07-21
### Fixed — **Three CI-hygiene / test-fixture correctness fixes (ci_hygiene_fixes).**
- **Stale CI coverage comment corrected.** `core.yml` claimed the `core` marker "selects only the two guard
  files, so coverage would be near-zero" — a fossil. Measured: the suite (~1,500 tests, 200+ marked files)
  covers **30% of pycat**. The comment now states the real number; coverage stays off in CI for a real
  reason (adds ~70% runtime with no report consumer), with the local command recorded.
- **tifffile fixtures pinned to `minisblack`.** Two `imwrite` calls in `test_lazy_sources_headless.py` wrote
  `(4,H,W)`/`(5,H,W)` arrays with no `photometric`, which a future tifffile default change could reinterpret
  as RGBA planes — a latent test time-bomb. Now explicit grayscale (verified under `-W error`).
- **`pywt` import deferred to function scope.** `from pywt import wavedecn, waverecn` moved out of
  `image_processing_tools` module scope into the one wavelet function that uses it, so the 8 toolbox modules
  that transitively import it no longer load PyWavelets on import (verified). Behaviour-identical.

## [1.6.233] - 2026-07-21
### Fixed — **A batch image whose consolidated-table append fails is no longer reported as a clean success (silent cohort corruption).**
`exception_context_classification` spec, increment 1 — the batch_step category and its concrete offender.
- **The bug:** `BatchWorker.run` marked an image `✓` even when adding its rows to `consolidated_long.csv`
  failed — so that image's rows silently vanished from the consolidated cohort while the batch reported
  success. A 93-of-100 cohort that looks complete is a silent scientific corruption. The success mark is now
  **gated** on the consolidated append succeeding; on failure the image gets a visible `⚠` partial status
  ("processed, but NOT added to the consolidated table — its rows are MISSING…"), so the drop is actionable.
  The per-image output folder is still complete; only the consolidated aggregation is flagged.
- **The category vocabulary:** `test_exception_budget.py` now recognizes the five handler categories
  (`ui_cleanup` / `optional_probe` / `scientific_result` / `write` / `batch_step`) and validates the category
  **when** a handler uses the `# broad-ok: <category> — <reason>` form — a typo'd category (a `write` that
  reads `writes`) fails, so it can't silently escape its standard. Legacy plain `# broad-ok: <reason>`
  handlers are unaffected; the batch handler above is annotated `# broad-ok: batch_step — …`.
- Tests (`core`): `test_batch_step_visibility.py` (AST-guards the gated success + visible partial — the loop
  is a QThread) and a category-validation case in `test_exception_budget.py`. **Follow-on** (recorded in the
  spec): the full ~166-handler category sweep, the writer-scoped swallow guard, and writer conversions.

## [1.6.232] - 2026-07-21
### Added — **A general object-quality gate (backlog A2, part 1) — block / warn / downgrade, with reasons.**
Every measurement has preconditions (a physical-unit result needs a real pixel size; a concentration needs a
valid calibration; a trusted number needs its reliability assessed), and those signals already exist — but
nothing composed them into one answer a navigator, batch run, QC advisor, and measurement UI could all
consult. New `utils/quality_gate.py` is that composer — **feature-agnostic, and it invents no new metric**;
it only combines `pixel_size`, `calibration.check_calibration_validity`, and `reliability`.
- `evaluate_quality(objects, requirement, *, context) -> GateResult`, where `QualityRequirement` declares
  what an op needs (`needs_pixel_size`, `needs_calibration`, `min_reliability`) and `context` supplies the
  signal inputs. Only the requested signals are consulted; the op that needs nothing is OK.
- Enforces the "refuse rather than lie" rules: **BLOCK ≠ WARN ≠ DOWNGRADE** (do-not-run / caveat-attached /
  reduced-claim); **an unassessed signal is never a silent pass** (it WARNs); **report, don't drop** — every
  signal's outcome is on the result and the overall verdict is the WORST of them (`runnable` is false only
  on BLOCK).
- Tests (`core`, `test_quality_gate.py`): missing pixel size blocks; unassessed pixel size / absent
  calibration warn; invalid calibration blocks and a warn-level warns; an unreliable grade downgrades; a
  below-floor grade warns; NaN reliability warns; the worst signal wins with all reported; and it composes
  the real `reliability` signal from raw inputs. **Remaining A2:** binding the measurement ops into
  `operation_catalog.json` with a `QualityRequirement` each, so the planner emits a measurement step as
  runnable only when the gate passes — the heavier, catalog-wiring half.

## [1.6.231] - 2026-07-21
### Added — **App mode + feature registry (backlog A3 substrate) — beginner/advanced, and the catalogue of "what can PyCAT do."**
The non-Qt foundation for the navigator/beginner-mode work, both consuming the 1.6.230 user-settings store
and both `core`-tested. The heavy beginner-home Qt dock is the remaining A3 piece.
- `utils/app_mode.py`: `AppMode` (BEGINNER/ADVANCED) persisted at `app.mode` — **first run is BEGINNER**,
  every later run is the last choice. `current_mode`/`set_mode`/`is_beginner`/`is_advanced`/`toggle_mode`
  and `on_mode_change(cb)` (fires on change). Owns no widget; any UI consults it. An unrecognized stored
  value degrades to BEGINNER. The store is injectable, so it tests without touching the real config.
- `utils/feature_registry.py`: `FeatureCard` (key, title, one-line summary, category, opaque `entry`
  callable, docs anchor, `min_mode`) + `FeatureRegistry` — the single answer to "what can PyCAT do, and
  where." A duplicate key is refused (two features can't claim one id); `visible_in(mode)` / `visible_now()`
  filter by app mode (a beginner sees the beginner set, an advanced user sees everything); `by_category`
  groups them. A **catalogue, not a launcher** — it never invokes `entry`, so it stays Qt-free. A future
  capability becomes discoverable the moment it registers one card.
- Tests (`core`): `test_app_mode.py` (first-run beginner, persistence, toggle, degrade, change-notify) and
  `test_feature_registry.py` (register/retrieve, duplicate refused, mode-visibility, category grouping).

## [1.6.230] - 2026-07-21
### Added — **Process-wide user settings (backlog A1) — one corruption-safe, atomic home for cross-session preferences.**
PyCAT had nowhere to remember a choice between sessions (no `QSettings`, no user-config file, no first-run
flag), so the navigator mode, acquisition profiles, plot-backend preference, and dismissed QC warnings had
nowhere to live. New `utils/user_settings.py` is that home — a general mechanism with **zero feature
knowledge**; each feature registers its own defaults and reads/writes its own namespaced keys.
- `UserSettings`: namespaced key/value store persisted to one JSON file in the OS user-config dir
  (`platformdirs.user_config_dir('pycat')`, home-dir fallback). Registered defaults (a stored value always
  wins), typed accessors (`get_bool`/`get_int`/`get_float`/`get_str`) that **coerce and fall back rather
  than raise**, `set`/`reset`, and `subscribe(key, cb)` firing only on an actual change. Process-wide
  instance via `settings()`.
- **Two startup-critical guarantees:** a corrupt settings file **never crashes** — it falls back to
  defaults and the bad file is quarantined (`.corrupt`), not deleted; and a write is **atomic** (temp file +
  `os.replace` + `fsync`), with `set()` transactional — a mid-write failure rolls back the in-memory change
  and leaves the previous good file intact.
- Tests (`core`, `test_user_settings.py`): cross-session round-trip, default resolution, typed coercion +
  fallback, change-only subscriptions, corruption-safety + quarantine, write atomicity, namespace
  non-collision. Unblocks the navigator/beginner-mode work (A3) and is reusable everywhere.

## [1.6.229] - 2026-07-21
### Added — **Spectral / bleed-through unmixing is now a UI step — the C1 capability is user-reachable.**
The linear unmixing shipped in 1.6.226 was API-only; it now has a toolbox step, so it is no longer an
invisible capability. **Menu:** Image Processing → Background and Noise Correction → "Spectral /
Bleed-through Unmixing (2–4 channels)"; it also appears in the exploratory workbench's Image Processing
section.
- New `_ImageOpsWidgetsMixin._add_run_spectral_unmixing`: pick a single-label CONTROL layer per channel
  (each a sample with only that fluorophore, imaged in all channels) + the mixed multi-channel image + a
  background offset, and Run. The **thin handler only reads layers and shows results** — the matrix
  estimation, the singular-matrix refusal, the unmix and the negative-fraction honesty check are all the
  `core`-tested `unmixing_tools`. Unmixed channels are added as `Unmixed C0…` layers; a high negative
  fraction and any matrix-plausibility warnings surface as napari messages; a refusal is shown, not crashed.
- Tests (`integration`, `test_unmixing_ui.py`): the step builds, is driven end-to-end (dropdowns → Run) and
  **recovers the true channel abundances** from a synthetic mixture; and with controls missing it warns and
  emits nothing. `menu_manager` / `ui_modules` held exactly at their line ratchets (net-zero registrations).

## [1.6.228] - 2026-07-21
### Added — **Typed result models (backlog D3) — a result crossing a boundary is a TYPE, not a bare dict.**
New `utils/result_models.py`: two frozen envelopes that formalize the results PyCAT passes between modules
(brushing, batch replay, plotting, export) as free-form dicts today — where a renamed key is a runtime
surprise, not a type error. **They compose existing types; they invent nothing.**
- `AnalysisResult` (operation_id, entity_type, source_layer_ids, measurements table, artifacts, provenance,
  calibration) — carries the `feature_provenance.FeatureProvenance` / `calibration.CalibrationCurve` PyCAT
  already produces. **Validates at construction:** non-empty ids, and `measurements` must be a DataFrame or
  None — never a bare dict (that is the drift the envelope exists to stop).
- `BatchStepResult` (status, outputs, warnings, error) — `status` is one of `ok/warning/error/skipped`, and
  `error` is a **typed `PyCATError`, never a string**. Status and error must AGREE: you cannot build a step
  marked `error` with no error, or one carrying an error but claiming success.
- Both are **frozen**; sequences coerce to tuples. `to_dict`/`from_dict` bridge to the dict form existing
  code speaks, so a producer can emit the typed object while a not-yet-migrated consumer still reads a dict
  (incremental, non-breaking adoption). The rich composed fields cross the serialization boundary in their
  dict form; the in-memory model holds the real typed objects.
- Tests (`core`, `test_result_models.py`): frozen + validation (unknown status, failed-step-without-error,
  stringly-typed error, measurements bare-dict all refused), dict-boundary round-trip, composed-dataclass
  serialization. Migrating the first consumers (brushing, batch, export) behind `to_dict`/`from_dict` is the
  follow-on — nothing breaks meanwhile.

## [1.6.227] - 2026-07-21
### Added — **A first-class biological object graph, increment 1 (backlog C2) — objects + parent/child, read-only.**
New `utils/object_graph.py`: every detected object as a persistent identity with a graph over it, instead of
a mask label plus a disconnected DataFrame row. **Reuses the existing identity — no parallel id scheme:** a
`BiologicalObject` is keyed on its `_pycat_entity_id` value (the canonical `EntityKey` string). **Read-only
view** assembled from tables PyCAT already produces — it changes no table and re-runs no analysis.
- `BiologicalObject` (key, entity_type, measurements, qc_flags, provenance, parent, children) +
  `ObjectGraph` with `get`/`parent_of`/`children_of`/`descendants`/`ancestors`/`roots`/`unrooted`/`of_type`/
  `filter` (cycle-guarded walks).
- `objects_from_table(df, entity_type, *, id_col, parent_id_col, measurement_cols, qc_col)` builds objects
  from one table; `build_object_graph(objects)` resolves the parent/child edges at construction.
- **Honest structure:** a flat table (no parent info) → a flat graph of roots; an object naming a parent NOT
  present → an explicit `unrooted` (orphan) bucket, never silently dropped or silently rerooted; roots and
  orphans are distinct. Rows without a stable id are skipped.
- Tests (`core`, `test_object_graph.py`): id-keyed objects + measurements, flat→roots, parent/child tree,
  descendants/ancestors, missing-parent→unrooted, of_type/filter, source table never mutated.
- Increment 1 is record + graph only. The schema-specific join that derives a punctum's parent-cell id from
  the cell-labelled-puncta convention, and the linked-navigation/state-vector vision, are later increments.

## [1.6.226] - 2026-07-21
### Added — **Linear spectral / bleed-through unmixing (backlog C1) — from controls, refusing to invert garbage.**
New `toolbox/unmixing_tools.py`: the general 2–4 channel form of the single-coefficient bleed-through knob
in `ratiometric_tools` (it complements it, does not replace it). Models each observed channel as a fixed
linear combination of the true fluorophore abundances, `c = M·a`, and recovers `a = M⁻¹·c`, with the science
in the rules — the same "refuse rather than lie" contract as the calibration and pixel-size gates:
- `estimate_mixing_matrix(single_label_controls, *, background)` — the mixing matrix comes from single-label
  CONTROLS, never the mixed data (that would be circular); background is subtracted BEFORE the crosstalk
  ratio is formed; a control dark in its own channel is refused (you cannot normalize by zero).
- `unmix(channels, M, *, background, condition_limit)` — **refuses a singular / ill-conditioned matrix**
  (`cond(M) > 1e6`) with a stated reason rather than pseudo-inverting it and amplifying noise into confident
  nonsense.
- `negative_fraction(unmixed)` — the built-in honesty check (a large negative fraction means the linear
  model is wrong); `clip_for_display` clips negatives **for display only**, never back into a measurement;
  `mixing_matrix_warnings` flags a likely swapped channel assignment or an over-subtracted background.
- Linear 2–4 channel crosstalk only — NOT a lambda-stack spectral unmix. Pure numpy, `core`-tested against
  synthetic mixtures with a known matrix (`test_unmixing.py`: control-based estimation, background removed
  first, mix→unmix round-trip, singular-matrix refusal, the negative-fraction honesty check).
- **Follow-on (tracked, not silent):** a toolbox UI step to select the controls + mixed image is not wired
  yet — the capability is usable via the API / batch today; UI surfacing waits on the feature-registry work.

## [1.6.225] - 2026-07-21
### Added — **The batch consolidated table now exports a provenance sidecar — wiring the orphaned `feature_provenance` export hook (backlog B2 complete).**
`feature_provenance.write_provenance_sidecar` was built and tested but had no caller, so a consolidated
table left no machine-readable record of what its features are or what software produced them. Now wired at
the batch export:
- `ConsolidatedLongWriter` tracks the feature vocabulary it writes and gains
  `write_provenance_sidecar()`, which emits `consolidated_long_provenance.json` beside the CSV — one entry
  per feature present, each carrying the software versions (filled automatically) and, for features in the
  measurement ontology, their units + definition. Nothing is fabricated: an unknown feature gets no guessed
  unit. No-op when no measurements were written.
- `batch_processor` calls it once after the consolidated table is finalized (additive, best-effort — a
  sidecar failure never fails the batch).
- Tests (`core`): the sidecar is written beside the table keyed by feature, ontology features carry
  units+definition while unknown ones do not, and the empty case writes nothing.
- **This completes backlog B2** (wire the actionable orphans): `condensate_modes` (1.6.223), `czi_seam`
  (1.6.224), and `feature_provenance` are now wired; `cohort_targets`'s histogram-brushing hook was already
  wired in the feature-explorer dock.

## [1.6.224] - 2026-07-21
### Added — **CZI opens now run a non-blocking mosaic-seam QC — wiring the orphaned `czi_seam` module (backlog B2).**
`file_io/czi_seam.py` (the pure, tested seam metric) was imported by nothing, so a returning tile-assembly
seam could go unnoticed. The streaming CZI open path now warns if one is present:
- New pure helpers in `czi_seam.py`: `sample_frame_indices` (a handful of evenly-spaced frames, never the
  whole movie), `seam_qc_message` (a one-line warning if the sampled frames share a persistent vertical
  seam, else None), `seam_qc_from_lazy_stack` (samples by **per-frame indexing** — `np.asarray` on the whole
  lazy stack is refused by design — row-subsamples each plane to stay cheap, needs ≥2 frames, degrades to
  None on any read/shape problem), and a thin `warn_seam_qc(stack, show_warning)` wrapper.
- `stack_openers._open_czi_streaming` calls `warn_seam_qc(lazy, napari_show_warning)` once per file
  (channel 0). **Best-effort and non-blocking:** a QC failure never breaks the open, and it reads a handful
  of planes, not the movie. It measures the seam; it does not fix or refuse the load.
- Tests (`core`): `test_czi_seam.py` gains the frame-sampling, message, lazy-stack (flags an injected
  persistent seam, clean → None, degrades safely, needs multiple frames), and an AST guard that the
  streaming loader wires it. No pixel/measurement changes.

## [1.6.223] - 2026-07-21
### Added — **The in-vitro field-summary tables now carry their `condensate_mode` — wiring the orphaned `condensate_modes` module (backlog B2).**
`toolbox/condensate_modes.py` was shipped and unit-tested but imported by nothing — so the "**2D
projection, not a volume fraction**" caveat lived only in a transient napari message while the number
travelled on into tables, the consolidated long table, and comparative figures with no qualifier attached.
Now wired at both in-vitro whole-field-summary emission points (fluorescence and brightfield):
- New pure `condensate_modes.annotate_summary_table(table, masks)` resolves the mode from the mask, adds a
  `condensate_mode` column, and — where a volume fraction is refused for that mode (a 2D field, or a time
  series with no Z) — adds a `volume_fraction_note` stating why. **Purely additive:** existing columns are
  untouched, so the byte-identical `field_summary` dict is never modified (its guard test still passes).
- `invitro_fluor_ui` and `invitro_bf_ui` each call it in one line after building `summ_df`, so the
  projection qualifier now rides IN the emitted/saved table instead of evaporating with the info message.
- Tests (`core`): `test_condensate_modes.py` gains the additive-annotation test (2D → mode `2d` + refusal
  note, originals untouched), the 3D case (mode `3d`, no refusal note — the volume fraction is real there),
  and an AST guard that both in-vitro handlers call `annotate_summary_table`. No measurement changes.

## [1.6.222] - 2026-07-21
### Fixed — **Status markers (the "logic gate" circles) now tell the truth: GREEN means DONE, not "ready".**
A tester found four ways the workflow status circles lied about readiness — and a lying marker undermines
the whole anti-black-box promise. All four are fixed in the SHARED mechanisms, so the fix applies to every
pipeline that uses them, not just the fluorescence one the tester used.
- **Green now means the step RAN, not "could run" (the systemic bug).** `button_with_circle` used the same
  solid green for "inputs satisfied" (ready) and "action completed" (done). Readiness now renders as a
  distinct **outlined amber ring** — it can never be misread as the solid-green done. The colour decision
  moved into a new Qt-free `utils/marker_logic.py::resolve_marker` (unit-tested headlessly): precedence
  done → ready → resting; `filled=False` only for ready.
- **Measure-line completes on Measure, not Draw.** The Draw→Measure→Clear line control is one cycling
  button, and the marker greened on *any* click — so it went green on the Draw click. `button_with_circle`
  gained `complete_on_click=False` + a `mark_done()` hook; the measure widget now marks done only from the
  Measure phase, success-gated on a real measurement (`_diameter_measured`).
- **Select-image labels turn green on a valid layer.** In `_layer_row`, a real (non-placeholder) layer
  that didn't match the dropdown's `name_hint` and wasn't manually picked (auto-defaulted) fell through to
  **red** — so a valid selection read as "nothing selected." A valid selection is now green (the hint is a
  suggestion, not a requirement); placeholder still reverts to red/yellow. Fixes all four selectors the
  tester named (they share `_layer_row`).
- **Optional-step colour convention decided and made explicit (kept blue).** Blue = "an optional step you
  completed" carries real information, so it stays — but its meaning is now stated in the tooltip and the
  `field_status` / `marker_logic` docstrings, applied uniformly to every optional action (e.g. upscaling),
  not left ad hoc.
- Tests: `tests/test_marker_logic.py` (`core`, Qt-free) pins the decision — ready is never solid green, a
  not-done marker is never solid green, done beats ready, only ready is outlined;
  `tests/test_status_markers.py` (`integration`) pins the widget wiring (ready-ring→green on run, cycling
  button no-green-on-click, optional→blue). No measurement or output changes — UX correctness only.

## [1.6.221] - 2026-07-21
### Changed — **condensate_physics decomposition COMPLETE: the tools file is now a pure re-export shim (byte-identical).**
The final domain — microrheology **moduli** (`per_track_msd_curves`, GSER + Evans G'/G'' estimators,
`extract_fusion_relaxation`) — moved **verbatim** into `condensate_physics/moduli.py` (it imports the MSD
path from the sibling `msd` module). No modulus number changed.

- **`condensate_physics_tools.py` is now a 122-line pure re-export shim** (from 2470, -95%) over the
  `toolbox/condensate_physics/` package: `msd.py`, `moduli.py`, `coarsening.py`, `relaxation.py`,
  `photobleaching.py`, `frame_quality.py`, `intensity.py`, `survival.py`. Every previously-public name is
  re-exported, so all callers (VPT, timeseries, dynamics UIs, navigator, trackmate) import unchanged; the
  now-unused module-level imports were removed from the shim.
- **Byte-identical across the whole decomposition** — the golden-master MSD->D->viscosity chain and every
  physics characterization test pass unmodified; moved keys recorded in the drop-guard's `_DELIBERATE`; the
  per-file ceiling ratcheted 2470 -> 122. Full `pytest -m core` green.

## [1.6.220] - 2026-07-21
### Changed — **condensate_physics decomposition step 4: the MSD / anomalous-diffusion domain moves out (byte-identical).**
The largest, most-checked domain — the MSD→D→viscosity chain the golden-master pins — moved **verbatim** into
`condensate_physics/msd.py`: `compute_msd`, `fit_anomalous_diffusion`, `msd_per_track`, `test_confinement`,
their `_short_track_rejections`/`_confined_msd`/`_lag_window_gate`/… helpers, and the
`MIN_TRACK_LENGTH_FRAMES` constant.

- **The tools module re-exports** the public entry points + `MIN_TRACK_LENGTH_FRAMES` + the constants/helpers
  the diagnostic tests read, so every caller (VPT, timeseries, dynamics UIs, `analysis_presets`) and the
  still-in-tools moduli functions (which call `compute_msd`) are unchanged.
- **Byte-identical — the golden-master MSD→D→viscosity chain (D to 1.1%, α to 0.1%, viscosity to 3.2%)
  passes unmodified**, as do the min-track-length diagnostics; two monkeypatch targets were updated to follow
  the moved symbols (assertions unchanged). Moved keys recorded in the drop-guard's `_DELIBERATE`.
- `condensate_physics_tools.py` dropped **1479 -> 602** lines; the per-file ceiling ratcheted to 602. Only
  the moduli section remains before the shim.

## [1.6.219] - 2026-07-21
### Changed — **condensate_physics decomposition step 3: three leaf domains move out (byte-identical).**
Three independent domains moved verbatim into the `condensate_physics/` package: `intensity.py`
(`fit_bimodal_intensity`, `intensity_decomposition_per_cell`), `survival.py` (`kaplan_meier_lifetimes`),
and `relaxation.py` (`fit_aspect_ratio_relaxation`). The tools module re-exports all four for every caller.

- Move, not rewrite — no fit or number changed; the intensity/survival/fusion tests pass unmodified. Moved
  keys recorded in the drop-guard's `_DELIBERATE`; two cross-module helper imports (`_bbox_cols`,
  `assess_fit`) carried into the new modules (caught by the undefined-name guard).
- `condensate_physics_tools.py` dropped **1802 -> 1479** lines; the per-file ceiling ratcheted to 1479.
  Remaining: MSD (the golden-master net) + moduli, then the shim.

## [1.6.218] - 2026-07-21
### Changed — **condensate_physics decomposition step 2: photobleaching + frame-quality domains move out (byte-identical).**
Two coupled domains — `analyse_frame_quality` calls `fit_photobleaching` — moved together into
`toolbox/condensate_physics/photobleaching.py` (`fit_photobleaching`, `apply_bleach_correction` + helpers)
and `frame_quality.py` (`analyse_frame_quality`, `detect_out_of_focus` + entropy/gradient/trend helpers).

- **The tools module re-exports all four** for every caller (condensate-physics/invitro/general-image UIs,
  the file_io stack-access probes). Move, not rewrite — no number changed.
- **Byte-identical:** the photobleaching characterization/window tests and the focus/debris tests pass; two
  test targets were updated to follow the moved symbols (a monkeypatch and a source-inspection module),
  assertions unchanged. Moved keys recorded in the drop-guard's `_DELIBERATE`.
- `condensate_physics_tools.py` dropped **2242 -> 1802** lines; the per-file ceiling ratcheted to 1802.

## [1.6.217] - 2026-07-21
### Changed — **condensate_physics decomposition step 1: the coarsening domain moves to its own module (byte-identical).**
Begins decomposing the 2,443-line `condensate_physics_tools.py` by physical quantity. `fit_coarsening` and
its phase helpers (`_coarsening_powerlaw_fits`, `_coarsening_is_arrested`, `_coarsening_confidence`) moved
**verbatim** into the new `toolbox/condensate_physics/coarsening.py`.

- **The tools module re-exports `fit_coarsening`** for every caller (condensate-physics/invitro/brightfield
  UIs, the navigator op-catalog, trackmate bridge). Move, not rewrite — no fit or number changed.
- **Byte-identical:** `test_fit_coarsening_output_is_byte_identical` and the arrest-classification tests
  pass unmodified; the moved nested keys are recorded in the drop-guard's `_DELIBERATE`.
- `condensate_physics_tools.py` dropped **2447 → 2242** lines; a new per-file ceiling established there that
  ratchets down as the remaining quantities (MSD, moduli, relaxation, frame-quality) move out.

## [1.6.216] - 2026-07-21
### Changed — **invitro decomposition COMPLETE: `invitro_tools.py` is now a pure re-export shim (byte-identical).**
The final domains — coarsening kinetics (`coarsening_statistics`), critical-concentration estimation
(`estimate_csat_lever_rule`), contact-angle geometry (`estimate_contact_angle`), fusion detection
(`detect_and_fit_fusions`), and sedimentation correction (`detect_sedimentation`) — moved **verbatim** into
`toolbox/invitro/analysis.py`.

- **`invitro_tools.py` is now an 88-line re-export shim** (from 2051, −96%) over the `toolbox/invitro/`
  package: `size_distribution.py`, `partition.py`, `field_summary.py`, `analysis.py`. Every previously-public
  name is re-exported, so all callers (invitro UIs, batch steps, timeseries, op-catalog) import unchanged.
- **Byte-identical:** no fit, background, threshold, or reported number changed across the whole
  decomposition; the existing in-vitro tests are the net. Moved keys recorded in the drop-guard's
  `_DELIBERATE`; the per-file ceiling ratcheted 2051 → 88. Full `pytest -m core` green.

## [1.6.215] - 2026-07-21
### Changed — **invitro decomposition step 3: the whole-field summary moves to its own module (byte-identical).**
`field_summary` and `_field_summary_metrics` — per-field droplet-size and phase-intensity statistics with
the honest-name result dict and its measured caveats — moved **verbatim** into
`toolbox/invitro/field_summary.py`. `invitro_tools.py` re-exports `field_summary` for every caller (invitro
UIs, batch steps, timeseries); no number changed, pinned by `test_field_summary_is_byte_identical` and the
enrichment/halo tests. `invitro_tools.py` dropped **799 → 605** lines; the per-file ceiling ratcheted to 605.
Remaining sections (coarsening, C_sat, contact-angle, fusion, sedimentation) follow.

## [1.6.214] - 2026-07-21
### Changed — **invitro decomposition step 2: the partition-coefficient domain moves to its own module (byte-identical).**
The calibration-sensitive quantitative core — `partition_coefficient_local` and its `_pc_*` phase helpers,
the assumptions-scoped `partition_measurement`, the no-cell-mask `partition_coefficient_field`, and the
dilution-series `estimate_phase_boundary` — moved **verbatim** into `toolbox/invitro/partition.py`.

- **`invitro_tools.py` re-exports** the four public entry points, so every caller (invitro fluor/BF UIs,
  `batch/steps/invitro_steps.py`, `timeseries_invitro_tools.py`) imports them unchanged. Move, not rewrite
  — no background handling, fit, or reported K_p changed.
- **Byte-identical:** `test_partition`, `test_partition_local_characterization`,
  `test_partition_measurement_characterization`, and the calibration/ΔG net pass. One test's monkeypatch
  target was updated to follow the moved `napari_show_warning` binding (assertions and values unchanged —
  the OVER-INCLUSIVE mask warning still fires). Moved keys recorded in the drop-guard's `_DELIBERATE`.
- `invitro_tools.py` dropped **1623 → 799** lines; the per-file ceiling ratcheted to 799. Remaining domains
  (field_summary, coarsening/C_sat, contact-angle, fusion, sedimentation) follow in later commits.

## [1.6.213] - 2026-07-21
### Changed — **invitro decomposition step 1: the size-distribution domain moves to its own module (byte-identical).**
Begins decomposing the 2,051-line `invitro_tools.py` by analysis domain. The MLE size-distribution path —
`fit_size_distribution_mle`, `fit_size_distribution`, and the phase helpers (`_fit_size_models`,
`_powerlaw_tail_comparison`, `_size_distinguishability`, `_size_verdict`) — moved **verbatim** into the new
`toolbox/invitro/size_distribution.py`.

- **`invitro_tools.py` re-exports** the two public entry points, so every caller (the invitro fluor/BF UIs,
  `batch/steps/invitro_steps.py`, the op-catalog api string) imports them unchanged. Move, not rewrite — no
  fit, threshold, or reported number changed.
- **Byte-identical:** `test_size_distribution_mle_characterization` and the invitro size tests pass
  unmodified; the moved keys are recorded in the drop-guard's `_DELIBERATE` set.
- `invitro_tools.py` dropped **2051 → 1623** lines; a new per-file ceiling was established there (1623) that
  ratchets down as the remaining domains (partition, field_summary, spatial, analysis) move out in
  follow-on commits.

## [1.6.212] - 2026-07-21
### Added — **A structural guard that every `_mpx` pixel-size accessor uses the ONE canonical helper (redundancy_consolidation axis 1).**
Pixel size scales every physical-unit measurement (viscosity, ΔG, size, density); a per-UI `_mpx()` that
re-derives it inconsistently silently corrupts units in one workflow but not another. Axis 1 of the
redundancy-consolidation spec routed every `_mpx` through the canonical `pixel_size_um_or_default`; this
adds the missing structural ratchet so it stays that way.

- New **`tests/test_pixel_size_single_accessor.py`** (core): an AST guard asserting every function named
  `_mpx` in the package references `pixel_size_um_or_default`, with a canary (a bespoke
  `dr.get('microns_per_pixel_sq') or 1.0` accessor is flagged; a routed one passes). Confirms all 10 current
  `_mpx` accessors route through the one helper. `test_pixel_size.py` pins the accessor's behaviour; this
  pins the structure — a new UI cannot quietly re-open the silent-units hole.
- No behaviour change. redundancy_consolidation axes 2–4 (background mechanics, worker lifecycle,
  stack-access) remain open.

## [1.6.211] - 2026-07-21
### Changed — **scientific_exceptions DONE: all 15 result-path handlers classified; none fabricate a default.**
Completing the scientific-exception guard: the remaining 11 broad handlers the AST guard flagged — in
`condensate_physics_tools` (5), `invitro_tools` (3), `vpt_tools` (3) — were classified and annotated
`# broad-ok:` with body-matched reasons.

- **The finding:** none of the five fit/measure modules fabricates a plausible default on failure. Every
  flagged handler reports the failure honestly — an all-NaN fit result with a `fit_success=False` flag, an
  explicit verdict string ("Power-law fit failed; confinement not assessed"), a fall-back to an
  already-measured value (the equivalent radius when the ellipse fit fails; the retained power law when the
  confined model fails), or an optional-backend/optional-check probe (CuPy version, intensity-semantics
  availability). So each was annotated, not converted — the correct action once classified by return value.
- The result-swallow ratchet is now **0** and the `toolbox` exception ratchet dropped **514 → 498** across
  the two increments. The guard catches any NEW broad handler that returns a fabricated scientific default.
- No scientific output changed; only failure-path documentation. Spec STATUS → DONE.

## [1.6.210] - 2026-07-21
### Added — **A guard against scientific modules silently returning a wrong NUMBER (scientific_exceptions Part 2).**
The exception-budget ratchet counts broad handlers; it does not ask what they *return*. A broad `except`
around a fit or calibration that then returns a plausible default is a silent wrong-number generator — the
audit's #4 concern. This adds the rule as a test and begins the classification.

- New **`tests/test_no_scientific_result_swallowing.py`** (core): an AST guard over the five fit/measure
  modules (vpt_tools, condensate_physics_tools, frap_tools, invitro_tools, partition_enrichment_tools) that
  flags any un-annotated broad `except` whose body directly `return`s a non-`None` value. Ratchet-style
  (pinned at today's count, only ever down) with a canary proving it flags a fabricated-default handler and
  passes a re-raising / None-returning / `# broad-ok:`-annotated one.
- **Measured inventory: 15 result-swallowing handlers** (far fewer than the 51 raw broad handlers — most
  re-raise or log). **frap_tools classified (4):** three return an all-NaN fit result AND warn the user (an
  honest missing value, not a fabricated default) and one is a documented degraded baseline fallback, so
  they are annotated `# broad-ok:` rather than converted. The scientific-swallow ratchet drops 15→11 and the
  `toolbox` exception ratchet 514→509.
- **Remaining (follow-on):** the 11 handlers in vpt_tools / condensate_physics_tools / invitro_tools —
  annotate the honest NaN-returners, convert genuine fabricated-default swallowers to typed raises. One
  module per commit. No scientific output changed; only failure-path documentation.

## [1.6.209] - 2026-07-20
### Changed — **Complexity ratchet 121 → 120: `field_summary` non-empty metrics extracted (byte-identical).**
The 182-line in-vitro whole-field summary — dominated by an ~80-line docstring and two large inline
measured-caveat comments — had its non-empty compute and result dict extracted into a helper, leaving the
orchestrator with the docstring, setup, and the empty branch. No number moved.

- **`_field_summary_metrics(props, image, bg_mask, cond_mask, microns_per_pixel, field_area_um2)`** carries
  the droplet-size and phase-intensity metrics and the honest-name result dict — with its deprecated
  aliases kept for back-compat and the measured caveats on what each quantity is and is not (the area
  fraction is a 2-D projection, not a volume fraction; the intensity ratio is not a partition coefficient;
  the dense/dilute contrast is pedestal-exact but not halo-immune). The `n == 0` empty branch (a different
  key set — no `intensity_ratio` / `dense_dilute_contrast`) stays in the orchestrator.
- **Pinned byte-identical** by a new `test_field_summary_is_byte_identical` — the exact populated dict and
  the empty branch — on a deterministic four-droplet scene (pure numpy/skimage, portable). The existing
  halo/contrast property tests pass unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 121 → 120 (the ratchet only moves down); recorded in the drop-guard's
  `_DELIBERATE` set.

## [1.6.208] - 2026-07-20
### Changed — **Complexity ratchet 122 → 121: `qc_focus` split into its result-branch phases (byte-identical).**
The 203-line focus/sharpness QC check — a big dispatch of result dicts with dense measured rationale — was
split into pure helpers, leaving the orchestrator with just the na/info branches. No number moved.

- **`_qc_focus_stack(a)`** — the 3D per-frame band-pass-energy branch (flags frames far below the median).
- **`_qc_focus_absolute(width, limit)`** — the single-image diffraction-limit verdict: the
  refuse-when-nothing-sharp path (a blurry cell can't hide a sharp punctum, but with no small object there
  is no evidence of focus) plus the deliberately wide gross-defocus screen (the step-vs-blob conversion
  constant makes an absolute ratio uncertain by ~1.5×; the comparative use across a dataset cancels it).
- **Pinned byte-identical** by a new `test_qc_focus_is_byte_identical` that exercises **all five** result
  branches (stack→warn, absolute→good, refuse→na, info, flat→na) on pure numpy/scipy inputs (portable) and
  asserts the exact status + value + diag scalars. The existing focus property tests pass unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 122 → 121 (the ratchet only moves down); recorded in the drop-guard's
  `_DELIBERATE` set.

## [1.6.207] - 2026-07-20
### Changed — **Complexity ratchet 123 → 122: `topology_metrics` basin-count phase extracted (byte-identical).**
The 192-line per-cell structural-envelope metric had its comment-dense basin-count phase — topological-
persistence peak counting with a range-vs-noise flat-field guard — extracted into a pure helper, leaving a
~55-line orchestrator (basic stats + connectivity). No number moved.

- **`_topo_basin_metrics(envelope, mask, image_noise)`** returns the basin-related keys (the caller
  `update`s them onto its dict), carrying the full measured rationale for why basins are gated by
  topological persistence rather than a bare `min_distance` or a global prominence gate. The dead
  `min_basin_distance` / `ball_radius` default computation (unused by the persistence method) was dropped in
  the move; the parameters stay in the signature.
- **Pinned byte-identical** by a new `test_topology_metrics_is_byte_identical` that feeds a **synthetic
  numpy envelope directly** (bypassing the GPU-routed rolling-ball), so the pure numpy/scipy metric is
  isolated and the golden values are platform-portable — exact basin count / persistence gate + list / cov
  / roughness / components / largest-frac on a peaked field (structure branch) and a near-flat field (flat
  branch, which omits `topo_noise_known`). The existing basin-count property tests pass unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 123 → 122 (the ratchet only moves down); recorded in the drop-guard's
  `_DELIBERATE` set.

## [1.6.206] - 2026-07-20
### Changed — **Complexity ratchet 124 → 123: `count_molecules_single` split by computational phase (byte-identical).**
The 214-line single-trace N&B molecule counter was decomposed into pure per-phase helpers, leaving a
~55-line orchestrator. No number moved.

- **Phases extracted:** `_estimate_pedestal_read_noise` (the two camera constants — pedestal and
  read-noise floor — read from the trace's own post-bleach tail, the dark reference) and
  `_fit_counting_nu` (the ν = variance-vs-mean slope fit: a free intercept when the trace has a noise
  floor, else through the origin, with the read-noise-corrected fallback). Each dense measured-rationale
  block moved with its phase.
- **Pinned byte-identical** by a new `test_count_molecules_single_is_byte_identical` — exact
  ν / N / bleach_r² / pedestal / read_noise_var / accepted / n_points on a clean trace (the through-origin
  branch) and a read-noise+pedestal trace (the free-intercept branch), so both ν-fit paths are covered.
  The existing accuracy and pedestal-removal property tests pass unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 124 → 123 (the ratchet only moves down); recorded in the drop-guard's
  `_DELIBERATE` set.

## [1.6.205] - 2026-07-20
### Changed — **Complexity ratchet 125 → 124: `fit_coarsening` split by computational phase (byte-identical).**
The 227-line coarsening-mechanism classifier (Ostwald ripening vs coalescence vs arrested) — the fifth and
last long physics-fit function in `condensate_physics_tools` — was decomposed into pure per-phase helpers,
leaving a ~35-line orchestrator. No number moved.

- **Phases extracted:** `_coarsening_powerlaw_fits` (the two `curve_fit`s + R²), `_coarsening_is_arrested`
  (the slope-test that decides whether the radius grew *at all* — a physical claim, never a fit statistic)
  and `_coarsening_confidence` (the seeded residual bootstrap + confidence tiers). The single
  `napari_show_warning` became a returned flag the orchestrator emits, keeping the helpers pure. Two
  provably-dead locals (`noise`, `r2_gap`) were dropped in the move.
- **Pinned byte-identical** by a new `test_fit_coarsening_output_is_byte_identical` — exact
  `preferred_mechanism` / confidence / R²s / rate constants / bootstrap agreement / radius change on
  Ostwald and arrested scenarios (the bootstrap is seeded via `default_rng(0)`, so its agreement is
  deterministic). The existing arrest-classification property tests in `test_coarsening_arrest.py` pass
  unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 125 → 124 (the ratchet only moves down); the decomposition is recorded in
  the drop-guard's `_DELIBERATE` set.

## [1.6.204] - 2026-07-20
### Changed — **Complexity ratchet 126 → 125: `link_trajectories_bayesian` split by computational phase (byte-identical).**
The 245-line Bayesian/Hungarian trajectory linker — which feeds every VPT viscosity PyCAT reports — was
decomposed into pure per-phase helpers, leaving a ~50-line orchestrator. Lifecycle/complexity only; no
number moved.

- **Phases extracted:** `_bayesian_cost_defaults` (resolve the None-defaulted cost params),
  `_start_new_tracks` (open tracks when none are viable), `_build_frame_cost_matrix` (the per-frame
  viable×detection cost block + death/birth/dummy structure) and `_apply_frame_assignment` (the Hungarian
  solve written back onto the DataFrame + active-track state). Two provably-dead locals (`_sigma2`, the
  unused `assigned_*` sets) were dropped in the move.
- **Pinned byte-identical** by a new `test_bayesian_linker_assignment_is_byte_identical` — it asserts the
  exact per-detection `track_id` + `link_cost` on a fixed scenario exercising births, ongoing links, a
  bridged dropout gap, velocity prediction and the area-consistency cost. The Hungarian solve is sensitive
  to the exact cost matrix, so identical output proves the construction was preserved; the existing
  purity/gap-closing/ambiguity property tests in `test_linkers.py` pass unmodified.
- `_MAX_LONG_FUNCTIONS` lowered 126 → 125 (the ratchet only moves down). The linker's helpers are
  allow-listed in the input-mutation guard (they write into the linker's OWN `active`/`df`, not a caller's
  array) and the decomposition is recorded in the drop-guard's `_DELIBERATE` set.

## [1.6.203] - 2026-07-20
### Changed — **One figure module: the deprecated `figure_publication.FigureSpec` shim is removed (figurespec_merge cleanup).**
The 1.6.192 merge made `figure_spec.FigureSpec` canonical but left `figure_publication.py` in place as the
home of the validated rendering primitives plus a deprecated `FigureSpec` shim that `figure_spec.refine()`
still constructed internally. This finishes the consolidation: one figure module, no deprecated duplicate.

- **Primitives folded into `figure_spec.py`** — `apply_spec`, `add_significance_bracket`, `export_figure`,
  `PUBLICATION_PALETTE`, `JOURNAL_COLUMN_MM`, `THEMES`, `_recolor_series` — now reading the canonical spec's
  field names directly (`x_label`/`y_limits`/`font_size_pt`/`journal_column`/`significance_brackets`).
- **`refine()` applies the canonical spec directly** via `apply_spec(fig, spec)` — no field-name mapping
  through a deprecated `FigureSpec`. Output is byte-equivalent (the merge changed the API surface, not
  pixels).
- **`figure_publication.py` deleted.** Its one external consumer — `plot_backend_pyqtgraph`'s
  `PUBLICATION_PALETTE` import — is repointed to `figure_spec`; the comparative-figures UI never referenced
  it.
- **Tests migrated:** `test_figure_publication.py` → `test_figure_spec_primitives.py` (repointed to the
  canonical spec; the deprecated class's redundant JSON-serialization tests dropped — the canonical
  round-trip is already covered in `test_figure_spec.py`); `test_figurespec_merge.py`'s two shim-dependent
  tests replaced with direct assertions on `refine()`'s output. Full `pytest -m core` green. Unblocks the
  not-yet-written `publication_features` and `explore_refine_export`.

## [1.6.202] - 2026-07-20
### Fixed — **Brushable plots tear down on close — figures, callbacks and subscriptions stop accumulating (plot_lifecycle Parts A/B).**
The audit found ">20 matplotlib figures" open during a session: `make_pickable` (the one integration every
brushable plot uses) connected two canvas callbacks, stored an overlay artist and a `LazyRefs` sequence on
the figure, and had NO teardown — and two sibling brushing helpers subscribed a CLOSURE the service holds
strongly (which Part C's weak-method net does not catch). Every plot window left all of that behind. Part C
(the `SelectionService` self-defense) shipped in 1.6.187; this is the UI half.

- **`make_pickable` now tracks its connection ids** and gets `dispose_pickable(figure)` — disconnect the
  pick/key/close callbacks, remove the selection overlay, drop the ref sequence, and `plt.close` the figure.
  Idempotent (a close signal can fire twice). Teardown is also wired to the figure's own `close_event`, so a
  closed window cleans up even without an explicit dispose. Selection *behaviour* is unchanged — lifecycle
  only.
- **`cohort_targets.attach_histogram_brushing` and `comparative_figures._attach_object_brushing` now return
  a `dispose`** that unsubscribes the (strongly-held) closure and disconnects its canvas cid — closing the
  subscription leak the weak-method net cannot reach.
- **Feature Explorer no longer leaks per column switch.** Its mini-histogram reuses one figure, and
  `fig.clear()` does not drop canvas callbacks — so every column switch used to leave another
  `button_press_event` cid and a stale subscription behind. It now disposes the previous brushing before
  re-wiring.
- **The suite's ">20 figures" warning is gone** — a `conftest` autouse fixture closes leftover pyplot
  figures after each test (test hygiene; runs after the body, so a test asserting on `plt.get_fignums()`
  mid-run is unaffected).
- `tests/test_plot_lifecycle_dispose.py` (core, headless via the Agg backend) pins it: a pick reaches the
  handler before dispose and not after; dispose closes the figure and returns `get_fignums()` to baseline;
  the overlay and refs are released; dispose is idempotent and safe on a never-pickable figure; the
  close_event wiring runs teardown; N open→dispose cycles do not grow the open-figure count; and the
  histogram-cohort `dispose` unsubscribes the closure across simulated column switches. Spec Parts A/B
  STATUS → DONE.

## [1.6.201] - 2026-07-20
### Added — **Sessions now persist the user's entered workflow parameters (session_persist_settings Part 2).**
A saved session recorded the layers, dataframes and calibration but NOT the workflow parameters the user
entered (thresholds, radii, method choices), so a reloaded session could not reproduce the analysis setup.
It now carries them — reusing the ONE parameter record that already exists rather than inventing a second
capture path.

- **The recorded batch config travels with the manifest.** `session_manifest.workflow_to_manifest_extra`
  serializes the batch processor's own config (its single source of parameter truth — the same dict
  `save_config` already writes) into a `workflow` manifest block; `write_session_outputs` attaches it on
  save. No recorded steps → no block, so an un-recorded session's manifest is byte-identical to before.
- **Restored into the processor on load.** `_read_session_payload` surfaces the `workflow` block into the
  payload (Qt-free, on the worker) and `_apply_session_payload` restores it into
  `central_manager._pycat_batch_processor.config` — so the reloaded session carries the exact recorded
  parameter set, available for replay and inspection in "Recorded Steps". Recording stays OFF (the restored
  steps are a completed recording, not a live one).
- **Backward-compatible both ways.** `workflow_from_manifest` returns None for a manifest written before
  this feature (or one that recorded nothing), so old sessions load unchanged.
- **Part 1 (manual pixel size) was already satisfied** — the manifest already stored and restored a
  user-entered pixel size with honest provenance. Added a regression guard test so a reload never silently
  drops the calibration the user typed.
- `tests/test_session_persist_workflow.py` (core) pins the serialize/deserialize pair, the full manifest
  round-trip, the loader payload surfacing + processor restore, the pre-feature back-compat (both read and
  apply leave old sessions untouched), and the pixel-size round-trip. Spec STATUS → DONE.

## [1.6.200] - 2026-07-20
### Fixed — **Clearing the workspace now resets every open method widget's fields (session_clear_reset Bug 2).**
Clearing reset the layers, the data repository and the status circles the clear path knew about, but left
each toolbox method widget's spin boxes and dropdowns showing the previous workflow's values — because each
UI builder created its own `FieldRegistry` (`ui/field_status.py`) as a local island the clear path could not
find. The fix is a central handle every registry registers with.

- **A Qt-free `FieldRegistryHub` (`utils/field_registry_hub.py`).** Holds the live field registries WEAKLY
  (a closed widget's registry drops out on its own — no leak, no resetting a dead widget) and resets them as
  one. One registry's `reset_all()` raising does not block clearing the rest. Being Qt-free, the clear
  plumbing and its tests never drag in PyQt5.
- **Coverage grows by construction, not by wiring.** `FieldRegistry.__init__` registers with the process-wide
  hub, so a new method widget's fields reset on clear without its builder remembering to opt in.
- **`_clear_everything` calls `active_field_registries().reset_all()`** — returning every open method widget
  to its "~fresh open" state. Which fields reset is `FieldRegistry.reset_all`'s existing decision: OPTIONAL
  and EXPERT fields return to defaults; a REQUIRED value the user supplied (no default) is left alone.
- `tests/test_field_registry_hub.py` (core) pins the mechanism — register/reset/return-count, weak-ref prune,
  idempotent registration, exception isolation, the singleton, `_clear_everything` calling the reset, and the
  auto-registration wiring (Qt, importorskip). The spec's Bug 2 STATUS is updated to DONE.

## [1.6.199] - 2026-07-20
### Fixed — **Session loader: the freeze fix is confirmed complete; the `ui_modules` session-map re-export is completed.**
Following up the session-loader spec's last open item (Bug 2, "the freeze"): the staged off-thread load is
**already implemented and tested**, so this closes the spec and fixes a related latent fragility surfaced
while verifying it.

- **The freeze fix (Part C) is done and Qt-free-tested.** `load_session` is split into
  `_read_session_payload` (the slow decode — `tifffile.imread` per layer, `pd.read_csv` per table — which
  **takes no viewer, so it structurally cannot create a layer**) and `_apply_session_payload` (the only
  half that calls `viewer.add_*`, on the caller thread). `load_session(use_worker=True)` runs the read on a
  worker via the tested `qt_worker.run_with_progress`; both "Load Session" handlers pass it. The read/apply
  split rides the tested main-thread-marshalling contract rather than unverified threading, and
  `tests/test_session_load_threading.py` pins it (reader takes no viewer / decodes into a payload / applier
  is the only half that adds layers / `load_session` round-trips unchanged). The spec STATUS is updated to
  COMPLETE.
- **Completed the `ui_modules` re-export.** `_SESSION_METHOD_SWITCH` / `_SESSION_METHOD_BY_DATA` /
  `_FileDropFilter` moved to `menu_manager` in the 1.6.149 decomposition, and the re-export comment promised
  they stayed importable from `ui_modules` — but the import line brought only `MenuManager`, leaving
  `ui_modules._SESSION_METHOD_SWITCH` resolvable **only by full-suite import order** (so
  `test_the_method_REGISTRY_wires_VPT_correctly` passed in the suite but failed in isolation). The re-export
  now includes all three, honoring the documented promise and making the test order-independent; the
  complexity ratchet is respected (`ui_modules.py` stays at its ceiling).

## [1.6.198] - 2026-07-20
### Added — **Resolution routes through the entity registry: views ask the one authority where an object is now.**
Completes the entity-registry spec. The registry authority (1.6.189) was populated at the identity
chokepoint (1.6.197); now the **consumer side** consults it. A view carries only the entity id and resolves
its *current* location through the registry, instead of trusting the bbox/frame/layer baked into the ref at
wiring time — which can go stale after a re-crop, a layer re-add, or a frame reindex. This closes the "a
row can carry correct identity with **stale** location" divergence end to end.

- New **`object_ref.location_from_registry(ref)`** — refreshes a ref's `EntityLocation` (bbox / frame /
  layer id / source) from `default_registry()` when the entity is known there. Both **`resolve_in_viewer`**
  (interactive navigation) and **`resolve_offline`** (batch crop) call it first, so an `update_location`
  propagates to every subsequent resolution — no per-view stale cache to chase. Per field, a registry
  `None` leaves the ref's own value in place; the registry may even supply a bbox the ref lacked.
- **Honest fallbacks** (the "a wrong location is worse than an admitted missing one" contract): a ref with
  no `entity_id`, or an entity the registry does not know (dataset closed / never registered), is returned
  unchanged — the ref's last-known location is used, never an invented one.
- New **`tests/test_selection_registry_routing.py`** (`core`): a stale ref is refreshed from the registry;
  an `update_location` propagates to resolution; an unknown/no-id ref is untouched; a registry `None` field
  leaves the ref's own value; and **`resolve_offline` crops the registry's current location, not the stale
  bbox** baked into the ref (the divergence test at the consumer side). The existing brushing and
  selection-service suites pass unchanged.

## [1.6.197] - 2026-07-20
### Added — **Entity registry populated at the identity chokepoint: id → current location, from one record.**
The registry authority shipped in 1.6.189 but nothing populated it (it was dependency-gated on the
auto-identity-stamping chokepoint, now landed in 1.6.196). This closes the "a row can carry correct identity
with **stale** location" divergence *at the source*: identity and location are now registered together from
one record at the same finalization point that stamps the id.

- New shared **`default_registry()`** in `utils/entity_registry.py` — the process-wide authority a view
  resolves location through, instead of caching bbox/layer/frame off whatever table it was handed.
- New **`entity_ref.populate_registry(table)`** — one stamped row → one `EntityRecord` binding the id to its
  current `EntityLocation` (bbox / layer id / frame / source), provenance, and dataset. It runs
  automatically inside `finalize_entity_table`, so **every chokepoint-stamped table also registers its
  rows** — identity and location co-generated (per-row frames included), never crossed. Additive and
  guarded: it never costs the caller their table, and an unstamped table registers nothing.
- New tests in **`tests/test_auto_identity_stamping.py`** (`core`): the chokepoint populates the registry
  with matching id + location; per-row frames resolve to records with their own frame (no divergence); an
  unstamped table registers nothing; and the shared default registry resolves a chokepoint-stamped row.
- **Remaining** (noted in the spec): routing `SelectionService` navigation through `resolve(id)` so views
  consult the registry as the location authority — a consumer-side change on the brushing/selection path.

## [1.6.196] - 2026-07-20
### Added — **Automatic entity-identity stamping via the result-finalization chokepoint.**
Manual `stamp_entity_ids` reached only **3 of many** object-producing paths; every new analysis was one
forgotten call away from silent row-position linking, and `operation_id` was a hard-coded string (an
interactive/batch divergence risk). Identity is now applied by **declaration** at a chokepoint, so coverage
grows by declaring a spec — not by scattering more stamping calls.

- New in **`utils/entity_ref.py`**: a frozen **`EntitySpec`** (entity_type + label/parent/frame columns), a
  declaration registry (`register_entity_spec` / `entity_spec_for`), and **`finalize_entity_table(table,
  operation_id)`** — the chokepoint. If the operation declares a spec, it stamps identity **and** location
  in one pass with the operation's real id; an undeclared operation is returned untouched (honestly
  row-position-linked); it is **idempotent**, so the automatic path and a manual call never double-stamp.
- **`operation_runner.execute` calls it automatically** on a DataFrame result, driven by the operation
  captured from `operation_context` (1.6.155) — so `operation_id` comes from the declaration/context, **not
  a hard-coded string**. A non-DataFrame result or an undeclared operation passes through unchanged; a
  finalization failure never costs the caller their result.
- **The 3 manual sites migrated** (cell / puncta / region props) to the declaration with **byte-identical
  ids** (proven by a parametrized equivalence test), and the top previously-unstamped producers
  (**condensate, tracks, colocalized objects**) now gain identity by declaration. Tracks declare
  `frame_column='frame'`, so a multi-frame table stamps each row with its **own** frame (the per-row-frame
  fix, 1.6.188, now flowing through the chokepoint).
- The entity-id **scheme is unchanged** (it is validated) — only *where and how reliably* it is applied.
- New **`tests/test_auto_identity_stamping.py`** (`core`): a declared operation is stamped automatically;
  `operation_id` flows from the declaration (same rows under two ops → different ids); per-row frames;
  identity + location co-generated; undeclared → untouched; the migrated sites are byte-identical; a
  previously-unstamped producer gains identity by declaring; idempotency; and the runner stamps at
  finalization (and leaves non-DataFrame/undeclared results alone).

## [1.6.195] - 2026-07-20
### Fixed — **headless-CI regressions: `core` tests must not pull in Qt.**
Six `core` tests passed locally (where PyQt5/qtpy are installed) but failed on the headless `core` CI
runner, violating the tier's contract ("no napari, no Qt — must pass headlessly"). Both were import-source
issues, fixed without any behaviour change:

- **`detect_beads_stack` (and its serial-path helpers) imported `iter_frames`/`materialize_stack` from the
  Qt-laden `file_io`**, which imports PyQt5 at module scope — so the new `test_detect_beads_stack_
  characterization` (added as the split's safety net) errored with `ModuleNotFoundError: PyQt5` in CI.
  These streaming helpers are actually *defined* in the headless `stack_access` (`file_io` only re-exports
  them), so `vpt_tools` now imports them from `stack_access` directly. `detect_beads_stack` no longer
  imports `file_io` at all (verified), and the detection table is byte-identical (40 detections, same
  order/coordinates).
- **`clear_all_without_saving(confirm=False)` imported `qtpy` unconditionally**, so a headless caller (and
  the `confirm=False` test) crashed without Qt. The import now lives inside the `if confirm:` block — only
  the path that shows a dialog needs Qt. The two tests that exercise the confirm *dialog* itself now
  `pytest.importorskip('qtpy')`, so they skip headlessly (as the suite's other Qt tests already do) rather
  than fail.

Full `pytest -m core` green locally (1395 passed); the headless CI path is now Qt-free for these paths.

## [1.6.194] - 2026-07-20
### Added — **Feature families: an organizing schema over the measurement layer.**
Measurements were emitted as a **flat** list of columns — `area`, `intensity_mean`, `viscosity`,
`ripley_l_max` all in one undifferentiated row, with no indication that the first is cheap geometry and the
last a material-state measurement requiring a fit. This adds the grouping the Feature Explorer, redundancy
analysis, and any "export only the geometry columns" action all want. **Purely additive — no emitted table
or existing ontology consumer changes.**
- `measurement_ontology.py`: new `FeatureFamily` str-enum (Geometry, Intensity, Partition, Material-state,
  Spatial, Colocalization, Topology, QC — a small, stable, canonical-order set), and `MeasurementDef` gains
  a `family` field **defaulting to `None`** (so nothing that reads the ontology breaks). The 22 populated
  ontology entries are assigned their family.
- New `utils/feature_families.py`: `classify_column(name)` resolves a column's family **ontology-first**
  (authoritative), then a **curated substring fallback** (a labelled guess), else `None` — and returns the
  `source` (`'ontology'` | `'inferred'` | `None`) so a guessed grouping is **never mistaken for a defined
  one**. `family_for_column` is the family-only accessor; `group_columns_by_family(columns)` partitions a
  table's columns into families in canonical order with an Ungrouped (`None`) bucket **last** — and drops
  nothing (the union of buckets equals the input). A genuinely ambiguous column stays Ungrouped, because a
  wrong family is more misleading than an absent one.
- Tests (`core`): `tests/test_feature_families.py` — additive default, every ontology family returned and
  marked `'ontology'`, substring fallback marked `'inferred'`, ambiguous → `None`/Ungrouped, canonical
  order preserved, nothing dropped, and str-enum JSON serialization. This is a *view* over existing
  columns; it does not reorganize the ontology module or the emitted tables.

## [1.6.193] - 2026-07-20
### Fixed — **Loading a session now REPLACES the workspace instead of stacking onto it.**
Loading a saved session used to load its layers/tables *on top of* whatever was already open
(`session_loader` called `open_image_auto(clear_first=False)`), so the previous dataset's layers, tables,
and identity references lingered underneath the restored one — two sessions coexisting in one workspace. A
session is a DOCUMENT, not an overlay: loading it now clears first.
- Both "Load Session" handlers in `ui/menu_manager.py` (the single-session picker `_load_discovered_session`
  and the multi-stem folder loader `_on_load`) now call `clear_all_without_saving(...)` **before**
  `load_session(...)`.
- The clear is **guarded, never silent**: if the workspace has layers it prompts (the same discard warning
  the Clear button uses), and `clear_all_without_saving` now **returns `True`/`False`** (cleared / user
  cancelled) so the handler ABORTS the load when the user declines — their current, possibly-unsaved work is
  never discarded without a yes. On an empty workspace there is nothing to clear and nothing to confirm.
- Tests (`core`): `tests/test_session_clear_load.py` — the return-contract (cancel → `False`, no clear;
  confirm → `True`, clears; `confirm=False` → clears unprompted) and an AST guard that **every**
  `load_session` caller clears first (Qt-bound handlers, verified structurally).
- Not addressed here (honest follow-on): the method-widget-reset-on-clear polish and the
  `session_persist_settings` spec (whose premise is stale — pixel size already persists via the session
  manifest); documented in their spec status blocks rather than implemented redundantly.

## [1.6.192] - 2026-07-20
### Changed — **The two FigureSpec systems merged behind one canonical spec; significance now rendered.**
`utils/figure_spec.py::FigureSpec` and `utils/figure_publication.py::FigureSpec` overlapped but differed —
a feature added to one was absent from the other, and no module knew which to use. Merged behind
`figure_spec.FigureSpec` (the canonical spec):
- The canonical spec **absorbs** the publication fields (journal `column`, `height_mm`, `theme`, `recolor`,
  `tick_format`, `significance_brackets`, `title_size_pt`) — **each defaulting off/None, so a spec that
  sets none of them renders EXACTLY as before**: the merge is pixel-equivalent for existing usage by
  construction.
- **`figure_spec.render()` now HONOURS significance** (the verified gap — the working bracket
  implementation lived only in `figure_publication`); a spec requesting brackets gets them, still driven by
  caller-supplied replicate-level pairs, never a pixel-level inference.
- New `figure_spec.refine(fig, spec)` applies theme / journal sizing / ticks / recolour / brackets to an
  already-rendered figure by **reusing the validated `figure_publication.apply_spec`** — so the output is
  byte-for-byte what the publication path always produced (the merge changes the API surface, not the
  pixels). Unit is inches internally; mm is converted at the journal-width boundary.
- `figure_publication.FigureSpec` is now **deprecated** (marked in its docstring) but fully functional —
  existing consumers keep working until they migrate.
- Tests (`core`): `tests/test_figurespec_merge.py` — the canonical spec carries every capability, default
  render is unchanged, render honours brackets, `refine` matches `apply_spec`, the JSON round-trip carries
  the new fields, and the deprecated shim still works; the existing `test_figure_spec` / `test_figure_
  publication` pass unmodified. Migrating the two consumers off the deprecated shim (then removing it) is
  the follow-on; nothing breaks meanwhile.

## [1.6.191] - 2026-07-20
### Changed — **Identity integration: `dataset_id_for` now returns a durable UUID for a readable file.**
Wires the dataset-identity registry (1.6.190) into entity identity: `dataset_id_for(path)` resolves a
**readable** file to its persistent UUID (surviving a move/remount/cross-platform, via the fingerprint
registry) instead of embedding the fragile PATH in every entity id. An absent or unreadable path — a test
fixture, a batch replay of a relocated file, a registry outage — falls back to the path string, so it is
**backward-compatible**: nothing that stamped a non-existent path changes, and a durable id never costs
the caller their table (any failure degrades to the path).
- `dataset_identity.default_registry()` is the process-wide registry (persisted to `~/.pycat/
  dataset_registry.json`, so a dataset keeps its UUID across sessions); `uuid_for_path()` is the
  readable-file → UUID entry point `dataset_id_for` routes through, cheap on a repeat (a known path returns
  its UUID without re-fingerprinting).
- Tests (`core`): `tests/test_dataset_identity.py` — a readable file's dataset id is a durable UUID and is
  stable; an absent path keeps the path-as-id (back-compat). Full core green.
- Follow-on: migrating OLD path-based session ids to UUIDs on load (rewrites ids in saved dataframes), and
  populating the entity registry / routing SelectionService navigation at the stamping chokepoint — the
  deeper wiring these mechanisms now enable.

## [1.6.190] - 2026-07-20
### Added — **Persistent dataset identity: a durable UUID + cheap fingerprint (path becomes a location).**
Dataset identity was the file PATH, which breaks on move / remount / cross-platform / copy / temp-cache —
and can resolve to the WRONG dataset if two files share a path. New **`utils/dataset_identity.py`**:
- `DatasetIdentity` (uuid + original_path + fingerprint) and `DatasetFingerprint` (size, mtime, ome_uuid,
  partial_hash); `DatasetRegistry.mint_or_recognise(path)` — exact-path hit reuses the UUID; a path miss
  that **fingerprint-matches** an existing dataset reuses THAT UUID with the updated path (identity
  survives a move); no match → a fresh UUID. Persisted to a small JSON sidecar so the same file gets the
  same UUID across sessions.
- **Cheap and honest:** `bounded_partial_hash` samples head + interior + tail (reads kilobytes, never the
  whole multi-GB file — measured in a test); the OME UUID is authoritative when present; a borderline
  match (same size, different bytes) becomes a **NEW dataset, never a merge** (merging two datasets'
  identities is far worse than a fresh UUID).
- Tests (`core`): `tests/test_dataset_identity.py` — same file → same UUID, moved file recognised keeps its
  UUID, borderline is new, OME UUID authoritative, the bounded-read measurement, and cross-instance
  persistence.
- The integration — routing `entity_ref.dataset_id_for` through the registry so `dataset_id` becomes the
  UUID (which changes what every entity id embeds and needs a one-time migration of old path-based session
  ids) — is the deliberate follow-on; this ships the mechanism.

## [1.6.189] - 2026-07-20
### Added — **Entity registry: resolve an entity id to its CURRENT location through one authority.**
A row carried an opaque `_pycat_entity_id` for equality AND separate location columns (bbox, layer id,
frame, source) generated independently — so it could carry correct identity with STALE location. New
**`utils/entity_registry.py`** closes that divergence:
- `EntityRecord` binds an entity's id, `EntityLocation` (bbox/layer/frame/source), provenance, and dataset
  in ONE record, so identity and location cannot be generated separately and drift apart.
- `EntityRegistry` — `register` / `resolve(id) → EntityRecord | None` / `update_location(id, …)` /
  `invalidate_dataset(uuid)`. A view holds only the id and resolves location through this authority, so a
  location change (a labels layer re-added, a re-crop, a frame reindex) is seen by every view at once, with
  no stale local cache to send it to the wrong place.
- **Resolution fails HONESTLY:** `resolve` returns `None` for an unknown id or a closed dataset — a view
  shows "cannot locate" rather than navigating to a guessed, wrong place (a wrong target is worse than an
  admitted miss).
- Tests (`core`): `tests/test_entity_registry.py` — register/resolve round-trip, unknown → None, the
  divergence test (`update_location` changes every subsequent resolve), `invalidate_dataset` → honest miss,
  identity+location in one record, re-register replaces.
- Additive: the location columns stay (tables remain standalone-readable) as a registry-backed cache.
  Wiring the registry's population into the identity-stamping finalization chokepoint and routing
  `SelectionService` navigation through `resolve` depends on the auto-identity-stamping mechanism + the
  dataset-UUID work; this ships the authority they will populate.

## [1.6.188] - 2026-07-20
### Fixed — **Entity identity: `stamp_entity_ids` now derives the frame PER ROW (auto-identity §C1).**
`stamp_entity_ids` took a scalar `frame` and stamped every row with it — correct for a single-frame table,
but for a multi-frame table (a tracked-object / time-series table where the same label recurs across
frames as DIFFERENT entities) it collapsed those distinct entities onto ONE id. Added `frame_column=`: when
present, identity derives its frame per row from that column; when absent, the scalar `frame` is used
exactly as before (back-compat). This is the concrete Part C1 fix from the auto-identity-stamping spec.
- Tests (`core`): `tests/test_entity_ref.py` — the same label in different frames now yields distinct ids
  (four (label, frame) pairs → four ids); a single-frame table with no frame_column is unchanged.
- The larger auto-identity mechanism — stamping automatically at the operation-runner finalization
  chokepoint driven by an `EntitySpec` on the `OperationSpec` (so coverage grows by declaration, not by
  new stamping calls) — is the architectural remainder tracked in the spec.

## [1.6.187] - 2026-07-20
### Added — **Plot/view lifecycle: SelectionService self-defense so subscriptions do not accumulate.**
A long session accumulated matplotlib figures and `SelectionService` subscriptions, and every selection
broadcast walks the subscriber list — an unbounded list is the lag source. The service already held
bound-method subscribers WEAKLY (a closed dock's method dies) and dropped dead handles on broadcast; this
adds the self-defense the leak finding demands, and makes it testable:
- `subscriber_count(*, include_deferred=True)` — the count of LIVE subscribers (dead weak handles pruned
  first), so a test can assert it returns to baseline across open/close cycles;
- `_prune_dead()` — a proactive sweep dropping subscribers whose weak handle has died (a view that closed
  without unsubscribing), so liveness does not have to wait for the next broadcast.
- Tests (`core`): `tests/test_selection_lifecycle.py` — the leak test (50 open→dispose cycles return to
  baseline), a closed view's bound method is pruned from the count, a broadcast never calls a
  garbage-collected subscriber, idempotent double-unsubscribe, and the deferred channel counted/pruned too.
- The per-view `dispose()` (disconnect canvas callbacks + close the figure) is the UI half; the
  weak-method design already covers the closed-dock subscription leak, and this is the service-level safety
  net that catches a missed one.

## [1.6.186] - 2026-07-20
### Added — **Analysis-aware kymographs — a line-scan over time paired with the measurements PyCAT computes.**
A roadmap capability with no implementation. New **`toolbox/kymograph_tools.py`**:
- `kymograph(stack, line, *, axis='time'|'depth', width_px, reduce, pixel_size_um, frame_interval_s)` — samples
  intensity along a line in every frame and stacks the profiles into a (position × time/depth) image. The
  stack is read with **`materialize_stack`, never `np.asarray`** — a lazy time-series `__array__` returns
  frame 0 only, and a kymograph is the worst place to hit that landmine (guarded by a test). Axes are
  labelled in µm / seconds only when calibrated, px / frame otherwise, and the `units` field says which;
  the averaging-band width is recorded.
- `colocalization_kymograph` — two channels' kymographs + the **per-time-slice Pearson** (the existing
  coloc metric); `object_property_kymograph` — a tracked object's property vs time from the per-object
  table.
- Tests (`core`): `tests/test_kymograph.py` — band velocity recovered from the slope, the lazy-stack
  guard, calibrated vs px/frame labels, per-slice Pearson matches an independent computation, a
  shrinking-diameter trend, and wide-band noise reduction without a slope shift. FRAP / phase-boundary
  variants and the draw-line UI are noted follow-ons.

## [1.6.185] - 2026-07-20
### Added — **Measurement ontology populated: Tier 2 geometry/intensity entries (transcribed, not invented).**
The ontology's machinery was well-built but Tier 2 (common `regionprops`-derived geometry/intensity) was
absent, so the Feature Explorer / figure labels / reliability captions showed `None` for the most-emitted
columns. Added six entries, each transcribed from scikit-image / the code with a real equation and units:
- `area` (`A = N_px × pixel_size²`), `equivalent_diameter` (`√(4A/π)`), `eccentricity`
  (`√(1 − b²/a²)`), `solidity` (`area/convex_area`), `intensity_mean`, `intensity_total`.
- **Units honesty:** the size entries carry `µm²`/`µm` with the caveat that the value is px²/px when no
  pixel size is set (calibration-dependent, not asserted); the intensity entries are `a.u.` with the
  offset/gain caveat. Every entry has a definition + equation + units (the well-formed guard), and every
  emitted key appears as a real column in the source (the no-orphan guard) — so the registry cannot fill
  with aspirational or blank claims. (Tier 1 scientific measurements were already present.)
- Transcribe-never-invent honoured: no reference is attached without the equation it supports.

## [1.6.184] - 2026-07-20
### Added — **SMLM / localization-table analysis — load, normalize to µm, feed the spatial stats that exist.**
The lab has super-resolution instruments and had no localization-table analysis — but the hard part (the
spatial statistics `ripleys_l` / `pair_correlation_function` / `nearest_neighbour_distance` /
`local_object_density`) already exists. This is the front door. New **`toolbox/smlm_tools.py`**:
- `load_localization_table(path, *, format='auto', pixel_size_um=None)` → a µm-normalized
  `LocalizationSet`. **Units are the whole risk:** detected from the column header (ThunderSTORM's
  `x [nm]` → µm) and REQUIRED explicitly (`pixel_size_um`) when the columns are bare — never guessed, the
  same gate images enforce. The same pattern in nm vs µm gives identical downstream stats (pinned).
- `temporal_merge(locset, *, radius_um, gap_frames)` — collapse a blinking molecule's repeated
  localizations to one, with `analyze_localizations` **warning that un-merged data OVER-COUNTS density**
  (never silently merged or not).
- `analyze_localizations(locset, *, cell_area_um2)` runs the existing Ripley/PCF/NN/density backend and
  reports the **median localization precision (nm) as the resolution floor** below which clustering is not
  real.
- Ontology entries added (`median_localization_precision_nm`, `ripley_l_max`, `nn_median`) with the
  precision-floor and blink-over-count caveats.
- Tests (`core`): `tests/test_smlm_tools.py` — nm→µm loading, the ambiguous-units gate, nm/µm identical
  stats, clustered-vs-random through the loader, temporal-merge reduces the over-count + warns, the
  precision floor, and the no-x/y-columns error. A points layer + a load UI are the thin follow-on.

## [1.6.183] - 2026-07-20
### Changed — **detect_beads_stack decomposed by pipeline stage (317 → 116 lines); VPT-baseline byte-identical.**
`detect_beads_stack` is the shared detection stage feeding the whole VPT viscosity chain (the ~8.325
baseline through TrackMate), and the most scientifically load-bearing function in VPT — so it was
GUARD-ANCHORED, not merely characterized. Split by pipeline stage into pure/worker helpers:
- `_choose_detection_backend` — the GPU / CPU-process-pool / serial tier selection (each costed for the
  stack; the equivalence guards pin that all three produce identical blobs);
- `_pool_predetect` — the process-pool coordinate pre-detection with its progress mapping;
- `_bead_hot_mask` — the hot-pixel-reject sensor mask;
- `_detect_all_frames` — the per-frame streaming loop, calling `_fast_frame_rows` (template + scoring) and
  `_precise_frame_rows` (Gaussian fit);
- `_assemble_detections` — the DataFrame build, `classify_beads`, and class filters.
- **Detection numerics, path outcomes, and output ORDER are untouched** (downstream linking is
  order-sensitive). The existing VPT equivalence guards (`test_vpt_gpu_equivalence`,
  `test_vpt_parallel_equivalence`, and the memo) pass unmodified, and a new serial-path characterization
  (`tests/test_detect_beads_stack_characterization.py`) pins the exact detection table — coordinates,
  order-sensitive hash, area, counts — on a seeded synthetic stack (the GPU-equivalence test skips without
  a GPU, so the serial path is now guarded on every machine). Revert is a clean single-function rollback
  if the baseline ever regresses. Complexity ratchet 127 → 126; truncation guard allowlisted.

## [1.6.182] - 2026-07-20
### Added — **Ratiometric / two-channel intensity-ratio analysis — the traps handled, not just the division.**
Multichannel confocals produce two-channel data and PyCAT can segment the objects, but there was no
ratiometric module — and a naive `A/B` is riddled with traps. New **`toolbox/ratiometric_tools.py`**:
- `ratio_image(N, D, *, background_num, background_den, threshold, mask)` → a `RatioResult` (the ratio
  image with `NaN` where the denominator is too small, the fraction thresholded, and the backgrounds /
  threshold used); `object_ratios(labels, N, D, …)` → a per-object table.
- **Background BEFORE ratio, always** — `(N−b_N)/(D−b_D)`; an un-subtracted offset bends the ratio toward
  1 (the pedestal test proves it). **Low-denominator pixels become `NaN`, not spikes**, and the excluded
  fraction is reported. **Both summary modes** — `ratio_of_means` (aggregate, robust) and `mean_of_ratio`
  (per-pixel, heterogeneity-sensitive) — are reported and labelled; neither is silently chosen. An optional
  **bleed-through coefficient** corrects `D − c·N` (no automatic unmixing); with none supplied the result
  is flagged uncorrected so the caller can warn.
- `ratio` registered in the **measurement ontology** with its equation and the background-first,
  mean-of-ratio, bleed-through, and thresholding caveats.
- Tests (`core`): `tests/test_ratiometric.py` — known-ratio recovery, the pedestal test, low-denominator →
  NaN with reported fraction, mean-of-ratio vs ratio-of-means (agree uniform / differ heterogeneous),
  bleed-through bias-toward-1 + coefficient correction, and the ontology entry. The tagged ratio LAYER and
  the channel-picker UI are the thin follow-on; the computation is delivered.

## [1.6.181] - 2026-07-20
### Changed — **partition_measurement: background-subtracted assessment extracted (191 → 110 lines), byte-identical.**
The Kp measurement-with-assumptions builder was dominated by its background-subtracted assessment — the
argument (and the branching) that the image cannot tell a camera pedestal from a genuine dilute phase (in
a partition measurement the dilute phase IS the denominator, so it is not a background to remove), so the
assumption is RESOLVED by a dark reference, stated by the caller, or recorded UNCHECKED rather than
guessed. That phase moved to `_partition_background_assumption(dark_reference, background_subtracted,
floor, dilute) → (checked, holds, detail)`, leaving a 110-line builder.
- **Behaviour-preserving, pinned as such.** `tests/test_partition_measurement_characterization.py` captured
  which branch fires and the exact `checked`/`holds`/`detail` of the `background_subtracted` assumption
  across all four inputs (dark reference / not-stated / stated-true / stated-false), plus the other
  assumptions and the measurement identity, before the split and asserts them unchanged after; the existing
  `test_claim_scoping` passes unmodified. Complexity ratchet 128 → 127; truncation guard allowlisted.

## [1.6.180] - 2026-07-20
### Changed — **fit_fusion_relaxation decomposed by phase (184 → 90 lines), proven byte-identical.**
The droplet-fusion relaxation fit fused the single-exponential-plus-drift curve fit, the tau confidence
interval, the observation-window adequacy check (a record shorter than ~3 τ biases τ low, which biases
η/γ by the same factor, and R² cannot see it), and a two-mode-relaxation test. Split into pure phase
helpers, each carrying its rationale:
- `_fusion_tau_ci` — the 95% CI on τ from the fit covariance;
- `_fusion_window_warn` — the relaxations-observed count (measured on the span, not the circular fitted τ)
  and the short-record warning;
- `_fusion_model_adequacy` — the residual runs test + the direct two-mode test (which catches 100% where
  the runs test catches ~62%, the drift term absorbing part of the slow mode).
- **Behaviour-preserving, pinned as such.** `tests/test_fusion_relaxation_characterization.py` captured the
  fitted parameters, R², the τ CI, the relaxations-observed count, the adequacy/two-mode verdicts, and
  WHICH warnings fire across adequate / short / two-mode / too-few-points traces before the split and
  asserts them unchanged after; the existing `test_fusion_physics` passes unmodified. Complexity ratchet
  129 → 128; truncation guard allowlisted.

## [1.6.179] - 2026-07-20
### Added — **FilterStore: the active analytical population, provably separate from selection.**
Two questions were tangled: *which entities am I examining?* (selection — transient attention) and *which
entities are in the active analysed population?* (filter). `SelectionState` answered the first; nothing
held the second as explicit state, so filtering happened inside individual analyses and a plot click could
surprise a user by changing results. New **`utils/filter_store.py`**:
- `Filter` (a NAMED restriction — `predicate` + `members` + `source` + `active`) and `FilterStore`
  (`set_filter` / `clear` / `population` / `is_active`) on its OWN change channel, reading and writing no
  selection state. `population()` is `None` when no filter is active — None means everything, never
  confused with an empty population.
- **The isolation invariant is the whole point, and it is enforced:** a filter change leaves
  `SelectionState` untouched and a selection change leaves the filter untouched (pinned both directions),
  plus a grep-level contract that no selection handler calls `set_filter` — a brush/click is attention,
  not a population change.
- `resolve_render_tier` — the four-tier emphasis resolver (excluded < filtered-in < selected < pinned)
  adapters call; `filtered_result_note` — a filtered result records its predicate + counts so it is never
  mistaken for an unfiltered one; `filter_table` — restrict to `population()`, never silently, input never
  mutated.
- Comparative-phenotyping "condition" is deliberately NOT merged into `FilterStore` here (noted for a
  future deliberate unification).
- Tests (`core`): `tests/test_filter_store.py` — population API, both isolation directions, the four tiers,
  the honest filtered-result note, `filter_table`, and no-implicit-filtering. The thin UI integration
  (per-adapter tier rendering + an explicit filter control) is the follow-on; the mechanism is delivered.

## [1.6.178] - 2026-07-20
### Added — **Feature Explorer: one legible card per measurement, aggregated from existing sources.**
The measurement platform (ontology, reliability/MRI, stability, redundancy, provenance) computed a lot but
had no single pane that made it legible. New **`utils/feature_explorer.py`** — `FeatureCard` +
`build_feature_card(table, key, *, context)`:
- pulls a measurement's definition / equation / units / caveats (ontology), reliability grade + worst-first
  reasons (MRI), stability verdict, correlated-with columns (the redundancy report), provenance summary,
  and a value distribution (mini-histogram);
- **aggregates, never recomputes** — every field is read from `context` (whatever ran) and degrades to
  `None` when its source is absent; only the distribution is derived, from the column itself. A card with
  just a definition, or just a distribution, is a correct (honest) card;
- mutates nothing.
- Thin dock (`ui/feature_explorer_dock.py`): a searchable column list + the card panel + a mini-histogram
  that reuses the 1.6.170 cohort emitter (`attach_histogram_brushing`), so a bin click selects those
  objects. All content comes from `build_feature_card`; the dock is a shell.
- Tests (`core`): `tests/test_feature_explorer.py` — a full card when all sources are present, a partial
  card with no ontology entry, per-source degradation with nothing fabricated, a distribution that matches
  an independent histogram, a correlated-with list that matches the redundancy report, the no-mutation
  contract, and an AST guard that the dock wires the assembler + the cohort histogram. (Live rendering
  needs an in-app glance.)

## [1.6.177] - 2026-07-20
### Added — **Feature redundancy analysis: find near-duplicate feature columns, report them, never drop them.**
PyCAT emits wide feature tables where several columns track the same underlying quantity (`area`,
`convex_area`, `equivalent_diameter` all track size), silently giving a downstream PCA/classifier four
copies of "size" four times the weight. Nothing reported it. New **`toolbox/feature_redundancy.py`**:
- `analyze_redundancy(table, *, method='spearman', threshold=0.95)` → a `RedundancyReport` (the |r| matrix,
  the redundant-column groups, one chosen representative per group with the reason, the droppable columns,
  and the excluded columns with reasons).
- **Spearman by default** — morphometric relationships are often monotonic-but-not-linear (area vs
  diameter is a square law) and Pearson understates that redundancy.
- **Transitive clustering, not pairwise dropping** — correlation-distance average-linkage clustering, so
  A~B, B~C groups all three even when A~C is just under threshold; an order-dependent pairwise drop would
  not.
- **The representative is chosen, not arbitrary** — an ontology-defined column beats a derived one, else
  the most complete (fewest NaNs), else alphabetical; the reason is recorded so the minimal set is
  reproducible.
- **Report, never auto-drop** — `minimal_feature_set(report)` is opt-in; the analysis never mutates the
  caller's table (pinned). Constant / NaN-heavy columns are excluded with a stated reason. The report is
  labelled dataset-specific (redundancy on one table is not a universal fact).
- Tests (`core`): `tests/test_feature_redundancy.py` — a known duplicate groups and keeps one; independent
  columns produce no groups (cry-wolf); transitive clustering; Spearman catches a square law Pearson
  misses; ontology-preferred representative; constant-column exclusion; minimal-set one-per-group; and the
  no-mutation contract. Prerequisite for the Feature Explorer.

## [1.6.176] - 2026-07-20
### Changed — **fit_frap_recovery decomposed by phase (206 → 109 lines), proven byte-identical.**
The FRAP recovery fit fused the hyperbolic curve fit, the normalisation-aware mobile-fraction derivation
(+ over-recovery warning), a residual-runs adequacy test, and a per-parameter identifiability assessment
(+ its warning) into one body dominated by measured rationale. Split into pure phase helpers:
- `_frap_derive_mobile` — the mobile fraction of the BLEACHED material that recovered (correct under
  pre-bleach normalisation, where `b - a` under-reported it) + the unphysical-plateau (b>1) warning;
- `_frap_identifiability` — the per-parameter 95% CI from the fit covariance, flagging any parameter
  whose interval is wider than its own value (the covariance is the only thing that knows whether the
  data constrains a parameter — R² cannot; the rationale moved here with it).
- **Behaviour-preserving, pinned as such.** `tests/test_frap_recovery_characterization.py` captured the
  fitted parameters, R², the mobile/immobile fractions, the over-recovery flag, the per-parameter CI
  widths and identifiability verdicts, and WHICH warnings fire across adequate / short-unidentifiable /
  over-recovery / too-few-points curves before the split and asserts them unchanged after; the existing
  `test_frap_fitting` passes unmodified. No number moved. Complexity ratchet 130 → 129; truncation guard
  allowlisted.

## [1.6.175] - 2026-07-20
### Changed — **fit_photobleaching decomposed by phase (233 → 65 lines), proven byte-identical.**
The exponential-bleach fit fused the curve fit, the tau confidence interval, the non-circular
observation-window adequacy metric, and its two-tier warning — most of its 233 lines being the measured
rationale for each (five failed single-number attempts, the circularity of checking against the fitted
tau, why two decay bounds are reported). Split into pure phase helpers, each carrying its own rationale:
- `_photobleach_tau_ci` — the 95% CI on tau from the fit covariance (the only evidence tau is determined;
  R² does not carry it);
- `_photobleach_window_metrics` — the two decay-observed bounds (no-floor lower bound; floor-subtracted
  upper bound), because no single scalar is both floor-robust and non-circular;
- `_photobleach_window_warn` — the two-tier warning (severe < 0.5 τ, mild 0.5–0.8 τ), measured on the
  decay actually observed rather than the fitted tau (which would be circular).
- **Behaviour-preserving, pinned as such.** `tests/test_photobleaching_characterization.py` captured the
  fitted parameters, R², the tau CI, both decay bounds, the correction factors, and WHICH warning tier
  fires across adequate / mid-window / short / flat synthetic movies before the split and asserts them
  unchanged after. The existing `test_photobleaching_window` passes unmodified. No number moved.
  Complexity ratchet 131 → 130; truncation guard allowlisted.

## [1.6.174] - 2026-07-20
### Changed — **fit_size_distribution_mle decomposed by phase (301 → 92 lines), proven byte-identical.**
The droplet-size-distribution identifier fused per-model MLE fitting, a Clauset power-law tail comparison
(with a seeded parametric bootstrap goodness-of-fit gate), a Vuong distinguishability test, and verdict
assembly into one 301-line body — a length at which the several statistical tests' interplay is
unreviewable. Split into pure phase helpers:
- `_fit_size_models` — per-model MLE (lognormal closed-form; gamma/weibull/exponential via scipy; the
  power law's Clauset x_min by KS minimisation) → the `models` table + `x_min`;
- `_powerlaw_tail_comparison` — the tail-only Vuong test that re-fits the best alternative on the SAME
  tail, gated by an absolute bootstrap KS goodness-of-fit test (so a locally-power-law-like tail of a
  lognormal/gamma cannot spuriously win);
- `_size_distinguishability` — the whole-sample Vuong test of best vs runner-up;
- `_size_verdict` — the human verdict, keeping the power-law claim scoped to its tail.
- **Behaviour-preserving, pinned as such.** `tests/test_size_distribution_mle_characterization.py`
  captured the selected model, every model's AIC/log-likelihood, the power-law x_min and its tail test,
  the distinguishability comparison, and the descriptive moments on lognormal and gamma samples before the
  split (the bootstrap is `default_rng(0)`-seeded, so the whole function is deterministic) and asserts
  them unchanged after. No number moved. Complexity ratchet 132 → 131; truncation guard allowlisted.

## [1.6.173] - 2026-07-20
### Changed — **classify_beads decomposed into its two classifier branches (306 → 68 lines), byte-identical.**
The VPT bead classifier fused two independent classifiers — a fast-template branch (ncc/snr/amplitude,
for large Airy-disk beads) and a Gaussian-fit branch (sigma/r²) — plus an empty guard into one 306-line
body. At that length the two schemes' interleaved thresholds are unreviewable. Split into named helpers:
- `_classify_fast_template` (+ its `_classify_fast_template_refs` reference-statistics phase — the NCC
  realness floor, singlet intensity, aggregate mass/amplitude gates, and dim/out-of-focus cutoffs);
- `_classify_gaussian_fit` (the sigma-based branch, R²-is-not-focus rationale preserved);
- `classify_beads` is now a 68-line empty-guard + dispatch on which metrics are present.
- **Behaviour-preserving, pinned as such.** `tests/test_classify_beads_characterization.py` captured the
  exact per-bead `bead_class` labels (the categorical output flips on any threshold drift), the
  `n_units_est` estimates, the dropped-rejected row count, and the recorded `classify_thresholds` on BOTH
  branches before the split and asserts them unchanged after. No number moved. Complexity ratchet 133 →
  132; truncation guard allowlisted with reason.

## [1.6.172] - 2026-07-20
### Changed — **partition_coefficient_local decomposed by phase (394 → 109 lines), proven byte-identical.**
The local-annulus partition-coefficient measurement — a 394-line science function whose per-droplet loop,
camera-floor logic, and six-branch verdict chain were fused into one body — is exactly what the
complexity budget warns about: at that length a silent failure path is invisible. Split into pure,
named phase helpers, none over 90 lines:
- `_pc_check_input` — the ABSOLUTE-intensity provenance gate (a preprocessed image makes Kp meaningless);
- `_pc_camera_floor` — the pedestal (`dark_reference` / extracellular median), with the "in vitro cannot
  be auto-detected by any method" refusal;
- `_pc_estimate_gap` — the interface-width annulus offset;
- `_pc_measure_droplets` — the per-droplet dense-vs-annulus measurement + the over-inclusive-mask CV
  warning;
- `_pc_verdict` — the reporting chain, including the two honesty rules (no validation claim next to a
  NaN; no confident "validated" when the mask is suspect).
- **Behaviour-preserving, and pinned as such.** A new byte-identity characterization test
  (`tests/test_partition_local_characterization.py`) captured the exact per-droplet and aggregate outputs
  across all five reporting branches (dark reference, extracellular, in-vitro-no-reference, raw-ratio,
  empty) plus the invalid-sample-type raise **before** the split, at `rel=1e-9`, and asserts them
  unchanged after — no floating-point operation was reassociated, no number moved. The complexity ratchet
  drops 134 → 133 (`_MAX_LONG_FUNCTIONS`), the win locked in downward.

## [1.6.171] - 2026-07-20
### Added — **Progress part 2, Part C: determinate bars over the core cell/condensate per-object loops.**
The analysis half of the progress work (1.6.132) covered the two zero-bar widgets and was upgraded to the
off-thread modal runner in 1.6.140. Part C is the piece left: the **core cell and condensate analyzers**,
whose per-cell loops make progress genuinely measurable, ran with **nothing on screen** during a
multi-second analysis.
- `cell_analysis_func` (per-cell contour/morphology loop) and `puncta_analysis_func` (per-cell puncta
  loop) gain `progress_callback(done, total)` — the `materialize_stack` signature, so `PhasedProgress` /
  the modal runner drive it; **`None` is a complete no-op**, so headless and batch callers are byte-
  identical (pinned in a test that compares the result with and without a callback). Batched to ~100
  updates so a repaint-per-cell can't dominate the runtime it reports.
- `run_cell_analysis_func` / `run_puncta_analysis_func` now run their compute through
  `run_with_progress` — the **modal, off-thread** runner (the 1.6.140 pattern) — so the countable loop
  shows a **determinate** bar and the window stays responsive; the napari layers are added back on the
  main thread. Headless (no QApplication) it runs synchronously with a no-op progress, so batch/tests are
  unchanged.
- `puncta_analysis_func`'s per-cell stat-writing block was extracted to `_store_cell_puncta_stats`
  (behaviour-preserving — the no-op-identity test covers it) to keep the loop within the review-length
  budget rather than raise the ceiling.
- Ratchet: `tests/test_progress_analysis_half.py` now lists the two tool functions (must accept
  `progress_callback`) and the two runners (must route through `run_with_progress`); a regression to a
  zero-feedback on-thread call fails. Functional proof in `tests/test_analysis_progress_callback.py`.
- **Honest limit** (same as part 1): this makes the wait VISIBLE and the window responsive, not the
  analysis shorter. **Needs an in-app glance** (viewer-coupled): confirm the modal bar advances during a
  real cell / condensate analysis and the result lands correctly.

## [1.6.170] - 2026-07-20
### Added — **Cohort selection: the histogram-bin and aggregate-row emitters — the two deferred targets.**
The `Cohort` selection target and `select_cohort` shipped in 1.6.151, with the comparative box/violin group
emitter; the histogram-bin and aggregate-row emitters were the clean follow-ons deferred with it. New
**`utils/cohort_targets.py`** delivers them as pure, GUI-free membership logic (the part that must be
correct, tested without a matplotlib event loop — exactly as the comparative emitter is):
- `bin_cohort(values, entity_ids, bin_index, bin_edges, …)` — the cohort of entities whose value falls in
  a histogram bin, membership matching the drawn range **exactly** (half-open bins, the **last bin closed**
  so the maximum lands in it, as matplotlib does), carrying the range as the definition
  (`"area ∈ [12, 18) µm²"`) so a dock can say *why* the objects are grouped.
- `aggregate_cohort(members, …)` — an aggregate row's contributing set, with the count stated
  (*"summarizes N objects"*), never one arbitrary member.
- `attach_histogram_brushing(…)` — wires a drawn histogram so a bar-click emits the bin cohort (returns an
  `emit_bin`/`apply_selection` handle, testable headless); `select_aggregate_row(…)` emits an aggregate
  row's cohort; `cohort_dock_label(…)` is the "N objects · why" caption.
- **Additive + selection≠filter**, both pinned: `select_cohort` fills `selected` with the members, so a
  cohort-unaware overlay highlights every member for free, and no emitter mutates the DataFrame or the
  analysed population. Tests: `tests/test_cohort_targets.py` (bin membership vs an independent recompute
  over every bin, last-bin-closed, aggregate count, round-trip through the real `SelectionService`).

These are the reusable emitters the shipped comparative case established; attaching them to a live
brushable histogram / aggregate-table dock is follow-on when those surfaces are built (none is in the tree
today — the spec asked to land bins/groups/aggregates first).

## [1.6.169] - 2026-07-20
### Added — **Biological QC Part B: the object-level flags now SURFACE — in the consolidated table and the QC report.**
The object-level QC module (`biological_qc_tools`, 1.6.152) computed edge/size/shape/intensity/containment
flags but exposed them nowhere a scientist would look. Part B wires them into the two places that matter,
and carries the cardinal contract — **flag, never filter** — through both: no row is ever dropped for
tripping a flag.
- **Consolidated long table** gains an additive `qc_flags` column (at the END of the schema, so every
  existing reader is untouched). `build_image_long_table`/`ConsolidatedLongWriter` compute the
  table-based flags (size/shape/intensity via robust MAD) from the wide object table, and **carry an
  upstream-stamped `qc_flags` through untouched** — that is how the mask-based flags (edge/containment),
  computed where the label image lives, reach the table. A comparison can now be recomputed with and
  without flagged objects — *"the effect holds when edge-touching cells are excluded"* is a stronger
  claim than an unqualified one. `qc=False` opts out; QC never breaks the keystone table build (a failure
  degrades to "no flags", never a lost row).
- **QC report** gains an object-level section (`qc_biological_objects`), appended by `run_full_qc` when an
  `object_table=` is supplied, in the same Assessment → Interpretation → Recommendation shape the imaging
  checks use. It states counts per flag: edge-touching is **definitive** (a truncated object is a
  measurement artefact — can read POOR); size/shape/intensity are **hints** worded as observations and
  never escalate past CHECK, because a mitotic or dead cell is real data. The QC UI wires the segmented
  cell/puncta table into the call so the section appears live; a malformed table degrades gracefully and
  never breaks the imaging report.
- Tests (`core`): `tests/test_biological_qc_surfaced.py` — seeded-outlier detection in the consolidated
  table, the **cry-wolf** clean-population test, the flag-never-drops-a-row contract (QC on/off give the
  same rows), upstream-flag carry-through, the report section's per-flag counts and definitive-vs-hint
  status, the empty-table N/A, and AST guards that the batch and QC UI actually wire it. Closes the Part B
  deferral recorded in 1.6.152.

## [1.6.168] - 2026-07-20
### Changed — **science_function_split: the 394-line MSD/α fit decomposed by phase, proven behaviour-preserving.**
Companion to the UI-builder split, governed by a stricter rule: **a numerical function may only be split if
a test can prove the numbers did not change.** `fit_anomalous_diffusion` (`condensate_physics_tools`) — the
MSD → D/α fit behind every viscosity number, and manuscript-facing — was the ideal first case: it already
carries 4 tests that assert its recovered values.

- Split **by computational phase** (validate → gate → fit → assess → package), never by line count, into
  pure helpers: `_lag_window_gate` (the defensible lag band), `_fit_msd_powerlaw` (the non-linear
  4·D·τ^α + 4σ_loc² refinement), `_assess_msd_identifiability` (the D/α confidence interval),
  `_classify_msd_motion` (R², fit quality, motion type), and `_package_msd_result`. The function dropped
  from **393 → 98 lines**, each helper well under the 120-line ceiling.
- **Every line was MOVED, not rewritten** — no floating-point operation reassociated, nothing "improved"
  while splitting. The proof: its 4 existing numerical tests (`test_msd_drift`,
  `test_msd_min_track_length`, `test_vpt_viscosity_chain`, and the route-equivalence viscosity chain)
  passed **unmodified**, and `test_no_undefined_names` confirmed no local was left unthreaded.
- The complexity ratchet `_MAX_LONG_FUNCTIONS` is lowered **135 → 134** (the ratchet moving down, which is
  it working), and a `_DELIBERATE` drop-guard record documents that the 75% shrink is a decomposition, not
  a truncation. **No numerical output changed anywhere.**

## [1.6.167] - 2026-07-20
### Added — **Publication figure refinement: refine the presentation, never re-run the analysis.**
A PyCAT comparative figure could be produced but not prepared for a journal — no export, no DPI, no vector
output — so adjusting an axis label or a colour meant re-running the analysis. That is slow and
scientifically wasteful: the numbers are already correct; only presentation needs work.

- New **`utils/figure_spec.py`** (`core`, Qt-free): separates a figure's **data** (`FigureData`) from a
  declarative **`FigureSpec`** (title, labels, limits, palette, fonts, size, DPI, n-annotation, caveat
  footnote). `render(fig_data, spec)` reads the data and applies the presentation; **mutating the spec and
  re-rendering never recomputes** — the plotted values are stashed on the figure so the contract is
  checkable.
- **The ontology supplies the defaults** (the payoff): the y-axis label defaults to *"Partition coefficient
  (dimensionless)"* (display name + units, no typing), and a measurement's caveats can render as a figure
  **footnote** — so the 2D-projection-proxy warning travels onto the figure instead of being lost between
  the analysis and the paper. Both are overridable per figure.
- **Export** (`export`) writes vector **PDF/SVG with fonts embedded as TEXT** (`pdf.fonttype=42`,
  `svg.fonttype='none'` — editors can adjust type, not outlines), a high-DPI PNG, the **summary DataFrame**
  alongside (a figure whose numbers are not saved is irreproducible), and the **spec as JSON** so the figure
  regenerates identically. **Size presets** (single / 1.5 / double column) are offered as *sizes*, never as
  journal-compliance claims (requirements vary; unverifiable compliance is worse than sensible defaults).
  Palettes are colour-blind-safe (Okabe–Ito) by default.
- New **`tests/test_figure_spec.py`** (`core`, matplotlib Agg): the **refine-never-recompute contract**
  (changing the spec leaves the plotted values byte-identical); the spec round-trips through JSON; labels
  and units default from the ontology (and an explicit label overrides); caveats render as a footnote
  matching the ontology; and export produces vector output with text fonts, a PNG at the requested DPI, the
  summary CSV, and a regenerating spec JSON.

## [1.6.166] - 2026-07-20
### Added — **PyCAT Validation Suite: a standing per-release regression benchmark, with the first baseline recorded.**
The test suite answers *"did anything break?"* per commit. At PyCAT's release cadence (often several
versions a day) that misses the question that catches slow degradation: *"is segmentation quality on our
canonical cases the same as it was ten releases ago?"* A change can keep every test green while moving Dice
from 0.94 to 0.89, and nobody notices until a result looks wrong months later.

- New **`benchmarks/`** (outside `tests/`, so it never runs on the per-change loop): `cases.py` — a fixed,
  seeded canonical case set (puncta, condensate/partition, cells) with **constructed** ground truth (never
  produced by a PyCAT run, so the suite cannot track a drifting method as "stable"); `run_suite.py` —
  measures each case against ground truth via `benchmark_tools` (Dice/IoU, matched-detection F1,
  object-count error, derived measurements like the partition coefficient, and runtime) and **appends** one
  record per version to `results.jsonl` (append-only — it diffs cleanly and never rewrites history).
- **`compare_to_baseline`** fails on a metric moving beyond a **declared, justified** tolerance in the
  *worse* direction (Dice ±0.02 covers seeded-RNG variation; a >5% partition-coefficient move is real),
  **reports — never fails on — an improvement** (an unexplained improvement often means the case or ground
  truth changed), and treats runtime as **advisory** (machines vary). Tolerances are never tuned to make a
  run pass — a metric beyond tolerance is a finding.
- **The first baseline is recorded** in `benchmarks/results.jsonl` (v1.6.166): puncta Dice 0.953, partition
  coefficient recovered at 5.0, cells Dice 1.0 — the deliverable that makes future cross-release comparison
  possible. Run it with `python -m benchmarks.run_suite <version>` (documented in `benchmarks/README.md`);
  a new `benchmark` pytest marker is registered.
- New **`tests/test_validation_suite.py`** (`core`, machinery only — the metric gate itself runs
  deliberately, not per-commit): the suite runs end-to-end and writes a well-formed record; a case is
  deterministic under its seed; the comparator flags an injected regression, reports an improvement without
  failing, and treats a higher count-error as a regression while runtime stays advisory; and the results
  file is strictly append-only.

## [1.6.165] - 2026-07-20
### Added — **Measurement Reliability Index: every reported number carries a decomposable reliability score.**
The roadmap's unifying construct, buildable now that all its inputs exist — imaging QC, biological
plausibility, parameter sensitivity, benchmark agreement, control separation, and calibration validity.
**This is composition, not new science:** every factor comes from a module that already measures it;
inventing a new heuristic here would be unvalidated and would undermine the score.

- New **`utils/reliability.py`** (`core`, pure): a frozen `ReliabilityScore` (value 0..1, grade, per-factor
  `contributions`, worst-first `reasons`, and an explicit `missing` list) and `reliability(measurement_key,
  *, image_qc, object_flags, calibration, sensitivity, benchmark)` composing the available signals.
- **Five honesty rules, enforced and tested.** (1) **An unmeasured factor is never treated as passing** —
  it goes in `missing` and does not contribute (silently assuming "fine" would make every score
  optimistic). (2) The score is **decomposable** — `value` is the product of the `contributions`. (3)
  `reasons` are **worst-first** and concrete. (4) **Missing core evidence (QC or calibration) caps the
  grade below `high`** — absence of evidence is not evidence of reliability. (5) A **refused calibration is
  a hard override to `unreliable`** — a number computed under an invalid calibration is not a weak
  measurement, it is not a measurement.
- **Stated aggregation:** the product of factor scores (each 0..1), explainable in a Methods section — not
  a tuned ML-ish blend. Adapters read each factor from its own module (`_score_qc` from `run_full_qc`,
  `_score_object_flags` from `biological_qc`, `_score_calibration` from `check_calibration_validity`,
  `_score_sensitivity` from `measurement_stability.stability_factor`, `_score_benchmark`). Scored for the
  partition/concentration/ΔG family first; `format_with_reliability` extends the `Parameter` display
  (`K_p = 4.2 (reliability: moderate)`).
- Reliability is **reported, never a silent filter** — the user decides (the biological-QC contract).
- New **`tests/test_reliability.py`** (`core`): a fully clean measurement scores `high`; **each factor
  degraded individually lowers the score and names itself** (one case per factor); missing core factors cap
  the grade and are listed (a supplied-but-all-`na` factor counts as missing, not passing); the value
  decomposes as the product of contributions; reasons are worst-first; and a **refused calibration scores
  `unreliable`, not merely low.**

## [1.6.164] - 2026-07-20
### Added — **Explicit 2D / 3D / time-series condensate modes: refuse the volume-fraction approximation, label everything.**
`invitro_fluor_ui` already prints *"area fraction=… (2D projection, not a volume fraction)"* — but that
honesty lives in a transient napari message while the number travels into tables, the consolidated long
table, and figures with no qualifier. A projected area fraction is not a volume fraction (their ratio
depends on object size/shape/axial overlap), and the same workflow is applied to 2D fields, z-stacks, and
time series where some measurements valid in one are meaningless in another.

- New **`toolbox/condensate_modes.py`** (`core`, pure): a `CondensateMode` enum (`FIELD_2D` / `ZSTACK_3D` /
  `TIMESERIES`) and `resolve_condensate_mode` — declared or derived from the data, **never silently guessed
  for an ambiguous 3D array** (z-stack vs time series have different valid measurements, so it refuses and
  points at the loader's disambiguation rather than guessing).
- **Refuse rather than convert:** `volume_fraction` returns `nan` **with a stated reason** in 2D (a single
  plane cannot measure it, and converting the projected fraction needs assumptions — mono-disperse spheres,
  no axial overlap — the data cannot support), and the true value from voxels in 3D. **The 2D numbers are
  unchanged — this is labelling and gating, not recomputation.**
- **The qualifier travels:** `attach_mode_column` stamps a `condensate_mode` column on every emitted table,
  so a projected fraction is never mistaken for a volume fraction downstream. The projection caveat is
  already data in the measurement ontology (queryable, renderable in a figure footnote), not only a UI
  string.
- **Time-series independence:** `mark_timeseries_as_unit` declares a per-frame series ONE biological unit,
  so the comparative-figures replicate aggregation (`aggregate_to_unit`) collapses it to one rather than
  counting each frame as an independent replicate — reusing the existing pseudoreplication machinery.
- New **`tests/test_condensate_modes.py`** (`core`, synthetic): the 2D volume-fraction refusal (NaN +
  reason); a 3D z-stack of known spheres whose true volume fraction differs materially from the projected
  area fraction; ambiguous-3D refusal; the mode column on tables; the ontology caveat; and a 20-frame
  series aggregating to one unit.

## [1.6.163] - 2026-07-20
### Added — **Background-mode guardrail: turn the partition-coefficient docstring reasoning into a check at the moment the mistake would be made.**
`client_enrichment` already supported three background treatments (scalar offset, signal-free-region mask,
local dilute shell) and its docstring carries careful reasoning — but **nothing surfaced any of it**, so
every GUI partition coefficient used `background=0.0`. Two consequences: users with a real dark reference
could not use it (their K biased toward 1), and users who *think* they should subtract "the area outside
the condensate" had no guidance that doing so destroys the measurement. The second is a mistake a
well-intentioned user makes naturally, and the code already knows why it is wrong.

- **The guardrail** (`assess_background_region`, wired into `client_enrichment`): when a background region
  is supplied, its mean is compared against the dilute-phase mean; if the region is not meaningfully darker
  (it is plausibly dilute phase, not background), a warning fires that states the **consequence** — *"if
  this region is inside the cell, subtracting it will subtract the dilute phase from itself and DESTROY the
  partition measurement; a background region must be outside the cell or a dark frame."* It warns, never
  blocks (the user may have a valid unusual case), reusing the existing napari warning path.
- **The choice travels with the result:** `client_enrichment` now emits `background_mode`
  (`none`/`scalar`/`region`), `background_source`, and `background_warning` — so a K computed with a
  dark-frame offset and one computed raw are distinguishable in the table (and the consolidated long
  table). A K computed raw and one offset-corrected are different measurements. **Default stays `none`**, so
  existing workflows are unchanged.
- **Ontology caveat:** the "dilute phase is NOT background / the only legitimate offset is the instrument
  offset" reasoning is now a caveat on `partition_coefficient` in the measurement ontology, making it
  available to figure footnotes.
- New **`tests/test_background_mode.py`** (`core`, synthetic): each mode produces the expected offset; a
  dilute-phase "background" region triggers the consequence-stating warning while a genuinely dark region
  does not; a region offset recovers the true K across a camera pedestal; and the mode/offset/source travel
  with the result. (`test_calibration` updated for the three new provenance keys — no calibration keys leak
  without a curve.)

## [1.6.162] - 2026-07-20
### Added — **Analysis presets: "reasonable starting parameters" as a declared, inspectable object that can't smuggle an unaudited default past a user.**
Sensible defaults are scattered across signatures, docstrings, and the maintainer's head; a new user
opening a condensate workflow faces a dozen parameters with no idea which suit their data. This makes a
preset a declared, versioned bundle — with two non-optional honesty invariants.

- New **`utils/analysis_presets.py`** (`core`): a frozen `AnalysisPreset` (key, applies_to, parameters,
  provenance, validated, validation_ref, requirements, caveats) and a **sparsely, honestly seeded**
  `ANALYSIS_PRESETS` registry — only instrument/sample combinations actually run (a validated condensate
  SNR gate, VPT bead tracking, an in-vitro 63× confocal starting point). An invented preset for unused
  hardware carries false authority and is not seeded.
- **`provenance` is mandatory and non-empty** (enforced at import) — a preset with unstated provenance is
  just a hidden default with a friendly name, which is exactly what the filter-sensitivity programme exists
  to expose. **`validated=True` requires a linked `validation_ref`** into `VALIDATED_CASES`, so the flag
  cannot be decorative — it means the set passed the sensitivity harness, not that it looked reasonable.
- **Drift guard:** `orphan_parameter_keys` checks every preset's keys against the REAL parameter names of
  the workflow it claims (read from the live function signatures), so a preset that rots out of sync with
  its function fails a test.
- **Populate, never lock:** `PresetApplication` seeds the values, lets the user change any, and reports
  "modified from <preset>" once edited (a result from a modified preset is not the preset's result) —
  recording the applied preset + modification state into the workflow. **Requirement gating reuses
  `operation_spec.runnability`** (the single requirements vocabulary), never a second gate.
- New **`tests/test_analysis_presets.py`** (`core`): unique keys + mandatory provenance; the drift guard;
  `validated⇒ref` (and the import-time refusal of an unlinked one); populate-not-lock deviation tracking
  and recording; and requirement gating with a stated reason.

## [1.6.161] - 2026-07-20
### Added — **Per-feature provenance: attach the workflow chain to each measurement, not just the session.**
`batch_processor` records a complete, replayable workflow — but that chain is attached to the *session*,
not the *measurement*. A 40-column table with a 12-step workflow gives no way to know that
`partition_coefficient` depended on steps 3, 5 and 9 but not on the fibril segmentation in step 7. A table
opened in six months carries its values but not the route to them, and reproducibility is the manuscript's
central claim.

- New **`utils/feature_provenance.py`** (`core`, pure): a frozen `FeatureProvenance` (feature, operation_id,
  input_layers, step_indices, parameters, software, acquisition) **composed from existing sources — never a
  second recording mechanism** that would drift from `batch_processor`. `software_versions()` and
  `acquisition_from_metadata()` capture the environment and `metadata_extract` fields automatically.
- **"All steps" is not provenance.** `trace_step_indices` walks the layer LINEAGE backward and reports only
  the steps that actually produced the feature's ancestors — so a feature from one workflow branch does NOT
  claim an independent branch's steps. When the lineage cannot discriminate (the layer is unrecorded), it
  returns `None` with a reason, never a useless "everything". **Absent beats guessed**, consistent with the
  layer-tag hook's `derived`/`inferred` distinction.
- **Surfaces** (Part D): a sidecar `<name>_provenance.json` keyed by column (not 40 unreadable extra CSV
  columns) via `write_provenance_sidecar`; the consolidated long table already carries `operation_id` among
  its provenance columns; and a `describe_provenance` "where did this number come from?" query that reads
  the chain. Capturing provenance never touches a computed value.
- New **`tests/test_feature_provenance.py`** (`core`): the discrimination test (a two-branch workflow — a
  branch-A feature does not list branch-B's steps); an unrecorded lineage → `None` + reason, not "all
  steps"; fields composed not fabricated (underivable → absent); software/acquisition captured
  automatically; and the sidecar round-trips keyed by column.

## [1.6.160] - 2026-07-20
### Added — **Per-measurement parameter stability: "if I nudge this parameter, does the number I report change?"**
`benchmark_tools` sweeps a parameter and compares the resulting **masks** (Dice/IoU/F1). It does not report
how much each *derived measurement* moves — and that is the scientifically load-bearing distinction: two
settings can produce masks that agree at Dice 0.95 while the partition coefficient computed from them
differs by 40%, because a small boundary shift moves the dense/dilute split. Mask agreement is not
measurement agreement, and it is the measurement that gets published.

- New **`toolbox/measurement_stability.py`** (`core`, pure): `measurement_stability(image, method, param,
  sweep, measure_fn)` runs the full chain (segmentation → measurement) across a **plausible** parameter
  range and returns a `StabilityResult` per measurement — baseline, per-setting values, `relative_range`
  ((max−min)/|baseline|), and a verdict. Segmentation reuses `benchmark_tools.run_candidate` (no second
  runner to drift).
- **Two traps encoded.** *Population change vs measurement change:* `n_objects` is reported alongside, and
  when the object count changes materially across the sweep the verdict is **`population-change`** ("a
  shifting measurement here reflects a DIFFERENT POPULATION, not instability") rather than confidently
  calling the measurement unstable. *A near-zero baseline* returns `nan` with a stated reason, never an
  infinite relative range.
- **Stated convention, not a fitted quantity:** the verdict thresholds (<5% stable, 5–20% sensitive, >20%
  unstable) are documented as a convention.
- **MRI adapter** (`stability_factor`) maps the verdict to a 0..1 parameter-sensitivity factor for the
  Measurement Reliability Index (`nan` = "cannot assess", never treated as reliable) — the classification
  lives here once, not duplicated in the index. Plus a **report figure** (measurement/baseline vs
  parameter) that reads the measurement ontology's display name and units.
- New **`tests/test_measurement_stability.py`** (`core`, synthetic, known-answer): a stable total-intensity
  measurement reads `stable`; a partition coefficient whose boundary the threshold decides reads
  `sensitive`/`unstable`; a count-changing sweep is a **population change**; `relative_range` is scale-free;
  and a zero baseline is `undefined`, not a divide-by-zero.

## [1.6.159] - 2026-07-20
### Added — **Route equivalence increment A: the matrix grows from three to six canonical workflows, plus metadata comparison.**
The cross-route equivalence matrix asserts the same workflow yields the same numbers through every route it
can run (headless / batch replay / session reload) — a divergence is the highest-severity reproducibility
bug PyCAT can have (the rolling-ball precedent: batch once passed a normalised image where interactive
passed raw counts). It shipped at three workflows by design; this adds the next three, the audit's
"beginning of the most important validation program in PyCAT." **Test-only; no divergence was found.**

- **Cellpose segmentation** (headless ≈ batch ≈ session) — the most-used path and the most parameter
  surface. Genuinely drives all three routes (torch present); the batch route runs the REAL
  `replay_cellpose_segmentation`, so it proves the batch replay reads the recorded diameter and resolves
  the right layer rather than falling back to defaults. Gated on cellpose being importable (the
  optional-dependency skip pattern) rather than weakening the assertion.
- **Colocalization** (headless ≈ session; batch a documented gap) — two-channel input exercising channel
  assignment (m1 = ch1-over-ch2 ≠ m2), driven through the real Manders and Pearson functions. Batch is a
  declared gap: no colocalization step exists in the replay registry.
- **Time-series condensate partition** (headless ≈ session; batch a documented gap) — a per-frame partition
  series over a stack through the real `client_enrichment`. Batch is a declared gap: a time-series is not a
  per-image batch step (same class as the VPT/MSD skip-stub).
- **Beyond the arrays — metadata comparison** (`compare_metadata` on `Workflow`, `compare_frame_metadata`):
  the two new DataFrame workflows now also compare schema (column order), dtype kind, **NaN policy** (a
  route emitting 0.0 where another emits NaN is a real divergence), and the **units column** — because two
  routes can produce numerically similar tables while differing in scientifically important metadata.
  Existing workflows pass `None` and are unaffected. Layer-tag/provenance comparison is a noted next step,
  deferred (the routes return arrays/frames, not tagged layers).
- The batch **gaps are declared, not skipped silently** — the harness still fails if a gap closes or a
  route vanishes without the matrix docstring being updated. Two fixture bugs surfaced and were fixed
  during construction (a division-by-zero from equal dilute/pedestal backgrounds, and a shared RNG that
  advanced between a route's reference call and its session re-call) — both in the test fixtures, not
  production. Test-only.

## [1.6.158] - 2026-07-20
### Validated — **Filter sensitivity increment 4: the puncta refinement gate cluster (no new inverter; one gate added, one confirmed inert).**
The puncta refinement gate applies `kurtosis_threshold=-3.0, local_snr_threshold=1.0,
global_snr_threshold=1.0` **together**, and it decides which puncta exist — every downstream count,
density, partition coefficient, and colocalization statistic inherits its decisions. This SNR family
already produced a proven inverter (the un-subtracted `object_mean/bg_std` ratio, fixed 1.6.86), so it is
the highest-value untested group. Test-first: a divergence would be a finding, not a tolerance to tune.

- **`local_snr_threshold` / `global_snr_threshold` — validated on all three signatures.** Both are
  contrast-to-noise (background-subtracted), so they are **offset-invariant** (a camera pedestal cancels)
  and **scale-free** (a pure intensity ratio carries no pixel units, unlike the ring geometry of increment
  2's scale case). Swept across the plausible range on a brightness-spanning population of clearly-real
  puncta, the survivors' mean brightness **does not drift** — no selection bias (the mechanism that made
  `r2_min` report a mean of 77 against a true 44). `segmentation.global_snr_threshold` is added to
  `VALIDATED_CASES` (the local sibling was added in increment 2).
- **`kurtosis_threshold=-3.0` — confirmed INERT, documented-absent.** scipy Fisher (excess) kurtosis has a
  hard floor of −2, so `kurtosis < -3.0` can never be true and the gate rejects nothing. It therefore
  **cannot be one arm of a two-parameter interaction cliff** — the joint kurtosis × local_snr grid is flat
  along the kurtosis axis. An inert gate has no bad control, so (like `bleach_r2_min`) it stays out of the
  registry, pinned instead by a test that fails if the kurtosis computation ever changes to be able to
  fire. A **secondary finding** is recorded: pushed into a firing range (e.g. 0.0), the kurtosis gate
  becomes brightness-selective (a faint object's local patch is less peaked), dropping the dimmest puncta
  first — which is exactly why the shipped default is set below the −2 floor to be inert.
- New **`tests/test_filter_sensitivity_puncta_cluster.py`** (`core`, synthetic, drives the real
  `_snr_conditions` and `stats.kurtosis`): offset, scale, and selection-bias sweeps for both SNR
  thresholds; the joint kurtosis × SNR interaction grid; and the kurtosis-inertness pin. **Test-only — no
  production behavior changed.**

## [1.6.157] - 2026-07-20
### Added — **QC for scan-acquisition aberrations: per-object motion shear, bidirectional phase, disk pattern, pinhole crosstalk.**
`data_qc_tools` covers saturation/focus/SNR/drift/vibration/aberration — every check asks about the image
as a whole or the optics. **None asked "was this OBJECT distorted by the way the pixels were collected?"**
The motivating case: on a laser-scanning confocal, a *mobile* condensate is torn/sheared because it moves
during the raster, while a *stable* one in the same frame is clean — so the same image contains trustworthy
and untrustworthy objects, and every existing whole-frame QC check passes it.

- New **`toolbox/scan_qc_tools.py`** (`core`, synthetic-tested):
  - **`qc_scan_shear`** (per object) — fits each object's intensity-weighted column centroid against row
    index (the slow-scan axis); a mobile object's centroid drifts systematically with row. **The in-frame
    control is the method:** each object's slope is compared against the others in the SAME frame, not a
    fixed global threshold (which varies with sample/scan-speed/zoom). If objects shear together it is
    **stage drift / sample flow**, not per-object motion, and is reported as such. An elongated tilted
    static object (whose centroid slope is morphology) is reported **`ambiguous`**, never confidently
    called motion; objects too small to fit a slope are `na`. A **velocity is reported only when the line
    time is known** — otherwise an honest px/row, never converted with an assumed line time.
  - **`qc_bidirectional_phase`** — cross-correlates odd-row vs even-row sub-images; a lateral offset is a
    bidirectional-scan comb artifact (a scanner phase-calibration problem).
  - **`qc_disk_pattern`** — spinning-disk pinhole striping as a **detrended** spectral peak. It reuses
    `qc_vibration`'s recorded lesson: a low-frequency vignetting gradient reads as periodic striping unless
    removed first, so the field is detrended before the spectral test.
  - **`qc_pinhole_crosstalk`** — elevated local background around bright objects vs distant background;
    warns that partition coefficients and enrichment ratios (what it most corrupts) will be biased.
- **Gated by modality, never guessed from pixels** (`run_scan_qc`): scan shear applies only to a
  point-scanner, disk pattern only to a spinning disk; on the wrong modality — or an unknown one — each
  reports `na` with the reason (a confident wrong verdict is worse than "not assessed"). Wired into
  `run_full_qc` behind optional `labels` / `modality` / `line_time_s` (appended only when a modality is
  given).
- **Per-object shear flags compose with biological QC** — `scan_shear_flags` returns a per-label Series
  that `biological_qc(..., scan_shear_flags=)` folds into the same flag columns and summary. Flag, never
  filter — so a condition comparison can be recomputed excluding motion-corrupted objects.
- **Metadata**: `metadata_extract` now carries `acquisition_mode` / `line_time_s` / `dwell_time_s` /
  `pinhole_um`, filled opportunistically and format-agnostically from the raw block (`None` unless a raw
  key plausibly names it and the value parses — a guessed scan mode is exactly what the gating refuses).
- New **`tests/test_scan_qc.py`** (`core`, synthetic — no microscope): the measured slope recovers the
  injected displacement-per-line; **one stable + one sheared object in a single frame flag exactly one**
  (the motivating case); uniform shear → drift, not per-object motion; a tilted elongated static object is
  `ambiguous`, not motion; bidirectional offset recovered; disk periodicity detected while a smooth
  vignette is not (the detrending test); a crosstalk halo raises the metric and a clean field does not;
  gating returns `na` with a reason on unknown modality; and the shear flag composes with biological QC.

## [1.6.156] - 2026-07-20
### Added — **Positive/negative control validation: "does my segmentation actually work on my data?", answered with the user's own controls.**
`benchmark_tools` scores candidates against a ground truth *within one image*. It cannot answer the
question a reviewer asks: does the method detect the objects in a **positive control** (known to contain
them) *and* detect **nothing** in a matched **negative control** (untransfected, no-primary, dye-only)? A
segmentation can score well on ground truth and still fire on empty fields — the false-positive rate on a
negative control is the number that tells a reviewer the detections are real.

- New **`toolbox/control_validation.py`** (`core`, pure): `validate_against_controls(positive, negative,
  method, param_grid)` sweeps one method across a parameter grid on BOTH controls with identical settings
  and returns a per-setting DataFrame (`ControlResult`: n_positive, n_negative, false_positive_rate,
  positive_density, separation, verdict + a **stated reason**). Scoring **reuses `benchmark_tools`**
  (`_labelled`, `basic_metrics`) — no parallel implementation to drift.
- **`recommend_parameters`** returns the setting that maximizes positive detection **subject to the
  negative control staying near zero** — not the most detections outright. **When no setting separates the
  two, it returns `None` and warns with the reason** — *"no parameter set distinguishes your positive from
  your negative control"* is a real finding about the ASSAY, not the software. A least-bad setting is never
  returned in its place (that would launder an assay problem into a software recommendation).
- **Honest edge handling:** mismatched acquisition between the two controls (exposure/gain/laser, via the
  calibration module's `AcquisitionFingerprint`) **warns loudly** — an intensity comparison across
  mismatched exposures is meaningless. Counts are **density-normalized** (objects/µm²) through a real pixel
  size so different field sizes are comparable; without a pixel size the density is left NaN, never faked
  to 1.0 (the pixel-size gate). The negative control's expected count is **user-declared** (default 0), not
  assumed zero — a legitimate autofluorescence baseline is not flagged as false positives.
- **Report artifact** (`control_report_figure`): detections vs the swept parameter for both controls on one
  axis, the recommended operating point marked and its separation stated — the supplementary figure behind
  *"parameters were chosen to maximize detection in positive controls while yielding <1% detections in
  matched negative controls."*
- **UI:** Toolbox → Image Processing → **Control Validation (positive/negative)** — pick the positive and
  negative image layers, sweep a threshold, see the recommendation (or the refusal) and the report figure.
- New **`tests/test_control_validation.py`** (`core`, synthetic): a recommendation recovers ~N objects with
  ~0 false positives; **the refusal case** (indistinguishable controls → `None` + reason — the most
  important test); mismatched acquisition warns; density is field-size independent; and a declared
  non-empty negative baseline is honored rather than flagged.

## [1.6.155] - 2026-07-20
### Fixed — **Explicit operation context: off-thread execution was silently degrading layer op tags from definitional to guessed.**
The layer-tag hook attributes each new layer to the operation that made it. Its mechanism —
`_op_from_stack`, a walk up the call stack for a decorated function carrying `__pycat_op__` — only fires
when that function is STILL ON THE STACK as the layer is created. **The off-thread execution shipped in
1.6.139/140 (`operation_runner`) breaks that:** the compute frame has already returned by the time the
result callback creates the layer, so the walk finds nothing and the op silently degrades from
definitional (`source='derived'`) to a name-substring guess (`source='inferred'`) — quietly, and worse as
more widgets adopt the runner. This replaces the implicit walk with an explicit context; the walk stays as
a fallback.

- New **`operation_context(op)` / `active_operation()`** in `utils/tag_registry.py` — a `contextvars`
  context declaring the operation responsible for layers created inside a block. **`contextvars`, not a
  module global:** a global would leak an op across threads and mis-attribute a layer created on another
  thread, which is strictly *worse* than an absent tag — it violates the hook's own "an absent tag is
  honest; a guessed one is a lie" principle. Thread-isolated by default; propagates into `asyncio`.
- **Hook resolution order** (`layer_tag_hook`) is now explicit → stack walk → name guess → absent. The
  explicit context is preferred (definitional, `source='derived'`); the stack walk is unchanged as the
  fallback for un-migrated synchronous paths; name inference still marks itself `source='inferred'`.
- **`@tags_layer`** now sets `operation_context(op)` around the wrapped call, so every decorated function
  *synchronously* creating a layer is covered with no call-site change.
- **`operation_runner.execute`** captures `active_operation()` at call time and re-establishes it around
  `on_result`, so a layer created in the result callback is attributed to the operation that produced the
  data — the fix for the concrete breakage. Benefits every current and future runner adoption.
- New **`tests/test_operation_context.py`** (`core`): the context sets/nests/restores (and restores on an
  exception); a layer made inside it is `derived`; **a layer made in an `operation_runner` result callback
  is tagged definitionally** (the regression — it fails on the pre-fix tree); the op does not leak across
  threads; and the stack walk + name inference are unchanged (fallback intact, a guess still a guess).
- **Additive and back-compatible** — the stack walk is retained, so no un-migrated path regresses; the
  `derived` vs `inferred` distinction (a fact vs a guess) is preserved exactly.

## [1.6.154] - 2026-07-20
### Added — **Measurement ontology: what each measurement MEANS, machine-readable and guarded against drift.**
`utils/measurement.py` already models a measurement's *value* (`Parameter`: units, uncertainty, source).
This adds the missing *definitional* side — what a measurement means, the equation behind it, and where it
comes from — the metadata a Methods section or figure legend needs, which today lives only in scattered
docstrings. **Additive: no computation changes.**

- New **`utils/measurement_ontology.py`** (`core`, pure): a frozen `MeasurementDef` (key, display_name,
  definition, equation, units, interpretation, caveats, reference, doi) and `MEASUREMENTS` — a registry
  seeded with 12 entries that are scientific *claims* (partition_coefficient, client_enrichment,
  delta_g_transfer, viscosity, D_um2_per_s, alpha, mobile_fraction, t_half, manders_m1/m2, pearson,
  projected_area_fraction). Plus `describe(key)` and `units_for(key)`.
- **Transcribed, never invented.** Every definition/equation/units is transcribed from an existing PyCAT
  docstring or the code itself. A `reference` is set only where the citation is certain (only `viscosity`,
  → Stokes–Einstein); unsourced fields are left `None` for a domain expert — *a wrong equation or DOI in a
  registry destined for a Methods section is worse than an absent one.* Keys are the EMITTED column names
  (`D_um2_per_s` not "diffusion_coefficient"; `projected_area_fraction`, flagged as a 2D projection proxy,
  not a true volume fraction).
- New **`tests/test_measurement_ontology.py`** (`core`): the load-bearing **units-agreement** test —
  constructs the real emitter (`delta_g_transfer(10, 1, 298)` → a `Parameter`) and asserts its emitted
  units equal the ontology's, so a units claim cannot silently drift from the code. Plus: every
  `emitted=True` key must appear in `src/pycat` (no aspirational entries), every `reference` must carry an
  equation (no orphan citations), and every entry is well-formed.
- **Consumer:** `comparative_figures.condition_comparison` now attaches the measurement's display name,
  units, definition, and caveats to the returned `summary.attrs`, and labels the y-axis
  `"{display_name} ({units})"` when the ontology knows the measurement — so a figure reads its own legend.
- **Deliberately NOT** Methods-section generation or a Measurement Reliability Index (both build on this),
  and NOT a duplication of scikit-image's `regionprops` geometry docs (plain geometry is delegated there).

## [1.6.153] - 2026-07-20
### Added — **CZI seam regression test: turn the reported mosaic discontinuity into a measurable number.**
The one prior-audit priority carried across three consecutive audits without closing — because *"a visual
bug with no measurement is a bug that cannot be closed."* Both reviews said the CZI work was
"architecturally improved but not validated against the reported defect" (a left-side column
discontinuity from a mis-assembled mosaic tile). `test_czi_bioformats_reader.py` had no seam assertion, so
CI could not notice the defect returning — or tell whether it was already gone. This is the measurement.
**It measures the seam; it does not fix it.**

- New **`file_io/czi_seam.py`** (pure numpy, `core`): `column_seam_score(frame, x)` — a boundary's step
  as a z-score against its NEIGHBOURS (normalized, not an absolute threshold, since absolute pixel steps
  vary with sample/exposure), and `persistent_seam_columns(frames)` — the columns anomalous on a MAJORITY
  of frames. That many-frame test is the key insight: real image structure moves frame to frame, a tile
  seam does not, so a boundary anomalous at a *fixed* column on *every* frame is a seam, not content.
- New **`tests/test_czi_seam.py`** (`core`): a clean synthetic mosaic scores no seam (the metric doesn't
  cry wolf); a deliberately offset mosaic scores high at *exactly* the injected boundary; the seam is
  persistent across frames while structure is not; and the score is normalized (a 20× contrast frame
  still scores no seam). Plus an **opt-in real-file assertion** — gated on `PYCAT_CZI_SEAM_FILE`, it reads
  frames through PyCAT's real CZI path and asserts no persistent seam. (The large real file cannot live in
  the repo; this is the only thing that confirms the *reported* defect is gone — run it with the file.)
- New **`scripts/czi_diagnostics.py`** (run-once, not CI): the measurements the audits asked for — read
  latency under forward/random/alternating access, cache hit-rate + planes cached, a staleness read-back
  check, and per-boundary seam scores for a frame sample.

Full `pytest -m core` green (1132). **Note:** the CI assertions prove the metric works and the synthetic
defect is caught; **the reported defect can only be closed by running the opt-in test / the diagnostics
script against the real file** — the number then either closes it or reopens it as a fix-spec with evidence.

## [1.6.152] - 2026-07-19
### Added — **Biological QC: an object-level QC layer — flag biological outliers, never filter them.**
PyCAT's imaging QC answers *"can I trust this image?"* (saturation, focus, SNR, drift). Nothing answered
*"can I trust this object?"* — yet the most common analysis errors are object-level and pass imaging QC
perfectly: a cell truncated by the field edge, an oversegmented nucleus, a condensate outside its cell,
an object of extreme size or intensity. Each silently biases a population statistic.

New **`toolbox/biological_qc_tools.py`** (headless, `core`-testable):
- `flag_edge_touching(labels, border_px=)` — objects whose mask reaches the field edge (truncated →
  area/shape/total-intensity wrong). The one flag stated **definitively** — it's a measurement artefact.
- `flag_size_outliers` / `flag_shape_outliers` / `flag_intensity_outliers` — **robust (median/MAD)**
  outlier detection, so the outliers can't corrupt the estimator that finds them; `k` is a declared
  parameter recorded on the result.
- `flag_containment_violations(child_table, parent_labels)` — a child whose centroid is outside any
  parent object (a condensate not in a cell is usually a segmentation error).
- `biological_qc(table, labels, parent_labels=)` — returns the table with the flag columns + a per-object
  `qc_flags` summary string + a per-flag count report on `.attrs['qc_report']`.

**The cardinal rule: flag, never filter.** Excluding objects is the user's explicit decision — the
no-silent-gates contract, and the exact failure the filter-sensitivity programme exists to catch. Flags
are worded as **observations** ("touches image border", "unusual size"), never verdicts ("bad cell") — a
mitotic or dead cell is real data. `biological_qc` **never removes a row** (pinned in a test).

Tests (`core`): `tests/test_biological_qc.py` — the injected-outliers-only detection, the **cry-wolf**
test (a clean population flags nothing), the **MAD-robustness** test (added outliers don't move the
inliers — what mean/SD gets wrong), containment, and the flag-don't-filter row-count contract. Full
`pytest -m core` green (1132).

**Deferred** (Part B, honest friction): surfacing the flags in the consolidated long table and the QC
report. The flags need a **label mask** at compute time, which the consolidated table's stream-from-CSV
path does not carry, so the computation must be wired upstream where the mask exists; the QC-report
section lands in the 1760-line `data_qc_tools`. The module is the reusable core those integrations consume.

## [1.6.151] - 2026-07-19
### Added — **Cohort selection: a GROUP as a typed selection target, so histogram bins / box-violin groups / aggregates select honestly.**
The top deferred-interaction item, and the prerequisite comparative-phenotyping increment 3 was blocked
on. A selection could only be a *set of entity ids*, so a histogram bar or a box/violin condition — a
*group* of objects defined by a range or a label — could not be selected without losing *why* those
objects belong together. Additive; the existing selection model is unchanged.

- **`Cohort`** (new frozen dataclass in `selection_service.py`): `members` + a human-readable
  `definition` ("area ∈ [12, 18) µm²", "genotype=WT") + a `kind` (`bin`/`group`/`aggregate`/`filter`).
  `SelectionState` gains a `cohort` field — **additive**: every existing consumer of
  `selected`/`hovered`/`pinned` is untouched.
- **`select_cohort(cohort, source)`** rides the existing service (same echo-suppression, generation
  counting, deferred lane). It sets `selected` to the members TOO, so a cohort-**unaware** view degrades
  gracefully — it reads `selected` and highlights every member — while a cohort-aware view reads `cohort`
  for the definition and count. **The image/labels overlay therefore highlights all members for free**
  (it already reads `selected`, k points). `clear` / `select_entity` / `toggle` clear the cohort; pins
  survive.
- **Comparative-figure groups emit cohorts** (`comparative_figures`): clicking a condition's unit-mean
  marker selects that whole condition as a cohort ("WT · 8 objects"), while an object point still selects
  one entity — nearest-wins. This unblocks the increment-3 box/violin case.
- **A cohort is a SELECTION, not a FILTER** — it never mutates the DataFrame or the analysed population
  (pinned in a test; the boundary noted in the docstrings). That is the deferred FilterStore's separate job.

New tests (`core`): `tests/test_cohort_selection.py` (round-trip, graceful degradation, echo-suppression,
clear-keeps-pins, selection≠filter) + a comparative-group cohort test. Full `pytest -m core` green (1124).

**Deferred** (clean follow-ons, the foundation now unlocks them): the histogram-bin emitter and the
aggregate-table-row emitter ("summarizes N objects" in the dock) — each is its own adapter; the emit
pattern is established by the comparative case, and rendering already works via the members-in-`selected`
degradation.

## [1.6.150] - 2026-07-19
### Changed — **Decompose `batch_step_registry.py`: 1663 → 432 lines (−74%), the fourth (and last-tracked) concentration point.**
The one god-file that had *grown* across the audit window, and the easiest to move safely — flat, no
classes, 26 replay handlers with the identical `(state, image_path, params, output_dir)` signature,
grouped by name prefix, and covered by a strong net (route-equivalence exercises batch replay end-to-end;
the OperationSpec composition guard reads `_STEP_MAP`). Behaviour-preserving move-don't-rewrite.

- The **26 `replay_*` handlers** moved into a new **`pycat.batch.steps`** package, split by prefix family
  (`io_steps`, `preprocessing_steps`, `segmentation_steps`, `brightfield_steps`, `invitro_steps`,
  `analysis_steps`), and the **10 shared helpers** into ONE `_common.py` (imported, never duplicated).
- **`_STEP_MAP` stays in `batch_step_registry.py`** and imports the handlers — the dispatch table must
  live in one place (it's what the composition guard reads). `register_all_steps` / `step_operations` /
  `_STEP_MAP` / `_STEP_OPERATIONS` are unchanged; no code imported an individual handler directly, so no
  re-exports were needed.
- **`replay_background_removal` deliberately stayed** in `batch_step_registry.py`: `test_batch_matches_the_recording`
  reads its SOURCE from that file (a white-box scale-logic check), so moving it would break a test the
  spec forbids editing — the target is met without it.
- New `tests/test_batch_step_map.py` (`core`): every `_STEP_MAP` entry resolves to a 4-arg callable —
  catches a bad move instantly. Line ratchet lowered 1663 → 432; the few moved broad handlers annotated
  `# broad-ok:`. Route-equivalence + the composition guard confirm replay is byte-for-byte unchanged.

Full `pytest -m core` green (1117). With this, all four tracked concentration points are decomposed —
`vpt_ui` (−54%), `ui_modules` (−41%), `file_io` (−42%), `batch_step_registry` (−74%).

## [1.6.149] - 2026-07-19
### Changed — **`ui_modules.py` decomposition, Phase 2: extract `MenuManager`. 5572 → 3268 lines (−41%).**
With Phase 1's menu-contract net in place, the safe move: `MenuManager` (2164 lines, ~33 methods, 39% of
the file) is lifted **verbatim** to a new **`src/pycat/ui/menu_manager.py`**, taking its two exclusive
helpers with it (`_FileDropFilter`, and the two session-restore method maps). `ui_modules.py`
**re-exports** it, so `from pycat.ui.ui_modules import MenuManager` (CentralManager, the smoke tests)
keeps working; `menu_manager.py` imports nothing from `ui_modules`, so there is no cycle.

- **The menu-contract snapshot matched, unchanged** — the Phase-1 net (`test_menu_contract.py`, now
  location-aware so it follows the class) confirms **not one of the 111 actions or 25 menus moved,
  renamed, or reordered**. That is the guarantee that makes a 2164-line move of an under-tested file
  safe, and it is exactly what the file was left un-split for the lack of.
- **`ui_modules.py`: 5572 → 3268 lines (−41%)**, under the ≤3600 target. Line ratchet lowered to 3268;
  `menu_manager.py` ratcheted at its 2344-line size.
- Behaviour-preserving: no rewrites, everything re-exported, full `pytest -m core` green (1115).
- **Not touched** (spec's explicit scope limit): `BaseUIClass`, `ToolboxFunctionsUI`, and the
  `AnalysisMethodsUI` family — moving those without behavioural tests is the blind refactor the ratchet
  warns about; a later spec can extend the same net to them.

**Deferred** (clean follow-up): `MenuManager`'s internal split into `ui/menus/{napari_menus,grid_view,
metadata_dialogs}.py` — that further thins `menu_manager.py` (not `ui_modules.py`, whose target is
already met) and is its own increment.

## [1.6.148] - 2026-07-19
### Added — **`ui_modules.py` decomposition, Phase 1: the menu-contract verification net (ships before any move).**
`ui_modules.py` is the file the codebase deliberately left un-split — its own ratchet warns *"a refactor
whose only verification is 'it still imports' ships bugs"* (a real one hid here once: a pixel-size gate
wrapped in `except: pass`, 1.5.509). So this spec builds the verification FIRST and ships it on its own;
**no code is moved yet.**

- **`tests/test_menu_contract.py` (`core`, pure AST)** — extracts the entire menu tree from
  `ui_modules.py` (every top-level menu + submenu title and, in order, the action labels under it: **25
  menus, 111 actions**) and asserts it against a committed reference (`tests/menu_contract_snapshot.json`,
  regenerate with `python tests/test_menu_contract.py --regenerate`). A blind refactor that silently
  drops, renames, reorders, or moves an action changes the contract and fails — the single highest-value
  guard, and exactly the regression hardest to catch by hand. Plus a non-triviality floor (so the guard
  can't pass vacuously) and a static check that `_setup_menu_bar` still assigns each guarded install's
  result attribute (the 1.5.509 bug class).
- **`tests/test_ui_smoke.py` (Qt-smoke, offscreen)** — the runtime companion: constructs the real
  `MenuManager` and asserts the guarded installs actually RAN (`_pycat_marker_action`, `palette_action`,
  a populated `_command_registry`, the layer-event backstops), that **every snapshot label became a real
  registered action** (cross-checking the static contract against the live menu), and that the guarded
  scene-switcher entry point is safe to invoke. *(These need an OpenGL-capable display, like the existing
  `test_ui_smoke` tests; they run in CI/with a display, not in this headless GL-less sandbox.)*

Full `pytest -m core` green (1115). Phase 2 (extracting `MenuManager` into its own module(s)) is a
separate version, gated on this net.

## [1.6.147] - 2026-07-19
### Changed — **Finish the `file_io.py` decomposition: the lazy wrapper + the exception conversion.**
Completes the two follow-ups deferred from 1.6.146.

- **`_ZarrTYX` → `file_io/lazy_sources.py`** (move 5) — the IMS lazy wrapper joins `_TiffPageStack` &
  friends in the Qt-free lazy-wrapper home. `lazy_sources` stays Qt-free (the subprocess headless test
  confirms it); `file_io` re-exports it. `file_io.py`: 1670 → **1633 lines (2805 → 1633 overall, −41.8%)**.
- **Exception conversion in the moved code** — the 45 broad `except Exception` handlers that moved into
  `naming.py` / `dialogs.py` / `stack_openers.py` are now annotated `# broad-ok:` with body-matched
  reasons (metadata probes → `None`, Qt/layer-inspection robustness, format-open log-and-continue). None
  were converted to raises — they are graceful-degradation swallows, not masked scientific failures, so
  a raise would change behaviour. The `file_io` broad-handler ratchet is lowered **284 → 239**.

Full `pytest -m core` green (1112 passed). This closes the `file_io.py` decomposition: dialogs, the
naming/pixel helpers, the format openers, and the lazy wrapper all in their proper homes; the file is
orchestration/wiring, not format-specific pixel logic; both the line ratchet (→1633) and the exception
ratchet (→239) are lowered so it cannot regrow.

## [1.6.146] - 2026-07-19
### Changed — **Decompose `file_io.py`: 2805 → 1670 lines (−40.5%), the second god-file after `vpt_ui`.**
Behaviour-preserving refactor — move code into its proper homes, no rewrites, no new features. Same
discipline as the `vpt_ui` decomposition (core green between each move, no test edited to make a move
pass, drop-guard move-records for everything relocated).

**Measured: `file_io.py` 2805 → 1670 lines (past the ≥39% target).** Four moves:
- `StackLoadCancelled` → `utils/errors.py` — kept a plain `Exception`, **deliberately not a
  `PyCATError`**: it is a user-cancel control-flow signal, not a failure, so `except PyCATError` must not
  swallow it. Re-exported.
- The two Qt dialogs (`LayerDataframeSelectionDialog`, `ChannelAssignmentDialog`) → `file_io/dialogs.py`.
- The pure pixel-size / naming helpers (`_lazy_contrast_limits`, `_tiff_pixel_size_um`,
  `_ome_pixel_size_um`, `_lazy_backing_label`) → a new **`file_io/naming.py`**, now headlessly testable
  (`tests/test_file_io_naming.py`).
- The three format-specific stack openers (`_open_stack_ims`, `_open_stack_generic`,
  `_open_czi_streaming`, ~600 lines) → **`file_io/stack_openers.py`** as a mixin. They were kept as a
  mixin, not standalone functions, because each writes `FileIOClass` state and calls sibling methods — a
  function form would take 10+ params or pass `self`, the "worse seam" the spec warns against.

Everything moved is **re-exported from `file_io.py`**, so every existing import path and call site still
works. The `file_io/file_io.py` line ratchet is lowered 2805 → 1670 so it cannot regrow. Full
`pytest -m core` green (1112 passed).

**Blocker recorded** (per the spec's "report blockers" rule): `derive_layer_name` and
`_clean_filename_token` could NOT move — two tests (`test_channel_modality`, `navigator/test_loader_fixes`)
AST-parse `file_io.py` *by path* and pull those functions out by name, so relocating them would fail
tests the decomposition is not allowed to edit. They stay put; the target was met without them.

**Deliberately deferred** (clean, non-target-critical follow-ups): moving the `_ZarrTYX` lazy wrapper to
`lazy_sources.py` (−63 lines more), and the broad-`except`→typed-error conversion in the moved code (the
handlers moved *within* the `file_io` package, so the package count is unchanged — a conversion sweep is
its own focused pass).

## [1.6.145] - 2026-07-19
### Changed — **Slim the distribution: stop shipping ~20 MB of docs/assets per release.**
PyPI enforces a total project-size quota and PyCAT was consuming it at ~25 MB per release, dominated by
documentation assets and unreferenced logos bundled into the artifacts. This is **build-config + asset
deletion only — no source-code change, no behaviour change.**

**Measured, before → after (`python -m build`):**
- **sdist: ~20.6 MB → 1.76 MB.** Dropped `docs/` (18 MB, mostly a 13 MB screenshot gallery) and
  `notebooks/` (816 KB) from the sdist `include` — they live in the git repo + the published docs site,
  and a `pip install` does not need them.
- **wheel: ~2.7 MB → 1.83 MB.** Deleted four unreferenced logo PNGs from `src/pycat/icons/`
  (`pycat_logo_1024.png` 572 KB, `pycat_logo-2.png`, `pycat_logo.png`, `pycat_logo_256.png` — ~880 KB
  total, grep-verified referenced nowhere in code). The two the app actually uses stay
  (`pycat_mark.png`, `pycat_logo_512.png`; icons now total 206 KB).

- **`.DS_Store`** — untracked the three tracked macOS files and added `**/.DS_Store` to the **wheel**
  exclusions (the sdist already excluded them, the wheel path did not).
- **`.xlsx` kept, deliberately.** `src/pycat/navigator/data/*.xlsx` (question tree, module contracts, tag
  hierarchy) **are read at runtime** by `navigator/loader.py` (`openpyxl.load_workbook` →
  `load_question_tree`/`load_raw_modules`/…), so they must ship — confirmed by grep, left in place.
- **Nothing runtime broke:** the built wheel still ships all required data — the 3 `.xlsx`,
  `navigator/data/operation_catalog.json`, and `utils/layer_bindings.json` (the dropdown-binding table).
  `run-pycat`'s icons (`pycat_mark`, `pycat_logo_512`) are intact.

`tests/test_distribution_size.py` (new, `core`) is the ratchet: it fails if `docs`/`notebooks` return to
the sdist, if an unreferenced or oversized icon ships (icons < 250 KB, each referenced or allow-listed),
if a `.DS_Store` reappears under `src/`, or if the wheel drops its `.DS_Store` exclusion. Full
`pytest -m core` green.

**Note:** this stops the bleeding but does **not** reclaim quota already consumed by past releases —
deleting old releases is a separate, manual, irreversible action on the PyPI web UI.

## [1.6.144] - 2026-07-19
### Added — **Comparative phenotyping increment 3: unblock brushing + the UI entry point.**
The comparative-figures library, superplots, and anti-pseudoreplication stats already shipped; this
completes the spec's Part D (brushing) and Step 2 (a UI entry point), which were blocked on a missing
per-object entity id.

- **The unblock — a resolvable `entity_id` in the consolidated table.** Object tables are already stamped
  with `_pycat_entity_id` by `stamp_entity_ids`, but `melt_object_measurements` dropped it. It is now
  carried through into a new `entity_id` column (`consolidated_table._CORE_COLS`), so a comparative-figure
  object row knows the global id the `SelectionService` already speaks — no fabricated id, no second
  keying scheme. Blank when the source table was never stamped.
- **Part D — single-entity brushing** (`comparative_figures._attach_object_brushing`, wired into
  `condition_comparison(..., selection_service=…)`): clicking an object point selects that entity through
  the EXISTING contract, self-highlighting on emit (the service suppresses a view's own receive) and
  ringing the matching point when a selection arrives from another view. Qt-free and headless-testable
  (matplotlib clicks don't fire under Agg, so the wired handlers are exposed on `fig._pycat_brushing`).
  **Cohort selection** (clicking a unit/condition marker to select the cohort it summarizes) is left as
  the noted-blocked seam — it needs the typed/cohort-target `SelectionState` still deferred on the
  interaction-layer roadmap. No second selection path was built, per the spec.
- **Step 2 — the UI entry point** (`ui/comparative_figures_ui.py`, menu: *Analysis Methods → Comparative
  Figures (batch consolidated table)*): pick a `consolidated_long.csv`, choose measurement / condition /
  biological unit / plot kind / optional test, and render the replicate-honest superplot with brushing
  wired to the shared `SelectionService`, alongside the inspectable summary frame.

New tests (`core`): `tests/test_comparative_brushing.py` (entity-id carry-through, object-click emits the
entity, a selection rings the point, no brushing without a service, the UI condition-field helper).
Two consolidated-table schema assertions updated for the new column. Full `pytest -m core` green (1108).

### Notes
- **Needs your in-app verification** (viewer-coupled): after a batch that wrote `consolidated_long.csv`,
  open *Analysis Methods → Comparative Figures*, render a superplot, and confirm clicking an object point
  brushes it in any open object layer (and vice-versa).

## [1.6.143] - 2026-07-19
### Changed — **Lightweight operation catalog: discovery no longer imports science modules; runnability reaches the Run buttons.**
Two linked findings. **Finding 1 — discovery is decoupled from implementation imports.**
`iter_operation_specs(live=False)` (the new default) builds the full `OperationSpec` catalog by reading
the generated `operation_catalog.json` — **without importing a single science module**. A missing
optional/specialist dependency (`pywavelets`, a GPU library) no longer makes a third of the operation
vocabulary undiscoverable; the operation is *listed*, and only fails — precisely, for that one op — when
it is actually run.

- `live=True` keeps the import-and-introspect path; the catalog GENERATOR (`build_catalog_document`) and
  the regeneration/drift guard now pin `live=True` explicitly (a generator that read its own output would
  be circular). The drift guard is what keeps the JSON faithful to the live decorators, so reading the
  artefact is safe.
- `OperationSpec` gains `module` + `function` (the executor coordinates, populated in both paths).
  `resolve_operation(spec)` imports the implementation **at call time** and raises a precise
  `OptionalDependencyError` naming the missing dependency for that operation. `module_importable(spec)`
  and `operation_availability(spec, available, check_module=…)` compose the requirement gate with an
  optional import probe.

**Finding 2 — consume the existing `OperationSpec` fields in the live UI.**
- **Run-button gating.** New `ui/operation_gating.py`: `session_facts(central_manager, viewer)` derives
  the available facts (`z_stack`/`time_axis`/`pixel_size`/`two_channels`/`gpu`) from the same predicates
  the tools already use (`has_time_axis`, `has_real_pixel_size`, `gpu_available`, the layer `axis_order`
  tag, `n_channels`); `gate_run_button(button, requirements, …)` disables the button with the stated
  reason (*"needs a 3D z-stack"*) and re-checks as layers change. **Fail-open** — any gating error leaves
  the button enabled. Wired into the five requirement-declaring ops that have a Run button: the three
  z-stack 3D tools (Remove Background / Segment Cells / Segment Condensates), VPT Link Trajectories, and
  the Temporal Enhancement competition. *(The other three requirement-declaring ops — `dog_3d`,
  `gabor_3d`, `gaussian_3d` — are internal tri-planar pipeline helpers with no UI button of their own, so
  they are covered only by the headless `operation_availability` API, not a widget.)*
- **Batch audit.** The recorded-steps dialog now shows each step's declared operation composition
  (`step_operations`), so replay is auditable in the UI, not only in tests.
- **Layer-input filtering** (spec's item 2) is already served by the existing tag-based `binding`
  mechanism in `create_layer_dropdown`, which resolves layers by `role`/`target` exactly as `inputs`
  would — no change needed.
- Per the spec, **no new `requirements` values** were added; `module_importable` is surfaced through the
  availability API, not the per-op vocabulary.

New tests (`core`): `tests/navigator/test_lightweight_catalog.py` (import-free discovery, lazy resolver
with precise errors, availability) and `tests/test_operation_gating.py` (session facts, disable-with-
reason, fail-open). Full `pytest -m core` green (1102 passed).

### Notes
- **Needs your in-app verification** (viewer-coupled): open a 2D image and confirm the three "…(3D)"
  Run buttons in the Z-Stack tool are disabled with a "needs a 3D z-stack" tooltip, and enable when a
  z-stack is loaded; likewise VPT "Link Trajectories" and the Temporal Enhancement "Run competition"
  button on a single frame vs a time-series.

## [1.6.142] - 2026-07-19
### Added — **Focus selection: close the spatial debris layer (optional `mask=` on the focus-series scorers).**
Completes the "best frame must not be the sharpest DEBRIS" rubric. The **statistical** layer already
shipped (1.6.91): `math_utils.robust_focus_energy` trims the top ~1% of per-pixel magnitudes, so a
*small* out-of-plane speck cannot hijack the chosen frame. This adds the **spatial** layer for the two
focus-series scorers, for the case trimming cannot reach — a *large* out-of-plane structure.

- `bf_analyse_focus_series` (`brightfield_tools`) and `analyse_frame_quality` (`condensate_physics_tools`)
  gain an optional **`mask=`** — a single `(H, W)` boolean applied to every frame, or a `(T, H, W)`
  per-frame stack. When supplied, the focus metrics (Brenner/Tenengrad/normalised variance;
  Laplacian variance/entropy/gradient energy) are scored **inside the masked region only**.
- **Masked pixels are extracted, never zero-filled** — a zero-fill outside the mask would create a
  high-gradient artefact at the boundary that inflates every metric. Gradients/Laplacians are computed on
  the full real frame, then aggregated over masked pixels.
- **`mask=None` is byte-identical** to the previous whole-frame behaviour; a full-True mask reduces to
  exactly the whole-frame numbers (pinned in the test). Mean intensity stays whole-frame (it feeds
  bleaching detection, not focus).
- New shared helper `math_utils.resolve_frame_mask` (per-frame mask, raises on shape mismatch — a wrong
  mask is worse than whole-frame, so it fails loudly). `_frame_entropy` / `_frame_gradient_energy` gained
  a `mask=` too. The trim layer is kept **on top of** the mask — they are complementary, not alternatives.
- **No caller fabricates a mask.** The frame-QC UIs run on a raw stack with no biological mask in hand, so
  they pass `None`; the capability is there for a caller that genuinely has a region.

`tests/test_focus_debris.py` (new, `core`): a deliberately adversarial fixture (debris ≥8% of the frame,
above the 1% trim, sharper per-pixel than the condensate) proves `mask=None` picks the debris frame and
`mask=` picks the condensate — for both scorers; a clean stack picks the same frame either way; the trim
layer defeats small debris independently; a wrong-shaped mask raises. Full `pytest -m core` green.

## [1.6.141] - 2026-07-19
### Added — **VPT results are now one combined dock with full four-way brushing and a bucket pager.**
The Video Particle Tracking results used to surface as three disconnected top-level things — the 2×2
figure in pyplot windows, the per-track table in a standalone `QDialog`, and only the histogram as a real
dock — and only the MSD plot actually brushed the others. This replaces that with **one dockable widget**
(`toolbox/vpt/results_dock.py`, `_VptResultsDockMixin`): the 2×2 figure canvas on the left, the per-track
table on the right (a horizontal splitter), driven the default way; the `_plots_consolidated` checkbox
still opts out to the old pop-out windows + table dialog.

- **The combined dock is also the brushing fix.** The table→plot / image→plot highlights were silently
  no-op'ing against dead canvases because the old pyplot/`QDialog` surfaces were disposable and took their
  `_msd_line_registry` / `_track_table_registry` with them when they closed. Embedding the figure canvas
  and the table in one persistent dock keeps the highlight targets alive.
- **Image → everywhere now works regardless of the active napari layer.** The bead-click picker was a
  *layer* callback, so napari only delivered it when "Bead Picker" was the active layer — the usual case
  (image/Tracks layer selected) left image→plot/table dead. Added a **viewer-level** pick
  (`_install_viewer_bead_pick`) that fires whatever layer is active, resolving the **nearest** bead on the
  current frame within a pixel radius (`_nearest_bead_tid`) — napari's exact-hit `get_value` never lands
  on one of hundreds of thousands of tiny dense beads, which is why bead picking looked dead.
- **Centered trajectories are a real fourth SelectionView** (`vpt.centered`). `_draw_centered_tracks` now
  keeps a per-track line/coords map with promote/demote and a click hit-tester, so clicking a centered
  path selects that track everywhere, and a selection from any other view emphasises its centered path
  (promoting one off the current page on demand). It was inert before.
- **Bucket pager** — the MSD spaghetti and centered panels cap how many tracks they draw (the spread stops
  changing past ~100). A `◀ Prev / Next ▶` pager with a live bucket-size spinbox pages through literal
  slices of tracks (no representative sampling), so **every track is on a plot on some page**; page 0 is
  the representative ensemble. A selection survives page turns (re-applied on the new page's artists).

### Notes
- **Needs your in-app verification** (viewer-coupled — headless-green only): open a VPT result and confirm
  (1) the combined dock lays out figure-left / table-right; (2) clicking a bead reveals it AND highlights
  the table row + MSD curve + centered path, from any active layer; (3) clicking a table row, an MSD
  curve, or a centered trajectory brushes all the others; (4) the pager steps through buckets and the
  highlight persists across pages.
- `analysis_plots` gains `only_tids` on `_draw_msd_into` / `_draw_centered_tracks` (draw an exact slice,
  no sampling) and a registry/hit-test on the centered panel; the pop-out path is unchanged.
- Full `pytest -m core` green; ratchets (complexity, drop-guard, exception budget, silent-fallbacks) green.

## [1.6.140] - 2026-07-19
### Changed — **Reliability adoption: the two zero-bar widgets now run off the Qt thread through the operation runner.**
Completes the reliability spec's Part 2 — the proof adoption. The two analyses whose slow work is the
computation (Cascade RF segmentation in `contrast_cascade_ui`, per-half-cycle rip fitting in
`fd_curve_ui`) now run through `OperationRunner` on a worker thread behind `run_with_progress`'s modal
progress dialog, and create their layer / show their tables back on the main thread via `on_result`. A
failure is transported to a `napari_show_warning` via `on_error`. **Responsive, not merely visible** —
no "Not Responding" during a multi-second segment or fit.

- This **supersedes the on-thread inline `QProgressBar`** those widgets got in progress-part-2 (1.6.132):
  the modal dialog (driven by the same tool `progress_callback`, forwarded by the runner) is now the
  progress indicator, so the inline bars were removed. `test_progress_analysis_half.py` is updated to
  ratchet the stronger guarantee — each widget must route its slow analysis through `OperationRunner`
  (a regression to a direct on-thread call fails), while the tool entry points still accept
  `progress_callback` (unchanged).
### Notes
- Only these **two** widgets were converted (the spec's "prove the runner, then migrate incrementally").
- **Needs an in-app glance** (viewer-coupled): confirm the modal dialog appears and the UI stays
  responsive during a real Cascade-RF segmentation / rip fit, and the result lands correctly.
- Full `pytest -m core` green.
- Files: `src/pycat/toolbox/contrast_cascade_ui.py`, `src/pycat/toolbox/fd_curve_ui.py`,
  `tests/test_progress_analysis_half.py`.

## [1.6.139] - 2026-07-19
### Added — **Reliability: typed failures + an exception ratchet + one operation runner.**
The two reliability findings the audit flagged as unchanged across revisions — broad exception handling
and per-widget background execution — addressed as **ratchets + one shared mechanism**, not a sweep.

**Part 1 — typed failures + the exception ratchet.**
- **`utils/errors.py`** — a small `PyCATError` family (`UnsupportedFormatError`,
  `MetadataUnavailableError`, `InvalidCalibrationError`, `ScientificAssumptionError`,
  `OptionalDependencyError`, `LayerResolutionError`), exactly the failures the code already
  distinguishes. `ScientificAssumptionError`/`UnsupportedFormatError` also subclass `ValueError`, so
  existing `except ValueError` callers keep working while new code can catch the family.
- **`tests/test_exception_budget.py`** — a per-package ratchet on un-annotated `except Exception`, at
  today's counts (1222 total across the tree), that only decreases. A deliberate handler annotates itself
  `# broad-ok: <reason>` (mandatory reason) and drops out of the count. This stops the measured growth at
  zero cost — the complexity budget's principle, applied to swallows.
- **First scientific conversions:** the `calibration.py` ΔG/curve gates (non-positive concentration,
  Celsius-as-Kelvin, too-few-points, bad schema) now raise typed errors that **name the assumption**,
  instead of a bare `ValueError`. `test_calibration.py` passes unmodified (the typed errors are
  `ValueError` subclasses).

**Part 2 — one operation runner.**
- **`utils/operation_runner.py`** on top of the existing `qt_worker` (no second threading mechanism):
  `execute(fn, progress=, on_result=, on_error=, cancellation=, generation=)` standardizes worker
  policy, **main-thread marshalling** of the result, **stale-result suppression** (a generation counter,
  so a slow result cannot overwrite a newer request), cooperative **cancellation** at progress
  boundaries, and **typed error transport** to `on_error`. Fully headless-tested (`run_with_progress`
  runs synchronously with no event loop): result delivered on the caller's thread, progress forwarded
  verbatim, a superseded result discarded, cancel stops the work, a typed error reaches `on_error`.
### Notes
- **The 2-widget adoption is NOT in this release** (deliberately). Routing the two zero-bar widgets'
  slow analyses through the runner moves them *off* the Qt thread behind `run_with_progress`'s modal
  dialog — which supersedes the *on-thread inline bar* progress-part-2 (1.6.132) added, and so conflicts
  with that spec's inline-bar ratchet. Resolving that (inline bar vs modal dialog) is a UX decision and
  the off-thread behaviour needs an in-app check, so the adoption is held for that pass. The runner is
  proven by its tests; adopting it is the demonstration.
- Full `pytest -m core` green.
- Files: `src/pycat/utils/errors.py`, `src/pycat/utils/operation_runner.py`,
  `src/pycat/utils/calibration.py`, `tests/test_exception_budget.py`, `tests/test_operation_runner.py`.

## [1.6.138] - 2026-07-19
### Changed — **vpt_ui decomposition, step 3: three more adapter modules — `vpt_ui.py` 1778 → 1139 lines (2458 → 1139 overall, −54%).**
The rest of the decomposition. Three more responsibility groups moved out of `vpt_ui.py` into the `vpt/`
package as behaviour-preserving mixins, taking the file from the panels-step 1778 to **1139 lines — a
54% reduction from the original 2458**, more than double the ≥25% target. `vpt_ui.py` is now close to
construction, wiring, and composition only.

- **`vpt/napari_adapter.py`** (`_VptNapariMixin`) — the napari-facing layer/overlay/reveal methods:
  Tracks/Points layer build, the picked-bead ring, reveal + navigate + camera, session-view restore.
- **`vpt/table_adapter.py`** (`_VptTableMixin`) — the track-table methods: per-track table build,
  row↔entity, table selection callback.
- **`vpt/msd_adapter.py`** (`_VptMsdMixin`) — the MSD-plot methods: per-track highlight/hit-testing,
  track-length histogram, plot selection callback.
### Notes
- **Behaviour-preserving moves, not rewrites** — every method body is byte-for-byte unchanged; only the
  module changed, and `VideoParticleTrackingUI` composes the four mixins. Verified up front that each
  moved group uses only `self` + imports (zero module-level `vpt_ui` names), and each new module copies
  `vpt_ui`'s import block verbatim, so no name can be missing.
- **Every pre-existing VPT/selection/brushing test passes unmodified.** Only the two bookkeeping ratchets
  were updated as designed: the drop guard's `_DELIBERATE` records the moved functions (it keys by
  filename, and nested pick/row/close handlers moved with their parents), and the `vpt_ui.py` ceiling is
  lowered to **1139**. Full `pytest -m core` green.
- **Deliberately NOT rewritten here:** formalising the msd/table adapters as standalone `SelectionView`s
  (`view_id`/`apply_selection`/`close`) covered by the shared contract suite is the interaction-layer §8
  *rewrite*, out of scope for a move. The MSD brushing stays its registry-based model. **In-app
  verification of the live VPT widget is still needed** (no headless test exercises the constructed
  widget — the VPT tests instantiate a bare subclass).
- Files: `src/pycat/toolbox/vpt_ui.py`, `src/pycat/toolbox/vpt/napari_adapter.py`,
  `src/pycat/toolbox/vpt/table_adapter.py`, `src/pycat/toolbox/vpt/msd_adapter.py`,
  `tests/test_nothing_was_dropped.py`, `tests/test_complexity_budget.py`.

## [1.6.137] - 2026-07-19
### Changed — **vpt_ui decomposition, step 2: the panel builders MOVE out — `vpt_ui.py` 2458 → 1778 lines (−28%).**
The measurable win the external audit asked for: it charged that the new abstractions were being added
*beside* the concentration points rather than absorbing them, and set as its success metric that one
concentration point becomes **materially smaller**. This does it for `vpt_ui.py`, the audit's named
first target — a **28% reduction, past the ≥25% goal**.

- The five pure-layout panel builders (`_add_bead_detection`, `_add_tracking`, `_add_microrheology`,
  `_add_host_segmentation`, `_build_per_track_metrics`) moved into a new **`toolbox/vpt/panels.py`** as
  the `_VptPanelsMixin`. `VideoParticleTrackingUI` now *composes* them (inherits the mixin) instead of
  implementing them — the file is closer to construction-and-wiring only.
- **Behaviour-preserving move, not a rewrite:** the method bodies are byte-for-byte unchanged; only the
  module they live in changed. `setup_ui` (the top-level construction/composition **and** the
  pixel-size-gate install) deliberately **stays** in `vpt_ui.py`, per the spec's rule that the file
  retains composition — which also keeps the pixel-size-gate contract satisfied in place.
### Notes
- **Every pre-existing VPT/selection/brushing test passes unmodified** — the move changed no behaviour.
  Only the two *bookkeeping* ratchets were updated as designed: the drop guard's `_DELIBERATE` records
  the five moves (it keys by filename, so a move reads as a vanish), and the per-file ceiling for
  `vpt_ui.py` is **lowered 2458 → 1778** (the ratchet moving down). Full `pytest -m core` green.
- **The remaining three adapter modules (napari / table / msd) are NOT in this release.** The line
  target is already met by this one extraction, and the first extraction surfaced a real, subtle failure
  (a length-reporting method and its pixel-size gate split across the two files) that was invisible until
  the full suite ran — proof that these need verification between steps, and that the *live VPT widget*
  (not exercised by any headless test — the VPT tests instantiate a bare subclass) needs an in-app check
  before more surgery. The napari/table/msd extractions, and formalising the msd/table adapters as
  `SelectionView`s (the deferred interaction-layer §8 rewrite), are the follow-on.
- Files: `src/pycat/toolbox/vpt_ui.py`, `src/pycat/toolbox/vpt/__init__.py`,
  `src/pycat/toolbox/vpt/panels.py`, `tests/test_nothing_was_dropped.py`, `tests/test_complexity_budget.py`.

## [1.6.136] - 2026-07-19
### Added — **Per-file line ratchets on the four concentration points (vpt_ui decomposition, step 1).**
Step 1 of the `vpt_ui.py` decomposition spec, and the part the spec itself flags as the
highest-value/lowest-cost: an external audit measured that while the new abstractions
(`SelectionService`, `OperationSpec`, the plot backends, the scene stack) were added, the god-files grew
or held **beside** them — `ui_modules.py` +18, `file_io.py` +18, `batch_step_registry.py` +50,
`vpt_ui.py` flat. The architecture is real but so far additive. `test_complexity_budget.py` now carries a
**whole-file line ceiling per concentration point**, set at today's value, that only ever moves DOWN — so
a new abstraction added beside a god-file instead of absorbing code fails the build. This stops the
measured drift immediately, at zero refactoring cost.
### Notes
- Ceilings (today's values): `vpt_ui.py` 2458, `ui_modules.py` 5573, `file_io.py` 2805,
  `batch_step_registry.py` 1663. Lower one after a real extraction; never raise one.
- **The extractions (spec steps 2–5) are NOT in this release.** They are behaviour-preserving *moves* of
  responsibility out of `vpt_ui.py` into a `vpt/` adapter package — but its `_build_*` UI-construction
  methods are not exercised by any headless test (the VPT tests instantiate a bare subclass that skips
  `__init__`), so a moved-but-broken panel would pass import + the AST/method tests yet fail only in the
  live widget. That makes each extraction viewer-coupled work needing in-app verification, done one at a
  time — flagged rather than shipped blind.
- Test-only, no production change. Full `pytest -m core` green.
- Files: `tests/test_complexity_budget.py`.

## [1.6.135] - 2026-07-19
### Added — **Comparative phenotyping inc 3: cross-condition figures on the consolidated table, replicate-honest by construction.**
The visible payoff of the arc: figures that compare conditions, built on increment 2's consolidated long
table. Existing `comparative_stats.py` already carried the honest replicate-level testing; this completes
`comparative_figures.py` to the increment-3 contract — three figure types, each returning
`(Figure, summary_df)` so the numbers behind a figure are always inspectable.

- **`condition_comparison`** — the superplot: objects (light) + per-unit means (dark) + a box/violin per
  condition. **Descriptive by default** — a p-value appears only when `test=True`, and then it comes from
  `compare_conditions` (replicate-level, named, refusing loudly below the minimum).
- **`dose_response`** — measurement vs a numeric condition field; unit means ± SEM (over units, not
  objects) at each dose.
- **`measurement_matrix`** — the new figure type: a small-multiples grid, one condition-comparison panel
  per measurement, for scanning several at once.
- **`aggregate_to_unit`** — the anti-pseudoreplication step, generalised to multi-field conditions/units;
  the biological unit is **declared** (`unit_cols`), defaulting to the image when no replicate is named,
  never inferred from data shape.
- Every summary frame reports **n at each level** (`n_objects`, `n_units`) and the unit-level SEM —
  deliberately NOT the object-level SEM, which is the pseudoreplicated lie the module refuses.
### Notes
- **Pseudoreplication, refused by construction — and proven, not commented:** the headline test builds
  450 objects from 3 replicates and asserts the reported n is 3 and the error bar is *materially wider*
  than the naive object-level SEM (measured ~8×). Other `core` tests: the condition effect is recovered;
  a requested test names its unit; a condition with too few units gets a stated refusal, not a p-value.
- **Part D (brushing) is blocked on prerequisites, deliberately not faked:** routing a click to the
  `SelectionService` needs (1) the consolidated table to carry a resolvable entity id per object row (an
  increment-2 extension) and (2) the deferred cohort/typed-target `SelectionState` (interaction-layer
  §3/§4). Per the spec, selection is left unwired with the seam documented rather than built as a second
  path. The figures already show the single-vs-cohort distinction visually. A **UI entry point** (read
  the consolidated CSV, render) is the remaining viewer-coupled follow-on.
- With Parts A–C done, **comparative-phenotyping increment 3's library is complete**; heatmap/PCA/
  clustering profiling is deliberately declined per the spec. Full `pytest -m core` green.
- Files: `src/pycat/utils/comparative_figures.py`, `tests/test_comparative_figures_inc3.py`.

## [1.6.134] - 2026-07-19
### Added — **Comparative phenotyping inc 2: the batch emits one tidy `consolidated_long.csv` — the keystone comparative output.**
The `ConsolidatedLongWriter` (the long-format assembler that melts each image's per-object tables and
attaches condition + provenance columns) shipped with unit tests, but **nothing called it from the batch
loop** — so the arc's keystone deliverable was unbuilt: a study across N images was still N per-image
folders a scientist joins by hand. This wires it in.

- **`batch_processor.BatchWorker.run`** now streams a top-level `consolidated_long.csv`: after each
  image is processed it reads that image's already-written per-image CSVs (`<stem>_cell_df.csv`,
  `<stem>_puncta_df.csv`), melts them to long rows, and appends them with the image's condition
  (WT/dose/replicate…, from increment 1's resolver) and provenance (pycat version) on every row. Holds
  no other image in memory (streaming append), with a schema fixed up front from the condition-field
  vocabulary so a column can never drift mid-batch.
- **Additive:** the consolidated table sits *alongside* the existing per-image folders and removes
  nothing; an image that produced no object tables contributes no rows; conditions are blank (never
  guessed) when no metadata source is configured.
- New Qt-free helpers: `consolidated_table.records_from_output_dir` (read an image's per-image CSVs
  back as `(object_type, df)` records — the streaming-from-disk source) and
  `SampleMetadataResolver.condition_field_names` (the field vocabulary, known before any pixels are
  read, that fixes the CSV schema).
### Notes
- With this, **comparative-phenotyping increment 2 is complete** — the long/tidy table the grouped
  stats and faceting of increments 3–4 join on. (Increments 3–4's *library* code exists; their UI
  surfaces remain, tracked as follow-on.)
- Tests (`core`, headless): the per-image reader reads/skips correctly; the field vocabulary unions
  sheet + pattern; two images stream into one long table with conditions and provenance per row; an
  empty image adds no rows; the batch loop's wiring is AST-verified (`batch_processor` is Qt-bound).
  Full `pytest -m core` green.
- Files: `src/pycat/batch_processor.py`, `src/pycat/utils/consolidated_table.py`,
  `src/pycat/utils/sample_metadata.py`, `tests/test_consolidated_batch_wiring.py`.

## [1.6.133] - 2026-07-19
### Added — **Comparative phenotyping inc 1, Parts B & C: wire the condition/metadata resolver into batch + session persistence.**
Increment 1's Part A — the `SampleMetadataResolver` (attach a condition like "WT replicate 2 at 10 µM"
to an image from a sample sheet / filename pattern / in-app tag, behind one API) — shipped earlier, but
its two integration points were never wired: the resolver was only consumed by the consolidated table
(increment 2), never by the batch loop or the session manifest as the spec specified. This closes that.

- **Part B — batch** (`batch_processor.BatchWorker.run`): builds one resolver per run from the config
  (`sample_sheet_path` / `sample_filename_pattern`, the keys the batch UI populates) and writes a
  `<stem>_sample_metadata.json` beside each image's results recording the resolved `fields` + which
  `source` supplied each. **Strictly additive:** with no source configured the resolver is `None` and
  nothing is written — a metadata-less batch produces exactly the files it did before. An unmatched
  sheet row warns once (a likely filename typo), never crashes.
- **Part C — session** (`writers._write_session_manifest` + `session_loader`): an in-app condition tag
  placed in the data repository is carried into the manifest on Save & Clear and restored into the
  repository on Load Session, so a tagged image comes back tagged. **Back-compat both ways:** an
  untagged session's manifest is byte-identical to before, and a manifest written before the field loads
  as "no tag".
- New Qt-free helpers in `utils/sample_metadata.py`: `write_image_sample_metadata` (the per-image
  writer, a no-op when the resolver is `None`) and `resolver_from_config`.
### Notes
- **The condition/metadata model is now fully realized** across all three attach paths and both
  integration points — the prerequisite the consolidated table (inc 2) and comparative figures (inc 3)
  join on. This resolves the one PARTIAL item from the spec audit (Parts B/C were unwired).
- Tests (`core`, headless): the per-image writer records/omits correctly and is a no-op without a
  source; the batch loop's wiring is AST-verified (batch_processor is Qt-bound); an in-app tag
  round-trips through Save → Load; an untagged session stays back-compatible. No behaviour change when
  no metadata source is configured. Full `pytest -m core` green.
- Files: `src/pycat/utils/sample_metadata.py`, `src/pycat/batch_processor.py`,
  `src/pycat/file_io/writers.py`, `src/pycat/file_io/session_loader.py`,
  `tests/test_sample_metadata_wiring.py`.

## [1.6.132] - 2026-07-19
### Added — **Progress, part 2: the ANALYSIS half — tool-level `progress_callback` for the two zero-bar widgets.**
The materialization half of the progress work shipped in 1.6.81/82/107 (decoding a lazy stack shows a
bar and runs off-thread). This is the different problem the roadmap parked: two widgets whose slow work
is **the analysis itself**, which had **nothing on screen** during a multi-second run — the verified gap
(`contrast_cascade_ui` and `fd_curve_ui` each had zero `QProgressBar` and zero tool-side progress).
Because the slowness is the computation, a bar alone would have nothing to drive it, so the tool
functions report progress first.

- **Part A — tool-side `progress_callback`** (the `materialize_stack` signature, so `PhasedProgress`
  composes; `None` is a complete no-op):
  - `contrast_cascade_tools.cascade_rf_segment` reports at its three **stage** boundaries (build
    features → train → predict). Each stage is one opaque call, so stage-level progress is honest — a
    per-pixel bar would fake a granularity the work doesn't have.
  - `fd_curve_tools.detect_all_rips` reports **per half-cycle** — the real countable loop, where each
    iteration runs a WLC fit and is the slow part.
- **Part B — the two widgets** now build a real **`QProgressBar`** (never a `QLabel`: `setValue`
  repaints synchronously and moves on a busy thread, `setText` does not) and drive it from the tool
  callback via `PhasedProgress`.
- **Part E — a sibling ratchet** (`tests/test_progress_analysis_half.py`, AST-based so it runs headless):
  the slow tool entry points must accept `progress_callback`, and the two widgets must construct a
  `QProgressBar` driven from that callback. A future zero-feedback slow widget fails here.
### Notes
- **Part D sweep — clean.** Every existing progress indicator in the app is already a `QProgressBar`,
  not a `QLabel` — the many `setRange(0, 0)` sites (brightfield / condensate-physics / in-vitro runners)
  are *honestly indeterminate* bars for single-opaque-call steps, which the spec explicitly permits. So
  the "a status label is not a progress reporter" hazard does not exist in the tree; no conversion was
  needed. **Part C** (converting per-cell/per-object *core* runners from indeterminate to determinate)
  is identified follow-on: each needs its own tool-side `progress_callback`, and the current
  indeterminate bars are correct for the single-call steps.
- **Same honest limit as part 1:** this makes the wait **visible, not shorter**. Off-thread execution of
  the analysis is a separate, larger change.
- **In-app verification (viewer-coupled):** the *moving-bar* behaviour of the two widgets is confirmed
  structurally (tool callbacks fire correctly, verified headlessly; bars constructed + wired, verified by
  AST), but the visible bar during a real Cascade-RF segmentation / rip-fit wants an in-app glance.
- Files: `src/pycat/toolbox/contrast_cascade_tools.py`, `contrast_cascade_ui.py`, `fd_curve_tools.py`,
  `fd_curve_ui.py`, `tests/test_progress_analysis_half.py`.

## [1.6.131] - 2026-07-19
### Added — **Filtering-defaults sensitivity harness, increment 3: the partition-coefficient camera-offset default (a LIVE inverter, pinned).**
Increment 1 built the harness and proved it on two fixed inverters; increment 2 added the segmentation
SNR (offset) and ring-geometry (scale) cases and correctly excluded `bleach_r2_min`. Increment 3 is the
next prioritisation call. The survey of the remaining ~37 candidate defaults found **no new *fixed*
inverter** — but it found a **live** one worth pinning, and several non-cases worth recording so the next
pass doesn't re-litigate them.

- **`partition.client_enrichment.background` (offset sensitivity) — ADDED.** The partition coefficient
  K = (dense − bg)/(dilute − bg) is exact at any camera pedestal *provided the offset is supplied*. The
  **default `background=0.0`** asserts there is none, so a real pedestal sits in both terms and drags K
  toward 1 — measured on a true K of 30: **30 / 15.5 / 5.83 / 2.38** at pedestals 0/100/500/2000, a 12×
  error on a flagship condensate metric. The function *warns* but still returns the wrong number, so
  this is the **first case whose negative control is the current default**, not a removed form: the
  positive control supplies the offset and recovers K at every pedestal; the negative drives the same
  real function with the default 0.0 and the harness catches the inversion (both controls exercise the
  real `client_enrichment` — no reimplementation).
### Notes
- **Non-cases recorded** (in the registry comment + `DEV_NOTES.md`) so they aren't re-evaluated:
  `segmentation.min_spot_radius=2` and `client_enrichment_per_condensate shell_px=5` are **live scale
  risks** but the same scale shape already covered by `local_ring_geometry` (reported as findings, a
  production fix is separate work); `max_area_fraction=0.25` is **safe by construction** (a fraction of
  cell area → scale-invariant); `kurtosis_threshold=-3.0` is **inert** (excess kurtosis has a −2 floor,
  so `< −3` never fires); `estimate_object_size_px_brightfield` is **dead/unwired** (excluded like
  `defocus_r2_max`).
- Test-only, no production behaviour changed. Fixture reused: `fixtures_synthetic.partition_scene`
  (known K). Full `pytest -m core` green.
- Files: `tests/filter_sensitivity.py`, `tests/test_filter_sensitivity.py`, `docs/audits/DEV_NOTES.md`.

## [1.6.130] - 2026-07-19
### Added — **Multi-scene switcher: load one position at a time, lazily, and switch in place.**
A multi-position acquisition (CZI/IMS/OME-TIFF) used to load **every selected scene into memory at
once** — the load-everything profile the streaming work removed everywhere else, with no way to change
position without reopening. Now a multi-position file loads **exactly one scene, lazily**, and a dock
switches position in place.

- **`_SceneStack`** (`file_io/lazy_sources.py`) — the lazy (T, Y, X) wrapper for one scene. Reads one
  plane at a time from its **pinned** scene, refuses `__array__`, and — the headline hazard — re-pins
  the scene on **every** read, so a shared stateful reader can never serve a frame from another
  position. Switching builds a fresh wrapper, so no plane cache is shared across scenes: a stale
  previous-position frame cannot exist by construction.
- **`file_io/scenes.py`** (new, Qt-free) — `build_scene_stack`, `tag_scene_layer`/`scene_of` (scene
  identity as a queryable tag, joinable to the comparative-phenotyping sample metadata — a position is
  often a condition), `list_scenes`/`scene_index`.
- **Routing** (`file_io._open_stack_generic`) — a multi-position file now loads **one** scene (the
  first chosen, default scene 0) and tags each lazy layer with its position. **Single-scene files are
  untouched.** The several-scenes-overlaid memory footgun is gone rather than kept beside the default.
- **Per-scene calibration** (`data/data_modules.update_metadata`) — reads the **currently selected**
  scene's pixel size, not a fixed scene 0, so a switch cannot silently mis-scale (a position can
  legitimately differ).
- **The switcher dock** (`ui/scene_switcher.py`, new) — a position dropdown (File menu → "Switch
  Position / Scene"). Switching rebinds every scene layer to a fresh `_SceneStack` for the new position,
  warms the first (slow) frame **off the Qt thread** (`run_with_progress`, no "Not Responding"),
  re-reads per-scene calibration, re-tags the layers, and **stamps derived layers with the position
  they were computed on** so a mask from position 1 cannot masquerade as belonging to position 2.
### Notes
- Headless-tested (Qt-free foundation): `_SceneStack` contract + one-plane read + scene-provenance
  (`test_scene_stack.py`), the scene helpers + per-scene metadata (`test_scenes.py`), and the switcher's
  `switch_to` rebind/re-tag/stale-derived logic (`test_scene_switcher.py`, `run_with_progress` runs
  synchronously with no event loop). The AST eager-read guard auto-covers the new wrapper. Full
  `pytest -m core` green.
- **Needs in-app verification** (viewer-coupled, cannot be checked headlessly): opening a real
  multi-position file loads one position; the File-menu switcher changes it in place without a freeze or
  a stale frame. Held for that verification before release.
- Files: `src/pycat/file_io/lazy_sources.py`, `src/pycat/file_io/scenes.py`,
  `src/pycat/file_io/file_io.py`, `src/pycat/data/data_modules.py`, `src/pycat/ui/scene_switcher.py`,
  `src/pycat/ui/ui_modules.py`, `tests/test_scene_stack.py`, `tests/test_scenes.py`,
  `tests/test_scene_switcher.py`.

## [1.6.129] - 2026-07-19
### Added — **OperationSpec increment 5: `requirements` — runnability gating with a stated reason. (The OperationSpec arc is complete.)**
The last field of the OperationSpec roadmap. Increment 2's `inputs` said which *layers* an operation
consumes; increment 5 adds `requirements` — the *data/environment* preconditions it needs to be runnable
at all: a 3D z-stack, a time axis, a calibrated pixel size, two channels, a GPU. Declared on
`@tags_layer` from a controlled vocabulary, **with the validation that makes it real** — so a consumer
can gate an operation **before** the click and **say why** ("needs a 3D z-stack"), instead of letting it
fail at run time.

- **`tag_registry.REQUIREMENTS`** — a controlled name→reason map (the single source of the requirement
  vocabulary and its human-readable phrasing). `@tags_layer(requirements=…)` accepts values from it;
  an unregistered requirement is a hard error at import, like an unregistered tag.
- **`OperationSpec.requirements`** — surfaced, snapshotted into `operation_catalog.json` (regenerated),
  and covered by the drift guard / regeneration check.
- **`operation_spec.runnability(spec, available)`** → `(can_run, reason)`, plus `unmet_requirements()`
  — the gating helpers. `reason` names, in human terms, exactly what is missing, so the UI can grey the
  operation out with an explanation. `inputs` remain the *layer* preconditions (the Capability
  machinery); `requirements` are the *environmental* ones — the two are complementary.
- **`tests/navigator/test_operation_requirements.py`** — the validation: vocabulary agreement, every
  requirement carries a reason, gating returns `(False, "needs …")` when unmet and `(True, "")` once
  satisfied, and a **downward-only coverage floor** (≥ 8 ops).
- **Annotated the unambiguous tranche (8 ops):** the 3D ops need a z-stack (`gaussian_3d`, `gabor_3d`,
  `dog_3d`, `bg_subtract_3d`, `cellpose_3d`, `subcellular_segment_3d`), the temporal ops need a time
  axis (`temporal_enhance`, `drift_correct`).
### Notes
- **The OperationSpec roadmap is now complete** (increments 1–5): a typed, drift-guarded spec that is a
  graph (`inputs`/`produces`), against which batch replay is auditable (`_STEP_OPERATIONS`), that the
  Navigator is *generated from* (increment 4), and that now carries runnability gating. Wiring the gate
  into actual UI widgets, and the separate `tag_resolver` binding table, remain as follow-on consumers —
  noted, not built here.
- Staged population per the increment-2 discipline; a guessed requirement is worse than none. No
  behaviour change. Full `pytest -m core` green.
- Files: `src/pycat/utils/tag_registry.py`, `src/pycat/navigator/operation_spec.py`,
  `src/pycat/navigator/op_catalog.py`, `src/pycat/navigator/data/operation_catalog.json`, the 4
  annotated toolbox modules, `tests/navigator/test_operation_requirements.py`,
  `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.128] - 2026-07-19
### Changed — **OperationSpec increment 4: flip the Navigator catalog from validate-against-the-spec to GENERATE-from-the-spec.**
The payoff increment 1 was built for. Increment 1 made the operation catalog a *validated* committed
snapshot; increments 2–3 turned the vocabulary into a graph and made batch replay auditable against it.
Increment 4 does the flip on the lowest-risk consumer — the **Navigator catalog** — as a proven-safe
change: the operation set is now **generated from the live spec** (`iter_operation_specs()` → the
`@tags_layer`/UI decorators) instead of read from the committed `operation_catalog.json`. The decorators
are the runtime source of truth; the JSON becomes a reviewable, shippable *artifact*, no longer
authoritative at run time — the Navigator builds correctly even if the file is absent. One subsystem
only, to prove the pattern before touching UI or batch.

- **`build_catalog_document()`** (`navigator/op_catalog.py`) — new: builds the catalog document purely
  from the live spec, no file write. `regenerate_operation_catalog()` is now a thin writer over it.
- **`build_operation_registry(from_spec=True)`** — the flip: the Navigator's layer ops are generated
  from `build_catalog_document()` by default. `from_spec=False` still builds from the committed file (an
  escape hatch for tooling); the two are **equal by construction**, so this is not a behaviour change.
- **The drift guard becomes a regeneration check.** `test_operation_spec_matches_catalog.py` now asserts
  the committed JSON *equals* `build_catalog_document()` exactly — stronger than the field-by-field
  checks (it also catches provenance/ordering/field-set drift), and it proves the committed artifact is
  the generation and nothing else. The granular coverage/field tests are kept because they name *what*
  diverged. A new test proves the registry generated from the spec is identical to the one built from
  the file.
### Notes
- **No behaviour change and no JSON churn:** the committed `operation_catalog.json` already equalled the
  regeneration (it was regenerated in increment 2), so the flip changed the *source of truth*, not the
  numbers. Full `pytest -m core` green.
- Scope held to one subsystem per the roadmap — batch (`_STEP_MAP`) and UI are untouched. The remaining
  increment is **5** (`requirements`/runnability gating, wiring the spec to `capabilities.py`), plus the
  separate binding-table effort.
- Files: `src/pycat/navigator/op_catalog.py`, `src/pycat/navigator/__init__.py`,
  `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.127] - 2026-07-19
### Added — **OperationSpec increment 3: declare the batch-step → operation composition, so replay is auditable against the vocabulary.**
Increment 2 made the operation vocabulary a graph. Increment 3 connects the **batch replayer** to it.
The increment-2 survey measured a key fact: `batch_step_registry._STEP_MAP` (68 workflow steps) and the
operation catalog (79 layer ops) have **zero name overlap** — a *step* is a workflow stage
(`condensate_segmentation`), an *operation* is a layer-producing transform (`subcellular_segment`). That
is correct design, not drift, so the two vocabularies are **not merged**. The honest relationship is
**composition**: a step *invokes* one or more operations. That mapping cannot be inferred, so it is now
**declared** and **drift-guarded** — rename an operation and the build breaks here instead of replay
silently breaking at run time. This is the prerequisite for ever *generating* batch steps.

- **`_STEP_OPERATIONS`** in `batch_step_registry.py` — the declared step → operation-ids map, placed
  next to `_STEP_MAP` (declared at the code, not a side table). Public accessors `step_operations(name)`
  and `all_step_operations()`. Each mapping was **verified** against the step's replay function (its
  toolbox imports → the op that function declares), never guessed.
- **`tests/navigator/test_batch_step_composition.py`** — the validation: every declared op exists in
  the operation vocabulary (catalog layer ops ∪ curated measure ops), every declared step is a real
  `_STEP_MAP` key, no empty declarations, ids are a deduplicated lower-case set, and a **downward-only
  coverage floor** (≥ 10 steps mapped). Undeclared steps are *reported*, not hidden.
- **Declared the unambiguous tranche (10 steps):** `preprocessing`→`preprocess`, `upscaling`→`upscale`,
  `calibration_correction`→`flatfield`/`bg_subtract_clear`, `auto_crop_roi`→`multi_otsu`,
  `cellpose_segmentation`→`cellpose`/`stardist`, `condensate_segmentation` & `ivf_segmentation`→
  `mask_stretch`/`subcellular_segment`, `bf_condensate_segmentation` & `ivbf_segmentation`→`bf_segment`,
  `ivf_size_distribution`→`invitro.size_distribution` (a measure op).
### Notes
- **Staged population, deliberately** (the increment-2 discipline). Steps that invoke only an *untagged*
  composite (e.g. `background_removal`'s rolling-ball path has no op id) or only file I/O
  (`open_image`, `save_and_clear`) are **left undeclared**, not mapped to a guess — a guessed mapping is
  drift with extra steps. The ratchet floor captures progress; later work raises it. `_STEP_MAP` and the
  catalog remain separate vocabularies by design (composition, not merger).
- No behaviour change. Full `pytest -m core` green.
- Files: `src/pycat/batch_step_registry.py`, `tests/navigator/test_batch_step_composition.py`.

## [1.6.126] - 2026-07-18
### Added — **OperationSpec increment 2: `inputs` on the decorator — the operation vocabulary is now a GRAPH.**
Increment 1 defined `OperationSpec` as a typed, drift-guarded view over the `@tags_layer` registry and
deferred the richer fields with an explicit rule: *a field nothing checks is exactly the drift this
effort exists to prevent.* Increment 2 adds the first of those fields — `inputs`, the layer
role(s)/target(s) an operation **consumes** — **with the validation that makes it real**. Together with
the existing `produces`, `inputs` turns the flat list of 79 operations into a directed graph
(`op_a.produces → op_b.inputs`). Increments 3–5 (batch composition, subsystem generation, runnability
gating) all need that graph. Additive; no behaviour change.

- **`@tags_layer(inputs=…)`** (`utils/tag_registry.py`) — optional, drawn from the **same** `ROLES` /
  `TARGETS` vocabularies (never a third one); an unregistered input value is a hard error at import,
  exactly as an unregistered tag already is. An operation that declares nothing is a *root* (it loads
  or creates a layer from a file). `_register_ui_operations()` gained the same ability.
- **`OperationSpec.inputs: tuple[str, ...] = ()`** (`navigator/operation_spec.py`) — populated from the
  registry in `iter_operation_specs()`, added to `operation_catalog.json` (regenerated), and covered by
  the existing drift guard (`test_catalog_fields_match_the_live_declaration` now also compares `inputs`,
  so a declared-vs-snapshot divergence fails like any other field).
- **`tests/navigator/test_operation_graph.py`** — the validation: no dangling edges (every declared
  role input is produced by some op or is the root role `image`), vocabulary agreement, traversability
  from the roots (an unreachable input-bearing op is *reported*, not failed), a real op→op edge exists,
  and a **downward-only coverage floor** (≥ 23 ops declare `inputs`) so the declarations populate
  incrementally without silently regressing.
- **Annotated the unambiguous image-consuming tranche (23 ops):** the filters that consume an image and
  return one (`bandpass`, `dog`, `log`, `rolling_ball`, `gaussian`, `bilateral`, `gabor`, `invert`,
  `wbns`, `bg_subtract`, `tone_map`, `local_contrast`, `clahe`) and the primary segmenters/detectors
  that consume an image (`cellpose`, `subcellular_segment`, `local_threshold`, `felzenszwalb`,
  `felzenszwalb_binary`, `multi_otsu`, `clean`, `bead_detect`, `bf_segment`, `host_segment`) — all
  `inputs=('image',)`.
### Notes
- **Staged population is deliberate.** The remaining ~56 ops (label-editors, merges, ambiguous-input
  ops) are **not** annotated: declaring an input you had to guess is drift with extra steps. The ratchet
  floor captures progress; later work raises it. `batch_step_registry._STEP_MAP` was left untouched — it
  names workflow steps, not layer operations, and has zero overlap with the catalog (composition is
  increment 3).
- No `parameters` / `batchable` / `requirements` in this increment. Full `pytest -m core` green.
- Files: `utils/tag_registry.py`, `navigator/operation_spec.py`, `navigator/op_catalog.py`,
  `navigator/data/operation_catalog.json`, the 8 annotated toolbox modules,
  `tests/navigator/test_operation_graph.py`, `tests/navigator/test_operation_spec_matches_catalog.py`.

## [1.6.125] - 2026-07-18
### Added — **Cross-route workflow-equivalence matrix: the same workflow must give the same numbers through every route.**
The strongest recommendation from the external architecture audit: stop adding isolated per-route
tests and start asserting that a canonical workflow produces the **same numbers** through headless call,
batch replay, and session reload. PyCAT exposes each operation through several routes, each of which
assembles its parameters independently — and a disagreement is the highest-severity class of bug PyCAT
can have (the same analysis silently yielding different numbers depending on how it was launched). This
is not hypothetical: batch preprocessing once passed a *normalised* image where the interactive path
passed **raw counts**, and the rolling-ball radius is not scale-invariant. This generalises the test
written for that one step into a matrix.

- **`tests/route_equivalence.py`** — the reusable harness. `run_all_routes(workflow)` drives a workflow
  through each route; `assert_routes_agree(...)` compares each route against a reference and **names
  which route diverged and by how much**. A route that cannot be driven headlessly is declared an
  `Unavailable` **documented gap** — and the harness fails if a gap closes or a route vanishes without
  the record being updated, so nothing is skipped silently. Comparators default to **exact** equality.
- **`tests/test_route_equivalence.py`** — three canonical workflows, distinct data shapes:
  1. **Rolling-ball background removal** (the known-divergence scale-semantics path) — runs all three
     routes and they are **bit-identical**. Both headless and batch reduce to the same toolbox call on
     the same *raw counts*; if the batch replay ever reverts to normalising first, this names it.
  2. **Puncta detection + measurement** — headless ≈ session (the per-object table survives Save &
     Clear → reopen unchanged). Batch is a **declared gap**: its replay needs a cellpose cell mask
     upstream (torch), absent in the headless `core` env.
  3. **VPT tracks → MSD → viscosity** — headless ≈ session, plus an **end-to-end check that the chain
     recovers the known Stokes–Einstein viscosity** (η = kT/6πRD; true D=0.05 → η recovered within
     15% on the noisy synthetic chain). Batch is a declared gap: VPT/MSD replay steps are deliberate
     skip-stubs (time-series, not per-image).
- Adding a fourth workflow is one `Workflow(...)` entry.
### Notes
- **Finding (minor, not fixed here — test-only spec):** a session-restored DataFrame comes back with an
  extra `Unnamed: 0` column — `write_session_outputs` writes the DataFrame index into the CSV, which
  reloads as a phantom column. The scientific numbers are untouched (the harness compares the named
  columns), but the schema gains a column across a save/load. Logged for a future writer fix
  (`to_csv(index=False)`); it is not a numeric divergence.
- **Justified tolerance:** the session route is a *decimal* (CSV) serialization, so a float64 can return
  differing in its last bit (~1 ULP, ~2e-16). That is the text format's precision, not a route
  computing a different number, so the DataFrame comparator allows 1-ULP-scale (rtol 1e-12, atol 1e-15)
  — far below any scientific significance. The array routes (rolling-ball) are compared **exactly**.
- No production code changed. If a real divergence is ever found, it is a finding to report with its own
  spec — not something to absorb by loosening a tolerance.

## [1.6.124] - 2026-07-18
### Fixed — **Tag discovery: kill the stale TEST loader (the "registry regressed" false alarm) and carry a declared `produces` onto the layer.**
An external audit reported the operation registry had *regressed* — "only 42 of ~100 operations
register," "`log` is missing," "duplicate registration is not rejected." **Three of those four findings
were artefacts of a defect in the test, not the product.** `tests/test_tag_registry.py` reimplemented
operation discovery with a hardcoded list of **11** module names inside `except Exception: pass`, missed
**7** decorated modules, counted 42 instead of the real **79**, and swallowed the import failures that
would have said why. Meanwhile `operation_spec._populate_registry()` already AST-discovers every
`@tags_layer` module correctly — the test had a second, worse loader.

- **Fix 1 (the real defect).** The tag tests now discover through the one mechanism —
  `_populate_registry()` — and an unimportable decorated module is a **loud failure**, not a quiet
  undercount. The `>= 50` floor is ratcheted to the live count (**79**). Routed both
  `test_tag_registry.py` and `test_tag_resolver.py` through it; deleted their private loaders. The four
  reported failures pass **with no product change** — the proof that findings 1–3 were test artefacts.
- **Fix 2 (the one genuine gap).** `layer_tag_hook` already copies a known op's declared `target` onto
  the layer (so `role=labels, target=cell` finds Cellpose output). It now also honours a declared
  `produces`: an op that **declares** it makes a mask is tagged `role=mask` even when the returned
  array's values happen to look like labels — the declaration is definitional, the data-shape is a
  guess, and declared beats inferred.
### Added
- An **end-to-end** acceptance test (`operation → layer → tags → resolver`) over six canonical ops —
  `cellpose`, `watershed`, `log`, `dog`, `rolling_ball`, `mask_merge` — each found by a query written in
  its *declared* semantics. `mask_merge` is fed labels-like data on purpose, so only the declared
  `produces='mask'` makes the `role=mask` query match.
### Notes
- **The record, stated plainly:** the "42 operations / missing `log` / no `TagCollision`" findings were a
  stale test loader. The registry holds 79 operations, `log` is present (in `image_processing_tools`,
  alias `laplacian_of_gaussian`), and `register_operation` does raise `TagCollision`. No operations were
  added to "fix" the count — that would have created the real collision the audit wrongly reported.
- Files: `src/pycat/utils/layer_tag_hook.py`, `tests/test_tag_registry.py`, `tests/test_tag_resolver.py`.

## [1.6.123] - 2026-07-18
### Changed — **Retrofit the linked-selection dock to the SelectionView contract.**
Continuing the Gap 5 retrofit (the table and the new pyqtgraph backend already conform): the dock's
`LinkedSelectionWidget` is now a `SelectionView` — `view_id`, `apply_selection(state)` (renamed from
`_on_selection`, alias kept), and a `close()` that **unsubscribes** and then Qt-closes. It is a
*receive-only* view (it renders the selected object's crop but never emits a command), so it needs no
programmatic guard.

- **Fixes a small lingering-subscription gap**: the outer wrapper's `close()` only removed the dock
  widget; the deferred subscription lived on (held weakly, so eventually GC'd, but never explicitly
  dropped). The wrapper now closes the widget, which unsubscribes.
### Notes
- Headless-tested: the widget satisfies the `SelectionView` protocol, applying a selection emits no
  command (receive-only), and `close()` unsubscribes. Existing dock tests green.
- Retrofit status: the emitting adapters (**table**, **pyqtgraph**) and the receive-only **dock** now
  conform to and are tested against the shared contract. The MSD-plot brushing works correctly but is
  registry/handler-based (rewritten in 1.6.120); formalizing it as a `SelectionView` class is a larger
  restructure of just-rewritten code — deferred rather than churned.

## [1.6.122] - 2026-07-18
### Added — **PyQtGraph 'explore' plot backend, built on the SelectionView contract.**
A fourth plot backend (alongside matplotlib/seaborn/plotly): a native-Qt interactive scatter. napari
is Qt, so a click is a Qt signal in the same event loop — no WebEngine bridge, low latency at large N.
matplotlib stays the export/publication backend; this is the interactive *explore* one.

- **`scatter(df, x_col, y_col, backend='pyqtgraph')`** returns a `PlotWidget` + `ScatterPlotItem`
  whose points map **1:1 to df rows in order** — it runs the same `_verify_row_order` guard the other
  backends do and REFUSES (`ok=False`) rather than wire a click that could land on the wrong object.
  `hue` colours per group but keeps ONE scatter item in row order (never the seaborn split-into-artists
  trap). `'pyqtgraph'` is in `BACKENDS` and `available_backends()`; the seam degrades with a message,
  not a crash, when the extra is absent.
- **Brushing is a proper `SelectionView`** (`PyQtGraphScatterView`): a click emits one command
  (`source_view`), an inbound selection highlights the matching points on a *separate overlay* item
  (O(1), not a recolour of N) under the `ProgrammaticGuard`, and `register_view` pushes current state
  on open. It passes the **same shared adapter contract** the table and the reference view pass — no
  second selection path. Camera-follow stays opt-in (the VPT-P3 no-loop lesson).
- **Optional dependency** `pip install pycat-napari[pyqtgraph]`; imported lazily, so PyCAT still
  imports and runs headlessly without it (the headless-import contract holds).
### Notes
- Rebuilt on `main` against the interaction-layer contract rather than merging the stale
  `pyqtgraph-backend` branch (which predated `SelectionView` and used the old bare-callback API — the
  exact rewrite the spec's ordering was meant to avoid). That branch is now superseded.
- Headless-tested: availability, backend registration, graceful absence, row-order 1:1, hue single-
  artist; and with Qt: the scatter maps to rows, an inbound selection highlights the overlay, and the
  adapter passes the SelectionView contract. Core: 1018 passed. **A live interactive click is worth a
  viewer once a UI opts a panel into this backend** — nothing uses it by default yet.

## [1.6.121] - 2026-07-18
### Added — **Interaction layer 5: a `SelectionView` adapter contract.**
Linked views used to be bare callbacks with no shared contract, so each re-invented apply / suppress /
cleanup and they drifted. This adds the contract, its mechanism, and a reusable test — the piece the
pyqtgraph plot backend should be built against.

- **`SelectionView` protocol** (`selection_service.py`): `view_id`, `apply_selection(state)`, `close()`.
- **`ProgrammaticGuard`** — the *primary* echo defence: `with guard.applying(): <render>` marks a
  programmatic update, and the view's outbound handler checks `is_applying` and bails, so rendering a
  selection never emits a new command (the service's `source_view` suppression stays as the second
  line). Re-entrant.
- **`register_view(service, view)`** subscribes the view AND pushes the current state, so a view opened
  while something is already selected reflects it — the initial apply is programmatic (emits nothing).
- **A reusable contract** (`tests/selection_view_contract.py`): programmatic apply emits no command, a
  user action emits exactly one, an unknown entity is safe, and `close()` unsubscribes. A reference
  adapter and the **retrofitted `BrushableTable`** (now a `SelectionView`: `apply_selection` + `close`,
  using the shared guard + `register_view`) both pass it. `detach()` stays as a back-compat alias.
### Notes
- Headless-tested: the guard is re-entrant, `register_view` pushes current state without bouncing a
  command, and both the reference adapter and the real Qt `BrushableTable` pass the shared contract.
  All existing selection/brushing tests green.
- Completes the interaction-layer spec's mechanism. Remaining, additive: retrofit the other views (MSD
  plot, VPT handlers, napari overlay, dock) to the protocol, then build/merge the pyqtgraph backend
  adapter against this contract.

## [1.6.120] - 2026-07-18
### Changed — **Interaction layer 4: MSD spaghetti background is one `LineCollection`; selection is an overlay.**
Hundreds of individual `Line2D` are slow to draw and force a per-artist style restore on every
selection. Both MSD renderers — the standalone `plot_msd_trajectories` and the consolidated panel's
`_draw_msd_into` — now draw the representative background as a **single `LineCollection`**, and a
selection (or a track brushed in from the table, Gap 3) is drawn as an **overlay `Line2D`** on top.
The background collection is never touched.

- **Hit-testing is on the coordinate ARRAYS**, not per-line artists (`_connect_nearest_curve_click_coords`
  + `_segment_distance_px_coords`), so collapsing the background costs nothing for interaction — same
  nearest-curve + click-to-cycle behaviour as before.
- **One implementation, shared:** the new `_msd_overlay_hooks` sets up promote/demote + the click
  hit-tester for BOTH renderers, so the standalone plot and the consolidated panel brush identically
  instead of from two divergent copies (the panel's bespoke blit + apply-pick + connect are gone).
  With one background artist a redraw is cheap, so selection uses a plain `draw_idle` — no blit
  bookkeeping. VPT's `_highlight_track_in_plot` was already overlay-aware (1.6.119) and needs no change.
### Notes
- Headless-tested: the background is exactly one `LineCollection` (not N `Line2D`); selecting a track
  adds one overlay and leaves the collection untouched; demote removes only the overlay; a non-sampled
  track promotes from the full frame; the log axes still frame the data (the `LineCollection`/log
  autoscale pitfall is checked); and the coords hit-tester matches the Line2D point-to-segment
  geometry. Consolidated-panel path smoke-checked end-to-end. **The live feel needs a viewer:** confirm
  clicking/​table-selecting a track in both the standalone and consolidated MSD plots still highlights
  correctly and feels responsive.
- Completes the interaction-layer spec's structural gaps (1=state 1.6.118, 3=promotion 1.6.119, 4=this;
  2=honest hit-testing was 1.6.100). Gap 5 (the `SelectionView` adapter contract, which lets the
  pyqtgraph backend be built correctly) remains as a separate pass.

## [1.6.119] - 2026-07-18
### Added — **Interaction layer 3: a track selected from the table shows even if it isn't in the MSD sample.**
The MSD spaghetti plot draws a fidelity-targeted representative subset (~100 of N), so a track picked
in the table that wasn't sampled had no curve to highlight — the bidirectional brushing quietly
couldn't reach it. Now the plot promotes it on demand.

- `plot_msd_trajectories` registers `promote(tid)` — draws a non-sampled track's curve on demand and
  returns its line — and `demote_line(line)` — removes it when it's deselected. A **sample line is
  never removed**; only a promoted focus curve is. The displayed set is effectively
  `representative_sample ∪ selected`.
- **VPT's `_highlight_track_in_plot` uses them**: when a selected track has no line it promotes one;
  when the previously-highlighted track was a promoted curve it demotes (removes) it instead of
  restyling. A promote/demote changes the line set, so it does a full redraw there rather than the
  blit fast-path (which assumes a fixed set); highlighting a sampled track still blits.
### Notes
- Headless-tested against the real `plot_msd_trajectories`: only the sample is drawn up front, a
  non-sampled track promotes (and is marked a focus curve), a sampled track's promote returns its
  existing line, demote removes a promoted curve but NEVER a sample line, and a track with no data
  promotes to nothing. **The live feel needs a viewer:** confirm selecting a table row for a track
  outside the sample now highlights its curve, and deselecting removes the promoted curve.
- Builds on 1.6.118's `SelectionState`. Gap 4 (`LineCollection` background) and Gap 5 (the
  `SelectionView` adapter contract) remain as separate additive passes.

## [1.6.118] - 2026-07-18
### Added — **Interaction layer 1: selection is now a hover / selected / pinned STATE.**
First increment of the interaction-layer spec. Selection was a single object — no multi-select, no
pinning while exploring, no independent hover. `SelectionService` now holds a `SelectionState`
(`selected: frozenset`, `primary`, `hovered`, `pinned: frozenset`, `generation`) and publishes the
whole state per change, with commands that produce a new one:

- `toggle(entity, source)` — ctrl-click to build a comparison set; `select_entity` — single select;
  `hover(entity, source)` — independent of selection; `pin`/`unpin` — survive a clear;
  `clear_selection(source)` — clears selected + hovered but **keeps pins** (Escape's semantics).
- **Back-compat is total.** `SelectionState` quacks like the old `Selection` (`entity_ids`,
  `primary_id`, `source_view`, `is_empty`), so every existing subscriber (the dock, the VPT views, the
  plots) and every existing test keeps working unchanged — the dispatch core (busy-guard, delayed
  release, deferred-debounce) is untouched, just extracted into `_publish` and shared by the old
  `select(Selection)` entry and the new commands.
### Notes
- Headless-tested: toggle add/remove, clear keeps pins, hover doesn't disturb selection, one command =
  one generation = one publish, a command reaches old subscribers via the back-compat interface, and
  the source view is skipped. All 72 existing selection/brushing tests still green.
- This is the keystone the pyqtgraph plot backend should be built against (its adapter must speak this
  state, not the old bare callback). Remaining interaction-layer increments (honest hit-testing —
  largely done in 1.6.100 via click-cycling; non-sampled track promotion; `LineCollection` background;
  the `SelectionView` adapter contract) are separate, additive passes.

## [1.6.117] - 2026-07-18
### Fixed — **CZI exit hang: force the exit from `atexit`, not `aboutToQuit` (which never fired).**
1.6.116's `QApplication.aboutToQuit` hook did not fix the hang — it is installed from the CZI-open
**worker thread**, where a cross-thread Qt `connect` is unreliable, and the hang is at Python
interpreter shutdown *after* `napari.run()` returns. Moved the guarantee to an **`atexit`** handler:
it runs on the main thread right before Python joins the JVM's non-daemon threads (exactly where it
hangs), so `os._exit(0)` there terminates cleanly. `aboutToQuit` is kept as a best-effort earlier
trigger.
- The handler prints `[PyCAT CZI] BioFormats JVM was open — forcing a clean process exit…` when it
  runs, so it is visible whether the fix engaged. Verified at the process level: the standalone reader
  fires the handler and exits `0`.
- Only the welcome-logo temp-file cleanup atexit is pre-empted (harmless), and only in a CZI session.
### Notes
- **Needs a viewer:** open the streaming `.czi`, close PyCAT — you should see that force-exit line and
  get the prompt back. If you close and do NOT see the line, `napari.run()` isn't returning on close
  and I'll hook the viewer's window-close event instead.

## [1.6.116] - 2026-07-18
### Fixed — **Closing PyCAT after a CZI now returns the terminal (force a clean exit).**
Headless mode (1.6.115) was not enough: something in the napari/Qt + BioFormats-JVM combination still
keeps the process alive at teardown — the window closes but the terminal never comes back. It cannot
be reproduced outside the GUI (a plain script exits fine), so rather than keep chasing which Java/Qt
thread refuses to die, PyCAT now forces a clean termination at the app's quit point: **once a CZI has
started the JVM**, `QApplication.aboutToQuit` flushes the streams and calls `os._exit(0)`. This only
arms in a CZI session (the JVM-start path) — every other session exits normally — and it runs after
other quit handlers, so it is the last thing before the process would otherwise hang.
### Notes
- **Needs a viewer:** confirm closing PyCAT after opening the streaming `.czi` returns the prompt.
- Keeps the 1.6.115 headless start (good practice regardless) and the scrubbing findings from
  1.6.113–115 (the stutters are inherent BioFormats seek latency).

## [1.6.115] - 2026-07-18
### Fixed — **Closing PyCAT after opening a CZI no longer hangs the terminal.**
Long-standing: after opening a streaming CZI, closing PyCAT left the process alive — the window shut
but the terminal never returned. Reading a CZI can make BioFormats touch Java AWT (colour models /
thumbnails), which spawns a **non-daemon AWT thread** that keeps the JVM — and the whole Python
process — running at shutdown. (A plain script exits fine because it never triggers AWT the way the Qt
app does, which is why it only bit inside `run-pycat`.) The JVM is now started **headless**
(`scyjava.config.enable_headless_mode()` → `-Djava.awt.headless=true`), so no AWT thread is ever
created; BioFormats reads pixels and metadata without it (verified: the reader still opens/reads the
real 8 GB file and the process exits cleanly).
### Diagnostics
- The `PYCAT_CZI_TRACE=1` readout now breaks latency into **worst lock-wait** and **worst openBytes**.
  On the real file this settled the scrubbing question: worst lock-wait **1–2 ms** (the prefetcher is
  not blocking foreground reads) and worst openBytes **~400 ms** — i.e. the intermittent stutters are
  BioFormats **seeking to distant frames**, an inherent random-access cost of this streaming CZI that
  caching cannot remove. The prefetch (1.6.114) is correct and harmless but only helps when scrubbing
  pauses or revisits cached frames; it cannot get ahead of a continuous drag through new frames.
### Notes
- **Needs a viewer:** confirm that opening the streaming `.czi` and then closing PyCAT returns the
  terminal prompt (no more reopening the terminal).

## [1.6.114] - 2026-07-18
### Changed — **CZI prefetch: foreground-priority + direction-aware (fixes back-and-forth scrubbing).**
An audit of 1.6.113's prefetch found it structurally wrong for anything but forward playback: it
published the current frame to the prefetcher only AFTER reading it, prefetched forward-only, and could
hold the reader's lock on obsolete frames while the UI waited on the one frame the user actually moved
to. Redesigned per that audit:

- **Foreground priority.** A read now publishes its request (target + a monotonic generation) and
  raises `_fg_pending` *before* it reads, so the background thread never starts a read while the UI is
  waiting, and abandons an obsolete read-ahead pass the moment a newer request arrives.
- **Direction-aware read-ahead.** The prefetcher follows the scrub: forward for a forward scrub,
  **backward for a backward scrub** (previously all-misses), a symmetric neighbourhood when direction
  is unknown or the frame is held, and a shallow ±2 on a large jump (no far speculation).
- **Buffer-layout guard.** `_read_plane_raw` now asserts the BioFormats plane byte-count matches
  `H·W·itemsize` and reports series/RGB/interleaved on mismatch — a wrong series or layout can be the
  wrong size in a way that still reshapes to a shifted image; this fails loudly instead.
### Notes
- Correctness investigation (the reported "seam"): BioFormats reports the streaming file as a **single
  series, single resolution, 500×500 uint16, non-RGB, non-interleaved**, and plane 0's buffer is
  **exactly** 500·500·2 bytes — so the reader selects the right series and the byte→array reshape is
  correct. The residual ~1.5% column-12 step and the anomalous row 0 are constant across frames and
  are in the acquisition, not the decode. No pixel-decoding defect.
- Benchmarked on the real 8 GB file at 25 fps: forward **40/40**, backward **40/40**, oscillate
  **36/36** frames served from cache (0 ms). **Needs a viewer:** confirm scrubbing actually feels
  smooth — set `PYCAT_CZI_TRACE=1` before `run-pycat` to print the real per-scrub cache hit-rate and
  read latency. If the trace shows high hit-rate but the viewer still lags, the bottleneck is napari's
  render path, not the reader.
- Deferred (audit #3): caching native uint16 for display and normalising to float32 only for analysis
  — would roughly double the cached temporal span, but it splits the display/analysis representation
  and cuts against PyCAT's uniform `[0,1]` loader contract, so it wants its own pass.

## [1.6.113] - 2026-07-18
### Changed — **Streaming CZI scrubbing is smooth: an LRU cache + background read-ahead.**
The direct BioFormats reader decodes ~5 ms/plane, which showed as intermittent stalls scrubbing the
15,766-frame movie frame by frame. The reader now caches planes and reads AHEAD.

- **Byte-budgeted LRU cache** (256 MB → 268 planes at 500², 16 at 2048²) so repeats and small back-and-
  forth scrubs are instant.
- **Background read-ahead**: a single worker thread decodes the next few frames (`_PREFETCH_AHEAD = 8`)
  ahead of the frame last accessed, and bails the moment the user moves on, so a forward scrub lands on
  already-decoded planes. Measured on the real 8 GB file: a 25 fps forward scrub was served **30/30
  from cache (0 ms)**, versus ~5 ms/plane cold.
- Every read (foreground + prefetch) is serialised on one lock — a loci `ImageReader` is not safe for
  concurrent `openBytes` — held per plane (~5 ms), so a foreground miss never waits long.
- **The prefetch thread detaches from the JVM whenever it goes idle.** A JNI thread that attached (via
  `openBytes`) and never detached blocks `DestroyJavaVM`, hanging the whole process at exit — found and
  fixed here; the process now exits cleanly.
### Notes
- Headless-tested: cache hit on repeat, read-ahead caches the frames ahead, the cache is byte-budgeted,
  and close stops the prefetcher. The reader was also run end-to-end on the real 8 GB file (opens,
  reads, prefetches frame 101 after frame 100, exits cleanly). **Needs a viewer:** confirm scrubbing
  the streaming `.czi` is now smooth with no intermittent stalls.
- The `@integration` real-file test hit an intermittent jpype `startJVM` access violation in this
  session's harness (unrelated to this change — the prefetch thread starts *after* JVM init, and it is
  deselected from the core suite); the reader is verified via the standalone benchmark above.

## [1.6.112] - 2026-07-18
### Fixed — **The CZI open no longer cancels itself.**
A regression in 1.6.111 (unreleased): opening the streaming CZI reported "CZI open cancelled" and
aborted on its own, with no user interaction. `QProgressDialog.close()` **emits `canceled`**, so when
the dialog closed on *normal completion*, the cancel handler fired and marked the load cancelled.

- The finish handler now marks completion (`done`), and the cancel handler ignores the
  `canceled` that `close()` emits once the work is done — only a real "Give up" click (or Escape/X)
  *before* completion cancels. Regression-tested (`test_busy_progress.py`, real Qt loop): a successful
  call returns its value instead of raising the cancellation.
### Notes
- Rolls up with 1.6.110 (dedupe + off-thread libCZI probe) and 1.6.111 (dialog auto-closes + "Give
  up"). **Needs a viewer:** confirm the streaming `.czi` now opens to completion on its own, and "Give
  up" still cancels cleanly.

## [1.6.111] - 2026-07-18
### Fixed — **The CZI "indexing" dialog now closes itself, and "Give up" actually works.**
From the viewer, on the streaming-CZI open dialog: it stayed open with the elapsed counter frozen, and
only advanced when the user X'd it out; there was no cancel button; and X-ing out early hung the UI.
All three are the same worker-dialog helper (`_run_with_busy_progress`), which had the exact bug the
newer `qt_worker` was built to avoid.

- **It closes when the work finishes.** `worker.finished` is emitted from the worker thread, and the
  old finish handler was a plain function — so Qt ran it *on the worker*, and `dlg.reset()` from there
  never ended the main thread's modal loop. The dialog hung open (frozen elapsed = work already done)
  until the user dismissed it. The handler is now a `QObject` slot that runs on the main thread (queued
  delivery), ending a `QEventLoop` with `loop.quit()`.
- **A "Give up" button that frees the UI.** The BioFormats index parse is a single uninterruptible JVM
  call, so cancel **detaches**: it stops waiting and lets the orphaned worker finish in the background
  (result dropped), instead of `thread.wait()` blocking the UI until the parse completes — which was
  the hang when X-ing out. The detached thread is retained until it finishes so it can't crash by being
  garbage-collected mid-run. Both CZI open sites report "CZI open cancelled." and abort cleanly.
### Notes
- Same fix benefits both CZI busy dialogs (the libCZI index probe and the BioFormats reader open).
  **Needs a viewer:** confirm the indexing dialog now closes on its own and the layer appears without
  X-ing out, and that "Give up" dismisses it and frees the window immediately.
- Still open, deliberately (secondary): the occasional scrubbing latency on the streaming movie —
  that's the prefetch/cache task (read T±k around the current frame), separate from this dialog fix.

## [1.6.110] - 2026-07-18
### Changed — **Opening a big streaming CZI no longer freezes the UI on the libCZI probe.**
The streaming-CZI reader (BioFormats, shipped 1.6.61) already opened its Java reader off-thread — but
the libCZI **metadata** open that routes to it ran on the Qt main thread, and for a 15,766-frame movie
parsing every subblock offset is ~11 s. Worse, it ran **twice**: once to decide the file needs
BioFormats, then again inside the streaming loader for pixel size / channel names. So ~20 s of "Not
Responding" preceded the (already responsive) BioFormats indexing dialog.

- **The two libCZI opens are deduplicated.** The routing probe (`probe_libczi`) now returns the libCZI
  image alongside its can-read verdict, and the streaming loader reuses it instead of re-opening —
  the multi-second subblock parse is paid once.
- **For a large CZI the probe runs off the Qt thread** behind the existing busy dialog, so even the
  first parse stays responsive. A small confocal/widefield CZI (a few MB, parses in milliseconds) still
  probes inline — a worker dialog would only flash. The gate is file size (`_CZI_OFFTHREAD_BYTES`,
  256 MB), which sits far below any streaming movie and far above any normal CZI.
### Notes
- No change to the reader itself or to normal-CZI behaviour (still libCZI, fast, no JVM). Verified: the
  streaming reader still opens and reads the real 8 GB file (integration test), and `probe_libczi`
  returns the image even when the pixel read fails (headless tests). **Needs a viewer:** confirm
  opening the streaming `.czi` shows the responsive indexing dialog from the start, with no initial
  freeze.
- Housekeeping: the CZI reader was fully built and shipped in 1.6.61 but never got a CHANGELOG entry;
  this documents the format is supported (confocal/widefield via libCZI; Zeiss fast-streaming via the
  opt-in `[bioformats]` extra).

## [1.6.109] - 2026-07-18
### Fixed — **QC on a long movie no longer OOMs; it assesses a bounded sample.**
With the IMS decode fixed (1.6.108), QC got further and then hit a *second* out-of-memory: `run_full_qc`
upcast the whole stack to **float64** (18.8 GiB for a 600×2048² movie), and even at float32 the
per-metric transients (`qc_snr`'s `np.diff` over every frame) are multi-GiB. Both were pre-existing and
independent of the off-thread work. Three parts:

- **QC now assesses an evenly-spaced sample of a long time series**, capped at `QC_MAX_FRAMES` (64).
  The UI reads **only those frames** off disk (`materialize_stack(max_frames=…)` indexes them via
  `__getitem__`), so a 600-frame movie costs ~1 GiB instead of ~18 GiB. QC is a health check, so an
  evenly-spaced sample across the acquisition answers it — and the report now carries a **"Frames
  assessed: N of M"** row that says so, and flags that the sampling lowers the rate the vibration check
  sees (drift, bleaching and focus are sampled across the whole run and unaffected).
- **`_to_float` casts to float32, not float64** — ample precision for every QC metric, half the memory,
  and a no-op (no copy) for a stack already decoded as float32.
- **The 3-D check reads the SHAPE, not a `.ndim` attribute.** The IMS readers advertise a `(T, Y, X)`
  shape but no `ndim`, so `getattr(_layer_data, 'ndim', 2)` read them as 2-D and fell to
  `np.asarray(wrapper)` — the lazy-guard refusal, i.e. the original crash. QC now derives 3-D from the
  shape and takes the decode path.
### Notes
- Headless-tested: `materialize_stack(max_frames=…)` returns evenly-spaced frames and reads ONLY those
  (endpoints included); `_to_float` is float32 and copy-free; the QC report adds the sampling note only
  when it actually subsampled. **Needs a viewer:** confirm QC now completes on the 600-frame .ims with
  the report showing "64 of 600 frames".
- **Judgment call worth your eye:** the sample is *strided* (spans the whole acquisition), which keeps
  drift/bleaching/focus honest but lowers the vibration check's frequency range. If you'd rather QC use
  a contiguous native-rate window (vibration correct, drift only over the window) or raise the 64-frame
  cap, say so — both are one-line changes.

## [1.6.108] - 2026-07-18
### Fixed — **`materialize_stack` could not read the IMS readers (QC crashed on an .ims stack).**
Running QC (or any full-stack analysis) on a lazy `.ims` movie raised
`RuntimeError: An implicit full-stack read was attempted on _ImsReaderTYX`. **A pre-existing bug the
1.6.107 off-thread change surfaced** by re-raising it cleanly instead of swallowing it: the old QC
code called the same `materialize_stack`.

- `materialize_stack` is the *sanctioned* full-read path, but for a lazy wrapper without
  `as_full_array` it fell through to `np.asarray(stack_like)` — and the IMS readers' `__array__` now
  **refuses** an implicit full read (`lazy_guard.refuse_implicit_full_read`) rather than truncating to
  one frame, so the blessed reader raised the very error it exists to prevent. It now reads any 3-D
  indexable wrapper **frame by frame via `__getitem__`** (guard-safe, the same access the guard's own
  message points to), keyed on shape before it ever touches `np.asarray`. Plain numpy / dask /
  `as_full_array` wrappers are unchanged. Regression-tested with a wrapper that refuses `__array__`.
### Changed — **Data Quality Control moved to the top level of Analysis Methods.**
It was tucked inside **Toolbox → Data Visualization**, which is both hard to find and conceptually
wrong — QC is the first thing you do to a dataset, not a plot. It is now a top-level item in the
**Analysis Methods** menu, next to Exploratory Analysis. (Per-frame **Frame Quality / Focus QC** stays
under Data Visualization; that is the different, per-frame scorer.)
### Notes
- Headless-tested: `materialize_stack` reads a guard-refusing 3-D wrapper frame-by-frame, preserves
  label dtype, and drives the progress callback. **Needs a viewer:** confirm QC now runs on the .ims
  stack (with the modal decode dialog), and that Data Quality Control appears at the top of Analysis
  Methods.

## [1.6.107] - 2026-07-18
### Changed — **Every widget's stack decode runs off the Qt thread now.**
The other half of 1.6.106. Fourteen sites across eight widgets decoded a lazy stack with
`materialize_stack` on the Qt main thread — the 1.6.81/82 progress bars made that wait visible (a
synchronous `repaint()` advances the bar) without making it shorter, so the window could still say
"Not Responding" while the bar moved. All fourteen now decode through a worker.

- **New `qt_worker.materialize_off_thread(layer.data, viewer=…, **kw)`** wraps `materialize_stack` in
  `run_with_progress`: the decode runs on a `QThread` behind a modal dialog, and the array comes back
  on the caller's thread — safe to hand straight to analysis, exactly as before. `dtype=` and any other
  kwargs pass through unchanged.
- **Converted:** FRAP (recovery + pre-bleach), condensate-physics (fusion + QC), data-QC,
  brightfield (dynamics + focus-QC), in-vitro fluorescence (dynamics + intensity + QC), in-vitro
  brightfield (dynamics + focus-QC), fusion (image mode), and the temperature module's shared cached
  `_get_stack` (which froze once, on whichever section was clicked first). The inline `PhasedProgress`
  bars for the decode phase are retired in favour of the modal dialog.
- **Not converted:** FRAP's 2-D per-candidate scan (`_offer_stack_2d_images`) — it decodes single 2-D
  frames in a loop, where an off-thread dialog would flash once per candidate. It stays synchronous and
  is the one excused entry.
### Notes
- The progress-rollout ratchet (`test_progress_rollout.py`) is rewritten for the new contract: a
  `*_ui.py` that decodes a stack **directly** (synchronously, on the Qt thread — bar or no bar) now
  fails; the way to pass is to route it through `materialize_off_thread`. The countdown is at zero.
- Headless-tested: the helper decodes via `materialize_stack` on the worker, passes kwargs and a
  callable progress callback, and survives a viewer with no Qt window; plus the five real-thread
  integration tests (work off-main, value back on-main, progress crosses to main, errors re-raise,
  threads cleaned up). **The per-widget feel needs a viewer** — confirm a dynamics/QC/FRAP run on a
  long stack shows the modal dialog and no longer says "Not Responding".

## [1.6.106] - 2026-07-18
### Changed — **Session load runs off the Qt thread — no more "Not Responding".**
Loading a session lagged the UI (you reported it; Windows shows "Python is not responding" on a longer
one) because `load_session` did its slow work — `tifffile.imread` per derived layer, `pd.read_csv` per
table — on the Qt main thread. The 1.6.81/82 progress bars made that wait *visible* without making it
*shorter*. This is the other half: the read moves to a worker thread while a modal dialog keeps the
window painting.

- **`load_session` is split into a read half and an apply half.** `_read_session_payload` does the
  decode and the CSV reads and touches **no viewer** (structurally — it has no viewer parameter), so it
  is safe to run on a `QThread`. `_apply_session_payload` creates the napari layers and writes the data
  repository, always on the caller's thread — because `viewer.add_*` off the main thread is a crash,
  not a freeze. `load_session` orchestrates the two via `pycat.utils.qt_worker.run_with_progress`.
- **The UI wiring** (`_open_session_loader`, and the quick "restore latest" path) passes
  `use_worker=True`. The worker owns a modal `QProgressDialog`; the old in-dialog progress bar is
  retired so there aren't two bars for one operation. Headless callers and tests default to
  `use_worker=False` (synchronous) and are unaffected — `run_with_progress` also falls back to
  synchronous when there is no running Qt app.
- **`qt_worker.run_with_progress`** (new, `pycat/utils/qt_worker.py`) runs a function on a `QThread` and
  returns its value **on the caller's thread**, re-raising exceptions there so existing `try/except`
  still works. It deliberately refuses a callback/future API so nobody is tempted to create a layer in
  the worker. Two subtle bugs in the pattern it replaces are fixed in it: a fast worker finishing before
  `exec_()` is entered (deadlock), and a signal-to-plain-function running the slot on the worker thread
  (off-main widget touch). Both were headlessly tested.
### Notes
- Headless-tested: the read half takes no viewer and decodes into a payload; the apply half is the only
  half that calls `viewer.add_*`; the synchronous round trip is unchanged; the worker helper's deadlock
  and thread-affinity fixes. **The off-thread feel needs a viewer** — confirm a real multi-file session
  restore no longer says "Not Responding" and the modal progress dialog advances while the window stays
  responsive.
- This also stages `load_session` (was 149 lines, over the complexity ceiling) into per-phase helpers —
  the prerequisite the roadmap called out either way. The same `qt_worker` helper now exists to move the
  per-widget `materialize_stack` freezes off-thread next (the other half of the same fix).

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

