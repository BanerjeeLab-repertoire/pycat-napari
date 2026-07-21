# Claude Code spec — Redundancy consolidation (science-preserving, output-identical)

> **◐ STATUS — Axis 1 (pixel-size accessors) DONE + now structurally guarded (1.6.212); axes 2–4 open.**
> Every `_mpx()` in the tree routes through the canonical `pixel_size.pixel_size_um_or_default(dr,
> context=...)` — the 10 UI accessors plus the two `_tools` copies (`intensity_profile_tools`,
> `morphological_complexity_tools`) that were converted from `except Exception: return 1.0`. The missing
> structural guard is now in place: `tests/test_pixel_size_single_accessor.py` (an AST check that every
> function named `_mpx` calls the canonical accessor, with a canary), so a new UI cannot quietly reintroduce
> a bespoke `dr.get('microns_per_pixel_sq') or 1.0` and re-open the silent-units hole.
> **Remaining:** axis 2 (background-subtraction mechanics — read-and-classify: merge only true duplicates,
> keep partition vs rolling-ball distinct), axis 3 (worker/thread lifecycle → `operation_runner`, to do
> WITH the timeseries/VPT decompositions), axis 4 (the 41 `np.asarray(...data)` vs `materialize_stack`
> stack-access sites — classify + route). Each remains bound by the output-identical rule.

**Date:** 2026-07-20 · **Target tree:** 1.6.203 · Verified against the 1.6.203 tree. A different axis
from decomposition: instead of splitting big files, this finds **duplicated logic** and routes it
through one canonical implementation. The overriding constraint — stated up front and enforced by tests
— is that **no scientific output may change**. Consolidation that alters a number is a regression, not
a cleanup. This is refactoring toward a single source of truth, verified byte-identical at every step.

## The governing rule
> Every consolidation must be proven **output-identical** on real inputs before and after. If two
> "duplicate" implementations produce *different* numbers, they are **not** duplicates — they are two
> behaviours, and merging them is a science change that must STOP and be reported, not silently
> resolved.

This is the inverse risk of decomposition. A split preserves behaviour by moving code untouched;
consolidation *merges* code, so it can silently pick one implementation's behaviour over another's. The
tests exist to catch exactly that.

## The verified redundancy targets, in priority order

### 1. Pixel-size accessors (59 references — highest leverage, clear canonical target)
Verified: a canonical helper **already exists** — `pixel_size_um_or_default(dr, context=...)` — but many
UIs still define their own `_mpx()` that wraps it *inconsistently*: some call the helper, some
re-derive from the data repository directly (`invitro_bf_ui`, `brightfield_ui`,
`morphological_complexity_tools`, `invitro_fluor_ui`, `frap_ui`, … each has its own `_mpx`).
- **Consolidate every `_mpx()` to call the one canonical accessor**, passing its `context` string.
- **The correctness stake:** pixel size scales every physical-unit measurement (viscosity, ΔG, size,
  density). An inconsistent accessor — one that defaults differently, or misses the gate — silently
  corrupts units in one workflow but not another. One accessor closes that.
- **Output-identical rule:** for each `_mpx` replaced, the value returned on the same data repository
  must be **exactly** what it returned before (same default behaviour, same gate interaction). Where an
  existing `_mpx` behaved *differently* from the canonical helper, that difference is a **finding** —
  report it (one of them was wrong) rather than silently adopting either.

### 2. Background-subtraction paths (8 sites — consolidate the mechanics, NOT the science)
Verified these are a mix: `partition_enrichment_tools.assess_background_region` (partition-specific,
scientific), the `image_processing_tools` rolling-ball/Gaussian family, `ratiometric_tools._prepare`,
`apply_background_subtraction`. **These are NOT all the same** — the partition background reasoning is
deliberately distinct from rolling-ball background removal (the codebase has careful docstrings on why).
- **Consolidate only the genuinely-identical mechanics** — e.g. the low-level "subtract a scalar/array
  offset with clipping" step if it is duplicated verbatim — into one helper.
- **Do NOT merge scientifically-distinct background concepts.** Partition background (instrument offset)
  and rolling-ball background (spatial estimation) are different measurements; merging them would be the
  exact error the partition docstring warns against. Keep them separate; note the distinction.
- This one is mostly a **read-and-classify** task: confirm which sites are true duplicates (merge) vs
  distinct science (leave, document why).

### 3. Worker/thread lifecycle (the audit's "duplicated worker lifecycle" #7)
Verified: `operation_runner` is the canonical qt-worker path, but several modules
(`timeseries_condensate_tools._make__stackprocessworker`, VPT's ProcessPool path, and the `_start_worker`
patterns) carry their own worker plumbing.
- **Route the duplicated Qt-worker lifecycle through `operation_runner`** where the semantics match.
- **Behaviour-preserving only** — threading changes are notoriously subtle; a consolidation that changes
  when a worker cancels, how progress reports, or thread affinity is a behaviour change even if the
  numbers match. Consolidate the *plumbing*, keep the *semantics*, and if they genuinely differ, leave
  them separate.
- Coordinate with the timeseries/VPT decomposition specs (their `execution.py` modules) — do the worker
  consolidation as part of those moves, not as a separate uncoordinated pass.

### 4. Stack-access (`np.asarray(...data)` vs `materialize_stack`) — 41 sites
Verified 41 sites. This overlaps the known frame-0 landmine. Consolidation here = **route every
stack-consuming site through `materialize_stack`** (or the explicit `iter_frames`), so there is one
stack-access path with one defused behaviour.
- **Per-site judgment** (as previously decided): most are safe 2D-only sites where `asarray` is correct.
  Only genuine lazy-time-series consumers need `materialize_stack`.
- **Output-identical:** for a 2D site, `materialize_stack` and `asarray` return the same array — so
  routing it through the helper changes nothing but removes the footgun. Verify per site.
- This is the consolidation that also closes a latent bug class — do it carefully, with the frame-0
  reasoning per site.

## Method — the output-identical discipline
For EVERY consolidation, in order:
1. **Characterize both sides.** Capture the output of the duplicated logic (and the canonical target) on
   a real/synthetic input at `rtol=1e-9` / exact for integers/masks.
2. **Prove they match.** If they do → consolidate (route the duplicate to the canonical).
3. **If they DON'T match → STOP.** That is a finding: two behaviours were masquerading as duplicates.
   Report which differs and by how much; do not merge until the science owner (Gable) decides which is
   correct. Merging silently would pick a winner arbitrarily — the exact regression this spec forbids.
4. **Re-run the characterization after consolidation** — identical, or revert.

## Scope discipline — consolidate mechanics, never merge science
- **Mechanical duplication** (a scalar-subtract, a pixel-size lookup, worker plumbing) → consolidate.
- **Scientifically-distinct logic that happens to look similar** (partition vs rolling-ball background,
  the two MSD paths, different threshold strategies) → **leave separate**, document why they differ.
- When unsure whether two things are "the same," treat them as **different** until proven identical.
  False consolidation is worse than duplication.

## Tests
- Each consolidated site: a characterization test proves the output is identical before and after.
- The pixel-size accessor: every workflow's `_mpx` returns the same value post-consolidation (assert per
  workflow on a fixture data repository).
- Any two implementations found to **differ** are reported as findings with the magnitude, not merged.
- No golden-master, filter-sensitivity, or route-equivalence test changes behaviour.
- A guard test (optional, high-value): assert only one canonical pixel-size accessor exists (no new
  `_mpx` that bypasses it) — a ratchet against re-duplication.

## Steps
1. **Pixel-size:** route every `_mpx` to `pixel_size_um_or_default`; characterize each returns-same;
   report any that differed. Add the single-accessor guard.
2. **Stack-access:** per-site, route lazy-time-series consumers through `materialize_stack`; verify 2D
   sites unchanged.
3. **Background mechanics:** classify the 8 sites; consolidate only verbatim-identical mechanics; leave
   and document the scientifically-distinct ones.
4. **Worker lifecycle:** coordinate with the timeseries/VPT decomposition; route matching plumbing to
   `operation_runner`, semantics preserved.
5. Characterization tests for every consolidation; full `pytest -m core` + golden-master +
   filter-sensitivity + route-equivalence green.
6. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG listing each consolidation and
   confirming output-identical (and any finding where two "duplicates" differed).

## Definition of done
- Pixel-size access flows through one canonical accessor; every workflow returns the same value as
  before; a guard prevents re-duplication.
- Stack access routes through `materialize_stack`/`iter_frames`; 2D sites unchanged; lazy sites defused.
- Genuinely-duplicate background/worker mechanics consolidated; scientifically-distinct ones left and
  documented.
- Every consolidation proven output-identical; any mismatch reported as a finding, not silently merged.
- All golden-master/sensitivity/route-equivalence tests pass unmodified.

## Cautions
- **Output-identical is the law.** A consolidation that changes a number is a regression. Characterize
  before and after, every time.
- **Different numbers = not duplicates = STOP and report.** Two implementations that disagree are two
  behaviours; merging them silently picks a winner arbitrarily. That decision is Gable's, not the
  refactor's.
- **Never merge scientifically-distinct logic** — partition vs rolling-ball background, the two MSD
  paths, distinct thresholds. When unsure, treat as different.
- **Worker consolidation preserves semantics, not just numbers** — cancel timing, progress, thread
  affinity all count as behaviour.
- Coordinate the worker-lifecycle consolidation with the timeseries/VPT decomposition specs — don't do
  it twice.
- One consolidation target per commit; each independently revertible.
