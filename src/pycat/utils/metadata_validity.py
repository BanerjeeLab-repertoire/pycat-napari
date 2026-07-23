"""**Is a metadata value MEANINGFUL, or present-but-useless?** (tag_confidence_and_metadata_validity Part 2)

A present-but-meaningless metadata value is worse than an absent one: it looks authoritative, it suppresses
the prompt that would have asked the user, and it silently satisfies gates that exist to catch missing
information (the pixel-size gate being the sharpest example). Real files show this directly — a Zeiss export
writes `Detector Model=""`, `<Microscope/>`, `PositionX="NaN"`.

So there is ONE shared filter, applied wherever metadata is recorded: a value is kept only if it is
meaningful. **A rejected value makes the field `None`**, which then correctly triggers the existing gates and
prompts — never a substituted "better" default.

The rules, in order:
- **Empty / whitespace-only** strings → reject.
- **Placeholder tokens** (case-insensitive): ``unknown``, ``n/a``, ``na``, ``none``, ``null``, ``undefined``,
  ``<none>``, ``default``, ``-``, ``?``, ``nan``, ``inf`` → reject.
- **Non-finite numbers** (``NaN``, ``inf``) → reject.
- **Field-aware sentinels** — the important, precise part: a ``pixel_size`` of exactly ``1.0`` is the known
  no-metadata sentinel (``test_pixel_size_sentinel``); a ``gain`` / ``magnification`` / ``NA`` of ``0`` is
  physically impossible. But ``binning == 1`` and ``amplification_gain == 1.0`` are **legitimate** — the
  number ``1`` is never blanket-rejected.
"""
from __future__ import annotations

import math

_PLACEHOLDERS = frozenset({
    'unknown', 'n/a', 'na', 'none', 'null', 'undefined', '<none>', 'default', '-', '?', 'nan', 'inf',
    'not available', 'n.a.', '--', 'tbd',
})


def _zero_is_impossible(field_lower: str) -> bool:
    """Fields where a value of exactly 0 is physically impossible (so 0 means 'unset'). Deliberately
    precise — ``amplification_gain`` is excluded (its 1.0 is legitimate and its 0 is not our call), and
    short tokens like ``na`` are matched exactly, never as a substring of ``channel_name``."""
    if 'magnification' in field_lower:
        return True
    if 'gain' in field_lower and 'amplification' not in field_lower:
        return True
    if field_lower in ('na', 'lens_na', 'numerical_aperture') or field_lower.endswith('_na'):
        return True
    return False


def is_meaningful(field, value) -> bool:
    """True if ``value`` should be recorded for ``field``; False if it is empty / a placeholder / non-finite
    / a field-specific sentinel. ``field`` names the metadata key (used only for the field-aware sentinels)."""
    if value is None:
        return False

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        return stripped.lower() not in _PLACEHOLDERS

    # Non-string, non-numeric (a list/dict/etc.) — not our concern; keep it.
    try:
        number = float(value)
    except (TypeError, ValueError):
        return True

    if not math.isfinite(number):                       # NaN / inf
        return False

    field_lower = str(field).lower()
    if 'pixel_size' in field_lower and abs(number - 1.0) < 1e-9:
        return False                                    # the 1.0 no-metadata sentinel
    if abs(number) < 1e-12 and _zero_is_impossible(field_lower):
        return False                                    # gain/magnification/NA of 0 is impossible
    return True


def rejection_reason(field, value):
    """A short, human-readable reason a value was rejected — for the ``raw`` block's ``rejected_reason``, so a
    discard is visible ("the file said Model='' and we discarded it"), not silent. None if it is meaningful."""
    if is_meaningful(field, value):
        return None
    if value is None:
        return 'absent'
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 'empty string'
        return f'placeholder token {value!r}'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return f'non-finite ({value})'
    field_lower = str(field).lower()
    if 'pixel_size' in field_lower and abs(number - 1.0) < 1e-9:
        return 'the 1.0 µm/px no-metadata sentinel'
    return f'{field}={value} is physically impossible (sentinel for unset)'
