# Claude Code spec — Navigator UX: first-user feedback

> **✅ STATUS — DONE (items 1–6). Item 1 @1.6.317; items 2/4/5/6 @1.6.318; items 3 + 3b @1.6.319.**
> **Item 3 + 3b (1.6.319).** Qt-free `session.data_observations(cm)` → evidence-backed observations from the
> loaded metadata (only where supported; a time-series/Z line needs the interval/step — no guessing from a
> plane count); the navigator renders a 'What we can tell from your data' section, marked read-from-file with
> the user's answers taking precedence (they already outrank metadata in `context_from_session`). Item 3b: the
> mode toggle, capability cards, and plan steps all carry explanatory text, guarded by
> `test_every_interactive_element_carries_explanatory_text`. `base` observations test + `integration` (section
> renders; the missing-layer guard). **Deliberately deferred:** a per-answer interactive OVERRIDE toggle inside
> the observations section (the answers are already overridable via the questions; a per-line toggle is a
> refinement, not the visibility the report asked for).
> **Items 2/4/5/6 (1.6.318).** Item 2: `plan_rows(plan, ctx)` prepends a visible 'Load data' step 0 (blocked
> until an image is open, satisfied once it is; the questionnaire is never gated — `plan_rows(plan)` without a
> ctx is unchanged). Item 4: a step-colour legend + per-step tooltips (`_STATE_MEANING`), reusing the existing
> status vocabulary. Item 5: the mode toggle labels the OUTCOME with a tooltip + subtitle (behaviour
> unchanged). Item 6: the Home dock splits Guided | Explore capabilities into tabs (`build_home_widget`
> refactored to a module-level `_render_home` to stay under the complexity ceiling). `base` step-0 test +
> `integration` tabs smoke.
> **Item 1 (1.6.317).** `planner.regate(plan, ctx)` re-evaluates a COMPILED plan's gaps/gates against a fresh
> context WITHOUT recompiling (recompiling could re-select modules and change the plan under the user).
> `NavigatorSession` retains its plan and gained `regate()` + `run_blocked_reason()` (never a dead control:
> 'Load an image first' / 'Set the pixel size' / the first blocker, or None when runnable).
> `session.context_from_session(cm)` refreshes the context from the loaded image's metadata — a user answer
> outranks it, and dimensionality is NEVER guessed from a bare plane count (axes only from an explicit
> `dimension_order`). `navigator_dock` now takes `central_manager`, subscribes to layer insert/remove +
> `register_data_switch_callback` (calibration), debounces, re-gates + re-renders (run-once-on-mount too), and
> shows the blocking reason inline. `tests/navigator/test_navigator_reevaluate.py` (`base`, 6) +
> `tests/test_navigator_dock.py` (`integration`, +1 re-gate smoke). Wired from `central_manager` + the home dock.

**Date:** 2026-07-23 · **Target tree:** post-1.6.297 (navigator increments 3–4 shipped) · Based on the
first real user session with the guided navigator, plus verification of what the metadata layer already
provides. Six items: one likely bug, four presentation problems, one genuinely new capability.

---

## 1. The plan never re-evaluates — the run action can never become enabled *(the bug)*

**Reported:** *"run analysis did not light up"* — and, after loading data and after restarting,
**nothing in the panel changed at all.** The panel is inert.

This is not a correctly-disabled button missing an explanation. The plan is evaluated **once, at compile
time**, and nothing recomputes when the viewer state changes. Loading an image, adding a layer, or
setting a pixel size never reaches the plan, so the run action **cannot become enabled by doing the very
thing it is waiting for**. Guided mode is non-functional as shipped: the user is (silently) waiting on
data, loads it, and nothing happens.

### Fix — re-evaluate on state change
- Subscribe the plan panel to the events that change the answers: **layer inserted/removed**, active
  layer changed, and calibration/pixel-size changes.
- On any of those, **re-run `evaluate_quality` for the plan's steps and re-render** — step states,
  step-0 satisfaction, and the run action's enablement all recompute from current state.
- Re-evaluation must be **cheap and debounced** — it fires on layer events, so it must not recompile the
  whole plan or re-run analysis. Re-gate the existing compiled plan against current context; do not
  recompute the plan structure unless the user changes an answer.
- The same path must run **once on mount**, so a panel opened after data is already loaded starts in the
  correct state rather than requiring an event to arrive.

### Fix — say why, always
Whenever the run action is disabled, the panel states the blocking reason in user language next to the
button — *"Load an image first"*, *"ΔG needs a calibrated pixel size — set the scale"*. Increment 2 put
these verdicts in `gate_report`; increment 3 was specified to surface them inline and did not.

**Both halves are required.** A reason without re-evaluation goes stale the moment the user acts on it
(it would still read "load an image first" with an image open). Re-evaluation without a reason leaves
the user guessing at what changed. Test them together.

## 2. "Load data" must be step 0 — present but non-blocking

**Reported:** *"the first step should be to load data"* — and it *"shouldn't block moving through it."*

The navigator currently plans the analysis and assumes data exists, which is why an empty session
produces a plan whose first real step can never run.

**Fix:** the compiled plan begins with an explicit **step 0: load data**, shown like any other step and
marked satisfied once an image is open.

**Non-blocking is the requirement:** a user must be able to answer every question, see the full proposed
plan, and inspect the steps **before** loading anything. Step 0 is a visible prerequisite, not a gate on
the questionnaire. Only *running* requires it.

---

## 3. Answer what the data already tells us *(new capability, mostly wiring)*

**Reported:** *"some questions like 'is this a timeseries' can be answered through metadata… suggestions
based on multichannel, time, z… revealed through parsing the data at load… these could appear as a
smaller section below the guide."*

**Verified: the metadata layer already emits every fact needed.** `metadata_extract` produces
`n_frames`, `n_timepoints`, `n_channels`, `n_z`, `frame_interval_s`, `z_step_um`, `dimension_order`,
`dwell_time_s`, `channels`. Nothing new must be measured — this is connecting existing extraction to the
question engine.

### Design
A **"What we can tell from your data"** section below the guided questions, populated at load:
- *"3 channels · 1 Z · 200 timepoints · 0.5 s frame interval"* — the observed shape, plainly.
- **Pre-answer the questions it settles.** A dataset with `n_timepoints > 1` and a real
  `frame_interval_s` answers "is this a time series?". `n_z > 1` with a `z_step_um` answers "is this a
  Z-stack?". `n_channels > 1` answers the multichannel branch.
- **Show pre-answers as suggestions the user can override**, not silent decisions. Mark them clearly as
  derived from metadata, with the evidence visible (*"time series — 200 timepoints, 0.5 s interval"*),
  and let the user change any of them. This follows the existing confidence discipline: metadata is
  strong evidence, not authority, and a user answer outranks it.
- **Absent metadata means no suggestion** — never guess dimensionality from array shape alone when the
  file did not say. A 3-plane stack could be Z, T, or channels; `dimension_order` disambiguates when
  present, and when it is absent the honest move is to ask.

### Payoff
This shortens the questionnaire for the common case (a user with well-described data answers fewer
questions), and it makes the navigator visibly *aware of the loaded data* rather than a generic
decision tree — which is the point of a guided mode.

---

## 3b. Nothing in the panel has a tooltip *(one missing layer, not four omissions)*

**Reported:** *"none of it has tooltips."* The unexplained colours, the unexplained mode toggle, and the
unexplained disabled button are not separate oversights — the panel shipped **without an explanatory
layer at all**.

**Blanket requirement:** every non-obvious control in the navigator panel explains itself — a tooltip, a
subtitle, or inline text. This covers the step colours (item 4), the Guided/Full toggle (item 5), the
run action's blocking reason (item 1), each proposed step (what it does and why it is in the plan), and
each capability card. A test asserts the set of interactive elements all carry explanatory text, so the
layer cannot go missing again wholesale.

---

## 4. The blue/green step colouring is unexplained

**Reported:** *"I don't understand what the blue and green sections are about."* The screenshot shows
`data_qc.assess` in blue and `acquisition` in green with no legend.

**Fix:** whatever the two states mean (ready-to-run vs already-satisfied, or step vs prerequisite),
**say so**. A one-line legend at the top of the plan, plus a tooltip per step. If the colours encode
quality-gate verdicts, reuse the vocabulary the rest of the app already uses rather than a third scheme
— and check the choice against the marker-semantics work, which deliberately assigned meanings to red/
green/amber. A fourth colour language in the same window is a cost.

If the colouring is *incidental* (styling, not semantics), remove it — colour that looks meaningful but
is not is worse than plain text.

---

## 5. The Guided/Full toggle is unexplained

**Reported:** *"the toggle between guided and full is weird and I don't understand what it's supposed to
do."*

`app_mode` is doing the right thing; the affordance does not communicate it.

**Fix:** label the outcome, not the mode. A tooltip and a one-line subtitle stating what changes —
*"Guided: answer questions and PyCAT proposes a workflow. Full: all analysis methods, no guidance."*
Consider naming the states after what the user gets rather than an abstract mode.

The underlying behaviour (BEGINNER default, persisted, runtime-switchable) is correct and should not
change — this is labelling only.

---

## 6. Move "Explore capabilities" to its own tab

**Reported:** *"The Explore is great. but it could be a separate tab called explore capabilities."*

The feature registry is working — it is the first time the QC dashboard, control validation, unmixing
and comparative figures have been discoverable at all. But it is competing for vertical space with the
guided questionnaire, and the two serve different intents: *"help me decide what to do"* versus
*"show me what PyCAT can do."*

**Fix:** a separate tab, **Explore capabilities**, alongside the guided tab. Same registry, same cards,
its own space. This also lets the capability list grow (the registry is designed for exactly that)
without squeezing the questionnaire.

---

## Tests
- **Loading an image re-enables the run action** without a restart (the reported bug — this is the
  primary regression test).
- The gate re-evaluates on layer insert/remove and on a calibration change; the plan re-renders and
  step-0 flips to satisfied.
- Re-evaluation runs once on mount, so a panel opened with data already loaded is correct immediately.
- Re-evaluation does not recompile the plan structure or re-run analysis (assert the compile path is not
  invoked on a layer event).
- The run action, when disabled, exposes a non-empty reason string, and that reason **updates** when the
  blocking condition clears (assert both together — a stale reason is its own bug).
- Every interactive element in the panel carries explanatory text (the missing-layer guard).
- A compiled plan starts with a load-data step; it is marked satisfied when an image is open.
- The questionnaire is fully navigable with **no** data loaded (non-blocking test).
- Metadata pre-answers: a fixture with `n_timepoints>1` + `frame_interval_s` pre-answers the time-series
  question; `n_z>1` + `z_step_um` pre-answers Z-stack; `n_channels>1` pre-answers multichannel.
- A pre-answer is overridable, and the user's override wins.
- **Absent metadata produces no pre-answer** (the no-guessing test) — including an ambiguous 3-plane
  stack with no `dimension_order`.
- Step colours have a rendered legend, or no semantic colour at all.
- The mode toggle exposes explanatory text.
- Explore capabilities renders in its own tab; the guided tab is unaffected.

## Steps
1. Re-evaluate the plan on viewer state change + surface the blocking reason (the bug — ship first).
2. Add load-data as step 0, non-blocking for the questionnaire.
3. "What we can tell from your data" section + metadata pre-answers as overridable suggestions.
4. Legend/tooltips for the step colouring — or remove the colouring.
5. Explanatory text for the Guided/Full toggle.
6. Move Explore capabilities into its own tab.
7. Tests above; full `pytest -m core` green.
8. Ship in that order — 1 and 2 are correctness/comprehension, 3 is capability, 4–6 are presentation.

## Definition of done
- Loading data enables the run action without a restart; the plan tracks viewer state.
- No dead controls: a disabled run action always states why, and the reason updates when it clears.
- Every non-obvious control in the panel explains itself.
- The plan includes loading as a visible step 0, and the questionnaire works before any data is loaded.
- Loaded data pre-answers the questions it can, shown as overridable, evidence-backed suggestions, with
  no guessing when metadata is absent.
- Colour semantics are explained or removed; the mode toggle explains itself.
- Explore capabilities has its own tab.
- Full `pytest -m core` green.

## Cautions
- **Metadata suggests; the user decides.** Pre-answers must be visible, evidenced, and overridable —
  a silent auto-answer would be the black-box behaviour the project exists to avoid.
- **Never infer dimensionality from array shape alone.** Without `dimension_order`, a 3-plane stack is
  ambiguous; ask rather than guess.
- **Step 0 must not gate the questionnaire.** The user explicitly wants to walk the questions before
  loading.
- **Do not add a fourth colour language.** Reuse the existing status vocabulary or drop the colouring;
  the marker-semantics work already assigned meanings that a new scheme would collide with.
- Behaviour of `app_mode` is correct — item 5 is labelling only, not a behavioural change.
- **Re-gate, don't recompile.** Re-evaluation fires on layer events; recompiling the plan or re-running
  analysis on every layer change would be far worse than the bug being fixed.
- **A reason that does not update is a new bug.** Ship re-evaluation and the reason text together.
