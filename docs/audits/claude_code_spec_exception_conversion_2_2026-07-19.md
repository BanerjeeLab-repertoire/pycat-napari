# Claude Code spec — Exception conversion increment 2: the scientific paths

**Date:** 2026-07-19 · **Target tree:** 1.6.156 · Verified against the 1.6.156 tree. Continues the
typed-failure work: the ratchet and the error hierarchy shipped in 1.6.139, and the `file_io`
decomposition converted 45 handlers as it moved code (284 → 239). This increment targets the largest
remaining concentration — `toolbox/` at **514** — where a swallowed failure is most likely to corrupt
a scientific result rather than a widget.

## State (verified)
| package | broad handlers | ratchet |
|---|---:|---|
| `toolbox` | 544 | pinned at 514 |
| `file_io` | 284 | pinned at 239 (converted during decomposition) |
| `ui` | 266 | — |
| `utils` | 125 | — |

The error hierarchy exists and is well-shaped: `PyCATError` with `UnsupportedFormatError`,
`MetadataUnavailableError`, `InvalidCalibrationError`, `ScientificAssumptionError`,
`OptionalDependencyError`, `LayerResolutionError`, plus `StackLoadCancelled` as a control-flow signal.

**The `file_io` decomposition proved the method**: converting handlers *while already moving code* cost
little and dropped the count 16%. This increment applies the same approach without a decomposition to
ride along with — so it must be targeted rather than exhaustive.

## The triage rule — convert by consequence, not by count
Do **not** attempt 514 handlers. Convert only those where swallowing changes a **number a scientist
will report**. The ordering:

**Tier 1 — convert (a swallowed failure produces a wrong or fabricated measurement):**
- calibration / unit conversion / pixel-size paths → `InvalidCalibrationError`
- fit routines where a failed fit currently falls back to a plausible value → `ScientificAssumptionError`
- metadata reads that feed physical units (frame interval, z-step, exposure) → `MetadataUnavailableError`
- segmentation/detection gates whose failure silently empties or fills a population
- anything inside a function that returns a `Parameter` — a typed failure there is the difference
  between an honest NaN and a fabricated number

**Tier 2 — annotate `# broad-ok: <reason>` (legitimate):**
- optional-backend probes (GPU, cellpose version, trackmate/Java)
- Qt teardown and widget cleanup
- best-effort notification/logging paths
- metadata *probing* where absence is expected and already reported honestly

**Tier 3 — leave alone for now:** everything else. The ratchet already prevents growth.

## The conversion pattern
```python
# BEFORE — a swallowed scientific failure
try:
    scale = float(meta['PhysicalSizeX'])
except Exception:
    scale = 1.0          # ← a fabricated number that will be reported as a measurement

# AFTER — the failure has a name, and the caller decides
try:
    scale = float(meta['PhysicalSizeX'])
except (KeyError, TypeError, ValueError) as exc:
    raise MetadataUnavailableError(
        "physical pixel size is absent from the file metadata"
    ) from exc
```
Two rules:
- **Narrow the caught exception type** as well as raising a typed one. `except Exception` around a
  dict lookup is hiding `KeyError` *and* every unrelated bug in the block.
- **Preserve `from exc`** so the original traceback survives — a typed error that discards its cause
  is harder to debug than the broad handler it replaced.

## Where a fallback is genuinely correct
Some fallbacks are legitimate science (a robust estimator failing over to a simpler one). Those must
become **explicit and recorded**, not silent:
```python
except np.linalg.LinAlgError:
    debug_log("robust fit failed; falling back to least squares")
    result = least_squares_fit(...)
    result.validation = ValidationLevel.DEGRADED   # the caller can see it happened
```
The existing `ValidationLevel` on `Parameter` is the right carrier — the fallback is honest because it
is *visible in the result*, not because it was logged somewhere.

## Target
Convert **≥60 Tier-1 handlers** in `toolbox/`, lower the ratchet to the achieved count, and annotate
the Tier-2 ones encountered. A 12% reduction concentrated entirely in scientific paths is worth more
than a larger reduction spread across Qt cleanup.

## Prioritized modules (highest scientific consequence first)
1. `calibration`-adjacent and unit-conversion paths wherever they appear
2. `condensate_physics_tools`, `vpt_tools` (fits, viscosity, MSD — where a fallback becomes a
   published number)
3. `partition_enrichment_tools`, `invitro_tools` (partition/enrichment ratios)
4. `frap_tools`, `fusion_tools` (fit-heavy)
5. `segmentation_tools` gates

## Tests
- For each converted site with a behavioural consequence, a test that the typed error is raised on the
  failure input — not merely that the code imports.
- **The no-fabrication test:** a function whose metadata is missing returns NaN or raises, and
  **never** a plausible default (this is the `test_pixel_size` / `test_no_silent_scientific_gates`
  contract, extended to the newly converted paths).
- Degraded fallbacks set `ValidationLevel` so the caller can detect them.
- Lower the `toolbox` ratchet; assert it holds.

## Steps
1. Inventory `toolbox/` handlers into Tier 1/2/3 (a scratch list, not a committed artifact).
2. Convert Tier 1 by module, in the priority order above, running `pytest -m core` per module.
3. Annotate Tier 2 handlers encountered with `# broad-ok: <reason>`.
4. Add typed-error tests for converted sites with behavioural consequences.
5. Lower the `toolbox` ratchet to the achieved count.
6. Full `pytest -m core` green.
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG reporting the before/after
   count and which modules were converted.

## Definition of done
- ≥60 Tier-1 handlers in `toolbox/` raise typed errors with narrowed catches and `from exc`.
- Legitimate broad handlers encountered are annotated with reasons.
- Genuine fallbacks are explicit and recorded via `ValidationLevel`, never silent.
- Typed-error tests exist for converted sites with behavioural consequences.
- `toolbox` ratchet lowered and holding; full `pytest -m core` green.

## Cautions
- **Convert by consequence, not by count.** A lower number achieved by converting Qt-teardown handlers
  is a worse outcome than a higher number with every scientific path typed.
- **Narrow the catch** as well as raising typed — leaving `except Exception` while raising a typed
  error still hides unrelated bugs.
- Always `raise ... from exc`; a typed error without its cause is harder to debug than what it replaced.
- Do not convert a handler into a raise where the current fallback is genuinely correct — make it
  explicit and record it via `ValidationLevel` instead.
- One module per commit. A 60-site sweep in one commit is un-bisectable if a test starts failing.
