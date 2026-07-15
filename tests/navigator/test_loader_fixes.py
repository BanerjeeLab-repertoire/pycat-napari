"""
Tests for the pure logic of the auto-loader fixes (patches 0002/0003/0004
against src/pycat). These import the pure helpers straight from the patched
source files via ast, so they exercise the ACTUAL shipped code, not a copy —
without needing napari/Qt/aicsimageio.

Set PYCAT_SRC to the src/pycat directory to enable; skipped otherwise.
"""
import ast
import os

import pytest

_SRC = os.environ.get("PYCAT_SRC")
pytestmark = pytest.mark.skipif(
    not (_SRC and os.path.isdir(_SRC)),
    reason="set PYCAT_SRC=/path/to/src/pycat to run the loader-fix tests")


def _load_func(rel_path, func_name, inject=None):
    """Extract a top-level function from a source file and exec it in isolation
    (so we don't import the module's heavy Qt/napari dependencies)."""
    src = open(os.path.join(_SRC, rel_path), encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = dict(inject or {})
            exec(ast.get_source_segment(src, node), ns)
            return ns[func_name]
    raise AssertionError(f"{func_name} not found in {rel_path}")


# --------------------------------------------------------------------------- #
# 0002 — frame interval prefers timestamps and flags conflicts                #
# --------------------------------------------------------------------------- #
def test_reconcile_prefers_timestamps_and_flags_conflict():
    reconcile = _load_func("file_io/metadata_extract.py", "reconcile_frame_interval")
    r = reconcile(0.5, "ome_time_increment", 0.1, "ome_delta_t", 0.005)
    assert r["frame_interval_s"] == 0.1              # timestamps win
    assert r["frame_interval_source"] == "ome_delta_t"
    assert r["frame_interval_nominal_s"] == 0.5
    assert r["frame_interval_inconsistent"] is True
    assert "0.5" in r["message"] and "0.1" in r["message"]


def test_reconcile_agreement_is_not_flagged():
    reconcile = _load_func("file_io/metadata_extract.py", "reconcile_frame_interval")
    r = reconcile(0.5, "nom", 0.48, "der")
    assert r["frame_interval_s"] == 0.48
    assert r["frame_interval_inconsistent"] is False


def test_reconcile_single_source_and_zero_rejected():
    reconcile = _load_func("file_io/metadata_extract.py", "reconcile_frame_interval")
    assert reconcile(0.5, "n", None, None)["frame_interval_s"] == 0.5      # nominal only
    assert reconcile(None, None, 0.1, "d")["frame_interval_s"] == 0.1      # derived only
    assert reconcile(0.0, "n", 0.1, "d")["frame_interval_nominal_s"] is None  # zero nominal dropped


# --------------------------------------------------------------------------- #
# 0003 — layer names derived from the filename                                #
# --------------------------------------------------------------------------- #
def test_layer_name_from_filename():
    dln = _load_func("file_io/file_io.py", "derive_layer_name")
    assert dln("cells_DAPI") == "cells_DAPI"
    assert dln("exp1_GFP") == "exp1_GFP"
    assert dln("untitled") == "untitled"
    assert dln("cells_DAPI", is_mask=True) == "cells_DAPI Mask"
    # metadata label appended only when it adds information
    assert dln("mydata", None, [{"source": "name", "label": "mCherry"}]) == "mydata · mCherry"
    # a positional GUESS is never appended (this is what stopped both files
    # collapsing to 'C0-DAPI')
    assert dln("data", None, [{"source": "position", "label": "DAPI"}]) == "data"
    # filename fallback and the generic last resort
    assert dln(None, "/p/some_RFP.tif") == "some_RFP"
    assert dln(None, None) == "Fluorescence Image"
    assert dln(None, None, is_mask=True) == "Mask Layer"


# --------------------------------------------------------------------------- #
# 0004 — metadata description blob parsed into fields                         #
# --------------------------------------------------------------------------- #
def test_parse_micromanager_json():
    parse = _load_func("file_io/metadata_extract.py", "parse_description_blob")
    r = parse('{"Interval_ms": 0, "Exposure-ms": 100, "Camera": "Andor", "Channels": ["GFP"]}')
    assert r["Exposure-ms"] == 100 and r["Camera"] == "Andor"
    assert "Channels" not in r          # nested container skipped in the flat view


def test_parse_imagej_and_ome():
    parse = _load_func("file_io/metadata_extract.py", "parse_description_blob")
    ij = parse("ImageJ=1.53\nunit=micron\nspacing=0.5\nframes=120")
    assert ij["unit"] == "micron" and ij["frames"] == "120"
    ome = parse('<?xml version="1.0"?><OME><Image><Pixels TimeIncrement="0.5" SizeT="120"/></Image></OME>')
    assert ome["TimeIncrement"] == "0.5" and ome["SizeT"] == "120"
    assert parse("") == {} and parse("just prose") == {}
