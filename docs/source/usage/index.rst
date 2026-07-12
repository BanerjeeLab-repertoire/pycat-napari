Usage
=====

PyCAT-Napari offers multiple ways to analyze your data, with an emphasis on providing a low/no-code solution through its graphical user interface (GUI). While programmatic API access is available, the GUI is the recommended interface for most users.

Usage Options
-------------

1. **GUI Application (Recommended)**
   
   The graphical interface provides an intuitive environment for image analysis, built on the napari viewer. This is the recommended approach for most users, especially those new to image analysis or those who prefer a visual workflow.

2. **Programmatic API**
   
   For automated analysis or integration into existing workflows, PyCAT's core functions can be accessed programmatically. However, note that the API is primarily designed to support the GUI and hasn't been extensively tested for standalone use.

Available Tutorials
-------------------

.. toctree::
   :maxdepth: 1
   :caption: Tutorials:

   tutorials/nuclear_condensate_analysis_gui_tutorial
   
.. note::
   More tutorials are being developed and will be added soon. Check back for updates or contribute your own tutorials through our GitHub repository.

Interpreting Your Results
-------------------------

Before you draw conclusions from PyCAT's measurements, read the measurement
guidance. It documents effects — chiefly the **size-dependent intensity bias** and
the consequences of **upscaling** — that change how results should be interpreted,
and that can otherwise produce confident but false conclusions.

.. toctree::
   :maxdepth: 2
   :caption: Guidance:

   measurement_guidance

Getting Help
------------

If you encounter any issues or need assistance:

* Check the troubleshooting section in our :doc:`../installation` guide
* Visit our `GitHub Issues page <https://github.com/BanerjeeLab-repertoire/pycat-napari/issues>`_
* Join discussions in our GitHub repository

Contributing
------------

We welcome contributions to our documentation! If you have suggestions for additional tutorials or improvements to existing ones, please see our :doc:`../development/contributing` guide.
Contributions can be made through pull requests or by opening issues in our GitHub repository.