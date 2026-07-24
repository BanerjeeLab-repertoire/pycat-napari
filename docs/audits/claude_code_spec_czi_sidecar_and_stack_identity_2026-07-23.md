# Claude Code spec — Close the two deferred items from the sidecar/channel-identity work

> **✅ STATUS — Part 1 (CZI split + sidecar wire) @1.6.327; Part 2 (stack RECALL) @1.6.328; Part 3 (collection
> guard) git-only. All three addressed; two honest residuals noted below.**
>
> **Part 2 — stack RECALL DONE, shipped 1.6.328.** All three stack back-ends (IMS / generic / CZI-streaming) now
> build the whole channel list and resolve it once through `resolve_channel_identity_on_load` (sidecar + recall
> by acquisition signature) **before naming** — recall keys on the full list's signature, so it cannot be
> applied per channel. Precedence preserved and asserted: metadata > sidecar > remembered > guess; recall
> applies to a new file of the same layout (signature, not path), a different layout does not recall, and a
> remembered answer never overrides a metadata/sidecar name. A normal stack resolves to the identical list, so
> naming is unchanged (characterization suites pass). `tests/test_load_channel_identity.py` +3 (`base`).
> **Two residuals, stated:** (1) **REMEMBER** during a stack load needs a stack channel-identity *prompt*, which
> the stack loaders don't have — a fresh answer is captured on the 2D dialog and recalled on stacks; there is no
> new remember trigger on the stack path. (2) Resolution is **per-loader immediately before naming** (via the
> one shared resolver), NOT at `_finalise_stack_load` as the spec suggested — because recall must precede naming
> and the funnel runs after it; this is one resolver called at one pre-naming point per loader, not a parallel
> collection point. Live layer-naming on a real stack is outside the headless gate.
>
> **Part 3 — collection-completeness guard DONE (test-only, git-only).** `test_ci_dependencies.py::
> test_no_NEW_test_file_is_silently_skippable_at_import` — a file carrying a `core`/`base` test with a bare
> MODULE-scope import of a GUI-bound pycat / optional-stack package is silently ignored by
> `conftest.pytest_ignore_collect` when that stack is absent (no error), so its headless tests never run in that
> lane. **The scan surfaced 26 such files** (not one incident — a class): they run in CI's full lane but hide in
> any partial/local lane. The guard grandfathers those 26 in `_SILENTLY_SKIPPABLE_AT_IMPORT` and **fails on any
> NEW one**, naming it, and requires a fixed file to be removed from the set (the debt list stays accurate). The
> 26 are follow-on debt to convert to the guarded-import pattern incrementally.
>
> **Part 1 — DONE, 1.6.327.** The libCZI-metadata preamble was extracted verbatim into `_czi_open_metadata`
> (returns `(image, microns_per_pixel)`), dropping `_open_czi_streaming` from 120→106 lines (ratchet metric) —
> the unreviewable-function count **fell by one**, `_MAX_LONG_FUNCTIONS` untouched. The sidecar is then wired on
> the CZI path exactly as the generic path: `sidecar_metadata_for` once per load (non-gating), each weak channel
> named from its emission via `enrich_channel_from_sidecar`. Code-motion + a 2-line reuse of the unit-tested
> helper; CZI/stack regression green. End-to-end CZI load remains outside the headless suite (no CZI fixture /
> BioFormats in the gate).

**Date:** 2026-07-23 · **Target tree:** 1.6.324 · Verified against the 1.6.324 tree. Two items were
deferred with stated reasons during the sidecar work. Both are worth closing, and the first was deferred
for a reason that is honest but should not stand permanently.

---

## Part 1 — `_open_czi_streaming` is 121 lines, so CZI never got the sidecar

### Verified
```
_MAX_LONG_FUNCTIONS = 120
_open_czi_streaming: 121 lines   (stack_openers.py:569-689)
```
Sidecar discovery is wired into the **position path** (line 177) and the **generic path** (line 483) via
`sidecar_metadata_for` + `enrich_channel_from_sidecar`. **CZI streaming is not**, because adding the
call would push a function that sits exactly at the ratchet over the limit.

**The call to leave the ratchet alone was right.** Raising a complexity ceiling to fit a feature is the
failure mode the ratchet exists to prevent, and the justification offered — CZI carries structured
metadata, so the sidecar case is less pressing — is reasonable.

**But the reason is contingent, not principled.** "CZI usually has good metadata" is not the same as
"a CZI never has a companion file worth reading," and the real cause is that one function is one line
too long. Leaving a capability off a format because of an unrelated line count is technical debt with a
plausible cover story.

### The fix: split the function, then wire it
`_open_czi_streaming` is 121 lines doing several separable things (reader setup, dimension resolution,
lazy-source construction, layer add, finalisation). Extract one cohesive block — the most natural is the
**reader/dimension resolution** preamble — into a helper, following the same
characterization-then-move discipline the science decompositions used:

1. Pin the current behaviour: a CZI load produces the same layers, shapes, scales, and metadata.
2. Extract the block; the function drops comfortably under the ratchet.
3. **Then wire the sidecar** exactly as the generic path does (line 483 is the template):
   discovered once, non-gating, `None` when absent.
4. The ratchet is **not** touched. The count should fall by one, not rise.

**Do not raise `_MAX_LONG_FUNCTIONS`.** If the split proves genuinely impossible without restructuring
the loader, say so and leave CZI unwired with the reason recorded in the code — but attempt the split
first.

### Tests
- A CZI load is byte-identical before and after the split (layers, shapes, scale, metadata).
- `_open_czi_streaming` is under the ratchet; `_MAX_LONG_FUNCTIONS` is unchanged or lower.
- A CZI with a companion sidecar gets channel names from it; without one, load is unaffected.
- Sidecar discovery on the CZI path is non-gating (a failing/absent sidecar never blocks the load).

---

## Part 2 — Identity recall/remember does not reach the stack path

### Verified
`stack_openers.py` imports `enrich_channel_from_sidecar` but **nothing in the stack path calls
`recall_identity`/`remember_identity`** (grep → only the sidecar import). So a user who answers the
channel-identity prompt for a stack gets no benefit on the next stack with the same layout — the
persistence that works on the 2D path is absent here.

The blocker was stated correctly: recall/remember keys on
`channel_designations.acquisition_signature(channel_infos)` — **a list covering all channels** — but the
stack loaders enrich channel info **per channel, inside the load loop**, so no aggregated list exists at
the point where recall would happen.

### The fix: aggregate at the one place that already sees every stack
`_finalise_stack_load` documents itself as *"the one place that sees every stack, IMS and generic
alike."* That is the natural home:

1. **Collect** the per-channel `_ch_info` dicts as the loaders build them, into a list on the load
   context rather than discarding each after use.
2. **Pass the aggregated list into `_finalise_stack_load`** (it already takes `channels_to_load`,
   `n_t`, `n_z` — this is one more parameter of the same kind).
3. In `_finalise_stack_load`, call **`recall_identity(channel_infos)`** once: if a remembered answer
   exists for this acquisition signature, apply it; if the user later answers the prompt,
   **`remember_identity`** stores it keyed to the signature.
4. **Signature, not path** — the whole point of `acquisition_signature` is that a *new file with the
   same channel layout* inherits the answer. Do not key on the file.

### The ordering rule
Recall must not overwrite better evidence. Precedence stays as the identity work established:
**real metadata > sidecar > remembered user answer > pixel guess** — except that an *explicit user
answer for this acquisition* outranks a guess but never overrides metadata that actually names the
channel. Assert this ordering in a test; it is the part most likely to get inverted.

### Tests
- A stack load aggregates one `_ch_info` per channel and passes the list to `_finalise_stack_load`.
- Answering the identity prompt for a stack, then loading a **different file with the same layout**,
  recalls the answer (the signature-not-path test).
- A different channel layout does **not** recall.
- Recall does not override metadata- or sidecar-derived names; it does outrank a pixel guess.
- The 2D path's existing recall behaviour is unchanged (regression).
- Headless-safe: aggregation and recall work without a viewer.

---

## Part 3 — The test-collection gap is a class, not an incident

A bare `import pycat.file_io` in `test_load_channel_identity.py` caused the file to be **silently
skipped** in the local gate — its tests had only ever run when named explicitly, since 1.6.320. Switching
to the guarded-import pattern moved the gate from **1877 → 1887 passed**.

That is a *silent* loss of coverage: nothing failed, tests simply did not run. It is the same shape as
the qtbot/openpyxl/skimage failures — an environment/collection mismatch — except this one produced **no
error at all**, which makes it worse.

**Guard:** a test asserting that **every** file in `tests/` is actually collected in the lane that should
run it — i.e. the collected-test count per marker matches the number of test functions carrying that
marker, or any skipped-at-import file is reported by name with its reason.

Ten tests hid for four versions. Without this guard the next bare import hides more, and nothing goes
red.

---

## Steps
1. Split `_open_czi_streaming` under the ratchet (characterize → move → verify), then wire the sidecar
   on the CZI path. Ship.
2. Aggregate channel infos through `_finalise_stack_load`; wire `recall_identity`/`remember_identity`
   with the stated precedence. Ship.
3. Add the collection-completeness guard. Ship.
4. Full `pytest -m core` green after each.

## Definition of done
- CZI streaming is under the complexity ratchet **without the ratchet moving**, and reads companion
  sidecars like the other paths.
- Stack loads recall and remember channel identity by acquisition signature, with metadata and sidecar
  still outranking a remembered answer.
- No test file can be silently skipped at import without being reported.

## Cautions
- **Never raise the ratchet to fit a feature.** Split the function; if it cannot be split, leave the
  feature unwired and record why in the code.
- **Signature, not path** — recall keyed to the file defeats the purpose.
- **Recall must not override metadata.** Assert the precedence explicitly; it is the easiest thing to
  invert.
- **Aggregate at `_finalise_stack_load`**, the one documented funnel for every stack — do not add a
  parallel collection point in each loader.
- The collection guard should **name** silently-skipped files rather than just counting, or diagnosing
  the next occurrence will be guesswork.
