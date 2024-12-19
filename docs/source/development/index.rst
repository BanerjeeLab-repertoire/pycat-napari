Development Guide
=================

Welcome to the PyCAT-Napari development documentation. This section provides information for developers who want to contribute to PyCAT-Napari, whether through code contributions, bug fixes, documentation improvements, or other enhancements.

Getting Started with Development
--------------------------------

To begin contributing to PyCAT-Napari:

1. Set up your development environment
2. Understand our development practices and guidelines
3. Learn about our support resources
4. Review our project roadmap

Development Resources
---------------------

.. toctree::
   :maxdepth: 1
   :titlesonly:

   contributing
   support
   roadmap

Setting Up Development Environment
----------------------------------

Quick start for developers:

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/BanerjeeLab-repertoire/pycat-napari.git
   cd pycat-napari

   # Create development environment
   conda env create -f pycat-devbio-napari-env-x86-windows.yml  # For Windows
   # OR
   conda env create -f pycat-devbio-napari-env-arm-mac.yml      # For Mac M1/ARM

   # Install development dependencies
   pip install -e ".[dev]"

Key Development Tools
---------------------

PyCAT-Napari uses several key tools for development:

* **pytest**: For testing
* **black**: Code formatting
* **ruff**: Code linting
* **sphinx**: Documentation generation
* **pre-commit**: Git hooks for code quality

Development Guidelines Overview
-------------------------------

We maintain high standards for code quality and documentation. Key principles include:

* Following PEP 8 style guidelines
* Writing comprehensive tests
* Maintaining thorough documentation
* Using type hints where appropriate
* Following our git workflow

Next Steps
----------

* Review our :doc:`contributing` guide for detailed contribution guidelines
* Check our :doc:`support` page for help resources
* See our :doc:`roadmap` for planned features and improvements

Need help getting started? Check our :doc:`support` page or reach out to the development team.