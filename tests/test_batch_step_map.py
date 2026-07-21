"""**Every `_STEP_MAP` entry resolves to a 4-arg replay callable.**

The batch decomposition (1.6.150) moved the 26 `replay_*` handlers into `pycat.batch.steps.*`, leaving
`_STEP_MAP` in `batch_step_registry.py` importing them. If a move typo'd an import or dropped a handler,
`_STEP_MAP` would carry a broken reference — this catches it instantly, cheaply, and headlessly. The
uniform `(state, image_path, params, output_dir)` signature is the contract batch replay calls them by.
"""
import inspect

import pytest

from pycat.batch_step_registry import _STEP_MAP

pytestmark = pytest.mark.core


def test_every_step_map_value_is_a_four_arg_callable():
    bad = []
    for name, fn in _STEP_MAP.items():
        if not callable(fn):
            bad.append(f"{name!r}: not callable ({fn!r})")
            continue
        try:
            params = list(inspect.signature(fn).parameters.values())
        except (ValueError, TypeError):
            continue                                    # a builtin without an introspectable signature
        positional = [p for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(positional) != 4:
            bad.append(f"{name!r}: {len(positional)} positional params, want 4 "
                       "(state, image_path, params, output_dir)")
    assert not bad, (
        "these _STEP_MAP handlers are broken after the batch decomposition:\n  " + "\n  ".join(bad))


def test_step_map_is_non_trivial():
    """Guard the guard: a broken import that emptied _STEP_MAP must not pass vacuously."""
    assert len(_STEP_MAP) >= 26, f"_STEP_MAP has only {len(_STEP_MAP)} entries — handlers went missing"
