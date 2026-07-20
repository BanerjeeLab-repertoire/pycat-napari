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

