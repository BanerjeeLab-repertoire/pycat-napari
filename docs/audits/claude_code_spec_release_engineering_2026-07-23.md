# Claude Code spec — Release-engineering hardening

> **◐ STATUS — Parts A + B + D DONE (A/D 1.6.305, B 1.6.306); Part C remains.**
> **Part A — DONE.** Ran the real correctness selection: **65 findings** (NOT near-zero as the spec expected)
> — 62 F811 redundant re-imports + 3 B023 closures (the B023 in the 1.6.295 `parse_ome_channels_and_instrument`).
> Drove all to zero (redundant imports removed; one cross-module re-import verified same-object before removal;
> `_pick` binds its loop vars), then removed `|| true` from the correctness step in `core.yml` — it is now
> BLOCKING. F841 + style stay advisory.
> **Part D — DONE.** `Development Status :: 4 - Beta`.
> **Part B — DONE.** `pythonpath = ["src"]` (1.6.305) + the **`wheel` CI job** (1.6.306) that builds the wheel,
> installs it `--no-deps` (not editable, not src/), and runs the same `-m core` suite against the artifact
> (`PYCAT_ALLOW_INSTALLED=1` for the working-tree-guard escape hatch, `-o pythonpath=` so `import pycat`
> resolves to the wheel not src/). Verified locally before landing: installed the wheel to an isolated dir,
> confirmed `pycat` imports from it, ran the FULL `-m core` — 1816 passed, identical to the source lane. The
> source and wheel lanes must agree.
> **Part C (finer marker tiers: core/base/gui/…) remains** — a large re-marking that is its own increment;
> the recent minimal-lane failures (1.6.303) are exactly what a true minimal `core` tier formalises.

**Date:** 2026-07-23 · **Target tree:** 1.6.297 · Verified against the 1.6.297 tree. The CI/config
cluster from the engineering audit, still largely unaddressed. Individually small; collectively they are
the gap between a rigorous *test suite* (which PyCAT has — 256 files, 44k lines) and a rigorous *release
process* (which it does not yet).

No scientific behaviour changes. Each part is independently shippable.

---

## Part A — Make correctness lint blocking
**Verified:** `.github/workflows/core.yml` still contains **5** `|| true` suppressions, including the
correctness selection (`F821` undefined name, `F823` use-before-assignment, `F601`, `F811` redefinition,
`B006` mutable default, `B023` closure capture, `B904` exception chaining).

The repo's own comment says to delete `|| true` *"once this step is observed green in a real run."* That
condition can now be met.

- Run the correctness selection; fix any real findings (the AST guards
  `test_no_undefined_names`/`test_conditional_imports` already cover these classes, so the count should
  be at or near zero).
- **Remove `|| true` from the correctness step** so it blocks.
- **Keep** `|| true` on style/F841 (unused locals) — those need individual review, per the existing
  comment. Split the job: *correctness → blocking*, *style → advisory*.

These rules have caught shipped defects in this codebase before; leaving them advisory means the
canonical implementation is watching but not stopping anything.

---

## Part B — Test invocation and the install route
**Verified:** no `pythonpath` in `[tool.pytest.ini_options]`, and CI installs packages individually
rather than via the declared extra (this is what produced the `qtbot` collection errors).

Adopt both halves:
- Add `pythonpath = ["src"]` so `pytest` runs from a fresh clone without `PYTHONPATH=src`.
- **Keep a separate CI job that installs the built wheel** and runs against the installed package — so
  the `pythonpath` convenience cannot hide a packaging error.
- Make CI install what the project declares (`pip install -e ".[test]"`), or, if the lean install is
  deliberate, split into two explicit lanes: a minimal lane (base deps, `-m core`) and a full lane
  (`.[test]` including pytest-qt, `-m integration`). Two lanes, two honest contracts.
- Document `pip install -e ".[test]"; pytest` as the canonical contributor workflow.

---

## Part C — Precise test markers
**Verified:** only `core` / `integration` / `benchmark` are declared, and `core` is not minimal — it
pulls the full scientific stack. The declared contract says *"core: pure scientific kernels - no napari,
no Qt, no GPU. Must pass headlessly."*

Split into the audit's contract so compatibility testing becomes diagnostic:
- `core` — pure/headless, **minimal dependencies**; audit current `core`-marked tests and move any that
  import the heavy scientific stack at module scope to `base`
- `base` — requires all declared base deps (the current de-facto `core`)
- `gui` — Qt/napari · `integration` — multi-subsystem · `optional` — an extra · `slow` · `gpu`

**Re-marking defines CI behaviour** — move a test to a stricter tier only when it genuinely needs those
deps, and verify in a minimal env rather than guessing. A true minimal `core` is what you run first
under a new interpreter, which is exactly what the 3.13 investigation needed and did not have.

---

## Part D — The Development Status classifier
**Verified:** still `Development Status :: 5 - Production/Stable`.

Change to `4 - Beta` while the install matrix, Python-version support, and release pipeline are still
stabilising — the 3.13 situation alone (a declared ceiling that could not install) argues the packaging
is not yet "stable". Purely expectation-setting; revisit when the clean-install matrix and a 3.13 lane
are green.

---

## Tests
- The correctness ruff selection runs **blocking** and green; a deliberately-introduced `F821` in a
  scratch branch fails CI (verify the gate actually bites).
- `pytest` from a fresh clone with no `PYTHONPATH` collects and runs.
- The wheel-install lane runs the suite against the installed package.
- A minimal-dependency environment runs `-m core` with **zero collection errors**.
- A correct `.[test]` install emits no `qt_api` warning.
- `test_requirements_parse` and `test_install_routes_agree` still pass.

## Steps
1. Fix any correctness-lint findings; remove `|| true` from the correctness step; split blocking vs
   advisory lint. Ship.
2. Add `pythonpath`; fix the CI install route; add the wheel-install lane; document the workflow. Ship.
3. Re-mark tests into the finer tiers; update CI lanes. Ship.
4. Classifier → Beta. (Can ride with any of the above.)
5. Full `pytest -m core` green after each.

## Definition of done
- Correctness lint blocks CI; style stays advisory.
- `pytest` works from a clone; a wheel-install lane guards packaging.
- `-m core` is genuinely minimal-dependency and collects cleanly in a lean environment.
- The classifier reflects the real state.
- Full `pytest -m core` green.

## Cautions
- **Only remove `|| true` after the selection is observed green** — introduce it blocking, not red.
- **Re-marking is behaviour-defining for CI.** Verify each move in a minimal env; do not guess from the
  test's name.
- **`pythonpath` must be backed by the wheel-install lane**, or it hides packaging errors — that is the
  explicit trade-off the audit named.
- Don't weaken the marker *definitions* to make the current markings legal; the definitions are right.
- Beta is not a regression — it is an accurate statement while the install matrix is in flux.
