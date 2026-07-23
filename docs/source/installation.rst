Installation
============

This guide will help you install PyCAT-Napari and verify your installation. We'll cover system requirements, installation methods, and testing procedures.

System Requirements
-------------------

Compatibility Matrix
^^^^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 20 15 15 50

   * - Platform
     - Python
     - Status
     - Notes
   * - Windows 10/11
     - 3.12
     - Tested
     - Logo display issue
   * - Mac M1/ARM
     - 3.12
     - Tested
     - Requires specific torch
   * - Mac Intel
     - 3.12
     - Untested*
     - Should work
   * - Linux
     - 3.12
     - Untested*
     - Should work

\* While untested, these platforms should work with standard installation.

Minimum Requirements
^^^^^^^^^^^^^^^^^^^^

* **Python Version**: 3.12.x (Required)

.. warning::
   PyCAT-Napari requires **Python 3.12**. Earlier versions, including 3.9, are no longer supported as of
   v1.5.39. **Python 3.13 is not yet supported** — a normal ``pip install`` on 3.13 fails (an upstream
   ``cellpose`` / ``numpy`` wheel conflict, not PyCAT's own code). See :doc:`known_issues` for the reason
   and the condition that will unblock it.

* **RAM**: 8GB (16GB recommended)
* **Disk Space**: ~100MB (including dependencies)
* **GPU**: Not required (CPU-only processing)

Pre-Installation Setup
----------------------

Before installing PyCAT-Napari, ensure your system meets the requirements and follow this quick setup check.

1. Python Installation Check
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Run this command in your terminal (mac)/command prompt (anaconda prompt)/powershell(windows):

.. code-block:: bash

   python --version

✅ If you get a version number: You have Python installed

❌ If you get an error: See :ref:`python-installation`

2. Package Manager Check
^^^^^^^^^^^^^^^^^^^^^^^^

Check for Conda or Mamba installation:

.. code-block:: bash

   conda --version
   # or
   mamba --version

✅ If you get a version number: You have conda/mamba installed

❌ If you get an error: See :ref:`python-installation`

Python Environment Guide
^^^^^^^^^^^^^^^^^^^^^^^^

Python environments are essential for managing dependencies and avoiding conflicts.

.. tip::
   Think of environments like separate containers for different projects - they help avoid conflicts and keep things organized.

Benefits of using environments:

* Keep projects separate
* Avoid version conflicts
* Ensure reproducibility

Why Miniforge?
"""""""""""""""

.. note::
   Miniforge is our recommended package manager - it's a lightweight distribution of python offering conda and the faster mamba package installer/manager.

Key advantages:

* Quicker dependency resolution
* Minimal initial install
* Fully compatible with conda commands (just use ``mamba`` instead of ``conda``)

Already have Anaconda? That's fine! You can skip the Miniforge installation.

Installing Miniforge
""""""""""""""""""""

Download the installer for your system from the `conda-forge download page
<https://conda-forge.org/download/>`_, then run it:

* **Windows:** you'll get a ``.exe`` — double-click it and accept the defaults.
* **macOS / Linux:** you'll get a ``.sh`` script (e.g.
  ``Miniforge3-MacOSX-arm64.sh``). This is run from the Terminal, **not** by
  double-clicking. Open the **Terminal** app and run ``bash`` followed by the
  path to the downloaded file — the easiest way is to type ``bash`` and a space,
  then drag the ``.sh`` file from Finder into the Terminal window to paste its
  path::

     bash ~/Downloads/Miniforge3-MacOSX-arm64.sh

  Follow the prompts (page through the license, type ``yes`` to accept, accept
  the default location, and answer ``yes`` to initialize). The exact filename
  depends on your system — Apple Silicon Macs get ``arm64``, Intel Macs get
  ``x86_64``.

After it finishes, **close the Terminal and open a new one** so the changes take
effect.

Basic Environment Commands
""""""""""""""""""""""""""

.. code-block:: bash

   # Create new environment with Python 3.12
   conda create -n pycat-env python=3.12

   # Activate the environment
   conda activate pycat-env

   # Verify you're in the right environment
   python --version  # Should show Python 3.12.x

Installation Methods
--------------------

Basic Installation
^^^^^^^^^^^^^^^^^^

1. Create and activate a new environment:

.. code-block:: bash

   # Create environment
   conda create -n pycat-env python=3.12

   # Activate environment
   conda activate pycat-env

2. Install PyCAT-Napari:

For Windows:

.. code-block:: bash

   pip install pycat-napari

.. note::
   On Windows, the application logo may not display correctly. This is purely cosmetic and does not affect functionality.

For Mac M1/ARM:

.. warning::
   **Check your Python architecture first.** On an Apple Silicon (M-series) Mac,
   run::

      python -c "import platform; print(platform.machine())"

   It must print ``arm64``. If it prints ``x86_64``, your Python is the Intel
   build running under Rosetta emulation — this causes ``Intel MKL`` warnings and
   can crash Cellpose with a segmentation fault. Do **not** use ``uname -m`` for
   this check; it reports the hardware and can say ``arm64`` even while Python is
   x86. To fix an ``x86_64`` result, install the **native Apple Silicon (arm64)**
   Miniforge from the `conda-forge download page
   <https://conda-forge.org/download/>`_ (the installer filename contains
   ``arm64``, not ``x86_64``), then recreate the ``pycat-env`` and re-check before
   installing. If it still reports ``x86_64``, an old Intel conda is likely first
   on your ``PATH`` — check ``which conda`` and ensure the Miniforge one wins.

On Apple Silicon, first install ``llvmlite`` and ``numba`` from conda-forge (they
ship prebuilt Apple-Silicon binaries), then install PyCAT:

.. code-block:: bash

   conda install -c conda-forge llvmlite numba
   pip install "pycat-napari[arm-mac]"

.. note::
   ``numba`` is a dependency. If ``pip`` cannot find a prebuilt ``llvmlite`` /
   ``numba`` for your Mac it will try to compile them from source, which fails
   with ``llvmlite needs CMake tools to build`` unless compiler tools are
   installed. Installing them from conda-forge first avoids the build entirely.
   If you still hit that error, add ``cmake`` to the conda install:
   ``conda install -c conda-forge cmake llvmlite numba``.

Optional Features
^^^^^^^^^^^^^^^^^

You can install PyCAT with additional tools, features, and packages; for example, dev, test, and doc tools. 
The devbio-napari package adds numerous additional image analysis tools. Learn more at `devbio-napari documentation <https://github.com/haesleinhuepf/devbio-napari>`_.

.. code-block:: bash

   # Development tools
   pip install "pycat-napari[dev]"

   # Additional bio-image analysis tools
   pip install "pycat-napari[devbio-napari]"

.. tip::
   You can designate multiple optional dependencies by separating them with a comma:

   .. code-block:: bash

      # Install dev tools on an ARM Mac
      pip install "pycat-napari[arm-mac, dev]"

Alternative Installation Methods
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you encounter issues with the standard installation, use our tested environment files located in the github repo, config/ folder. 
We provide complete environment files that match our development package setup (no dev tools installed though, please install those separately if youre trying to install a dev version for a fork or pull request) to provide you with the same environment we developed and ran in. 
To use these environment files, just download the yaml file from the config folder on the github repo, then cd to the location of the downloaded file in your terminal, then run:

.. code-block:: bash

   # Windows
   mamba env create -f pycat-devbio-napari-env-x86-windows.yml

   # Mac M1/ARM
   mamba env create -f pycat-devbio-napari-env-arm-mac.yml

Verification and Testing
------------------------

Basic Installation Checks
^^^^^^^^^^^^^^^^^^^^^^^^^

1. Verify your installation:

.. code-block:: bash

   # Activate your environment
   conda activate pycat-env

   # Verify Python version
   python --version  # Should show 3.12.x

   # Test basic import
   python -c "import pycat; print('PyCAT import successful!')"

   # Launch GUI (basic smoke test)
   run-pycat  # Should open the GUI window

Optional Test Suite
^^^^^^^^^^^^^^^^^^^

PyCAT includes a basic test suite:

.. code-block:: bash

   # Install test dependencies
   pip install "pycat-napari[test]"

   # Run all tests with coverage report
   pytest --cov=pycat_napari tests/

The test suite verifies:

* Package imports and resource accessibility
* GUI initialization (non-interactive tests only)
* Core image processing functions
* Data management and file I/O
* Feature analysis tools

.. note::
   GUI-interactive tests are skipped as they require manual interaction.

What Success Looks Like
^^^^^^^^^^^^^^^^^^^^^^^

A successful installation should show:

* All import tests passing
* Basic GUI launching without errors
* Image processing tests completing successfully
* No failures in core functionality tests

Troubleshooting
---------------

If you encounter installation issues, check:

1. Python version (must be 3.12.x) — verify with ``python --version``
2. Environment activation
3. Complete installation of dependencies
4. Support & Troubleshooting section of the `README <https://github.com/BanerjeeLab-repertoire/pycat-napari>`_
5. Existing GitHub issues

Common platform-specific issues
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Mac (Apple Silicon): "Intel MKL" warnings and/or a Cellpose segmentation fault.**
Your Python is the Intel (x86) build running under Rosetta emulation. Check with
``python -c "import platform; print(platform.machine())"`` — if it says ``x86_64``
on an M-series Mac, install the native **arm64** Miniforge and recreate the
environment (see the warning in the *For Mac M1/ARM* section above). Symptoms
include repeated ``Intel MKL WARNING: ... SSE4.2 ... deprecated`` lines and/or
``zsh: segmentation fault run-pycat`` after ``Cellpose model not found in
cache...``.

**Mac: ``conda-libmamba-solver`` / ``libarchive.19.dylib`` error.** A
Homebrew-installed Miniconda whose solver library broke after a Homebrew update
(not a PyCAT issue). Use Miniforge from the `conda-forge download page
<https://conda-forge.org/download/>`_ instead and create your ``pycat-env``
there.

**Mac: ``llvmlite needs CMake tools to build``.** ``pip`` tried to compile
``llvmlite`` / ``numba`` from source. Install them from conda-forge first:
``conda install -c conda-forge llvmlite numba``, then install PyCAT.

**"Could not find a version that satisfies the requirement pycat-napari" (every
version rejected).** Your Python is neither 3.9 nor 3.12 (often 3.10/3.11 from a
base environment). Confirm with ``python --version`` and recreate the environment
with ``python=3.12``.

.. note::
   Still having problems? Open a GitHub issue or reach out to us for urgent help.


.. _python-installation:

Python and Package Manager Installation
---------------------------------------

If you do not have Python and/or a package manager installed, you can use the following instructions to install them.
While you can install Python 3.12 directly from `python.org <https://www.python.org/downloads/>`_, we **strongly** recommend using a package manager instead, and you may as well get two birds with one stone.

Package managers provide:

* Easier environment management
* Simplified package installation
* Better dependency resolution
* Consistent cross-platform experience

There are several popular package managers for Python:

* **Mambaforge/Miniforge** (Recommended): A minimal distribution with the fast Mamba package manager. Mambaforge and Miniforge are functionally the same, but Mambaforge is becoming deprecated in favor of Miniforge.
* **Anaconda**: Full-featured distribution with GUI tools (Anaconda Navigator) but larger installation size, and significantly slower conda package manager.
* **Miniconda**: A minimal distribution of the Anaconda distribution with conda package manager.

We recommend Miniforge because:

* Faster package installation and dependency resolution
* Minimal initial installation size
* Full compatibility with conda commands
* Includes Python

Installing Miniforge
^^^^^^^^^^^^^^^^^^^^

1. Download and install Miniforge:

   Follow the instructions for downloading and installing Miniforge from the `Miniforge <https://github.com/conda-forge/miniforge#mambaforge>`_ GitHub repository.

2. Verify installation:

   .. code-block:: bash

      # Close and reopen your terminal, then run:
      mamba --version

.. tip::
   For a detailed walkthrough of installing and getting started with Miniforge/Mambaforge, see this excellent 
   `guide from BiAPoL <https://biapol.github.io/blog/mara_lampert/getting_started_with_mambaforge_and_python/readme.html>`_.

Alternative: Anaconda
^^^^^^^^^^^^^^^^^^^^^

If you prefer a more user-friendly interface, you can install the Anaconda distribution.

1. Download `Anaconda Individual Edition <https://www.anaconda.com/products/individual>`_
2. Use Anaconda Navigator for visual environment management
3. Replace ``mamba`` with ``conda`` in all commands

While Anaconda provides a more beginner-friendly experience with its Navigator GUI, it comes with many pre-installed packages you may not need. This results in:

* Larger download size (~3GB vs ~100MB for Miniforge)
* Slower package operations
* More disk space usage

.. warning::
   Mambaforge/Miniforge and Anaconda are not compatible with each other. It is advised you only have one installed at a time.
   If you already have Anaconda, you can just use it as your package manager, just replace all ``mamba`` commands in the PyCAT documentation with ``conda`` commands.
   If you are starting from scratch, we follow the Miniforge installation instructions above.

