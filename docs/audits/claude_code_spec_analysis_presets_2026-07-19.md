# Claude Code spec — Analysis presets: unify the scattered preset idea

> **◐ STATUS — headless CORE DONE, shipped in 1.6.162 (verified 2026-07-22). Part A + the Qt-free half of
> Parts B/C are complete and guarded; the UI picker + its live batch-record wiring are the only residual,
> and both are Qt-bound.**
>
> **Built and green (`utils/analysis_presets.py` + `tests/test_analysis_presets.py`, 8 `core` tests):**
> - **Part A** — the frozen `AnalysisPreset` (key, applies_to, parameters, mandatory `provenance`,
>   `validated` + `validation_ref`, `requirements`, `caveats`) and the sparsely/honestly seeded
>   `ANALYSIS_PRESETS` registry (3 grounded presets: a validated condensate SNR gate, the VPT 200-frame
>   bead case, an explicitly-unvalidated 63×/1.4 confocal starting point).
> - **The two honesty invariants, enforced at import** (`_validate_registry`): non-empty `provenance`, and
>   `validated=True` ⇒ a linked `validation_ref` that must point at a real `VALIDATED_CASES` id — so
>   `validated` can never become decorative.
> - **The drift guard** (`orphan_parameter_keys`): a preset may only set parameters the workflow's LIVE
>   function signature actually has, read from `inspect.signature` at import so it cannot silently rot.
> - **Requirements gating** (`preset_availability`) REUSES `operation_spec.runnability` — the single
>   requirements vocabulary, never a second gate.
> - **Populate-never-lock** (`PresetApplication`): seeds the values, tracks per-key deviation, reports
>   `"modified from <preset>"` once edited, and `record()` emits the `{preset_key, modified_parameters,
>   is_modified, parameters}` dict shaped for `batch_processor.record`.
>
> **Residual (Qt-bound, deferred with the other UI specs):** Part B's preset-picker widget in the grounded
> workflows (the populate-not-lock control + showing description/provenance/caveats + the greyed-out reason
> from `preset_availability`), and Part 3's LIVE recording — calling `PresetApplication.record()` into the
> actual workflow via `batch_processor.record` — which only fires once the UI produces a `PresetApplication`.
> `record()`'s output shape is already pinned by test; only the wiring to a live session remains.

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Turns
"reasonable starting parameters" from tribal knowledge into a declared, versioned, inspectable object.
Modest to build; disproportionately useful for new users and for the reproducibility story.

## The gap (verified)
Scattered per-widget preset rows exist (the time-series condensate UI has one), and many functions
carry sensible defaults. But there is **no workflow-level preset system** — verified: no
`ANALYSIS_PRESETS` anywhere.

The consequence is a real onboarding problem you have already hit with testers: a new user opening a
condensate workflow faces a dozen parameters with no indication of which values are reasonable for
*their* kind of data. The knowledge exists — it is distributed across defaults, docstrings, and your
head — but it is not offered at the moment of use.

It also intersects the filter-sensitivity work: the programme has been auditing which defaults are
dangerous. A preset system is where the *validated* answers should live.

## Design — a preset is a declared, inspectable bundle
```python
@dataclass(frozen=True)
class AnalysisPreset:
    key: str                       # 'invitro_condensate_confocal_63x'
    display_name: str
    applies_to: str                # workflow / pipeline id
    description: str               # what data this suits, in one sentence
    parameters: dict[str, Any]     # the actual values
    provenance: str                # WHERE these values came from — see below
    validated: bool                # has this been through the sensitivity harness?
    caveats: tuple[str, ...]
```

### The field that matters: `provenance`
Every preset must say **where its numbers came from**:
- *"defaults from the 63x/1.4 confocal condensate work, validated against the filter-sensitivity
  harness"*, or
- *"starting point only — not validated; adjust for your data"*.

A preset with unstated provenance is just a hidden default with a friendly name — and hidden defaults
are precisely what the sensitivity programme exists to expose. **A preset must never be a way to
smuggle an unaudited parameter set past a user.**

### Seed honestly, and sparsely
Seed only presets whose values are genuinely grounded — the instrument/sample combinations actually
used (the 63x confocal condensate case, the brightfield in-vitro case, the VPT bead case with its
validated `MIN_TRACK_LENGTH_FRAMES = 200`). **Do not invent presets for hardware you have not run.**
An invented preset is worse than no preset: it carries false authority.

Mark anything unvalidated as `validated=False` and say so in the UI.

## Part B — the UI contract
- A preset **populates** controls; it never locks them. The user sees the values and can change any of
  them. Silent application would be a black box.
- Selecting a preset shows its `description`, `provenance`, and `caveats` — the reasoning travels with
  the numbers.
- **Deviation is visible:** once a user changes a parameter, the UI shows "modified from
  `<preset>`" rather than continuing to claim the preset. This matters for reproducibility — a result
  produced from a modified preset is not the preset's result.
- The applied preset key + modification state are recorded in the workflow (via
  `batch_processor.record`) so a session states which preset it started from.

## Part C — composition with what exists
- **Filter sensitivity:** a preset's parameters can be run through the harness; `validated=True` should
  mean *"this exact parameter set passed the invariance checks"*, not *"someone liked it"*. Wire the
  registry so a preset can point at its validation record.
- **Measurement ontology / caveats:** preset caveats surface alongside measurement caveats.
- **OperationSpec `requirements`:** a preset that requires a z-stack or a calibrated pixel size should
  be greyed out with a reason when those aren't present — reuse `runnability()` rather than writing a
  second gate.

## Tests (`core`)
- Presets load, are keyed uniquely, and every one declares non-empty `provenance`.
- A preset's `parameters` keys all correspond to real parameters of the workflow it claims to apply to
  (no orphan keys — the drift guard).
- `validated=True` requires a linked validation record; asserting this prevents the flag becoming
  decorative.
- Applying a preset then changing a value marks the state as modified.
- The applied preset and modification state appear in the recorded workflow.
- A preset whose `requirements` are unmet is reported unavailable with a reason.

## Steps
1. `utils/analysis_presets.py` — the dataclass + a small seeded registry (JSON or module-level).
2. Preset picker in the workflows that have grounded presets; populate-not-lock behaviour.
3. Modification tracking + recording into the workflow.
4. Drift guard test (preset keys ↔ real parameters) and the `validated` linkage.
5. Requirements gating via `runnability()`.
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Presets exist as declared objects with mandatory provenance and explicit validation status.
- Applying one populates but does not lock; deviation is visible and recorded.
- Preset keys are guarded against drift from the real parameter names.
- Unmet requirements grey a preset out with a stated reason.
- Only grounded presets are seeded; unvalidated ones say so.
- Full `pytest -m core` green.

## Cautions
- **A preset must never smuggle an unaudited default past the user.** Mandatory `provenance` and an
  honest `validated` flag are what prevent that; do not make either optional.
- **Do not invent presets for instruments you have not used.** False authority is worse than absence.
- Populate, never lock — and show the deviation once the user edits.
- `validated=True` must mean the parameter set passed the sensitivity harness, not that it looked
  reasonable.
- Reuse `runnability()` for gating; do not write a second requirements check.
