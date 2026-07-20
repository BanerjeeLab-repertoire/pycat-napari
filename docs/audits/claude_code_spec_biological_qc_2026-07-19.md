# Claude Code spec — Biological QC: a second QC layer at the object level

**Date:** 2026-07-19 · **Target tree:** 1.6.144 · Verified against the 1.6.144 tree. Adds the
object-level QC layer the roadmap describes as *"a second QC layer beyond imaging QC"* — flagging
biological outliers rather than imaging problems. Additive: flags are reported, never silently
excluded.

## The gap (verified)
PyCAT's QC (`data_qc_tools.py`, 1760 lines) is thorough about **imaging** quality — saturation, focus,
SNR, vignetting, drift, photobleaching, Nyquist sampling, spherical aberration, ghosting. All of it
answers *"can I trust this image?"*

Nothing answers **"can I trust this object?"** Verified: no `touching_edge`, `biological_qc`, or
oversegmentation flagging exists outside incidental mask cleanup (`extend_mask_to_edges`,
`dist_to_edge` are geometry helpers, not QC).

That matters because the most common analysis errors are object-level, not image-level:
- a **cell touching the field edge** is truncated — its area, shape, and total intensity are wrong,
  and it silently biases every population statistic;
- an **oversegmented nucleus** (one nucleus split in two) doubles the apparent count;
- a **condensate outside the cytoplasm** is usually a segmentation error, not biology;
- a **dead or mitotic cell** has legitimate but wildly different morphology;
- an object with **extreme intensity** may be an aggregate or debris.

Every one of these passes imaging QC perfectly.

## Design — flag, quantify, never silently drop
`toolbox/biological_qc_tools.py`, headless (`core`-testable), operating on the object table + masks:
```python
def flag_edge_touching(labels, *, border_px=0) -> pd.Series[bool]
def flag_size_outliers(table, *, column='area', method='mad', k=3.5) -> pd.Series[bool]
def flag_shape_outliers(table, *, columns=('eccentricity','solidity')) -> pd.Series[bool]
def flag_intensity_outliers(table, *, column='intensity_mean') -> pd.Series[bool]
def flag_containment_violations(child_table, parent_labels) -> pd.Series[bool]   # condensate outside cell
def biological_qc(table, labels, *, parent_labels=None) -> pd.DataFrame
```
`biological_qc` returns the table with **boolean flag columns plus one `qc_flags` summary string**,
and a report of how many objects tripped each flag.

**The cardinal rule: this module flags, it does not filter.** Excluding objects is the user's
decision, made explicitly — consistent with the codebase's no-silent-gates contract and with the
filter-sensitivity programme's whole premise (a silent default that removes a population is the most
dangerous kind of bug). Provide the flags and the counts; let the analysis decide.

## Statistical honesty in the outlier tests
- Use **robust** statistics (median/MAD), not mean/SD — a population containing outliers corrupts the
  very estimator used to find them.
- `k` must be a **declared parameter with a stated default**, and the flag column must record the
  threshold used, so a downstream reader can see how aggressive the flagging was.
- **Never flag on a single criterion where the biology is legitimately variable.** Mitotic cells are
  real; the flag should say *"unusual morphology"*, not *"bad object"*. The wording matters — this is
  a hint for review, not a verdict.
- Edge-touching is the one flag that is **objectively a measurement artefact** (the object is
  truncated), so it can be stated definitively.

## Part B — surface it where it matters
1. **The consolidated long table** (comparative phenotyping increment 2) gains the flag columns, so
   condition comparisons can be recomputed with and without flagged objects — and the difference is
   visible. That is the scientifically honest use: *"the effect holds when edge-touching cells are
   excluded"* is a much stronger claim than an unqualified one.
2. **The QC report** gains an object-level section stating counts per flag, in the existing
   Image → Assessment → Interpretation → Recommendation shape the QC module already uses.

## Tests (`core`, synthetic)
- Edge-touching: objects constructed against the border are flagged; interior ones are not; a
  `border_px` margin widens the flag correctly.
- Size/intensity outliers: a population with known injected outliers flags exactly those; a clean
  population flags **none** (the cry-wolf test — as important as detection).
- Robustness: adding outliers does not change which *inliers* are flagged (this is what MAD buys, and
  it fails loudly with mean/SD).
- Containment: a child object outside its parent is flagged; one inside is not.
- **Flags never drop rows** — the returned table has the same length as the input. This is the
  contract test for "flag, don't filter."

## Steps
1. `toolbox/biological_qc_tools.py` with the flag functions + `biological_qc` aggregator.
2. Flag columns into the consolidated long table (additive).
3. An object-level section in the QC report.
4. The test suite above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (biological QC: object-level
   flags, reported not enforced).

## Definition of done
- Edge, size, shape, intensity, and containment flags exist and are robust-statistics based.
- `biological_qc` returns flags + counts and **never removes rows**.
- Flags appear in the consolidated table and the QC report.
- A clean population produces no flags; a seeded one flags exactly the injected objects.
- Full `pytest -m core` green.

## Cautions
- **Flag, never filter.** Silent exclusion is the exact failure mode the filter-sensitivity programme
  exists to catch; do not introduce a new instance of it in the name of quality.
- Robust statistics only — mean/SD outlier detection is corrupted by the outliers it seeks.
- Word flags as observations (*"touches image border"*, *"unusual morphology"*), not verdicts
  (*"bad cell"*). A mitotic cell is real data.
- Do not build the Measurement Reliability Index here — it composes QC with segmentation stability
  and benchmarking, and is a much larger construct.
