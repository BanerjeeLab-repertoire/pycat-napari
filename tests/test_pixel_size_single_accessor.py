"""**One pixel-size accessor. Every `_mpx` routes through the canonical helper — structurally.**

Pixel size scales every physical-unit measurement PyCAT reports (viscosity, ΔG, size, density). The
danger the `redundancy_consolidation` spec names is a per-UI `_mpx()` that re-derives it from the data
repository *inconsistently* — one that defaults differently, or misses the pixel-size gate — silently
corrupting units in one workflow but not another. A canonical accessor already exists
(`pixel_size.pixel_size_um_or_default`), and axis 1 of that spec routed every `_mpx` through it.

`tests/test_pixel_size.py` pins the accessor's BEHAVIOUR; this pins the STRUCTURE: every function named
``_mpx`` anywhere in the package must call the canonical accessor, so a new UI cannot quietly reintroduce
a bespoke `dr.get('microns_per_pixel_sq') or 1.0` and re-open the silent-units hole. A ratchet by
construction — a `_mpx` that bypasses the accessor fails here.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"
_CANONICAL = "pixel_size_um_or_default"


def _mpx_functions(source: str):
    """Yield every ``def _mpx`` FunctionDef in the source."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_mpx":
            yield node


def _calls_canonical(func: ast.AST) -> bool:
    """True if the canonical accessor name appears anywhere in the function body (as a call/reference)."""
    return any(isinstance(n, ast.Name) and n.id == _CANONICAL for n in ast.walk(func))


def _scan():
    found, offenders = [], []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for func in _mpx_functions(src):
            rel = path.relative_to(_SRC).as_posix()
            found.append(rel)
            if not _calls_canonical(func):
                offenders.append(f"{rel}:{func.lineno}")
    return found, offenders


def test_every_mpx_routes_through_the_canonical_accessor():
    """Structural single-source-of-truth guard: no `_mpx` may re-derive the pixel size itself."""
    found, offenders = _scan()
    assert found, "no `_mpx` accessors found — the scan is broken, not the code"
    assert not offenders, (
        "these `_mpx` pixel-size accessors do NOT route through "
        f"`{_CANONICAL}`:\n  " + "\n  ".join(offenders)
        + f"\n\nPixel size scales every physical-unit measurement; a bespoke `_mpx` that defaults or "
          f"gates differently silently corrupts units in one workflow but not another. Call "
          f"`{_CANONICAL}(dr, context='<ui>')` — the one accessor — instead.")


def test_the_guard_detects_a_bespoke_accessor():
    """Canary: a `_mpx` that re-derives the pixel size is flagged; one that calls the canonical passes."""
    bespoke = ("class W:\n"
               "    def _mpx(self):\n"
               "        return self._dr().get('microns_per_pixel_sq') or 1.0\n")
    assert [f for f in _mpx_functions(bespoke) if not _calls_canonical(f)], \
        "the guard failed to flag a bespoke pixel-size accessor"

    routed = ("class W:\n"
              "    def _mpx(self):\n"
              "        return pixel_size_um_or_default(self._dr(), context='w')\n")
    assert not [f for f in _mpx_functions(routed) if not _calls_canonical(f)]
