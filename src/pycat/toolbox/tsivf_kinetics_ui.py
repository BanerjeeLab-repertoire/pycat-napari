"""Time-series in-vitro panel — multi-channel measurement picker and the reaction-kinetics step (1.7.002).

UI + plotting only; the measurement and kinetics live in ``toolbox/invitro/timeseries_kinetics.py``.
"""
from __future__ import annotations

import numpy as np
import napari
from napari.utils.notifications import show_info as napari_show_info, show_warning as napari_show_warning
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QLabel, QListWidget, QListWidgetItem, QPushButton,
                            QSizePolicy, QSpinBox)

#: Layer names that are transmitted light — measured only if the user ticks them (they carry no fluorescence).
_TRANSMITTED_HINT = ('brightfield', 'transmitted', 'dic', 'phase')


def _time_series_layers(viewer):
    return [lyr for lyr in viewer.layers
            if isinstance(lyr, napari.layers.Image) and getattr(lyr.data, 'ndim', 0) == 3]


def channel_checklist(ui):
    """A checkable list of every 2D+t image layer, kept in sync with the viewer. Fluorescence channels start
    ticked; brightfield/transmitted channels start unticked."""
    lst = QListWidget()
    lst.setMaximumHeight(110)
    lst.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _refresh(*_):
        was = {lst.item(i).text(): lst.item(i).checkState() for i in range(lst.count())}
        lst.clear()
        for lyr in _time_series_layers(ui.viewer):
            it = QListWidgetItem(lyr.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            default = Qt.Unchecked if any(h in lyr.name.lower() for h in _TRANSMITTED_HINT) else Qt.Checked
            it.setCheckState(was.get(lyr.name, default))
            lst.addItem(it)

    ui.viewer.layers.events.inserted.connect(_refresh)
    ui.viewer.layers.events.removed.connect(_refresh)
    _refresh()
    return lst


def short_channel_name(layer_name):
    """'sub-HHrz-Trial-4 C0-488 Stack' -> 'C0-488': the channel part of a PyCAT stack layer name, so result
    columns read ``net_C0_488`` instead of ``net_sub_HHrz_Trial_4_C0_488_Stack``. The full name if none."""
    import re
    m = re.search(r'(?:^|\s)(C\d+-[^\s\[\]]+)', str(layer_name))
    return m.group(1) if m else str(layer_name)


def checked_channels(ui, lst, shape):
    """``{channel name: (T, Y, X) data}`` for the ticked layers whose shape matches the label stack, keyed by
    :func:`short_channel_name` (full layer names if the short ones would collide)."""
    names = {lyr.name: lyr for lyr in _time_series_layers(ui.viewer)}
    picked = []
    for i in range(lst.count()):
        it = lst.item(i)
        lyr = names.get(it.text())
        if it.checkState() == Qt.Checked and lyr is not None and tuple(lyr.data.shape) == tuple(shape):
            picked.append(lyr)
    short = [short_channel_name(lyr.name) for lyr in picked]
    keys = short if len(set(short)) == len(short) else [lyr.name for lyr in picked]
    return {k: lyr.data for k, lyr in zip(keys, picked)}


def add_kinetics_step(ui, layout):
    grp = QGroupBox("Step 5b — Reaction Kinetics & Heterogeneity")
    form = QFormLayout(grp)
    form.setContentsMargins(9, 20, 9, 6)
    form.addRow(QLabel(
        "<span style='color:#aaa;font-size:9pt;'>"
        "Per droplet, from its background-subtracted signal: baseline, plateau, onset / half / 90% times and "
        "maximum rate (model-free — a fitted curve would average away a heterogeneous onset). Then the spread "
        "over droplets, frame by frame: how far ahead some droplets are of others.</span>"))
    ch_dd = QComboBox()
    ch_dd.setToolTip("The channel whose kinetics to analyse (one measured in Step 4).")
    form.addRow("Channel:", ch_dd)
    min_len = QSpinBox(); min_len.setRange(3, 100000); min_len.setValue(10)
    min_len.setToolTip("Only droplets tracked for at least this many frames get kinetics.")
    form.addRow("Min frames per droplet:", min_len)
    smooth = QSpinBox(); smooth.setRange(1, 51); smooth.setSingleStep(2); smooth.setValue(5)
    smooth.setToolTip("Rolling-median window (frames) used for the crossing times and rates.")
    form.addRow("Smoothing (frames):", smooth)
    run = QPushButton("▶  Kinetics")
    run.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(run)

    def _refresh_channels():
        names = ui._dr().get('tsivf_measured_channels') or []
        cur = ch_dd.currentText()
        ch_dd.clear(); ch_dd.addItems(names)
        if cur in names:
            ch_dd.setCurrentText(cur)

    def _on_run():
        from pycat.toolbox.invitro.timeseries_kinetics import (droplet_kinetics, kinetics_heterogeneity,
                                                                heterogeneity_over_time)
        _refresh_channels()
        linked = ui._dr().get('tsivf_linked')
        if linked is None or 'track_id' not in getattr(linked, 'columns', []) or not ch_dd.count():
            napari_show_warning("Run Step 4 (measure + link) first.")
            return
        ch, dt = ch_dd.currentText(), ui._frame_interval_s()
        kin = droplet_kinetics(linked, ch, dt, smooth_frames=smooth.value(), min_frames=min_len.value())
        if not len(kin):
            napari_show_warning(f"No droplet was tracked for {min_len.value()}+ frames in '{ch}'.")
            return
        summary = kinetics_heterogeneity(kin)
        curve = heterogeneity_over_time(linked, kin, ch)
        ui._dr()['tsivf_kinetics'] = kin
        ui._dr()['tsivf_heterogeneity'] = curve
        ui._record('tsivf_kinetics', {'channel': ch, 'min_frames': min_len.value(),
                                      'smooth_frames': smooth.value(), 'frame_interval_s': dt})
        import pandas as pd
        from pycat.toolbox.timeseries_invitro_fluor_ui import _show
        _show(f"TSIVF Kinetics — {ch}", [("Heterogeneity summary", pd.DataFrame([summary])),
                                          ("Per-droplet kinetics", kin), ("Heterogeneity over time", curve)])
        _plot_kinetics(ui, linked, kin, curve, ch, dt)
        napari_show_info(f"{summary.get('n_responded', 0)} of {summary['n_tracks']} droplets responded; "
                         f"half-time median {summary.get('t_half_s_median', np.nan) / 60:.1f} min "
                         f"(IQR {summary.get('t_half_s_iqr', np.nan) / 60:.1f} min).")

    run.clicked.connect(_on_run)
    layout.addWidget(grp)


def _plot_kinetics(ui, linked, kin, curve, channel, dt):
    """Traces (a sample + the median), the half-time distribution, and the spread over time — in one dock."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    from pycat.toolbox.invitro.timeseries_kinetics import channel_column

    col = f'net_{channel_column(channel)}'
    to_min = dt / 60.0
    fig = Figure(figsize=(12, 3.6), tight_layout=True)
    ax = fig.subplots(1, 3)
    tids = kin['track_id'].to_numpy()
    for tid in np.random.default_rng(0).choice(tids, min(40, len(tids)), replace=False):
        g = linked[linked['track_id'] == tid].sort_values('frame')
        ax[0].plot(g['frame'] * to_min, g[col], lw=0.6, alpha=0.5)
    ax[0].plot(curve['frame'] * to_min, curve['net_median'], 'k', lw=2, label='median')
    ax[0].set_xlabel('time (min)'); ax[0].set_ylabel(f'{channel} − local background'); ax[0].legend(fontsize=8)
    th = kin.loc[kin['responded'], 't_half_s'].dropna() / 60.0
    ax[1].hist(th, bins=40, color='#4c72b0')
    ax[1].set_xlabel('half-time (min)'); ax[1].set_ylabel('droplets')
    ax[2].plot(curve['frame'] * to_min, curve['progress_iqr'], label='spread of progress (IQR)')
    ax[2].plot(curve['frame'] * to_min, curve['fraction_activated'], label='fraction past own 50%')
    ax[2].set_xlabel('time (min)'); ax[2].set_ylim(0, 1.05); ax[2].legend(fontsize=8, loc='center right')
    canvas = FigureCanvasQTAgg(fig)
    # A bottom dock opens at its content's minimum height: without this it came up ~60 px tall, the three
    # panels squashed to slivers ("Tight layout not applied"). Same floor as the VPT results dock.
    canvas.setMinimumSize(720, 340)
    try:
        if getattr(ui, '_tsivf_kin_dock', None) is not None:
            ui.viewer.window.remove_dock_widget(ui._tsivf_kin_dock)
    except (RuntimeError, AttributeError, KeyError, ValueError):
        pass                                       # stale dock reference -> just add a fresh one
    ui._tsivf_kin_dock = ui.viewer.window.add_dock_widget(canvas, name=f"Kinetics — {channel}", area='bottom')
