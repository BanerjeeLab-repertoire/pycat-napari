"""In-app QC exemplar gallery — *"what does this defect look like, and what does it cost me?"*

Opens beside a QC report. The user reads *"Focus: bad"* on their own data, opens the gallery, and
sees a clean frame next to a defocused one, the verdict PyCAT gives each, and what the defect
costs downstream.

**The images are simulated, and the widget says so on every panel.** They stand in for real
exemplars, which accumulate slowly; the interface does not change when a real one replaces a
simulated one.

The exemplars come from ``pycat.toolbox.qc_gallery``, the same generator that builds the
documentation (``tools/build_qc_gallery.py``) — so **the widget, the docs and the test suite are
generated from one source.** An exemplar that stops tripping its own metric fails
``tests/test_qc_gallery.py``.
"""

from __future__ import annotations

import numpy as np


def _qt():
    """Resolve Qt lazily — this module must be importable headlessly, like the science ones."""
    from qtpy.QtCore import Qt
    from qtpy.QtGui import QImage, QPixmap
    from qtpy.QtWidgets import (QComboBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
                                QScrollArea, QVBoxLayout, QWidget)
    return dict(Qt=Qt, QImage=QImage, QPixmap=QPixmap, QComboBox=QComboBox, QFrame=QFrame,
                QGroupBox=QGroupBox, QHBoxLayout=QHBoxLayout, QLabel=QLabel,
                QScrollArea=QScrollArea, QVBoxLayout=QVBoxLayout, QWidget=QWidget)


_STATUS_COLOUR = {'good': '#2e7d32', 'warn': '#ef6c00', 'bad': '#c62828'}


def _to_pixmap(Q, array, is_stack, side=260):
    """Render a frame to a greyscale pixmap, contrast-stretched for display only."""
    frame = np.asarray(array)
    if is_stack:
        frame = frame[-1]                     # the last frame: bleaching and drift show there

    f = frame.astype(float)
    lo, hi = float(np.percentile(f, 0.5)), float(np.percentile(f, 99.8))
    if hi <= lo:
        hi = lo + 1.0
    disp = np.clip((f - lo) / (hi - lo), 0, 1)
    disp = (disp * 255).astype(np.uint8)
    disp = np.ascontiguousarray(disp)

    h, w = disp.shape
    image = Q['QImage'](disp.data, w, h, w, Q['QImage'].Format_Grayscale8)
    return Q['QPixmap'].fromImage(image).scaled(
        side, side, Q['Qt'].KeepAspectRatio, Q['Qt'].SmoothTransformation)


def _panel(Q, entry, which):
    """One side of the comparison: the image, and the verdict PyCAT gives it."""
    verdict = entry[f'{which}_verdict']
    status = verdict.get('status', '?')
    colour = _STATUS_COLOUR.get(status, '#555555')

    box = Q['QWidget']()
    layout = Q['QVBoxLayout'](box)
    layout.setContentsMargins(4, 4, 4, 4)

    heading = Q['QLabel']('Clean' if which == 'clean'
                          else f"Degraded — {entry['params']}")
    heading.setWordWrap(True)
    heading.setStyleSheet('font-weight: bold;')
    layout.addWidget(heading)

    picture = Q['QLabel']()
    picture.setPixmap(_to_pixmap(Q, entry[which], entry['is_stack']))
    picture.setStyleSheet(f'border: 2px solid {colour};')
    layout.addWidget(picture)

    caption = Q['QLabel'](f"<b>{status.upper()}</b> — {verdict.get('headline', '')}")
    caption.setWordWrap(True)
    caption.setStyleSheet(f'color: {colour};')
    layout.addWidget(caption)

    return box


def make_qc_gallery_widget():
    """Build the gallery panel. Returns a QWidget ready to dock."""
    from pycat.toolbox.qc_gallery import build_gallery

    Q = _qt()
    gallery = build_gallery()
    by_key = {e['key']: e for e in gallery}

    root = Q['QWidget']()
    outer = Q['QVBoxLayout'](root)

    banner = Q['QLabel'](
        "<b>These exemplars are SIMULATED.</b> They show what a defect looks like and what it "
        "costs — they are <i>not</i> an acquisition standard, and your data is not expected to "
        "resemble a synthetic image. Each carries the exact parameter that produced it.")
    banner.setWordWrap(True)
    banner.setStyleSheet(
        'background: #fff8e1; border: 1px solid #ffb300; padding: 6px; color: #5d4037;')
    outer.addWidget(banner)

    chooser = Q['QComboBox']()
    for entry in gallery:
        chooser.addItem(entry['title'], entry['key'])
    outer.addWidget(chooser)

    images = Q['QWidget']()
    image_row = Q['QHBoxLayout'](images)
    outer.addWidget(images)

    explain = Q['QLabel']()
    explain.setWordWrap(True)
    explain.setTextFormat(Q['Qt'].RichText)
    explain.setOpenExternalLinks(True)     # the citations must be clickable

    scroller = Q['QScrollArea']()
    scroller.setWidgetResizable(True)
    scroller.setWidget(explain)
    outer.addWidget(scroller, stretch=1)

    def _show(_index=0):
        entry = by_key[chooser.currentData()]

        while image_row.count():
            item = image_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        image_row.addWidget(_panel(Q, entry, 'clean'))
        image_row.addWidget(_panel(Q, entry, 'degraded'))

        # Image → Assessment → Interpretation → Recommendation. The costs come SECOND, before
        # the fix, because "your image is blurry" is not actionable and "your enrichment is
        # halved" is.
        explain.setText(
            f"<h3>{entry['title']}</h3>"
            f"<p><i>Simulated: {entry['params']}. Detected by "
            f"<code>{entry['metric']}</code>.</i></p>"
            f"<p><b>What it looks like.</b> {entry['looks_like']}</p>"
            f"<p><b>What it costs you.</b> {entry['costs']}</p>"
            f"<p><b>How to fix it.</b> {entry['fix']}</p>"
            # A defect the user cannot look up is a defect they cannot learn from. Wikipedia is
            # the accessible entry point; the primary reference is what a reviewer expects.
            f"<p><b>Read more.</b> "
            f"<a href='{entry['wiki'][1]}'>{entry['wiki'][0]}</a> &middot; "
            f"{entry['cite'][0]} "
            f"<a href='{entry['cite'][1]}'>doi</a></p>"
            + (f"<blockquote><i>{entry['cite_quote']}</i></blockquote>"
               if entry.get('cite_quote') else ""))

    chooser.currentIndexChanged.connect(_show)
    _show()

    return root
