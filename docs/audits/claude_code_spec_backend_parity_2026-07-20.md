# Claude Code spec — Plot-backend parity: seaborn subsets, PyQtGraph default, Plotly scope

> **◐ STATUS — Part 1 DONE (shipped 1.6.260). Parts 2–3 remain (both interactive/Qt-bound).**
>
> **Part 1 — seaborn per-artist subset mapping — DONE, with a premise correction.** The blunt multi-artist
> refusal is replaced by `plot_backends._seaborn_subset_mappings`, which matches each artist to exactly one
> hue subset by the SAME point-count + coordinate verification the single-artist path uses (matching by
> COORDINATES, not by assuming seaborn's artist order, so a future ordering change cannot silently mis-map),
> and falls back to the refusal if any artist can't be matched to exactly one distinct subset — **a verified
> mapping or an honest refusal, never a guess**, exactly the property the audit praised. `scatter()`'s
> multi-artist branch now returns the verified mappings; `brushing.attach_brushing()` dispatches single-artist
> vs. verified-split so callers don't special-case it. Covered by `tests/test_seaborn_subset_brushing.py`
> (8 `core` tests).
>
> **PREMISE CORRECTION:** modern seaborn (**0.13.2** in this env) keeps a hue plot in ONE `PathCollection`
> in DataFrame order — `hue`/`style`/`size` do NOT split it into multiple artists. So `scatter()`'s existing
> single-artist path ALREADY brushes hue correctly (verified by `_verify_row_order`), and the multi-artist
> refusal the spec targeted **never triggers on this seaborn**. Part 1 is therefore a *verified fallback* for
> seaborn versions/plots that do split, not a fix for a live bug — it removes a blunt refusal and future-proofs
> the safety property, and the real-seaborn end-to-end test confirms a hue scatter is brushable, not refused.
>
> **Parts 2–3 REMAIN, both interactive/Qt-bound** (deferred with the other UI specs): Part 2 (default to the
> PyQtGraph backend above an ~N-point threshold for *interactive* scatter, matplotlib retained for
> publication, record which backend rendered) and Part 3 (Plotly scoped honestly — hover/identity always,
> click-to-napari only where QtWebEngine is present, no dead affordances). The headless slices there are thin
> (a threshold decision + a backend-provenance record); the substance is Qt interaction.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. The brushing
audit's §4: the four plot backends are not at parity. This spec closes the three concrete gaps it
names, in priority order, without weakening the scientific-safety stance that makes the current
behaviour trustworthy.

## Verified state
- `plot_backends.py:177-182` — seaborn: when it draws more than one artist (a `hue`/`style`/`size`
  split), PyCAT **refuses brushing** rather than risk a wrong index map. Scientifically safe, but it
  disables brushing on the most common exploratory plots.
- `plot_backend_pyqtgraph.py` exists and (per the audit) speaks directly to `SelectionService`, but is
  not the default for large scatters; its Qt interaction tests are GUI-gated.
- Plotly is present but full click-to-napari needs the optional QtWebEngine bridge.

## Part 1 — seaborn multi-artist subset mapping (highest value)
The current all-or-nothing refusal is safe but blunt. The audit's fix is right: **build a separate
entity-id mapping per artist** instead of requiring one artist for the whole table.

When seaborn splits by `hue` (etc.), each artist corresponds to a **subset** of the DataFrame in a
known order. So:
1. Reconstruct the grouping seaborn used (the `hue`/`style`/`size` columns and their category order).
2. For each artist, compute the subset of rows it represents, **in the order seaborn plotted them**.
3. Map `artist[i], point[j] → subset entity id`.
4. Brushing then works per artist.

**The safety rule stays paramount:** only enable per-artist brushing when the subset reconstruction can
be *verified* — the number of points in each artist must equal the size of the reconstructed subset. If
any artist's point count does not match its subset, **fall back to the current refusal** for that plot.
A verified mapping or an honest refusal — never a plausible guess. This preserves exactly the property
the audit praised (refusing unsafe index mappings) while removing the refusal where it is provably safe.

## Part 2 — PyQtGraph as the default for large interactive scatters
The audit: PyQtGraph is the strongest route for high-performance interactive exploration and should be
the default for large scatters, with matplotlib retained as the publication path.
- Add a **size threshold**: above N points (start ~5,000, configurable), interactive scatter defaults
  to the PyQtGraph backend; below it, matplotlib is fine.
- **Matplotlib remains the canonical publication backend** — the default switch is for *interactive*
  exploration only. Export/publication always routes through matplotlib (the FigureSpec system).
- Make the choice explicit and overridable, and record which backend rendered a given view.
- Because PyQtGraph's interaction tests are GUI-gated, add whatever `core`-level tests are possible
  (the data→coordinate mapping, the `SelectionService` wiring) and mark the true interaction tests
  Qt-smoke.

## Part 3 — Plotly: scope it honestly as optional
The audit: Plotly has identity-bearing hover but full click-to-napari depends on QtWebEngine, so it is
not "addressable in the same way" in a standard install.
- **Keep Plotly optional and say so.** Where QtWebEngine is absent, Plotly offers hover/identity but not
  click-to-napari brushing — surface that limitation in the UI rather than silently offering a dead
  button.
- Do not make Plotly a core brushing path; matplotlib (publication) and PyQtGraph (interactive) are the
  two first-class routes.
- Detect QtWebEngine availability and enable the click bridge only when present.

## Tests (`core` where possible; Qt-smoke otherwise)
- **Seaborn subsets:** a `hue`-split plot with verifiable subsets enables per-artist brushing, and a
  clicked point maps to the correct entity id in the right subset.
- **Seaborn safety fallback:** when an artist's point count does not match its reconstructed subset,
  brushing is refused for that plot (the safety property preserved).
- **PyQtGraph default:** a scatter above the threshold selects the PyQtGraph backend; below it,
  matplotlib; the choice is overridable and recorded.
- **Publication still matplotlib:** exporting any interactive plot routes through matplotlib regardless
  of the interactive backend used.
- **Plotly scope:** with QtWebEngine absent, the Plotly path reports hover-only (no dead click button);
  with it present (mockable), click bridging is enabled.

## Steps
1. Seaborn per-artist subset mapping with the point-count verification gate + fallback.
2. PyQtGraph default-above-threshold for interactive scatter; matplotlib retained for publication;
   record the backend.
3. Plotly optional-scope: QtWebEngine detection, honest UI messaging, no dead affordances.
4. Tests above.
5. Full `pytest -m core` green.
6. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (backend parity: seaborn hue
   brushing where verifiable; PyQtGraph default for large interactive scatter; Plotly scoped optional).

## Definition of done
- Seaborn `hue`-split plots brush correctly where the subset mapping is verifiable, and refuse safely
  where it is not.
- Large interactive scatters default to PyQtGraph; publication always routes through matplotlib.
- Plotly is honestly scoped — hover/identity always, click-to-napari only with QtWebEngine, no dead
  buttons.
- Full `pytest -m core` green.

## Cautions
- **A verified mapping or an honest refusal — never a guess.** The seaborn subset mapping must be
  point-count-verified per artist; if it can't be, keep the current refusal. This is the exact
  scientific-safety property the audit praised.
- **Matplotlib stays the publication path** — the PyQtGraph default is interactive-only.
- **No dead affordances** — do not offer Plotly click-to-napari where QtWebEngine can't back it.
- Record which backend rendered a view (provenance + debuggability).
- Do not weaken the "refuse unsafe index mappings" stance anywhere; extend where safe, refuse otherwise.
