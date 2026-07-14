"""
**A dependency declared in one place and not another is how an install route silently produces a
different environment.**

The 1.6.0 migration removed ``aicsimageio`` from ``pyproject.toml`` — and **left it in six other
files**:

* ``config/requirements-base.txt``
* ``meta.yaml`` *(the conda recipe)*
* four conda environment lockfiles

So a user installing via conda, or a developer following the README, would still have received
``aicsimageio`` — **the library 1.6.0 exists to remove**, along with its 2023-era pins
(``zarr<2.16``, ``tifffile<2023.3``, ``fsspec<2023.9``, ``lxml<5``) that hold ``numpy<2`` and
``zarr<3`` in place.

***And no performance report from such an environment would have been interpretable.***

The lockfiles were worse than stale
------------------------------------
They were **exported conda lockfiles pinned to Python 3.9** — and PyCAT requires **``>=3.12``.
They could not have worked at all.** They also pinned ``numpy=1.23.5``, ``tifffile=2023.2.28``,
``lxml=4.9.4``.

**The README told developers to build from them.** They were deleted, and ``config/README.md``
records what went and why.
"""

import pathlib
import re

import pytest


_ROOT = pathlib.Path(__file__).resolve().parents[1]


# Every file that can produce an installed environment. A package named here but not in
# `pyproject.toml` is a route to a DIFFERENT environment than the one PyCAT is tested against.
_INSTALL_ROUTES = (
    "pyproject.toml",
    "meta.yaml",
    "config/requirements-base.txt",
)


def _declares(path, package):
    """Is ``package`` an actual declaration here — not merely mentioned in a comment?"""
    if not path.exists():
        return False

    for line in path.read_text(encoding='utf-8', errors='ignore').split('\n'):
        stripped = line.strip()

        # A comment is not a declaration. The history is worth keeping in comments, and it is.
        if stripped.startswith('#'):
            continue

        # `- aicsimageio` (yaml), `aicsimageio` (txt), `"aicsimageio>=4"` (toml)
        if re.search(rf'(^|[\s\-"\']){re.escape(package)}\b', stripped):
            return True

    return False


@pytest.mark.core
def test_NO_install_route_still_ships_aicsimageio():
    """**Removing it is what FREES the pins** — so an install route that keeps it undoes the
    entire 1.6.0 migration for that user, silently."""
    offenders = [route for route in _INSTALL_ROUTES
                 if _declares(_ROOT / route, 'aicsimageio')
                 or _declares(_ROOT / route, 'aicspylibczi')]

    assert not offenders, (
        f"these install routes still declare aicsimageio: {offenders}\n\n"
        f"It is FROZEN in maintenance mode, and its pins (zarr<2.16, tifffile<2023.3, "
        f"fsspec<2023.9, lxml<5) are what held numpy<2 and zarr<3 in place. A user installing "
        f"through this route gets the world 1.6.0 exists to escape — **and no performance report "
        f"from that environment is interpretable.**"
    )


@pytest.mark.core
def test_EVERY_install_route_ships_bioio_AND_the_czi_reader():
    """**BioIO's readers are separate packages** — that is the improvement, and the trap.

    ``bioio-czi`` is **not optional**: Zeiss market share makes CZI non-negotiable, and a missing
    plugin is a **missing FORMAT**.
    """
    missing = []

    for route in _INSTALL_ROUTES:
        path = _ROOT / route
        if not path.exists():
            continue
        for package in ('bioio', 'bioio-czi'):
            if not _declares(path, package):
                missing.append(f"{route}: {package}")

    assert not missing, (
        "these install routes are missing a required BioIO package:\n  " + "\n  ".join(missing)
        + "\n\n`bioio-czi` is NOT optional — Zeiss market share makes CZI non-negotiable."
    )


@pytest.mark.core
def test_the_DEAD_python39_lockfiles_are_GONE():
    """**They were exported conda lockfiles pinned to Python 3.9.** PyCAT requires ``>=3.12``.

    ***They could not have worked*** — and they pinned ``aicsimageio=4.10.0``, ``numpy=1.23.5``,
    ``tifffile=2023.2.28``. **The README told developers to build from them.**
    """
    dead = [
        "config/pycat-napari-env-arm-mac.yaml",
        "config/pycat-devbio-napari-env-arm-mac.yaml",
        "config/pycat-napari-env-x86-windows.yaml",
        "config/pycat-devbio-napari-env-x86-windows.yaml",
    ]

    resurrected = [name for name in dead if (_ROOT / name).exists()]

    assert not resurrected, (
        f"these Python-3.9 lockfiles are back: {resurrected}\n\n"
        f"They pin aicsimageio and numpy 1.23 on a Python PyCAT does not support. If a conda "
        f"environment is genuinely needed, generate it from `pyproject.toml` — do not commit an "
        f"exported lockfile, which is a second source of truth by construction."
    )


@pytest.mark.core
def test_the_README_does_not_point_at_a_DELETED_environment_file():
    """A build instruction that names a file which does not exist is worse than no instruction."""
    readme = (_ROOT / "README.md").read_text(encoding='utf-8', errors='ignore')

    dangling = re.findall(r'pycat-(?:devbio-)?napari-env-[\w-]+\.ya?ml', readme)

    assert not dangling, (
        f"the README still tells users to build from these deleted files: {set(dangling)}"
    )
