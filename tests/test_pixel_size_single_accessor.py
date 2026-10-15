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


# ── Completion Spec A5: EVERY read of the raw key, not only functions named `_mpx` ────────────────────────────────
#
# The `_mpx` scan above missed 15 call sites that read `microns_per_pixel_sq` directly with a 1.0 default (or by
# indexing) -- among them the colocalisation and SACF paths -- so the loader's "we don't know" sentinel became a
# silent 1 um/px. Reads are now confined to the accessor module and the files below, each with its reason; any
# other read must go through `pixel_size_um` / `pixel_size_um_or_default` / `has_real_pixel_size`.
_RAW_KEY = "microns_per_pixel_sq"
_RAW_READ_ALLOWED = {
    "utils/pixel_size.py": "the accessor itself",
    "file_io/file_io.py": "loader: recovers a real scale when the reader landed on the placeholder",
    "file_io/stack_load.py": "loader: decides whether a previously rejected value is still the placeholder",
    "file_io/napari_adapter.py": "render: napari needs a positive layer.scale even for the placeholder",
    "file_io/session_loader.py": "persistence: restores the saved value verbatim",
    "file_io/session_manifest.py": "persistence: saves the value verbatim (with its provenance flags)",
    "ui/field_status.py": "the pixel-size gate UI that sets and confirms the value",
    "batch/steps/preprocessing_steps.py": "rescales the stored value when an upscale changes the pixel grid",
    "navigator/session.py": "reads a metadata snapshot and rejects the sentinel itself (abs(px - 1) > 1e-9)",
}


def _raw_key_reads(source: str):
    """Line numbers where the raw key is READ: every occurrence of the string constant except a dict-literal key or
    the index of an assignment target (both are writes)."""
    tree = ast.parse(source)
    writes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            writes.update(id(k) for k in node.keys if k is not None)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            writes.add(id(node.slice))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value == _RAW_KEY and id(n) not in writes]


def test_no_raw_pixel_size_read_outside_the_gate():
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel in _RAW_READ_ALLOWED:
            continue
        lines = _raw_key_reads(path.read_text(encoding="utf-8", errors="ignore"))
        offenders += [f"{rel}:{ln}" for ln in lines]
    assert not offenders, (
        "these read `microns_per_pixel_sq` directly instead of through the pixel-size gate:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe loader writes exactly 1 as the 'unknown' sentinel; a direct read turns it into a silent 1 um/px. "
          "Use pixel_size_um (NaN when unknown), pixel_size_um_or_default (falls back ON THE RECORD) or "
          "has_real_pixel_size -- or, for a loader/persistence/render path, add the file to _RAW_READ_ALLOWED with "
          "its reason.")


def test_the_raw_read_scan_sees_reads_and_ignores_writes():
    src = ("def f(dr):\n"
           "    a = dr.get('microns_per_pixel_sq', 1.0)\n"
           "    b = dr['microns_per_pixel_sq']\n"
           "    dr['microns_per_pixel_sq'] = 2.0\n"
           "    d = {'microns_per_pixel_sq': 3.0}\n")
    assert _raw_key_reads(src) == [2, 3]
