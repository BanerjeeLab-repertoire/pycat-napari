"""**A 2-D TIFF whose pixel size came from the file shows a µm scale bar, not 'px'.**

sidecar_metadata spec, Step 1 — the reported ISS-file regression. The 2-D load path set
`pixel_size_from_metadata` True only inside the 1.0-sentinel recovery branch, so a file whose scale was read
straight from `tiff_tags` (reader succeeded, no recovery) kept the flag False and the scale bar rendered
'px' on a correctly-calibrated image. The fix routes the flag through the one provenance helper
(`_calibration_is_from_metadata`, which reads `pixel_size_source`) whenever a real (non-sentinel) pixel size
is present — the same helper the stack path already uses.
"""
import ast
import pathlib

import pytest

from pycat.file_io.tagging import _calibration_is_from_metadata

pytestmark = pytest.mark.core

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


def _iss_like_repo():
    """The repository state after loading Meet's ISS TIFF, pre-fix: a real scale from tiff_tags, but the
    provenance flag never got set on the 2-D path."""
    return {
        'microns_per_pixel_sq': 0.09765625 ** 2,
        'file_metadata': {'common': {'pixel_size_source': 'tiff_tags'}},
        'pixel_size_from_metadata': False,          # the bug
    }


def _apply_2d_provenance_fix(dr):
    """The exact logic added to the 2-D load path."""
    mpp_sq = float(dr.get('microns_per_pixel_sq', 1))
    if abs(mpp_sq - 1.0) > 1e-9:
        dr['pixel_size_from_metadata'] = _calibration_is_from_metadata(dr, mpp_sq ** 0.5)
    return dr


def test_a_metadata_pixel_size_is_marked_calibrated_from_provenance():
    import types
    dr = _apply_2d_provenance_fix(_iss_like_repo())
    assert dr['pixel_size_from_metadata'] is True

    from pycat.file_io.napari_adapter import _is_calibrated
    cm = types.SimpleNamespace(active_data_class=types.SimpleNamespace(data_repository=dr))
    assert _is_calibrated(cm, 0.09765625) is True    # -> the scale bar renders 'um', not 'px'


def test_a_rejected_corrupt_scale_is_NOT_re_marked_as_real():
    """The sentinel guard: a scale rejected as implausible is set to 1.0 with the flag False; the fix must
    not resurrect it even if a source string was recorded."""
    dr = {'microns_per_pixel_sq': 1.0,               # rejected -> sentinel
          'file_metadata': {'common': {'pixel_size_source': 'tiff_tags'}},
          'pixel_size_from_metadata': False}
    _apply_2d_provenance_fix(dr)
    assert dr['pixel_size_from_metadata'] is False   # untouched — the sentinel is skipped


def test_the_2d_load_path_routes_the_flag_through_the_provenance_helper():
    """The fix is wired: the 2-D path in file_io.py references the one helper (not a bare value check)."""
    src = (_SRC / 'file_io' / 'file_io.py').read_text(encoding='utf-8')
    assert '_calibration_is_from_metadata' in src, "the 2-D path no longer routes through the helper"


def test_no_site_sets_the_flag_by_a_bare_value_comparison():
    """The provenance is decided in ONE place. No file may set `pixel_size_from_metadata` from a comparison
    of the pixel size against 1.0 — that is the value-as-sentinel guess the helper exists to replace. The
    single documented fallback lives inside `_calibration_is_from_metadata` (tagging.py)."""
    offenders = []
    for path in sorted(_SRC.rglob('*.py')):
        if path.name == 'tagging.py':                # the helper's own documented fallback is the exception
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = ' '.join(ast.dump(t) for t in node.targets)
            if 'pixel_size_from_metadata' not in targets:
                continue
            rhs = ast.dump(node.value)
            if 'Compare' in rhs and '1.0' in rhs:
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert not offenders, (
        "these set pixel_size_from_metadata from a value comparison instead of provenance:\n  "
        + "\n  ".join(offenders))
