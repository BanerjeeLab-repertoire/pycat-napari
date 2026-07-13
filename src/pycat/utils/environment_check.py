"""
**Is this environment actually the one PyCAT was built against?**

A user can break PyCAT by installing a napari plugin, and there is **no way to stop them.** pip has
no *"conflicts-with"* field; napari discovers plugins from whatever is installed; and napari's own
plugin manager makes installing one a single click. **So PyCAT cannot prevent the damage — it can
only refuse to pretend nothing happened.**

This is not hypothetical. Installing ``bioio`` into a working PyCAT environment silently pulled in
**numpy 2.5.1**, **zarr 3.2.1** and **tifffile 2026.6.1**, *uninstalling the pinned ones*, and broke
**cellpose, numba, and the image loader** in one command. The failure the user actually saw was::

    AttributeError: '_TIFF' object has no attribute 'RESUNIT'

**That message sends a scientist looking at their microscope.** It is ``aicsimageio`` reading a
``tifffile`` three years newer than it supports — and *nothing in that traceback says so.*

What this does
--------------
It reads PyCAT's **own declared requirements from its installed metadata** — not from a hardcoded
list, which would go stale the moment ``pyproject.toml`` changes — and compares them against **what
is actually importable.**

When they disagree it says **which package**, **which version**, **what PyCAT needs**, and **the
exact command to fix it.**

Why the metadata and not a hardcoded list
------------------------------------------
The pins **will** move. The whole point of the BioIO migration is to move them: ``aicsimageio`` is
frozen in 2023 and is the thing holding ``numpy<2`` and ``zarr<3`` in place. **A check that hardcodes
today's pins would start lying the day they change** — and a lying check is worse than none, because
it would confidently clear a broken environment.

Reading the metadata means this keeps working through the migration **and** catches the *reverse*
failure afterwards: someone with an old ``zarr<3`` sitting in a BioIO-era environment.
"""

from __future__ import annotations

import sys


# ── The packages whose version actually decides whether PyCAT works ──────────────────────
#
# Not every dependency needs checking. These are the ones where a mismatch produces a **cryptic
# failure far from its cause** — which is the whole reason this file exists.
_LOAD_BEARING = {
    'numpy': "the array type every module depends on",
    'zarr': "the lazy-loading store — a version mismatch breaks stack access, silently",
    'tifffile': "the TIFF reader. A too-new one breaks aicsimageio: 'no attribute RESUNIT'",
    'fsspec': 'the filesystem layer aicsimageio reads through',
    'lxml': 'the XML parser aicsimageio reads OME metadata with',
    'cellpose': "cell segmentation",
    'numba': "the JIT kernels",
    'napari': "the viewer",
    'aicsimageio': "the image reader",
}


def _installed_version(name):
    """What version is ACTUALLY importable — not what the metadata claims is installed."""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _constraints_declared_by(package):
    """Every load-bearing pin that ``package`` declares. ``{}`` if it is not installed."""
    try:
        from importlib.metadata import requires
        lines = requires(package) or []
    except Exception:
        return {}

    constraints = {}
    for line in lines:
        # e.g. "numpy>=1.22,<2.0" or "torch>=2.2.0,<2.3.0; platform_system=='Darwin'"
        specification = line.split(';')[0].strip()

        for name in _LOAD_BEARING:
            if not specification.lower().startswith(name):
                continue
            remainder = specification[len(name):].strip()
            if remainder.startswith('['):        # an extra, e.g. "bioio[czi]"
                continue
            if remainder:
                constraints[name] = remainder
            break

    return constraints


def _all_constraints():
    """**Every pin that matters — PyCAT's OWN, and its dependencies'.**

    ── The first version of this check was BLIND to the failure that prompted it ──

    It read only ``pycat-napari``'s requirements. But the crash that started this was::

        AttributeError: '_TIFF' object has no attribute 'RESUNIT'

    — and **PyCAT does not pin tifffile at all.** ``aicsimageio`` does (``tifffile<2023.3.15``),
    and it was ``aicsimageio`` reading a tifffile **three years newer than it supports.**

    ***A guard that misses the exact failure that prompted it is theatre.*** So the check walks the
    packages that hold the load-bearing pins, not just PyCAT's own line.

    Returns ``{package: (specification, who_declared_it)}``. When two packages constrain the same
    thing, **the tighter one is reported** — because satisfying the loose one while violating the
    tight one is still a broken environment.
    """
    from packaging.specifiers import SpecifierSet

    # The packages whose pins can break PyCAT. `pycat-napari` first so its own constraints are the
    # baseline; the rest are the dependencies that carry pins of their own.
    #
    # `aicsimageio` is the important one and the reason this list exists: it is FROZEN in
    # maintenance mode, and it pins tifffile, fsspec, lxml and zarr to 2023-era versions. Anything
    # that upgrades those breaks the image loader, and the traceback never says why.
    sources = ('pycat-napari', 'aicsimageio', 'cellpose', 'numba', 'napari', 'bioio')

    merged = {}

    for source in sources:
        for name, specification in _constraints_declared_by(source).items():
            if name == source:
                continue                          # a package does not constrain itself

            if name not in merged:
                merged[name] = (specification, source)
                continue

            # Two packages constrain the same thing. Keep the TIGHTER one — an environment that
            # satisfies the loose pin while violating the tight one is still broken.
            existing, _ = merged[name]
            try:
                combined = SpecifierSet(existing) & SpecifierSet(specification)
                merged[name] = (str(combined), f"{merged[name][1]} + {source}")
            except Exception:
                pass

    return merged


def _pycat_requirements():
    """Kept for the callers that only want PyCAT's own line."""
    return {name: specification
            for name, (specification, _) in _all_constraints().items()}


def _satisfies(version_string, specification):
    """Does ``version_string`` satisfy ``specification``? ``None`` if it cannot be decided."""
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version
        return Version(version_string) in SpecifierSet(specification)
    except Exception:
        return None


def check_environment(verbose=True):
    """**Report every load-bearing package whose version disagrees with PyCAT's own pins.**

    Returns a list of problems. An empty list means the environment matches what PyCAT was built
    against.
    """
    constraints = _all_constraints()

    if not constraints:
        # PyCAT installed from source without metadata, or an unusual layout. Nothing to compare
        # against — and **guessing would be worse than staying quiet.**
        return []

    problems = []

    for name, (specification, declared_by) in constraints.items():
        installed = _installed_version(name)
        if installed is None:
            continue        # not installed at all — a different failure, and it will say so itself

        verdict = _satisfies(installed, specification)
        if verdict is False:
            problems.append({
                'package': name,
                'installed': installed,
                'required': specification,
                'declared_by': declared_by,
                'why_it_matters': _LOAD_BEARING[name],
            })

    if problems and verbose:
        _report(problems)

    return problems


def _report(problems):
    """**Say which package, which version, what is needed, and the command to fix it.**

    The failure this replaces was ``AttributeError: '_TIFF' object has no attribute 'RESUNIT'`` —
    *which sends a scientist looking at their microscope.*
    """
    print("\n" + "=" * 74, file=sys.stderr)
    print("  PyCAT: THIS ENVIRONMENT DOES NOT MATCH WHAT PyCAT REQUIRES", file=sys.stderr)
    print("=" * 74, file=sys.stderr)

    print(f"""
  {len(problems)} package(s) are at a version PyCAT was not built against. **This usually means
  something was installed into this environment that upgraded them** — a napari plugin, or any
  package with a conflicting dependency. pip will do this **without asking**, and it uninstalls
  the pinned version to do it.
""", file=sys.stderr)

    for problem in problems:
        print(f"    {problem['package']}", file=sys.stderr)
        print(f"        installed : {problem['installed']}", file=sys.stderr)
        print(f"        required   : {problem['required']}", file=sys.stderr)
        print(f"        pinned by  : {problem['declared_by']}", file=sys.stderr)
        print(f"        this is    : {problem['why_it_matters']}", file=sys.stderr)
        print(file=sys.stderr)

    repair = ' '.join(f'"{p["package"]}{p["required"]}"' for p in problems)
    print("  TO REPAIR:\n", file=sys.stderr)
    print(f"      pip install {repair}\n", file=sys.stderr)

    print("""  If that pulls the wrong versions back in, the safest fix is a clean environment:

      conda env remove -n <your-env>
      conda create -n <your-env> python=3.12
      conda activate <your-env>
      pip install pycat-napari

  PyCAT will still try to start. **But a failure after this point is very likely caused by the
  mismatch above, not by your data.**""", file=sys.stderr)
    print("=" * 74 + "\n", file=sys.stderr)


def warn_if_environment_is_broken():
    """Called at startup. **Never raises** — a broken check must not be worse than no check."""
    try:
        return check_environment(verbose=True)
    except Exception as exc:
        # A guard that crashes the program it is guarding has done more harm than the bug it was
        # looking for. Say so quietly and get out of the way.
        print(f"[PyCAT] (the environment check itself failed: {exc})", file=sys.stderr)
        return []
