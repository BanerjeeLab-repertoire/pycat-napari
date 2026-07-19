"""VPT UI — the pure-layout panel builders, extracted from vpt_ui.py (behaviour-preserving move).

These are the ``_add_*`` / ``setup_ui`` Qt-construction methods: no logic, just widgets and layout.
They live here as a mixin so ``vpt_ui.py`` composes them instead of implementing them — the file is
construction-and-wiring only. Bodies are UNCHANGED from vpt_ui.py; they use ``self`` (resolved by
the composed class) and the imports below (copied verbatim from vpt_ui so no name is missing).
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


class _VptPanelsMixin:
    """The VPT panel-construction methods. Mixed into ``VideoParticleTrackingUI``."""

    def _build_per_track_metrics(self, tracks, ptc):
        """Per-track summary rows: track_id, n_frames, duration, and a per-track
        D/α from a quick power-law fit of that track's own MSD curve. Returns a
        DataFrame (one row per track) for the linked per-track table."""
        import numpy as _np
        import pandas as _pd
        rows = []
        dt = self._frame_dt.value()
        # group per-track MSD once
        by_tid = {tid: g.sort_values('lag_s')
                  for tid, g in ptc.groupby('track_id')} if ptc is not None else {}
        for tid, g in tracks.groupby('track_id'):
            if tid < 0:
                continue
            n = int(len(g))
            dur = n * dt
            D = _np.nan; alpha = _np.nan
            mg = by_tid.get(tid)
            if mg is not None and len(mg) >= 3:
                lag = mg['lag_s'].values.astype(float)
                msd = mg['msd_um2'].values.astype(float)
                ok = (lag > 0) & (msd > 0)
                if ok.sum() >= 3:
                    # log-log linear fit: slope = alpha, intercept -> D (msd=4Dτ^α)
                    p = _np.polyfit(_np.log(lag[ok]), _np.log(msd[ok]), 1)
                    alpha = float(p[0])
                    D = float(_np.exp(p[1]) / 4.0)
            rows.append({'track_id': int(tid), 'n_frames': n,
                         'duration_s': round(dur, 3),
                         'D_um2_per_s': (round(D, 5) if D == D else None),
                         'alpha': (round(alpha, 3) if alpha == alpha else None)})
        return _pd.DataFrame(rows).sort_values('track_id').reset_index(drop=True)

    def _add_host_segmentation(self, layout):
        grp  = QGroupBox("Step 2 — Segment Host Condensate")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the channel showing the condensate (host) phase. The mask "
            "is eroded inward so beads near the interface — where fusion and "
            "surface flow corrupt bulk diffusion — are excluded.</span>")
        note.setWordWrap(True); form.addRow(note)

        # Host mode selector. Not all data has a companion host channel:
        #   • Host channel   — segment a separate condensate channel (default).
        #   • No host        — no condensate boundary at all (e.g. beads-in-
        #                      glycerol viscosity controls); track every bead
        #                      across the full frame.
        #   • Infer from beads — (experimental / not yet enabled) synthesize a
        #                      host region from the bead distribution when the
        #                      condensate is real but unlabelled.
        self._rb_mode_host   = QRadioButton("Host channel")
        self._rb_mode_nohost = QRadioButton("No host (full frame)")
        self._rb_mode_infer  = QRadioButton("Infer from beads")
        self._rb_mode_host.setChecked(True)
        self._rb_mode_infer.setToolTip(
            "Infer an unlabelled host boundary from the bead distribution "
            "(density + watershed + a physical size gate). Detect beads first "
            "(Step 3), then run 'Infer Host from Beads' here. Only condensates "
            "large enough for boundary-free bulk diffusion are kept.")
        self._rb_mode_nohost.setToolTip(
            "No condensate boundary — track all beads across the whole field. "
            "Use for bulk-medium controls (e.g. beads diffusing in glycerol).")
        mode_row = QHBoxLayout()
        for rb in (self._rb_mode_host, self._rb_mode_nohost, self._rb_mode_infer):
            mode_row.addWidget(rb)
        mode_row.addStretch()
        mode_w = QWidget(); mode_w.setLayout(mode_row)
        form.addRow("Host mode:", mode_w)
        for rb in (self._rb_mode_host, self._rb_mode_nohost, self._rb_mode_infer):
            rb.toggled.connect(self._on_host_mode_changed)

        # Physics gate for inferred hosts: minimum condensate radius for a bead
        # to sample bulk diffusion without feeling the interface. Only used in
        # 'Infer from beads' mode.
        from qtpy.QtWidgets import QDoubleSpinBox
        self._min_cond_radius = QDoubleSpinBox()
        self._min_cond_radius.setRange(0.5, 100.0)
        self._min_cond_radius.setValue(5.0)
        self._min_cond_radius.setSingleStep(0.5)
        self._min_cond_radius.setSuffix(" µm")
        self._min_cond_radius.setToolTip(
            "Minimum condensate radius (µm) to keep. Beads in condensates "
            "smaller than this feel boundary/interface effects and don't report "
            "bulk viscosity, so small condensates are discarded. Edge-clipped "
            "condensates are judged by their projected (circle-fit) radius.")
        self._min_cond_radius_row = self._min_cond_radius  # keep a handle
        form.addRow("Min condensate radius:", self._min_cond_radius)

        self._host_dd = self.create_layer_dropdown(napari.layers.Image)
        self._host_dd.setToolTip("Fluorescence channel that labels the condensate host phase.")
        form.addRow("Host channel:", self._host_dd)

        self._seg_method = QSpinBox()  # placeholder swap below with combobox-like radios
        method_row = QHBoxLayout()
        self._rb_otsu     = QRadioButton("Otsu")
        self._rb_triangle = QRadioButton("Triangle")
        self._rb_li       = QRadioButton("Li")
        self._rb_otsu.setChecked(True)
        for rb in (self._rb_otsu, self._rb_triangle, self._rb_li):
            method_row.addWidget(rb)
        method_row.addStretch()
        mw = QWidget(); mw.setLayout(method_row)
        mw.setToolTip("Global threshold method for the host phase.")
        form.addRow("Threshold:", mw)

        self._erosion_spin = QSpinBox()
        self._erosion_spin.setRange(0, 100); self._erosion_spin.setValue(5)
        self._erosion_spin.setToolTip(
            "Erosion depth in pixels. Beads within this distance of the "
            "condensate edge are excluded. Use ~1-2× bead radius + margin.")
        form.addRow("Interface erosion (px):", self._erosion_spin)

        self._host_prog = QProgressBar(); self._host_prog.setVisible(False)
        self._seg_btn = QPushButton("▶  Segment Host & Erode")
        self._seg_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._seg_btn.clicked.connect(self._on_segment_host)
        form.addRow(self._host_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(self._seg_btn))
        layout.addWidget(grp)
        self._on_host_mode_changed()  # set initial enabled/label state

    def _add_bead_detection(self, layout):
        grp  = QGroupBox("Step 3 — Detect Beads")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        note = QLabel(
            "<span style='color:#aaa;font-size:9pt;'>"
            "Select the bead channel. Beads are found per frame by "
            "Laplacian-of-Gaussian blob detection; only beads inside the "
            "eroded host mask are kept.</span>")
        note.setWordWrap(True); form.addRow(note)

        self._bead_dd = self.create_layer_dropdown(napari.layers.Image)
        self._bead_dd.setToolTip("Fluorescence channel showing the probe beads.")
        form.addRow("Bead channel:", self._bead_dd)

        self._min_sigma = QDoubleSpinBox()
        self._min_sigma.setRange(0.5, 20); self._min_sigma.setValue(1.0)
        self._min_sigma.setSingleStep(0.5); self._min_sigma.setDecimals(1)
        self._min_sigma.setToolTip("Smallest bead scale (px). Bead radius ≈ √2·sigma.")
        form.addRow("Min sigma (px):", self._min_sigma)

        self._max_sigma = QDoubleSpinBox()
        self._max_sigma.setRange(0.5, 40); self._max_sigma.setValue(5.0)
        self._max_sigma.setSingleStep(0.5); self._max_sigma.setDecimals(1)
        self._max_sigma.setToolTip("Largest bead scale (px). Bead radius ≈ √2·sigma.")
        form.addRow("Max sigma (px):", self._max_sigma)

        self._bead_thresh = QDoubleSpinBox()
        self._bead_thresh.setRange(0.001, 1.0); self._bead_thresh.setValue(0.02)
        self._bead_thresh.setSingleStep(0.005); self._bead_thresh.setDecimals(3)
        self._bead_thresh.setToolTip("Detection sensitivity. Lower = detect more (dimmer) beads.")
        form.addRow("Threshold:", self._bead_thresh)

        from qtpy.QtWidgets import QComboBox
        self._quality_mode = QComboBox()
        self._quality_mode.addItem("Fast (template match) — recommended", "fast")
        self._quality_mode.addItem("Fast fit (bounded Gaussian, quick)", "fast_fit")
        self._quality_mode.addItem("Precise fit (full Gaussian, slow)", "precise")
        self._quality_mode.setCurrentIndex(0)
        self._quality_mode.setToolTip(
            "How bead quality/classification is measured:\n"
            "• Fast — empirical-PSF template + cross-correlation. Seconds/minutes "
            "for a long movie. Best default for throughput.\n"
            "• Fast fit — a real Gaussian fit with a tight iteration cap.\n"
            "• Precise fit — full Gaussian fit; highest precision, slowest "
            "(can take many minutes on a long movie).")
        form.addRow("Detection mode:", self._quality_mode)
        self._quality_mode.currentIndexChanged.connect(self._on_quality_mode_changed)

        self._subpixel = QCheckBox("Sub-pixel centres")
        self._subpixel.setChecked(True)
        self._subpixel.setToolTip(
            "Refine each bead centre to sub-pixel precision with a cheap "
            "intensity centroid (fast mode). Off = integer blob centres.")
        form.addRow(self._subpixel)

        self._template_per_frame = QCheckBox("Rebuild PSF template per frame (drift/SMLM)")
        self._template_per_frame.setChecked(False)
        self._template_per_frame.setToolTip(
            "Fast mode builds one empirical PSF template per stack by default "
            "(fastest; correct when the PSF is stable). Enable to rebuild the "
            "template every frame — adapts to focus drift, useful for SMLM-like "
            "data, slightly slower.")
        form.addRow(self._template_per_frame)

        # Physical bead size (nm). Sets the template patch size (converted to px
        # via the pixel size) and the ring de-duplication merge radius.
        self._bead_size_nm = QDoubleSpinBox()
        self._bead_size_nm.setRange(0.0, 100000.0); self._bead_size_nm.setValue(200.0)
        self._bead_size_nm.setSingleStep(50.0); self._bead_size_nm.setDecimals(0)
        self._bead_size_nm.setSuffix(" nm")
        self._bead_size_nm.setToolTip(
            "Physical bead diameter in nanometres. Converted to pixels using the "
            "loaded pixel size to size the detection template and the ring-merge "
            "radius. 0 = do not use a physical size (fall back to sigma-based).")
        form.addRow("Bead size:", self._bead_size_nm)

        self._template_type = QComboBox()
        self._template_type.addItem("Empirical PSF (from data) — recommended", "empirical")
        self._template_type.addItem("Airy model (Bessel J₁, ringed beads)", "airy")
        self._template_type.setCurrentIndex(0)
        self._template_type.setToolTip(
            "Template used for fast-mode matching:\n"
            "• Empirical — measured from the cleanest beads in your data "
            "(best default; adapts to your actual PSF).\n"
            "• Airy model — an analytic diffraction pattern (central disk + "
            "ring). Use when large beads show a visible Airy ring, so one bead "
            "matches as a single object instead of the ring being detected "
            "separately.")
        form.addRow("Template:", self._template_type)

        self._dedup_rings = QCheckBox("Merge duplicate ring/multi-scale detections")
        self._dedup_rings.setChecked(True)
        self._dedup_rings.setToolTip(
            "A single large bead can trigger several detections (at multiple "
            "scales, or on its Airy ring). Merge detections that fall within "
            "about one bead radius, keeping the brightest (the bead centre). "
            "Uses the bead size above to set the merge radius.")
        form.addRow(self._dedup_rings)

        # ── Advanced: classification strictness (viscosity-dependent) ────────
        # Dim detections are routed to the out-of-plane (yellow) bin. How
        # aggressively depends on the sample: in a viscous sample (~3 Pa·s and
        # above, the default) beads move slowly and a dim spot is almost always
        # a bead drifting out of focus, so a firm dim gate is correct. In a
        # low-viscosity sample (approaching water) beads cross the focal plane
        # quickly and the same gate would wrongly bin real beads — so this is an
        # exposed control, hidden by default to keep the common case simple.
        self._strictness_row = QWidget()
        _sr = QHBoxLayout(self._strictness_row)
        _sr.setContentsMargins(0, 0, 0, 0)
        self._strictness = QDoubleSpinBox()
        self._strictness.setRange(0.2, 3.0); self._strictness.setValue(1.0)
        self._strictness.setSingleStep(0.1); self._strictness.setDecimals(1)
        self._strictness.setToolTip(
            "Classification strictness for the dim / out-of-plane (yellow) bin.\n"
            "1.0 (default) is tuned for viscous samples (~3 Pa·s and above),\n"
            "where beads move slowly and dim spots are usually out of focus.\n"
            "Lower it (toward 0.2) for less viscous / faster samples so fewer\n"
            "real beads are pushed to yellow; raise it for an even stricter\n"
            "dim gate. Stable dim tracks are promoted back to singlet after\n"
            "linking regardless of this value.")
        _sr.addWidget(QLabel("Strictness (viscosity):"))
        _sr.addWidget(self._strictness)
        self._strictness_row.setVisible(False)   # hidden until 'Advanced' toggled

        self._show_advanced = QCheckBox("Show advanced detection options")
        self._show_advanced.setChecked(False)
        self._show_advanced.toggled.connect(self._strictness_row.setVisible)
        form.addRow(self._show_advanced)
        form.addRow(self._strictness_row)


        # ── Which bead population drives microrheology ───────────────────────
        # The three classes are never silently mixed. Green (singlets) is the
        # correct default for Stokes-Einstein viscosity. Yellow (out-of-plane)
        # can be measured on its own to check whether it agrees, then optionally
        # combined with green. Red (aggregates) is always a separate readout —
        # its size would bias viscosity — never in the viscosity population.
        self._pop_choice = QComboBox()
        self._pop_choice.addItem("Green (singlets) — recommended", "singlet")
        self._pop_choice.addItem("Yellow (out-of-plane) only", "out_of_plane")
        self._pop_choice.addItem("Green + Yellow (combine)", "singlet+out_of_plane")
        self._pop_choice.setCurrentIndex(0)
        self._pop_choice.setToolTip(
            "Which detected population to link and measure for viscosity.\n"
            "• Green (singlets): clean, in-focus single beads — the correct\n"
            "  default; Stokes-Einstein assumes a known single-bead size.\n"
            "• Yellow (out-of-plane) only: measure the dim/defocused population\n"
            "  on its own, to check whether it gives a consistent viscosity\n"
            "  before trusting or combining it.\n"
            "• Green + Yellow: combine both, once you've confirmed yellow agrees.\n"
            "Aggregates (red) are always tracked separately and never included\n"
            "in the viscosity population.")
        form.addRow("Microrheology population:", self._pop_choice)

        self._bead_prog = QProgressBar(); self._bead_prog.setVisible(False)
        btn = QPushButton("▶  Detect Beads")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_detect_beads)
        form.addRow(self._bead_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
        layout.addWidget(grp)

    def _add_tracking(self, layout):
        grp  = QGroupBox("Step 4 — Link Trajectories")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        method_grp = QGroupBox("Linker")
        ml = QVBoxLayout(method_grp)
        ml.setContentsMargins(4, 20, 4, 4); ml.setSpacing(3)
        self._rb_trackmate = QRadioButton("TrackMate LAP  (recommended)")
        self._rb_trackmate.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._rb_bayesian  = QRadioButton("Bayesian / Hungarian")
        self._rb_greedy    = QRadioButton("Greedy nearest-neighbour")
        self._rb_trackmate.setChecked(True)
        self._rb_trackmate.setToolTip(
            "Real TrackMate LAP tracker via embedded Fiji. Requires "
            "pip install pycat-napari[trackmate] + a JDK. Falls back to "
            "Bayesian if unavailable.\n\n"
            "VALIDATED: TrackMate-through-PyCAT recovers viscosity within ~10% "
            "of the reference workflow. This is the recommended linker for "
            "quantitative viscosity/microrheology.")
        self._rb_bayesian.setToolTip(
            "PyCAT's native Bayesian/Hungarian linker with gap closing.\n\n"
            "Since 1.5.335 (gap off-by-one fix + auto-estimated linking distance) "
            "this produces clean full-length tracks and reproduces the reference "
            "viscosity on validated data when the bead motion is small relative to "
            "the inter-bead spacing. It does frame-to-frame assignment, so its "
            "reliability depends on that ratio \u2014 see the linking-conditions tag "
            "below, which is computed from your data. For fast/dense beads (high "
            "ratio), prefer TrackMate LAP (global optimisation).")
        self._rb_greedy.setToolTip(
            "Fast greedy nearest-neighbour linker.\n\n"
            "Since 1.5.335 (gap off-by-one fix + auto-estimated linking distance) "
            "it produces clean tracks when bead motion is small relative to "
            "inter-bead spacing. It commits to the nearest match each frame with no "
            "global optimisation, so it is the most sensitive of the three to "
            "identity ambiguity when beads move far or sit close together \u2014 see "
            "the linking-conditions tag below (computed from your data). For "
            "fast/dense beads, prefer TrackMate LAP.")
        for rb in (self._rb_trackmate, self._rb_bayesian, self._rb_greedy):
            ml.addWidget(rb)

        # Live linking-conditions tag: the frame-to-frame linkers (greedy,
        # Bayesian) are reliable only when a bead's per-frame displacement is
        # small relative to the nearest-neighbour spacing. The ratio
        # R = motion / NN_spacing is the governing quantity (displacement alone
        # is not — a fast bead is trivially linkable if neighbours are far). This
        # label is filled after Detect Beads from the measured motion and
        # spacing, so the warning is specific to the user's movie, not generic.
        self._link_cond_lbl = QLabel("")
        self._link_cond_lbl.setWordWrap(True)
        self._link_cond_lbl.setStyleSheet("QLabel { font-size: 11px; }")
        self._link_cond_lbl.setToolTip(
            "Ratio R = per-frame bead displacement / nearest-neighbour spacing, "
            "measured from your detections (no tracking needed). Frame-to-frame "
            "nearest-neighbour linking (greedy, Bayesian) is reliable when a bead's "
            "step is small versus the distance to its neighbours:\n"
            "  R < 0.10  SAFE   — NN linking reliable\n"
            "  0.10–0.25 CAUTION — mostly fine, occasional identity swaps\n"
            "  0.25–0.50 RISKY  — identity ambiguous; prefer TrackMate LAP (global)\n"
            "  R > 0.50  UNSAFE — frame-to-frame linking unreliable; use TrackMate "
            "LAP or acquire at a faster frame rate to shrink the displacement.\n\n"
            "This does not block any linker — it reports the conditions so you can "
            "choose. TrackMate's global optimisation tolerates higher R than the "
            "greedy/Bayesian frame-to-frame linkers.")
        ml.addWidget(self._link_cond_lbl)
        form.addRow(method_grp)

        self._max_link = QDoubleSpinBox()
        self._max_link.setRange(0.01, 50)
        # Default max linking distance ≈ 2× the bead diameter (a bead should not
        # move more than about its own size between frames in a viscous medium;
        # linking farther invites mis-links). Derived from the Step-3 bead size
        # when available, else a small sensible fallback.
        try:
            _bead_um = (self._bead_size_nm.value() / 1000.0
                        if hasattr(self, '_bead_size_nm') else 0.2)
        except Exception:
            _bead_um = 0.2
        self._max_link.setValue(max(0.05, round(2.0 * _bead_um, 3)))
        self._max_link.setSingleStep(0.05); self._max_link.setDecimals(3)
        self._max_link.setToolTip(
            "Maximum bead displacement between frames (µm). Auto-filled after "
            "Detect Beads from the measured per-frame bead MOTION (a short-window "
            "time-projection width vs the single-frame PSF width gives the "
            "displacement scale), times the margin factor k below. A too-small "
            "value clips the beads' own jitter and shatters stable beads into "
            "short tracks; this estimate is set from the data. Editable — override "
            "if needed.")
        form.addRow("Max linking dist (µm):", self._max_link)

        # Advanced: margin factor k on the auto-estimated linking distance.
        self._link_k = QDoubleSpinBox()
        self._link_k.setRange(1.0, 6.0); self._link_k.setValue(2.5)
        self._link_k.setSingleStep(0.5); self._link_k.setDecimals(1)
        self._link_k.setToolTip(
            "Margin factor applied to the measured per-frame bead motion when "
            "auto-setting the max linking distance (distance ≈ k × motion σ). "
            "Higher k = more tolerant linking (bridges bigger jumps, risks "
            "mis-links in dense fields); lower k = stricter. 2.5 is a good "
            "default. Takes effect on the next Detect Beads run.")
        self._link_k_row = QWidget()
        _lk = QFormLayout(self._link_k_row)
        _lk.setContentsMargins(0, 0, 0, 0); _lk.setSpacing(5)
        _lk.addRow("Linking-distance margin k:", self._link_k)
        self._link_k_row.setVisible(False)
        self._show_link_adv = QCheckBox("Show advanced linking options")
        self._show_link_adv.setChecked(False)
        self._show_link_adv.toggled.connect(self._link_k_row.setVisible)
        form.addRow(self._show_link_adv)
        form.addRow(self._link_k_row)

        # ── The default was 0, and the reasoning was backwards ──────────────────
        #
        # The old tooltip said bridging a gap is DANGEROUS — that a bead which vanishes and
        # reappears is "more likely a broken trajectory that should be pruned". **Ground truth
        # says the opposite** (tests/test_linkers.py, 1.5.477: the first test the linkers have
        # ever had).
        #
        # With objects whose true identity is known, a detector that misses 10 % of frames turns
        # **20 objects into 92 tracks at gap=0**, with only **49 %** of detections keeping their
        # identity:
        #
        #     dropout   gap   purity     tracks (true = 20)
        #     10 %      0     **49 %**   **92**
        #     10 %      1     87 %       32
        #     10 %      **3** **99 %**   **21**
        #     20 %      0     **29 %**   **147**
        #     20 %      **3** **99 %**   **21**
        #
        # And it is SAFE: **zero mixed tracks at any gap** on separated objects. Bridging repairs
        # a break; it does not invent a link. (A mixed track WOULD be dangerous — it injects a
        # spurious jump into the MSD and deflates the viscosity — which is presumably the fear
        # the old default was built on. The fear is real; the setting was aimed at the wrong
        # end.)
        #
        # **On Gable's real bead data it moves the answer.** Against the 8.325 Pa·s reference:
        # gap=0 gives 10.14, gap=1 gives 7.97, and **gap=2-3 gives 8.54-8.57 (2.6 %)** with
        # alpha = 0.97 — closer to the Brownian 1.0 than gap=1's 0.93.
        #
        # Default 2: enough to bridge the ~15 % dropout measured on real bead data, without
        # reaching so far that a genuinely lost bead is stitched to a new one.
        self._max_gap = QSpinBox()
        self._max_gap.setRange(0, 20); self._max_gap.setValue(2)
        self._max_gap.setToolTip(
            "Max frames a bead can vanish and still be reconnected.\n\n"
            "**Default 2.** Detection drops beads — around 15% of frames on real data — and a "
            "linker that cannot bridge that SHATTERS the tracks. Measured against known "
            "identities: a 10% dropout turns 20 objects into 92 tracks at gap=0, with only 49% "
            "of detections keeping their identity. At gap=3 it is 21 tracks and 99%.\n\n"
            "Bridging is safe on separated objects: zero mixed tracks at any gap. It repairs a "
            "break rather than inventing a link.\n\n"
            "On the reference bead data (true viscosity 8.325 Pa\u00b7s): gap=0 gives 10.1, "
            "gap=1 gives 8.0, gap=2-3 gives 8.5. Raise it if your detection is poor; lower it "
            "only if beads are close enough to be confused with each other.")
        form.addRow("Max frame gap:", self._max_gap)

        self._track_prog = QProgressBar(); self._track_prog.setVisible(False)
        btn = QPushButton("▶  Link Trajectories")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_link)
        form.addRow(self._track_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))

        layout.addWidget(grp)

    def _add_microrheology(self, layout):
        grp  = QGroupBox("Step 5 — Microrheology (MSD → Viscosity)")
        form = QFormLayout(grp)
        form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

        self._frame_dt = QDoubleSpinBox()
        self._frame_dt.setRange(0.0001, 3600); self._frame_dt.setValue(0.1)
        self._frame_dt.setDecimals(4); self._frame_dt.setSingleStep(0.01)
        self._frame_dt.setToolTip(
            "Time between frames (seconds). Auto-filled from the file's metadata "
            "when available (OME TimeIncrement / per-plane DeltaT / MicroManager "
            "Interval_ms); edit to override.")
        self._frame_dt_touched = False
        self._frame_dt.valueChanged.connect(
            lambda _v: setattr(self, '_frame_dt_touched', True))
        form.addRow("Frame interval (s):", self._frame_dt)

        self._bead_radius = QDoubleSpinBox()
        self._bead_radius.setRange(0.001, 5.0); self._bead_radius.setValue(0.1)
        self._bead_radius.setDecimals(3); self._bead_radius.setSingleStep(0.01)
        self._bead_radius.setToolTip(
            "Probe bead radius (µm). 20nm–2µm typical → 0.01–1.0 µm radius.\n\n"
            "Viscosity is INVERSELY proportional to this: η = kT/(6πRD). A radius "
            "that is 30% wrong makes the viscosity 30% wrong.")
        form.addRow("Bead radius (µm):", self._bead_radius)

        # Provenance of the radius. Recorded because η ∝ 1/R, so the radius is not a
        # nuisance parameter — it propagates linearly into the answer. A dropdown, not
        # a free-text field: it is structured (so it can be batched, queried and
        # exported) and it is one click rather than something to retype every run.
        self._radius_source = QComboBox()
        self._radius_source.addItems(["manufacturer", "calibrated", "metadata",
                                      "assumed"])
        self._radius_source.setToolTip(
            "Where this radius came from. Recorded with the result.\n\n"
            "• manufacturer — the bead specification / datasheet (the usual case).\n"
            "• calibrated — measured against a standard on this instrument.\n"
            "• metadata — read from the acquisition file.\n"
            "• assumed — a default or a guess; the result inherits that uncertainty.\n\n"
            "Note: do NOT derive the radius from the imaged blob. The blob is the bead "
            "CONVOLVED WITH THE PSF — for a 200 nm bead at ~1.2 NA the PSF is comparable "
            "to the bead itself, so the apparent size is dominated by the optics. You "
            "would be measuring the microscope, and the viscosity would come out too "
            "LOW. Comparing the apparent size to the spec as a SANITY CHECK is good "
            "practice (it catches a wrong vial or aggregates); using it as the input "
            "is not.\n\n"
            "Example of a fully-specified radius: 0.100 µm, manufacturer spec, ±5%.")
        form.addRow("Radius source:", self._radius_source)

        self._radius_note = QLineEdit()
        self._radius_note.setPlaceholderText("optional — e.g. cat# / lot, or ±5%")
        self._radius_note.setToolTip(
            "Optional free text stored with the result: a catalogue number, a lot, a "
            "tolerance. Leave blank if the source dropdown says enough.")
        form.addRow("Radius note:", self._radius_note)

        self._temp_C = QDoubleSpinBox()
        self._temp_C.setRange(-20, 100); self._temp_C.setValue(24.0)
        self._temp_C.setDecimals(1); self._temp_C.setSingleStep(0.5)
        self._temp_C.setToolTip("Temperature (°C) for the Stokes-Einstein relation.")
        form.addRow("Temperature (°C):", self._temp_C)

        self._min_track = QSpinBox()
        # Default from the physics, not from this widget — see
        # `MIN_TRACK_LENGTH_FRAMES` for the lag-window derivation. The spinbox used
        # to default to 5, which is one usable lag: no slope to fit.
        from pycat.toolbox.condensate_physics_tools import MIN_TRACK_LENGTH_FRAMES
        self._min_track.setRange(2, 10000); self._min_track.setValue(MIN_TRACK_LENGTH_FRAMES)
        self._min_track.setToolTip(
            "Minimum track length (frames) to include in the MSD.\n\n"
            f"Default {MIN_TRACK_LENGTH_FRAMES}. MSD lags are computed out to "
            "n_frames/4, and the 95% CI on D is honest at ~30 lags but delivers only "
            "78% coverage at 4 — so a usable fit needs 30x4 = 120 frames minimum. "
            f"{MIN_TRACK_LENGTH_FRAMES} frames gives 50 lags, with headroom for gappy "
            "tracks.\n\n"
            "Lower it and the log-log slope loses the lag window it needs to separate "
            "the diffusive part from the localisation-noise floor (MSD = 4Dt^a + "
            "4*sigma_loc^2) — alpha becomes unconstrained and D collapses to a single "
            "noisy displacement variance.\n\n"
            "Rejected tracks are reported, including whether they look like the linker "
            "lost the bead rather than the bead being absent.")
        form.addRow("Min track length:", self._min_track)

        # Drift-correction mode (#9). COM subtraction is standard for
        # microrheology but removes REAL collective motion (internal flow,
        # sedimentation, bulk translation) along with stage drift — so the choice
        # is explicit and recorded, not always-on.
        self._drift_mode = QComboBox()
        self._drift_mode.addItem("Ensemble COM (standard)", "com")
        self._drift_mode.addItem("Immobile-reference (flow-safe)", "immobile_reference")
        self._drift_mode.addItem("None (keep collective motion)", "none")
        self._drift_mode.setToolTip(
            "How to remove drift before the MSD:\n"
            "• Ensemble COM — subtract the mean displacement of all beads "
            "(classic microrheology). Removes stage drift AND any real bulk flow.\n"
            "• Immobile-reference — estimate drift from only the most stationary "
            "tracks, so genuinely flowing/diffusing beads don't bias the "
            "correction. Safer when real motion is present.\n"
            "• None — no correction; use when collective flow IS the signal "
            "(e.g. internal-flow studies).")
        form.addRow("Drift correction:", self._drift_mode)

        # Out-of-plane handling (#10). A defocused bead's axial fluctuations can
        # masquerade as in-plane motion and bias viscosity. Recovered out-of-plane
        # (yellow) beads are already excluded from viscosity unless the population
        # selector includes them — but the temporal-stability pass promotes stable
        # dim tracks back to singlet, which can fold a persistent defocused bead
        # into the viscosity set. This makes that promotion explicit.
        self._promote_stable = QCheckBox("Promote stable dim tracks to singlet")
        self._promote_stable.setChecked(True)
        self._promote_stable.setToolTip(
            "When on, a dim (out-of-plane) track that persists stably across "
            "frames is treated as a real faint bead and included in the singlet "
            "viscosity population. Turn OFF for a stricter singlet-only viscosity "
            "that never merges defocused beads — safer if axial fluctuations of "
            "out-of-focus beads might bias the measurement.")
        form.addRow(self._promote_stable)

        # ── Advanced: viscoelastic moduli (G'/G'') options ───────────────────
        # Hidden by default (mirrors the 'Show advanced detection options'
        # pattern above). The G'/G'' point estimate always uses the Evans (2009)
        # conversion; these controls add optional bootstrap confidence bands.
        self._moduli_boot = QCheckBox("Bootstrap G′/G″ confidence intervals")
        self._moduli_boot.setChecked(False)
        self._moduli_boot.setToolTip(
            "Estimate uncertainty on the storage/loss moduli by resampling whole "
            "tracks with replacement and re-computing G′/G″ for each resample; "
            "the plot then shows shaded confidence bands. This is the honest "
            "response to noisy data — it shows which parts of the spectrum are "
            "trustworthy. Bands are approximate (empirical coverage runs a little "
            "below nominal). Adds compute time proportional to the resample count.")
        self._moduli_nboot = QSpinBox()
        self._moduli_nboot.setRange(20, 2000); self._moduli_nboot.setValue(200)
        self._moduli_nboot.setSingleStep(50)
        self._moduli_nboot.setToolTip(
            "Number of bootstrap resamples for the G′/G″ confidence bands. More "
            "resamples = smoother, more stable bands but longer compute. 200 is a "
            "reasonable default; raise for a final figure.")
        self._moduli_boot_row = QWidget()
        _mb = QFormLayout(self._moduli_boot_row)
        _mb.setContentsMargins(0, 0, 0, 0); _mb.setSpacing(5)
        _mb.addRow(self._moduli_boot)
        _mb.addRow("Bootstrap resamples:", self._moduli_nboot)

        # ── Lag-window fit gate (MSD → D/α fit range) ────────────────────────
        # The reliable MSD lag window is bounded by hardware: high-frequency
        # cutoff = frame interval, low-frequency cutoff = acquisition duration.
        # Fitting outside it gives a wrong D/α. These pick the upper-lag rule and
        # whether to clip the fit to the defensible band (warn, not block).
        self._lag_rule = QComboBox()
        self._lag_rule.addItem("Fraction of track length", "fraction")
        self._lag_rule.addItem("Fixed frequency window", "fixed")
        self._lag_rule.addItem("Minimum independent pairs", "min_pairs")
        self._lag_rule.setToolTip(
            "How to set the upper-lag (low-frequency) cutoff of the MSD fit:\n"
            "• Fraction of track length — fit lags up to a fraction of the record "
            "length; longer lags have too few independent samples. Standard, "
            "conservative.\n"
            "• Fixed frequency window — restrict to a hardware-defensible band "
            "(set the upper lag in seconds). Matches routine lab practice.\n"
            "• Minimum independent pairs — keep a lag only while enough independent "
            "trajectories span it. Adapts to how many/how long your tracks are.")
        self._lag_fraction = QDoubleSpinBox()
        self._lag_fraction.setRange(0.02, 1.0); self._lag_fraction.setValue(0.25)
        self._lag_fraction.setDecimals(2); self._lag_fraction.setSingleStep(0.05)
        self._lag_fraction.setToolTip(
            "Upper lag = this fraction × longest track duration (Fraction rule).")
        self._lag_fixed_s = QDoubleSpinBox()
        self._lag_fixed_s.setRange(0.001, 1e6); self._lag_fixed_s.setValue(10.0)
        self._lag_fixed_s.setDecimals(3)
        self._lag_fixed_s.setToolTip(
            "Upper lag in seconds (Fixed-window rule). E.g. 10 s for a "
            "0.1–10 s defensible band at 0.1 s/frame.")
        self._lag_minpairs = QSpinBox()
        self._lag_minpairs.setRange(2, 1000); self._lag_minpairs.setValue(10)
        self._lag_minpairs.setToolTip(
            "Keep lags spanned by at least this many independent tracks "
            "(Minimum-independent-pairs rule).")
        self._lag_confine = QCheckBox("Confine fit to scientifically defensible bounds")
        self._lag_confine.setChecked(True)
        self._lag_confine.setToolTip(
            "When ON (default), the MSD fit is clipped to the hardware-defensible "
            "lag window (frame interval → the upper-lag rule above). When OFF, the "
            "full available lag range is fit at your own risk. Either way, PyCAT "
            "WARNS (never blocks) if the acquisition can't cover the window.")
        _mb.addRow("Upper-lag rule:", self._lag_rule)
        _mb.addRow("  fraction:", self._lag_fraction)
        _mb.addRow("  fixed upper lag (s):", self._lag_fixed_s)
        _mb.addRow("  min independent pairs:", self._lag_minpairs)
        _mb.addRow(self._lag_confine)
        self._moduli_boot_row.setVisible(False)   # hidden until toggled

        self._show_moduli_adv = QCheckBox("Show advanced fit / moduli options")
        self._show_moduli_adv.setChecked(False)
        self._show_moduli_adv.toggled.connect(self._moduli_boot_row.setVisible)
        form.addRow(self._show_moduli_adv)
        form.addRow(self._moduli_boot_row)

        # Plot layout preference: one consolidated 2×2 window (default) vs separate
        # pop-out windows. There is also a live toggle button on the consolidated
        # window, so this only sets the initial layout.
        self._plots_consolidated = QCheckBox("Show all plots in one window (2×2)")
        self._plots_consolidated.setChecked(True)
        self._plots_consolidated.setToolTip(
            "ON (default): MSD, Evans G′/G″, centered trajectories, and the van "
            "Hove displacement distribution appear together in one 2×2 figure "
            "(with a button to pop them into separate windows). OFF: each plot "
            "opens in its own resizable window, and the MSD plot supports "
            "click-a-track-to-reveal-it-in-the-viewer.")
        form.addRow(self._plots_consolidated)

        # Fidelity-based render opt-out. By default the MSD spaghetti plot draws
        # the smallest representative sample that reproduces the full percentile
        # band (~95% fidelity) — fast and faithful, since extra lines just
        # overplot. This checkbox forces drawing EVERY track for anyone who wants
        # the literal full spaghetti (streams in progressively so it stays live).
        self._plots_draw_all = QCheckBox("Draw every track (slower; default shows a representative sample)")
        self._plots_draw_all.setChecked(False)
        self._plots_draw_all.setToolTip(
            "OFF (default): the MSD plot draws the smallest random sample of "
            "tracks that reproduces the full 10–90% spread to ~95% (labelled with "
            "the measured fidelity). The ensemble mean and fit always use ALL "
            "tracks regardless — this only affects how many faint lines are drawn. "
            "ON: draw every track (streamed in progressively).")
        form.addRow(self._plots_draw_all)

        self._rheo_prog = QProgressBar(); self._rheo_prog.setVisible(False)
        btn = QPushButton("▶  Compute MSD & Viscosity")
        btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        btn.clicked.connect(self._on_rheology)
        form.addRow(self._rheo_prog); from pycat.ui.field_status import button_with_circle as _bwc
        form.addRow(_bwc(btn))
        layout.addWidget(grp)
