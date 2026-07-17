# Claude Code spec — Tail cleanup: finish the six outstanding sub-tasks

> **STATUS — CLOSED (2026-07-17, tree 1.6.92).** All six items landed.
> - **Items 1, 2, 3** (VPT overlay unpadded pulse + in-place trajectory; short-track fragmentation
>   diagnostic): already shipped in **1.6.85** — verified present, no new work.
> - **Item 5** (focus mask threading): shipped in **1.6.91**. The spec's Fix 2 did not survive the
>   tree — it targeted dead code (`bf_analyse_focus_series`, zero callers) and callers with no mask
>   available. Per the user's explicit choice, delivered instead as a **maskless robustification**
>   (`math_utils.robust_focus_energy`, top-1% trim), wired into `bf_focus_metric` and both quality
>   tables. Debris z-sweep test with a live negative control.
> - **Item 4** (`set_data`): the reorder shipped in 1.6.91; the pinned store-vs-reject decision is
>   resolved here in **1.6.92** — warns-and-stores on a type mismatch.
> - **Item 6** (README): all four fixes shipped in **1.6.92** — `[dev]` backtrack warning, filled dev
>   step 2 + unified `pycat-env`, `pytest` test command, stale version literal removed.
> Every premise was re-verified against the live tree before acting; the spec targeted 1.6.90, and
> four of six items were already done by the time it was picked up.

**Date:** 2026-07-17 · **Target tree:** 1.6.90 · Verified against the 1.6.90 tree. Clears the tail
left across three prior specs (VPT rework, audit quick-wins, README audit). All six are small,
verified, and mutually independent — ship as one version or several. Touches
`object_ref.py`/`selection_overlay.py`/`vpt_tools.py`/`data_modules.py`/`brightfield_tools.py`/
`condensate_physics_tools.py`/`README.md`. Not `file_io.py`.

---

## 1 + 2 — VPT selection overlay: unpadded pulsing marker, in-place trajectory (VPT rework P4/P5)
Only P1–P3 of the VPT rework shipped. The overlay-visual half did not.

**P4 — the circle is offset even when empty; make it a pulse at the true centre.** `resolve_in_viewer`
(`object_ref.py:351`) uses `pad_px=8`, so the selection marker sits padded OUTWARD from the object.
`selection_overlay._centre_for` (`:58`) already computes the true centre. Fix:
- the selection MARKER goes at the true centroid, UNPADDED (padding stays only on the crop WINDOW for
  context — leave `resolve_offline`/`crop_slice` padding alone; only the marker position changes);
- replace the static circle with a gentle pulse (animate the `Points` marker `size`/`opacity` via a
  `QTimer`, ~1–2 Hz; or a one-shot expand-and-fade "ping" on selection if per-frame animation is too
  costly). Display-only — never touches the data/labels layer.

**P5 — the shifted-outline trajectory looks wrong; bold the real one in place.** The selection draws a
displaced duplicate of the track (same `pad_px` shift). Fix: do NOT draw a shifted copy — emphasise
the ACTUAL track line (increase linewidth + full alpha + raise zorder), the same in-place blit
emphasis the MSD plot uses (`analysis_plots._pblit_highlight`). When the view is zoomed to the bead,
the pulsing circle may be enough on its own — so make the bolded trajectory secondary, circle primary.
All emphasis at TRUE coordinates, no offset.

**Test:** the selection marker coordinate == object centroid (not centroid+pad); the pulse is on the
overlay layer, not the data layer; the highlighted trajectory shares coordinates with the base track.

## 3 — VPT short-track fragmentation diagnostic (VPT rework P2 tail)
`min_track_length` is now 200 (`MIN_TRACK_LENGTH_FRAMES`, shipped). But the spec's second half — report
WHY tracks are short — did not ship (the "fragment"/"reject" messages in `vpt_tools.py` are about
DETECTION ring-merging, a different stage, not MSD track-length rejection). Add: when tracks are
dropped for length < `min_track_length`, report a count and flag the fragmentation signature — "N
tracks rejected as too short; M cover beads present > K frames → likely linking fragmentation, not
absence." Surface it (return value / logged summary), don't silently drop (the no-silent-gates
contract). Do NOT try to fix the linking — just make the number honest.

**Test:** long clean tracks + short fragments → recovers D from the long ones AND reports the fragment
count rather than silently excluding.

## 4 — `set_data` KeyError on a new key (audit quick-win, still live)
`data_modules.py:111` `set_data` still checks `self.data_repository[key].__class__` BEFORE the
`elif key not in self.data_repository` — so a genuinely new key raises `KeyError`. Reorder existence
check first:
```python
if key not in self.data_repository:
    self.data_repository[key] = data
elif self.data_repository[key].__class__ != data.__class__:
    napari_show_warning(f"Data type mismatch for key {key}.")
    self.data_repository[key] = copy.deepcopy(data)   # store after warning
else:
    self.data_repository[key] = copy.deepcopy(data)
```
**Test** (`core`): new key stores with no exception; same-type overwrite deep-copies; different-type
overwrite warns and stores. Monkeypatch `napari_show_warning`.

## 5 — Focus scoring picks the sharpest debris (audit quick-win, still whole-frame)
`bf_focus_metric` (`brightfield_tools.py:707`) has a `mask=` param, but `bf_analyse_focus_series`
(`:753`, calls at `:778`/`:840`) still scores whole-frame, and `analyse_frame_quality`
(`condensate_physics_tools.py:1783`) has no mask option. Fix:
- thread an optional `mask=` through `bf_analyse_focus_series` → `bf_focus_metric`;
- add the same optional `mask=` to `analyse_frame_quality`, restricting laplacian-variance/entropy to
  the masked region when supplied; `mask=None` preserves whole-frame behaviour (back-compat);
- callers with a segmentation mask in hand pass it; do NOT fabricate a mask.

**Test** (`core`): synthetic stack — in-focus condensate in frame A, sharper debris in frame B;
`mask=None` picks B (reproduces the bug), mask = condensate region picks A (the fix). Both
`bf_analyse_focus_series` and `analyse_frame_quality`.

## 6 — README fixes (documentation audit)
Per `docs/audits/readme_documentation_audit_2026-07-16.md`:
- **The `[dev]` trap (`README.md:308`):** `pip install "pycat-napari[dev]"` backtracks past the modern
  1.6 pins to an old 1.5.x release with no error (a user hit 1.5.40 this way). Reframe it as an add-on
  to a current install, pin it, OR steer dev users to the editable `pip install -e ".[dev]"` (`:728`)
  as canonical. Add a one-line warning on all bare `pip install "pycat-napari[...]"` lines.
- **Stale version (`:877`):** `Current Version: 1.5.357` — remove the hardcoded literal (point to PyPI
  / `pip show`).
- **Wrong test command (`:737`):** `pytest --cov=pycat_napari` → the package is `pycat` (pyproject
  addopts already set `--cov=pycat`); use `pytest`.
- **Blank Development step 2:** fill the env-creation commands; unify the env name (README mixes
  `pycat-16`/`pycat-env`/`pycat-napari-env`).
- Optional: a docs-lint guard (grep README for a hardcoded `Current Version:` literal or a bare
  unpinned `pip install "pycat-napari[`), modelled on `test_install_routes_agree.py`.

---

## Steps
1. Items 1–2: overlay marker unpad + pulse + in-place trajectory; test.
2. Item 3: fragmentation diagnostic; test.
3. Item 4: `set_data` reorder; test.
4. Item 5: focus mask threading; debris test.
5. Item 6: README fixes (+ optional docs-lint).
6. Full `pytest -m core` green (complexity budget — extract helpers, don't raise the ceiling).
7. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG. **README-only changes ride
   in the same commit as the code items — no standalone docs commit** (per the versioning rule; there
   IS code here so the commit gets a version bump, and the README folds in).

## Definition of done
- VPT selection: unpadded pulsing marker on the bead; no shifted ghost trajectory; real track bolded
  in place.
- Short-track rejection reports fragmentation honestly.
- `set_data` stores a new key without raising.
- Focus scoring restricted to a supplied mask; whole-frame preserved when none; debris test passes.
- README leads to a CURRENT install; no stale literal; correct test command; followable dev setup.
- Full `pytest -m core` green.

## Cautions
- Overlay changes display-only; never mutate data/labels layers.
- Focus `mask=None` MUST preserve whole-frame behaviour; only restrict with a real mask.
- Don't remove the crop-window padding (context around the object is wanted) — only the MARKER goes
  unpadded.
- README: PIN or steer to editable — rewording a bare unpinned `[dev]` install still traps the next
  user.
- All six are independent — if one is fiddly, ship the rest.
