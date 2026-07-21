# Claude Code spec — Fix status-marker (logic-gate) bugs in the Fluorescence cellular pipeline

> **✅ STATUS — DONE, shipped in 1.6.222.** All four fixes landed in the SHARED mechanisms, so they correct
> the class across every pipeline (the fluorescence one was the tester's acceptance target). **Fix 3
> (systemic ready≠done):** the colour decision moved to a new Qt-free `utils/marker_logic.py::resolve_marker`
> (precedence done→ready→resting; `filled=False` only for ready); `button_with_circle` now renders readiness
> as an **outlined amber ring**, never solid green — green means the step RAN. **Fix 1 (measure-line):**
> `button_with_circle` gained `complete_on_click=False` + a `mark_done()` hook; the Draw→Measure→Clear
> cycling button now marks done only from the Measure phase, success-gated on `_diameter_measured` (was
> greening on the Draw click). **Fix 4 (select labels):** `_layer_row`'s `else` branch greened only a
> hint-matching or user-picked layer and left an auto-defaulted valid layer **red** — a valid selection is
> now green (hint is a suggestion, not a requirement); fixes all four selectors, which share `_layer_row`.
> **Fix 2 (optional colour):** chose **Option A** — blue = "optional step completed" is kept (it carries
> real information) but its meaning is now explicit in the tooltip + the `field_status`/`marker_logic`
> docstrings, applied uniformly to every optional action. Success-gating beyond the click is done for
> measure-line; for other run buttons the ready≠done appearance is the fix and full success-gating (marking
> done from each handler's success rather than its click) is noted as a follow-on. Tests:
> `tests/test_marker_logic.py` (`core`, Qt-free) + `tests/test_status_markers.py` (`integration`); full
> `pytest -m core` green. No measurement/output changes.

**Date:** 2026-07-21 · **Target tree:** 1.6.221 · Verified against the 1.6.221 tree. A tester reviewed
the **Cellular object analysis (Fluorescence)** pipeline's status circles ("logic gates") and reported
four concrete inconsistencies. These are real UX-correctness bugs in `field_status.py`'s
`button_with_circle` / `label_with_circle` and their wiring — a status marker that lies about readiness
undermines the whole anti-black-box, "the software tells you where you are" philosophy. All four are
verified against the mechanism.

## The tester's findings (verbatim intent)
1. **Measure-line gate fires too early.** "Turns green as soon as the user presses **Draw lines**. It
   should turn green after the user hits **Measure line**." The completion marker is wired to the wrong
   button.
2. **Upscaling turns BLUE, not green — terminology inconsistency.** "Turns blue as soon as the user
   presses **Run upscaling**. Shouldn't it also turn green just to be consistent?" The optional-step
   blue vs required-step green distinction is confusing the user.
3. **"Green before you pressed it."** "Logic gate near **Run segmentation / Run condensate segmentation
   / Run cell analyser / Run condensate analyser** turns green **before** the user has pressed that
   button." Repeated across four run-buttons — a systemic issue, not a one-off.
4. **Label doesn't turn green on a valid selection.** "Red label near **Select Image layer / Select
   Fluorescence Image to process / Select Image for cell analysis / Select image for puncta
   measurement** does **not** turn green after selecting a suitable image layer." Also repeated across
   four selectors — systemic.

## Root cause (verified in `field_status.py`)
`button_with_circle` has TWO green triggers that the tester is (correctly) conflating:
- **readiness green** — if `watch_dropdowns` are satisfied, the circle goes green ("Ready to run") even
  though the action has NOT run;
- **completion green** — after `button.clicked`, `state['done']=True` → green ("Done — this step has
  been run").

Both render identical green. So finding #3 ("green before pressed") is the **readiness-green being
misread as completion-green** — the marker turned green because the dropdowns were ready, not because
the step ran. And finding #4 is the mirror: the **label** circle near a selector is not turning green on
a valid selection, because either it isn't watching the right dropdown or the readiness check isn't
wired to that label. The two findings are the same underlying issue — readiness state is applied
inconsistently: appearing where it shouldn't (run buttons) and missing where it should (select labels).

## The fixes

### Fix 1 — measure-line gate to the correct button
The measure-line status (`ui_modules.py` `_measure_line_status`, ~line 1260/1319) marks done on **Draw
lines**; it must mark done on **Measure line**. Re-wire `_mark_done` (or the equivalent completion
signal) to the Measure-line button's `clicked`, not Draw-lines. Draw-lines may set an intermediate
state, but *completion* is Measure-line.

### Fix 2 — resolve the blue-vs-green terminology (decide once, apply consistently)
The blue = "optional step done", green = "required step done" scheme is intentional but the tester finds
it inconsistent for upscaling. Two honest options — **pick one and apply it uniformly**, do not leave it
ad hoc:
- **Option A (recommended):** keep blue for optional-done (it carries real information — "you ran an
  optional step"), but make the distinction legible: a tooltip and/or a tiny legend so blue reads as
  "optional, done" not "different kind of incomplete". Upscaling is genuinely optional, so blue is
  *correct*; the fix is making that meaning obvious.
- **Option B:** if the team decides the optional/required colour split is more confusing than useful,
  use green for any completed step and drop blue — simpler, at the cost of losing the optional signal.
Whichever is chosen, apply it to **every** optional step, not just upscaling, so the terminology is
uniform. Document the chosen convention in the field_status docstring.

### Fix 3 — separate "ready" from "done" so green means DONE (the systemic one)
Green must mean **the step has run**, not "the step could run". The readiness state needs a distinct
appearance so it can never be misread as completion:
- **Readiness** (dropdowns satisfied, not yet run) → a distinct look: e.g. **amber/outlined** or a
  "ready" tooltip, NOT the same solid green as done. The tester's whole confusion is that ready and done
  are the same colour.
- **Completion** (button actually clicked and its handler succeeded) → green.
- Apply across all four run-buttons the tester listed. After this, a run-button circle is only green
  once the run has happened — matching the user's mental model.
- **Consider gating completion on success, not just click:** `button.clicked` fires even if the handler
  errors. Where feasible, mark done from a success signal rather than the raw click, so green means "ran
  successfully," not "was clicked." (If that's a larger change, at minimum fix the ready-vs-done
  appearance now and note the success-gating as a follow-on.)

### Fix 4 — select-image labels turn green on a valid layer
The `label_with_circle` (or the label near each selector) must turn green when a **suitable image layer
is selected**, across all four selectors the tester named. Verified `label_with_circle` exists and is
meant to "turn green once a real layer is selected" — so the bug is that these four selectors either
aren't using it, aren't watching the right dropdown, or their validity check rejects a valid selection.
- Wire each selector's label circle to its dropdown's change signal with the same non-placeholder
  validity check `button_with_circle` uses (`_ok`: non-empty, not starting with select/none/--/choose).
- On a valid layer → green; on placeholder → red (required) or yellow (optional).
- Verify the four fluorescence-pipeline selectors specifically (Select Image layer / Select Fluorescence
  Image to process / Select Image for cell analysis / Select image for puncta measurement).

## Scope
- These are the **Fluorescence cellular object analysis** pipeline's markers, but Fix 3 (ready≠done) and
  Fix 4 (label-on-select) are in the shared `field_status.py` mechanism — so fixing them **correctly
  fixes the same class everywhere**. Sweep the other pipelines for the same pattern after, but the
  fluorescence pipeline is the verification target the tester used.
- **UX-correctness, not science** — no measurement changes. But it matters: a lying status marker erodes
  trust in exactly the anti-black-box property PyCAT sells.

## Tests (Qt-smoke + `core` where logic is separable)
- Measure-line: the status is NOT done after Draw-lines; IS done after Measure-line.
- Ready vs done: a run-button circle with satisfied dropdowns shows the READY appearance (distinct from
  green) before click; GREEN only after click (/success).
- Select labels: each of the four selectors' labels goes green on a valid layer selection and reverts on
  placeholder.
- Optional terminology: the chosen convention (A or B) is applied to every optional step, with the
  meaning exposed (tooltip/legend).
- `reset()` still reverts every marker to its initial red/yellow (per-step and whole-workflow Clear).
- A deliberately-erroring run handler does NOT leave the circle green (if success-gating is implemented).

## Steps
1. Re-wire the measure-line completion to the Measure-line button (Fix 1).
2. Introduce a distinct READY appearance in `button_with_circle`, separate from done-green; apply to the
   four run-buttons (Fix 3).
3. Wire the four fluorescence selectors' labels to turn green on valid selection (Fix 4).
4. Decide + uniformly apply the optional-step colour convention; document it (Fix 2).
5. Qt-smoke + core tests above.
6. Sweep other pipelines for the ready-vs-done and label-on-select patterns; note/fix.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (status-marker logic: green now
   means done not ready; select-labels turn green on valid layer; measure-line gate corrected; optional
   colour convention unified).

## Definition of done
- Green = the step has run (not "ready to run"); readiness has a distinct, non-green appearance.
- The four run-button circles are green only after the run; the four select-labels turn green on a valid
  layer.
- The measure-line circle completes on Measure-line, not Draw-lines.
- The optional-step colour convention is decided, uniform, and its meaning is exposed.
- `reset()` behaviour intact; no measurement/output changes.
- Full `pytest -m core` green.

## Cautions
- **Green must mean DONE.** The core bug is ready-green masquerading as done-green; if readiness stays
  green, the fix failed. Give readiness its own look.
- **Fix the shared mechanism, verify on the fluorescence pipeline** — Fixes 3 & 4 live in
  `field_status.py`, so they correct the class; but the tester's exact buttons/labels are the
  acceptance test.
- **Prefer success-gating for completion** where feasible — a circle should not go green because a
  button was clicked into an error.
- **Pick ONE optional-colour convention and apply it everywhere** — the inconsistency is the complaint;
  a half-applied fix reintroduces it.
- No science changes — but treat this as correctness, not polish: the status markers are the
  anti-black-box promise made visible.
