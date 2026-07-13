"""
**A user can break PyCAT by installing a napari plugin, and there is no way to stop them.**

pip has no *"conflicts-with"* field. napari discovers plugins from whatever is installed. napari's
own plugin manager makes installing one a **single click.**

***So PyCAT cannot prevent the damage — it can only refuse to pretend nothing happened.***

This is not hypothetical
------------------------
Installing ``bioio`` into a working PyCAT environment silently pulled in **numpy 2.5.1**, **zarr
3.2.1** and **tifffile 2026.6.1**, *uninstalling the pinned ones*, and broke **cellpose, numba and
the image loader** in one command.

The failure the user actually saw was::

    AttributeError: '_TIFF' object has no attribute 'RESUNIT'

***That message sends a scientist looking at their microscope.*** It is ``aicsimageio`` reading a
``tifffile`` three years newer than it supports — **and nothing in that traceback says so.**

The first version of this check was blind to it
------------------------------------------------
It read only ``pycat-napari``'s own requirements — and **PyCAT does not pin tifffile at all.**
``aicsimageio`` does.

***A guard that misses the exact failure that prompted it is theatre.*** So the check walks the
packages that hold the load-bearing pins, and reports **which package declared each one.**
"""

import pytest


@pytest.mark.core
def test_the_check_CATCHES_the_failure_that_prompted_it():
    """**The tifffile crash.** PyCAT doesn't pin tifffile — ``aicsimageio`` does.

    A check that only reads PyCAT's own line would clear this environment as healthy while the
    image loader is dead.
    """
    check = pytest.importorskip("pycat.utils.environment_check")

    # Gable's environment, from his pip output verbatim.
    declared = {
        'pycat-napari': {'numpy': '>=1.22,<2.0', 'zarr': '>=2.12,<3.0'},
        'aicsimageio': {'tifffile': '<2023.3.15,>=2021.8.30',
                        'fsspec': '<2023.9.0,>=2022.8.0'},
        'cellpose': {'numpy': '<2.1,>=1.20.0'},
        'numba': {'numpy': '<2.5,>=1.22'},
    }
    installed = {'numpy': '2.5.1', 'zarr': '3.2.1', 'tifffile': '2026.6.1',
                 'fsspec': '2026.6.0'}

    original_constraints = check._constraints_declared_by
    original_version = check._installed_version
    try:
        check._constraints_declared_by = lambda package: declared.get(package, {})
        check._installed_version = installed.get

        problems = check.check_environment(verbose=False)
    finally:
        check._constraints_declared_by = original_constraints
        check._installed_version = original_version

    found = {problem['package'] for problem in problems}

    assert 'tifffile' in found, (
        "the check did NOT catch tifffile — **the package that actually crashed.** PyCAT does not "
        "pin it; aicsimageio does. A check that reads only PyCAT's own requirements is blind to "
        "the exact failure that prompted it."
    )
    assert 'numpy' in found and 'zarr' in found

    # And it must say WHO pinned it — otherwise the user cannot reason about the fix.
    tifffile_problem = next(p for p in problems if p['package'] == 'tifffile')
    assert 'aicsimageio' in tifffile_problem['declared_by'], (
        "the report must name the package that declared the pin. 'tifffile is wrong' is not "
        "actionable; 'aicsimageio requires tifffile<2023.3.15' is."
    )


@pytest.mark.core
def test_a_HEALTHY_environment_is_reported_as_healthy():
    """**A check that fires on a good environment is worse than no check** — it trains the user to
    ignore it, and then it will not be believed on the day it matters."""
    check = pytest.importorskip("pycat.utils.environment_check")

    declared = {'pycat-napari': {'numpy': '>=1.22,<2.0', 'zarr': '>=2.12,<3.0'}}
    installed = {'numpy': '1.26.4', 'zarr': '2.18.2'}

    original_constraints = check._constraints_declared_by
    original_version = check._installed_version
    try:
        check._constraints_declared_by = lambda package: declared.get(package, {})
        check._installed_version = installed.get

        problems = check.check_environment(verbose=False)
    finally:
        check._constraints_declared_by = original_constraints
        check._installed_version = original_version

    assert problems == [], f"a healthy environment was reported as broken: {problems}"


@pytest.mark.core
def test_the_pins_are_read_from_METADATA_and_not_HARDCODED():
    """**The pins WILL move.** The whole point of the BioIO work is to move them — ``aicsimageio``
    is frozen in 2023 and is what holds ``numpy<2`` and ``zarr<3`` in place.

    ***A check that hardcodes today's pins would start lying the day they change*** — and a lying
    check is worse than none, because it would confidently clear a broken environment.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "utils"
              / "environment_check.py").read_text(encoding='utf-8', errors='ignore')

    assert 'importlib.metadata' in source, (
        "the constraints must be read from the installed metadata, not hardcoded — they are about "
        "to change"
    )
    # No version literals in the constraint logic.
    for hardcoded in ("'<2.0'", '"<2.0"', "'<3.0'", '"<3.0"'):
        assert hardcoded not in source, (
            f"{hardcoded} is hardcoded. When the pin moves, this check will lie."
        )


@pytest.mark.core
def test_the_check_NEVER_crashes_the_program_it_guards():
    """**A guard that crashes the program it is guarding has done more harm than the bug it was
    looking for.**"""
    check = pytest.importorskip("pycat.utils.environment_check")

    original = check._constraints_declared_by
    try:
        def _explode(package):
            raise RuntimeError("the metadata is unreadable")

        check._constraints_declared_by = _explode

        # Must not raise.
        result = check.warn_if_environment_is_broken()
        assert result == []
    finally:
        check._constraints_declared_by = original
