# Claude Code spec — Slim the distribution: stop shipping ~25 MB per release

> **✅ STATUS — DONE, shipped in 1.6.145** (stamped 2026-07-20 from a CHANGELOG cross-reference). sdist 20.6 → 1.76 MB, wheel 2.7 → 1.83 MB; logo/`.DS_Store` cleanup; `test_distribution_size.py` ratchet.

**Date:** 2026-07-19 · **Target tree:** 1.6.133 · Verified against the 1.6.133 tree. PyPI enforces a
**total project size** quota, and PyCAT is consuming it at roughly 25 MB per release across ~130
releases. The dominant cause is documentation assets shipped inside the source distribution. Touches
`pyproject.toml` build config and the `src/pycat/icons/` directory only — **no source-code change, no
behaviour change**.

## The measured problem

### 1. `docs/` (18 MB) ships in every sdist — the dominant cost
`[tool.hatch.build.targets.sdist].include` lists `"docs"` (pyproject.toml:225–235). Contents:
```
docs/                       18M
  docs/source/_static       14M
    .../screenshots         13M   ← the single largest item
  docs/qc_gallery          1.9M
  docs/source/_static/examples 1.1M
  docs/logos               1.1M
  docs/audits              672K
```
**13 MB of screenshots is downloaded by every `pip install --no-binary` and stored by PyPI for every
release.** Documentation is published on the docs site and lives in the git repo; a user installing
the package does not need the screenshot gallery. `notebooks/` (816 K) is the same category.

### 2. Four of six icons are dead weight (1.1 MB → ~200 KB)
`[tool.hatch.build.targets.wheel.package-data]` ships `src/pycat/icons/*.png`. Verified references in
the entire tree (`src/`, `docs/`, `README.md`, `pyproject.toml`):

| file | size | references |
|---|---:|---:|
| `pycat_mark.png` | 16K | **used** (`ui_modules.py:3826`) |
| `pycat_logo_512.png` | 188K | **used** (`run_pycat.py:431, 675`) |
| `pycat_logo_1024.png` | **572K** | **0** |
| `pycat_logo-2.png` | 136K | **0** |
| `pycat_logo.png` | 108K | **0** |
| `pycat_logo_256.png` | 64K | **0** |

880 KB of unreferenced PNGs ship in **every wheel and every sdist**.

### 3. `.DS_Store` is shipping despite an exclude rule
Three exist under `src/`: `src/.DS_Store`, `src/pycat/.DS_Store`, `src/pycat/utils/.DS_Store`. The
sdist `exclude` lists `"**/.DS_Store"` (pyproject.toml:246), but the **wheel** `package-data` glob
`src/pycat/icons/*.png` and the general packaging path do not apply that exclusion. They are also
tracked in the repo, which is why they keep reappearing.

### 4. Three `.xlsx` files (88 KB) ship inside the package
`src/pycat/navigator/data/*.xlsx` — the question-tree, module-contracts, and tag-hierarchy
spreadsheets. **Verify at build time whether any code reads them.** `operation_catalog.json` and
`layer_bindings.json` in the same area ARE runtime data and must ship. If the `.xlsx` files are
curation/reference artefacts (they appear to be design inputs, not runtime data), they belong in
`docs/` or the repo only — but confirm by grepping for a reader before removing.

## The fix

### A — remove `docs/` and `notebooks/` from the sdist
Drop `"docs"` and `"notebooks"` from the sdist `include` list. Keep `README.md`, `CHANGELOG.md`,
`LICENSE`, `CONTRIBUTING.md`, `THIRD_PARTY_LICENSES.txt`, `config`, `pyproject.toml`, and `src/pycat`.

**Confirm nothing in the build or runtime reads from `docs/`** (e.g. a docs-derived data file). If some
small file under `docs/` IS needed at runtime, move that specific file into `src/pycat/` where package
data belongs — do not keep 18 MB to carry a few KB.

This alone takes the sdist from ~25 MB to ~7 MB.

### B — delete the four unreferenced icons
Remove `pycat_logo_1024.png`, `pycat_logo-2.png`, `pycat_logo.png`, `pycat_logo_256.png` from
`src/pycat/icons/`. Keep `pycat_mark.png` and `pycat_logo_512.png`.

If a large logo is wanted for the README/docs site, it belongs in `docs/logos/` (which already exists
and, after change A, no longer ships) — not inside the installed package.

**Before deleting, re-grep** for each filename across the whole repo including any `.ui`/`.qss`/JSON
resources, in case a non-Python file references one.

### C — stop shipping `.DS_Store`
- `git rm --cached` the three tracked `.DS_Store` files and add `.DS_Store` to `.gitignore` if absent.
- Add `"**/.DS_Store"` to the **wheel** exclusions as well as the sdist's.

### D — decide the `.xlsx` question deliberately
Grep for any code that opens those three spreadsheets. If nothing reads them → move to `docs/` (or
delete if superseded by `operation_catalog.json`). If something does → leave them and note why.

### E — a size ratchet so this cannot silently regrow
Add `tests/test_distribution_size.py` (mark `core`): build-config assertions that do not require an
actual build —
- assert `docs`/`notebooks` are NOT in the sdist include list;
- assert the icons directory contains only the referenced files (or: total icon bytes < 250 KB);
- assert no `.DS_Store` exists under `src/`;
- assert every file matched by `package-data` globs is either referenced in source or explicitly
  allow-listed with a reason.

Mirror the `does not GROW` idiom from `test_complexity_budget.py`. This is the durable part: without a
ratchet, assets creep back.

## Steps
1. Grep-verify: nothing references the four icons; nothing reads the `.xlsx`; nothing runtime-reads
   `docs/`.
2. Remove `docs`/`notebooks` from sdist include.
3. Delete the four unreferenced icons.
4. Untrack + gitignore `.DS_Store`; add the wheel exclusion.
5. Resolve the `.xlsx` question per D.
6. Add `tests/test_distribution_size.py`.
7. **Build locally and report the measured before/after sizes** of both wheel and sdist
   (`python -m build`, then `ls -la dist/`). The numbers are the deliverable — state them in the
   CHANGELOG.
8. Full `pytest -m core` green; verify `run-pycat` still launches (the icons are used by the launcher,
   so this is the real smoke test).
9. Ship: own version + PyPI push + commit (EXPLICIT filenames: pyproject.toml, the deleted icons,
   .gitignore, the new test, CHANGELOG).

## Definition of done
- sdist no longer contains `docs/` or `notebooks/`; measured size reported.
- Only referenced icons ship; icons total < 250 KB.
- No `.DS_Store` in the tree or artifacts.
- The `.xlsx` question is resolved deliberately and documented.
- A ratchet test prevents regrowth.
- `run-pycat` launches with correct window/menu icons.
- Full `pytest -m core` green.

## Cautions
- **Verify before deleting.** An icon referenced from a non-Python resource, or an `.xlsx` read at
  runtime, would break the app. Grep the whole repo, not just `src/**/*.py`.
- `layer_bindings.json` and `operation_catalog.json` **must keep shipping** — the pyproject comment is
  explicit that without the bindings table every dropdown silently stops autopopulating in the
  installed package while working fine in the repo. Do not touch those.
- Removing `docs/` from the sdist does not affect the published documentation site or the repo.
- `run-pycat` is the smoke test — icons are loaded by the launcher and the menu bar, so a bad deletion
  shows up immediately there rather than in `pytest`.
- This does not reclaim quota already consumed by past releases; it stops the bleeding. Deleting old
  releases is a separate, manual, irreversible task on the PyPI web UI (no API exists) — and burns
  those version numbers permanently.
