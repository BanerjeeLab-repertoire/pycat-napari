# Claude Code spec — SMLM / localization-table analysis (the loader is the gap)

> **✅ STATUS — core DONE, shipped in 1.6.184.** `toolbox/smlm_tools.py` — `LocalizationSet` +
> `load_localization_table` (ThunderSTORM + generic CSV, header-sniffed units, hard gate on ambiguous
> units), `temporal_merge` (blink collapse) and `analyze_localizations` (connects to the existing
> `spatial_metrology_tools` Ripley/PCF/NN/density, reports the median-precision resolution floor, warns on
> un-merged over-count). Ontology entries `median_localization_precision_nm` / `ripley_l_max` / `nn_median`.
> `tests/test_smlm_tools.py` (nm→µm, ambiguous-units gate, nm/µm identical stats, clustered-vs-random,
> temporal-merge + warning, precision floor). Follow-on (thin UI): a points layer + a load-and-run widget —
> the loader and the analysis they wrap are delivered.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The lab has three
super-resolution-capable instruments (Dragonfly TIRF single-molecule, campus STED, incoming Airyscan
2) and **zero** localization-table analysis. The good news the tree reveals: the hard part — the
spatial statistics — **already exists**. This is mostly an import-and-connect job.

## What already exists (verified)
`spatial_metrology_tools.py` provides the cluster-analysis backend SMLM needs:
`nearest_neighbour_distance` (`:109`), `local_object_density` (`:209`), `ripleys_l` (`:379`),
`pair_correlation_function` (`:470`). These are exactly the functions PALM/STORM/PAINT analysis runs on
a localization set. Verified: no localization-table **loader** exists (`grep` for
thunderstorm/palm/storm/read_smlm finds nothing). So the gap is the front door, not the analysis.

## Why this is worth building now
- The spatial-stats backend is done and validated — the expensive half is paid for.
- Localization clustering is directly on-thesis: it answers "how are condensate components spatially
  organized below the diffraction limit?" — a distinctive axis for the manuscript.
- It is import-and-analyze, consistent with PyCAT's "own the downstream quantification" positioning.

## Design — load, validate, analyze
### Part A — the loader
```python
def load_localization_table(path, *, format='auto', pixel_size_um=None) -> LocalizationSet
```
Support the common exports: **ThunderSTORM CSV** (the de facto standard), a generic CSV with
x/y[/z]/frame/uncertainty columns, and leave a hook for others. `format='auto'` sniffs the header.

```python
@dataclass
class LocalizationSet:
    x_um: np.ndarray            # ALWAYS in µm — see the units trap
    y_um: np.ndarray
    z_um: np.ndarray | None
    frame: np.ndarray | None
    uncertainty_nm: np.ndarray | None
    n: int
    source_units: str           # what the file used, recorded for provenance
```

### The traps that matter (these are the science)
1. **Units.** ThunderSTORM exports x/y in **nanometres**; other tools use pixels or µm. Guessing wrong
   scales every downstream distance and destroys the cluster analysis. **Detect from the column header
   where possible; require an explicit `pixel_size_um`/unit declaration when ambiguous; never silently
   assume.** This is the same pixel-size-gate discipline PyCAT already enforces for images — a wrong
   scale here corrupts Ripley's L and the PCF exactly as it corrupts viscosity.
2. **Localization precision belongs in the analysis.** A pair-correlation function computed without
   accounting for localization uncertainty over-reports clustering at short distances (each molecule is
   a fuzzy blob, not a point). Where an uncertainty column exists, pass it through so the PCF/Ripley
   analysis can note the resolution floor. At minimum, **report the median uncertainty** so a user
   knows the length scale below which structure is not trustworthy.
3. **Multiple blinks of one molecule ≠ multiple molecules.** SMLM molecules blink and are localized
   repeatedly across frames; naive clustering counts them as a dense cluster. Flag this: if `frame` is
   present, offer a simple temporal-merge (localizations within a distance AND consecutive frames
   collapse to one) and **warn that un-merged data over-counts density**. Do not silently merge —
   surface it as a choice with its consequence stated.

### Part B — connect to the existing stats
Feed the `LocalizationSet` coordinates into `ripleys_l`, `pair_correlation_function`,
`nearest_neighbour_distance`, `local_object_density`. Add a thin SMLM analysis entry that runs these
and reports clustering with the precision floor annotated. Add the localizations as a napari points
layer (tagged) so they render over the image.

### Part C — provenance and ontology
Record loader source, detected units, and the merge choice in the output. Register the SMLM-specific
outputs (`ripley_l_max`, `pcf`, `nn_median`, `median_localization_precision_nm`) in the measurement
ontology with the clustering/precision caveats.

## Tests (`core`, synthetic)
- A ThunderSTORM-style CSV in nm loads to µm correctly; a pixel-unit CSV requires and uses
  `pixel_size_um`.
- **The units test:** the same spatial pattern expressed in nm vs µm yields identical Ripley/PCF after
  loading — proving the scale is normalized.
- Clustered vs random point sets: `ripleys_l` (already tested) distinguishes them through the loader
  path.
- Multi-blink data: temporal merge reduces the apparent density; the warning fires when unmerged.
- Median localization precision is reported and flows to the output.
- Missing/ambiguous units raise rather than assuming (the gate discipline).

## Steps
1. `toolbox/smlm_tools.py` — `LocalizationSet` + `load_localization_table` (ThunderSTORM + generic CSV,
   header sniff).
2. Units detection/declaration with a hard gate on ambiguity.
3. Optional temporal-merge for blinks, with a warning when skipped.
4. Connect to the existing `spatial_metrology_tools` functions; add a points layer.
5. Provenance + ontology entries.
6. A UI entry point (load table, declare units if needed, run clustering).
7. Tests above.
8. Full `pytest -m core` green.
9. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Localization tables (ThunderSTORM + generic CSV) load into a µm-normalized `LocalizationSet`.
- Ambiguous units are gated, never assumed; the same pattern in nm and µm gives identical stats.
- The existing Ripley/PCF/NN/density backend runs on loaded localizations.
- Localization precision is reported as the resolution floor; blink over-counting is flagged.
- Outputs carry provenance and ontology entries.
- Full `pytest -m core` green.

## Cautions
- **Units are the whole risk.** ThunderSTORM is nm; assuming px or µm silently destroys every spatial
  statistic. Gate on ambiguity exactly as the image pixel-size gate does.
- **Localization precision sets the floor** — clustering below it is not real; report it so the user
  cannot over-interpret short-range structure.
- **Blinks over-count density** — offer temporal merge and warn; never silently merge or silently not.
- Do not reimplement the spatial statistics — they exist and are tested; connect to them.
- This is import-and-analyze; PyCAT does not do the localization itself.
