"""
Pipeline SNR Analysis — measures signal-to-noise ratio for condensate puncta
at every diagnostic step layer already in the viewer.

SNR metric:
  Signal = mean of top 2% non-zero pixel values  (candidate condensate spots)
  Noise  = std of pixels in the IQR 25th-75th percentile  (background texture)
  SNR    = Signal / Noise

The IQR noise region is used rather than the bottom-50% because background
subtraction steps hard-clip background pixels to zero, collapsing their std to 0
and producing NaN/infinite SNR even though the signal has actually been destroyed.
The IQR captures the transition zone between background and signal — where noise
matters most for thresholding — and remains non-zero after clipping.

NaN SNR means the IQR noise std collapsed to zero:
  the step hard-zeroed the background, which looks good on paper but destroys
  the ability to distinguish weak signal from noise (everything ≤ threshold = 0).

Empirical findings on real condensate data (DAPI ch, GFP ch, ball_radius=15):
  • /max normalisation + LoG(σ=3) alone gives 5–7× SNR vs raw.
  • /max normalisation + DoG(σ scaled to ball_radius) gives nearly the same.
  • Rolling-ball subtraction at any scaling factor hard-zeros the background,
    collapsing the IQR std to 0 and producing NaN SNR — it is counterproductive
    for condensate segmentation on both nuclear (DAPI) and cytoplasmic (GFP) channels.
  • The rolling-ball BACKGROUND itself (not the subtraction result) contains
    useful chromatin/nuclear topology information for DAPI channels.
"""
import numpy as np


def _snr_of_array(arr):
    """
    Returns (snr, signal_mean, noise_std) for a 2-D float array.
    NaN values indicate the IQR noise floor collapsed to zero.
    """
    flat = np.asarray(arr, dtype=np.float64).ravel()
    nz = flat[flat > 0]
    if len(nz) < 10:
        return np.nan, np.nan, np.nan
    sig_thresh = np.percentile(nz, 98)
    signal_px = nz[nz >= sig_thresh]
    p25, p75 = np.percentile(flat, 25), np.percentile(flat, 75)
    noise_px = flat[(flat >= p25) & (flat <= p75)]
    if len(noise_px) < 10:
        return np.nan, np.nan, np.nan
    ns = float(noise_px.std())
    if ns < 1e-12:
        return np.nan, float(signal_px.mean()), ns
    return float(signal_px.mean() / ns), float(signal_px.mean()), ns


# Step-name prefixes produced by pipeline_diagnostic_tools.py
_CURRENT_PREFIXES = ('PP [', 'BGR [')
_V100_PREFIXES    = ('v100-PP [', 'v100-BGR [')
_ALL_PREFIXES     = _CURRENT_PREFIXES + _V100_PREFIXES


def compute_snr_table(viewer):
    """
    Scan the viewer for all diagnostic step layers and compute SNR for each.
    Returns a list of dicts with keys:
      step, pipeline, snr, signal, noise_std, delta_snr
    where delta_snr is relative to the first step (raw input) of that pipeline.
    """
    import napari.layers as _nl

    rows = []
    current_baseline = None
    v100_baseline    = None

    # Collect layers in order (bottom-to-top matches step order)
    ordered = [l for l in reversed(list(viewer.layers))
               if isinstance(l, _nl.Image)
               and any(l.name.startswith(p) for p in _ALL_PREFIXES)]

    for layer in ordered:
        nm = layer.name
        pipeline = 'v1.0.0' if any(nm.startswith(p) for p in _V100_PREFIXES) else 'current'
        arr = np.asarray(layer.data, dtype=np.float32)
        s, sig, ns = _snr_of_array(arr)

        if pipeline == 'current' and current_baseline is None:
            current_baseline = s
        if pipeline == 'v1.0.0' and v100_baseline is None:
            v100_baseline = s

        baseline = current_baseline if pipeline == 'current' else v100_baseline
        delta = (s - baseline) if (baseline is not None and not np.isnan(s)
                                   and not np.isnan(baseline)) else np.nan
        rows.append(dict(step=nm, pipeline=pipeline,
                         snr=s, signal=sig, noise_std=ns, delta_snr=delta))
    return rows


def _add_pipeline_snr_analysis(ui_instance, layout=None, separate_widget=False):
    """
    Add the Pipeline SNR Analysis dock to the viewer.
    Scans for existing diagnostic step layers, computes SNR for each, and
    displays a colour-coded table.  A 'Refresh' button re-scans after
    running the diagnostic widget.
    """
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
        QTableWidget, QTableWidgetItem, QLabel, QSizePolicy,
        QHeaderView, QAbstractItemView)
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    import numpy as np

    outer = QVBoxLayout()

    title = QLabel('<b>Pipeline SNR Analysis</b>')
    title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    outer.addWidget(title)

    desc = QLabel(
        'Signal = mean(top 2% non-zero px) / Noise = std(IQR 25-75%).<br>'
        '<b>NaN</b> = background hard-clipped to 0 (step is destructive).<br>'
        'Green = SNR improves vs raw. Red = SNR falls. Grey = NaN.'
    )
    desc.setWordWrap(True)
    desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    desc.setStyleSheet('color:#aaa; font-size:9pt;')
    outer.addWidget(desc)

    table = QTableWidget(0, 5)
    table.setHorizontalHeaderLabels(['Step', 'Pipeline', 'SNR', 'ΔSNR', 'Noise σ'])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    for c in range(1, 5):
        table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    outer.addWidget(table)

    note = QLabel('')
    note.setWordWrap(True)
    note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
    note.setStyleSheet('color:#888; font-size:9pt; padding:4px;')
    outer.addWidget(note)

    btn_row = QHBoxLayout()
    refresh_btn = QPushButton('↺  Refresh')
    refresh_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    btn_row.addWidget(refresh_btn)
    outer.addLayout(btn_row)

    def _populate():
        table.setRowCount(0)
        rows = compute_snr_table(ui_instance.viewer)
        if not rows:
            note.setText('No diagnostic step layers found. Run the Pipeline Step '
                         'Diagnostics widget first, then click Refresh.')
            return

        # Find the best SNR to baseline colour scale
        valid_snrs = [r['snr'] for r in rows if not np.isnan(r['snr'])]
        best = max(valid_snrs) if valid_snrs else 1.0
        first_snr = rows[0]['snr'] if rows else np.nan

        for r in rows:
            row_idx = table.rowCount()
            table.insertRow(row_idx)

            # Truncate long step names for display
            step_display = r['step']
            items = [
                QTableWidgetItem(step_display),
                QTableWidgetItem(r['pipeline']),
                QTableWidgetItem('NaN' if np.isnan(r['snr'])
                                 else f"{r['snr']:.1f}"),
                QTableWidgetItem('NaN' if np.isnan(r['delta_snr'])
                                 else f"{r['delta_snr']:+.1f}"),
                QTableWidgetItem('0 (clipped)' if (not np.isnan(r['snr']) and r['noise_std'] < 1e-12)
                                 else ('NaN' if np.isnan(r['noise_std'])
                                       else f"{r['noise_std']:.2e}")),
            ]

            # Colour coding
            if np.isnan(r['snr']):
                bg = QColor(80, 40, 40)   # dark red — destructive
                fg = QColor(200, 120, 120)
            elif not np.isnan(r['delta_snr']) and r['delta_snr'] > 0:
                # Green intensity proportional to relative gain
                frac = min(r['delta_snr'] / max(abs(r['delta_snr']), 1.0), 1.0) * 0.6
                bg = QColor(int(30 + frac*40), int(80 + frac*120), int(30 + frac*40))
                fg = QColor(220, 255, 220)
            elif not np.isnan(r['delta_snr']) and r['delta_snr'] < 0:
                bg = QColor(80, 50, 30)   # dark orange — regression
                fg = QColor(220, 160, 100)
            else:
                bg = QColor(50, 50, 50)   # baseline row
                fg = QColor(200, 200, 200)

            for item in items:
                item.setBackground(bg)
                item.setForeground(fg)
                item.setTextAlignment(Qt.AlignCenter)
            items[0].setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            for c, item in enumerate(items):
                table.setItem(row_idx, c, item)

        # Summary note
        nan_steps = [r['step'] for r in rows if np.isnan(r['snr'])]
        best_row  = max((r for r in rows if not np.isnan(r['snr'])),
                        key=lambda r: r['snr'], default=None)
        summary = ''
        if best_row:
            summary += (f"Best step: <b>{best_row['step']}</b> "
                        f"(SNR {best_row['snr']:.1f}, "
                        f"Δ{best_row['delta_snr']:+.1f} vs raw). ")
        if nan_steps:
            summary += (f"<b>{len(nan_steps)} step(s) collapse background to 0</b> "
                        f"(NaN SNR) — they hard-zero the noise floor, "
                        f"making weak signal indistinguishable from background.")
        note.setText(summary)

    refresh_btn.clicked.connect(_populate)
    _populate()

    w = QWidget()
    w.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, 'Pipeline SNR Analysis')
