"""
PyCAT Client Partition / Enrichment
=====================================
Quantify how strongly a second channel (a "client" protein or RNA) is enriched
inside condensates defined by a first channel (the "scaffold").

The single-channel bimodal partition tool (fit_bimodal_intensity) answers
"how much brighter is the dense phase than the dilute phase in the SAME
channel". This module answers the complementary, very common question: "given
condensates segmented in channel A, how concentrated is channel B inside them
versus outside" — the client enrichment / partition coefficient.

Definitions
-----------
For a client channel B, a condensate (dense) mask, and a surrounding region
(the cell, or a dilute-phase mask):

    enrichment (partition coefficient)
        = mean(B in dense) / mean(B in dilute)

    where the dilute region is (cell mask AND NOT dense mask), i.e. the same
    cell's non-condensate area. A value >1 means B is recruited into the
    condensate; ~1 means no preference; <1 means exclusion.

Both a per-condensate table (one enrichment per object, using each object's
local dilute background) and a per-cell summary are produced.

Author
------
    Banerjee Lab, SUNY Buffalo, 2026
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import scipy.ndimage as ndi

# Via the notification shim: keeps the partition/enrichment measurements
# importable and testable with no GUI stack.
from pycat.utils.notify import show_info as napari_show_info
from pycat.utils.notify import show_warning as napari_show_warning


# ---------------------------------------------------------------------------
# Core enrichment computation
# ---------------------------------------------------------------------------

#: A candidate background region is "suspect" when it is NOT meaningfully darker than the dilute phase —
#: i.e. its mean is at least this fraction of the dilute mean. Below this it is plausibly signal-free.
_BACKGROUND_SUSPECT_RATIO = 0.7


def assess_background_region(region_mean, dilute_mean, *, ratio=_BACKGROUND_SUSPECT_RATIO):
    """Is a candidate 'signal-free' background region plausibly signal-free? Returns ``(suspect, message)``.

    The dilute phase is NOT background — subtracting a region that is really dilute phase subtracts the
    partition measurement's own denominator and destroys it. So when the candidate region's intensity is
    comparable to the dilute phase (not meaningfully darker), the message states the **consequence**, not
    merely the observed similarity. It warns; it never blocks — the user may have a legitimate case."""
    if not (np.isfinite(region_mean) and np.isfinite(dilute_mean)) or dilute_mean <= 0:
        return False, None
    if region_mean >= ratio * dilute_mean:
        return True, (
            f"The selected background region (mean {region_mean:.4g}) has intensity SIMILAR to the dilute "
            f"phase (mean {dilute_mean:.4g}). If this region is inside the cell, subtracting it will "
            f"subtract the dilute phase from itself and DESTROY the partition measurement — a background "
            f"region must be OUTSIDE the cell, or a dark/blank frame. (The dilute phase is real client "
            f"signal, not background.)")
    return False, None


def client_enrichment(
    client_image: np.ndarray,
    dense_mask: np.ndarray,
    cell_mask: Optional[np.ndarray] = None,
    dilute_dilation_px: int = 0,
    dilute_gap_px: int = 0,
    background: float = 0.0,
    background_mask: Optional[np.ndarray] = None,
    calibration_curve=None,
    image_metadata: Optional[dict] = None,
    temperature_K: Optional[float] = None,
) -> dict:
    """
    Global client partition coefficient:  K = (dense − bg) / (dilute − bg).

    What "background" means here
    ----------------------------
    The only legitimate background to subtract is the **instrument / camera
    offset** — the additive signal present in every pixel regardless of sample
    (dark counts + read offset), measured from a genuinely fluorophore-free
    region (outside the cell, or a dark/blank frame). It must be subtracted
    because an additive offset b makes (C_dense+b)/(C_dilute+b) ≠ K and biases
    the ratio toward 1.

    The **dilute phase is NOT background.** In a cell the dilute phase (the
    surrounding cyto/nucleoplasm) is real client signal and is the denominator
    of the partition coefficient. Subtracting "the region outside the
    condensate" as background would be subtracting the dilute phase from itself
    and destroy the measurement. So supply `background`/`background_mask` only
    from a truly signal-free region; leave it at 0 if you have no such region
    (the dense and dilute means are then reported raw and you can subtract an
    offset yourself).

    Parameters
    ----------
    client_image : (H, W) intensity image of the client channel.
    dense_mask : (H, W) binary/int mask of the condensates.
    cell_mask : optional (H, W) mask bounding the dilute region. If None, the
        dilute region is everything outside the dense mask.
    dilute_dilation_px : if >0, the dilute region is a shell of this thickness
        around each condensate (local dilute) rather than the whole cell.
    background : scalar instrument offset to subtract (used if background_mask
        is None). 0 = no subtraction.
    background_mask : optional (H, W) mask of a signal-free region; its mean is
        used as the instrument offset (overrides `background`).

    Returns
    -------
    dict with dense_mean, dilute_mean (both background-subtracted),
        dense_mean_raw, dilute_mean_raw (before subtraction), background,
        enrichment (K), n_dense_px, n_dilute_px.
    """
    img = np.asarray(client_image, dtype=float)
    dense = np.asarray(dense_mask) > 0

    # Instrument background: from a signal-free region if given, else the scalar.
    if background_mask is not None:
        bgm = np.asarray(background_mask) > 0
        bg = float(img[bgm].mean()) if bgm.any() else float(background)
    else:
        bg = float(background)

    # ── The dilute region must be OFFSET from the dense mask, not adjacent to it ──
    #
    # A droplet edge is not sharp: the PSF gives it a halo, and the pixels immediately
    # outside the dense mask are **halo, not dilute phase**. Including them inflates the
    # dilute reference and collapses the enrichment.
    #
    # Measured, TRUE enrichment = 30, droplets with a realistic 2.5 px edge:
    #
    #     edge width    dilute_mean    enrichment
    #     sharp         100.0          **30.00**
    #     1 px          113.0          25.54
    #     2.5 px        130.0          20.66
    #     5 px          163.1          **14.86**
    #
    # **A realistic PSF halves the enrichment**, and every real droplet has one.
    #
    # `dilute_dilation_px` was meant to help and made it WORSE: it built the shell
    # IMMEDIATELY ADJACENT to the dense mask (`dilated & ~dense`) — **which is the halo
    # itself**, the worst possible choice. With a 2.5 px edge it took the enrichment from
    # 20.66 (no shell) down to **2.86**.
    #
    # The fix is the annulus GAP already used by `partition_coefficient_local` (1.5.423):
    # step AWAY from the mask before sampling.
    #
    #     dilute region                        dilute_mean    enrichment
    #     adjacent shell (the old behaviour)   1440.5         **2.86**
    #     gap 5 px, shell 6 px                 621.5          22.10
    #     **gap 10 px, shell 6 px**            600.9          **26.63**
    #     gap 15 px, shell 6 px                599.9          26.90
    if dilute_gap_px > 0 or dilute_dilation_px > 0:
        _gap = int(dilute_gap_px) if dilute_gap_px > 0 else 0
        _width = int(dilute_dilation_px) if dilute_dilation_px > 0 else 6

        inner = (ndi.binary_dilation(dense, iterations=_gap) if _gap > 0 else dense)
        outer = ndi.binary_dilation(inner, iterations=_width)
        dilute = outer & ~inner
        if cell_mask is not None:
            dilute &= (np.asarray(cell_mask) > 0)

        if _gap == 0:
            napari_show_warning(
                "Client enrichment: `dilute_dilation_px` builds the dilute shell IMMEDIATELY "
                "ADJACENT to the dense mask — which is the droplet's PSF HALO, not the dilute "
                "phase. Measured on droplets with a realistic 2.5 px edge and a TRUE "
                "enrichment of 30, that took the answer from 20.66 down to **2.86**.\n\n"
                "Pass `dilute_gap_px` (e.g. 10) to step AWAY from the mask before sampling — "
                "the same annulus gap used by `partition_coefficient_local`. With a 10 px gap "
                "the enrichment comes back to 26.6.")
    else:
        if cell_mask is not None:
            dilute = (np.asarray(cell_mask) > 0) & ~dense
        else:
            dilute = ~dense

    n_dense = int(dense.sum())
    n_dilute = int(dilute.sum())
    # Means on the RAW image (no clipping — clipping negatives after background
    # subtraction biases means that sit near the background level).
    dense_raw = float(img[dense].mean()) if n_dense else np.nan
    dilute_raw = float(img[dilute].mean()) if n_dilute else np.nan
    dense_c = dense_raw - bg
    dilute_c = dilute_raw - bg
    enrichment = (dense_c / dilute_c) if (np.isfinite(dilute_c) and dilute_c > 0) else np.nan
    # ── background = 0.0 silently asserts "there is no camera offset" ──────────
    #
    # K = (dense - bg) / (dilute - bg) is correct, and it recovers the true value exactly
    # at ANY pedestal — PROVIDED the background is supplied. The default of 0.0 asserts
    # that there is none, which is almost never true of a real detector, and the result
    # then collapses toward 1 with no warning. Measured, with a TRUE K of 30:
    #
    #     pedestal    bg NOT given    bg given
    #        0           30.00          30.00
    #      100           15.50          30.00
    #      500            5.83          30.00
    #     2000            2.38          30.00
    #
    # A 12x error, and the number looks perfectly plausible. So say so.
    if (background_mask is None and float(background) == 0.0
            and np.isfinite(dilute_c) and dilute_c > 0):
        napari_show_warning(
            "Client enrichment: no background was supplied, so K = dense/dilute with NO "
            "camera offset removed. The pedestal appears in BOTH terms and drags K toward "
            "1 — with a true K of 30 and a 500-count pedestal, this returns 5.83. Pass "
            "`background=` (the camera floor, e.g. from a dark frame) or `background_mask=` "
            "(a signal-free region). If the image genuinely has no offset — because it was "
            "already background-subtracted — this warning can be ignored.")

    # ── The background choice TRAVELS with the result ───────────────────────────
    #
    # A K computed with a dark-frame offset and one computed raw are DIFFERENT measurements; a reader must
    # be able to tell them apart, so the mode/offset/source ride in the table (and hence the consolidated
    # long table). Derived from the inputs — never a separate recording.
    if background_mask is not None:
        background_mode, background_source = 'region', 'signal-free region mask (mean)'
    elif float(background) != 0.0:
        background_mode, background_source = 'scalar', 'user scalar offset'
    else:
        background_mode, background_source = 'none', 'none (raw means, no offset removed)'

    # ── The guardrail: a "background" region that is really dilute phase would DESTROY the measurement ──
    # Reuse the existing napari warning machinery; state the CONSEQUENCE, not just the similarity. Also
    # carried in the result so a headless caller (and a test) sees the same verdict the UI shows.
    background_warning = None
    if background_mask is not None:
        suspect, message = assess_background_region(bg, dilute_raw)
        if suspect:
            background_warning = message
            napari_show_warning("Client enrichment background region — " + message)

    result = dict(dense_mean=dense_c, dilute_mean=dilute_c,
                  dense_mean_raw=dense_raw, dilute_mean_raw=dilute_raw,
                  background=bg, enrichment=enrichment,
                  n_dense_px=n_dense, n_dilute_px=n_dilute,
                  background_mode=background_mode, background_source=background_source,
                  background_warning=background_warning)

    # ── Optional CALIBRATED path — real units, gated ─────────────────────────
    #
    # Additive: with no curve this is skipped and `result` is exactly what it has always been.
    # With a curve, the dense/dilute intensities become apparent molar concentrations and their
    # ratio becomes a real K_p and a transfer free energy — BUT only through the validity gate. A
    # curve measured under a different acquisition converts nothing; it just produces a wrong number
    # of the right magnitude, which is the exact failure this codebase refuses to ship. So a
    # mismatch is reported as `calibration_validity` and the concentrations are NOT computed.
    if calibration_curve is not None:
        result.update(_calibrated_partition(
            calibration_curve, image_metadata, dense_c, dilute_c, temperature_K))

    return result


def _calibrated_partition(curve, image_metadata, dense_intensity, dilute_intensity,
                          temperature_K):
    """Concentrations + real-unit K_p + ΔG for `client_enrichment`, behind the validity gate.

    Kept out of `client_enrichment` so that function does not grow, and so the gated conversion is
    testable on its own. Returns only extra keys; on a hard block it returns the verdict and no
    concentrations — a refused calibration must not leave a plausible number behind.
    """
    from pycat.utils.calibration import (check_calibration_validity, intensity_to_concentration,
                                         delta_g_transfer)

    verdict = check_calibration_validity(curve, image_metadata or {})
    out = {'calibration_validity': {'valid': verdict.valid, 'level': verdict.level,
                                    'reason': verdict.reason}}
    if not verdict.valid:
        napari_show_warning(f"Calibrated partition: refused — {verdict.reason}. Reporting the "
                            f"intensity ratio only.")
        return out
    if verdict.level == 'warn':
        napari_show_warning(f"Calibrated partition: {verdict.reason}")

    c_dense = intensity_to_concentration(dense_intensity, curve, name='dense_concentration')
    c_dilute = intensity_to_concentration(dilute_intensity, curve, name='dilute_concentration')
    out['dense_concentration'] = c_dense
    out['dilute_concentration'] = c_dilute
    out['Kp_calibrated'] = (c_dense.value / c_dilute.value
                            if c_dilute.value not in (0, None) and c_dilute.value > 0 else float('nan'))

    if temperature_K is not None:
        try:
            out['delta_g_transfer'] = delta_g_transfer(c_dense, c_dilute, temperature_K)
        except ValueError as exc:
            out['delta_g_transfer'] = None
            napari_show_warning(f"Calibrated partition: ΔG not computed — {exc}")
    return out


def client_enrichment_per_condensate(
    client_image: np.ndarray,
    dense_labels: np.ndarray,
    cell_mask: Optional[np.ndarray] = None,
    shell_px: int = 5,
    background: float = 0.0,
) -> pd.DataFrame:
    """
    Per-condensate client enrichment, using each object's LOCAL dilute shell.

    For every labeled condensate, the dilute reference is a ring of thickness
    `shell_px` around that object (excluding all condensates), so enrichment is
    measured against the local background rather than a global one — more
    robust to intensity gradients across the field.

    Parameters
    ----------
    client_image : (H, W) client channel.
    dense_labels : (H, W) integer label image of condensates (one label each).
    cell_mask : optional bounding mask for the dilute shell.
    shell_px : thickness (px) of the local dilute ring around each object.
    background : constant subtracted from the client image.

    Returns
    -------
    DataFrame: label, area_px, client_mean_dense, client_mean_local_dilute,
               enrichment, integrated_client.
    """
    img = np.asarray(client_image, dtype=float) - float(background)
    img = np.clip(img, 0, None)
    labels = np.asarray(dense_labels)
    all_dense = labels > 0
    cm = (np.asarray(cell_mask) > 0) if cell_mask is not None else None

    rows = []
    for lbl in np.unique(labels):
        if lbl == 0:
            continue
        obj = labels == lbl
        area = int(obj.sum())
        # Local dilute shell = (obj dilated by shell_px) minus ALL condensates
        shell = ndi.binary_dilation(obj, iterations=int(shell_px)) & ~all_dense
        if cm is not None:
            shell &= cm
        dense_mean = float(img[obj].mean()) if area else np.nan
        dilute_mean = float(img[shell].mean()) if shell.any() else np.nan
        enr = (dense_mean / dilute_mean) if (dilute_mean and dilute_mean > 0) else np.nan
        rows.append({
            'label': int(lbl), 'area_px': area,
            'client_mean_dense': dense_mean,
            'client_mean_local_dilute': dilute_mean,
            'enrichment': enr,
            'integrated_client': float(img[obj].sum()),
        })
    return pd.DataFrame(rows)


def client_enrichment_per_cell(
    client_image: np.ndarray,
    dense_labels: np.ndarray,
    cell_labels: np.ndarray,
    shell_px: int = 5,
    background: float = 0.0,
) -> pd.DataFrame:
    """
    Per-cell client enrichment summary.

    For each labeled cell, computes the whole-cell enrichment (mean client in
    that cell's condensates / mean client in that cell's dilute phase) and the
    median of the per-condensate enrichments within the cell.

    Parameters
    ----------
    client_image : (H, W) client channel.
    dense_labels : (H, W) condensate label image.
    cell_labels : (H, W) cell label image.
    shell_px : local dilute-shell thickness for the per-condensate values.
    background : constant subtracted from the client image.

    Returns
    -------
    DataFrame: cell_label, n_condensates, whole_cell_enrichment,
               median_per_condensate_enrichment, dense_mean, dilute_mean.
    """
    img = np.asarray(client_image, dtype=float) - float(background)
    img = np.clip(img, 0, None)
    dense_labels = np.asarray(dense_labels)
    cell_labels = np.asarray(cell_labels)
    all_dense = dense_labels > 0

    rows = []
    for cl in np.unique(cell_labels):
        if cl == 0:
            continue
        cell = cell_labels == cl
        dense_in_cell = all_dense & cell
        dilute_in_cell = cell & ~all_dense
        dense_mean = float(img[dense_in_cell].mean()) if dense_in_cell.any() else np.nan
        dilute_mean = float(img[dilute_in_cell].mean()) if dilute_in_cell.any() else np.nan
        whole = (dense_mean / dilute_mean) if (dilute_mean and dilute_mean > 0) else np.nan

        # per-condensate enrichments within this cell
        obj_labels = np.unique(dense_labels[dense_in_cell])
        obj_labels = obj_labels[obj_labels > 0]
        per_obj = []
        for lbl in obj_labels:
            obj = dense_labels == lbl
            shell = ndi.binary_dilation(obj, iterations=int(shell_px)) & ~all_dense & cell
            if obj.any() and shell.any():
                dm = float(img[obj].mean()); lm = float(img[shell].mean())
                if lm > 0:
                    per_obj.append(dm / lm)
        rows.append({
            'cell_label': int(cl),
            'n_condensates': int(len(obj_labels)),
            'whole_cell_enrichment': whole,
            'median_per_condensate_enrichment': float(np.median(per_obj)) if per_obj else np.nan,
            'dense_mean': dense_mean, 'dilute_mean': dilute_mean,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# UI entry point (Toolbox)
# ---------------------------------------------------------------------------

def _add_client_enrichment(ui_instance, layout=None, separate_widget=False):
    """
    Widget: client/partner enrichment of a second channel inside condensates.

    Needs a client intensity image, a condensate label (or binary) mask, and
    optionally a cell label mask. Reports per-condensate and (if cells given)
    per-cell enrichment.
    """
    import napari
    from PyQt5.QtWidgets import (
        QGroupBox, QFormLayout, QLabel, QSpinBox, QDoubleSpinBox, QPushButton,
        QProgressBar, QSizePolicy)

    grp  = QGroupBox("Client Partition / Enrichment")
    form = QFormLayout(grp)
    form.setContentsMargins(4, 20, 4, 4); form.setSpacing(5)

    desc = QLabel(
        "Measures how enriched a client channel is inside condensates: "
        "mean(client in dense) / mean(client in local dilute shell). Needs a "
        "client image and a condensate mask; a cell mask enables per-cell "
        "summaries.")
    desc.setWordWrap(True)
    desc.setStyleSheet("font-size:9pt; color:#aaa; padding-bottom:4px;")
    form.addRow(desc)

    client_dd = ui_instance.create_layer_dropdown(napari.layers.Image)
    client_dd.setToolTip("Client channel (the protein/RNA whose enrichment you want).")
    form.addRow("Client channel:", client_dd)

    dense_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    dense_dd.setToolTip("Condensate mask (labels or binary) from the scaffold channel.")
    form.addRow("Condensate mask:", dense_dd)

    cell_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    cell_dd.setToolTip("Optional cell labels — enables per-cell enrichment. 'None' to skip.")
    form.addRow("Cell mask (optional):", cell_dd)

    shell_spin = QSpinBox(); shell_spin.setRange(1, 100); shell_spin.setValue(5)
    shell_spin.setToolTip("Thickness (px) of the local dilute ring around each condensate.")
    form.addRow("Dilute shell (px):", shell_spin)

    bg_spin = QDoubleSpinBox(); bg_spin.setRange(0, 1e6); bg_spin.setValue(0.0)
    bg_spin.setDecimals(2)
    bg_spin.setToolTip("Instrument offset (scalar): a constant camera/background offset subtracted from the "
                       "client image before ratioing. Leave 0 for raw means.")
    form.addRow("Background offset (scalar):", bg_spin)

    bg_region_dd = ui_instance.create_layer_dropdown(napari.layers.Labels)
    bg_region_dd.setToolTip(
        "Optional instrument offset FROM A REGION: a SIGNAL-FREE mask (OUTSIDE the cell, or a dark/blank "
        "frame) whose mean becomes the offset. This is the only legitimate background. Do NOT pick a region "
        "inside the cell — the dilute phase is client SIGNAL, not background, and subtracting it destroys "
        "the measurement (you'll be warned if the region looks like dilute phase). Overrides the scalar.")
    form.addRow("Background offset (from region):", bg_region_dd)

    prog = QProgressBar(); prog.setVisible(False)
    btn  = QPushButton("▶  Compute Enrichment")
    btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    form.addRow(prog); form.addRow(btn)

    def _on_run():
        from napari.utils.notifications import show_info as _info, show_warning as _warn
        import numpy as _np
        layers = [l.name for l in ui_instance.viewer.layers]
        iname, dname = client_dd.currentText(), dense_dd.currentText()
        if iname not in layers:
            _warn("Select a valid client image."); return
        if dname not in layers:
            _warn("Select a valid condensate mask."); return
        client = _np.asarray(ui_instance.viewer.layers[iname].data)
        dense = _np.asarray(ui_instance.viewer.layers[dname].data)
        if client.ndim != 2:
            _warn("Client enrichment currently expects a 2D image (or one slice)."); return

        # If the dense mask is binary, label it for per-condensate output
        if dense.max() <= 1:
            from scipy.ndimage import label as _label
            dense_labels, _n = _label(dense > 0)
        else:
            dense_labels = dense

        cname = cell_dd.currentText()
        cell_labels = None
        if cname != 'None' and cname in layers:
            cell_labels = _np.asarray(ui_instance.viewer.layers[cname].data)

        # Instrument offset: a signal-free REGION mask (its mean) overrides the scalar — the region path
        # also fires the guardrail inside client_enrichment (a region that is really dilute phase is
        # flagged, because subtracting it would destroy the measurement). The per-condensate/per-cell
        # variants take a scalar, so the region's own mean is passed to them for the same offset.
        rname = bg_region_dd.currentText()
        bg_mask = None
        if rname != 'None' and rname in layers:
            _region = _np.asarray(ui_instance.viewer.layers[rname].data) > 0
            if _region.any():
                bg_mask = _region
        _bg_scalar = float(client[bg_mask].mean()) if bg_mask is not None else bg_spin.value()

        prog.setVisible(True); prog.setRange(0, 0)
        try:
            per_cond = client_enrichment_per_condensate(
                client, dense_labels,
                cell_mask=(cell_labels > 0) if cell_labels is not None else None,
                shell_px=shell_spin.value(), background=_bg_scalar)
            glob = client_enrichment(
                client, dense_labels,
                cell_mask=(cell_labels > 0) if cell_labels is not None else None,
                background=bg_spin.value(), background_mask=bg_mask)
            per_cell = None
            if cell_labels is not None:
                per_cell = client_enrichment_per_cell(
                    client, dense_labels, cell_labels,
                    shell_px=shell_spin.value(), background=_bg_scalar)
        except Exception as e:
            prog.setVisible(False)
            _warn(f"Enrichment failed: {e}")
            import traceback; traceback.print_exc(); return
        prog.setVisible(False)

        try:
            ui_instance.central_manager.active_data_class.data_repository[
                'client_enrichment_df'] = per_cond
        except Exception:
            pass
        rec = getattr(ui_instance, '_record', None)
        if callable(rec):
            rec('client_enrichment', {
                'client': iname, 'condensate_mask': dname,
                'cell_mask': cname if cell_labels is not None else None,
                'global_enrichment': glob['enrichment'],
                'n_condensates': int(len(per_cond))})

        try:
            from pycat.toolbox.analysis_plots import plot_enrichment_distribution
            if len(per_cond):
                plot_enrichment_distribution(per_cond, interactive=True)
        except Exception as e:
            print(f"[PyCAT] enrichment plot failed: {e}")
        try:
            from pycat.ui.ui_utils import show_dataframes_dialog
            overview = pd.DataFrame([{
                'global enrichment': round(glob['enrichment'], 3) if glob['enrichment']==glob['enrichment'] else None,
                'dense mean': round(glob['dense_mean'], 2) if glob['dense_mean']==glob['dense_mean'] else None,
                'dilute mean': round(glob['dilute_mean'], 2) if glob['dilute_mean']==glob['dilute_mean'] else None,
                'n condensates': len(per_cond),
                'median per-condensate enrichment': round(per_cond['enrichment'].median(), 3) if len(per_cond) else None,
                # The background choice travels with the result: a K computed with an offset and one raw are
                # DIFFERENT measurements, and a reader must be able to tell them apart (spec Part C).
                'background mode': glob.get('background_mode'),
                'background source': glob.get('background_source'),
            }])
            tables = [('Overview', overview), ('Per-condensate', per_cond.round(3))]
            if per_cell is not None and not per_cell.empty:
                tables.append(('Per-cell', per_cell.round(3)))
            show_dataframes_dialog("Client Enrichment", tables)
        except Exception:
            pass
        _info(f"Global client enrichment = {glob['enrichment']:.2f}× "
              f"across {len(per_cond)} condensates (background: {glob.get('background_mode')}).")
        if glob.get('background_warning'):
            _warn("Background region — " + glob['background_warning'])

    btn.clicked.connect(_on_run)

    if layout is not None and not separate_widget:
        layout.addWidget(grp)
    else:
        from PyQt5.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QSizePolicy
        w = QWidget(); vl = QVBoxLayout(w); vl.addWidget(grp)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            from pycat.ui.ui_modules import _apply_scroll_guard
            _apply_scroll_guard(w)
        except Exception:
            pass
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setWidget(w)
        ui_instance.viewer.window.add_dock_widget(sa, name="Client Enrichment", area='right')
