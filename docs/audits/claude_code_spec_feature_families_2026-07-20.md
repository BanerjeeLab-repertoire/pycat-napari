# Claude Code spec — Feature families: the organizing schema over the measurement layer

> **✅ STATUS — DONE, shipped in 1.6.194.** All three parts landed, purely additive (no emitted table or
> existing ontology consumer changed). **Part A:** `measurement_ontology.py` gains a `FeatureFamily`
> str-enum (Geometry, Intensity, Partition, Material-state, Spatial, Colocalization, Topology, QC — the
> enum's definition order IS the canonical display order) and a `MeasurementDef.family` field defaulting to
> `None`. **Part B:** the 22 populated ontology entries are assigned their family (only where confident;
> ambiguous ones would stay `None`). **Part C:** new `utils/feature_families.py` — `classify_column`
> resolves ontology-FIRST then a curated substring fallback, returning a `FamilyAssignment(family, source)`
> where `source` is `'ontology'` / `'inferred'` / `None` so a guess is never read as a definition;
> `family_for_column` is the family-only accessor; `group_columns_by_family` partitions columns in canonical
> order with the Ungrouped (`None`) bucket last and **drops nothing** (union of buckets == input).
> `tests/test_feature_families.py` pins the additive default, ontology-vs-inferred source marking,
> ambiguous→Ungrouped, canonical order, nothing-dropped, and str-enum JSON serialization; full `pytest -m
> core` green. This is a *view* over existing columns — the ontology module and emitted tables are
> unchanged. Consumers (Feature Explorer grouped panel, within-family redundancy scope) can now attach.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. A small schema
addition that gives the whole measurement platform its organizing structure: grouping measurements into
families (Geometry, Intensity, Material-state, Spatial, …). The Feature Explorer, redundancy analysis,
and any future profiling all want this grouping; today it does not exist. Additive, low-risk, and it
composes with the ontology population work.

## The gap (verified)
Verified: the ontology's `MeasurementDef` has **no** `family`/`category` field, and no
`FEATURE_FAMILIES` grouping exists anywhere. PyCAT emits features as a **flat** list of DataFrame
columns — `area`, `intensity_mean`, `partition_coefficient`, `ripley_l_max`, `viscosity` all sit in one
undifferentiated row with no indication that the first two are cheap geometry/intensity and the last is
a material-state measurement requiring calibration and a fit.

Consequences:
- The Feature Explorer's column list is a flat scroll of 40 names instead of grouped sections.
- Redundancy analysis has no natural within-family scope (size features are redundant *with each
  other*; grouping makes that legible).
- There is no way to say "give me the material-state features" or "export only geometry" — a natural
  request that the flat model cannot answer.

## Design — one field, one registry, one helper
### Part A — add `family` to the ontology entry
```python
class FeatureFamily(str, Enum):
    GEOMETRY      = 'geometry'        # area, diameter, eccentricity, solidity
    INTENSITY     = 'intensity'       # mean/total/max intensity, contrast
    PARTITION     = 'partition'       # K_p, enrichment, ΔG, concentration
    MATERIAL      = 'material_state'  # viscosity, diffusion, α, mobile fraction, t_half
    SPATIAL       = 'spatial'         # NN distance, Ripley L, PCF, density
    COLOCALIZATION= 'colocalization'  # Pearson, Manders, overlap
    TOPOLOGY      = 'topology'        # persistence, connectedness, scale-space
    QC            = 'qc'              # reliability, stability, biological flags
```
`MeasurementDef` gains `family: FeatureFamily | None`. **Additive** — existing entries default to
`None` until assigned, and nothing that reads the ontology breaks.

### Part B — a family for columns NOT in the ontology
Most emitted columns are not yet ontology entries. Provide a **name-based classifier** as a fallback:
```python
def family_for_column(name) -> FeatureFamily | None
```
that maps by ontology first (authoritative), then by a curated substring map (`*area*`→geometry,
`*intensity*`→intensity, `pearson|manders`→coloc, …), else `None`. **The ontology is truth; the
substring map is a labelled guess** — mark which source a family assignment came from so a guessed
grouping is never mistaken for a defined one (the same derived-vs-inferred honesty the tag hook uses).

### Part C — group a table
```python
def group_columns_by_family(columns) -> dict[FeatureFamily | None, list[str]]
```
Returns families in a stable canonical order, with an `None`/"Ungrouped" bucket for the unclassifiable.
This is what the Feature Explorer's left panel and any "export family X" action consume.

## Scope discipline
- **Assign families only where confident.** A measurement whose family is genuinely ambiguous stays
  `None`; an "Ungrouped" bucket is honest, a wrong family is misleading.
- Do not reorganize the ontology module or the emitted tables — this is a *view* over existing columns,
  not a restructuring of outputs.
- The families themselves are a small, stable enum — resist proliferation; a dozen families is a
  browsing aid, thirty is a second taxonomy to maintain.

## Tests (`core`)
- Every populated ontology entry with a `family` returns it via `family_for_column`.
- The substring fallback classifies obvious cases (`convex_area`→geometry) and returns `None` for
  genuinely ambiguous names — with the source marked ontology vs inferred.
- `group_columns_by_family` partitions a real column list, preserves canonical family order, and puts
  unclassifiable columns in the Ungrouped bucket.
- Adding `family` does not break any existing ontology consumer (the additive-default test).
- No column is silently dropped by grouping — the union of all buckets equals the input columns.

## Steps
1. `FeatureFamily` enum + `family` field on `MeasurementDef` (additive default `None`).
2. Assign families to the populated ontology entries (composes with the ontology-population spec).
3. `family_for_column` (ontology-first, substring fallback, source marked) + `group_columns_by_family`.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Measurements carry a family (ontology-defined where known, name-inferred as a marked fallback, else
  Ungrouped).
- A table's columns can be grouped into families in canonical order with nothing dropped.
- The addition is purely additive — no existing consumer or emitted table changes.
- Full `pytest -m core` green.

## Cautions
- **Additive only** — `family` defaults to `None`; do not require it or break existing entries.
- **Ontology is truth, substring is a labelled guess** — mark the source so a guessed family is never
  read as defined.
- Ungrouped is honest; do not force a family onto an ambiguous measurement.
- Keep the family enum small and stable — it is a browsing aid, not a second measurement taxonomy.
- This groups; it does not reorganize outputs or the ontology module.
