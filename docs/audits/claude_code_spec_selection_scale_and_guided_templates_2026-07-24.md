# Claude Code spec — Selection overlay ignores layer scale; guided-panel execution and saveable templates

> **◐ STATUS — Part 1 (the scale bug) DONE, shipped 1.6.329. Parts 2 (guided execution) + 3 (templates)
> remain; Part 4 is a design note (no build).**
> **Part 1 — DONE.** `selection_overlay.show_selection` resolves the object's source layer by
> `pycat_layer_id` and draws the bbox + centre with that layer's `scale`/`translate` (reconciled to the
> viewer ndim); `object_ref.resolve_in_viewer` centres the camera in WORLD coordinates (`pixel × scale +
> translate`). A recorded-but-not-open source layer is an honest miss (nothing drawn, reported). A
> mismatched-**pixel-grid** image is hidden under the highlight and restored on clear — image-only, never the
> target, never a user-hidden layer (attributed via `_HIDDEN_BY_PYCAT`), never all (leaves ≥1 visible image).
> `tests/test_selection_overlay_scale.py` (`base`, 12, duck-typed viewer — napari-free); existing
> brushing/selection tests unmodified. Scale-1.0 is a regression test.

**Date:** 2026-07-24 · **Target tree:** 1.6.324+ · Verified against the tree and the reported GUI
session. Three items: one real correctness bug (brushing misplacement), one deliberate gap to close
(guided execution), one new capability (saveable method templates). Plus a note on an interaction
suggestion.

---

## Part 1 — The selection overlay is drawn without the source layer's scale *(the bug)*

**Reported:** the cellular-object fluorescence plot *"isn't brushing properly with the image — it
doesn't highlight the cell or zoom to it"*, with a suspicion of upscaled-vs-non-upscaled coordinates.

**Verified — the suspicion is correct, and the cause is more general than upscaling.**

`selection_overlay._rect_for` documents its output as *"the bbox as a rectangle, **in the layer's
coordinate space**"* (line 44). `_replace_layer` accepts a `scale` parameter and applies it (lines
75, 98-99). But `show_selection` calls it **without one**:

```python
_replace_layer(viewer, BBOX_LAYER, viewer.add_shapes, rects)      # no scale
_replace_layer(viewer, CENTRE_LAYER, viewer.add_points, ...)      # no scale
```

So the overlay layer is created at **scale 1.0** while its coordinates are in the **source layer's pixel
space**. Whenever the source layer's scale ≠ 1, the box lands in the wrong place.

This bites in two common situations, and PyCAT has both:
1. **Calibrated layers.** PyCAT sets `layer.scale = pixel_size`, so a calibrated image is already at a
   non-unit scale — the box is offset by the calibration factor even with no upscaling involved.
2. **Upscaled layers.** An upscaled image and its source differ by the upscale factor, so a bbox
   measured on one is wrong on the other.

### The fix
1. **Resolve the object's source layer** and draw the overlay with **that layer's `scale`** (and
   `translate`, if set). The entity/tag machinery already records which layer an object came from — use
   it rather than assuming the active layer.
2. **Pass the scale through** to both `_replace_layer` calls; the parameter already exists.
3. **Zoom/centre in world coordinates.** The camera works in world space, so the centre must be the
   layer coordinate multiplied by that layer's scale. This is why "doesn't zoom to it" and "doesn't
   highlight it" appear together — the same missing transform.

### The upscaling decision (as directed)
**When an upscaled layer is present, highlight in the upscaled space.** Rationale: the upscaled layer is
what the user is looking at and what the measurement was computed on. So the rule is: **resolve to the
layer the object was measured on**, and draw in that layer's coordinate space — which for an upscaled
workflow is the upscaled layer.

Where the object's source layer is no longer present, **say so** rather than drawing an unverified box:
a missing-source selection should report "cannot locate this object in the current layers" (the entity
registry already models honest resolution failure).

### Hide dimension-mismatched image layers during a selection
Once the overlay resolves to a target layer, **check the other visible image layers for a dimension
mismatch** — a different pixel-grid shape from the target (e.g. an upscaled layer at 2048×2048 alongside
its 1024×1024 source). When one is found, **hide the mismatched layer** for the duration of the
selection so the canvas is not showing two images at different resolutions under the highlight.

Rules:
- **Compare the pixel-grid shape**, not the world extent. Two layers with correct scales occupy the same
  world extent but different grids; it is the grid mismatch that produces the confusing render.
- **Hide only image layers**, and only ones that mismatch the target. Labels, points, shapes, tracks and
  the overlay layers themselves are untouched — as are image layers whose dimensions match.
- **Restore visibility when the selection is cleared or moves to a layer of different dimensions.** The
  user's layer visibility is their arrangement; borrowing it temporarily is acceptable, keeping it is
  not.
- **Remember what was hidden by PyCAT**, and restore only those. A layer the user had already hidden
  stays hidden — do not turn it back on.
- **Never hide the target layer**, and never hide everything: if the check would leave no visible image,
  hide nothing and leave the canvas as it was.

### Tests
- An object on a layer with `scale=(0.0977, 0.0977)` draws its bbox at the **world** position matching
  the object, not at the raw pixel index.
- An object measured on an upscaled layer highlights on the upscaled layer, correctly positioned.
- The camera centres on the object in world coordinates (assert `camera.center` against the expected
  world position).
- Scale 1.0 behaviour is unchanged (regression).
- A selection whose source layer is absent reports the failure rather than drawing a box.
- Multi-ref (aggregate) selections all land correctly.
- With an upscaled layer and its differently-shaped source both visible, selecting an object on the
  upscaled layer hides the mismatched source; clearing the selection restores it.
- A layer the user had already hidden is **not** turned back on by the restore.
- Image layers whose dimensions match the target stay visible; non-image layers are never hidden.
- If hiding would leave no visible image layer, nothing is hidden.

---

## Part 2 — Wire execution from the guided panel

**Not a bug** — the panel states it plainly: *"This plan is ready. Running it from the guided panel is
coming — for now, run each step from its method panel."* The plan compiles correctly (every step green
or amber with reasons shown). Execution simply is not wired.

That message is honest, but a permanently-disabled primary button trains users to distrust the panel,
and it was read as a failure in this session.

### The change
Wire **Run analysis** to execute the compiled plan through the existing execution path — the same one
each method panel uses — step by step, respecting the quality gates already computed:
- **blocked** steps do not run; the plan stops with the stated reason.
- **amber/caveat** steps run, with the caveat recorded in the result.
- **probe** steps run first, as the planner ordered them.
- Progress and cancellation route through the canonical operation runner (off the Qt thread).
- Each step's output lands as it would from its own panel — same layers, same tables, same tags — so
  the guided route and the manual route are **the same computation** (this is what
  `test_route_equivalence` exists to assert; add guided as a route if practical).

**Until it is wired**, keep the explanatory sentence but make the button's state unambiguous — a
disabled control with "coming soon" beside it reads better as a labelled placeholder than as a primary
action that failed.

---

## Part 3 — Save the answered plan as a reusable method template

**Requested:** *"what I want is a widget to be constructed off of the answers and for that to be savable
as a template method if the user likes it — they will probably want to revisit it for other analysis."*

This is the natural payoff of the navigator: the questionnaire produces a plan, and a plan the user likes
should be reusable without re-answering.

### Design
1. **Save**: serialise the compiled plan — the `AnalysisIntent` (the answers), the ordered steps with
   their operations, and any parameters the user adjusted — under a user-supplied name.
2. **Reuse**: a saved template can be applied to a **new dataset**, re-running the quality gates against
   the new data (the answers carry over; the gate verdicts must not). A template that was runnable on one
   dataset may be blocked on another, and that must be re-evaluated rather than assumed.
3. **Storage**: this is a user-level artefact that should survive sessions and apply across datasets —
   use the general `user_settings` service (or the analysis-presets mechanism, which already models
   "named, reusable workflow configuration"). **Prefer extending `analysis_presets`** over inventing a
   second concept; a saved guided plan *is* a preset with provenance about how it was authored.
4. **Show its origin.** A template records the answers that produced it, so a user revisiting it can see
   *why* the steps are there — and edit the answers to regenerate rather than hand-editing steps.

### Tests
- A compiled plan saves under a name and reloads with the same steps and answers.
- Applying a template to a different dataset re-runs the gates (a template blocked on the new data
  reports it; verdicts are not carried over).
- Templates persist across sessions.
- A template records the answers that generated it.
- Deleting/renaming works; a corrupt template file degrades to "not available", never a crash.

---

## Part 4 — "All of the above" as an answer option *(design note)*

**Suggested:** *"in some cases 'all of the above' might be a good option."*

Reasonable, but it needs care: the question engine's branches lead to different operation sets, and
"all" may compile to a plan whose steps conflict (two segmentation routes for the same objects) or that
is far longer than the user expects.

**Recommendation:** treat this as a **per-question property**, not a global one. Add `allows_multi` to
the question spec and enable it only where the branches genuinely compose — e.g. "which measurements do
you want?" (composes) versus "how should objects be segmented?" (does not). Where multi-select is
enabled, the planner must **merge** the resulting operation sets and de-duplicate shared prerequisites.

**Do not add a blanket "all of the above"** — a plan that silently includes mutually exclusive routes is
worse than asking one more question. Flagged here for a decision rather than specced for build.

---

## Steps
1. **Part 1** (bug): resolve source layer → pass its scale/translate to the overlay → centre in world
   coordinates. Ship first; it is a correctness defect users are hitting.
2. **Part 2**: wire Run analysis through the existing execution path with gate semantics preserved.
3. **Part 3**: save/apply guided plans as templates, extending `analysis_presets`.
4. **Part 4**: decide per-question multi-select; spec separately if adopted.
5. Full `pytest -m core` green after each.

## Definition of done
- Brushing highlights and zooms to the correct location on calibrated and upscaled layers; a missing
  source layer reports honestly.
- Run analysis executes the compiled plan through the same path as the method panels, respecting gates.
- A guided plan can be saved, named, reused on new data with gates re-evaluated, and shows the answers
  that produced it.
- Full `pytest -m core` green.

## Cautions
- **Scale, not just upscaling.** A calibrated layer already has non-unit scale — the bug is present
  without any upscaling. Fix the general case.
- **Resolve to the layer the object was measured on**, do not assume the active layer.
- **Never draw an unverified box.** A missing source layer reports a failure; a box in the wrong place is
  worse than no box.
- **Visibility changes must be temporary and attributable.** Track what PyCAT hid and restore exactly
  that; a user finding their layers silently switched off is its own bug.
- **Compare pixel-grid shape, not world extent** — correctly-scaled layers share a world extent while
  still rendering confusingly at different grids.
- **Guided and manual must be the same computation** — if they diverge, one of them is wrong. Assert it.
- **Re-evaluate gates when a template is applied to new data.** Carrying over a verdict would assert
  quality that was never checked on this dataset.
- **No blanket "all of the above"** — enable multi-select only where branches genuinely compose.
