# Claude Code spec — Per-feature provenance: attach the workflow chain to each measurement

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Elevates the
existing workflow-level recording to the **feature level**, so any reported number can be traced to
the exact chain that produced it. The roadmap frames it as *"elevate existing batch recording to
per-feature."*

## The gap (verified)
`batch_processor.record(step_name, params)` (`:630`) builds `self.config['steps']` — a complete,
ordered record of the workflow, viewable in "Recorded Steps" and replayable. That machinery is solid.

What it cannot answer: **"which steps produced *this* number?"** The chain is attached to the *session*,
not to the *measurement*. A results table with 40 columns and a 12-step workflow gives no way to know
that `partition_coefficient` depended on steps 3, 5 and 9 but not on the fibril segmentation in step 7.

Verified: no `feature_provenance` or per-feature chain exists.

This matters most where it is easiest to get wrong: a table exported today, opened in six months,
carries its values but not the route to them. Reproducibility is the manuscript's central claim, and
this is the last obvious hole in it.

## Design — build on what exists, don't parallel it
### Part A — the provenance record
```python
@dataclass(frozen=True)
class FeatureProvenance:
    feature: str                     # column name, e.g. 'partition_coefficient'
    operation_id: str | None         # the OperationSpec that computed it
    input_layers: tuple[str, ...]    # pycat_layer_id(s) consumed
    step_indices: tuple[int, ...]    # indices into the recorded workflow
    parameters: dict                 # the params that actually affected it
    software: dict                   # pycat version + key dependency versions
    acquisition: dict                # pixel size, frame interval, exposure (from metadata_extract)
```

### Part B — derive it, do not ask for it
The inputs already exist and must be **composed, not re-entered**:
- `operation_id` — from the explicit `operation_context` (1.6.155) that already tags layers;
- `input_layers` — from the layer tags / `EntityRef` identity already attached;
- `step_indices` + `parameters` — from `batch_processor.config['steps']`;
- `software` / `acquisition` — from the environment and `metadata_extract`.

**No new user input, and no second recording mechanism.** If a field cannot be derived, leave it
`None` — an absent provenance field is honest; a guessed one would be worse than none (the same
principle the layer-tag hook already applies to `derived` vs `inferred`).

### Part C — the ancestry question
Determining *which* steps affected a given feature requires walking the layer lineage backward: the
feature came from an operation, which consumed layers, which were produced by earlier operations. The
lineage relations already exist in the tag system. Walk that graph rather than assuming "all steps
affected everything" — the latter is technically true but useless, since it cannot distinguish the
segmentation that mattered from the unrelated branch.

**Where the lineage is incomplete, say so** (`step_indices=None` with a reason) rather than falling
back to "all steps."

### Part D — where it surfaces
1. **Exported tables** get a companion `<name>_provenance.json` keyed by column — not 40 extra columns
   in the CSV, which would be unreadable.
2. **The consolidated long table** already carries provenance columns; extend them with `operation_id`
   so a row is traceable without the sidecar.
3. **A "where did this number come from?" affordance**: given a column, show its chain. This is the
   feature that makes the rest worth building — provenance nobody can query is just storage.

## Tests (`core`)
- A feature computed through a recorded workflow carries the operation, inputs, and step indices that
  actually produced it.
- **The discrimination test:** in a workflow with two independent branches, a feature from branch A
  does **not** list branch B's steps. This is the whole point — a provenance record that says
  "everything" has no information content.
- Software and acquisition metadata are captured automatically.
- An underivable field is `None` with a reason, never fabricated.
- The sidecar JSON round-trips and is keyed by column name.
- Provenance capture does not change any computed value.

## Steps
1. `utils/feature_provenance.py` — the dataclass + a composer that derives fields from the existing
   sources.
2. Lineage walk for `step_indices`, with an honest failure mode.
3. Sidecar JSON on table export; `operation_id` into the consolidated table.
4. The "where did this come from?" query affordance.
5. Tests above.
6. Full `pytest -m core` green.
7. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Every exported measurement can name the operation, inputs, steps, parameters, software, and
  acquisition behind it.
- Independent workflow branches are discriminated — a feature does not claim unrelated steps.
- Underivable fields are absent with a reason, never guessed.
- A user can ask "where did this number come from?" and get the chain.
- No computed value changes.
- Full `pytest -m core` green.

## Cautions
- **Compose from existing sources; do not build a second recording mechanism.** A parallel provenance
  recorder would drift from `batch_processor`, which is the registry-duplication tax this codebase has
  worked hard to remove.
- **"All steps" is not provenance.** If the lineage cannot discriminate, report that it could not —
  a record that lists everything is indistinguishable from no record.
- Sidecar file, not 40 extra CSV columns.
- Absent beats guessed, consistently with the layer-tag hook's `derived`/`inferred` distinction.
- Capturing provenance must never alter a computed result.
