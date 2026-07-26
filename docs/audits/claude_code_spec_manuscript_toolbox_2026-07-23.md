# Claude Code spec — Manuscript-prep figure toolbox

> **◐ STATUS — Part A DONE (the panel registry), shipped 1.6.384.** `toolbox/manuscript/panels.py`: the
> `FigurePanel` dataclass + a registry of five panels in figure order, each with a plain-language
> `data_requirement` and a data-driven `available(context)` so a data-absent panel is greyed with its
> requirement (never a dead button). **Fig 3 — comparative phenotyping** is fully wired to
> `condition_comparison_figure` and generates a real figure end-to-end (proven headlessly);
> **Supp — reliability** and **Supp — ΔG thermodynamics** are wired to `reliability_report_section` /
> `delta_g_transfer`. **Premise drift found:** `benchmarks/run_suite` and `data_qc_tools.run_full_qc` no longer
> exist at the paths the spec names (data-QC moved to the `data_qc/` package), so **Fig 1 (QC)** and
> **Fig 2 (benchmark)** are registered but grey honestly (`available=False`) until their composers are
> re-pointed — exactly the greyed-not-dead behaviour the design calls for. `tests/test_manuscript_panels.py`
> (`base`, 8).
>
> **GIF path — DONE, 1.6.385.** `export_stack_as_gif` added beside `export_stack_as_mp4` (shared LUT/contrast
> helpers), and a latent bug fixed along the way — `matplotlib.cm.get_cmap` was removed in matplotlib 3.9, so
> the MP4 export was already broken in the current env (no test caught it); both paths now use a version-tolerant
> `_get_cmap`. `tests/test_video_export_gif.py` (`base`, 5). **Fig 1 (QC) IS re-pointable** —
> `data_qc.runner.run_full_qc` exists (data-QC moved to the `data_qc/` package); **Fig 2 (benchmark) has no
> composer** in the tree (no validation/Dice-F1 suite found) and stays greyed. **Remaining: re-point Fig 1 to
> `run_full_qc` (render its report as the QC sub-panel); wire the GIF option into the export widget; Part C timed
> panels; and the Qt gallery that renders the registry (greyed tooltips + generate-on-click).**
>
> **Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. The highest-value
remaining item if the manuscript is the goal: a grouped set of **panel generators** that turn the
rigor/measurement work into publication figures, each stating what data it needs. Everything it composes
now exists and is tested — this is assembly, not construction.

## What exists to compose (all verified present)
`benchmarks/run_suite.py` · `utils/comparative_figures.py` · `utils/figure_spec.py` (canonical, now
feature-complete after the publication-features work) · `utils/reliability.py` · `utils/calibration.py` ·
`toolbox/data_qc_tools.py` · `toolbox/video_export_tools.py` · `utils/feature_registry.py` (so the
toolbox becomes discoverable for free).

**The one genuinely new piece:** `video_export_tools` has **no GIF path** (grep for gif → 0). Everything
else is wiring.

## Part A — The panel registry
```python
# toolbox/manuscript/panels.py
@dataclass(frozen=True)
class FigurePanel:
    key: str                  # 'fig2_benchmark'
    title: str
    figure_role: str          # 'main' | 'supplementary'
    data_requirement: str     # PLAIN LANGUAGE: exactly what to run first
    tooltip: str
    generate: Callable        # builds the panel from that data
    available: Callable       # is the required data present now?
    produces: str             # 'figure' | 'table' | 'recorded_demo'
```
`data_requirement` is the heart of it: *"run the validation suite (Analysis → Validation) on at least
one segmentation case; needs ground-truth masks."* When the data is absent the panel is **greyed with
its requirement as the tooltip — never a dead button, always an instruction.**

## Part B — The panels
Grouped by the manuscript's figure plan; each generates through the canonical `FigureSpec`, so they
share fonts, size presets, colourblind palettes, ontology axis labels, and vector/bundle export:

- **Fig 1 — QC pipeline.** Largely schematic, but the QC portion generates from `data_qc_tools.run_full_qc`
  on a sample image. Generate the QC sub-panel; leave the schematic as a template slot.
- **Fig 2 — benchmark/validation.** Fully generatable from `benchmarks/run_suite` — Dice/F1 vs ground
  truth, plus cross-release metric stability if ≥2 releases are recorded.
- **Fig 3 — comparative phenotyping.** From `comparative_figures.condition_comparison_figure` on the
  consolidated long table. Replicate-aware, brushing already built.
- **Supp — reliability/rigor.** MRI per measurement + parameter sensitivity.
- **Supp — calibrated thermodynamics.** Partition → concentration → ΔG from `calibration.py`.

## Part C — Timed panels (measured, not asserted)
For claims that are measurable, generate the graph rather than the assertion:
- **Lazy-loading performance** — load time and first-frame latency vs file size across a few real files.
  Turns "PyCAT handles big data" into a figure with numbers.
- **Batch throughput** — images processed vs wall time from a real batch run.
- **Runtime by method** — from the validation suite's recorded runtimes.

Report the machine and environment with the numbers; never hardcode a performance claim.

## Part D — Recorded demos
Add a **GIF export path** to `video_export_tools` (imageio GIF writer alongside the existing MP4), plus a
scripted-capture helper that records a napari + dock interaction.

Three recipes, `produces='recorded_demo'`:
1. **Linked analysis (lead demo)** — click a plot point → the object highlights in the image → the table
   row scrolls into view; then switch objects. The recipe drives the **real `SelectionService`**, so the
   GIF is always truthful to current behaviour.
2. **Lazy loading** — open a large file → immediate view → smooth scroll. Pairs with the Part C
   performance panel that quantifies it.
3. **Batch replay** — interactive → JSON → N images → results.

Lead with the linked-analysis demo: it is the rare capability and it embodies keeping the biology
attached to the measurement.

## Part E — Discoverability
Register the toolbox as a `FeatureCard` so it appears in Explore capabilities alongside everything else —
no bespoke menu wiring.

## Tests (`core` + Qt-smoke for capture)
- Each panel's `available()` correctly reports present/absent from a fixture; absent → disabled **with
  the requirement string**.
- Benchmark, comparative, reliability, and calibration panels generate a figure from fixture data and
  route through `FigureSpec` (assert ontology-derived labels, vector export).
- The performance panel produces a plot from a timing fixture with measured values.
- GIF export writes a valid GIF from a synthetic frame sequence.
- The linked-analysis recipe drives the real `SelectionService` (Qt-smoke).
- **No panel fabricates data**: an unmet requirement greys/raises, never emits a placeholder figure.

## Steps
1. `toolbox/manuscript/panels.py` — `FigurePanel` + registry with requirement strings.
2. Wire the generatable panels to their existing sources through `FigureSpec`.
3. Timing harnesses → performance/throughput/runtime panels.
4. GIF export + scripted capture + the three recipes.
5. The Manuscript Prep dock; register as a `FeatureCard`.
6. Tests; full `pytest -m core` green.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- A Manuscript Prep surface lists the paper's figures as panels with plain-language data requirements
  and generate buttons gated on availability.
- Panels generate through the canonical `FigureSpec`.
- Timed claims produce measured graphs with the environment noted.
- GIF export exists; three demo recipes drive the real system, led by linked analysis.
- Nothing fabricates data; the toolbox is discoverable as a feature card.

## Cautions
- **Compose, don't reinvent.** If a number isn't computable yet, list the requirement — don't fake it.
- **The requirement string IS the feature.** Vague instructions defeat the purpose.
- **Demos must be truthful** — real `SelectionService`, never a staged interaction.
- **Timed panels report measured numbers** with the machine noted; a hardcoded performance claim is a
  reviewer liability.
- Route every panel through the canonical `FigureSpec`; do not grow a second styling path.
