"""
Run PyCAT Test Module

This module contains test cases for the main functions in the PyCAT application, specifically
focusing on the initialization and execution of the main application components. The tests
verify the proper import and functionality of key functions, ensuring that the application
can be launched and its core features are accessible.

The test suite includes checks for the existence of essential resources, the importability
of main functions, and the correct setup of the application environment.

Author
------
    Christian Neureuter, GitHub: https://github.com/cneureuter

Date
----
    4-20-2024
"""

# Third party imports
import pytest
import importlib.resources
from unittest.mock import patch, MagicMock
import sys

# Local application imports
from pycat.run_pycat import run_pycat_func, main


def test_icon_path():
    """
    Test that the PyCAT icon file exists and is accessible.

    This test verifies that the PyCAT icon file is present in the expected location
    and can be accessed without errors. The icon is a critical resource for the
    application's user interface.

    Notes
    -----
    The test validates:
    - Existence of the icon file
    - Accessibility of the icon file without exceptions
    """
    # Try to access the icon file
    try:
        logo_path = importlib.resources.files('pycat') / 'icons' / 'pycat_logo_512.png'
        with importlib.resources.as_file(logo_path) as icon_path:
            assert icon_path.exists(), "Icon file does not exist"
    except Exception as e:
        pytest.fail(f"Failed to access icon file: {str(e)}")


def test_run_pycat_import():
    """
    Test that the main function can be imported.

    This test verifies that the run_pycat_func can be imported successfully from
    its module. This function is essential for launching the PyCAT application.

    Notes
    -----
    The test validates:
    - Successful import of run_pycat_func
    - Correct module structure and accessibility
    """
    # Try to import the run_pycat_func
    try:
        from pycat.run_pycat import run_pycat_func
        assert callable(run_pycat_func)
    except ImportError:
        pytest.fail("Failed to import run_pycat_func")


@pytest.mark.skip(reason="Requires GUI interaction")
def test_run_pycat_func():
    """
    Test that run_pycat_func initializes without errors.

    This test verifies that the run_pycat_func can be executed without raising
    exceptions. It is marked as skipped by default because it launches the GUI,
    which may not be suitable for automated test environments.

    Notes
    -----
    The test validates:
    - Successful execution of run_pycat_func without exceptions
    """
    # Try to execute the run_pycat_func
    try:
        run_pycat_func()
    except Exception as e:
        pytest.fail(f"run_pycat_func failed to execute: {str(e)}")


def test_main_function():
    """
    Test that the main function is properly defined and callable.

    This test verifies that the main function is defined and can be called
    successfully. It also checks that the function correctly interacts with
    other components, such as the run_pycat_func.

    Notes
    -----
    The test validates:
    - Callability of the main function
    - Correct interaction with run_pycat_func
    """
    # Check that the main function is callable
    assert callable(main), "main function should be callable"
    
    # Try to execute the main function
    with patch('pycat.run_pycat.run_pycat_func') as mock_run:
        main()
        mock_run.assert_called_once()