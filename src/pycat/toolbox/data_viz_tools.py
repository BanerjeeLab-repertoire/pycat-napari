"""
Data Visualization and Plotting Tools for PyCAT

This module contains classes and functions for visualizing data using various types of plots.
The PlottingWidget class provides a GUI for selecting and visualizing data from different 
DataFrames using various types of plots. The class uses PyQt5 for the GUI and pandas, seaborn,
and matplotlib for data manipulation and plotting. 

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

from pycat.utils.general_utils import debug_log
from napari.utils.notifications import show_warning as napari_show_warning
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QComboBox, QRadioButton, QButtonGroup, QGroupBox, QHBoxLayout, QLabel, QCheckBox, QScrollArea, QLineEdit, QPushButton, QSizePolicy
from PyQt5.QtCore import Qt


class PlottingWidget(QWidget):
    """
    A widget for selecting and visualizing data from different DataFrames using various types of plots.

    Attributes
    ----------
    central_manager : CentralManager
        An instance of CentralManager to manage and provide access to data resources.
    data_class_instance : DataClass
        An active instance of DataClass used to interact with data.
    dataframes : dict
        A dictionary of pandas DataFrames indexed by name.
    layout : QVBoxLayout
        Layout for organizing UI components vertically within the widget.
    df_combo : QComboBox
        Dropdown menu to select from available DataFrames.
    line_radio, violin_radio, hist_radio : QRadioButton
        Radio buttons to select the type of plot to display.
    line_options, violin_options, hist_options : QWidget
        Widgets containing specific options for each type of plot.
    plot_button : QPushButton
        Button to trigger the plotting of selected data.
    """
    def __init__(self, central_manager):
        """
        Initializes the PlottingWidget with a reference to a CentralManager instance.

        Parameters
        ----------
        central_manager : CentralManager
            An instance of CentralManager which manages the active data classes and dataframes.
        """
        super().__init__()

        self.central_manager = central_manager
        self.data_class_instance = self.central_manager.active_data_class
        self.dataframes = self.data_class_instance.get_dataframes()

        layout = QVBoxLayout()

        # Dropdown to select DataFrame
        self.df_combo = QComboBox()
        self.df_combo.setToolTip("Choose which analysis result table (dataframe) to plot.")
        self.df_combo.addItems(self.dataframes.keys())
        self.df_combo.currentIndexChanged.connect(self.on_dataframe_changed)
        layout.addWidget(self.df_combo)

        # ── The plotting backend ────────────────────────────────────────────────
        #
        # The same data, rendered by a different library. What changes is not the picture but
        # **what you can do with it**:
        #
        #   matplotlib  the default. Click a point -> see the object.
        #   seaborn     matplotlib underneath, with statistical defaults. **Same click.**
        #   plotly      zoom, pan, legend filtering, and a hover that says which object each
        #               point is. **The click does not reach napari** without QtWebEngine — and
        #               the widget SAYS so rather than doing nothing.
        #
        # Only the backends that are actually importable are offered. An option that silently
        # fails is worse than one that is not there.
        self.backend_combo = QComboBox()
        self.backend_combo.setToolTip(
            "Which library draws the plot.\n\n"
            "matplotlib — click a point to see the object it measures.\n"
            "seaborn — the same, with statistical styling.\n"
            "plotly — zoom/pan/filter, and hover to see which object a point is "
            "(the click needs QtWebEngine).")
        try:
            from pycat.utils.plot_backends import available_backends
            self.backend_combo.addItems(
                [name for name, (ok, _) in available_backends().items() if ok])
        except Exception as _exc:
            debug_log('PlottingWidget: could not list the plotting backends', _exc)
            self.backend_combo.addItems(['matplotlib'])
        layout.addWidget(self.backend_combo)

        # Radio buttons for plot type
        self.line_radio = QRadioButton("Scatter/Line Plot")
        self.line_radio.setToolTip("Plot one column against another as points and/or a line.")
        self.violin_radio = QRadioButton("Violin Plot")
        self.violin_radio.setToolTip("Show the distribution of a column as a violin (density) plot.")
        self.hist_radio = QRadioButton("Histogram Plot")
        self.hist_radio.setToolTip("Show the distribution of a column as a histogram.")
        self.line_radio.clicked.connect(self.update_ui)
        self.violin_radio.clicked.connect(self.update_ui)
        self.hist_radio.clicked.connect(self.update_ui)
        radio_layout = QHBoxLayout()
        radio_layout.addWidget(self.line_radio)
        radio_layout.addWidget(self.violin_radio)
        radio_layout.addWidget(self.hist_radio)
        layout.addLayout(radio_layout)

        # Options for each plot type
        self.line_options = self.create_line_options()
        self.violin_options = self.create_violin_options()
        self.hist_options = self.create_hist_options()
        layout.addWidget(self.line_options)
        layout.addWidget(self.violin_options)
        layout.addWidget(self.hist_options)

        # Plot button
        self.plot_button = QPushButton("Plot")
        self.plot_button.setToolTip("Draw the plot with the current settings.")
        self.plot_button.clicked.connect(self.plot_data)
        layout.addWidget(self.plot_button)

        self.setLayout(layout)
        self.update_ui()

    def update_ui(self):
        """
        Updates the user interface elements based on the current state of the data class instance and selected options.
        Ensures that the UI components are synchronized with the current data and selections.
        """

        # Update the active data class instance
        self.data_class_instance = self.central_manager.active_data_class
        # Update the available DataFrames
        self.update_dataframes()
        # Show or hide plot options based on the selected plot type
        self.line_options.setVisible(self.line_radio.isChecked())
        self.violin_options.setVisible(self.violin_radio.isChecked())
        self.hist_options.setVisible(self.hist_radio.isChecked())
        # Update the plotting options dropdown
        self.update_plot_options_dropdowns()

    def on_dataframe_changed(self, index):
        """
        Handles the event when a new DataFrame is selected in the dropdown menu.

        Parameters
        ----------
        index : int
            The index of the newly selected DataFrame in the dropdown menu.
        """

        # This slot is called when the user selects a different DataFrame from the dropdown.
        self.update_dataframes()
        self.update_plot_options_dropdowns()


    def update_dataframes(self):
        """
        Refreshes the DataFrame selection dropdown to match the currently available DataFrames in the data class instance.
        """

        # Disconnect the signal to prevent triggering on programmatically setting items
        self.df_combo.currentIndexChanged.disconnect(self.on_dataframe_changed)

        # Get the current list of DataFrame names
        new_dataframe_names = set(self.data_class_instance.get_dataframes().keys())

        # Get the current list of items in the dropdown
        current_items = set([self.df_combo.itemText(i) for i in range(self.df_combo.count())])

        # Calculate the difference
        items_to_add = new_dataframe_names - current_items
        items_to_remove = current_items - new_dataframe_names

        # Remove items that are no longer present
        for item in items_to_remove:
            index = self.df_combo.findText(item)
            if index >= 0:
                self.df_combo.removeItem(index)

        # Add new items
        for item in items_to_add:
            self.df_combo.addItem(item)

        # Reconnect the signal after updating the dropdown
        self.df_combo.currentIndexChanged.connect(self.on_dataframe_changed)


    def update_plot_options_dropdowns(self):
        """
        Updates dropdown menus and checkboxes to reflect the columns available in the currently selected DataFrame.
        """

        # Get the name of the currently selected DataFrame
        current_df_name = self.df_combo.currentText()
        current_df_columns = self.dataframes[current_df_name].columns.tolist()

        # Clear the dropdowns for line plot
        self.line_x_combo.clear()
        self.line_y_combo.clear()
        self.hist_data_combo.clear()
        # Populate the dropdowns with the columns of the current DataFrame
        self.line_x_combo.addItems(current_df_columns)
        self.line_y_combo.addItems(current_df_columns)
        self.hist_data_combo.addItems(current_df_columns)

        # Update the checkboxes for the plots
        for checkbox in self.checkboxes:
            checkbox.deleteLater()
        self.checkboxes.clear()
        for column in current_df_columns:
            checkbox = QCheckBox(column)
            self.checkboxes.append(checkbox)
            self.checkbox_layout.addWidget(checkbox)

    def create_line_options(self):
        """
        Creates and returns a widget containing options for configuring line or scatter plots.

        Returns
        -------
        QWidget
            A widget containing UI components for setting options specific to line or scatter plots.
        """

        # Create a group box to contain the line plot options
        group = QGroupBox()
        layout = QVBoxLayout()
        # Create dropdowns for the line plot data
        self.line_x_combo = QComboBox()
        self.line_x_combo.setToolTip("Column to plot on the X axis.")
        self.line_y_combo = QComboBox()
        self.line_y_combo.setToolTip("Column to plot on the Y axis.")
        # Populate the QComboBoxes with DataFrame columns
        self.line_x_combo.addItems(self.dataframes[self.df_combo.currentText()].columns)
        self.line_y_combo.addItems(self.dataframes[self.df_combo.currentText()].columns)

        # For setting the X and Y data 
        xy_layout = QHBoxLayout()
        xy_layout.addWidget(QLabel("X values:"))
        xy_layout.addWidget(self.line_x_combo)
        xy_layout.addWidget(QLabel("Y values:"))
        xy_layout.addWidget(self.line_y_combo)
        layout.addLayout(xy_layout)

        # For linestyle and Marker style
        self.linestyle_combo = QComboBox() 
        self.linestyle_combo.setToolTip("Line style connecting the points ('None' = points only).")
        self.marker_combo = QComboBox()     
        self.marker_combo.setToolTip("Marker symbol for each data point.")
        self.linestyle_combo.addItems(["-", "--", "-.", ":", "None"])
        self.marker_combo.addItems(["o", "s", "v", "x"])
        lm_layout = QHBoxLayout()
        lm_layout.addWidget(QLabel("Linestyle:"))
        lm_layout.addWidget(self.linestyle_combo)
        lm_layout.addWidget(QLabel("Marker Style:"))
        lm_layout.addWidget(self.marker_combo)
        layout.addLayout(lm_layout)

        # For X-scale and Y-scale
        self.x_scale_combo = QComboBox()
        self.x_scale_combo.setToolTip("Linear or logarithmic X axis.")
        self.y_scale_combo = QComboBox()
        self.y_scale_combo.setToolTip("Linear or logarithmic Y axis.")
        self.x_scale_combo.addItems(["linear", "log"])
        self.y_scale_combo.addItems(["linear", "log"])
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("X-Scale:"))
        scale_layout.addWidget(self.x_scale_combo)
        scale_layout.addWidget(QLabel("Y-Scale:"))
        scale_layout.addWidget(self.y_scale_combo)
        layout.addLayout(scale_layout)

        # For X-limit and Y-limit
        self.x_limit = QLineEdit()
        self.x_limit.setToolTip("Optional X-axis range as 'min,max'. Leave blank to autoscale.")
        self.x_limit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.y_limit = QLineEdit()
        self.y_limit.setToolTip("Optional Y-axis range as 'min,max'. Leave blank to autoscale.")
        self.y_limit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel("X-Limit:"))
        limit_layout.addWidget(self.x_limit)
        limit_layout.addWidget(QLabel("Y-Limit:"))
        limit_layout.addWidget(self.y_limit)
        layout.addLayout(limit_layout)

        group.setLayout(layout)
        return group


    def create_violin_options(self):
        """
        Creates and returns a widget containing options for configuring violin plots.

        Returns
        -------
        QWidget
            A widget containing UI components for setting options specific to violin plots.
        """
        
        # Create a group box to contain the violin plot options
        group = QGroupBox()
        layout = QVBoxLayout()

        # Create a widget and layout for checkboxes
        checkbox_widget = QWidget()
        self.checkbox_layout = QHBoxLayout()  # Define the layout for checkboxes
        checkbox_widget.setLayout(self.checkbox_layout)
        # Create and add checkboxes based on DataFrame columns
        self.checkboxes = []
        for column in self.dataframes[self.df_combo.currentText()].columns:
            checkbox = QCheckBox(column)
            self.checkboxes.append(checkbox)
            self.checkbox_layout.addWidget(checkbox)  # Use self.checkbox_layout here

        # Wrap the checkboxes inside a scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)  # Set horizontal scrollbar
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # Turn off vertical scrollbar
        scroll.setWidget(checkbox_widget)
        layout.addWidget(QLabel("Data columns:"))
        layout.addWidget(scroll)

        # Side by side combo boxes for Orientation and Inner plot layout
        combo_layout = QHBoxLayout()

        # For orientation
        self.orientation_combo = QComboBox()
        self.orientation_combo.setToolTip("Vertical or horizontal violins.")
        self.orientation_combo.addItems(["v", "h"])
        combo_layout.addWidget(QLabel("Orientation:"))
        combo_layout.addWidget(self.orientation_combo)

        # For inner layout style
        self.inner_combo = QComboBox()
        self.inner_combo.setToolTip("What to draw inside each violin (box, quartiles, points, or nothing).")
        self.inner_combo.addItems(["box", "quart", "point", "stick"])
        combo_layout.addWidget(QLabel("Inner Layout:"))
        combo_layout.addWidget(self.inner_combo)

        layout.addLayout(combo_layout)

        group.setLayout(layout)
        return group


    def create_hist_options(self):
        """
        Creates and returns a widget containing options for configuring histogram plots.

        Returns
        -------
        QWidget
            A widget containing UI components for setting options specific to histogram plots.
        """

        # Create a group box to contain the histogram plot options
        group = QGroupBox()
        layout = QVBoxLayout()
        # Create a dropdown for the histogram data
        self.hist_data_combo = QComboBox()
        self.hist_data_combo.setToolTip("Column whose distribution the histogram shows.")
        self.hist_data_combo.addItems(self.dataframes[self.df_combo.currentText()].columns)
        # Create input fields for number of bins and bin width
        self.hist_bins_input = QLineEdit()
        self.hist_bins_input.setToolTip("Number of histogram bins. Leave blank to auto-select.")
        self.hist_bins_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.hist_bin_width_input = QLineEdit()
        self.hist_bin_width_input.setToolTip("Fixed bin width (overrides bin count if set).")
        self.hist_bin_width_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        # Radio buttons for 'cumulative' distribution function
        self.cumulative_true_radio = QRadioButton("True")
        self.cumulative_true_radio.setToolTip("Plot the cumulative distribution instead of the raw counts.")
        self.cumulative_false_radio = QRadioButton("False")
        self.cumulative_false_radio.setChecked(True)  # default to False
        cumulative_layout = QHBoxLayout()
        cumulative_layout.addWidget(QLabel("Cumulative:"))
        cumulative_layout.addWidget(self.cumulative_true_radio)
        cumulative_layout.addWidget(self.cumulative_false_radio)
        # Group the radio buttons so that they're mutually exclusive
        self.cumulative_group = QButtonGroup()
        self.cumulative_group.addButton(self.cumulative_true_radio)
        self.cumulative_group.addButton(self.cumulative_false_radio)

        # Radio buttons for 'kde'
        self.kde_true_radio = QRadioButton("True")
        self.kde_true_radio.setToolTip("Overlay a smooth kernel-density estimate on the histogram.")
        self.kde_false_radio = QRadioButton("False")
        self.kde_false_radio.setChecked(True)  # default to False
        kde_layout = QHBoxLayout()
        kde_layout.addWidget(QLabel("KDE:"))
        kde_layout.addWidget(self.kde_true_radio)
        kde_layout.addWidget(self.kde_false_radio)
        # Group the radio buttons so that they're mutually exclusive
        self.kde_group = QButtonGroup()
        self.kde_group.addButton(self.kde_true_radio)
        self.kde_group.addButton(self.kde_false_radio)

        # Add widgets to layout
        layout.addWidget(QLabel("Data column:"))
        layout.addWidget(self.hist_data_combo)
        layout.addWidget(QLabel("Number of bins:"))
        layout.addWidget(self.hist_bins_input)
        layout.addWidget(QLabel("Bin width:"))
        layout.addWidget(self.hist_bin_width_input)
        layout.addLayout(cumulative_layout)
        layout.addLayout(kde_layout)

        group.setLayout(layout)

        return group

    def _backend(self):
        """Which plotting backend the user selected. Defaults to matplotlib."""
        combo = getattr(self, 'backend_combo', None)
        return combo.currentText().lower() if combo is not None else 'matplotlib'

    def _show_plotly(self, df, x_col, y_col):
        """**Plotly: full interactivity, and the identity in the hover.**

        A click inside a plotly figure lives in JavaScript. Reaching napari from there needs a
        ``QWebEngineView`` and a ``QWebChannel`` — **a heavy dependency and a real Qt risk** in an
        app that already has a user hitting OpenGL/Qt rendering failures.

        So the identity goes where it works **with no bridge at all**: the hover. The user sees
        *which object* each point is — its label, its frame, the file it came from — without any
        of that machinery.

        **And when the click genuinely is not available, it says so**, rather than doing nothing.
        *Silence is the failure mode that makes people think a feature is broken.*
        """
        try:
            from pycat.utils.plot_backends import plotly_scatter, available_backends
            from pycat.utils.object_ref import refs_from_dataframe
        except Exception as exc:
            debug_log('PlottingWidget: the plotly backend is unavailable', exc)
            napari_show_warning("Plotly is not installed. Install it with: pip install plotly")
            return

        ok, message = available_backends().get('plotly', (False, 'plotly is not installed'))
        if not ok:
            napari_show_warning(f"Plotly is not available. {message}")
            return
        if message:
            napari_show_warning(message)      # e.g. "the click will not reach napari"

        try:
            refs = refs_from_dataframe(df)
            figure = plotly_scatter(df, x_col, y_col, refs=refs,
                                    title=f"{y_col} vs {x_col}")
            figure.show()
        except Exception as exc:
            debug_log('PlottingWidget: the plotly figure failed', exc)
            napari_show_warning(f"The plotly figure could not be built: {exc}")

    def _wire_brushing(self, figure, artist, df):
        """**Make the points clickable, if the rows are objects.**

        A row of a per-object results table IS an object — it carries a ``label``, a ``bbox``, and
        the file it came from (1.5.495). So a point on a plot of that table can be clicked back to
        the object it measures, and the object can be shown.

        **A row that is an AGGREGATE cannot.** A per-frame or per-cell summary row has no single
        object to point at, and this method **declines silently** rather than wiring a click that
        would land somewhere arbitrary. *A click that lands on the wrong object is worse than a
        click that does nothing — it lands, and nothing says so.*

        The resolution works in **both worlds**, and the plot does not know which:

        * a **live session** — the object is revealed in the napari viewer
        * a **batch table**, loaded from a CSV with the session long gone — the object's region is
          read straight out of the source file, and shown as a crop

        The second is only possible because the **bbox travelled with the row.**
        """
        try:
            from pycat.utils.object_ref import refs_from_dataframe
            from pycat.utils.brushing import make_pickable, crop_for_ref
        except Exception as exc:
            debug_log('PlottingWidget: the brushing machinery is unavailable', exc)
            return

        # Is this a table of OBJECTS, or a table of summaries? The bbox is the tell: a row that
        # can be located in an image has one; a row that averages forty objects cannot.
        has_bbox = ('bbox' in df.columns
                    or all(c in df.columns for c in ('bbox_y0', 'bbox_x0', 'bbox_y1', 'bbox_x1')))
        if not has_bbox:
            return

        refs = refs_from_dataframe(df)

        def _on_select(ref):
            crop, message = crop_for_ref(ref, viewer=getattr(self, 'viewer', None))
            if crop is None:
                napari_show_warning(
                    f"That point cannot be shown as an image. {message}")
                return
            try:
                viewer = getattr(self, 'viewer', None)
                if viewer is not None:
                    name = f"object {ref.object_id}"
                    if name in viewer.layers:
                        viewer.layers[name].data = crop
                    else:
                        viewer.add_image(crop, name=name)
            except Exception as exc:
                debug_log('PlottingWidget: could not show the picked object', exc)

        make_pickable(figure, artist, refs, on_select=_on_select,
                      viewer=getattr(self, 'viewer', None))

    def plot_data(self):
        """
        Generates and displays the plot based on the selected DataFrame, plot type, and associated options.
        """

        # Get the selected DataFrame
        df = self.dataframes[self.df_combo.currentText()]

        # Setup the plot if line plot is selected
        if self.line_radio.isChecked():
            x_col = self.line_x_combo.currentText() # Get the selected X column
            y_col = self.line_y_combo.currentText() # Get the selected Y column
            ls = self.linestyle_combo.currentText() # Get the selected linestyle
            ms = self.marker_combo.currentText() # Get the selected marker style
            x_scale = self.x_scale_combo.currentText() # Get the selected X scale
            y_scale = self.y_scale_combo.currentText() # Get the selected Y scale
            x_lim = self.x_limit.text() # Get the X limit
            y_lim = self.y_limit.text() # Get the Y limit
            # ── The plot the user builds HERE is the brushable one ─────────────
            #
            # This widget lets the user pick ANY results DataFrame and ANY two columns. When a row
            # of that DataFrame is ONE OBJECT — which every per-object results table now is
            # (1.5.495) — **each point on this plot IS an object**, and clicking it should show
            # that object.
            #
            # That is the natural wiring point, and it is better than hand-wiring the fifteen
            # analysis plots: those each make one fixed figure, while **anything the user plots
            # here becomes clickable for free.**
            #
            # A `picker` radius is set so matplotlib will emit a pick event at all. Without it the
            # click is swallowed and nothing happens — which is the failure mode that makes people
            # think brushing is broken.
            _line, = plt.plot(df[x_col], df[y_col], linestyle=ls, marker=ms, picker=5)
            self._wire_brushing(plt.gcf(), _line, df)

            # ── The same plot, in any backend, addressed the same way ──────────
            #
            # matplotlib / seaborn / plotly all render this. What differs is **how a click gets
            # back to Python**, and that difference is not cosmetic:
            #
            #   matplotlib  a pick event on the canvas          -> works today
            #   seaborn     **IS matplotlib** — the same canvas, the same event
            #   plotly      a JavaScript callback in a browser  -> needs a Qt↔JS bridge
            #
            # So plotly gets the identity into the **hover** instead, which needs no bridge at
            # all: the user moves the mouse over a point and sees which object it is. That is most
            # of the value, and it costs nothing.
            if self._backend() == 'plotly':
                self._show_plotly(df, x_col, y_col)
                return

            plt.xscale(x_scale)
            plt.yscale(y_scale)
            if x_lim:
                plt.xlim(x_lim)
            if y_lim:
                plt.ylim(y_lim)
            plt.xlabel(x_col)
            plt.ylabel(y_col)

        # Setup the plot if violin plot is selected
        elif self.violin_radio.isChecked():
            # Retrieve the selected columns from the checkboxes
            selected_columns = [checkbox.text() for checkbox in self.checkboxes if checkbox.isChecked()]

            # Get the orientation and inner options
            orientation = self.orientation_combo.currentText()
            inner = self.inner_combo.currentText()

            # Using seaborn for the violin plot
            if orientation == "v":
                sns.violinplot(data=df[selected_columns], cut=0, inner=inner, orient=orientation)
                plt.xlabel("Data")
            else:
                # Melt the dataframe for the selected columns
                melted_df = pd.melt(df, value_vars=selected_columns)
                sns.violinplot(data=melted_df, y="variable", x="value", cut=0, inner=inner, orient=orientation)
                #sns.violinplot(data=df, y=selected_columns, cut=0, inner=inner, orient=orientation)
                plt.ylabel("Data")

        # Setup the plot if histogram plot is selected
        elif self.hist_radio.isChecked():
            # Get the selected data column
            data_col = self.hist_data_combo.currentText()
            # Get the number of bins or bin width
            bins = None
            bin_width = self.hist_bin_width_input.text()
            # Determine if the histogram should be cumulative
            cumulative = self.cumulative_true_radio.isChecked()
            kde = self.kde_true_radio.isChecked()

            # If bin width is specified
            if bin_width:  
                bin_width = float(bin_width)
                # Calculate the number of bins based on bin width
                data_range = df[data_col].max() - df[data_col].min()
                bins = int(data_range / bin_width)
            # If number of bins is specified
            elif self.hist_bins_input.text():  
                bins = int(self.hist_bins_input.text())

            # Using seaborn's histplot
            if bins is not None:
                sns.histplot(df[data_col], bins=bins, kde=kde, cumulative=cumulative, stat="density" if kde else "count")
            else:
                sns.histplot(df[data_col], kde=kde, cumulative=cumulative, stat="density" if kde else "count")

                
            plt.xlabel(data_col)
            plt.ylabel("Density" if kde else "Count")

        plt.show()