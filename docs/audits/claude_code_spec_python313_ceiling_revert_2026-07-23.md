# Claude Code spec — Stop declaring Python 3.13 support that does not work

> **● STATUS — DONE, shipped 1.6.302.** `requires-python` reverted to `">=3.12,<3.13"` with the explanatory
> comment (real reason + the STALE-pin finding + the upstream unblock pointer). `installation.rst` corrected
> (it wrongly claimed "supported range 3.12–3.13") and now links `known_issues` via `:doc:`; `known_issues.md`
> updated to `<3.13` with an explicit **Re-enable procedure** (bump ceiling → add classifier → re-verify
> segmentation on real data → add a 3.13 CI lane). New `tests/test_python_version_ceiling.py` (`core`, 2)
> asserts `requires-python` and the `Programming Language :: Python ::` classifiers AGREE in both directions
> (every permitted minor advertised; nothing advertised outside the ceiling) — the guard that would have
> caught the original state — plus a focused pin that 3.13 stays excluded until the upstream cellpose/numpy pin
> relaxes. `test_requirements_parse` / `test_install_routes_agree` still pass. Full core green.

**Date:** 2026-07-23 · **Target tree:** 1.6.297 · Verified against the 1.6.297 tree. One-line
correctness fix with a documentation follow-through. PyCAT currently **permits an interpreter it cannot
install on**.

## The inconsistency (verified)
```toml
requires-python = ">=3.12,<3.14"     # line 10 — permits 3.13
...
"Programming Language :: Python :: 3.12",   # line 35 — 3.13 NOT advertised
```
No `Programming Language :: Python :: 3.13` classifier exists (grep for `3.13` in pyproject → 0 hits).
So the metadata permits 3.13 while declining to advertise it — internally inconsistent on its own terms.

More importantly, **the permission is wrong**: per the investigation recorded in
`docs/source/known_issues.md`, a plain `pip install pycat-napari` on Python 3.13 **fails**. `cellpose<4`
is a base dependency, cellpose 3 declares `numpy<2.1`, numpy ships no cp313 wheels below 2.1, so pip
resolves to numpy 2.0.2, attempts a source build, and dies for want of a C compiler.

A user on 3.13 therefore gets an install failure with a confusing numpy/meson traceback — with nothing
pointing at the actual cause.

## The fix

### 1. Revert the ceiling
```toml
requires-python = ">=3.12,<3.13"
```
pip then refuses cleanly with *"Package 'pycat-napari' requires a different Python"* — an immediately
actionable message — instead of a source-build failure three layers down.

**Declaring support that fails at install is worse than not declaring it.** A clean refusal respects the
user's time; a meson compiler error does not.

### 2. Point at the explanation
Add a short comment beside the ceiling giving the real reason and the unblock condition, so nobody
re-derives it:
```toml
# 3.13 is BLOCKED UPSTREAM, not by PyCAT's own code (which compiles and passes core on 3.13).
# cellpose 3 declares numpy<2.1; numpy has no cp313 wheels below 2.1. We pin cellpose<4 because
# Cellpose 4 removed the cyto2 CNN models. Verified 2026-07-23: the cellpose pin is STALE (cellpose 3
# segments byte-identically on numpy 2.3.5), so the unblock is upstream relaxing it —
# see docs/source/known_issues.md and MouseLand/cellpose#1095.
requires-python = ">=3.12,<3.13"
```

### 3. Surface it where a 3.13 user will look
Link `known_issues.md` from `docs/source/installation.rst` — a one-line "Python 3.13 is not yet
supported; see Known Issues for why and for the unblock condition." The doc already exists and is in the
toctree; it just needs to be reachable from where someone hits the problem.

### 4. Note the re-enable procedure
In `known_issues.md`, state plainly what to do when upstream relaxes the pin: bump the ceiling to
`<3.14`, add the 3.13 classifier, **re-run the byte-identical segmentation verification on real data**,
and add a 3.13 CI lane. The verification is not optional — the current evidence is one image, one model,
CPU.

## What NOT to do
- **Do not relax `cellpose<4`** to escape the numpy pin. That trades a packaging problem for a
  scientific one: different segmentation model, invalidated baselines, and SAM is unusably slow on the
  lab's GPU-less machines.
- **Do not add the 3.13 classifier** while the ceiling is `<3.13` — that would restore the same
  inconsistency in the opposite direction.
- **Do not ship a `--no-deps` workaround.** The probe environment is not reproducible by a user.

## Tests
- A test asserts `requires-python` and the `Programming Language :: Python ::` classifiers **agree** —
  every permitted minor version is advertised, and no advertised version is outside the ceiling. This is
  the guard that would have caught the current state, and it prevents the pair drifting again.
- `docs/source/installation.rst` links `known_issues`.
- Existing dependency/metadata tests (`test_requirements_parse`, `test_install_routes_agree`) still pass.

## Steps
1. Set `requires-python = ">=3.12,<3.13"` with the explanatory comment.
2. Link known-issues from the installation docs; add the re-enable procedure to the doc.
3. Add the ceiling/classifier agreement test.
4. Full `pytest -m core` green.
5. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (3.13 is not yet installable —
   the declared ceiling now matches reality; reason and unblock condition documented).

## Definition of done
- `requires-python` matches what actually installs; a 3.13 user gets a clean, actionable refusal.
- The ceiling carries its real reason and a pointer to the evidence.
- Installation docs link the known-issues record.
- A test keeps the ceiling and the classifiers in agreement.
- Full `pytest -m core` green.

## Cautions
- **This is a deliberate step back, not a regression.** The ceiling was moved to `<3.14` optimistically;
  the investigation showed 3.13 cannot install. Reverting is the honest state.
- Keep the reasoning in the file — the previous stale comments survived precisely because the prose
  sounded authoritative while being wrong.
- Re-enabling later requires re-verification on real data, not just flipping the number back.
