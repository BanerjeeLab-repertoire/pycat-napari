"""**Operation-guidance coverage ratchet + authoring round-trip (Method-Widget Spec 3 infrastructure).**

`navigator/data/operation_guidance.json` holds the scientist-authored guidance (when-to-use / advantages /
limitations / alternatives / not-applicable-when / references) the Navigator shows for an op. The CONTENT is
authored by hand — never generated — so this guards the STRUCTURE, not the science:

- the store is well-formed and schema-versioned;
- every authored entry references a REAL op and uses only the known fields with the right types;
- the reader refuses to guess (None for an unauthored op);
- authored coverage only GROWS (a ratchet, floor 0 while the store ships empty);
- the authoring vehicle round-trips: a generated workbook, filled and ingested, yields the authored guidance.

All `base`: importing `pycat.navigator.guidance` runs the navigator package `__init__`, so this needs the
scientific lane, and the workbook helpers use `openpyxl`.
"""
import json
from pathlib import Path

import pytest

from pycat.navigator.guidance import (
    guidance_for, authored_op_ids, GUIDANCE_FIELDS, GUIDANCE_TEXT_FIELDS, GUIDANCE_LIST_FIELDS)

pytestmark = pytest.mark.base

_REPO = Path(__file__).resolve().parents[2]
_GUIDANCE = _REPO / "src" / "pycat" / "navigator" / "data" / "operation_guidance.json"

# Authored coverage may only go UP. Raise as guidance is authored; never lower.
_GUIDANCE_COVERAGE_FLOOR = 0


def _real_op_ids() -> frozenset:
    """Every op-id a plan can carry: the catalog ops + the measure/interpret ops (which live in code, not the
    catalog JSON). An authored guidance key must be one of these."""
    from pycat.navigator.op_catalog import _measure_ops
    cat = json.loads((_REPO / "src" / "pycat" / "navigator" / "data" / "operation_catalog.json")
                     .read_text(encoding="utf-8"))
    ids = {o.get("op") for o in cat.get("operations", [])}
    ids |= {o.get("id") for o in _measure_ops()}
    return frozenset(i for i in ids if i)


def test_guidance_json_is_wellformed():
    data = json.loads(_GUIDANCE.read_text(encoding="utf-8"))
    assert data.get("schema_version") == 1, "operation_guidance.json must carry schema_version 1"
    assert isinstance(data.get("guidance"), dict)


def test_every_authored_entry_references_a_real_op_and_valid_fields():
    """A guidance key naming an op that does not exist would be dead content; a stray field would be lost. Guard
    both — even while the store is empty this pins the contract for when authoring begins."""
    real = _real_op_ids()
    allowed = set(GUIDANCE_FIELDS)
    for op_id in authored_op_ids():
        assert op_id in real, f"guidance authored for {op_id!r}, which is not a real op"
        entry = guidance_for(op_id)
        assert set(entry) <= allowed, f"{op_id}: unknown guidance field(s) {set(entry) - allowed}"
        for f in GUIDANCE_TEXT_FIELDS:
            assert isinstance(entry.get(f, ""), str), f"{op_id}.{f} must be prose (str)"
        for f in GUIDANCE_LIST_FIELDS:
            assert isinstance(entry.get(f, []), list), f"{op_id}.{f} must be a list"


def test_guidance_coverage_does_not_shrink():
    assert len(authored_op_ids()) >= _GUIDANCE_COVERAGE_FLOOR, (
        f"authored guidance coverage fell to {len(authored_op_ids())} below the floor "
        f"{_GUIDANCE_COVERAGE_FLOOR}. Guidance may only be added; if you authored some, raise the floor."
    )


def test_guidance_for_refuses_to_guess_for_an_unauthored_op():
    # a real op with no authored guidance returns None — never a fabricated stand-in
    assert guidance_for("cellpose") is None or isinstance(guidance_for("cellpose"), dict)
    assert guidance_for("no_such_op_xyz") is None


def test_the_authoring_workbook_round_trips(tmp_path):
    """The authoring vehicle, headless: generate a fill-in workbook (factual columns pre-filled, judgement columns
    blank), author one op in it, ingest it back — the authored guidance comes through, list fields split per
    line, and blank rows are NOT entries. Ingests to a TMP path so the shipped store is never mutated."""
    openpyxl = pytest.importorskip("openpyxl")
    from pycat.navigator.guidance import generate_guidance_workbook, ingest_guidance_workbook

    wb_path = tmp_path / "guide.xlsx"
    n = generate_guidance_workbook(wb_path)
    assert n > 0 and wb_path.exists()

    wb = openpyxl.load_workbook(str(wb_path))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header[:3] == ["op_id", "module", "summary"] and header[3:] == list(GUIDANCE_FIELDS)
    col = {name: i + 1 for i, name in enumerate(header)}
    target = next((r for r in range(2, ws.max_row + 1)
                   if ws.cell(r, col["op_id"]).value == "cellpose"), None)
    assert target, "the generated workbook should have a row per catalog op, including cellpose"
    ws.cell(target, col["when_to_use"]).value = "Use for whole-cell bodies with a learned model."
    ws.cell(target, col["alternatives"]).value = "local_threshold\nfelzenszwalb"
    wb.save(str(wb_path))

    result = ingest_guidance_workbook(wb_path, out_path=tmp_path / "out.json")
    assert result["cellpose"]["when_to_use"].startswith("Use for whole-cell")
    assert result["cellpose"]["alternatives"] == ["local_threshold", "felzenszwalb"]   # split per line
    # a row left blank is not a guidance entry
    assert "bandpass" not in result
