# Roadmap — Comparative phenotyping: consolidated output, cross-condition figures, publication polish

**Date:** 2026-07-16 · **Tree:** 1.6.72 · A roadmap, not a single spec — this is a multi-increment
capability that turns PyCAT from "produces measurements per image" into "produces comparative,
publication-ready phenotyping across mutants / perturbations / conditions." Grounded in the current
batch output shape (verified below). Increments are sequenced foundation-first; each becomes its own
spec when its turn comes.

## The gap (verified in the 1.6.72 tree)
- **Batch writes one subfolder per image** (`batch_processor.py`: `file_output = output_dir /
  image_path.stem`), each with its own tables. There is **no top-level consolidated table**.
- **No condition/perturbation concept exists** anywhere real — the `condition` hits in the code are
  the word in pseudoreplication warnings, not a data model. No genotype/treatment/replicate/dose.
- So a comparative study across N mutants = N folders of disconnected CSVs the scientist stitches by
  hand in Excel/pandas. That manual step is exactly the error-prone, expertise-dependent work PyCAT
  exists to remove — and it's where "PyCAT owns the downstream quantification" currently breaks down.
- **Plots are matplotlib**, drawn per-analysis (`analysis_plots.py`), with no cross-condition faceting
  and no "refine this figure for publication" path. Export today = whatever `savefig` a widget does.

This connects to several existing roadmap items (the condensate thermodynamics report, the shared
output schema, the measurement ontology, phenotypic vs structural profiling). This roadmap is the
UNIFYING frame that ties them into a comparative-analysis capability.

## The architecture (foundation-first)
```
   flexible metadata IN            the substrate              the visible output         the polish
┌──────────────────────┐   ┌────────────────────────┐   ┌────────────────────┐   ┌──────────────────┐
│ sample sheet  ─┐      │   │  CONSOLIDATED          │   │ COMPARATIVE        │   │ PUBLICATION      │
│ filename parse ─┼────▶ │──▶│  long-format table     │──▶│ FIGURES            │──▶│ figure refinement│
│ in-app tag    ─┘      │   │  object×measure×cond×  │   │ (faceted + stats)  │   │ (theme/label/    │
│  (ALL THREE)          │   │   provenance           │   │                    │   │  export)         │
└──────────────────────┘   └────────────────────────┘   └────────────────────┘   └──────────────────┘
```
Build left to right — each increment is useless-to-wrong without the one before it. The consolidated
table is the keystone: comparative figures and refinement are just views/polish over it.

---

## Increment 1 — the condition/metadata model (all three attach paths)
**Decision (Gable): all three available.** A condition/perturbation label attaches via ANY of:
- **Sample sheet (primary):** a CSV the user fills — `filename → genotype, treatment, replicate,
  dose, …` (arbitrary columns). Batch reads it and joins by filename/stem.
- **Filename/folder parse (fallback):** a configurable pattern (e.g. `{genotype}_rep{replicate}_
  {dose}uM`) that extracts fields when no sheet row exists.
- **In-app tag (interactive):** a metadata field per image/session, stored in the manifest
  (`session_manifest.py` already persists session metadata — extend it), for one-off tagging.
Precedence: explicit sample-sheet row > in-app tag > filename parse > "unlabelled". A small
`SampleMetadata` resolver returns the condition dict for a given image, from whichever source has it.
Nothing downstream cares WHICH source — it just gets `{genotype:…, treatment:…, replicate:…}`.

**Deliverable:** `SampleMetadata` resolver + sample-sheet reader + configurable filename parser +
manifest metadata field; a test that all three paths yield the same condition dict and precedence is
honoured.

## Increment 2 — the consolidated long-format table (THE FOUNDATION)
One tidy dataframe batch emits at the TOP level (not per-image): each row = one object's one
measurement, with its condition labels and provenance columns attached:
```
image_stem | genotype | treatment | replicate | dose | object_type | object_id |
measurement | value | units | channel | frame | pixel_size_um | pycat_version | operation_id | ...
```
- **Long (tidy) format** — the substrate for grouped stats/faceting (wide is a pivot away; long is not
  recoverable from wide). One `measurement`/`value`/`units` triple per row.
- Reuses the increment-2-brushing **EntityRef identity** (`object_id`) and the **measurement ontology**
  (units/definitions) if built — so a consolidated row is traceable back to its image object AND
  self-describing. Ties to the "shared output schema" roadmap item.
- Batch appends every image's objects into this one table (streaming, so a 200-image batch doesn't
  hold all in memory); writes it as `pycat_batch_results/consolidated_long.csv` (+ parquet for big
  studies) alongside the existing per-image folders (additive — don't remove per-image output).
- Provenance columns (pixel size, version, operation id, acquisition metadata) travel per row — the
  metadata-awareness the engineering audit wants, made automatic.

**Deliverable:** batch emits the consolidated long table; a per-row provenance contract; a test that
N images → one table with N images' objects, correct condition join, no memory blowup. **This is the
keystone — everything else is a view over it.**

## Increment 3 — comparative figures (grouped / faceted, with honest stats)
Cross-condition plots built FROM the consolidated table:
- grouped/faceted by condition (box/violin/strip per genotype×treatment; dose-response curves;
  per-replicate points overlaid on condition summaries);
- **honest statistics** — respect the pseudoreplication warning already in `analysis_plots.py`
  (per-object pixels are pseudoreplicated; aggregate to the replicate/biological-unit level before
  error bars/tests), report the test used and n at each level, don't fabricate significance;
- the standard comparative-phenotyping figure types (per-measurement condition comparison, a
  measurement×condition matrix, dose-response, replicate-structure plot).
Built on the existing `plot_backends` abstraction so these participate in brushing (select a condition
group → highlight its objects) and can later render via PyQtGraph interactively.

**Deliverable:** a comparative-figure module that takes the consolidated table + a grouping spec and
emits faceted figures with replicate-aware stats; tests on synthetic multi-condition data with a known
effect (recovers the effect; doesn't cry significance on a null; aggregates to avoid pseudoreplication).

## Increment 4 — publication figure refinement (the polish layer)
Take ANY PyCAT matplotlib figure (comparative or per-analysis) → refine → export publication-quality:
- an editable figure spec (title, axis labels, units, limits, tick formatting, colour palette /
  colour-blind-safe themes, font sizes, panel layout, significance annotations, scale bars);
- a small refinement UI (adjust without re-running the analysis — the figure holds its data + spec);
- export at publication settings (vector PDF/SVG + high-DPI PNG, embedded fonts, sized to journal
  column widths);
- **matplotlib stays the export/publication backend** (per the plotting-backend addendum — PyQtGraph
  is for interactive explore, matplotlib for publish). This increment is where "refine a matplotlib
  plot" lives.
- Consistent theming across all figure types so a paper's panels match.

**Deliverable:** a figure-refinement layer (spec + UI + publication export) usable on any PyCAT figure;
a test that a refined figure round-trips its spec and exports at the requested DPI/format/size.

---

## Sequencing & why foundation-first
1. **Metadata model** (increment 1) — nothing can be comparative without condition labels.
2. **Consolidated table** (increment 2) — THE keystone; comparative figures and refinement are views
   over it. Build and harden this before anything visual.
3. **Comparative figures** (increment 3) — the visible payoff, but only correct once the table +
   replicate structure exist (pseudoreplication is a correctness issue, not cosmetics).
4. **Refinement/export** (increment 4) — polish last; it applies to ALL figures (comparative and
   per-analysis), so it's most valuable once the comparative figures exist to polish.

Each increment is independently useful once its predecessor lands, and each becomes its own verified
spec at its turn (re-validated against the tree then). Do NOT build them in parallel — the table
schema decisions in 2 constrain 3 and 4.

## Manuscript significance
This is the difference between "another analysis tool" and "a comparative phenotyping platform." The
consolidated condition-aware table + replicate-honest comparative figures directly serve a
multi-mutant/perturbation figure in the paper — and the provenance-per-row + measurement ontology make
that figure REPRODUCIBLE, which is the rigor angle editors reward. It also unifies the phenotypic-vs-
structural profiling axis: both become columns/facets in the same consolidated substrate.

## Open decisions for later increments (flag when speccing each)
- Increment 1: exact sample-sheet column contract + filename-pattern grammar.
- Increment 2: long-table column set (which provenance columns are mandatory); parquet vs csv default
  for large studies; how object_id ties to EntityRef if the brushing arc hasn't landed yet.
- Increment 3: which comparative figure types ship first; default statistical tests + the
  biological-unit aggregation rule.
- Increment 4: journal-preset sizes; theme set; whether refinement is per-figure or a saved global
  style.
