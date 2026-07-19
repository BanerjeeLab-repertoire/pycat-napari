# Claude Code spec — Tag registry: fix the TEST discovery defect + propagate `target` to layers

**Date:** 2026-07-18 · **Target tree:** 1.6.121 · Verified against the 1.6.121 tree. An external audit
reported four failing tag/resolver tests and concluded the operation registry had *regressed* — that
canonical operations were "missing," that duplicate registration was "not rejected," and that "only 42
of ~100 operations register." **Three of those four findings are artefacts of a defect in the TEST, not
the product.** This spec fixes the real defect (a stale hardcoded module list in the test) and the one
genuine product gap (`target` not reaching the layer).

## What was actually verified (correcting the audit)

| Audit claim | Verified reality |
|---|---|
| "Only 42 operations register; the toolbox is outside the vocabulary" | **85 `@tags_layer` decorators exist; the catalog holds 79.** The test counts 42 because *the test's own loader* imports a stale hardcoded list of 11 modules and misses 7. |
| "Canonical operations are missing, at minimum `log`" | **All ten named ops are present** — `log`, `clahe`, `dog`, `rolling_ball`, `watershed`, `cellpose`, `mask_merge`, `hand_drawn`, `bandpass`, `invert`. `log` is declared in `image_processing_tools` with alias `laplacian_of_gaussian`. |
| "Duplicate registration is not rejected — DID NOT RAISE `TagCollision`" | `register_operation` **does** raise `TagCollision` (`tag_registry.py:111`). The test registers an impostor `'watershed'` — but `watershed` lives in `segmentation_tools`, one of the modules the stale list fails to import, so nothing was registered to collide *with*. **Same root cause.** |
| "Cellpose outputs do not carry `target=cell`" | **This one is real.** The catalog declares `cellpose → target: 'cell'`, but the tag hook never copies `target` onto the layer. |

**Root cause of findings 1–3: one stale list.** `tests/test_tag_registry.py::_load_tagged_modules`
hardcodes 11 module names inside `try: … except Exception: pass`. 18 modules actually bear
`@tags_layer`. The missing 7 include `batch_roi_tools`, `layer_tags`, `layer_tag_hook`, `op_catalog`,
`entity_ref`, `operation_spec`, `tag_registry`. Meanwhile `operation_spec._populate_registry()` already
does this **correctly** via AST-based discovery (`_discover_tag_modules()`). The test reimplemented
discovery, badly, and then silently swallowed the failures.

## Fix 1 — the test must use the real discovery mechanism (not its own stale list)
Replace `_load_tagged_modules()`'s hardcoded list with the existing, correct discovery:
```python
from pycat.navigator.operation_spec import _populate_registry
skipped = _populate_registry()      # AST-discovers every @tags_layer module
```
- **Do not keep the bare `except Exception: pass`.** That swallow is what turned a discovery failure
  into a misleading "the registry is nearly empty" signal. `_populate_registry` already returns a
  `skipped` list of `(module, exc)`; the test should **assert it is empty**, or fail naming the module
  and exception. A module that cannot be imported must be a loud failure, not a quiet undercount.
- Raise the `>= 50` floor to a ratchet at the real number (85 today, or the count `_populate_registry`
  yields) so a genuine future regression fails, using the downward-only idiom from
  `test_complexity_budget.py`.
- Audit the rest of `tests/test_tag_registry.py` and `test_tag_resolver.py` for the same
  reimplemented-loader pattern and route them all through `_populate_registry`.

**One discovery mechanism, used everywhere.** Two loaders is exactly the drift this whole effort exists
to remove — and here it produced a false regression report that cost real review time.

## Fix 2 — propagate declared `target` (and the other declared semantics) to the layer
The genuine gap. `layer_tag_hook` always sets `role` (`_role_from`, `:96`) but never copies the
operation's declared `target`. So a Cellpose layer is `{role: labels, layer_type: labels}` with no
`target: cell`, and a resolver query for `role=labels, target=cell` fails **even though a Cellpose
layer is right there** — the "last inch" problem.

Fix: when a layer is produced by a known operation, copy the operation's declared output semantics onto
the layer from the **registry entry** (the single source of truth), not from a second inference path:
- `target` (the audit's case), and any other declared field the registry carries for the output
  (`produces` where it differs from `role`).
- Look the operation up by the `__pycat_op__` the hook already has access to; if the op is unknown,
  behave exactly as today (role-only) — no guessing.
- Do NOT let the hook's inferred `role` overwrite a *declared* `produces`; declared beats inferred.

**Acceptance test** (the one the audit rightly asks for — end-to-end, not unit):
```
execute operation → layer created → tags attached → resolver finds it
```
for `cellpose` (target=cell), `watershed`, `log`, `dog`, `rolling_ball`, and `mask_merge`. Assert a
resolver query using the *declared* semantics returns the layer. This is the test that would have
caught the gap; `test_tag_resolver.py` is the natural home.

## Steps
1. Rewrite `_load_tagged_modules()` to call `_populate_registry()`; assert `skipped` is empty; remove
   the bare-`except` swallow.
2. Ratchet the operation-count floor to the true count.
3. Sweep the tag/resolver tests for other private loaders; route through the one mechanism.
4. Re-run the four reported failures — they should pass with no product change (that is the proof
   findings 1–3 were test artefacts).
5. Copy declared `target`/`produces` from the registry entry onto produced layers in `layer_tag_hook`.
6. Add the end-to-end operation→layer→tags→resolver test for the six named operations.
7. Full `pytest -m core` green (complexity budget).
8. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG — and state plainly that
   the "42 operations / missing `log` / no collision" findings were a stale test loader, so the record
   is not left implying a product regression.

## Definition of done
- The tag tests discover operations via `_populate_registry()`; an unimportable module FAILS loudly.
- The operation count is ratcheted at the true value.
- The four reported failures pass; findings 1–3 required no product change.
- A layer produced by an operation carries its declared `target`; the resolver finds Cellpose output
  by `role=labels, target=cell`.
- The end-to-end test covers six canonical operations.
- Full `pytest -m core` green.

## Cautions
- **Do not "fix" findings 1–3 by registering operations that already exist.** They are registered. The
  bug is discovery in the test. Adding duplicate declarations would create the real collision the
  audit wrongly reported.
- The `except TagCollision: pass` at `tag_registry.py:348` is **correct** — `_register_ui_operations`
  yields to a toolbox function that already claimed the op. Do not remove it. (It is unrelated to the
  reported failure.)
- Declared semantics come from the registry entry only — do not add a second inference path in the
  hook.
- Unknown operation ⇒ role-only, exactly as today. No guessing.
