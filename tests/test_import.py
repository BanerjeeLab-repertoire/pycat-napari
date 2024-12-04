"""
Package Import Test Module for PyCAT

This module contains test cases that verify the proper importability of the PyCAT package
and its main entry points. These tests are fundamental to ensuring that the package is
correctly installed and that critical components are accessible to users of the package.

The test suite validates both the base package import and the availability of specific
entry point functions, which are essential for the package's functionality.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pytest


def test_package_import():
    """
    Test that pycat package can be imported.

    This test verifies that the base pycat package can be imported successfully,
    which is fundamental to the package's usability. A failed import would
    indicate installation or packaging issues.

    Notes
    -----
    The test validates:
    - Successful import of the pycat package
    - Proper package installation
    """
    # Try to import the pycat package
    try:
        import pycat
        assert True
    except ImportError:
        assert False, "Failed to import pycat package"


def test_entry_point_import():
    """
    Test that the main entry point function can be imported.

    This test verifies that the main entry point function (run_pycat_func) can be
    imported from its expected location. This function is critical as it serves
    as the primary interface for users to interact with the package.

    Notes
    -----
    The test validates:
    - Successful import of the run_pycat_func
    - Correct module structure and accessibility
    """
    # Try to import the run_pycat_func
    try:
        from pycat.run_pycat import run_pycat_func
        assert True
    except ImportError:
        assert False, "Failed to import run_pycat_func"