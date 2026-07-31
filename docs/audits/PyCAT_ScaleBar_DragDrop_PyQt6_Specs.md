# PyCAT — Three Specs: Scale-Bar µm Regression · Drag-Drop Defence · PyQt6 Migration

*Against PyCAT **1.6.422**. The scale-bar root cause below is **confirmed** — traced from the
user-reported symptom to the exact line, cross-checked against the napari PRs that changed the
behaviour (#8900, #8907, #9007, #9029). Where something still needs verifying on the machine, it says
so explicitly rather than assuming.*

---

# SPEC A — Scale bar reads "px" instead of µm on napari 0.8.0 (URGENT, user-reported)

## The symptom

User on napari 0.8.0 reports the PyCAT scale bar now shows **pixels**, not microns. Worked before the
napari update.

## Root cause — confirmed, with the mechanism

There are **two** scale-bar paths in PyCAT, and the docstring that says PyCAT avoids napari's built-in
bar applies to the *wrong one*:

| Path | Used by | Mechanism |
|---|---|---|
| `ui/ui_utils.py:428 draw_custom_scale_bar` | **video export only** (`temperature_ui.py:709`) | Custom Shapes layer in data coords — *this* is the one whose docstring says it "deliberately avoids napari's built-in `viewer.scale_bar`". |
| `file_io/napari_adapter.py:195 _enable_auto_scale_bar` | **the interactive viewer** (called from `field_status.py` ×2, `base_ui.py:841`, `temperature_ui.py:79`) | **Uses napari's built-in `viewer.scale_bar`** — line 216 `sb = viewer.scale_bar`, line 236 `sb.unit = label`. |

So the bar the user sees is napari's native one, driven by `sb.unit = 'um'`.

**What napari changed (this is the confirmed part):**

1. **#8900 (0.7.1)** — the `ScaleBarOverlay` default unit became **`"px"`**.
2. **#8907 + #9007 (0.7.1)** — the scale bar now **derives its unit from the layers**:
   `unit = self.viewer.layers.units[self.viewer.dims.displayed[-1]]`, used *"if they have units set and
   are logically consistent across layers."* #9007 is titled *"Fix scale bar unit guessing logic and
   **deprecate `scale_bar.unit`**."*
3. **#9029 (0.8.0)** — *"Next round of deprecation for `scale_bar.unit`."* The next round after a
   deprecation is removal or no-op.

**PyCAT's code sits exactly in the gap this creates.** `_enable_auto_scale_bar` does two things:

```python
sb.unit = label                      # 'um' — the DEPRECATED ScaleBarOverlay.unit
```
wrapped in:
```python
with _w.catch_warnings():
    _w.simplefilter('ignore', FutureWarning)   # ← silences the deprecation warning
    sb.unit = label
except Exception:
    pass                                        # ← swallows the failure entirely
```

…and its docstring states, deliberately:

> **"NEVER sets `layer.units` — that is the confirmed cause of the black canvas on lazy 3D stacks."**

So: PyCAT sets the **deprecated** attribute (now ignored/removed), and refuses to set the **supported**
one (`layer.units`). napari therefore finds no layer units, falls back to its new default — **`"px"`**.
That is precisely the reported symptom.

The `FutureWarning` suppression + bare `except: pass` are why this failed **silently** through a napari
upgrade instead of surfacing.

## The fix

Migrate from `scale_bar.unit` to `layer.units`, which is the path napari now reads. The complication is
real and must be handled, not ignored: **PyCAT avoided `layer.units` because it caused a black canvas on
lazy 3D stacks.** napari 0.8.0 ships *"Fix: update cached unit scale when layer data dimensionality
changes (#9164) (#9181)"* — a stale cached unit scale on a dimensionality change is a very strong
candidate for exactly that black-canvas refit. **Verify before relying on it.**

### A1. Verify the black-canvas bug is fixed (do this FIRST — it gates the whole spec)

On the 0.8.0 machine, in a scratch script:
```python
import napari, numpy as np
v = napari.Viewer()
lyr = v.add_image(np.random.random((50, 512, 512)))   # 3D stack, the failing case
lyr.scale = (1.0, 0.067, 0.067)
lyr.units = ('um', 'um', 'um')                         # the thing PyCAT refuses to do
v.scale_bar.visible = True
napari.run()
```
Then repeat with a **lazy** stack (a `_TiffPageStack`/dask-backed array — the actual failing case, since
the bug was specific to lazy 3D). Scrub the slider, toggle 2D/3D, press Home (reset_view).

- **Canvas stays alive and the bar reads µm → proceed to A2.**
- **Black canvas returns → stop and go to A4 (the fallback).** Report the repro upstream; it would be a
  live napari bug on 0.8.0.

### A2. Set `layer.units` in `_enable_auto_scale_bar`

Replace the `sb.unit = label` block. Keep every existing guard (the finite/positive scale validation
that protects `reset_view` from NaN zoom is correct and stays):

```python
# napari >= 0.7.1 derives the scale-bar unit from the LAYERS
# (viewer.layers.units[dims.displayed[-1]]), and ScaleBarOverlay.unit is deprecated
# (#9007) with the next deprecation round in 0.8.0 (#9029). Setting only sb.unit
# silently produced a 'px' bar on 0.8.0 -- the units must live on the layer.
if _np.isfinite(px) and px > 0 and _is_calibrated(central_manager, px):
    sc = [float(s) for s in image_layer.scale]
    if all(_np.isfinite(s) and s > 0 for s in sc[:-2]) or len(sc) <= 2:
        sc[-1] = px; sc[-2] = px
        _apply_z_scale(sc, image_layer, central_manager, px)
        image_layer.scale = sc
    _set_layer_units(image_layer, 'um')     # NEW — the supported path
    label = 'um'
else:
    _set_layer_units(image_layer, 'px')
    label = 'px'
```

with a small, defensive helper (napari's units are **per-axis**, so it must match `ndim`):

```python
def _set_layer_units(layer, unit):
    """Set per-axis units on a layer for napari's scale bar.

    napari reads viewer.layers.units[dims.displayed[-1]]; units must be per-axis and
    CONSISTENT ACROSS LAYERS (napari warns and falls back when they disagree). Setting
    a scalar or a wrong-length tuple is a common cause of that inconsistency.
    """
    try:
        n = int(getattr(layer, 'ndim', len(layer.scale)))
        layer.units = tuple([unit] * n)
    except Exception as e:
        debug_log(f'could not set layer.units={unit!r} on {layer.name!r}', e)
        return False
    return True
```

**Do not keep writing `sb.unit`.** If you want belt-and-braces for older napari, write it *in addition*
and only when the attribute exists — but drop the blanket `except: pass` (see A5).

### A3. Set units on **every** layer, not just the reference image

This is the part most likely to bite. napari uses the layer units *"if they are logically consistent
across layers"* — a single layer with different (or unset) units makes them inconsistent and napari
falls back. PyCAT adds many layers (labels, masks, points, shapes, overlays), and it already has the
right hook: **`_align_layer_scales(viewer, central_manager)`**, called when `label == 'um'`.

**Extend `_align_layer_scales` to also align units**, so every layer it touches gets the same per-axis
units as the reference image. Same for `_update_scale_bar_for_active_layer` (line ~254), which currently
only flips `sb.unit` — it must set units on the active layer instead.

**Exclude the PyCAT Scale Bar Shapes layer** (`layer_name='PyCAT Scale Bar'`) if it is ever present in
the interactive viewer, and any purely decorative overlay, from unit alignment — or give it the same
units. Either is fine; inconsistency is what breaks it.

### A4. Fallback if A1 shows the black canvas is NOT fixed

If `layer.units` still kills lazy 3D stacks on 0.8.0, do **not** ship the regression. Instead:

- Set `layer.units` **only when the layer is 2D** (`ndim <= 2`), where the bug does not apply, and
- For 3D/lazy stacks, fall back to PyCAT's **own** overlay — reuse `draw_custom_scale_bar` (which
  already exists, is proven, broadcasts across frames, and zooms correctly) for the interactive viewer
  as well as video export, and hide napari's native bar (`sb.visible = False`) so the user never sees a
  misleading "px" bar next to a correct µm one.
- Record the napari repro in `docs/audits/` and open it upstream.

This is the honest outcome if napari's fix doesn't cover the lazy case: one correct bar beats a native
bar that lies.

### A5. Stop the silent failure (the reason this shipped unnoticed)

The suppression pattern is what let a napari upgrade break a physical-units display without a peep.
Both are in `_enable_auto_scale_bar` / `_update_scale_bar_for_active_layer`:

```python
_w.simplefilter('ignore', FutureWarning)   # hid the deprecation notice
except Exception:  pass                    # hid the failure
```

**Replace with:** no `FutureWarning` suppression (a deprecation on a physical-units path is exactly what
you want to see), and a logged failure that reaches the user:

```python
except Exception as e:
    debug_log('scale-bar unit could not be set — bar may show pixels', e)
    show_warning('PyCAT: could not set the scale bar to µm — it may be showing pixels. '
                 'Check the pixel size, or report this with your napari version.')
```
A scale bar silently switching from µm to px is a **measurement-integrity** failure — a figure exported
with a "px" bar labelled as microns is a wrong result. It must be loud.

### A6. Tests

`tests/test_scale_bar_units.py` (mark `integration` — needs a viewer; add a `base`-tier unit test for
the helper):

```python
def test_layer_units_set_when_calibrated():
    """A calibrated image must carry per-axis 'um' units — the property napari's
    scale bar actually reads (viewer.layers.units), NOT the deprecated scale_bar.unit."""
    # after _enable_auto_scale_bar with a known pixel size:
    assert tuple(layer.units) == ('um',) * layer.ndim

def test_layer_units_are_px_when_uncalibrated():
    assert tuple(layer.units) == ('px',) * layer.ndim

def test_all_layers_share_units():
    """napari falls back to the default unit when layers disagree — the 0.8.0 'px'
    regression. Every layer the viewer holds must report the same units."""
    units = {tuple(l.units) for l in viewer.layers if hasattr(l, 'units')}
    assert len(units) == 1

def test_units_helper_matches_ndim():
    """A wrong-length units tuple is a silent inconsistency source."""
    for ndim in (2, 3, 4):
        assert len(_units_for(ndim, 'um')) == ndim
```

Plus a **guard test** that the regression class can't recur:
```python
def test_scale_bar_does_not_rely_on_deprecated_scale_bar_unit():
    """scale_bar.unit is deprecated (napari #9007, #9029). PyCAT must set layer.units.
    This test exists because relying on sb.unit silently produced a px bar on 0.8.0."""
    src = (SRC / 'file_io' / 'napari_adapter.py').read_text()
    assert 'layer.units' in src or '_set_layer_units' in src
```

### A7. Also fix the docstring that misled

`_enable_auto_scale_bar`'s docstring currently asserts "NEVER sets `layer.units`" as a rule. Once A2
lands, that line is actively wrong and would mislead the next reader into re-breaking it. Rewrite it to
record the *history*: why it was avoided, what napari fixed, and what the current contract is.

---

# SPEC B — Defend PyCAT's drag-drop routing against napari superseding it

## Current state (working — protect it)

PyCAT's routing is correct and Gable confirms it works: `_FileDropFilter` (`ui/menu_manager.py:31`) is an
**app-level event filter** that intercepts `DragEnter`/`DragMove`/`Drop`, extracts local file URLs, calls
`event.acceptProposedAction()`, **returns `True` (consumes the event)**, and routes through PyCAT's
openers (channel assignment + data-repository registration). Input widgets (`QLineEdit`, `QTextEdit`,
`QAbstractSpinBox`) are correctly excluded so path-drops into fields still work.

Getting it working required real effort that is easy to lose: forcing `setAcceptDrops(True)` on
`_qt_window`, `_qt_viewer`, the vispy canvas (`qtv.canvas.native`) and its children, **plus deferred
re-assertion on timers** because vispy resets `acceptDrops=False` after PyCAT's setup runs.

## The risk

The routing rests on four things napari/vispy can change without notice:
1. Private accessors `window._qt_window`, `_qt_viewer` / `qt_viewer`.
2. The vispy canvas widget structure (`canvas.native` and its children).
3. **Timing** — the deferred re-assertion assumes vispy settles within the timer windows.
4. **Event-filter ordering** — an app-level filter normally sees events first, but a napari-installed
   filter or a widget-level handler could take precedence.

napari 0.8.0 did substantial Qt-layer work (theme/QSS restructuring, Qt6 `colorScheme` detection) and
lists **#9136 "Fix problem with ignoring annotation when drag and drop."** I have **not** read that
diff, and its wording suggests reader/type *annotations* rather than canvas `acceptDrops` — so treat it
as *"napari touched drag-drop; re-verify"*, not as a fix or a threat.

**The failure mode that matters is silent:** if napari's handler wins, files still load — through
**napari's** reader, bypassing channel assignment and data-repository registration. The user sees an
image appear and has no idea the PyCAT pipeline was skipped. That is worse than a visible break.

## Spec

### B1. Self-verify the routing at startup, and fail loudly

After the drop-filter installation block, add a **deferred verification** (same timer pattern already
used for re-assertion) that checks the three preconditions and reports if any is missing:

```python
def _verify_drop_routing():
    """Confirm PyCAT's drop routing is actually armed. If napari/vispy changes the widget
    tree or resets acceptDrops, files would silently load through NAPARI's reader --
    bypassing channel assignment and data-repository registration -- and the user would
    never know. A visible warning beats a silent pipeline bypass."""
    problems = []
    if QApplication.instance() is None or self._pycat_drop_filter is None:
        problems.append('app-level event filter not installed')
    qtv = _get_qt_viewer(self.viewer)                 # the ('_qt_viewer','qt_viewer') probe, factored out
    if qtv is None:
        problems.append('could not reach the QtViewer (napari private API moved)')
    else:
        native = _canvas_native(qtv)
        if native is None:
            problems.append('could not reach the vispy canvas widget')
        elif not native.acceptDrops():
            problems.append('canvas acceptDrops is False (drops on the image area will bypass PyCAT)')
    if problems:
        msg = ('PyCAT: drag-and-drop may not route through PyCAT '
               f'({"; ".join(problems)}). Files dropped on the image area could load without '
               'channel assignment. Use File > Open instead, and report this with your napari version.')
        debug_log(msg)
        show_warning(msg)
    return not problems
```

Schedule it once, ~1–2 s after the last re-assertion timer (after vispy has settled). **One** warning,
not a repeating nag.

### B2. Factor the private-API probes into one place

The `('_qt_viewer', 'qt_viewer')` fallback probe and the `('canvas', '_canvas')` + `.native` walk are
currently inline in `menu_manager.py`. Extract them to `ui/_napari_internals.py`:

```python
def get_qt_viewer(viewer):     ...   # tries the known accessors, returns None
def get_canvas_native(qtv):    ...   # tries ('canvas','_canvas') + .native, returns None
def get_qt_window(viewer):     ...   # window._qt_window
```
**Why:** these are the ~40 private-API touchpoints the napari-0.8.0 audit flagged as the upgrade blast
radius. One module means one place to fix when napari moves them, and one place for the version-probe
test (B4) to exercise. Update the other call sites (`run_pycat.py`, `dock_space.py`, `napari_menus.py`,
`viewer_actions.py`, `navigator_dock.py`, `session_actions.py`, `qt_worker.py`, `batch_processor.py`)
to use it as you touch them — not a big-bang sweep.

### B3. Guard against double-handling

The filter already returns `True` on `Drop`, which prevents napari's handler from also running **as long
as PyCAT's filter runs first**. Make that assumption explicit and cheap to detect: in `_route`, before
loading, record the paths and a timestamp on `self._last_routed`; if napari *also* inserts a layer for
the same file within a short window, the layer-insertion backstop (already present, watching
`layers.events.inserted` for foreign layers) can detect the duplicate and warn. This turns a
double-load from a confusing mystery into a named condition.

### B4. Tests

`tests/test_drop_routing.py` (mark `integration`):
```python
def test_drop_filter_is_installed_and_canvas_accepts_drops():
    """The three preconditions for PyCAT drop routing. If any fails, drops on the
    image area silently bypass channel assignment (napari's reader handles them)."""
    assert menu_manager._pycat_drop_filter is not None
    qtv = get_qt_viewer(viewer);      assert qtv is not None
    native = get_canvas_native(qtv);  assert native is not None
    assert native.acceptDrops() is True

def test_filter_consumes_the_drop():
    """Returning True is what stops napari ALSO loading the file."""
    assert filt.eventFilter(some_widget, drop_event_with_urls) is True

def test_filter_ignores_input_widgets():
    assert filt.eventFilter(QLineEdit(), drop_event_with_urls) is False
```
And a `core`-tier structural guard that the private-API probes stay centralized:
```python
def test_napari_private_accessors_are_centralized():
    """Private napari internals (_qt_window/_qt_viewer/canvas.native) must be reached
    through ui/_napari_internals.py so a napari upgrade has ONE place to fix."""
    # allow _napari_internals.py itself; flag NEW direct uses elsewhere (ratchet, like
    # test_complexity_budget: existing sites grandfathered, no new ones).
```

---

# SPEC C — PyQt5 → PyQt6 migration: audit and plan

## The audit (measured, not estimated)

| Surface | Count |
|---|---|
| Files importing PyQt5 | **93** |
| `from PyQt5.*` import statements | **311** (QtWidgets 203, QtCore 91, QtGui 17) |
| `qtpy` imports already present | 17 (so the pattern is already accepted in the codebase) |
| **Unscoped `Qt.*` enums** | **~90** (`Qt.ScrollBarAlwaysOff` ×18, `Qt.AlignTop` ×13, `Qt.Horizontal` ×10, `Qt.DisplayRole` ×9, `Qt.AlignCenter` ×5, …) |
| **`QSizePolicy.*` enums** | **639** (`.Ignored` ×270, `.Fixed` ×246, `.Expanding` ×63, `.Minimum` ×59) |
| `QMessageBox.*` enums | 47 (`.Yes` ×17, `.No` ×12, `.Question` ×4, `.Ok`/`.Cancel`/`.Save` ×9, roles ×3) |
| `QDialog.*` enums | 14 (`.Accepted` ×9, `.Rejected` ×5) |
| `QFrame` / `QHeaderView` / `QAbstractItemView` / `QFileDialog` enums | ~30 |
| **`QAction` imported from QtWidgets** (moved to QtGui in Qt6) | ~50 refs, ≥5 import sites |
| **`.exec_()` → `.exec()`** | 28 |
| `QVariant` | 2 (review) |
| **Total enum requalification sites** | **≈820** |

**Not present (good news — these are the usual migration killers and PyCAT has none):**
`QDesktopWidget`, `QRegExp`, `QApplication.desktop()`, `QSound`, `QDirModel`, `QFontMetrics.width()`,
`AA_EnableHighDpiScaling` / `AA_UseHighDpiPixmaps` / high-DPI rounding attributes.

Also to change: `"pyqt5"` and `"PyQtWebEngine"` in `pyproject.toml`, and `qt_api = "pyqt5"` in the
pytest config.

## The strategic decision you should make first

**~639 of the ~820 enum sites are `QSizePolicy.Ignored` / `.Fixed` / `.Expanding` / `.Minimum`.**
That single class dominates the entire migration cost. And here is the fork:

- **PyQt6 is strict.** Unscoped enum access was removed. Every one of the ~820 sites must become fully
  scoped: `QSizePolicy.Policy.Fixed`, `Qt.AlignmentFlag.AlignTop`, `Qt.ScrollBarPolicy.ScrollBarAlwaysOff`,
  `QMessageBox.StandardButton.Yes`, `QDialog.DialogCode.Accepted`, …
- **PySide6 is forgiving.** It has kept a "forgiving mode" that still accepts the unscoped spelling
  alongside the scoped one — which would make the bulk of those ~820 sites a **no-op**.

napari's own guidance in the 0.8.0 notes is *"consider migrating to **PySide6 or PyQt6**"* — both are
supported. So **PySide6 is very likely the substantially cheaper destination**, and the difference is
not marginal: it's roughly the difference between a mechanical import swap and an 800-site rewrite.

**Two caveats before choosing PySide6:** (a) confirm forgiving-enum mode is still enabled in the PySide6
version you'd target — Qt for Python has signalled it won't live forever; (b) PySide6 is LGPL vs PyQt6's
GPL/commercial, which for an academic open-source tool is a point in PySide6's favour but should be
checked against PyCAT's license posture.

**Recommendation:** spend 30 minutes proving it before committing. Take one representative module
(`ui/base_ui.py` — it has `QSizePolicy`, `Qt.*`, `QAction`, and `exec_()` all in one file), run it under
PySide6 and under PyQt6, and count the actual breakage. Decide on evidence. If PySide6's forgiving mode
holds, the migration collapses from a multi-week project to a few days.

## The plan (phased, each phase independently shippable)

### Phase 0 — Decide the target (above). Do not skip.

### Phase 1 — Swap imports to `qtpy` (binding-agnostic, ships alone, zero behaviour change)

`qtpy` is already used in 17 places and is what napari itself uses. Mechanically rewrite the 311
`from PyQt5.X import ...` → `from qtpy.X import ...`.

- **This is safe under PyQt5** — qtpy resolves to whatever binding is installed, so with `pyqt5` still
  pinned the app behaves identically. Ship and verify before changing bindings.
- **Handle `QAction` in the same pass:** `qtpy.QtWidgets.QAction` exists as a shim, but the correct
  Qt6 home is `QtGui`. Move the ~5 import sites to `from qtpy.QtGui import QAction` — qtpy handles
  both bindings.
- **`.exec_()` → `.exec()`** in the same pass (28 sites). `exec()` exists in Qt5 too, so this is safe
  immediately.
- Add a ratchet test: no NEW `from PyQt5` imports (grandfather any stragglers, like
  `test_complexity_budget` does).

**Deliverable:** PyCAT runs identically on PyQt5, but every import is binding-agnostic. This is the
single highest-value phase and it de-risks everything after it.

### Phase 2 — Requalify enums (the bulk — size depends on Phase 0)

If the target is **PyQt6**, all ~820 sites need scoping. Do it **class by class**, largest first, each
its own commit so a regression is bisectable:

1. `QSizePolicy` (639) — `.Ignored/.Fixed/.Expanding/.Minimum/.Preferred` → `QSizePolicy.Policy.*`
2. `Qt.*` (~90) — by enum family: `AlignmentFlag`, `ScrollBarPolicy`, `Orientation`, `ItemDataRole`,
   `CheckState`, `WindowModality`, `TextFormat`, `AspectRatioMode`, `TransformationMode`, `ArrowType`,
   `WindowType`, `WidgetAttribute`, `TextInteractionFlag`
3. `QMessageBox` (47) — `StandardButton.*`, `Icon.*`, `ButtonRole.*`
4. `QDialog` (14) — `DialogCode.*`
5. `QFrame`, `QHeaderView`, `QAbstractItemView`, `QFileDialog` (~30) — their respective scopes

**All scoped spellings are valid in Qt5 as well**, so each commit is shippable under the current PyQt5
pin and verifiable immediately. That's what makes this safe: you are never in a half-migrated state that
only runs on the new binding.

If the target is **PySide6** and forgiving mode holds, Phase 2 shrinks to the handful of genuinely
removed symbols found in Phase 3 testing.

### Phase 3 — Flip the binding

1. `pyproject.toml`: `"pyqt5"` → `"pyqt6"` (or `"pyside6"`); `"PyQtWebEngine"` → `"PyQt6-WebEngine"`
   (or `"PySide6-QtWebEngine"` — check what the plotly bridge actually needs, and whether that extra is
   still wanted at all).
2. `qt_api = "pyqt6"` (or `"pyside6"`) in the pytest config.
3. Full GUI smoke pass: every dock, every dialog, the drop routing (Spec B), the scale bar (Spec A), the
   navigator dock, video export, and the batch toolbar.
4. Verify the ~40 private-napari-internal call sites still resolve (Spec B2 centralizes them, which is
   why B2 should land *before* this phase).

### Phase 4 — Cleanup

Remove any binding-specific branches, update install docs and the conda env files, and note the
supported binding in the README.

## Sequencing against the other two specs

**Spec A is urgent and independent — ship it first**, on PyQt5, today. Spec B's B2 (centralizing the
private accessors) should land **before** Phase 3, because that's the phase most likely to disturb them.
Phase 1 (qtpy) can proceed in parallel with everything, since it's behaviour-neutral.

There is no deadline pressure beyond napari's **Q4 2026** PyQt5 drop — but Phase 1 is cheap, safe, and
makes the eventual flip a one-line change, so there's little reason to defer it.

---

## Suggested delivery order

| # | Change | Why this order |
|---|---|---|
| 1 | **A1** verify black-canvas on 0.8.0 | Gates the whole scale-bar fix. 15 min. |
| 2 | **A2–A3, A5, A7** scale-bar `layer.units` + loud failure | **Urgent, user-reported, measurement-integrity.** |
| 3 | **A6** tests incl. the deprecated-API guard | Locks the regression class closed. |
| 4 | **B1–B2** drop-routing self-verify + centralize private accessors | Cheap; protects a working feature and prepares Phase 3. |
| 5 | **C Phase 0** the PySide6-vs-PyQt6 evidence test | 30 min that may save weeks. |
| 6 | **C Phase 1** qtpy swap + `QAction` + `exec_()` | Behaviour-neutral, ships on PyQt5. |
| 7 | **C Phase 2–4** | Sized by the Phase 0 outcome. |

Each is its own version bump + PyPI push + commit per the standing ritual; A2/A3/A5/A7 can go together
as one scale-bar fix since they're one coherent change.
