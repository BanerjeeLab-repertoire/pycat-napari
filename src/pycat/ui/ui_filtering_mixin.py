"""
Preprocessing & filtering widgets mixin for ToolboxFunctionsUI.

Holds the image preprocessing / filtering widget builders (enhanced RB-Gaussian
background removal, WBNS, wavelet noise subtraction, bilateral filter, CLAHE,
FFT bandpass, im2bw, best slice, peak/edge enhancement, morphological Gaussian,
DPR, Laplacian-of-Gaussian). Split out of ui_modules.ToolboxFunctionsUI to keep
that file navigable; methods are moved verbatim and inherited via the mixin, so
behaviour is unchanged. They rely on attributes/methods provided by
BaseUIClass/ToolboxFunctionsUI at runtime (self.viewer, self.central_manager,
self.on_general_button_clicked, self._add_widget_to_layout_or_dock, etc.).
"""

import math

import napari
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit, QWidget,
    QComboBox, QSlider, QScrollArea, QSizePolicy)

from pycat.toolbox.image_processing_tools import (
    run_enhanced_rb_gaussian_bg_removal, run_wbns,
    run_wavelet_noise_subtraction, run_apply_bilateral_filter, run_clahe,
    run_peak_and_edge_enhancement, run_morphological_gaussian_filter, run_dpr,
    run_apply_laplace_of_gauss_filter)
from pycat.toolbox.fft_bandpass_tools import run_fft_bandpass, run_im2bw
from pycat.toolbox.brightfield_tools import run_best_slice


class _FilteringWidgetsMixin:
    """Preprocessing / filtering widget builders for ToolboxFunctionsUI (mixin)."""

    def _add_run_enhanced_rb_gaussian_bg_removal(self, layout=None, separate_widget=False):
        """Add a widget for rolling-ball and Gaussian background removal with edge enhancement, optionally in a separate dock."""
        remove_background_layout = QVBoxLayout()
        self.add_text_label(remove_background_layout, 'Enhanced RB-Gauss Background Removal', bold=True) # Add widget title label
        remove_background_button = QPushButton("Remove Background") # Create a button widget
        remove_background_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        def _on_enhanced_bg_removal():
            # Capture BEFORE running — previously this read the active layer
            # AFTER the operation completed, by which point napari had often
            # auto-selected the newly created output layer instead of the
            # input layer that was actually processed.
            active = self.viewer.layers.selection.active
            active_name = active.name if active is not None else ''
            self.on_general_button_clicked(
                run_enhanced_rb_gaussian_bg_removal, None,
                self.central_manager.active_data_class, self.viewer)
            dr = self.central_manager.active_data_class.data_repository
            bg_rec = {
                'active_layer': active_name,
                'ball_radius':  int(dr.get('ball_radius', 50)),
            }
            # run_enhanced_rb_gaussian_bg_removal's "already enhanced" branch
            # reads this same override from data_repository; without recording
            # it, replay_background_removal falls back to {} (library defaults)
            # instead of the user's tuned foreground-suppression slider values.
            sp = dr.get('foreground_suppression_params', None)
            if sp:
                bg_rec['foreground_suppression_params'] = dict(sp)
            self._record('background_removal', bg_rec)
        remove_background_button.clicked.connect(_on_enhanced_bg_removal)
        remove_background_layout.addWidget(remove_background_button) # Add the button to the layout
        remove_background_widget = QWidget()
        remove_background_widget.setLayout(remove_background_layout)
        self._add_widget_to_layout_or_dock(remove_background_widget, layout, separate_widget, "Enhanced Background Removal Dock")


    def _add_run_wbns(self, layout=None, separate_widget=False):
        """Add a widget for wavelet background and noise subtraction, optionally in a separate dock."""
        WBNS_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(WBNS_layout, 'Wavelet BG and Noise Subtraction', bold=True) # Add widget title label
        self.add_text_label(WBNS_layout, 'Noise Level') # Add a text label
        WBNS_noise_input = QLineEdit() # Create a text input
        WBNS_noise_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        WBNS_noise_input.setPlaceholderText('1') # Set a default text value
        WBNS_layout.addWidget(WBNS_noise_input) # Add the text input to the layout  
        self.add_text_label(WBNS_layout, 'PSF Size') # Add a text label
        WBNS_psf_input = QLineEdit() # Create a text input
        WBNS_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        WBNS_psf_input.setPlaceholderText('3') # Set a default text value
        WBNS_layout.addWidget(WBNS_psf_input) # Add the text input to the layout
        WBNS_button = QPushButton("Run WBNS") # Create a button widget
        WBNS_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_wbns, None, WBNS_psf_input, WBNS_noise_input, self.viewer)) # function, viewer, *args
        WBNS_layout.addWidget(WBNS_button) # Add the button to the layout
        WBNS_widget = QWidget() # Create a main widget to contain the input widget
        WBNS_widget.setLayout(WBNS_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(WBNS_widget, layout, separate_widget, "WBNS Dock") # Add widget to layout or dock


    def _add_run_wavelet_noise_subtraction(self, layout=None, separate_widget=False):
        """Add a widget for wavelet noise subtraction, optionally in a separate dock."""
        wavelet_layout = QVBoxLayout() # Create a vertical layout widget
        self.add_text_label(wavelet_layout, 'Wavelet Noise Subtraction', bold=True)# Add widget title label
        self.add_text_label(wavelet_layout, 'Noise Level') # Add a text label
        wavelet_noise_input = QLineEdit() # Create a text input
        wavelet_noise_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        wavelet_noise_input.setPlaceholderText('1') # Set a default text value
        wavelet_layout.addWidget(wavelet_noise_input) # Add the text input to the layout
        self.add_text_label(wavelet_layout, 'PSF Size') # Add a text label
        wavelet_psf_input = QLineEdit() # Create a text input
        wavelet_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        wavelet_psf_input.setPlaceholderText('3') # Set a default text value
        wavelet_layout.addWidget(wavelet_psf_input) # Add the text input to the layout
        wavelet_button = QPushButton("Run WNS") # Create a button widget
        wavelet_button.clicked.connect(lambda: self.on_general_button_clicked( # Connect the button to the function
            run_wavelet_noise_subtraction, None, wavelet_psf_input, wavelet_noise_input, self.viewer)) # function, viewer, *args
        wavelet_layout.addWidget(wavelet_button) # Add the button to the layout
        wavelet_widget = QWidget() # Create a main widget to contain the input widget
        wavelet_widget.setLayout(wavelet_layout) # Set the layout for the widget
        self._add_widget_to_layout_or_dock(wavelet_widget, layout, separate_widget, "WNS Dock") # Add widget to layout or dock


    def _add_run_apply_bilateral_filter(self, layout=None, separate_widget=False):
        """Add a widget for applying a bilateral filter, optionally in a separate dock."""
        bilateral_layout = QVBoxLayout()
        self.add_text_label(bilateral_layout, 'Bilateral Filter', bold=True) # Add widget title label
        self.add_text_label(bilateral_layout, 'Filter Size') # Add a text label
        filter_size_input = QLineEdit() # Create a text input
        filter_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bilateral_layout.addWidget(filter_size_input) # Add the text input to the layout
        bilateral_button = QPushButton("Apply Filter") # Create a button widget
        bilateral_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_bilateral_filter, None, filter_size_input, self.viewer))
        bilateral_layout.addWidget(bilateral_button) # Add the button to the layout
        bilateral_widget = QWidget()
        bilateral_widget.setLayout(bilateral_layout)
        self._add_widget_to_layout_or_dock(bilateral_widget, layout, separate_widget, "Bilateral Filter Dock")



    # Image Enhancement and Filter Functions


    def _add_run_clahe(self, layout=None, separate_widget=False):
        """Add a widget for contrast-limited adaptive histogram equalization, optionally in a separate dock."""
        clahe_layout = QVBoxLayout()
        self.add_text_label(clahe_layout, 'Contrast-Limited Adapt. Hist. Equalization', bold=True) # Add widget title label
        self.add_text_label(clahe_layout, 'Clip Limit') # Add a text label
        clahe_clip_input = QLineEdit() # Create a text input
        clahe_clip_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        clahe_clip_input.setPlaceholderText('0.0025') # Set a default text value
        clahe_layout.addWidget(clahe_clip_input) # Add the text input to the layout
        def_window_size = math.ceil(self.central_manager.active_data_class.data_repository['cell_diameter']//4) # Calculate the default window size
        self.add_text_label(clahe_layout, 'Window Size') # Add a text label    
        clahe_window_size_input = QLineEdit() # Create a text input
        clahe_window_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        clahe_window_size_input.setPlaceholderText(str(def_window_size)) # Set a default text value
        clahe_layout.addWidget(clahe_window_size_input) # Add the text input to the layout
        clahe_button = QPushButton("Run CLAHE") # Create a button widget
        clahe_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_clahe, None, clahe_clip_input, clahe_window_size_input,  self.viewer))
        clahe_layout.addWidget(clahe_button) # Add the button to the layout
        clahe_widget = QWidget()
        clahe_widget.setLayout(clahe_layout)
        self._add_widget_to_layout_or_dock(clahe_widget, layout, separate_widget, "CLAHE Dock")


    def _add_run_fft_bandpass(self, layout=None, separate_widget=False):
        """FFT bandpass filter — annular frequency mask for background/feature isolation."""
        fft_layout = QVBoxLayout()
        self.add_text_label(fft_layout, 'FFT Bandpass Filter', bold=True)
        self.add_text_label(fft_layout,
            'Annular frequency mask: keeps spatial frequencies between the '
            'inner and outer radii. Removes low-frequency background and '
            'high-frequency noise. Works on a 2D image or a whole stack.')
        self.add_text_label(fft_layout, 'Inner radius (low cutoff, px)')
        fft_low_input = QLineEdit(); fft_low_input.setPlaceholderText('3')
        fft_low_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fft_layout.addWidget(fft_low_input)
        self.add_text_label(fft_layout, 'Outer radius (high cutoff, px)')
        fft_high_input = QLineEdit(); fft_high_input.setPlaceholderText('40')
        fft_high_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        fft_layout.addWidget(fft_high_input)
        fft_button = QPushButton("Run FFT Bandpass")
        fft_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_fft_bandpass, None, fft_low_input, fft_high_input, self.viewer))
        fft_layout.addWidget(fft_button)
        fft_widget = QWidget(); fft_widget.setLayout(fft_layout)
        self._add_widget_to_layout_or_dock(fft_widget, layout, separate_widget, "FFT Bandpass Dock")

    def _add_run_im2bw(self, layout=None, separate_widget=False):
        """MATLAB-style manual threshold binarization (absolute intensity cutoff)."""
        bw_layout = QVBoxLayout()
        self.add_text_label(bw_layout, 'Manual Threshold (im2bw)', bold=True)
        self.add_text_label(bw_layout,
            'Binarize on an absolute intensity value you supply (pixels ≥ '
            'threshold → 1). Unlike Otsu/Li, the level is not auto-chosen.')
        self.add_text_label(bw_layout, 'Threshold value')
        bw_input = QLineEdit(); bw_input.setPlaceholderText('e.g. 0.5 or 128')
        bw_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bw_layout.addWidget(bw_input)
        bw_button = QPushButton("Binarize")
        bw_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_im2bw, None, bw_input, self.viewer))
        bw_layout.addWidget(bw_button)
        bw_widget = QWidget(); bw_widget.setLayout(bw_layout)
        self._add_widget_to_layout_or_dock(bw_widget, layout, separate_widget, "Manual Threshold Dock")

    def _add_run_best_slice(self, layout=None, separate_widget=False):
        """Extract the best (most-informative/sharpest) slice of a Z/T-stack."""
        bs_layout = QVBoxLayout()
        self.add_text_label(bs_layout, 'Best Slice Selector', bold=True)
        self.add_text_label(bs_layout,
            'Reduce a Z- or T-stack to a single representative 2D plane — the '
            'most informative slice (max std) or the sharpest (Brenner / '
            'Tenengrad). Useful before 2D segmentation of a nuclear/DAPI stack.')
        self.add_text_label(bs_layout, 'Selection metric')
        bs_method = QComboBox(); bs_method.addItems(['std', 'brenner', 'tenengrad'])
        bs_method.setToolTip(
            "std: maximum intensity spread (max-information plane).\n"
            "brenner: sharpest focus (Brenner gradient).\n"
            "tenengrad: sharpest edges (Sobel gradient magnitude).")
        bs_layout.addWidget(bs_method)
        bs_button = QPushButton("Extract Best Slice")
        bs_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        bs_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_best_slice, None, bs_method, self.viewer))
        bs_layout.addWidget(bs_button)
        bs_widget = QWidget(); bs_widget.setLayout(bs_layout)
        self._add_widget_to_layout_or_dock(bs_widget, layout, separate_widget, "Best Slice Dock")

    def _add_run_peak_and_edge_enhancement(self, layout=None, separate_widget=False):
        """Add a widget for peak and edge enhancement, optionally in a separate dock."""
        enhancement_layout = QVBoxLayout()
        self.add_text_label(enhancement_layout, 'Peak and Edge Enhancement', bold=True) # Add widget title label
        enhancement_button = QPushButton("Run Edge Enhancement") # Create a button widget
        enhancement_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        enhancement_button.clicked.connect(lambda: self.on_general_button_clicked( 
            run_peak_and_edge_enhancement, None, self.central_manager.active_data_class, self.viewer))
        enhancement_layout.addWidget(enhancement_button) # Add the button to the layout
        enhancement_widget = QWidget() 
        enhancement_widget.setLayout(enhancement_layout)
        self._add_widget_to_layout_or_dock(enhancement_widget, layout, separate_widget, "Peak and Edge Enhancement Dock")


    def _add_run_morphological_gaussian_filter(self, layout=None, separate_widget=False):
        """Add a widget for morphological Gaussian filtering, optionally in a separate dock."""
        gauss_filter_layout = QVBoxLayout()
        self.add_text_label(gauss_filter_layout, 'Morphological Gaussian Filter', bold=True) # Add widget title label
        self.add_text_label(gauss_filter_layout, 'Filter Size') # Add a text label
        filter_size_input = QLineEdit() # Create a text input
        filter_size_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        gauss_filter_layout.addWidget(filter_size_input) # Add the text input to the layout
        gauss_filter_button = QPushButton("Apply Filter") # Create a button widget
        gauss_filter_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_morphological_gaussian_filter, None, filter_size_input, self.viewer))
        gauss_filter_layout.addWidget(gauss_filter_button) # Add the button to the layout
        gauss_filter_widget = QWidget()
        gauss_filter_widget.setLayout(gauss_filter_layout)
        self._add_widget_to_layout_or_dock(gauss_filter_widget, layout, separate_widget, "Morphological Gaussian Dock")


    def _add_run_dpr(self, layout=None, separate_widget=False):
        """Add a widget for deblur by pixel reassignment, optionally in a separate dock."""
        DPR_layout = QVBoxLayout()
        self.add_text_label(DPR_layout, 'Deblur by Pixel Reassignment', bold=True)# Add widget title label
        self.add_text_label(DPR_layout, 'Gain Level') # Add a text label
        DPR_gain_input = QLineEdit() # Create a text input
        DPR_gain_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        DPR_layout.addWidget(DPR_gain_input) # Add the text input to the layout
        self.add_text_label(DPR_layout, 'PSF Size') # Add a text label
        DPR_psf_input = QLineEdit() # Create a text input
        DPR_psf_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        DPR_layout.addWidget(DPR_psf_input) # Add the text input to the layout
        DPR_button = QPushButton("Run DPR") # Create a button widget
        DPR_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_dpr, None, DPR_psf_input, DPR_gain_input, self.central_manager.active_data_class, self.viewer))
        DPR_layout.addWidget(DPR_button) # Add the button to the layout
        DPR_widget = QWidget() # Create a main widget to contain the input widget
        DPR_widget.setLayout(DPR_layout)
        self._add_widget_to_layout_or_dock(DPR_widget, layout, separate_widget, "DPR Dock")


    def _add_run_apply_laplace_of_gauss_filter(self, layout=None, separate_widget=False):
        """Add a widget for applying a Laplacian of Gaussian filter, optionally in a separate dock."""
        LoG_layout = QVBoxLayout()
        self.add_text_label(LoG_layout, 'Laplacian of Gaussian Filter', bold=True) # Add widget title label
        self.add_text_label(LoG_layout, 'Sigma Value') # Add a text label
        LoG_sigma_input = QLineEdit() # Create a text input
        LoG_sigma_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        LoG_layout.addWidget(LoG_sigma_input) # Add the text input to the layout
        LoG_button = QPushButton("Apply LoG Filter") # Create a button widget
        LoG_button.clicked.connect(lambda: self.on_general_button_clicked(
            run_apply_laplace_of_gauss_filter, None, LoG_sigma_input, self.viewer))
        LoG_layout.addWidget(LoG_button) # Add the button to the layout
        LoG_widget = QWidget()
        LoG_widget.setLayout(LoG_layout)
        self._add_widget_to_layout_or_dock(LoG_widget, layout, separate_widget, "LoG Filter Dock")
