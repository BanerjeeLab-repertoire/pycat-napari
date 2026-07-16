# Claude Code spec — Two audit-derived correctness fixes

**Date:** 2026-07-16 · **Target tree:** 1.6.70 · Verified against the 1.6.70 tree. Two small,
independent, verified correctness fixes from the science/engineering audit. Both contained, no design
questions, collision-free with the loader/brushing/OperationSpec work in flight. Do them as one commit
or two — your call — but they're unrelated so test each independently.

---

## Fix 1 — `set_data` raises `KeyError` on a genuinely new key (engineering defect)
**Verified** (`data/data_modules.py:131`): `set_data` checks the value's class BEFORE checking whether
the key exists:
```python
if self.data_repository[key].__class__ != data.__class__:      # line 131 — KeyError if key is new
    napari_show_warning(f"Data type mismatch for key {key}.")
elif key not in self.data_repository:                          # line 134 — too late
    self.data_repository[key] = data
else:
    self.data_repository[key] = copy.deepcopy(data)
```
For any key not already in the repository, line 131 raises `KeyError` before line 134 can handle the
"new key" case. (It's masked in practice only because most callers set keys that already exist.)

**Fix — reorder so the existence check comes first:**
```python
if key not in self.data_repository:
    self.data_repository[key] = data
elif self.data_repository[key].__class__ != data.__class__:
    napari_show_warning(f"Data type mismatch for key {key}.")
    self.data_repository[key] = copy.deepcopy(data)   # still store — preserve current store-anyway behaviour
else:
    self.data_repository[key] = copy.deepcopy(data)
```
Preserve the existing effect for the two already-working branches (new key stored raw; existing key
deep-copied). Decide the mismatch branch deliberately: today it WARNS then (via the `else`) would have
deep-copied — keep it storing after the warning so a type change isn't silently dropped. Confirm the
warning still fires for a real type mismatch on an existing key.

**Test** (`tests/test_set_data.py`, `core` — pure, no Qt if `napari_show_warning` is import-guarded;
if it pulls napari, patch it): setting a NEW key stores it with no exception (the regression);
overwriting with the SAME type deep-copies; overwriting an existing key with a DIFFERENT type warns
and still stores. Monkeypatch `napari_show_warning` to capture the warning.

---

## Fix 2 — focus scoring picks the sharpest DEBRIS (science correctness)
**Verified partial state — this is a WIRING fix, not a build.** The mask machinery already exists:
- `brightfield_tools.bf_focus_metric(image, mask=None)` (`:707`) already accepts a mask and its
  docstring already describes the debris problem; `temperature_tools` already calls it correctly with
  a mask (`bf_focus_metric(frame, mask=fm)`).
- BUT the series scorers still call it WHOLE-FRAME: `bf_analyse_focus_series`
  (`brightfield_tools.py:778` and `:840`) call `bf_focus_metric(frame)` / `bf_focus_metric(arr[i])`
  with no mask.
- AND the condensate-side `analyse_frame_quality` (`condensate_physics_tools.py:1607`) scores
  `laplacian_variance` / `image_entropy` over the whole frame with no mask option — same debris
  vulnerability, no machinery yet.

So sharp dust / a bright out-of-plane speck can score higher than an in-focus condensate → the "best
frame" can be the junk frame.

**Fix:**
1. `bf_analyse_focus_series` (and `bf_analyse_frame_quality` if it feeds the same path): accept an
   optional `mask=` (or per-frame mask stack) and pass it through to `bf_focus_metric`. When a mask is
   provided, focus is scored INSIDE it. `mask=None` preserves current whole-frame behaviour (back-compat).
2. `analyse_frame_quality` (`condensate_physics_tools.py:1607`): add the SAME optional `mask=` and
   restrict the Laplacian-variance / entropy computation to the masked region when provided. Mirror
   the `bf_focus_metric` mask semantics so both sides behave identically.
3. Callers that HAVE a relevant mask (the cell/object segmentation) should pass it. Grep the callers
   (`invitro_bf_ui.py:620`, `brightfield_ui.py:943`, the focus-series users) — where a segmentation
   mask is already in hand, thread it through; where none exists, leave `mask=None` (unchanged).
   Do NOT fabricate a mask — only pass one that genuinely marks the objects of interest.
4. Optional but cheap (audit's recommendation): report focus PER COMPARTMENT (per-object) not just
   per-frame, and use >1 metric so a single Brenner/Laplacian value isn't the sole discriminator —
   but the mask restriction is the core fix; the per-compartment report can be a follow-up if it grows
   the function past the complexity ceiling.

**Test** (extend `tests/test_data_qc.py` or a new `tests/test_focus_debris.py`, `core`): build a
synthetic stack where an in-focus condensate is in frame A and a SHARPER piece of out-of-plane debris
is in frame B. Assert: whole-frame scoring (`mask=None`) picks frame B (the debris — reproduces the
bug); mask-restricted scoring (mask = the condensate region) picks frame A (the fix). Do this for BOTH
`bf_analyse_focus_series` and `analyse_frame_quality` so both sides are guarded. This is exactly the
audit's stated acceptance criterion.

---

## Steps
1. Fix 1: reorder `set_data`; add `test_set_data.py`.
2. Fix 2: thread `mask=` through `bf_analyse_focus_series` + `analyse_frame_quality`; pass masks from
   callers that have them; add the debris acceptance test for both.
3. Full `pytest -m core` green (esp. `test_data_qc`, any brightfield/condensate focus test, and the
   complexity budget — if the mask threading grows `analyse_frame_quality` past 120 lines, extract the
   masked-scoring into a helper, don't raise the ceiling).
4. Ship: own version + PyPI push + commit (EXPLICIT filenames: data_modules.py, brightfield_tools.py,
   condensate_physics_tools.py, the callers touched, the tests, pyproject, CHANGELOG) + CHANGELOG
   entry (set_data KeyError fix; focus scoring restricted to object mask to avoid picking sharp debris).

## Definition of done
- `set_data` stores a brand-new key without raising; existing-key behaviour unchanged; mismatch warns
  and stores.
- `bf_analyse_focus_series` and `analyse_frame_quality` accept an optional mask and score focus inside
  it; `mask=None` preserves old behaviour; callers with a segmentation mask pass it.
- The debris acceptance test passes for both focus paths (whole-frame picks debris, masked picks the
  condensate).
- Full `pytest -m core` green.

## Cautions
- `set_data`: preserve the two working branches' effects exactly; only fix the ordering + the mismatch
  branch's store-after-warn. This is a reorder, not a rewrite.
- Focus: `mask=None` MUST preserve current whole-frame behaviour (back-compat — existing callers
  without a mask keep working). Only restrict when a real object mask is supplied.
- Do NOT fabricate a mask to force the fix — a wrong mask is worse than whole-frame. Pass only genuine
  segmentation masks.
- Watch the complexity ceiling on `analyse_frame_quality` (already a long function); extract a helper
  for masked scoring rather than raising `_MAX_LONG_FUNCTIONS`.
- These two fixes are unrelated — if one is trickier than expected, ship the other independently rather
  than blocking both.
