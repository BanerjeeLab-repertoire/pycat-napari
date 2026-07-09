# PyCAT ↔ napari Integration Audit — 2026-07-09

How PyCAT interfaces with napari, across three axes:
1. **Branding** — does the app present as PyCAT, or does napari leak through?
2. **napari feature usage** — which napari capabilities PyCAT uses / under-uses.
3. **File drag-and-drop routing** — the reported bug: canvas drops hitting napari's
   reader instead of PyCAT's pipeline.

Findings tagged `[BRAND]`, `[FEATURE]`, `[DND]`; priority **P1/P2/P3**.

---

## 1. Branding

**State: mostly good, one real OS-level gap.**

What PyCAT already does well:
- Window title set to `PyCAT-Napari` (`run_pycat.py:309`).
- Window + app icon set to the PyCAT logo (`run_pycat.py:290`, `:438`).
- Splash/logo QSS overridden so the PyCAT logo wins over napari's themed logo
  (`run_pycat.py:~455`).
- Version label reworded to "PyCAT {ver} • napari {ver}" and the help/forum line
  reworded to point at the PyCAT GitHub first (`run_pycat.py:461+`).
- napari-native menus (File/View/Plugins/Window/Help/Layers) hidden or neutralized by
  title (`ui_modules.py:2902+`), and a "◆ PyCAT ▸" marker action added (`:3103`).

Findings:
- `[BRAND][P2]` **`setApplicationName` / `setApplicationDisplayName` are never called.**
  The OS taskbar / dock / window-manager therefore still identify the process as "napari"
  (or "python"). One-line fix in `run_pycat.py` on the QApplication:
  `app.setApplicationName("PyCAT"); app.setApplicationDisplayName("PyCAT")` (and
  `setDesktopFileName("PyCAT")` on Linux). → **FIXED** this release.
- `[BRAND][P3]` **Window title is `PyCAT-Napari`.** Per the rebrand-away-from-"napari-addon"
  roadmap note, consider `PyCAT` (optionally "PyCAT — Python Condensate Analysis Toolbox").
  Left as-is pending your call on the rebrand (it's a positioning decision, not a bug).
  → surfaced, not changed.

---

## 2. napari feature usage

**State: heavy use of core layers; advanced visualization barely touched.**

Used (by frequency): `viewer.layers` (320), `viewer.window` (67), `add_labels` (50),
`add_image` (42), `dims` (15), `add_points` (7), `camera` (6), `add_shapes` (5),
`reset_view` (4), `scale_bar` (3), `text_overlay` (2), `add_tracks` (1).

Findings:
- `[FEATURE][P3]` **Advanced visualization is under-integrated** — `add_tracks` used only
  once (VPT), `add_vectors` / `add_surface` never, 3D display (`ndisplay=3`) never,
  `grid.enabled` never. This is not a defect — it maps exactly to the roadmap's 3D
  volume-rendering, kymograph, and tracks-visualization items. Confirms those are genuine
  additive opportunities: PyCAT owns analysis but leans on napari for 2D display and could
  lean on it far more for 3D / tracks / vectors.
- `[FEATURE][P3]` **`scale_bar` / `text_overlay` are used but sparsely** (3 / 2 sites).
  PyCAT has its own custom scale-bar drawing (`ui_utils.draw_custom_scale_bar`) — worth
  confirming the two don't fight (custom vs native scale bar). Noted for a focused check.
- **Sound:** the core integration pattern (PyCAT computes → pushes results as
  Image/Labels/Points/Shapes layers) is clean and idiomatic. No misuse found.

---

## 3. File drag-and-drop routing  ← the reported bug

**State: partially handled; a real gap for canvas drops.**

What exists (`ui_modules.py`):
- `_FileDropFilter` (`:147`) — an application-level `QObject` event filter that intercepts
  DragEnter/DragMove/Drop, extracts local file paths, and routes them to PyCAT's own
  openers (`open_2d_image` for standard files, `open_stack` for `.ims`), skipping input
  fields so path-drops into text boxes still work. Well-designed.
- Installed app-wide (`:3171-3174`) via `QApplication.installEventFilter`, and
  `_qt_window.setAcceptDrops(True)` (`:3175`).
- napari's native Open actions are disabled (`_disable_napari_open_actions`, `:3013`) so
  the *menu* path can't bypass PyCAT.

The gap (`[DND][P1]`):
- **PyCAT never overrides napari's CANVAS-widget `dropEvent`.** napari's `QtViewer` (the
  image canvas) is a widget with its own `setAcceptDrops(True)` + `dropEvent` that calls
  napari's reader. An application-level event filter *usually* sees events first, but a
  drop that lands directly on the canvas can still be routed to napari's widget-level
  `dropEvent` — so dropping a file onto the image area (the most natural place) can bypass
  PyCAT and go through napari's reader, which breaks PyCAT's channel-assignment /
  data-repository registration (the reported symptom).
- **Fix (defensive, belt-and-suspenders):** in addition to the app filter, install the same
  `_FileDropFilter` directly on the napari canvas / qt_viewer widget, and turn OFF
  `setAcceptDrops` on the canvas so napari's own `dropEvent` can't fire. Both are done in a
  `try/except` that degrades gracefully across napari versions (the private
  `_qt_viewer` / `qt_viewer` accessors differ between versions).
  → **FIXED** this release — but **must be verified on a real canvas drop** (napari isn't
  available in the audit sandbox, so the widget-accessor path and precedence can only be
  confirmed live). Verification steps in the changelog.

---

## Priority summary
- **P1:** `[DND]` canvas drops bypass PyCAT → **fixed**, needs live verification.
- **P2:** `[BRAND]` OS app name still "napari" → **fixed** (`setApplicationName`).
- **P3:** window-title rebrand (your call); under-used napari 3D/tracks/vectors (roadmap,
  additive); confirm custom vs native scale bar don't conflict.

## Verified non-issues
- Menu-based loading is already locked to PyCAT (napari Open actions disabled).
- The app-level drop filter itself is correct; the gap is specifically the canvas widget.
- Branding of splash/logo/menus/version label is thorough — only the OS app name was missing.
