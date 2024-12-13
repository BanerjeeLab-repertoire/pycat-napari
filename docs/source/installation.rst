============
Installation
============

To install PyCAT-Napari, follow the instructions below.

Basic Installation
------------------

1. **Create and activate a new environment**:

   .. code-block:: bash

       conda create -n pycat-env python=3.9
       conda activate pycat-env

2. **Install PyCAT-Napari**:

   .. code-block:: bash

       pip install pycat-napari

Platform-Specific Installations
-------------------------------

Windows
~~~~~~~
.. code-block:: bash

    pip install pycat-napari

Note: The application logo may not display correctly on Windows.

Mac M1/ARM
~~~~~~~~~~
.. code-block:: bash

    pip install "pycat-napari[arm-mac]"

Optional Features
-----------------

To install additional features, you can use the following commands:

.. code-block:: bash

    # Development tools
    pip install "pycat-napari[dev]"
    # Additional bio-image analysis tools (recommended)
    pip install "pycat-napari[devbio-napari]"

The ``devbio-napari`` package adds numerous additional image analysis tools. Learn more at the `devbio-napari documentation <https://github.com/haesleinhuepf/devbio-napari>`_.

Alternative Installation Methods
--------------------------------

If you encounter issues with the standard installation, use our tested environment files:

.. code-block:: bash

    # Windows
    mamba env create -f pycat-devbio-napari-env-x86-windows.yml
    # Mac M1/ARM
    mamba env create -f pycat-devbio-napari-env-arm-mac.yml

Verifying Installation & Optional Testing
-----------------------------------------

After installation, verify PyCAT-Napari is working correctly:

1. **Basic Checks**:

   .. code-block:: bash

       # Activate your environment
       conda activate pycat-env
       # Verify Python version
       python --version  # Should show 3.9.x
       # Test basic import
       python -c "import pycat; print('PyCAT import successful!')"
       # Launch GUI (basic smoke test)
       run-pycat  # Should open the GUI window

2. **Optional Test Suite**:

   .. code-block:: bash

       # Install test dependencies if you haven't
       pip install "pycat-napari[test]"
       # Run all tests with coverage report
       pytest

The test suite checks:

* Package imports and resource accessibility
* GUI initialization (non-interactive tests only)
* Core image processing functions
* Data management and file I/O
* Feature analysis tools

.. note:: GUI-interactive tests are skipped as they require manual interaction.

What Success Looks Like
-----------------------

* All import tests pass
* Basic GUI launches without errors
* Image processing tests complete successfully
* No failures in core functionality tests

If you encounter any failures, check:

1. Python version (must be 3.9.x)
2. Environment activation
3. Complete installation of dependencies
4. Troubleshooting Guide
5. Check the issues