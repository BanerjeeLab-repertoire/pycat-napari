"""**Section-binding coverage ratchet (Spec 1.5).**

`navigator/data/section_bindings.json` maps a plan step's op-id to the `_add_*` UI section builder that renders
it, so the Navigator can generate a real method panel (Spec 1.2). This guards that mapping the way
`test_complexity_budget` guards complexity and `test_route_equivalence` guards the batch routes:

- every binding names a builder that actually EXISTS (a stale name = a silently missing panel section);
- coverage only GROWS, never shrinks;
- any op a canonical plan can select but that has NO section is a DECLARED gap, not a build-time surprise
  (the same declared-gap discipline the route-equivalence harness uses).

All tests are marked `base`: although tests 1–3 are pure JSON + AST in spirit, importing
`pycat.navigator.sections` runs the navigator package's `__init__`, which eagerly pulls the planner/op-catalog
stack — so this file needs the scientific lane, not the minimal one. Test 4 additionally compiles real plans and
imports the planner inside the test.
"""
import ast
import json
from pathlib import Path

import pytest

from pycat.navigator.sections import section_for, mapped_op_ids

_REPO = Path(__file__).resolve().parents[2]
_BINDINGS = _REPO / "src" / "pycat" / "navigator" / "data" / "section_bindings.json"

# The coverage floor may only go UP. Raise it as bindings are added; never lower it.
_SECTION_COVERAGE_FLOOR = 22

# Op-ids a canonical plan can select that have NO interactive panel section BY DESIGN — automatic gates and
# pseudo-steps, not tools the user runs from a section. Declaring them here makes "no section" a recorded
# decision. An op that is neither mapped nor listed here fails test 4 loudly.
_KNOWN_GAPS = frozenset({
    "data_qc.assess",     # automatic QC probe (focus/SNR/saturation) — a gate, not a runnable section
    "acquisition",        # the load/acquisition pseudo-step the planner prepends — no tool panel
})


def _add_builder_names_in_source() -> frozenset:
    """AST-scan the UI source for every ``def _add_*`` method name (across the ToolboxFunctionsUI mixins and the
    panel classes). AST, not import — no Qt, no instantiation."""
    names = set()
    for base in ("ui", "toolbox"):
        for path in (_REPO / "src" / "pycat" / base).rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_add_"):
                    names.add(node.name)
    return frozenset(names)


@pytest.mark.base
def test_section_bindings_json_is_wellformed():
    data = json.loads(_BINDINGS.read_text(encoding="utf-8"))
    assert data.get("schema_version") == 1, "section_bindings.json must carry schema_version 1"
    sections = data["sections"]
    assert isinstance(sections, dict) and sections
    for op_id, binding in sections.items():
        assert set(binding) >= {"builder", "owner"}, f"{op_id}: a binding needs 'builder' and 'owner'"
        assert binding["builder"].startswith("_add_"), f"{op_id}: builder must be an _add_* section builder"
        assert binding["owner"], f"{op_id}: builder needs an owner object on central_manager"


@pytest.mark.base
def test_every_mapped_builder_exists():
    """A binding naming a builder that no longer exists yields a silently missing step. The builder name is a
    contract — check it against the real `def _add_*` methods in the source."""
    defined = _add_builder_names_in_source()
    for op_id in mapped_op_ids():
        builder = section_for(op_id)["builder"]
        assert builder in defined, (
            f"section_bindings maps {op_id!r} -> {builder!r}, but no `def {builder}` exists in the UI source. "
            f"A generated panel would silently drop this step."
        )


@pytest.mark.base
def test_section_coverage_does_not_shrink():
    """Generated panels are only as good as the mapping. This is a ratchet, like test_complexity_budget:
    coverage may grow, never regress."""
    assert len(mapped_op_ids()) >= _SECTION_COVERAGE_FLOOR, (
        f"section coverage fell to {len(mapped_op_ids())} below the floor {_SECTION_COVERAGE_FLOOR}. "
        f"Bindings may only be added; if you added some, raise the floor."
    )


@pytest.mark.base
def test_builder_for_resolves_or_refuses_but_never_guesses():
    """The loader's contract: resolve a mapped op to its bound builder, else return None — never raise, never
    fall back to a name-similarity guess (the same discipline the execution adapters use)."""
    from pycat.navigator.sections import builder_for

    class _FakeToolbox:
        def _add_run_cellpose_segmentation(self, layout=None, separate_widget=False):
            return "built"

    class _FakeCM:
        def __init__(self):
            self.toolbox_functions_ui = _FakeToolbox()

    cm = _FakeCM()
    resolved = builder_for(cm, "cellpose")                 # mapped + builder present -> bound callable
    assert callable(resolved) and resolved() == "built"
    assert builder_for(cm, "no_such_op") is None           # unmapped -> None, not a guess
    assert builder_for(cm, "preprocess") is None           # mapped but builder absent on this owner -> None

    class _Empty:
        pass

    assert builder_for(_Empty(), "cellpose") is None        # owner attribute absent -> None


@pytest.mark.base
def test_placeholder_text_names_the_step_and_where_to_run_it():
    """The gap placeholder must name the step and say where to run it — a deferred step is visible, never a
    silent drop. Pure text, tested here; the Qt panel only wraps it in a QLabel."""
    from pycat.navigator.sections import placeholder_text
    txt = placeholder_text("spatial_statistics")
    assert "spatial statistics" in txt                       # the op label, humanised
    assert "no panel section is wired" in txt
    assert "Run it from its own method panel" in txt
    # a dotted measure-op id uses its last segment as the label
    assert "cell analysis" in placeholder_text("feature_analysis.cell_analysis")


@pytest.mark.base
def test_resolve_plan_sections_walks_a_real_plan_in_order_with_gaps_flagged():
    """The Spec 1.2 join, headlessly: resolving a real cell plan yields its steps IN EXECUTION ORDER, each mapped
    step carrying its builder name and each unmapped step flagged as a gap (never dropped). This is what
    GeneratedMethodUI walks; the only thing it adds is calling the bound builder / rendering the placeholder."""
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order
    from pycat.navigator.contracts import AnalysisIntent
    from pycat.navigator.sections import resolve_plan_sections

    session = NavigatorSession()
    session.intent = AnalysisIntent(target="cell", observables=["morphology"])
    plan = session.planner.compile(session.intent, session.ctx, pins={})

    planned = resolve_plan_sections(plan)
    # order and op-ids match the executor's, exactly — never re-derived
    assert [ps.op_id for ps in planned] == [step.name for step in execution_order(plan)]
    by_op = {ps.op_id: ps for ps in planned}
    # the segmentation + analysis steps resolve to their real builders
    assert by_op["cellpose"].builder_name == "_add_run_cellpose_segmentation" and not by_op["cellpose"].gap
    assert by_op["feature_analysis.cell_analysis"].builder_name == "_add_run_cell_analysis_func"
    # the automatic gates are gaps: no builder, flagged for a placeholder, NOT dropped
    assert by_op["data_qc.assess"].gap and by_op["data_qc.assess"].builder_name is None
    # every gap has no builder and every non-gap has one — the placeholder/render decision is unambiguous
    assert all((ps.builder_name is None) == ps.gap for ps in planned)


@pytest.mark.base
def test_planner_ops_that_lack_sections_are_declared():
    """Any op a canonical plan can select but that has no section must be in _KNOWN_GAPS — so 'this step has no
    UI' is a recorded decision, not a surprise at build time. Mirrors the route-equivalence declared-gap harness."""
    from pycat.navigator.session import NavigatorSession
    from pycat.navigator.execution import execution_order
    from pycat.navigator.contracts import AnalysisIntent

    canonical = [
        ("cell", ["morphology"]),
        ("condensate", ["count", "size"]),
        ("condensate", ["colocalization"]),
    ]
    mapped = mapped_op_ids()
    undeclared = {}
    for target, observables in canonical:
        session = NavigatorSession()
        session.intent = AnalysisIntent(target=target, observables=observables)
        plan = session.planner.compile(session.intent, session.ctx, pins={})
        for step in execution_order(plan):
            op_id = step.name
            if op_id not in mapped and op_id not in _KNOWN_GAPS:
                undeclared.setdefault(op_id, f"{target}/{observables}")
    assert not undeclared, (
        "canonical plans select ops with neither a section binding nor a _KNOWN_GAPS declaration: "
        + ", ".join(f"{op} (from {where})" for op, where in undeclared.items())
        + ". Bind a builder for it, or declare it a known gap."
    )
