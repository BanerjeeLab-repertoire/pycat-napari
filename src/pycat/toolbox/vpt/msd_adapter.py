"""VPT UI — MSD-plot methods (per-track highlight/hit-testing, track-length histogram, plot selection callback), extracted from vpt_ui.py (behaviour-preserving move).

A mixin so ``vpt_ui.py`` composes it instead of implementing it. Bodies are UNCHANGED; they use
``self`` (resolved by the composed class) and the imports below (copied verbatim from vpt_ui).
"""
from __future__ import annotations
try:
    from pycat.ui.field_status import label_with_circle
except Exception:
    label_with_circle = lambda t,**k: t
import numpy as np

from pycat.utils.pixel_size import pixel_size_um_or_default
import pandas as pd
import napari
from napari.utils.notifications import (
    show_info    as napari_show_info,
    show_warning as napari_show_warning,
)
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QProgressBar,
    QScrollArea, QSizePolicy, QRadioButton, QComboBox, QLineEdit,
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt


class _VptMsdMixin:
    """VPT MSD-plot methods. Mixed into ``VideoParticleTrackingUI``."""

    def _on_selection_plot(self, selection):
        tid = self._track_of(selection)
        if tid is None:
            return
        try:
            self._highlight_track_in_plot(tid)
        except Exception as e:
            print(f"[PyCAT VPT] link→plot failed: {e}")

    def _highlight_track_in_plot(self, track_id):
        """Emphasise a track's MSD curve in the live plot (if one is open and its
        line map was registered). No-op if the plot isn't showing.

        Uses the plot's blit path (redraw only the changed lines) rather than a
        full-figure redraw, so image/table -> plot highlighting is as fast as a
        direct plot click."""
        reg = getattr(self, '_msd_line_registry', None)
        if not reg:
            return
        lines = reg.get('lines'); canvas = reg.get('canvas')
        state = reg.setdefault('state', {'prev': None})
        blit = reg.get('blit_highlight')
        if lines is None:
            return
        prev = state.get('prev')

        ln = lines.get(int(track_id))
        changed = False          # did the LINE SET change (promote/demote)? then blit is invalid
        if ln is None:
            # Not in the representative sample — promote it to a focus curve so it can be shown.
            promote = reg.get('promote')
            if promote is not None:
                try:
                    ln = promote(int(track_id))
                    changed = ln is not None
                except Exception:
                    ln = None
        if ln is prev:
            return   # already highlighted — nothing to redraw

        # Un-highlight the previous track: a promoted focus curve is REMOVED when it stops being
        # selected; a sample line is restored to the faint base style.
        if prev is not None:
            demote = reg.get('demote_line')
            if demote is not None and demote(prev):
                changed = True
            elif prev in lines.values():
                try:
                    prev.set_color('#4c72b0'); prev.set_alpha(0.18); prev.set_linewidth(0.8)
                    prev.set_zorder(1)
                except Exception:
                    pass

        state['prev'] = ln
        if ln is not None:
            try:
                ln.set_color('#ff8c00'); ln.set_alpha(1.0); ln.set_linewidth(2.2)
                ln.set_zorder(5)
            except Exception:
                pass
        try:
            if changed or blit is None:
                if canvas is not None:
                    canvas.draw_idle()     # the line set changed — a blit assumes a fixed set
            elif ln is not None:
                blit(ln, prev)             # fast path: both lines are sampled
        except Exception:
            pass

    def _update_tracklen_hist(self, tracks):
        """Draw the track-length (frames-per-track) histogram in a popped-out
        dock widget. A healthy link has many long tracks; a fragmentation-prone
        linker piles up very short ones. Called after each link."""
        try:
            if tracks is None or 'track_id' not in tracks or tracks.empty:
                return
            import numpy as _np
            lengths = tracks.groupby('track_id').size().values
            n_frames = int(tracks['frame'].nunique()) if 'frame' in tracks else 0
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            fig = Figure(figsize=(4.2, 2.6), tight_layout=True)
            ax = fig.add_subplot(111)
            Lmax = int(lengths.max()) if len(lengths) else 1
            nbins = int(_np.clip(Lmax, 5, 40))
            ax.hist(lengths, bins=nbins, color='#4c72b0',
                    edgecolor='#2b4a72', linewidth=0.4)
            med = float(_np.median(lengths)) if len(lengths) else 0.0
            ax.axvline(med, color='#c44e52', ls='--', lw=1,
                       label=f"median {med:.0f}")
            if n_frames > 0:
                frac_long = float(_np.mean(lengths >= 0.5 * n_frames))
                ax.set_title(
                    f"{len(lengths)} tracks · {frac_long*100:.0f}% span ≥½ movie",
                    fontsize=9)
            else:
                ax.set_title(f"{len(lengths)} tracks", fontsize=9)
            ax.set_xlabel("track length (frames)", fontsize=9)
            ax.set_ylabel("count", fontsize=9)
            ax.tick_params(labelsize=8)
            ax.legend(fontsize=8, frameon=False)
            canvas = FigureCanvasQTAgg(fig)
            # Reuse a single dock so repeated links replace it instead of stacking.
            try:
                if getattr(self, '_tracklen_dock', None) is not None:
                    self.viewer.window.remove_dock_widget(self._tracklen_dock)
            except Exception:
                pass
            self._tracklen_dock = self.viewer.window.add_dock_widget(
                canvas, name="Track lengths", area='right')
        except Exception as _e:
            print(f"[PyCAT VPT] track-length histogram skipped: {_e}")
