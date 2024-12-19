.. PyCAT-Napari documentation master file

============================
PyCAT-Napari Documentation
============================


.. image:: _static/pycat_logo_512.png
   :alt: PyCAT Logo
   :width: 250px

**PyCAT (Python Condensate Analysis Toolbox)** is an open-source application built on `napari <https://napari.org/>`_ for analyzing biomolecular condensates in biological images. It provides a comprehensive suite of tools for fluorescence image analysis, particularly focused on condensate detection, measurement, and characterization. PyCAT aims to provide a low/no-code solution, accessible to researchers with varying levels of programming experience.

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2

   installation
   usage/index
   features
   api/index
   development/index
   development/contributing
   development/support
   development/roadmap


Features
--------

PyCAT-Napari offers a wide range of tools for biological image analysis, including but not limited to:

* **Image Processing and Segmentation**: Versatile toolbox with common image processing and analysis functions. Specialized condensate segmentation and object filtering algorithms. Optimized for in-cellulo analysis in challenging biological datasets.
* **Quantitative Region Analysis**: Simple and intuitive layer and ROI mask design. Extensive ROI feature analysis, including area, intensity, shape, texture, and more. Advanced colocalization analysis with object-based and pixel-wise methods.
* **Integrated Analysis Pipelines**: 

  * **Condensate Analysis Pipeline**: Tailored for in-cellulo biomolecular condensates.
  * **Colocalization Analysis Pipeline**: Combines object-based and pixel-wise methods for robust colocalization studies.
  * **General ROI Analysis Pipeline**: Flexible pipeline for various region of interest analyses.

For a complete overview, see the :doc:`features` page.

Installation
------------

Follow the :doc:`installation` guide to set up PyCAT-Napari on your system. It covers system requirements, installation methods, and verification steps.

Usage
-----

PyCAT-Napari provides both a graphical user interface (GUI) and programmatic API access. The GUI is recommended for most users for an intuitive experience.

Explore our tutorials and usage guides in the :doc:`usage/index` section.

Development
-----------

Interested in contributing or developing PyCAT-Napari further? Visit the :doc:`development/index` for information on setting up your development environment, guidelines, and more.

Contributing
------------

We welcome contributions! Please see our :doc:`development/contributing` for details on how to contribute to PyCAT-Napari.

Support
-------

Need help or have questions? Check our :doc:`development/support` page for resources and troubleshooting assistance.

Roadmap
-------

Interested in the future of PyCAT-Napari? View our :doc:`development/roadmap` to see planned features and improvements.

Indices and Tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`