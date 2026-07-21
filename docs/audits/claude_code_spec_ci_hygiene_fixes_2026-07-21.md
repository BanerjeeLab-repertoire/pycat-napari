# Claude Code spec — CI hygiene + test-fixture correctness (audit's new findings)

> **✅ STATUS — DONE, shipped in 1.6.222.** All three fixes landed. **Fix 1:** the stale core.yml comment
> (claiming the marker "selects only the two guard files") is corrected with the MEASURED number — the core
> suite (~1,500 tests, 200+ marked files) covers **30% of pycat** (46,882 statements). Coverage is kept OFF
> in CI for a real reason (it adds ~70% to the job's runtime, 4.5→7.5 min, and nothing consumes the report —
> no service upload, no threshold), with the local command recorded; enable `--cov-report=xml` the day a
> coverage service/threshold is wired up. **Fix 2:** the two ambiguous `tifffile.imwrite` fixtures in
> `test_lazy_sources_headless.py` are pinned `photometric='minisblack'` (verified: passes under
> `-W error::DeprecationWarning`) — the future tifffile default change can no longer silently shift the plane
> layout. **Fix 3:** `from pywt import wavedecn, waverecn` moved from module scope into
> `wavelet_bg_and_noise_calculation` — importing `image_processing_tools` (and the 8 modules that transitively
> import it) no longer loads PyWavelets (verified: `pywt` absent from `sys.modules` after a fresh import).
> Behaviour-identical; full `pytest -m core` green.


**Date:** 2026-07-21 · **Target tree:** 1.6.221 · Verified against the 1.6.221 tree. Three small,
concrete, *newly-identified* issues from the latest engineering audit — distinct from the larger
release-engineering spec (which covers ruff/pythonpath/markers/classifier). These are fast, low-risk
correctness fixes worth landing on their own: a stale CI comment now blocking a useful coverage
decision, a deprecated tifffile default that will break a test on a future upgrade, and transitive
import coupling through `pywt`.

## Fix 1 — the stale core-marker CI comment (blocks a coverage decision)
**Verified:** `.github/workflows/core.yml:156-157` says *"The `core` marker currently selects only the
two guard files, so a coverage report would be near-zero and meaningless"* — and uses that to justify
**not** running coverage on the core job. But **202 test files are now `core`-marked** (VPT, metadata,
identity stamping, batch recording, brushing, biological QC, operation graphs, scientific kernels). The
comment is a fossil from when almost nothing was marked, and it is silently suppressing coverage that
would now be meaningful.

- Update the comment to describe the *actual* suite.
- **Re-decide coverage on measured evidence, not the fossil rationale.** Run `pytest -m core` once with
  `--cov=pycat --cov-report=term`, read the real number, and either enable coverage
  (`--cov=pycat --cov-report=xml`) if it is meaningful, or keep it off with a comment stating the
  *measured* coverage and a real reason — never the "two files" claim.
- If coverage is enabled, do NOT add a failing coverage *threshold* in the same change (that is a
  separate policy decision); just start reporting it.

## Fix 2 — the tifffile fixture uses a deprecated photometric default
**Verified:** `tests/test_lazy_sources_headless.py:170` writes
`tifffile.imwrite(path, np.zeros((4, 16, 16), dtype=np.uint16))` with **no `photometric`**. Current
tifffile warns that a `(4, H, W)` array with no photometric is interpreted as RGB-with-separate-planes,
and that the default will change in a future release. When it changes, this fixture's plane layout could
shift and the lazy-stack test would fail for a reason unrelated to the code under test — a latent
time-bomb in the test suite.

- Write the fixture explicitly as grayscale pages:
  `tifffile.imwrite(path, data, photometric="minisblack")` at that site (and line 128's `imwrite(path,
  truth)` if it has the same ambiguity — check its shape).
- This pins the fixture's interpretation so a tifffile upgrade cannot silently change what the test
  reads. The lazy-stack behaviour under test is unchanged; only the fixture's declared interpretation is
  made explicit.
- Verify the test still passes and the deprecation warning is gone.

## Fix 3 — move `pywt` from module scope into the function that uses it
**Verified:** `image_processing_tools.py:71` — `from pywt import wavedecn, waverecn` at **module scope**.
And **8 toolbox modules transitively import `image_processing_tools`**, so importing a feature/
segmentation/coloc/time-series module drags in PyWavelets even when the selected operation never touches
wavelets. `pywt` is a legitimate base dependency (so this is not a missing-dep bug), but the coupling
means a minimal headless import of, say, a segmentation module needs PyWavelets present.

- Move the import to **function scope** — inside the wavelet background-subtraction function(s) that
  actually use `wavedecn`/`waverecn`:
  ```python
  def wbns_func(...):
      from pywt import wavedecn, waverecn
      ...
  ```
- This reduces import cost, isolates the subsystem, produces a targeted error (only the wavelet path
  fails if `pywt` is somehow absent, not the whole module), and makes minimal scientific environments
  easier to construct — which directly helps the `core`/`base` marker separation (a truly minimal
  `core` import shouldn't need every transitive scientific dep).
- **Behaviour-preserving:** the function's output is identical; only *when* `pywt` is imported changes.
  Verify the wavelet function still runs and produces the same result.
- Check for other module-scope imports of heavy optional-ish scientific libs in the same file while
  there (the audit flags this as a pattern, not a single line) — but only move ones that are genuinely
  used in a minority of functions; do not scatter imports for libs used throughout.

## Why these three together
All three are **small, verified, low-risk hygiene** with concrete payoffs: the CI comment fix unblocks a
real coverage decision, the tifffile fix defuses a future test failure, and the `pywt` move reduces
coupling that the marker-separation work (release-engineering spec) will otherwise trip over. None
touches scientific output. They are the kind of thing that rots if not captured.

## Tests
- Fix 1: `pytest -m core` collects the full 202-file suite (not two); the coverage decision is recorded
  with a real number.
- Fix 2: the lazy-sources test passes with no tifffile photometric deprecation warning.
- Fix 3: the wavelet background function produces identical output; importing a segmentation/coloc/
  time-series module no longer imports `pywt` at module load (assert `pywt` not in `sys.modules` after
  importing the module fresh, before calling the wavelet function).
- No scientific output changes anywhere.

## Steps
1. Update the core.yml comment; run `pytest -m core --cov=pycat --cov-report=term` once; record the
   measured coverage and enable or justify accordingly.
2. Add `photometric="minisblack"` to the ambiguous `imwrite` fixture(s); confirm warning gone.
3. Move `from pywt import ...` into the wavelet function(s); verify identical output + deferred import.
4. Full `pytest -m core` green.
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (CI comment corrected + coverage
   decision; tifffile fixture pinned to minisblack; pywt import deferred to reduce coupling).

## Definition of done
- The core.yml comment reflects the real 202-file suite; the coverage decision is based on a measured
  number, not the "two files" fossil.
- The tifffile fixture declares `minisblack`; no deprecation warning; test passes.
- `pywt` imports at function scope; importing the coupled modules no longer pulls it in; wavelet output
  identical.
- No scientific output changes.
- Full `pytest -m core` green.

## Cautions
- **Don't add a coverage threshold in Fix 1** — reporting coverage and gating on it are separate
  decisions; just fix the comment and report.
- **Fix 2 is fixture-only** — the lazy-stack behaviour under test must not change; only the fixture's
  declared photometric.
- **Fix 3 is behaviour-preserving** — deferring the import must not change the wavelet result; verify
  identical output, and only defer imports genuinely used by a minority of functions.
- These are hygiene, not the big release-engineering work (ruff/pythonpath/markers/classifier) — that is
  a separate spec; don't conflate.
