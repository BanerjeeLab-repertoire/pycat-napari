# Claude Code spec — Evidence-based confidence, metadata validity filtering, and contradiction surfacing that doesn't numb the user

> **◐ STATUS — Parts 1 & 2 DONE; Part 3 (contradiction surfacing) + Part 4 (anti-numbing) remain.**
> **Part 2** (the `is_meaningful` validity filter) shipped 1.6.290: `utils/metadata_validity.py`
> (empty/placeholder/non-finite + field-aware pixel_size=1.0 / gain·magnification·NA=0 sentinels, never
> blanket-rejecting the number 1), applied at the metadata write guard; `tests/test_metadata_validity.py`
> (28 core tests).
> **Part 1** (evidence-graded confidence) shipped 1.6.292: `channel_modality.classify_channel_from_pixels`
> floors a binary call at chance — decisive → band [0.70, 0.95] (`_binary_confidence`), at/below chance or a
> tie → modality `None`; the 3-way sub-type may sit between its 1/3 chance and ~0.90. `navigator/tags.py`
> gained `confidence_for(source, evidence)` grading WITHIN metadata (declarative 0.99 / derived 0.90 / weak
> 0.70; flat 0.8 fallback for unstated), a `TagSet.evidence` field, and the documented scale in-code;
> `user`/`pipeline`/`derived` unchanged. `tests/test_tag_confidence.py` (9 core tests).
> **Part 3 + Part 4** (contradiction detection + severity, the metadata-button indicator, and the
> per-pattern "expected for this instrument" anti-numbing store) remain — largely interactive/Qt-bound.


**Date:** 2026-07-21 · **Target tree:** 1.6.269 · Verified against the 1.6.269 tree. Three joined
problems in the tag/metadata layer: confidence numbers that carry **no information**, **no filter** on
generic-but-invalid metadata values, and **no visible signal** when metadata contradicts itself or the
pixels. The hard requirement threaded through all of it: the warning must stay **visible without
obstructing**, and must **not train the user to ignore it**.

---

## Part 1 — Confidence is a coin flip because it scores the SOURCE, not the EVIDENCE

### Verified
`channel_modality.classify_channel_from_pixels` returns `min(1.0, fluor_score)` where `fluor_score` is
a sum of three heuristic bumps (0.4 / 0.3 / 0.3). The fluorescence-vs-transmitted call is **binary**,
so a correct answer scores 0.5–0.7 — but **a coin flip on a binary question is already 0.5**. Those
numbers carry zero information: 0.5 reads as "I know nothing," 0.7 as "barely better," when the
classifier is usually certain.

`navigator/tags.py` compounds it:
```python
_SOURCE_CONFIDENCE = {"user":1.0, "pipeline":0.95, "derived":0.85,
                      "metadata":0.8, "inferred":0.5, "default":0.3}
```
`metadata` is a **flat 0.8** regardless of what the metadata actually said, and anything unmapped
defaults to **0.5**. So a file that *explicitly declares* `ContrastMethod="Fluorescence"`,
`Fluor="DAPI"`, `AcquisitionMode="LaserScanningConfocalMicroscopy"` scores the same 0.8 as a vague
hint. The file **says what it is**; that deserves ~0.99.

### The fix — confidence reflects the evidence, and the scale must mean something
1. **Floor a binary call at its chance level.** For a 2-way decision, never report < 0.5 as if it were
   informative — below chance is not "low confidence," it is *no decision*. Return `None` (undecided)
   rather than a number under the chance level. Only genuinely multi-way calls (brightfield/DIC/phase,
   3-way) can meaningfully sit between chance and certainty.
2. **Declarative metadata → near-certain.** When the file states the answer in a dedicated field
   (`ContrastMethod`, `AcquisitionMode`, `Fluor`, `IlluminationType`), confidence is **~0.99** — the
   instrument recorded it. Reserve **1.0 for the user** (an explicit human answer), keeping the
   existing `user: 1.0` semantics intact.
3. **Grade *within* the metadata source** instead of one flat 0.8:
   - declarative field naming the modality/fluorophore → **0.99**
   - unambiguous derived evidence (emission wavelength → spectral bucket) → **0.9**
   - weak/indirect (name substring, filename hint) → **0.7**
   - present but generic/placeholder (see Part 2) → **not used at all**
4. **Keep the confidence SCALE documented and honest.** Write down what a number means
   (`>=0.95` declared, `0.7–0.95` inferred from real evidence, `0.5` chance, `None` undecided) so the
   value is interpretable rather than decorative — the same discipline the measurement ontology applies
   to units.

**Do not** change `user: 1.0`, `pipeline: 0.95`, or `derived: 0.85`; those are already meaningful and
tested.

---

## Part 2 — Filter generic-but-invalid metadata values

### Verified
The only guard when writing metadata is `if v is not None and str(v) != ''`. The uploaded Zeiss file
shows the gap directly: `<Detector Model="" ...>`, `<Microscope />`, `PositionX="NaN"`.

**A present-but-meaningless value is worse than an absent one**, because it looks authoritative,
suppresses the prompt that would have asked the user, and silently satisfies gates that exist to catch
missing information (the pixel-size gate being the sharpest example).

### The fix — one validity filter, applied at write time
Add a small, shared `is_meaningful(field, value)` used wherever metadata is recorded:
- **Empty / whitespace-only** strings → reject (already partly done).
- **Placeholder tokens** (case-insensitive): `unknown`, `n/a`, `na`, `none`, `null`, `undefined`,
  `<none>`, `default`, `-`, `?`. Reject.
- **Non-finite numerics**: `NaN`, `inf` → reject (your file's `PositionX="NaN"`).
- **Sentinel numerics, per field** — this is the important one, and it must be **field-aware, not
  global**: `pixel_size == 1.0` is the known sentinel the gate already treats as "unset"
  (`test_pixel_size_sentinel.py` pins this); `gain == 0`, `magnification == 0`, `NA == 0` are
  physically impossible. But `binning == 1` and `amplification_gain == 1.0` are **legitimate values** —
  never blanket-reject the number 1.
- **Rejection is recorded, not silent**: keep the raw value in the `raw` block with a
  `rejected_reason`, so a user can see *"the file said Model='' and we discarded it"*. Discarding
  invisibly is its own trap.

**Rule:** a rejected value makes the field `None` — which then correctly triggers the existing gates
and prompts. Never substitute a "better" default.

---

## Part 3 — Surfacing contradictions without training the user to ignore them

### What to surface
Two kinds, both real in the uploaded file:
- **Internal metadata contradiction** — `Objective Immersion="Oil"` vs `ObjectiveSettings Medium="Air"`
  with `RefractiveIndex="1.518"` (oil's RI). A genuine Zeiss ZEN export inconsistency.
- **Metadata vs pixels** — declared `ContrastMethod` disagreeing with the pixel classifier.
  **Metadata still wins** (preserving `test_metadata_still_wins_over_pixels`), but the disagreement is
  recorded and shown.

### The interaction (as specified)
- The **metadata button** gains a status indicator: normal when clean, **red when contradictions were
  found**.
- **Hover tooltip** names them concretely — not "there are contradictions" but *"Objective says Oil
  immersion; ObjectiveSettings says Air medium (RI 1.518 indicates oil)."* A vague warning is
  ignorable; a specific one is actionable.
- **Never blocks.** No modal, no disabled action, no forced acknowledgement. Workflow continues.
- Clicking opens the existing `_show_metadata_dialog` (menu_manager:942) with the contradictions
  listed first.

**Colour-vocabulary caution:** `field_status.py` already assigns meanings — RED = *required input not
provided*, GREEN = *step has run*, amber-outlined = *ready*. A metadata-contradiction red is a
**different concept** and must not collide with the step-status semantics the marker-logic work just
fixed. Either use a visually distinct indicator (a warning glyph/badge on the button) or extend the
vocabulary **explicitly and document it** — do not overload the existing red.

---

## Part 4 — The anti-numbing requirement (the hard part)

A warning that fires often and can't be acted on becomes wallpaper. Three mechanisms, all with
precedent in this codebase:

### 4a. Severity, reusing the existing vocabulary
`metadata_extract.py:898` already defines `critical` vs `info` for acquisition differences. Reuse it:
- **critical** — the contradiction affects a *quantitative* result (pixel size, NA, gain, objective).
  Red indicator.
- **info** — cosmetic or non-quantitative (a channel colour, an empty model string). Recorded in the
  dialog, **no red indicator**.
Only critical contradictions raise the flag. This is the single biggest anti-numbing lever: **most
files will show nothing**, so red keeps meaning something.

### 4b. Cry-wolf tests as a standing requirement
`biological_qc` already ships `test_a_clean_population_flags_NOTHING_the_cry_wolf_test`. Mirror it:
**a clean, well-formed file must raise ZERO contradictions.** Add cry-wolf tests over a set of real
clean exports. If a rule fires on clean files, the rule is wrong — fix the rule, never lower the
threshold to hide it.

### 4c. The system learns which contradictions are real — user feedback, per-pattern
This is the "learns what is good and bad" requirement, and it must be built so it **cannot** become a
blanket mute:
- A contradiction can be marked, in the dialog, as **"expected for this instrument"** (e.g. Zeiss ZEN
  always writing `Medium="Air"` on oil objectives — a known vendor quirk, not a real problem).
- Store that judgement **keyed by the contradiction PATTERN plus the acquisition fingerprint**
  (instrument/software/objective) in `user_settings` — which already anticipates exactly this
  ("a dismissed QC warning" is named in its module docstring). **Never key it to the file**, or the
  user re-dismisses forever and learns to click through.
- A pattern marked expected is **demoted to info**, not deleted: it still appears in the dialog,
  greyed, with *"you marked this expected for Zeiss ZEN exports."* **Reversible.**
- **Suppression is per-pattern only.** There is deliberately **no "ignore all"** — a global mute is the
  mechanism by which warning systems die.
- **Record the precision signal.** Track how many raised contradictions get marked expected. A pattern
  marked expected across many fingerprints is a **bug in the rule** — surface that to the developer
  (a test or a log), so the system's false-positive rate is visible and fixable rather than absorbed by
  the user. That is what makes it *learning* rather than *muting*.

**The honest limit, stated plainly:** this learns from *one user's* judgements locally. It is not a
cross-user model, and the spec must not imply it is. The developer-facing precision signal (4c last
bullet) is what turns individual dismissals into an actual rule improvement.

---

## Tests (`core`)
**Confidence:**
- A binary call never reports an informative-looking value below chance; undecided returns `None`.
- Declarative metadata (`ContrastMethod="Fluorescence"`) → confidence ≥ 0.95; weak name-hint → ≤ 0.75.
- `user`/`pipeline`/`derived` confidences are unchanged (regression).
- The documented scale matches what the code emits.

**Validity filter:**
- `""`, `"Unknown"`, `"N/A"`, `NaN` are rejected → field is `None` → the relevant gate still fires.
- `binning=1` and `amplification_gain=1.0` are **kept** (the never-blanket-reject-1 test).
- `pixel_size==1.0` remains the sentinel (existing test passes unmodified).
- A rejected value is retained in `raw` with its reason.

**Contradictions:**
- The uploaded file's Oil/Air contradiction is detected and classified **critical**.
- A clean file raises **zero** contradictions (cry-wolf).
- Metadata still wins over pixels on disagreement (existing test unmodified) **and** the disagreement is
  recorded.
- Critical raises the indicator; info does not.

**Anti-numbing:**
- Marking a pattern expected demotes it to info for **matching fingerprints only**, and a different
  instrument still raises it.
- The judgement persists across sessions via `user_settings` and is reversible.
- There is **no global mute** (assert no API allows suppressing all).
- The precision signal counts marked-expected patterns.

---

## Steps
1. Evidence-graded confidence + binary floor + documented scale.
2. `is_meaningful` validity filter (field-aware sentinels) + `rejected_reason` in `raw`.
3. Contradiction detection (internal + metadata-vs-pixels) with critical/info severity.
4. Metadata-button indicator + specific tooltip + dialog listing; distinct from step-status colours.
5. Per-pattern "expected for this instrument" in `user_settings`, keyed by fingerprint; reversible;
   no global mute; precision counter.
6. Cry-wolf tests over clean files.
7. Full `pytest -m core` green.
8. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- Confidence reflects evidence: declarative metadata ~0.99, user 1.0, binary calls never report
  below-chance numbers as informative.
- Generic/placeholder/sentinel values are filtered per-field, recorded with a reason, and leave the
  field `None` so gates fire.
- Contradictions are detected, severity-classified, shown as a non-blocking red indicator with a
  specific tooltip, and listed in the existing dialog.
- Clean files raise nothing; suppression is per-pattern and reversible with no global mute; the
  false-positive rate is visible to developers.
- Existing confidence/metadata/pixel-size tests pass unmodified.

## Cautions
- **A number below chance is not low confidence — it is no decision.** Return `None`.
- **Never blanket-reject the value 1** — `binning=1` is real; `pixel_size=1.0` is a sentinel. Field-aware
  only.
- **A rejected value must leave the field `None`**, never a substituted default — the gates depend on it.
- **Do not overload the step-status red** from `field_status.py`; this is a different concept and the
  marker semantics were just fixed.
- **Most files must show nothing.** If the indicator is usually on, the rules are wrong — fix the rules,
  never raise the threshold to quiet them.
- **No global mute, ever.** Per-pattern, fingerprint-keyed, reversible. A blanket "ignore all" is how
  this feature would fail.
- Suppression is one user's local judgement — don't imply cross-user learning; the developer precision
  signal is what makes it improve.
