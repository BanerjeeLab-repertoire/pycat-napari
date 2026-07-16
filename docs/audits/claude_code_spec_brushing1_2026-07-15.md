# Claude Code spec — Brushing increment 1: kill the lag and the wrong-target bug

## ✅ STATUS — DONE, shipped in 1.6.73 (executed against the 1.6.72 tree)
Both bugs reproduced first, then fixed and re-verified. `pytest -m core`: **629 passed, 2 skipped**
(was 621). Definition of done met: a click on a lazy stack crops with **0** full-array attempts; a ref
with `source_layer_id` resolves to its own layer; a legacy ref falls back and says so; every layer
carries `metadata['pycat_layer_id']`; `test_tag_hook_installs.py` still green. Both guards
mutation-checked (reverting either fix turns them red).

**Fix 1 is worse in the current tree than the spec describes — worth knowing.** The spec predicted a
multi-second freeze ("one click tries to load the entire acquisition"). Since the lazy wrappers'
`__array__` now **refuses** (`refuse_implicit_full_read`, 1.6.3+), the eager read doesn't freeze — it
**raises**, the surrounding `except` abandons the live-layer path entirely, and the click falls through
to `resolve_offline`. Reproduced: with the source file moved, the user is told *"The source file is
gone"* **while the layer is open in the viewer**. So the symptom was a silent wrong answer, not lag.

**Two small deviations, both additive:**
1. **`layers_for_ref` is a shared helper**, not the same matching logic written twice. The spec asks
   for the correction in `resolve_in_viewer` *and* in `crop_for_ref`'s loop; two copies of "find the
   ref's own layer" is exactly the drift this codebase keeps paying for (see the `stack_access`
   re-export tombstone). One implementation, both callers.
2. **A ref whose layer is CLOSED now resolves to nothing**, rather than falling back to the
   role-based first match. The spec only defines the fallback for refs with *no* `source_layer_id`
   (old refs). A ref that names a layer which isn't open is telling us the honest answer is "not
   here" — quietly highlighting a different mask would be the original bug wearing a new hat.

**Note for increment 2:** the spec references `docs/audits/brushing_roadmap_2026-07-15.md` for
increments 2–5; that file is **not in the tree** (only the per-increment specs brushing2–5 are).

**Date:** 2026-07-15 · **Target tree:** 1.6.64 · Verified against the 1.6.64 tree.
First of five brushing increments (2–5 in `docs/audits/brushing_roadmap_2026-07-15.md`). This one is
the shippable bug-fix pass — the two changes that make brushing *correct* and *fast* — and it plants
the minimal layer-identity seed the later increments build on. Does not touch `file_io.py`.

## Why these two first
An architecture audit of the brushing/linked-selection system proposed a full `SelectionService` +
`EntityRef` rebuild. That's staged as increments 2–5. **Increment 1 is just the two fixes the audit
itself calls highest-priority** — they eliminate the two ways brushing is currently *harmful* (a
multi-second freeze, and a scientifically wrong highlight), in a handful of lines, with no new
architecture.

## Fix 1 (highest priority) — `crop_for_ref` materializes the whole stack
**Verified:** `src/pycat/utils/brushing.py:215` does `data = np.asarray(layer.data)` and THEN slices
(216). On a lazy TIFF/IMS/CZI/dask layer that triggers the guarded full-array conversion — **one click
tries to load the entire acquisition** before taking an 8-px crop. This is the recurring
`np.asarray(layer.data)` materialization trap, in the brushing path.
**Fix — slice BEFORE materializing.** Index the lazy layer to the frame, slice the crop window, and
only then coerce the tiny crop to an array:
```python
lazy = layer.data
if ref.frame is not None and getattr(lazy, 'ndim', 2) >= 3:
    plane = lazy[int(ref.frame)]          # one plane, lazily — not the whole stack
else:
    plane = lazy
window = ref.crop_slice(pad_px=pad_px)
if window is not None:
    crop = plane[window]                   # slice the (still-lazy) plane
    return np.asarray(crop), ''            # materialize ONLY the crop
```
Keep the `try/except` + `resolve_offline` fallback exactly as-is. The change is purely the
order: index → slice → `np.asarray` on the crop, never on `layer.data`. Note the lazy wrappers
support `__getitem__` for a single frame index (that's the TYX contract); if a given wrapper can't be
indexed lazily, the `except` still falls through to `resolve_offline` — do not re-introduce a
whole-stack `np.asarray` as a fallback.

**Guard test** (`tests/test_brushing.py`, mark `core`): build a lazy stack wrapper whose `__array__`
raises (the `refuse_implicit_full_read` guard, or a stub that records if it's called), attach it as a
layer, call `crop_for_ref`, and assert the crop comes back WITHOUT the full-array path ever firing.
This is the same shape as `test_no_eager_reads.py`.

## Fix 2 — `resolve_in_viewer` selects the FIRST labels layer (wrong-target)
**Verified:** `src/pycat/utils/object_ref.py:212–213` loops the viewer and grabs the first layer whose
role is `labels`/`mask`, then sets `selected_label = ref.object_id` on it. **With two segmentations
open, a punctum from analysis A highlights integer label N in an unrelated mask B.** That's a
scientific error, not just UX — the user is shown the wrong object as if it were right.
**Fix — resolve to the ref's OWN layer.** The ref must carry which layer it came from, and the resolve
must honour it:
1. Add a `source_layer_id: str | None = None` field to `ObjectRef` (`object_ref.py`) — additive,
   defaulted, so every existing construction still works.
2. In `resolve_in_viewer`, if `ref.source_layer_id` is set, select THAT layer (match by the layer's
   stamped id — see the identity seed below); only fall back to the role-based first-match when the
   ref has no `source_layer_id` (old refs), and when it does fall back, say so via the return/debug so
   a silently-wrong highlight becomes a visibly-degraded one.
3. Same correction in `crop_for_ref`'s layer loop (fix 1) — prefer the ref's own layer when known.

## The identity seed (minimal — the foundation increments 2–5 extend)
Both fixes above need "which layer did this object come from." Rather than invent a parallel scheme,
**stamp a stable `layer_id` in the ONE place every layer is already tagged** — the viewer tag hook
(`src/pycat/utils/layer_tag_hook.py`), which already wraps every `add_image`/`add_labels`/`add_points`/
`add_shapes`/`add_tracks` and stamps `role`/`__pycat_op__`. Add: when the hook fires, if the new
layer has no `metadata['pycat_layer_id']`, stamp one (`uuid4().hex`). One place, every layer, zero
per-call-site edits. Then `resolve_in_viewer`/`crop_for_ref` match on `metadata['pycat_layer_id'] ==
ref.source_layer_id`. (Populating `ref.source_layer_id` at ref-creation time is increment 2's job; in
increment 1 it's an optional field that, when present, is honoured — the plumbing, not yet the fill.)

## Steps
1. `layer_tag_hook.py`: stamp `metadata['pycat_layer_id'] = uuid4().hex` on layers lacking one (in the
   existing hook body — do NOT add a new hook). Guard with try/except like the rest of the hook.
2. `object_ref.py`: add `source_layer_id` field; make `resolve_in_viewer` prefer it, fall back loudly.
3. `brushing.py`: rewrite `crop_for_ref` to slice-before-materialize + prefer the ref's layer.
4. Tests: the no-eager-read guard for `crop_for_ref`; a wrong-target guard asserting that with two
   mask layers open and a ref carrying `source_layer_id` of the second, resolve selects the SECOND
   (mark both `core`).
5. Full `pytest -m core` green (including `test_tag_hook_installs.py` — the layer-id stamp must not
   break the hook; and `test_brushing.py`).
6. Ship: own version + PyPI push + commit (EXPLICIT filenames: brushing.py, object_ref.py,
   layer_tag_hook.py, the tests, pyproject, CHANGELOG) + CHANGELOG entry noting increment 1 of the
   brushing plan (lag + wrong-target fixed; layer-id seed planted for increments 2–5).

## Definition of done
- A click on a plot backed by a lazy stack crops WITHOUT materializing the acquisition (guarded).
- With multiple segmentations open, a ref with `source_layer_id` resolves to its OWN layer; a legacy
  ref without one falls back and says it did.
- Every layer carries `metadata['pycat_layer_id']`; the tag hook still installs and works.
- Full `pytest -m core` green. Behaviour-preserving except the two bugs, which are fixed.

## Cautions
- Do NOT re-introduce a whole-stack `np.asarray` anywhere in `crop_for_ref` — the `except` path goes
  to `resolve_offline`, never to an eager read.
- The layer-id stamp is additive metadata; it must not change tagging behaviour or layer identity for
  napari. Keep it inside the existing hook's try/except.
- `source_layer_id` is optional and defaulted — every existing `ObjectRef(...)` / `from_row` call must
  still work untouched. Increment 1 makes resolve HONOUR it; increment 2 makes ref-creation FILL it.
- Do not build the `SelectionService` or `EntityRef` here — that's increments 3 and 2. This increment
  is bug-fixes + the one-line identity seed only.
