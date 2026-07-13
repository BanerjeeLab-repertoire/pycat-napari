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
    Returns (cnr, snr_raw, signal_mean, background_mean, noise_std) for a 2-D array.
    NaN values indicate the IQR noise floor collapsed to zero.

    TWO metrics, because they answer different questions and only one of them is a
    property of the DATA:

      snr_raw = <signal> / sigma_bg
          The plain intensity-to-noise ratio. It does NOT subtract the background,
          so it is inflated by any pedestal the camera adds — and a camera offset is
          an instrument constant with no physical content. Measured on a synthetic
          image with a real contrast of 50 over a noise sigma of 5: adding a pedestal
          of 0 / 100 / 500 / 2000 counts reported an "SNR" of 28 / 78 / 282 / 1049.
          The identical image. This is retained (some users expect it, and it is a
          fine relative number *within* one image) but it must be labelled as what it
          is: an INTENSITY-to-noise ratio.

      cnr = (<signal> - <background>) / sigma_bg
          The contrast-to-noise ratio: how far the signal stands above the background,
          in units of the noise. This is the interpretable quantity, and it is
          invariant to the camera offset — it reported ~27 in all four cases above.

    Why this matters here specifically: this module computes ``delta_snr`` ACROSS
    preprocessing steps to tell the user whether a step helped. Background
    subtraction REMOVES the pedestal — so with the un-subtracted metric, one of the
    most useful steps in the pipeline appears to CRATER the SNR (measured:
    delta_snr = -257 when the true contrast changed by +1.5). The tool actively
    misled about the very thing it exists to evaluate. The verdict is therefore
    driven by CNR.
    """
    flat = np.asarray(arr, dtype=np.float64).ravel()
    nz = flat[flat > 0]
    if len(nz) < 10:
        return np.nan, np.nan, np.nan, np.nan, np.nan
    sig_thresh = np.percentile(nz, 98)
    signal_px = nz[nz >= sig_thresh]
    p25, p75 = np.percentile(flat, 25), np.percentile(flat, 75)
    noise_px = flat[(flat >= p25) & (flat <= p75)]
    if len(noise_px) < 10:
        return np.nan, np.nan, np.nan, np.nan, np.nan

    sig_mean = float(signal_px.mean())
    bg_mean = float(noise_px.mean())
    ns = float(noise_px.std())
    if ns < 1e-12:
        return np.nan, np.nan, sig_mean, bg_mean, ns

    cnr = (sig_mean - bg_mean) / ns          # background-subtracted: the honest one
    snr_raw = sig_mean / ns                  # legacy intensity-to-noise ratio
    return float(cnr), float(snr_raw), sig_mean, bg_mean, ns


# Step-name prefixes produced by pipeline_diagnostic_tools.py
_CURRENT_PREFIXES = ('PP [', 'BGR [')
_V100_PREFIXES    = ('v100-PP [', 'v100-BGR [')
_ALL_PREFIXES     = _CURRENT_PREFIXES + _V100_PREFIXES


def compute_snr_table(viewer):
    """
    Scan the viewer for all diagnostic step layers and compute contrast metrics.
    Returns a list of dicts with keys:
      step, pipeline, cnr, snr_raw, signal, background, noise_std, delta_cnr, delta_snr

    ``delta_cnr`` — the change in CONTRAST-to-noise relative to the first step — is
    the number to trust when judging whether a preprocessing step helped.
    ``delta_snr`` (the un-subtracted intensity-to-noise ratio) is retained for
    continuity but is inflated by the camera pedestal, so background subtraction makes
    it collapse even when the real contrast is unchanged. See ``_snr_of_array``.
    """
    import napari.layers as _nl

    rows = []
    cur_base_cnr = cur_base_snr = None
    v100_base_cnr = v100_base_snr = None

    # Collect layers in order (bottom-to-top matches step order)
    ordered = [l for l in reversed(list(viewer.layers))
               if isinstance(l, _nl.Image)
               and any(l.name.startswith(p) for p in _ALL_PREFIXES)]

    for layer in ordered:
        nm = layer.name
        pipeline = 'v1.0.0' if any(nm.startswith(p) for p in _V100_PREFIXES) else 'current'
        arr = np.asarray(layer.data, dtype=np.float32)
        cnr, snr_raw, sig, bg, ns = _snr_of_array(arr)

        if pipeline == 'current' and cur_base_cnr is None:
            cur_base_cnr, cur_base_snr = cnr, snr_raw
        if pipeline == 'v1.0.0' and v100_base_cnr is None:
            v100_base_cnr, v100_base_snr = cnr, snr_raw

        b_cnr = cur_base_cnr if pipeline == 'current' else v100_base_cnr
        b_snr = cur_base_snr if pipeline == 'current' else v100_base_snr

        def _delta(v, base):
            if base is None or np.isnan(v) or np.isnan(base):
                return np.nan
            return float(v - base)

        rows.append(dict(step=nm, pipeline=pipeline,
                         cnr=cnr, snr_raw=snr_raw,
                         signal=sig, background=bg, noise_std=ns,
                         delta_cnr=_delta(cnr, b_cnr),
                         delta_snr=_delta(snr_raw, b_snr),
                         # legacy key so existing consumers keep working; it now
                         # carries the HONEST metric rather than the inflated one.
                         snr=cnr))
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
    # 'CNR' not 'SNR': the column shows the background-SUBTRACTED contrast-to-noise
    # ratio. The un-subtracted ratio is inflated by the camera pedestal and made
    # background subtraction look destructive (see _snr_of_array).
    table.setHorizontalHeaderLabels(['Step', 'Pipeline', 'CNR', 'ΔCNR', 'Noise σ'])
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
                QTableWidgetItem('NaN' if np.isnan(r['delta_cnr'])
                                 else f"{r['delta_cnr']:+.1f}"),
                QTableWidgetItem('0 (clipped)' if (not np.isnan(r['snr']) and r['noise_std'] < 1e-12)
                                 else ('NaN' if np.isnan(r['noise_std'])
                                       else f"{r['noise_std']:.2e}")),
            ]

            # Colour coding
            if np.isnan(r['snr']):
                bg = QColor(80, 40, 40)   # dark red — destructive
                fg = QColor(200, 120, 120)
            elif not np.isnan(r['delta_cnr']) and r['delta_cnr'] > 0:
                # Green intensity proportional to relative gain. Driven by the
                # CONTRAST-to-noise change: the un-subtracted SNR collapses whenever
                # a step removes the camera pedestal, which would paint background
                # subtraction as 'destructive' when it is nothing of the kind.
                frac = min(r['delta_cnr'] / max(abs(r['delta_cnr']), 1.0), 1.0) * 0.6
                bg = QColor(int(30 + frac*40), int(80 + frac*120), int(30 + frac*40))
                fg = QColor(220, 255, 220)
            elif not np.isnan(r['delta_cnr']) and r['delta_cnr'] < 0:
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
                        f"(CNR {best_row['cnr']:.1f}, "
                        f"Δ{best_row['delta_cnr']:+.1f} vs raw). ")
        if nan_steps:
            summary += (f"<b>{len(nan_steps)} step(s) collapse background to 0</b> "
                        f"(NaN CNR) — they hard-zero the noise floor, "
                        f"making weak signal indistinguishable from background.")
        note.setText(summary)

    refresh_btn.clicked.connect(_populate)
    _populate()

    w = QWidget()
    w.setLayout(outer)
    ui_instance._add_widget_to_layout_or_dock(
        w, layout, separate_widget, 'Pipeline SNR Analysis')
