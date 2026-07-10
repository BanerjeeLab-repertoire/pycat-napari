"""
PyCAT drag-and-drop diagnostic (napari 0.7.x canvas drop).

Run from the work terminal (pycat-dev312 active) at the repo root:

    python dnd_diag.py

WHY: files dropped on the napari CANVAS show the red no-drop cursor because the
canvas widget rejects the drag before any handler fires. This pins EXACTLY which
widget is under the cursor and whether it is a Qt widget we can fix
(setAcceptDrops) or a native GL surface Qt drag-drop can't reach.

STEPS:
  1. Run it - a napari window opens with a red instruction label.
  2. Drag any file from your file manager over the CANVAS (image area) and HOLD
     for ~2 seconds (don't drop yet). Watch the terminal.
  3. It prints, innermost-first, every widget that sees the DragEnter/Move, each
     with its class, objectName, and current acceptDrops state.
  4. Then drop it (or move away) and close the window.
  5. Paste the ENTIRE terminal output back.

Safe and deletable: loads no data, changes nothing.
"""
import sys
from qtpy.QtWidgets import QApplication, QLabel
from qtpy.QtCore import QObject, QEvent
import napari


_EVT = {QEvent.DragEnter: 'DragEnter', QEvent.DragMove: 'DragMove',
        QEvent.Drop: 'Drop', QEvent.DragLeave: 'DragLeave'}


class _Probe(QObject):
    def eventFilter(self, obj, event):
        et = event.type()
        if et in _EVT:
            name = obj.objectName() if hasattr(obj, 'objectName') else ''
            accepts = obj.acceptDrops() if hasattr(obj, 'acceptDrops') else '?'
            print(f"  DND> [{_EVT[et]:9}] "
                  f"{type(obj).__module__}.{type(obj).__name__} "
                  f"objectName={name!r} acceptDrops={accepts}")
        return False


def _describe(w, indent=0):
    pad = '  ' * indent
    ad = w.acceptDrops() if hasattr(w, 'acceptDrops') else '?'
    nm = ''
    try:
        nm = w.objectName()
    except Exception:
        pass
    print(f"{pad}{type(w).__module__}.{type(w).__name__} "
          f"objectName={nm!r} acceptDrops={ad}")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    probe = _Probe()
    app.installEventFilter(probe)

    viewer = napari.Viewer(title="PyCAT DnD diag")
    win = viewer.window
    qtv = getattr(win, '_qt_viewer', None) or getattr(win, 'qt_viewer', None)

    print("\n=== QtViewer ===")
    if qtv is not None:
        _describe(qtv)
    else:
        print("  QtViewer NOT FOUND via _qt_viewer / qt_viewer")

    print("\n=== Canvas + parent chain (native widget up to the window) ===")
    canvas = None
    for attr in ('canvas', '_canvas'):
        canvas = getattr(qtv, attr, None) if qtv else None
        if canvas is not None:
            print(f"(via qtv.{attr})")
            break
    native = getattr(canvas, 'native', canvas) if canvas is not None else None
    if native is not None:
        w = native
        depth = 0
        while w is not None and depth < 12:
            _describe(w, depth)
            try:
                if hasattr(w, 'windowHandle') and w.windowHandle() is not None:
                    print('  ' * depth + "   ^ has a native windowHandle "
                          "(may be a native GL surface)")
            except Exception:
                pass
            w = w.parent() if hasattr(w, 'parent') else None
            depth += 1
    else:
        print("  canvas/native NOT FOUND")

    if native is not None and hasattr(native, 'children'):
        print("\n=== Canvas native children ===")
        for c in native.children():
            _describe(c, 1)

    lbl = QLabel("DRAG A FILE OVER THE CANVAS (image area) AND HOLD ~2s - "
                 "watch the terminal, then close this window.")
    lbl.setStyleSheet("color:red; font-size:13pt; background:white; padding:8px;")
    viewer.window.add_dock_widget(lbl, name="DnD diag")

    napari.run()


if __name__ == "__main__":
    main()
