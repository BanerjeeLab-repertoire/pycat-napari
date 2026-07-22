# Claude Code spec — Interaction layer: selection state model, honest hit-testing, adapter contract

> **◐ GAP 2's HONEST HIT-TESTER (pure primitive) DONE, shipped 1.6.272. The analysis_plots wiring + Gaps
> 1/3/4/5 remain.** New Qt-free `utils/hit_testing.py`: `hit_test(curves, click_xy, *, tolerance_px,
> ambiguity_px) -> HitResult` finds the nearest curve by point-to-segment distance over the curves'
> DISPLAY-space coordinate arrays, and — the point — **refuses an ambiguous click** (best and second-best
> within `ambiguity_px` → `primary=None`, `candidates` NAMES both, so the caller reports rather than guesses)
> and a miss beyond `tolerance_px`. `point_segment_distance` clamps to endpoints (a click past an end
> measures to the end, not the infinite line). Pure geometry, backend-neutral — the same primitive backs the
> matplotlib wiring and the future pyqtgraph adapter. `test_hit_testing.py` (`core`) pins nearest-wins,
> empty→nothing, ambiguous→nothing+candidates, endpoint clamping, one-click-one-selection, log-in-display.
> **Remaining:** wire it into `analysis_plots.py` (remove the per-line `set_picker`, one `button_press_event`
> per axes calling `hit_test` on the coordinate arrays) — the Qt integration; and Gap 1 (`SelectionState`
> hover/selected/pinned, back-compat-preserving), Gap 3 (promote a non-sampled selected track), Gap 4
> (LineCollection background + overlay artists), Gap 5 (the `SelectionView` adapter protocol + contract
> suite). This primitive is the foundation the wiring and the pyqtgraph adapter build on (per the spec's
> sequencing note).

**Date:** 2026-07-17 · **Target tree:** 1.6.90 · Verified against the 1.6.90 tree. Derived from an
architecture review of PyCAT's brushing layer. **~60% of that review describes what already
exists** — this spec covers ONLY the verified gaps. Additive; no rewrite of the landed brushing arc.
Touches `selection_service.py`, `analysis_plots.py`, a new adapter protocol module. Not `file_io.py`.

## What already exists — DO NOT REBUILD
Verified in the tree, so the review's corresponding sections are already satisfied:
- **Entity identity** (`utils/entity_ref.py`): `EntityKey(dataset_id, operation_id, entity_type,
  entity_id)` + `EntityLocation` + `EntityRef`, with the opaque `_pycat_entity_id` column. This IS the
  review's §3 registry identity. Keep it.
- **Selection dispatch** (`utils/selection_service.py`): `generation` counter (:58), `source_view`
  echo-suppression (:199/:227), `subscribe`/`unsubscribe`/`subscribe_deferred` (:112–137) — i.e. the
  review's §10 immediate/deferred two-lane design and §5 suppression, already shipped.
- The linked-selection dock, overlay highlighting, opt-in camera-follow (brushing inc 5).
**Do not refactor these into a new `interaction/` package tree.** A parallel re-implementation would
reintroduce exactly the multiple-registries tax the engineering audit flagged. Extend in place.

---

## Gap 1 — Selection is one object; it should be a STATE with hover/selected/pinned
**Verified:** `Selection.mode` is a STRING (`selection_service.py:56`, `"hover"|"selected"|"pinned"`)
and the service holds a single `self._selected` (:104). So there is no multi-selection, no pinning
alongside an active selection, and no independent hover. Scientifically this blocks: ctrl-click to
build a comparison set, pinning a track while exploring another, and "Escape clears selection but
keeps pins."

**Fix — an immutable state, dispatched as one object:**
```python
@dataclass(frozen=True)
class SelectionState:
    selected: frozenset[str]      # entity id strings (EntityKey.as_column_value())
    primary: str | None
    hovered: str | None
    pinned: frozenset[str]
    generation: int
```
- `SelectionService` holds a `SelectionState` and publishes the whole state (not a lone `Selection`).
- Add commands that produce a new state: `select(entity, source)`, `toggle(entity, source)` (ctrl-click),
  `hover(entity, source)`, `pin(entity, source)`, `clear(source)` (clears `selected`+`hovered`, KEEPS
  `pinned`).
- **Back-compat is mandatory:** keep the existing `select(Selection)` / subscriber callback signature
  working (subscribers receive the state; adapt or wrap so current subscribers — the dock, VPT, the
  plots — don't break). Existing brushing tests must stay green.
- `generation` increments per state change (already exists — reuse it).

**Test** (`core`): toggle adds/removes from `selected`; `clear` empties selected/hovered but preserves
`pinned`; hover doesn't disturb selected; one command → one generation increment → one publish;
existing subscribers still fire.

## Gap 2 — Replace per-line pickers with ONE hit-tester + ambiguity rejection
**Verified:** `analysis_plots.py` calls `ln.set_picker(5)` on every line (:157, :493) with 2
`pick_event` handlers (:365, :711). In dense spaghetti, matplotlib's picker returns whatever artist it
hits first — arbitrary, and scientifically dishonest.

**Fix:**
- Remove per-line `set_picker`; use ONE `button_press_event` handler per axes.
- Hit-test in DISPLAY coordinates (correct under log scales and zoom — the MSD plot is log-log):
  point-to-segment distance, `t = clip(dot(p-a, b-a)/dot(b-a, b-a), 0, 1)`, take the minimum across
  segments. With ~100 displayed curves a direct scan is instant — do NOT build a spatial index.
- Return a result with ambiguity, and **refuse ambiguous clicks**:
```python
@dataclass
class HitResult:
    primary: str | None
    candidates: tuple[str, ...]
    distance_px: float
    ambiguity_px: float          # second_best_distance - best_distance
```
  If `distance_px > tolerance` → select nothing. If `ambiguity_px < threshold` → select nothing and
  give feedback naming the candidates (a status message / brief overlay). Arbitrary selection in a
  dense region is the failure mode this removes — consistent with the no-silent-gates philosophy.
- Suppress re-selecting the already-selected entity (no-op, avoids redundant republish).

**Test** (`core`, pure geometry): nearest segment wins; an empty-area click selects nothing; an
ambiguous click (two curves within threshold) selects NOTHING and reports candidates; log-scale
coordinates are handled in display space; one click → at most one selection.

## Gap 3 — A selected track that isn't in the representative sample cannot be shown
**Verified:** `representative_track_sample` (`analysis_plots.py:41`) draws a fidelity-targeted subset
(~100 of N). A track selected from the TABLE that isn't in that sample has no artist — so it can't be
highlighted. Real limitation of the current bidirectional brushing.

**Fix:** the displayed set becomes `representative_sample | selected | pinned`. On an inbound
selection, if the entity has no artist, render it as a FOCUS curve (an overlay `Line2D`, styled as
selected); when deselected and not pinned and not in the sample, remove it. Bounded rendering, full
brushing.

**Test:** selecting an entity outside the sample promotes it (artist exists, highlighted);
deselecting removes it; a pinned one survives deselection; a sampled one is never removed.

## Gap 4 — Render background curves as a `LineCollection`, selection as overlay artists
**Verified:** 39 individual `Line2D`/`ax.plot` sites, zero `LineCollection`. Hundreds of individually
styled lines are slow to draw and force per-artist style restore on every selection change.

**Fix (MSD spaghetti plot specifically):**
- background representative curves → ONE `LineCollection` (thin, low alpha);
- selection/hover/pinned → a small number of overlay `Line2D` artists on top (this is the same
  O(1)-overlay principle brushing inc 4 already applied to scatter);
- hit-testing uses the underlying coordinate ARRAYS (Gap 2), not artist picking — so collapsing the
  background into one collection costs nothing for interaction.
Keep the population median/percentile band as-is.

**Test:** the spaghetti plot creates one background collection + ≤ small-N overlay artists (not N
Line2D); selection changes touch only overlays (background collection untouched).

## Gap 5 — A view-adapter contract so every linked view behaves the same
**Verified:** subscribers are bare callbacks (`selection_service.py:112`) with no shared contract, so
each view re-invents apply/suppress/cleanup and they drift.

**Fix — a small Protocol + shared tests (NOT a package restructure):**
```python
class SelectionView(Protocol):
    view_id: str
    def apply_selection(self, state: SelectionState) -> None: ...
    def close(self) -> None: ...      # disconnect mpl cids / Qt signals; unsubscribe
```
- Add a `programmatic update` guard helper each adapter uses so a PROGRAMMATIC view update never emits
  a command (the review's §5 rule — the primary contract; the existing source-suppression stays as the
  second line of defence).
- On registration, immediately push current state so a newly-opened plot reflects the active selection.
- **Adapter contract tests every adapter must pass** (the highest-value part): programmatic apply emits
  NO command; a user action emits exactly ONE command; `close()` disconnects everything and
  unsubscribes; opening applies current state; an unknown entity is handled safely.
- Retrofit the EXISTING views (MSD plot, VPT table, napari overlay, dock) to the protocol — wrap, don't
  rewrite.

**Test:** the shared contract suite, parametrized over each adapter.

---

## Steps
1. `SelectionState` + commands in `selection_service.py` (back-compat preserved); tests.
2. Hit-tester + ambiguity rejection in `analysis_plots.py`; remove per-line pickers; tests.
3. Promotion of selected/pinned non-sampled tracks; tests.
4. `LineCollection` background + overlay artists for the MSD plot; tests.
5. `SelectionView` protocol + programmatic-update guard + the shared adapter contract suite; retrofit
   existing views.
6. Full `pytest -m core` green — especially the existing brushing/VPT tests (this is additive;
   nothing that worked may break). Complexity budget: extract helpers, don't raise the ceiling.
7. Ship: own version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (interaction layer:
   hover/selected/pinned state, honest hit-testing with ambiguity rejection, non-sampled track
   promotion, LineCollection rendering, adapter contract).

## Definition of done
- Hover, multi-select (ctrl-click), and pinning are real, independent state; Escape clears selection
  but keeps pins.
- One hit-tester per axes; ambiguous or empty clicks select NOTHING and say why; no per-line pickers.
- A track selected from any view is displayable even if outside the representative sample.
- The MSD background is one `LineCollection`; selection touches only overlay artists.
- Every linked view satisfies the adapter contract suite (programmatic ≠ command; clean close).
- Full `pytest -m core` green; existing brushing behaviour preserved.

## Cautions
- **Extend in place — do NOT create a parallel `interaction/` package** duplicating `entity_ref.py` /
  `selection_service.py`. The review proposes a fresh tree; PyCAT already has these, and a second
  implementation is the registry-duplication tax the audit warned about.
- Back-compat: existing subscribers/tests must keep working through the state change. Wrap, adapt,
  don't break.
- Ambiguity rejection is a FEATURE (scientific honesty), not a bug — an ambiguous click must select
  nothing rather than guess.
- Keep hit-testing in display coordinates — the MSD plot is log-log; data-space distance is wrong.
- Don't switch plotting backends here; this is backend-neutral by design. **This spec must land
  BEFORE `claude_code_spec_pyqtgraph_backend_2026-07-17.md`** — the pyqtgraph adapter should be built
  against the `SelectionView` protocol and contract tests introduced here, not against the old
  bare-callback API (which would mean writing it twice, and risking a second selection path that
  bypasses the contract).
- Don't fold the FilterStore / full ViewCoordinator lifecycle from the review into this increment —
  selection-vs-filter separation is a worthwhile later item, not this scope.
