# Claude Code spec — Navigator increments 3 & 4: make the generator reachable

> **◐ STATUS — Increment 3 DONE (shipped 1.6.308). Increment 4 remains.**
> **Increment 3 (minimal navigator dock).** Qt-free `navigator/session.py` (`NavigatorSession` drives the
> existing `HybridQuestionEngine` + `Planner`: next_question → answer → is_ready → compile_plan; editing is a
> pin + recompile) + `plan_rows()` which renders a `Plan` with each step's quality-gate verdict INLINE
> (blocked names why / downgraded stays runnable with a caveat / unknown names its probe — increment 2 made
> visible). Thin `ui/navigator_dock.py` renders the question flow → colour-coded plan rows → Run/Start-over;
> `install_navigator_action` mounts it with tabify (1.6.297) from `central_manager`. **Also fixed:** the engine
> loads its question tree from the shipped `navigator/data/*.xlsx` and needed **openpyxl**, which was
> UNDECLARED — the engine could not construct on a clean install; openpyxl is now a dependency (the workbooks
> already ship in the wheel; verified). `tests/navigator/test_navigator_session.py` (`base`, 7) +
> `tests/test_navigator_dock.py` (`integration`, 4). **Follow-ons:** the plan→ops execution bridge (`on_run` is
> wired but `None` until it lands) and **Increment 4** (beginner mode + feature-card surfacing).

**Date:** 2026-07-23 · **Target tree:** 1.6.297 · Verified against the 1.6.297 tree. Increments 1 and 2
landed: the catalog now holds **89 operations** (measurement half added) and the planner consults
`quality_gate` (2 navigator files reference it). The engine works and is quality-gated — and **no user
can reach it**. This is the last mile, cut into two independently-shippable pieces.

## Verified state
| piece | status |
|---|---|
| catalog operations | **89** (was 79) — measurement ops added |
| `quality_gate` in `navigator/` | **2 files** — planner gating wired |
| `app_mode` consumers in `ui/` | **0** |
| `feature_registry` consumers anywhere | **0** |
| navigator dock (`QuestionEngine`/`AnalysisIntent` in ui/toolbox) | **0** |

`utils/app_mode.py` (`AppMode`, `current_mode`, `set_mode`, `is_beginner`) and
`utils/feature_registry.py` (`FeatureCard`, `FeatureRegistry`, `registry`, `register_feature`) are built
and tested with **zero consumers**. The drivable engine API is
`HybridQuestionEngine.next_question(intent, ctx)` (`question_engine.py:225`) and
`Planner.compile(intent, ctx, …)` (`planner.py:137`).

---

## Increment 3 — a minimal navigator dock (ship alone)

**Scope: one dock, no beginner mode, no feature registry.** The smallest thing that makes the generator
usable.

A dock that drives the existing engine:
1. Ask `next_question(intent, ctx)`; render the returned `QuestionSpec` as its prompt plus choices.
2. Record the answer into the `AnalysisIntent`; repeat until the engine returns `None` (leaf reached).
3. Call `planner.compile(intent, ctx)` and render the resulting plan as an **editable** step list — this
   is the reference design's "editable methods widget", not a locked wizard.
4. **Surface the quality-gate reasons inline.** Increment 2 put verdicts in `gate_report`; a blocked step
   must show *why* ("ΔG needs a calibrated pixel size — set the scale first"), a warn/downgrade step must
   show its reason while staying runnable, and an unknown must show the probe the planner prepended.
   Rendering the plan without the reasons wastes increment 2 entirely.
5. Run the plan through the existing execution path.

**Do not** build a new execution engine, a new gating vocabulary, or a new plan model — all three exist.
This increment is presentation.

Mount via `viewer.window.add_dock_widget`, and use the **tabify+raise** behaviour shipped in 1.6.297 so
the dock is visible rather than squeezed.

### Tests (Qt-smoke + `core` for the drive logic)
- Feeding answers to the engine reaches a leaf and produces a compiled plan (headless — no dock needed).
- A plan containing a quality-blocked step renders the blocking reason; a downgraded step renders its
  reason and remains runnable; an unknown renders its probe.
- The plan is editable before running (a step can be removed/changed).
- The dock mounts and raises (tabify behaviour, per the 1.6.297 contract).
- Existing navigator tests pass unmodified.

---

## Increment 4 — beginner mode + feature surfacing (ship after 3)

**Scope: make the navigator the default first experience, and surface the invisible features.**

1. **Wire `app_mode` into the UI.** `current_mode()` defaults to BEGINNER on first run (persisted via
   `user_settings`), and the mode change signal reconfigures panels at runtime — no restart. A visible,
   prominent toggle ("Guided / Full"); an advanced user flips it once and is never re-greeted.
2. **Register `FeatureCard`s for every capability that currently has no UI entry point.** Verified
   candidates: biological QC, measurement stability, the measurement ontology / Feature Explorer,
   feature provenance, analysis presets, scan QC, control validation, reliability (MRI), QC gallery,
   figure refinement, unmixing, SMLM, kymographs, ratiometric. Each card: title, one-sentence summary a
   scientist understands, category, entry callable, docs anchor.
3. **The beginner home dock** — navigator question-flow (increment 3) + capability cards grouped by
   category + the mode toggle. Default surface when `is_beginner()`.
4. **Advanced mode restores today's view** — menus first, navigator available as a card/menu entry.

### The rule that keeps this honest
**Guide, don't cage.** Beginner mode foregrounds; it never hides. The full methods menu is one toggle
away, generated plans stay editable, and every card opens the *real* feature — no mock UI, no
placeholder panels.

### Tests
- First run (no stored mode) surfaces the beginner home; a stored `advanced` does not.
- The mode persists across sessions and switches at runtime without restart.
- Every currently-unsurfaced capability has a registered card — **enumerate the expected set and assert
  presence**, so a future feature that forgets to register is caught.
- A card's `entry` opens the real feature.
- `min_mode` gating: advanced-only cards are hidden in beginner view.
- Advanced mode leaves the existing menu experience unchanged.

---

## Steps
1. **Increment 3**: navigator dock driving `next_question` → `compile` → editable plan with gate reasons
   inline; mount with tabify+raise. Ship.
2. **Increment 4**: `app_mode` wired to the UI with a persisted BEGINNER default and a visible toggle;
   `FeatureCard`s registered for the unsurfaced capabilities; beginner home dock. Ship.
3. Full `pytest -m core` green after each.
4. Each increment is its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- A user can ask a scientific question and receive a runnable, editable, quality-gated plan from the GUI.
- Blocked and downgraded steps state their reasons where the user reads the plan.
- First run defaults to a guided home; the choice persists; advanced mode restores today's view.
- Every capability that previously had no UI entry point is discoverable as a card that opens the real
  feature.
- Full `pytest -m core` green.

## Cautions
- **Increment 3 ships alone.** The whole reason this arc stalled for ~50 versions is that it was specced
  as one large integration; keep the pieces separately shippable.
- **Render the gate reasons.** A plan without them throws away increment 2 and reduces the navigator to
  a step generator.
- **Reuse, don't rebuild** — `next_question`, `compile`, `gate_report`, `app_mode`, `feature_registry`,
  `user_settings`, and the tabify mount all exist. This is wiring.
- **No mock UI.** A card must open the real feature; a placeholder is worse than an absent card.
- **Guide, don't cage** — beginner mode must never hide capability, and generated plans stay editable.
- The card-presence test is what stops the invisible-feature problem recurring; don't skip it.
