"""
Basic image-operation widgets mixin for ToolboxFunctionsUI.

Holds the pure image-transform widget builders (rescale intensity, invert,
upscaling, rolling-ball + Gaussian background removal) — the self-contained
"take an image, apply an operation" tools, grouped with the other image
processing/filtering widgets rather than the __init__-coupled base I/O (open,
save, measure line, pre-process, calibration), which stay in the main class.
Split out of ui_modules.ToolboxFunctionsUI; methods moved verbatim and inherited
via the mixin, so behaviour is unchanged.
"""

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy, QCheckBox)

from pycat.toolbox.image_processing_tools import (
    run_apply_rescale_intensity, run_invert_image, run_upscaling_func,
    run_rb_gaussian_background_removal)


class _ImageOpsWidgetsMixin:
    """Basic image-transform widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_apply_rescale_intensity(self, layout=None, separate_widget=False):
        """Add a widget for rescaling image intensity values, optionally in a separate dock."""
        rescale_intensity_layout = QVBoxLayout()
        self.add_text_label(rescale_intensity_layout, 'Rescale Intensity', bold=True) # Add widget title label
        self.add_text_label(rescale_intensity_layout, 'Output Min') # Add a text label
        out_min_input = QLineEdit() # Create a text input
        out_min_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rescale_intensity_layout.addWidget(out_min_input) # Add the text input to the layout
        self.add_text_label(rescale_intensity_layout, 'Output Max') # Add a text label
        out_max_input = QLineEdit() # Create a text input
        out_max_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rescale_intensity_layout.addWidget(out_max_input) # Add the text input to the layout
        rescale_intensity_button = QPushButton("Rescale Intensity") # Create a button widget
        rescale_intensity_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        rescale_intensity_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_rescale_intensity, None, out_min_input, out_max_input, self.viewer))
        rescale_intensity_layout.addWidget(rescale_intensity_button) # Add the button to the layout
        rescale_intensity_widget = QWidget()
        rescale_intensity_widget.setLayout(rescale_intensity_layout)
        self._add_widget_to_layout_or_dock(rescale_intensity_widget, layout, separate_widget, "Rescale Intensity Dock")


    def _add_run_invert_image(self, layout=None, separate_widget=False):
        """Add a widget for inverting image intensity values, optionally in a separate dock."""
        invert_image_layout = QVBoxLayout()
        self.add_text_label(invert_image_layout, 'Invert Image', bold=True) # Add widget title label
        invert_image_button = QPushButton("Invert Image") # Create a button widget
        invert_image_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_invert_image, None, self.viewer))
        invert_image_layout.addWidget(invert_image_button) # Add the button to the layout
        invert_image_widget = QWidget()
        invert_image_widget.setLayout(invert_image_layout)
        self._add_widget_to_layout_or_dock(invert_image_widget, layout, separate_widget, "Invert Image Dock")


    def _add_run_upscaling(self, layout=None, separate_widget=False):
        """Add a widget for upscaling images, optionally in a separate dock."""
        upscaling_layout = QVBoxLayout()
        self.add_text_label(upscaling_layout, 'Upscale Images', bold=True) # Add widget title label
        upscaling_checkbox = QCheckBox("Update Data Class") # Add a checkbox for updating the data class
        upscaling_checkbox.setChecked(True) # Set the checkbox to checked by default
        upscaling_layout.addWidget(upscaling_checkbox) # Add the checkbox to the layout
        upscaling_button = QPushButton("Run Upscaling") # Create a button widget
        def _on_upscaling():
            # run_upscaling_func operates on viewer.layers.selection (the
            # highlighted set), not just the single active layer — record
            # all selected layer names so replay knows what was upscaled.
            selected_names = [l.name for l in self.viewer.layers.selection
                              if l is not None]
            self.on_general_button_clicked(
                run_upscaling_func, None, upscaling_checkbox,
                self.central_manager.active_data_class, self.viewer)
            self._record('upscaling', {
                'update_data_class': upscaling_checkbox.isChecked(),
                'selected_layers': selected_names,
            })
        upscaling_button.clicked.connect(_on_upscaling)
        try:
            from pycat.ui.field_status import button_with_circle
            _ups_wrapped = button_with_circle(upscaling_button, optional=True)  # yellow → blue on run
            self._upscaling_status = _ups_wrapped
            upscaling_layout.addWidget(_ups_wrapped)
        except Exception:
            upscaling_layout.addWidget(upscaling_button) # Add the button to the layout
        upscaling_widget = QWidget()
        upscaling_widget.setLayout(upscaling_layout)
        self._add_widget_to_layout_or_dock(upscaling_widget, layout, separate_widget, "Upscaling Dock")


    # Background and Noise Correction Functions


    def _add_run_rb_gaussian_background_removal(self, layout=None, separate_widget=False):
        """Add a widget for rolling-ball and Gaussian background removal, optionally in a separate dock."""
        remove_background_layout = QVBoxLayout()
        self.add_text_label(remove_background_layout, 'RB-Gauss Background Removal', bold=True) # Add widget title label
        eq_int_checkbox = QCheckBox("Equalize Intensity") # Add a checkbox for equalizing intensity
        eq_int_checkbox.setChecked(False) # Set the checkbox to unchecked by default
        remove_background_layout.addWidget(eq_int_checkbox) # Add the checkbox to the layout   
        remove_background_button = QPushButton("Remove Background") # Create a button widget
        remove_background_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        remove_background_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_rb_gaussian_background_removal, None, eq_int_checkbox, self.central_manager.active_data_class, self.viewer))
        remove_background_layout.addWidget(remove_background_button) # Add the button to the layout
        remove_background_widget = QWidget()
        remove_background_widget.setLayout(remove_background_layout)
        self._add_widget_to_layout_or_dock(remove_background_widget, layout, separate_widget, "Background Removal Dock")


    def _add_run_reference_subtraction(self, layout=None, separate_widget=False):
        """Reference / background subtraction widget.

        Subtracts a reference pattern from every frame of an input stack (or a 2D
        image), in a modality-appropriate way, and adds the corrected result as a
        new layer. The reference can be a frame WITHIN the input stack (static-
        pattern removal) or a SEPARATE image loaded via Add Image (a clear field
        of the same view). Exports the result as TIFF or MP4.
        """
        from PyQt5.QtWidgets import (QRadioButton, QButtonGroup, QSpinBox,
                                     QDoubleSpinBox, QFileDialog)
        import numpy as np

        lay = QVBoxLayout()
        self.add_text_label(lay, 'Reference / Background Subtraction', bold=True)
        self.add_text_label(
            lay, 'Subtract a reference pattern from every frame. Brightfield '
                 'keeps the gray baseline; fluorescence preserves the background '
                 'floor + noise and softens the subtraction so signal is not '
                 'driven below zero.')

        # --- input layer ---
        self.add_text_label(lay, 'Input image / stack')
        input_dd = self.create_layer_dropdown(napari.layers.Image)
        lay.addWidget(input_dd)

        # --- reference source ---
        self.add_text_label(lay, 'Reference source')
        ref_internal = QRadioButton("A frame within the input (frame index below)")
        ref_external = QRadioButton("A separate image layer (e.g. via Add Image)")
        ref_internal.setChecked(True)
        ref_grp = QButtonGroup(lay); ref_grp.addButton(ref_internal); ref_grp.addButton(ref_external)
        lay.addWidget(ref_internal); lay.addWidget(ref_external)

        idx_row = QHBoxLayout()
        idx_row.addWidget(QLabel("Reference frame index:"))
        ref_idx = QSpinBox(); ref_idx.setRange(0, 100000); ref_idx.setValue(0)
        idx_row.addWidget(ref_idx)
        idx_w = QWidget(); idx_w.setLayout(idx_row); lay.addWidget(idx_w)

        self.add_text_label(lay, 'External reference layer (used if selected above)')
        ref_dd = self.create_layer_dropdown(napari.layers.Image)
        lay.addWidget(ref_dd)

        # --- modality ---
        self.add_text_label(lay, 'Modality')
        mode_bf = QRadioButton("Brightfield (subtract pattern, keep gray baseline)")
        mode_fl = QRadioButton("Fluorescence (preserve floor + noise, no negatives)")
        mode_bf.setChecked(True)
        mode_grp = QButtonGroup(lay); mode_grp.addButton(mode_bf); mode_grp.addButton(mode_fl)
        lay.addWidget(mode_bf); lay.addWidget(mode_fl)

        # --- advanced: clip fraction (fluorescence) ---
        adv_row = QHBoxLayout()
        adv_row.addWidget(QLabel("Max clip fraction (%) [fluorescence]:"))
        clip_pct = QDoubleSpinBox()
        clip_pct.setDecimals(3); clip_pct.setRange(0.001, 1.0)
        clip_pct.setSingleStep(0.001); clip_pct.setValue(0.010)  # 0.01% default
        clip_pct.setToolTip(
            "Fluorescence only. The subtraction is softened so no more than this "
            "fraction of pixels clips at zero. If the applied strength drops well "
            "below 100%%, the reference is likely too bright / mismatched for this "
            "data.")
        adv_row.addWidget(clip_pct)
        adv_w = QWidget(); adv_w.setLayout(adv_row); lay.addWidget(adv_w)

        def _resolve_inputs():
            iname = input_dd.currentText()
            if iname not in [l.name for l in self.viewer.layers]:
                napari.utils.notifications.show_warning("Select an input layer."); return None
            from pycat.file_io.file_io import materialize_stack
            stack = materialize_stack(self.viewer.layers[iname].data)
            stack = np.asarray(stack, dtype=np.float32)
            # Reference resolution.
            rebuild = None
            if ref_internal.isChecked():
                if stack.ndim != 3:
                    napari.utils.notifications.show_warning(
                        "Frame-index reference needs a (T,H,W) stack."); return None
                ri = int(np.clip(ref_idx.value(), 0, stack.shape[0] - 1))
                reference = stack[ri]; rebuild = ri
            else:
                rname = ref_dd.currentText()
                if rname not in [l.name for l in self.viewer.layers]:
                    napari.utils.notifications.show_warning(
                        "Select an external reference layer."); return None
                ref_data = np.asarray(materialize_stack(self.viewer.layers[rname].data),
                                      dtype=np.float32)
                if ref_data.ndim == 3:
                    ref_data = ref_data[0]  # use first frame of a stack reference
                reference = ref_data
                # shape check
                fshape = stack.shape[-2:] if stack.ndim == 3 else stack.shape
                if reference.shape != tuple(fshape):
                    napari.utils.notifications.show_warning(
                        f"Reference shape {reference.shape} != frame shape {tuple(fshape)}."); return None
            mode = 'brightfield' if mode_bf.isChecked() else 'fluorescence'
            return stack, reference, mode, rebuild, iname

        def _apply(add_layer=True):
            from pycat.toolbox.temperature_tools import reference_subtraction
            r = _resolve_inputs()
            if r is None:
                return None
            stack, reference, mode, rebuild, iname = r
            corrected, info = reference_subtraction(
                stack, reference, mode=mode,
                clip_fraction=clip_pct.value() / 100.0,
                rebuild_reference_index=rebuild)
            if add_layer:
                nm = f"{iname} (ref-subtracted, {mode})"
                self.viewer.add_image(corrected, name=nm)
                msg = f"Added '{nm}'."
                if mode == 'fluorescence':
                    a = info.get('alpha', 1.0)
                    msg += (f" Subtraction strength {a*100:.0f}%%"
                            + ("" if a > 0.98 else
                               " — softened; reference may be too bright for this data."))
                napari.utils.notifications.show_info(msg)
            return corrected

        run_btn = QPushButton("Apply subtraction \u2192 new layer")
        run_btn.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        run_btn.clicked.connect(lambda: _apply(add_layer=True))
        lay.addWidget(run_btn)

        # --- export ---
        self.add_text_label(lay, 'Export the subtracted result')
        exp_row = QHBoxLayout()
        tiff_btn = QPushButton("Export TIFF")
        mp4_btn = QPushButton("Export MP4")
        for b in (tiff_btn, mp4_btn):
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        exp_row.addWidget(tiff_btn); exp_row.addWidget(mp4_btn)
        exp_w = QWidget(); exp_w.setLayout(exp_row); lay.addWidget(exp_w)

        def _export_tiff():
            corrected = _apply(add_layer=False)
            if corrected is None:
                return
            out, _ = QFileDialog.getSaveFileName(
                None, "Save subtracted TIFF", "reference_subtracted.tiff",
                "TIFF (*.tiff *.tif)")
            if not out:
                return
            try:
                import tifffile
                arr = np.asarray(corrected)
                # Preserve float32; downstream PyCAT reads floats fine. Compress:
                # lossless, costs a few ms, and float stacks are large.
                tifffile.imwrite(out, arr.astype(np.float32), compression='zlib')
                napari.utils.notifications.show_info(f"Saved {out}")
            except Exception as e:
                napari.utils.notifications.show_warning(f"TIFF export failed: {e}")

        def _export_mp4():
            corrected = _apply(add_layer=False)
            if corrected is None:
                return
            arr = np.asarray(corrected)
            if arr.ndim != 3:
                napari.utils.notifications.show_warning(
                    "MP4 export needs a stack (a single 2D frame has nothing to play).")
                return
            out, _ = QFileDialog.getSaveFileName(
                None, "Save subtracted MP4", "reference_subtracted.mp4", "MP4 (*.mp4)")
            if not out:
                return
            try:
                # Match the temperature module's MP4 backend (imageio.v3 + pyav):
                # normalize to 8-bit grayscale → RGB and write frame-by-frame.
                import imageio.v3 as iio
                a = arr.astype(np.float32)
                mn, mx = float(a.min()), float(a.max())
                a8 = (np.zeros_like(a, dtype=np.uint8) if mx <= mn else
                      ((a - mn) / (mx - mn) * 255).astype(np.uint8))
                with iio.imopen(out, "w", plugin="pyav") as writer:
                    writer.init_video_stream("libx264", fps=15)
                    for fr in a8:
                        rgb = np.stack([fr, fr, fr], axis=-1)  # gray→RGB
                        writer.write_frame(np.ascontiguousarray(rgb))
                napari.utils.notifications.show_info(f"Saved {out}")
            except Exception as e:
                napari.utils.notifications.show_warning(f"MP4 export failed: {e}")

        tiff_btn.clicked.connect(_export_tiff)
        mp4_btn.clicked.connect(_export_mp4)

        w = QWidget(); w.setLayout(lay)
        self._add_widget_to_layout_or_dock(w, layout, separate_widget,
                                           "Reference Subtraction Dock")
