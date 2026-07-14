"""
**The runner reported PASS for 23 tests it never ran.**

``tools/run_core_tests.py`` is a hand-rolled substitute for pytest — the sandbox has no pytest and
no network. Its dispatch was: *no parameters → run it; parameters → treat them as ``parametrize``
cases.*

**But a test asking for a FIXTURE has parameters and no ``parametrize`` decorator.** It fell into
the parametrize branch, which built its cases from ``SCIENTIFIC_MODULES`` — a name most test files
do not define. ``ns.get(...)`` returned ``[]``, so ``combinations`` was **empty**, **the loop body
never executed**, ``n_fail`` stayed ``0``, and the runner printed::

    test_a_CHANGED_file_gets_a_FRESH_reader   PASS

***A test whose entire body is `assert False` reported PASS.*** Verified with a canary.

**And these were not peripheral tests:**

* ``test_reader_cache`` — **4 of 5**
* ``test_one_plane_reads_one_plane`` — **3 of 5** *(the perf guard the whole 1.6 arc turns on)*
* ``test_file_io`` — **5 of 5**, the entire file

The guards protecting the BioIO migration were reporting green **without executing a single line**,
for an entire release arc. *This is the same failure as a metric that cannot catch its own bug, one
level up: **the thing that checks the checks was not being checked.***

*(When the runner was fixed and they finally ran, they all passed — so no product bug was hiding
behind this. **But nobody knew that, and that is the whole problem.**)*

── Why this file does not spawn the runner ──────────────────────────────────────────────

The obvious guard plants an ``assert False`` test, runs the real runner in a subprocess, and checks
it says FAIL. *It works* — and it costs **148 seconds**, doubling a suite that already takes that
long, because the inner run executes the entire suite too.

**A guard nobody can afford to run is a guard that gets deleted.**

So the decision itself — ``plan_test``, *"how should this test be run, and CAN it be?"* — is a pure
function, and this tests it **in-process**. That is where the bug lived, and it is the thing that
must never again answer *"parametrize"* for a test with no cases to run.
"""

import pathlib
import sys

import pytest


_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

run_core_tests = pytest.importorskip("run_core_tests")


@pytest.mark.core
def test_a_test_that_CANNOT_RUN_is_a_FAILURE_not_a_pass():
    """**The bug, pinned.**

    A test asking for a fixture nobody can build must be **unrunnable** — ``None`` — so the runner
    reports FAIL. If this ever answers ``'parametrize'`` again, it will build zero cases, execute
    nothing, and print PASS.
    """
    plan = run_core_tests.plan_test(
        parameters=['a_fixture_nobody_defined'],
        is_parametrized=False,
        namespace={},                       # no SCIENTIFIC_MODULES — the 23 tests' situation
    )

    assert plan is None, (
        f"a test with an unbuildable fixture was planned as {plan!r}.\n\n"
        "**If that is 'parametrize', the runner builds zero cases, runs nothing, and prints "
        "PASS.** That is exactly how 23 guards — including 4 of 5 reader-cache tests and 3 of 5 "
        "one-plane perf tests — reported green for a release arc without ever executing.\n\n"
        "***A test that cannot be run is a FAILURE, not a pass.***"
    )


@pytest.mark.core
@pytest.mark.parametrize('fixture', ['tmp_path', 'monkeypatch', 'capsys'])
def test_the_BUILTIN_fixtures_the_suite_uses_can_be_built(fixture):
    """``tmp_path``, ``monkeypatch``, ``capsys`` — the ones the real guards ask for."""
    plan = run_core_tests.plan_test([fixture], is_parametrized=False, namespace={})

    assert plan == 'fixtures', (
        f"`{fixture}` cannot be injected, so every test using it is unrunnable. "
        f"The suite asks for it — see test_reader_cache, test_one_plane_reads_one_plane."
    )


@pytest.mark.core
def test_a_CUSTOM_fixture_defined_in_the_test_module_can_be_built():
    """``counting_reader``, ``a_fifty_frame_tiff``, ``slow_storage`` — these BUILD the conditions.

    Without them the 1.6 guards cannot run at all, and the runner was calling them PASS anyway.
    """
    def counting_reader():
        return {'opens': 0}
    counting_reader._pycat_fixture = True

    namespace = {'counting_reader': counting_reader}

    plan = run_core_tests.plan_test(['counting_reader'], is_parametrized=False,
                                    namespace=namespace)

    assert plan == 'fixtures', (
        "a `@pytest.fixture` defined in the test module could not be built. The guards that "
        "protect the reader cache and the one-plane read all depend on exactly this."
    )


@pytest.mark.core
def test_a_fixture_is_REALLY_built_not_just_signature_satisfied():
    """*A fixture that is injected but inert is the same bug wearing a hat.*

    ``tmp_path`` must be a real, writable directory — and a **nested** fixture (one that itself
    takes ``tmp_path``) must resolve too, because the suite has those.
    """
    def a_nested_fixture(tmp_path):
        (tmp_path / 'written_by_the_fixture').write_text('real')
        return tmp_path
    a_nested_fixture._pycat_fixture = True

    namespace = {'a_nested_fixture': a_nested_fixture}

    built, teardown = run_core_tests._build_fixtures(['a_nested_fixture'], namespace)

    try:
        assert built is not None, "the nested fixture could not be built"
        path = built['a_nested_fixture']
        assert (path / 'written_by_the_fixture').read_text() == 'real', (
            "the fixture ran but its temp directory is not real — an inert placeholder that "
            "satisfies the signature lets the same bug back in through the other door."
        )
    finally:
        for undo in reversed(teardown or []):
            undo()


@pytest.mark.core
def test_a_test_with_NO_parameters_still_just_runs():
    """The common case must not have been broken by any of this."""
    assert run_core_tests.plan_test([], is_parametrized=False, namespace={}) == 'run'


@pytest.mark.core
def test_a_PARAMETRIZED_test_is_still_parametrized():
    """``parametrize`` still wins over fixture resolution — it is a real decorator, not a guess."""
    assert run_core_tests.plan_test(['axes'], is_parametrized=True, namespace={}) == 'parametrize'
