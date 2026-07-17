# Documentation Audit — README accuracy & the "[dev] installs an old version" bug

**Date:** 2026-07-16 · **Tree:** 1.6.82 · Focused audit of `README.md` prompted by a user report:
following the `[dev]` instructions landed them on **PyCAT 1.5.40**. Diagnosed below (it's real), plus
the related staleness found while tracing it. Fixes are concrete; this can become a spec or a direct
README edit.

## THE REPORTED BUG — diagnosed (real)
The README has **two different "[dev]" instructions that do opposite things**:
- **Line 308** (in the optional-extras section): `pip install "pycat-napari[dev]"` — installs the
  *published PyPI package* + dev extras. **This is what put the user on an old version.**
- **Line 728** (in the Development section): `pip install -e ".[dev]"` — the correct
  *editable-from-source* install.

**Why line 308 yields 1.5.40 (the mechanism):** `pip install "pycat-napari[dev]"` asks pip to resolve
the newest published `pycat-napari` whose `[dev]` extra's dependency set is satisfiable in the user's
environment. Because 1.6.x moved to modern `zarr`/`tifffile`/`fsspec`/`bioio` pins (the README itself
explains this at line 82, "Why you cannot just `pip install --upgrade`"), pip **silently backtracks to
an old release (1.5.40) whose looser pins resolve** rather than failing — so the user gets a
years-old version with no error. The README's own line-82 warning describes exactly this hazard but the
line-308 command walks straight into it.

**Fix:**
1. In the extras section (line ~305–325), do NOT present `pip install "pycat-napari[dev]"` as a
   standalone dev-setup step. Reframe it as "extras you can ADD to an existing current install," and
   pin it: `pip install "pycat-napari[dev]==<current>"` OR (better) point dev users to the
   from-source editable install (line 728) as the canonical dev path. The bare unpinned extras install
   is the trap.
2. Add a one-line warning next to any bare `pip install "pycat-napari[...]"`: "if pip installs an old
   1.5.x version, it backtracked past the modern pins — use a pinned version or the editable install."
3. Verify the other bare-extra installs (lines 311/314/317/320) have the same backtracking risk and
   pin or warn them the same way.

## RELATED STALENESS (found while tracing the bug)

### 1. Hardcoded stale version string
Line 877: `Current Version: 1.5.357` — the tree is **1.6.82**. A hardcoded version in the README always
rots. **Fix:** remove the hardcoded number (point to PyPI / the badge / `pip show pycat-napari`), or
make it a CI-substituted token — never a manually-maintained literal.

### 2. The Development "Setup" step 2 is BLANK
Lines 718–722: step 2 "Create development environment" has `# Windows` and `# Mac M1/ARM` headers with
**no commands under them**, then a `mamba activate pycat-napari-env` for an env that **was never
created** (no `conda create`/`mamba create` line anywhere in the Development section). A new contributor
following this cannot set up. **Fix:** fill in the env-creation commands (mirror the working
`conda create -n pycat-env python=3.12` from the install section) and use a consistent env name — the
Development section invents `pycat-napari-env` while the rest of the README uses `pycat-env`/`pycat-16`.

### 3. Wrong test command
Line 736: `pytest --cov=pycat_napari tests/` — the package dir is **`pycat`**, not `pycat_napari`
(`src/pycat/`), and `pyproject.toml:273` already sets `addopts = "-v --cov=pycat ..."`. So the
documented command double-specifies the wrong coverage target. **Fix:** `pytest` (the addopts already
handle cov), or `pytest --cov=pycat tests/` if explicit.

### 4. Env-name inconsistency across the README
`pycat-16` (line 98), `pycat-env` (line 218), `pycat-napari-env` (line 722) all appear for the same
purpose. Not wrong, but confusing. **Fix:** pick one canonical dev env name and use it throughout, or
explicitly note these are examples.

## What's ACCURATE (don't touch)
- The Python 3.12 requirement + the 3.9-dropped-at-1.5.39 note (line 64) is correct.
- The "Why you cannot just `pip install --upgrade`" / 1.5.x→1.6 migration section (line 82) is correct
  and valuable — it's the same mechanism behind the reported bug; it just needs to be cross-referenced
  from the `[dev]` command.
- The extras list itself (gpu/stardist/trackmate/devbio-napari/arm-mac) is accurate; only the
  *unpinned* install pattern is the hazard.
- The torch/cu118 GPU install line (108/289) matches the environment.

## Steps (if done as a spec)
1. Fix the `[dev]` trap: reframe line 308 as an add-on-to-current or point to the editable install;
   pin or warn on all bare `[extra]` installs.
2. Remove the hardcoded `1.5.357` (line 877) and the stale `1.5.40` pathway.
3. Fill the blank Development step-2 env creation; unify the env name.
4. Fix the `--cov=pycat_napari` → `pycat` test command.
5. Add a test guard if practical: a docs-lint that greps README for a hardcoded `Current Version:`
   literal or a bare `pip install "pycat-napari[` without a pin/warning, so this can't silently rot
   again. (Model on the existing `test_install_routes_agree.py` / `test_no_stale_reader_names.py`
   doc-contract tests.)
6. This is a DOCS-ONLY change → per the versioning rule it does NOT get its own version bump; it rides
   forward in the next code change's commit.

## Definition of done
- No README path leads to an old 1.5.x install without an explicit warning/pin; the canonical dev
  install is the editable from-source one.
- No hardcoded version literal in the README.
- The Development section is followable end-to-end (env creation present, consistent name, correct
  test command).
- Optionally: a docs-lint guard prevents re-rotting.

## Cautions
- The core hazard is pip's silent backtracking past modern pins — the fix is to PIN or steer to
  editable, not just to reword. A bare `pip install "pycat-napari[dev]"` will re-trap the next user
  even with nicer prose around it.
- Docs-only: no version bump; folds into the next code commit (per the delivery rule).
- Don't delete the valuable 1.5→1.6 migration explainer — cross-reference it, it's the same root cause.
