"""VPT UI — the measured-drift readout, its plot, and the on-the-beads display check (1.6.476).

A mixin so ``vpt_ui.py`` composes it. Linking measures the drift (``measured_drift.measure_drift``); this
shows the measurement, ticks the apply box only when the drift is real, and checks after every link that
the markers drawn on the image sit on the beads' intensity centroids.
"""
from __future__ import annotations

import numpy as np

#: Median marker-to-centroid distance (px) above which the drawn positions are reported as OFF the beads.
#: A correct display measures ~0.3 px on the G3BP1 bead movie; drawing drift-corrected positions there
#: measures 2.75 px.
DISPLAY_OFFSET_LIMIT_PX = 1.0


class _VptDriftMixin:
    """Measured-drift readout + display guarantee. Mixed into ``VideoParticleTrackingUI``."""

    def _with_measured_drift(self, tracks):
        """Link-step (worker thread): measure the drift and store it BESIDE the detected positions — the
        tracks keep the beads' centroids. Returns ``(tracks, drift)``."""
        from pycat.toolbox.vpt.measured_drift import measure_drift, attach_drift
        drift = measure_drift(tracks, self._mpx())
        return attach_drift(tracks, drift, self._mpx()), drift

    def _tracks_for_msd(self, tracks):
        """Step 5: the tracks the MSD / viscosity use — drift-subtracted only when the apply box is ticked.
        The stored tracks, and everything drawn from them, keep the detected centroids."""
        box = getattr(self, '_drift_apply', None)
        if box is None or not box.isChecked():
            return tracks
        from pycat.toolbox.vpt.measured_drift import drift_corrected_for_msd
        return drift_corrected_for_msd(tracks)

    def _picker_drift_and_display_check(self, tracks, img_layer, mpp, drift):
        """Link-step (GUI thread): the pickable bead layer, the drift readout, and the on-the-beads check."""
        try:
            self._add_pickable_bead_points(tracks, img_layer, mpp)
        except Exception as e:                       # broad-ok: ui_cleanup — the picker is a convenience layer
            print(f"[PyCAT VPT] pickable bead layer failed: {e}")
        self._show_measured_drift(drift)
        try:
            self._check_display_on_beads(tracks, img_layer, mpp)
        except (ValueError, IndexError, TypeError) as e:
            print(f"[PyCAT VPT] display check skipped: {e}")

    def _show_measured_drift(self, drift):
        """Readout in the Step 4 panel, the apply box set to the verdict, and a drift-vs-frame plot dock."""
        if not drift:
            return
        self._dr()['vpt_drift'] = drift
        box = getattr(self, '_drift_apply', None)
        if box is not None:
            box.setChecked(bool(drift.get('real')))
        lbl = getattr(self, '_drift_readout', None)
        if lbl is not None:
            net = drift.get('net_px', (0.0, 0.0))
            lbl.setText(f"{drift.get('verdict', '')}  Net (dy, dx) = ({net[0]:.2f}, {net[1]:.2f}) px from "
                        f"{drift.get('n_ref_tracks', 0)} long tracks. "
                        + ("Ticked — the MSD will be corrected; the image is not moved."
                           if drift.get('real') else "Not applied (tick to apply anyway)."))
        print(f"[PyCAT VPT] measured drift: {drift.get('verdict', '')}")
        self._plot_measured_drift(drift)

    def _plot_measured_drift(self, drift):
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        except ImportError:
            return
        frames = np.asarray(drift.get('frames', []))
        if not len(frames):
            return
        fig = Figure(figsize=(4.2, 2.6), tight_layout=True)
        ax = fig.add_subplot(111)
        ax.plot(frames, drift['dy_px'], color='#4c72b0', lw=1.2, label='dy')
        ax.plot(frames, drift['dx_px'], color='#dd8452', lw=1.2, label='dx')
        noise = drift.get('noise_px')
        if noise is not None and np.isfinite(noise):
            ax.axhspan(-noise, noise, color='0.85', zorder=0, label=f'noise ±{noise:.2f} px')
        ax.axhline(0, color='0.5', lw=0.6)
        ax.set_xlabel('frame', fontsize=9)
        ax.set_ylabel('drift (px)', fontsize=9)
        ax.set_title(("REAL — applied to MSD" if drift.get('real') else "not real — not applied")
                     + f" · peak {drift.get('peak_px', 0):.2f} px", fontsize=9)
        ax.tick_params(labelsize=8)
        ax.legend(fontsize=8, frameon=False)
        canvas = FigureCanvasQTAgg(fig)
        try:
            if getattr(self, '_drift_dock', None) is not None:
                self.viewer.window.remove_dock_widget(self._drift_dock)
        except (RuntimeError, AttributeError, KeyError, ValueError):
            pass                                     # stale dock reference → just add a fresh one
        self._drift_dock = self.viewer.window.add_dock_widget(canvas, name="Measured drift", area='right')

    def _check_display_on_beads(self, tracks, img_layer, mpp, n_frames=3):
        """The display guarantee, checked on the data: on a few frames, the median distance from each drawn
        track position to the local intensity centroid in the bead image. Warns loudly if the markers are
        off the beads. Returns the median offset (px) or None when it cannot be checked."""
        from pycat.toolbox.vpt.measured_drift import centroid_offsets_px
        if img_layer is None or tracks is None or tracks.empty:
            return None
        data = img_layer.data
        if getattr(data, 'ndim', 0) != 3:
            return None
        ycol = 'y_um_raw' if 'y_um_raw' in tracks else 'y_um'
        xcol = 'x_um_raw' if 'x_um_raw' in tracks else 'x_um'
        frames = np.unique(tracks['frame'].to_numpy().astype(int))
        picks = frames[np.linspace(0, len(frames) - 1, min(n_frames, len(frames))).astype(int)]
        offs = []
        for f in picks:
            sub = tracks[tracks['frame'] == f]
            if f >= data.shape[0] or sub.empty:
                continue
            yx = np.column_stack([sub[ycol].to_numpy(float), sub[xcol].to_numpy(float)]) / mpp
            offs.append(centroid_offsets_px(np.asarray(data[f]), yx))
        if not offs:
            return None
        med = float(np.nanmedian(np.concatenate(offs)))
        self._dr()['vpt_display_offset_px'] = med
        if med > DISPLAY_OFFSET_LIMIT_PX:
            from napari.utils.notifications import show_warning
            show_warning(f"VPT display check FAILED: the drawn bead positions sit a median {med:.2f} px from the "
                         f"beads' intensity centroids (limit {DISPLAY_OFFSET_LIMIT_PX:g} px). The image layer "
                         f"'{img_layer.name}' may not be the stack that was detected — check the bead channel.")
        else:
            print(f"[PyCAT VPT] display check: drawn positions sit {med:.2f} px (median) from the bead centroids.")
        return med
