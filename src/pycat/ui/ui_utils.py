"""
User-Interface (UI) Utilities Module for PyCAT

This module provides functions for displaying dataframes in a pop-up dialog window and adding images with a default
colormap to a Napari viewer. The functions are designed to enhance the user experience when working with dataframes
and image data in the Napari viewer. The module also includes a function to refresh the viewer with new data, as 
sometimes modifying an active layer in napari requires manually 'refreshing' it to see the changes.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pandas as pd
import napari
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QScrollArea, QWidget, QLabel, QTableView, 
                             QPushButton, QMenu, QFileDialog, QApplication, QAbstractItemView)
from PyQt5.QtCore import Qt, QAbstractTableModel


class DataFrameModel(QAbstractTableModel):
    """
    A custom table model to interface with a pandas DataFrame for use within Qt's Model-View-Controller (MVC) architecture.

    Attributes
    ----------
    _data : pandas.DataFrame
        The pandas DataFrame that backs this model.

    Parameters
    ----------
    df : pandas.DataFrame, optional
        The pandas DataFrame to be used by the model. Defaults to an empty DataFrame.
    """

    def __init__(self, df=pd.DataFrame()):
        """
        Initializes the DataFrameModel with a specified pandas DataFrame.

        Parameters
        ----------
        df : pandas.DataFrame, optional
            The pandas DataFrame to initialize the model. Defaults to an empty DataFrame.
        """
        super(DataFrameModel, self).__init__()
        self._data = df

    def rowCount(self, parent=None):
        """
        Returns the number of rows in the model. Overrides QAbstractTableModel.rowCount.
        """
        return self._data.shape[0]

    def columnCount(self, parent=None):
        """
        Returns the number of columns in the model. Overrides QAbstractTableModel.columnCount.
        """
        return self._data.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        """
        Returns the data stored at the specified index with the given role. Overrides QAbstractTableModel.data.

        Parameters
        ----------
        index : QModelIndex
            The index of the data to return.
        role : int
            The role for which data is requested, typically Qt.DisplayRole for displaying text.

        Returns
        -------
        str or None
            The data at the given index as a string if the index is valid and the role is DisplayRole, otherwise None.
        """
        if index.isValid() and role == Qt.DisplayRole:
            return str(self._data.iloc[index.row(), index.column()])
        return None

    def headerData(self, section, orientation, role):
        """
        Returns the header data for the given role and section in the specified orientation. 
        Overrides QAbstractTableModel.headerData.

        Parameters
        ----------
        section : int
            The section of the header, corresponding to column or row number.
        orientation : Qt.Orientation
            The orientation (horizontal or vertical) of the header.
        role : int
            The role for which the header data is requested, typically Qt.DisplayRole for displaying text.

        Returns
        -------
        QVariant or None
            The header data as a QVariant if the role is DisplayRole, otherwise None.
        """
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal: # Column headers
            return self._data.columns[section]
        else: # Row headers
            return str(self._data.index[section])
        
def create_table_view(dataframe):
    """
    Creates and configures a QTableView to display a pandas DataFrame within a the Napari viewer's Qt application 
    using the MVC (Model-View-Controller) design pattern. The function sets up a table model for the DataFrame, 
    applies settings to optimize the display and interaction, and incorporates a context menu for additional 
    functionalities such as copying and saving data.

    Parameters
    ----------
    dataframe : pandas.DataFrame
        The pandas DataFrame to be displayed in the QTableView. This DataFrame is used to populate the rows and 
        columns of the table view.

    Returns
    -------
    table_view : QTableView
        The configured QTableView object with the DataFrame model set, columns resized to fit content, and a custom
        context menu enabled for user interactions like copying data to the clipboard or saving it to a CSV file.

    Notes
    -----
    A custom context menu is added to provide additional functionalities directly accessible by right-clicking on the
    table view. The context menu is configured to work with the specific position and data of the clicked view through
    a lambda function connection.
    """
    # Create a table from the DataFrame
    table_model = DataFrameModel(dataframe)
    table_view = QTableView()
    table_view.setModel(table_model)
    table_view.resizeColumnsToContents()
    table_view.setEditTriggers(QAbstractItemView.NoEditTriggers)

    # Context Menu for Copy/Save functionality
    table_view.setContextMenuPolicy(Qt.CustomContextMenu)
    table_view.customContextMenuRequested.connect(lambda pos: table_context_menu(pos, table_view, dataframe))

    return table_view

def table_context_menu(position, table_view, dataframe):
    """
    Creates and displays a context menu for a table view at a specified position, providing options for copying content 
    and saving the data to a CSV file.

    Parameters
    ----------
    position : QPoint
        The position on the screen where the context menu is requested.
    table_view : QTableView
        The table view instance where the context menu will be applied.
    dataframe : pandas.DataFrame
        The pandas DataFrame associated with the table view, used for saving content.

    Notes
    -----
    The function adds "Copy" and "Save as CSV" actions to the context menu and performs the corresponding action
    based on user selection.
    """
    menu = QMenu()
    copy_action = menu.addAction("Copy")
    save_action = menu.addAction("Save as CSV")

    action = menu.exec_(table_view.viewport().mapToGlobal(position))

    if action == copy_action:
        copy_table_content(table_view)
    elif action == save_action:
        save_table_as_csv(dataframe)

def copy_table_content(table_view):
    """
    Copies the content of the specified table view to the clipboard in a tab-separated format, including column headers.

    Parameters
    ----------
    table_view : QTableView
        The table view whose content is to be copied to the clipboard.

    Notes
    -----
    The function constructs a string of tab-separated values from the table's data, including headers, and copies
    it to the system clipboard.
    """
    model = table_view.model()
    rows = model.rowCount()
    cols = model.columnCount()

    copied_text = ""

    # Adding headers
    headers = [model.headerData(i, Qt.Horizontal, Qt.DisplayRole) for i in range(cols)]
    copied_text += '\t'.join(headers) + '\n'

    for row in range(rows):
        # Check if the DataFrame's index is meaningful or just a default range
        if isinstance(model._data.index, pd.RangeIndex):
            # If it's a default RangeIndex, copy data without the index label
            row_data = [model.data(model.index(row, col), Qt.DisplayRole) for col in range(cols)]
        else:
            # If the index has meaningful labels, prepend the index label to row data
            index_label = model.headerData(row, Qt.Vertical, Qt.DisplayRole)
            row_data = [index_label] + [model.data(model.index(row, col), Qt.DisplayRole) for col in range(cols)]
        copied_text += '\t'.join(row_data) + '\n'

    clipboard = QApplication.clipboard()
    clipboard.setText(copied_text)

def save_table_as_csv(dataframe):
    """
    Prompts the user to select a file path and saves the specified pandas DataFrame to a CSV file at that location.

    Parameters
    ----------
    dataframe : pandas.DataFrame
        The DataFrame to be saved as a CSV file.

    Notes
    -----
    The function opens a file dialog for the user to select the save location and filename, then writes the DataFrame
    to a CSV file at the specified path.
    """
    path, _ = QFileDialog.getSaveFileName(None, "Save File", "", "CSV Files (*.csv)")
    if path:
        dataframe.to_csv(path, index=True)

def show_dataframes_dialog(window_title, tables_info):
    """
    Displays a dialog window with a scrollable area that contains multiple dataframes shown as table views.

    Parameters
    ----------
    window_title : str
        The title of the dialog window.
    tables_info : list of tuples
        A list where each tuple contains a title (str) for the table and a pandas DataFrame. The title is displayed
        as a label above the corresponding table view.

    Notes
    -----
    Each DataFrame from the `tables_info` list is displayed in a separate section within a scrollable area. 
    The dialog includes an "OK" button to close the window.
    """

    # Create a dialog window
    dialog = QDialog()
    dialog.setWindowTitle(window_title)
    layout = QVBoxLayout()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scrollContent = QWidget(scroll)
    scrollLayout = QVBoxLayout(scrollContent)
    scrollContent.setLayout(scrollLayout)

    # Add each table to the scroll area
    for title, df in tables_info:
        if df is not None:
            section_label = QLabel(title)
            section_label.setStyleSheet("font-size: 16pt;") # Adjust the font size as needed
            scrollLayout.addWidget(section_label, alignment=Qt.AlignCenter) # Align the title to the center

            table_view = create_table_view(df)
            scrollLayout.addWidget(table_view)

    # Add the scroll area and an OK button to the dialog
    scroll.setWidget(scrollContent)
    layout.addWidget(scroll)
    button = QPushButton("OK")
    button.clicked.connect(dialog.accept)
    layout.addWidget(button)
    dialog.setLayout(layout)
    dialog.exec_()



def add_image_with_default_colormap(data, viewer, colormap='viridis', **kwargs):
    """
    Adds an image to a Napari viewer using a specified colormap, with an emphasis on 'viridis' for enhanced visual 
    inspection. This function is tailored for use with the Napari visualization tool, facilitating the addition of 
    image data with a specified colormap for improved visual distinction and analysis.

    Parameters
    ----------
    data : numpy.ndarray
        The image data to be displayed in the Napari viewer. Compatible with the types of data Napari can visualize.
    viewer : napari.Viewer
        An instance of the Napari Viewer that supports the `add_image` method.
    colormap : str, optional
        The name of the colormap to apply to the image data, defaulting to 'viridis' for its effective visual clarity.
    **kwargs : dict
        Additional keyword arguments to be passed to the `add_image` method of the Napari viewer, allowing for 
        customization such as opacity, blending mode, scale, and more.

    Notes
    -----
    The default colormap 'viridis' is chosen due to its effectiveness in making distinct features stand out visually.
    Other colormaps provided by Napari can also be specified for different visual effects.
    """
    # Add the image to the Napari viewer with the specified (or default viridis) colormap and any additional kwargs
    viewer.add_image(data, colormap=colormap, **kwargs)


def refresh_viewer_with_new_data(viewer, active_layer, new_data=None):
    """
    Update the Napari viewer by removing an active label layer and replacing it with a new layer that 
    contains updated data. This function is designed to refresh the display after data modifications 
    and can be used with either the current data or newly provided data.

    Parameters
    ----------
    viewer : napari.Viewer
        The Napari viewer instance in which the layer is displayed.
    active_layer : napari.layers.Layer
        The currently active label layer that needs to be refreshed. This layer will be replaced.
    new_data : numpy.ndarray, optional
        The new data to be displayed in the layer. If None, the function uses a copy of the current 
        data in `active_layer`. Defaults to None.

    Raises  
    ------
    ValueError
        If the active layer type is not supported for refreshing the viewer.

    Notes
    -----
    The function preserves the name of the active layer, removes the old layer from the viewer, and 
    adds a new layer with the same name but updated data. If `new_data` is provided, it replaces 
    the current data; otherwise, a copy of the current data is used to refresh the viewer.
    """
    
    if new_data is None:
        updated_data = active_layer.data.copy()  # Create a copy of the updated data
    else:
        updated_data = new_data.copy() # Use the provided new data
    
    layer_name = active_layer.name  # Preserve the name of the active layer
    if isinstance(active_layer, napari.layers.Image):
        viewer.layers.remove(active_layer)
        add_image_with_default_colormap(viewer, updated_data, name=layer_name)
    elif isinstance(active_layer, napari.layers.Labels):
        viewer.layers.remove(active_layer)  # Remove the old layer
        viewer.add_labels(updated_data, name=layer_name)  # Re-add the layer with the updated data
    else:
        raise ValueError("The active layer type is not supported for refreshing the viewer.")