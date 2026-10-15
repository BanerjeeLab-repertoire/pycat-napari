"""**Correct code that production cannot reach is declared, not discovered** (Completion Spec C4).

Four times the right implementation existed, was tested, and no production path called it: the truncated
size-distribution MLE, ``partition_coefficient_local`` in the time series, ``OperationRunner.execute``, and
``qc_snr`` in the op catalog. A public ``toolbox``/``utils`` function that a test exercises but no production code
references must be listed in ``tests/reachability_gaps.json`` with a reason. A new one fails here until it is
wired or declared; one that becomes reachable must leave the list, so the list only shrinks.
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.base

_GAPS = Path(__file__).with_name('reachability_gaps.json')


def _declared():
    return json.loads(_GAPS.read_text(encoding='utf-8'))


def test_every_tested_but_unreachable_function_is_declared():
    from tests.reachability import unreachable_tested_functions
    found = unreachable_tested_functions()
    new = {n: w for n, w in found.items() if n not in _declared()}
    assert not new, ('public functions with tests but no production caller -- wire them, or declare each in '
                     f'tests/reachability_gaps.json with a reason: {new}')


def test_a_function_that_became_reachable_leaves_the_list():
    from tests.reachability import unreachable_tested_functions
    found = unreachable_tested_functions()
    stale = sorted(n for n in _declared() if n not in found)
    assert not stale, f'now reachable (or gone) -- remove from tests/reachability_gaps.json: {stale}'


def test_every_declared_gap_has_a_reason():
    assert all(entry.get('reason') for entry in _declared().values())
