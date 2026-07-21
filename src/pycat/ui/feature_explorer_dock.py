"""**Feature Explorer dock — a searchable browser over the measurement layer. A thin shell.**

Left: the table's columns (searchable, grouped by ontology family when present). Right: the `FeatureCard`
for the selected column — definition / units, the reliability grade with its worst-first reasons, the
stability verdict, the correlated columns, the provenance chain, and a mini-histogram of the value.

**All content comes from `feature_explorer.build_feature_card`** (Qt-free, `core`-tested) — this file only
lays it out. The mini-histogram reuses the cohort-emitting histogram (`cohort_targets.attach_histogram_
brushing`, 1.6.170), so clicking a bin selects those objects through the `SelectionService`. Nothing here
recomputes an analysis; a field the sources did not fill shows "not assessed", never a fabricated value.
"""
from __future__ import annotations

import numpy as np

from pycat.utils.general_utils import debug_log

DOCK_NAME = 'Feature Explorer'
_VIEW_ID = 'feature_explorer'


def _family_of(key):
    """The ontology feature family for grouping the column list, or None (flat) when unknown."""
    try:
        from pycat.utils.measurement_ontology import describe
        d = describe(key)
        return getattr(d, 'family', None) if d is not None else None
    except Exception:      # broad-ok: family grouping is best-effort — an unknown column just goes flat
        return None


def _card_text(card):
    """Render a `FeatureCard` as the panel's rich text — every field degrading to a plain 'not assessed'
    rather than a blank or a fabricated value."""
    def _or(v, alt='not assessed'):
        return v if v else alt
    lines = [f"<h3>{card.key}</h3>"]
    if card.definition:
        lines.append(f"<p>{card.definition}</p>")
    if card.equation:
        lines.append(f"<p><i>{card.equation}</i></p>")
    lines.append(f"<b>Units:</b> {_or(card.units, '—')}<br>")
    lines.append(f"<b>Reliability:</b> {_or(card.reliability)}")
    if card.reliability_reasons:
        lines.append("<ul>" + "".join(f"<li>{r}</li>" for r in card.reliability_reasons) + "</ul>")
    lines.append(f"<b>Stability:</b> {_or(card.stability)}<br>")
    lines.append(f"<b>Correlated with:</b> {_or(', '.join(card.correlated_with), 'none on this table')}<br>")
    lines.append(f"<b>Provenance:</b> {_or(card.provenance_summary)}<br>")
    if card.caveats:
        lines.append("<b>Caveats:</b><ul>" + "".join(f"<li>{c}</li>" for c in card.caveats) + "</ul>")
    return "".join(lines)


def build_feature_explorer_dock(table, *, context=None, selection_service=None,
                                entity_id_col='entity_id', parent=None):
    """Assemble and return the Feature Explorer QWidget. ``table`` is the results table; ``context`` carries
    whatever analyses ran (reliability / stability / redundancy / provenance) for `build_feature_card` to
    aggregate; ``selection_service`` routes the mini-histogram's bin clicks as cohort selections.

    Returns ``None`` if Qt is unavailable (headless) — the aggregator is usable without this shell."""
    try:
        from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QLineEdit,
                                     QTextBrowser, QLabel)
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    except Exception as exc:      # broad-ok: no Qt/matplotlib (headless) — the aggregator works without this shell
        debug_log('Feature Explorer: Qt/matplotlib unavailable', exc)
        return None

    from pycat.utils.feature_explorer import build_feature_card

    root = QWidget(parent)
    layout = QHBoxLayout(root)

    # ── left: searchable column list ──────────────────────────────────────────
    left = QVBoxLayout()
    search = QLineEdit(); search.setPlaceholderText('Search columns…')
    col_list = QListWidget()
    columns = [c for c in getattr(table, 'columns', []) if c != entity_id_col]
    # Group by ontology family when available; flat otherwise (kept simple: sort by family then name).
    columns = sorted(columns, key=lambda c: (str(_family_of(c) or '~'), c))
    for c in columns:
        col_list.addItem(c)
    left.addWidget(QLabel('Measurements')); left.addWidget(search); left.addWidget(col_list)

    # ── right: card panel + mini-histogram ────────────────────────────────────
    right = QVBoxLayout()
    card_view = QTextBrowser()
    fig = Figure(figsize=(3.2, 1.8)); canvas = FigureCanvasQTAgg(fig)
    right.addWidget(card_view); right.addWidget(canvas)

    layout.addLayout(left, 1); layout.addLayout(right, 2)

    # The mini-histogram figure is REUSED across column switches, and `fig.clear()` drops the artists but
    # NOT the canvas callbacks — so without this, every switch would leave another `button_press_event`
    # cid (and a stale subscription) on the same canvas (plot_lifecycle). Hold the previous brushing's
    # disposer and tear it down before re-wiring.
    _brush = {'dispose': None}

    def _show(key):
        card = build_feature_card(table, key, context=context)
        card_view.setHtml(_card_text(card))
        fig.clear(); ax = fig.add_subplot(111)
        dist = card.distribution
        if dist is not None:
            edges = np.asarray(dist['edges'])
            counts, edges2, bars = ax.hist(
                np.repeat((edges[:-1] + edges[1:]) / 2.0, np.asarray(dist['counts'])),
                bins=edges)
            ax.set_title(key, fontsize=8)
            # Wire bin clicks -> cohort selection (1.6.170), when a service + entity ids are present.
            if selection_service is not None and entity_id_col in getattr(table, 'columns', ()):
                try:
                    from pycat.utils.cohort_targets import attach_histogram_brushing
                    # Tear down the previous column's brushing first — its cid lives on this same reused
                    # canvas, which fig.clear() did not touch.
                    if _brush['dispose'] is not None:
                        _brush['dispose']()
                    vals = table[key].to_numpy()
                    eids = table[entity_id_col].astype(str).to_numpy()
                    handles = attach_histogram_brushing(
                        fig, ax, vals, eids, bin_edges=edges2,
                        selection_service=selection_service,
                        view_id=_VIEW_ID, measurement=key, units=card.units, bars=bars)
                    _brush['dispose'] = handles.get('dispose')
                except Exception as exc:      # broad-ok: histogram brushing is optional — never break the dock
                    debug_log('Feature Explorer: could not wire histogram brushing', exc)
        canvas.draw_idle()

    col_list.currentTextChanged.connect(lambda k: _show(k) if k else None)

    def _filter_list(text):
        text = (text or '').lower()
        for i in range(col_list.count()):
            it = col_list.item(i)
            it.setHidden(text not in it.text().lower())
    search.textChanged.connect(_filter_list)

    if columns:
        col_list.setCurrentRow(0)
    return root
