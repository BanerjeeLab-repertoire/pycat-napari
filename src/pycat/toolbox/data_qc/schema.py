"""The QC result contract — versioned, frozen, and serialisable (Completion Spec G10).

Every check returns one dict per metric. The contract used to be implicit: ``status``/``tier`` were bare repeated
string literals, two checks added top-level keys nobody declared, ``diag`` held raw numpy arrays (not
JSON-serialisable), and the report indexed keys unconditionally (``KeyError`` instead of degrading). Here:

* ``SCHEMA_VERSION`` names the contract; bump it when the key set or a key's meaning changes.
* ``RESULT_KEYS`` is the frozen key set. ``normalise_result`` makes any dict conform: missing keys get their
  default, undeclared top-level keys move into ``diag`` (nothing is dropped), status/tier become enums.
* ``QCStatus`` / ``QCTier`` are ``str`` enums whose member NAMES equal their values, so ``r['status'] == 'bad'``,
  ``{'good': 1.0}[r['status']]`` and ``f"{r['status']}"`` all keep working for every existing caller.
* ``results_to_json`` emits the supplementary-file form: ``{schema_version, results: [...]}`` with arrays as
  lists and non-finite numbers as ``null``.
"""
from __future__ import annotations

import enum
import json
import math

import numpy as np

SCHEMA_VERSION = '1.0'


class QCStatus(enum.StrEnum):
    good = 'good'
    warn = 'warn'
    bad = 'bad'
    info = 'info'      # reported, not judged (e.g. needs the optics to become a verdict)
    na = 'na'          # declined: the check cannot support a verdict on this data (the reason is in `how`)


class QCTier(enum.StrEnum):
    core = 'core'
    advisory = 'advisory'


#: The frozen key set, in report order. ``diag`` is the only open container.
RESULT_KEYS = ('name', 'tier', 'status', 'value', 'unit', 'headline', 'how', 'good', 'diag')
_DEFAULTS = {'name': '', 'tier': QCTier.core, 'status': QCStatus.na, 'value': None, 'unit': '',
             'headline': '', 'how': '', 'good': '', 'diag': None}


def normalise_result(result) -> dict:
    """``result`` conformed to the contract: every key present, extras moved into ``diag``, enums for status/tier.
    An unknown status/tier string is kept as ``na``/``core`` with the original recorded in ``diag``."""
    r = dict(result or {})
    out = {k: r.pop(k, _DEFAULTS[k]) for k in RESULT_KEYS}
    diag = dict(out['diag']) if isinstance(out['diag'], dict) else ({} if out['diag'] is None else
                                                                   {'value': out['diag']})
    diag.update(r)                                   # undeclared top-level keys: kept, but under diag
    for key, enum_type, default in (('status', QCStatus, QCStatus.na), ('tier', QCTier, QCTier.core)):
        try:
            out[key] = enum_type(out[key])
        except ValueError:
            diag[f'unrecognised_{key}'] = out[key]
            out[key] = default
    out['diag'] = diag or None
    return out


def finalise_results(results) -> list:
    return [normalise_result(r) for r in results]


def _jsonable(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)


def results_to_dict(results) -> dict:
    """The JSON-serialisable form of a QC report (a supplementary-file payload)."""
    return {'schema_version': SCHEMA_VERSION,
            'results': [_jsonable(normalise_result(r)) for r in results]}


def results_to_json(results, **json_kwargs) -> str:
    return json.dumps(results_to_dict(results), allow_nan=False, **json_kwargs)
