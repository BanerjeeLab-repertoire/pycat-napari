# PyCAT — Specs for the Health-Audit Findings (v1.6.422)

*Covers the findings from the codebase health audit. Two corrections to that audit are recorded up
front: one finding is **more severe** than I reported, and one recommendation is **withdrawn** because
it contradicts an existing, well-reasoned, documented decision in the repo that I had not read when I
made it.*

---

## Corrections to the health audit

### ✏️ Correction 1 — H1 is worse than "prints and exits"

I described `fix_tifffile.py` as running `sys.exit()` as an import side effect, with "low real-world
impact." The `sys.exit` part is right; the impact assessment was too soft. Reading the module-scope
statements: importing `pycat.fix_tifffile` on a machine with an unpatched tifffile **rewrites
`site-packages/tifffile/tifffile.py` and writes a `.bak` beside it** (lines 62–68):

```python
backup = tifffile_path.with_suffix(".py.bak")
backup.write_text(content, encoding="utf-8")     # ← writes to site-packages
fixed = content.replace(OLD, NEW)
tifffile_path.write_text(fixed, encoding="utf-8") # ← MUTATES a third-party library
```

So the hazard isn't "a tool that walks the package dies" — it's "a tool that walks the package
**silently monkey-patches an installed dependency on disk**." Still nothing in PyCAT imports it, so
normal operation is unaffected, but the severity of the failure mode is higher than I stated.

### ✏️ Correction 2 — Health-audit Finding 3 (mass `ruff --fix`) is WITHDRAWN

I recommended "a one-time `ruff check src --fix` lint-hygiene commit" to cut the 3219 findings. That
recommendation was made without reading `pyproject.toml`'s ruff rationale, which says the opposite —
deliberately:

> *"The rest of the ~2,390 Ruff findings are style/maintainability and **should NOT be auto-fixed en
> masse** — a global `--fix` over a 7,000-line `file_io.py` is exactly how a working codebase gets
> broken."*

And CI already implements a three-tier posture that matches it: **blocking** correctness subset (driven
to zero at 1.6.305), **advisory** F841 with GitHub annotations, **advisory** full style, neither failing
the build. That is a mature, considered position and it is correct. My recommendation would have
generated exactly the churn the comment warns about — and would have been actively dangerous for the
749 F401 findings, which are the deliberate re-export shims (`ui/ui_modules.py` alone has 101; ruff
marks F401 fixes *unsafe* for good reason).

**No mass-lint spec follows.** What replaces it is H2 below — the *individual triage* of F841 that the
CI comment already says is the intended path ("does not fail the build until triaged").

---

# H1 — Make `fix_tifffile.py` import-safe

**Severity:** real hygiene bug, low likelihood, high blast radius if triggered.
**Effort:** ~20 minutes including the guard test.

## Problem

`src/pycat/fix_tifffile.py` is a **standalone maintenance script** — its own docstring says
`Usage: python fix_tifffile.py`, and `file_io/file_io.py:407,413` tell the user to run it — but it lives
inside the importable package with **no `if __name__ == "__main__":` guard**. Every statement is at
module scope, so importing it: prints diagnostics, calls `sys.exit(0)` or `sys.exit(1)`, and (on an
unpatched tifffile) **rewrites `site-packages/tifffile/tifffile.py`**.

Anything that imports the package tree — `pkgutil.walk_packages` discovery, doc generators, IDE
indexers, a coverage sweep — triggers it. It is the one place in an otherwise import-safe package
(274 modules, zero real import failures) where merely importing a module terminates the process and
mutates a dependency.

## Spec

### H1a. Move it out of the package (preferred)

The cleanest fix is that a maintenance script does not belong in the shipped package namespace.

1. `git mv src/pycat/fix_tifffile.py scripts/fix_tifffile.py`.
2. Wrap the body in a `main()` with a `__main__` guard anyway (a script that can't be imported safely is
   still a latent trap wherever it lives):
   ```python
   def main() -> int:
       """Patch the installed tifffile for NumPy 2.0. Returns a process exit code."""
       ...  # existing body, with `return 1` / `return 0` in place of sys.exit(...)

   if __name__ == "__main__":
       raise SystemExit(main())
   ```
   Converting `sys.exit(n)` → `return n` also makes the script testable.
3. Update the two user-facing strings in `file_io/file_io.py:407,413` from
   `"Run 'python fix_tifffile.py' …"` to the new path (`python scripts/fix_tifffile.py`).
4. Check `MANIFEST.in` / `pyproject.toml` packaging so the move doesn't silently drop it from the sdist
   if you want it shipped; `scripts/` already exists in the repo root.

**Alternative (H1b), if it must stay importable:** keep it at `src/pycat/fix_tifffile.py`, apply step 2
only, and leave the help strings alone (`python -m pycat.fix_tifffile` then works). This is strictly
worse — the module still sits in the namespace inviting a stray import — but it is a one-file change.

### H1c. Fix the stale premise while you're in there *(this is health-audit Finding 2a)*

The docstring asserts:
> *"PyCAT requires numpy<2.0 by default. If you intentionally installed NumPy 2.0 …"*

That is no longer true: `pyproject.toml` now declares **`numpy>=1.22` unpinned** (the `numpy<2.0`
constraint existed for aicsimageio, which has been removed; the code was audited for numpy-2 removals).
Rewrite the docstring to state the current situation — numpy 2.x is supported, this script exists only
for the case where an installed `tifffile` predates the `newbyteorder` removal — so the next reader
isn't told a constraint that no longer exists.

### H1d. Guard test — prevent recurrence

This codebase's culture is that a fixed bug class gets a ratchet (`test_complexity_budget`, the
dependency guard, the marker guard). Import safety deserves the same. Add to
`tests/test_ci_dependencies.py`, which already hosts the AST import guards
(`test_no_undeclared_module_scope_imports`, `test_no_undeclared_unguarded_lazy_import`). Mark it
`core` — pure `ast`/`pathlib`, no heavy imports:

```python
def test_no_package_module_exits_or_writes_at_import():
    """Importing any pycat module must be side-effect free.

    `fix_tifffile.py` was a standalone script living in the package with no __main__
    guard: importing it called sys.exit() AND rewrote site-packages/tifffile/tifffile.py.
    Anything that walks the package (pkgutil discovery, doc builds, IDE indexers) hit it.
    A module that needs to exit or write is a SCRIPT -- give it a main() + __main__ guard,
    or move it to scripts/.
    """
    BANNED_CALLS = {"exit", "_exit"}                    # sys.exit, os._exit
    BANNED_METHODS = {"write_text", "write_bytes", "unlink", "rmtree", "mkdir"}
    offenders = []
    for f in (SRC / "pycat").rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        for node in tree.body:                          # MODULE SCOPE ONLY
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                name = getattr(fn, "attr", getattr(fn, "id", ""))
                if name in BANNED_CALLS or name in BANNED_METHODS:
                    offenders.append(f"{f.relative_to(SRC)}:{sub.lineno} calls {name}() at import")
    assert not offenders, (
        "Modules with side effects at import:\n" + "\n".join(offenders) +
        "\n\nGive these a main() + `if __name__ == '__main__':` guard, or move them to scripts/."
    )
```

Scope it to module scope only (`tree.body`, then walk each top-level statement) so a `write_text` inside
a function — which is normal and fine — doesn't trip it. If any legitimate module trips it, add an
explicit allowlist entry with a comment, the same grandfathering pattern `test_complexity_budget` uses.

### H1e. Verification

```bash
python -c "import pkgutil, importlib, pycat; [importlib.import_module(m.name) for m in pkgutil.walk_packages(pycat.__path__, 'pycat.')]"
```
Must complete without exiting. (This is the sweep that died before the fix — with `fix_tifffile`
excluded it reported 274 OK / 0 real failures, so this becomes a clean 275.)

---

# H2 — Triage the 31 F841 unused locals

**Severity:** low individually; the *class* matters because CI already flags it and says the triage is
outstanding. **Effort:** an afternoon, or spread across whatever files you touch.

## Problem, framed correctly

CI runs `ruff check src --select F841 --output-format=github || true` and its comment states the intent
precisely — this is the framing the spec must preserve:

> *"they must be reviewed individually rather than deleted. Several sit in stateful loading and
> time-series code (`from_meta`, `is_lazy`, `ndim`, `strategy_dd`, `otsu_classes_spin`), where an unused
> local may be the RESIDUE of logic that was partially removed while downstream code still behaves as if
> it existed. **The question for each is not 'is it used?' but 'was something meant to use it?'.**
> Reported, never auto-fixed, and does not fail the build until triaged."*

So the deliverable is **not** deletion. It is a decision per site, recorded.

## A worked example that proves the point — and ties to the scale-bar bug

`file_io/napari_adapter.py:209` inside `_enable_auto_scale_bar`:
```python
from_meta = bool(dr.get('pixel_size_from_metadata', False))   # ← F841, never used
mpx_sq = dr.get('microns_per_pixel_sq', 1)
...
if _np.isfinite(px) and px > 0 and _is_calibrated(central_manager, px):   # gate uses _is_calibrated
```
The function's own docstring says *"Real metadata pixel size → µm bar. No metadata → pixel bar."* —
i.e. `from_meta` **was** the intended gate, and was superseded by `_is_calibrated(...)`. That is exactly
"the residue of half-removed logic," and it sits in the very function that produced the µm→px scale-bar
regression. Whether `from_meta` should be deleted or should still participate in the gate is a real
question about intent, not a lint cleanup. **Answer it as part of the scale-bar fix.**

## Spec

### H2a. Triage procedure — one pass, three outcomes

For each of the 31 sites, record one of:

- **DEAD** — the value was superseded and nothing downstream depends on it → delete the assignment, and
  add a one-line comment at the call site only if the deletion is non-obvious.
- **INTENDED** — something *was* meant to use it (a gate, an output column, a returned field) → wire it
  up. This is a behaviour change and needs a test.
- **KEEP** — deliberately computed for its side effect, or a documented placeholder → keep it and add
  `# noqa: F841` **with a reason**, so the next triage doesn't re-litigate it.

### H2b. Priority order — science-bearing sites first

These sit in analysis code where an unused local could mean a lost output:

| Site | Local | Question to answer |
|---|---|---|
| `file_io/napari_adapter.py:209` | `from_meta` | Superseded by `_is_calibrated`, or should it still gate? *(fold into the scale-bar fix)* |
| `toolbox/invitro/size_distribution.py:101` | `ll_pl` | The Vuong-style LR test recomputes the power-law log-density (`lp_pl`) analytically instead of using the stored `pm['loglik']`. Fresh computation is correct → almost certainly **DEAD**, but confirm the two agree before deleting (if they disagree, that's a finding). |
| `toolbox/coloc/object_based.py:1126` | `inside` | `np.argwhere(roi)` in the randomisation null — was the null meant to place objects at `inside` positions? |
| `toolbox/coloc/nulls.py:178` | `num_blocks` | Loop uses `np.ndindex(*shape)`; `num_blocks` looks like leftover from a different block-iteration scheme. |
| `toolbox/invitro/analysis.py:274` | `base_x_mid` | In the contact-angle geometry — check it isn't a dropped centre-line term. |
| `toolbox/dynamic_spatial_tools.py:418` | `n_e`, `n_s` | Event/split counts — were they meant to reach the summary? |
| `toolbox/condensate_physics/moduli.py:249` | `N` | Length var; likely **DEAD**. |
| `toolbox/frap_tools.py:455` | `f0` | Central-difference Hessian genuinely doesn't need `f0` → **DEAD** (verified during an earlier audit). |

Then the loading/UI sites the CI comment names (`is_lazy`, `ndim`, `strategy_dd`, `otsu_classes_spin`,
`default_checked_layers`, `layer`, `sel`, `sig`, `idx`, `rr_cc`, the loop `e`s), which are lower risk.

### H2c. Ratchet, don't gate

Do **not** make F841 blocking in one step — that would force all 31 decisions at once, which is exactly
the churn the repo's lint policy avoids. Instead add a ratchet in the spirit of
`test_complexity_budget`:

```python
_F841_BUDGET = 31   # today's count. It may only go DOWN. Lower it as sites are triaged.

def test_unused_locals_do_not_GROW():
    """F841 is 'residue of half-removed logic'. Existing sites are grandfathered pending
    individual triage (see the CI comment); a NEW one is a fresh half-removal and must be
    resolved in the change that introduced it."""
    count = <run ruff --select F841 --output-format=json, len(results)>
    assert count <= _F841_BUDGET, f"New unused locals: {count} > {_F841_BUDGET}"
```
Lower `_F841_BUDGET` with each triage commit. When it reaches 0, promote F841 into the blocking CI
selection and delete the ratchet — the same arc the blocking subset already went through at 1.6.305.

---

# H3 — The 8 unclassified test files *(optional, tidy-up)*

**Finding:** 8 test files carry no marker and no `importorskip`
(`test_ui_structure`, `test_materialize_stack`, `test_vpt_parallel_equivalence`, `test_central_manager`,
`test_tifffile_zarr_shim`, `test_import`, `test_general_utils`, `test_navigator`).

**They are safe** — I checked each: none imports pandas/scipy/skimage at module scope, so they run in
every lane, and `test_heavy_import_tests_are_marked` correctly does not flag them.

**Spec (low priority):** add `pytestmark = pytest.mark.core` to each so classification is explicit rather
than inferred, matching the convention in the other 127 core files. Verify each genuinely passes in the
minimal lane (numpy + pytest only) before marking it `core` — if any needs more, mark it `base` instead.
No guard needed; the existing marker guard covers the dangerous case.

---

# What is NOT specced, and why

| Health-audit item | Disposition |
|---|---|
| **Mass `ruff --fix` (Finding 3)** | **Withdrawn.** Contradicts the documented policy in `pyproject.toml` and would be unsafe on the 749 F401 re-export shims. See Correction 2. |
| 1496 E702 / 615 I001 / 207 E402 / 114 E741 | Style, advisory in CI by design. The E402/F401 sites in shims already carry `# noqa: E402,F401`. Leave them. |
| 26 `except Exception: pass` | Spot-checked, confined to Qt/matplotlib teardown. Not worth a sweep. **Exception:** the two in the scale-bar path *are* being fixed — as part of Spec A of the scale-bar work, not here. |
| 3 TODO/FIXME markers | Nothing to do at this density. |
| Decomposition (`analysis_plots.py`, `analysis_methods_ui.py`) | Covered by the separate decomposition audit; the complexity ratchet already budgets them. |

---

## Delivery order

| # | Change | Notes |
|---|---|---|
| 1 | **H1a + H1c + H1d** — move/guard `fix_tifffile.py`, fix its stale docstring, add the import-safety ratchet | One coherent commit. ~20 min. The only finding with a genuine hazard. |
| 2 | **H2b (science-bearing subset)** — triage `from_meta`, `ll_pl`, `inside`, `num_blocks`, `base_x_mid`, `n_e/n_s` | `from_meta` folds into the scale-bar fix; the rest can go together. Each decision recorded per H2a. |
| 3 | **H2c** — add the F841 ratchet at today's count | Cheap; stops the class growing while triage proceeds. |
| 4 | **H2b (remainder)** + **H3** | Opportunistic — fold into whatever change touches those files. |

Per the standing ritual each code change gets its own version bump + PyPI push + commit; H1's three
parts are one change, and the F841 triage can be batched per file group.

**Bottom line:** the health audit found one real bug (H1, more severe than I first said), one
already-planned triage that just needs a procedure and a ratchet (H2), one tidy-up (H3) — and one
recommendation of mine that was wrong and is withdrawn. The codebase's own lint governance was ahead of
my advice on that last point.
