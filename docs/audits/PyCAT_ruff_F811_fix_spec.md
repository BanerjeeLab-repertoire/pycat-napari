# PyCAT — Spec: fix the ruff `check` failures (F811 redefinitions + malformed noqa)

*All 13 findings are mechanical and behaviour-neutral. They are the redundant-reimport findings from
the audit's Part 3, now failing CI as ruff **F811** (redefinition of an already-imported name), plus one
malformed `# noqa` directive. None changes runtime behaviour — every redefined symbol is already
imported at module top, so the local re-import is pure redundancy left behind when the ui_decomposition
refactor hoisted imports to module level.*

**The one rule that prevents breakage:** several of the offending local imports also pull in symbols
that are **not** at module top (`QGroupBox`, `QFormLayout`, `QSpinBox`, `QDialog`, `QTableWidget`,
`QFileDialog`, etc.). So do **not** delete whole local-import lines — remove **only** the symbols ruff
flags (the ones already imported at module top) and keep the rest. I verified each module-top import
below actually contains the flagged symbol, so removal is safe.

Ship as one commit (it's all lint cleanup, one logical change). This is a docs/lint-class change with no
`pyproject` version bump needed on its own — but since CI is red, fold it into the next code change's
zip **or** push it as the fix that turns CI green, your call; it touches no science.

---

## 1. Malformed `# noqa` — `utils/feature_provenance.py:89`

**Current:**
```python
for parent in lineage.get(lid, ()):  # noqa: dict.get default () is intentional
```
ruff reads everything after `# noqa:` as a comma-separated list of **codes**, so
`dict.get default () is intentional` is an invalid code list.

**The line has no lint violation to suppress** — `dict.get(key, ())` is perfectly valid and ruff
wasn't flagging it. The `# noqa` was never needed. **Fix: delete the directive entirely**, keep the
explanatory intent as a plain comment if you want it:
```python
for parent in lineage.get(lid, ()):  # dict.get default () is intentional
```
(If some *other* rule genuinely fires on this line once the malformed directive is gone, replace it with
a real coded suppression, e.g. `# noqa: B905`. But first confirm anything actually fires — most likely
nothing does and the plain comment is the end of it.)

---

## 2. F811 duplicate `import numpy as np` — `toolbox/vpt/linking.py:58` and `:205`

**Current:** `import numpy as np` at module top (line 9) **and** re-imported inside two functions
(lines 58, 205). Both function-local re-imports are pure redundancy.

**Fix:** delete the two local `import numpy as np` lines (58 and 205). Module-top line 9 covers both.

---

## 3. F811 duplicate `import os as _os` — `toolbox/timeseries/ui.py:224`

**Current:** `import os as _os` appears at line 181 and again at line 224 (both function-local, same
alias).

**Fix:** delete the **second** occurrence (line 224). If line 181's `_os` is in scope for the code at
224 they'd share it — but they're in different functions, so the safe minimal fix is: **delete line 224's
`import os as _os`** and confirm that function still has `_os` in scope. It won't unless 181 and 224 are
in the same function. Check:
- If **same function** → line 224 is the redundant one; delete it.
- If **different functions** → line 224 is *not* actually redundant at runtime (each function needs its
  own import), and ruff is flagging module-level shadowing. In that case **hoist `import os` to module
  top once** (as `import os as _os`) and delete **both** local imports (181 and 224). This is the
  cleaner fix and removes the F811 at its root.

Given both are `import os` — a stdlib module with no import cost concern — **hoist to module top and
remove both locals** is the right call.

---

## 4. F811 `QPushButton` / `QComboBox` — `ui/analysis_methods_ui.py:490` and `:1208`

**Module top (line 12)** already imports the full set including `QPushButton`, `QLabel`, `QComboBox`.

**Site 490** (local block spanning 489–490):
```python
from PyQt5.QtWidgets import (QGroupBox, QFormLayout, QSpinBox,
                              QPushButton, QLabel)
```
`QGroupBox`, `QFormLayout`, `QSpinBox` are **not** at module top — keep them. `QPushButton`, `QLabel`
**are** — remove them:
```python
from PyQt5.QtWidgets import QGroupBox, QFormLayout, QSpinBox
```

**Site 1208:**
```python
from PyQt5.QtWidgets import QComboBox        # ← QComboBox already at top; whole line is redundant
```
This line imports only `QComboBox`, which is at module top → **delete the entire line 1208.**

---

## 5. F811 `QPushButton` / `QHBoxLayout` — `ui/base_ui.py:85` and `:708`

**Module top (lines 14–15)** import `QHBoxLayout`, `QLabel`, `QPushButton` (among others).

**Site 85:**
```python
from PyQt5.QtWidgets import (QPushButton, QComboBox as _QCB, QLabel as _QLbl)
```
The aliases `_QCB` / `_QLbl` are used on the very next line (`findChildren((QPushButton, _QCB, ...))`)
and are **local aliases not present at module top** — keep them. `QPushButton` **is** at module top —
remove just it:
```python
from PyQt5.QtWidgets import QComboBox as _QCB, QLabel as _QLbl
```
Then confirm line 86 (`for w in widget.findChildren((QPushButton, _QCB, QLineEdit)):`) still resolves
`QPushButton` — it does, from module top. ✓

**Site 708:**
```python
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget
```
`QHBoxLayout` and `QLabel` are at module top; check `QWidget` — if `QWidget` **is** at module top too,
**delete the whole line 708**; if `QWidget` is **not**, trim to:
```python
from PyQt5.QtWidgets import QWidget
```
(ruff only flagged `QHBoxLayout` here, which means `QLabel`/`QWidget` status differs — trim exactly the
flagged `QHBoxLayout` and leave whatever ruff didn't flag. Safest: `from PyQt5.QtWidgets import QWidget`
if `QWidget` isn't up top, else delete the line.)

---

## 6. F811 `QHBoxLayout` — `ui/metadata_dialogs.py:20`

**Module top (line 10)** imports the full set including `QHBoxLayout`.

**Site 20** (block 20–22):
```python
from PyQt5.QtWidgets import (QDialog, QHBoxLayout,
                              QTableWidget, QTableWidgetItem, QHeaderView,
                              QFileDialog)
```
`QDialog`, `QTableWidget`, `QTableWidgetItem`, `QHeaderView`, `QFileDialog` are **not** at module top —
keep them. Remove only `QHBoxLayout`:
```python
from PyQt5.QtWidgets import (QDialog, QTableWidget, QTableWidgetItem,
                              QHeaderView, QFileDialog)
```

---

## 7. F811 `QHBoxLayout` — `ui/recorded_steps_dialog.py:20`

*(This file post-dates the 1.6.376 snapshot I have, so specced by the same pattern — verify the
module-top import and local block before trimming.)*

**Reported local block:**
```python
from PyQt5.QtWidgets import (QFileDialog, QDialog, QListWidget, QHBoxLayout, QAbstractItemView)
```
`QHBoxLayout` is redundant with module top (line 9). Keep `QFileDialog`, `QDialog`, `QListWidget`,
`QAbstractItemView`; remove `QHBoxLayout`:
```python
from PyQt5.QtWidgets import (QFileDialog, QDialog, QListWidget, QAbstractItemView)
```

---

## 8. F811 `QHBoxLayout` — `ui/session_loader.py:34`

**Module top (line 10)** imports `QHBoxLayout`. **Site 34:**
```python
from PyQt5.QtWidgets import (QFileDialog, QDialog, QListWidget, QHBoxLayout, QAbstractItemView)
```
Keep the other four, remove `QHBoxLayout`:
```python
from PyQt5.QtWidgets import (QFileDialog, QDialog, QListWidget, QAbstractItemView)
```

---

## 9. F811 `QComboBox` / `QLabel` — `ui/toolbox_functions_ui.py:602`

**Module top (line 9)** imports `QComboBox` and `QLabel`. **Site 602:**
```python
from PyQt5.QtWidgets import QComboBox, QFileDialog, QLabel
```
`QFileDialog` is **not** at module top — keep it. Remove `QComboBox`, `QLabel`:
```python
from PyQt5.QtWidgets import QFileDialog
```

---

## Verification (run before committing)

1. **ruff is green:**
   ```
   ruff check src
   ```
   Expect zero F811 and no invalid-noqa warning.

2. **Nothing was over-trimmed** — every symbol still resolves. The risk in this whole change is deleting
   a symbol that was *only* available via the local import. Guard against it by byte-compiling and, where
   possible, importing each touched module:
   ```
   python -m compileall -q src/pycat
   python -c "import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]" \
       src/pycat/utils/feature_provenance.py \
       src/pycat/toolbox/vpt/linking.py \
       src/pycat/toolbox/timeseries/ui.py \
       src/pycat/ui/analysis_methods_ui.py \
       src/pycat/ui/base_ui.py \
       src/pycat/ui/metadata_dialogs.py \
       src/pycat/ui/recorded_steps_dialog.py \
       src/pycat/ui/session_loader.py \
       src/pycat/ui/toolbox_functions_ui.py
   ```
   Compile catches a genuinely-removed name only at the *use* site if the name is now undefined at module
   scope — so also grep each trimmed symbol to confirm a module-top or surviving-local definition remains
   in that file. For the two judgement calls (`base_ui.py:708 QWidget`, `timeseries/ui.py _os` hoist),
   confirm the module-top import set before deleting.

3. **Core tests still green:**
   ```
   pytest -m "core or base" -o addopts=
   ```

## Why removal, not `# noqa: F811`

Every one of these is a symbol already imported at module top — the local re-import adds nothing, so the
correct fix is to remove the redundant symbol, not to suppress the warning. Reserve `# noqa: F811` for
the genuine PyCAT pattern where a *function-local* Qt import is deliberately kept to avoid an
`UnboundLocalError` from a name that's conditionally reassigned in the same scope — none of these 13
sites is that case (each local import's flagged symbol is never reassigned locally; it's a plain
duplicate). If you later hit a real one, suppress it with the coded form `# noqa: F811` and a comment
saying why, so it doesn't read like the malformed directive in finding 1.
