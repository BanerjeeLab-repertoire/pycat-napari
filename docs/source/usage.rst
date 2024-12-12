=====
Usage
=====

PyCAT-Napari offers two primary ways to analyze your data: through a user-friendly GUI or programmatically via Python code.

Getting Started with the GUI
----------------------------

To launch the graphical interface, use the following command in your terminal:

.. code-block:: bash

    run-pycat

Basic GUI Workflow
~~~~~~~~~~~~~~~~~~

1. **Load Data**

   * Click ``File > Open`` or drag-and-drop files
   * Supported formats: TIFF, CZI, PNG, JPG
   * Multiple files can be loaded simultaneously

2. **View and Process**

   * Use the layer list to manage loaded images
   * Access tools through the left sidebar
   * Adjust parameters in the right panel

3. **Analyze**

   * Select analysis methods from the Analysis menu
   * Configure analysis parameters
   * Results appear in new layers

4. **Export**

   * Save processed images via ``File > Save``
   * Export measurements as CSV/Excel
   * Generate analysis reports

Using the Programmatic API
--------------------------

For automated analysis or integration into existing workflows, you can use the PyCAT-Napari API:

.. code-block:: python

    # Launch the GUI programmatically
    from pycat import run_pycat_func
    run_pycat_func()

    # Or use analysis tools programmatically
    import pycat
    from pycat.analysis import process_image  # Example import

    # Load and process an image
    image_path = "my_image.tif"
    results = process_image(
        image_path,
        method="condensate_detection",
        parameters={"threshold": 0.5}
    )

    # Access results
    measurements = results.measurements
    processed_image = results.image

    # Save results
    results.save("output_directory")

.. note:: While both interfaces offer the same capabilities, the GUI is recommended for exploratory analysis and parameter optimization, while the API is ideal for batch processing and reproducible workflows.

For detailed API documentation, see our `API Reference <link-to-docs>`_.

Example Workflows
-----------------

PyCAT includes several pre-configured workflows for common analysis scenarios:

In-Cellulo Condensate Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Example code snippet
    from pycat import analyze_single_condensate
    results = analyze_single_condensate(
        image_path="sample.tif",
        channel=0,  # First channel
        roi_size=50  # pixels
    )

Multi-Channel Colocalization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Example code snippet
    from pycat import analyze_colocalization
    results = analyze_colocalization(
        image_path="multi_channel.tif",
        channels=[0, 1],  # Analyze first two channels
        method="pearson"  # Correlation method
    )

Example Datasets
~~~~~~~~~~~~~~~~

Download sample data to try these workflows:

* ``example_single.tif``: Single condensate example
* ``example_multi.tif``: Multi-channel example
* ``example_batch/``: Batch processing example set

.. note:: Example datasets include both raw data and expected results for validation.