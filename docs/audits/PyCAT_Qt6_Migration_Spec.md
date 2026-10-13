# Spec — Migrate PyCAT off PyQt5 (to qtpy + a Qt6 binding)

**Baseline:** repo at **1.6.476**.
**Goal:** a new user's first experience of PyCAT is not a PyQt5 deprecation warning, and the Qt binding
stops being a constraint.

---

## Executive summary — this is ~1 day, not weeks

I measured the migration surface and then **empirically verified** the hard parts by installing
`qtpy` 2.4.3 with PyQt6 and with PySide6 and resolving every enum token PyCAT actually uses.

| Item | Raw count | Actual work |
|---|---|---|
| `from PyQt5.*` imports | **323** across **96 files** | Mechanical swap to `qtpy` |
| Unscoped enum sites (`QSizePolicy.Fixed`, `Qt.AlignTop`, …) | **~845** | **ZERO** — verified below |
| `QAction` imported from `QtWidgets` (moved to `QtGui` in Qt6) | 13 | **ZERO** — qtpy exposes it from both |
| `.exec_()` (removed in Qt6) | 28 | **ZERO** — qtpy shims it |
| `pyqtSignal` / `pyqtSlot` | **70 / 5** across 22 files | **The only real rename** → `Signal` / `Slot` |
| `QVariant` | 2 | Docstring text only, not code |
| `QDesktopWidget`, `QRegExp`, `QApplication.desktop()`, `QSound`, `QDirModel`, high-DPI attrs | **0** | None present — the usual blockers are absent |

**The enum finding is the one that changes the plan.** PyQt6 removed top-level enum access, which is
why the naive estimate was ~845 edits. But qtpy *promotes* the enums back. I extracted all **68
distinct enum tokens** PyCAT uses and resolved each one through qtpy:

- **qtpy + PyQt6 → 68/68 resolved.**
- **qtpy + PySide6 → 68/68 resolved.**

Including `QSizePolicy.Fixed` (643 call sites), `QMessageBox.Yes`, `QDialog.Accepted`, `QFrame.StyledPanel`,
`QHeaderView.Stretch`, `QAbstractItemView.SelectRows`. **No enum requalification is required.**

So the real migration is **~323 import-line edits + 75 signal/slot renames**, both scriptable.

---

## Which binding: recommend **PySide6**

The code change is binding-*neutral* — after the qtpy swap PyCAT runs on either, and the choice becomes
a one-line dependency decision you can reverse. But two things favour PySide6:

1. **Licensing — and this is a real, pre-existing problem.** PyCAT is **BSD 3-Clause**. It hard-depends
   on `pyqt5`, which is **GPL**. A BSD-licensed package whose install pulls a GPL dependency effectively
   forces GPL on the combined distributed work. **PyQt6 is also GPL; PySide6 is LGPL.** Moving to
   PySide6 *resolves* a license tension you already carry — moving to PyQt6 carries it forward. For a
   tool intended to be redistributed alongside a Nature Methods paper, that matters more than the
   deprecation warning that prompted this.
2. **Belt and braces on enums.** PySide6 keeps its own "forgiveness mode" for unscoped enums in addition
   to qtpy's promotion, so it has two independent reasons to work. PyQt6 relies solely on qtpy's
   promotion layer.

napari supports both, so this doesn't constrain the viewer.

*(Flag for Gable: confirm no PyQt5-licensed obligation already exists on your side, and that Christian
is content with the license posture change — it's an improvement, but it's his copyright line on the
LICENSE file.)*

---

## Phase 1 — Swap imports to qtpy (behaviour-neutral, ships on PyQt5)

`qtpy` resolves to whatever binding is installed, so **this phase changes nothing at runtime while
`pyqt5` is still the pinned dependency.** Ship and verify it before touching the binding.

1. Rewrite the 323 import statements:
   ```
   from PyQt5.QtWidgets import ...   →   from qtpy.QtWidgets import ...
   from PyQt5.QtCore    import ...   →   from qtpy.QtCore    import ...
   from PyQt5.QtGui     import ...   →   from qtpy.QtGui     import ...
   ```
   Scriptable, but **review the diff** — 17 `qtpy` imports already exist, so some files will mix.
2. Rename the signals in the same pass (the only non-mechanical part):
   - `pyqtSignal` → `Signal` (70 occurrences, 22 files)
   - `pyqtSlot` → `Slot` (5 occurrences)
   These are **not** exposed by qtpy under either binding — verified — so they must change. Both are
   available as `Signal`/`Slot` from `qtpy.QtCore` on PyQt5 as well, so this is still behaviour-neutral.
3. Add `qtpy` to the runtime dependencies in `pyproject.toml`.
4. **Verify on PyQt5:** `run-pycat` launches, every dock opens, the drop routing works, batch runs.
   Full test suite green. Nothing should behave differently.
5. Add a ratchet test: no new `from PyQt5` imports (grandfather any stragglers), in the style of
   `test_complexity_budget`.

**This phase alone is worth shipping** even if the binding decision stalls — it makes PyCAT
binding-agnostic and reduces the eventual flip to a dependency edit.

## Phase 2 — Flip the binding

1. `pyproject.toml`: `"pyqt5"` → `"pyside6"` (or `"pyqt6"`).
2. The plotly bridge extra: `"PyQtWebEngine"` → `"PySide6-QtWebEngine"` (or `"PyQt6-WebEngine"`).
   Only 2 `QtWebEngineWidgets` references exist, both reachable through qtpy. **Also check whether that
   extra is still wanted at all** — it is heavy (~100 MB) and the pyproject comment already calls it a
   common source of Qt install trouble.
3. `qt_api = "pyside6"` in the pytest config.
4. **Full GUI smoke pass.** This is where real differences surface, and it is the phase that needs you
   at the keyboard, not a script: every dock and dialog, the navigator dock, drag-and-drop routing, the
   scale bar, video export, the batch toolbar, the pyqtgraph "explore" backend, and the plotly bridge
   if retained.
5. **Re-check the ~40 private-napari-internal call sites** (`window._qt_window`, `_qt_viewer`,
   `window._status_changed`). These reach past the public API into napari's Qt layer and are the most
   likely place a binding change bites. They are the smoke-test checklist.

## Phase 3 — Cleanup

Update install docs, conda env files and the README; state the supported binding. Remove any
PyQt5-specific branches.

---

## Risks, honestly

- **The private napari internals (~40 sites) are the real unknown.** Everything measurable came back
  clean; this is the part I cannot verify without running the GUI.
- **`pyqtgraph` and the plotly/WebEngine extras** need their own confirmation under the new binding —
  they are optional backends, so worst case they get gated off rather than blocking the migration.
- **Qt6 behavioural differences beyond the API** (high-DPI handling defaults, stylesheet details,
  `QFileDialog` native-vs-Qt behaviour) won't appear in a static audit. Budget time for visual polish
  after the flip, not just for making it run.
- My verification covered **import-time resolution** of enums and the moved/renamed symbols. It does
  **not** prove runtime behaviour of every widget — that is what Phase 2's smoke pass is for.

---

## Why not just bump to PyQt6 and leave the imports alone

Because the imports are the thing holding you to one binding. Going `PyQt5` → `PyQt6` directly means
all ~845 enum sites *do* need requalification (no qtpy promotion layer), the `QAction` move becomes
real, and `.exec_()` must change — and you would be locked to GPL and to a single vendor. The qtpy route
is less work, keeps the license door open, and makes any future binding change a one-line edit.
