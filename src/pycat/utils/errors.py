"""**Typed failures for the things PyCAT actually distinguishes — so a caller can catch a family.**

`except Exception: pass` is the codebase's most common failure handler, and most of them are fine (Qt
teardown, an optional backend that isn't installed, a metadata probe that may legitimately find
nothing). The dangerous ones are the handlers that swallow a *scientific* failure — a calibration that
isn't valid, an assumption that doesn't hold — and return a plausible number anyway. Those should raise
a **named** error a caller can catch specifically, not a bare `Exception` indistinguishable from a
missing font.

This module is that vocabulary. It is deliberately small: exactly the failures the code already
distinguishes in its comments and messages, all deriving from `PyCATError` so a caller can catch the
whole family (`except PyCATError`) or one kind (`except InvalidCalibrationError`). Do not grow it
speculatively — add a class when a real handler is converted to raise it.
"""

from __future__ import annotations


class PyCATError(Exception):
    """Base for every typed PyCAT failure. Catch this to handle the whole family; catch a subclass to
    handle one kind. A bare ``except Exception`` still catches these — the point is that it no longer
    has to, because the failure now has a name."""


class UnsupportedFormatError(PyCATError, ValueError):
    """A file/layer is of a kind PyCAT cannot read or process — not a corrupt file, an unsupported one.
    The message should name the format and what was expected.

    Also a ``ValueError`` (it replaces the bare ``raise ValueError`` these checks used), so existing
    ``except ValueError`` callers keep working while new code can catch the ``PyCATError`` family."""


class MetadataUnavailableError(PyCATError):
    """Required metadata (pixel size, a channel map, a time axis) is genuinely absent — as opposed to
    present-but-implausible. Raised where proceeding would mean fabricating it."""


class InvalidCalibrationError(PyCATError):
    """A calibration (curve, pixel size, exposure/gain match) is missing, expired, or does not apply to
    this acquisition. Raising this is the difference between 'no concentration reported' and a
    concentration computed from a calibration that was never valid here."""


class ScientificAssumptionError(PyCATError, ValueError):
    """A scientific precondition for a computation does not hold — too few replicates for a test, a fit
    whose assumptions failed, a gate whose input is out of range. The message must NAME the assumption,
    so a refusal reads as a reason, never a silent wrong number.

    Also a ``ValueError`` (it replaces the bare ``raise ValueError`` these gates used), so existing
    ``except ValueError`` / ``pytest.raises(ValueError)`` callers keep working while new code can catch
    the ``PyCATError`` family or this kind specifically."""


class OptionalDependencyError(PyCATError):
    """An optional backend/package needed for this path is not installed (cellpose, a GPU stack, an
    Excel reader). Distinct from a bug: the honest response is 'install X', not a traceback."""


class LayerResolutionError(PyCATError):
    """A step could not resolve the layer it needs (none match, or several do and choosing would be a
    guess). Mirrors the resolver's refuse-to-guess contract — a wrong auto-selection is worse than an
    empty one."""


__all__ = [
    'PyCATError', 'UnsupportedFormatError', 'MetadataUnavailableError',
    'InvalidCalibrationError', 'ScientificAssumptionError', 'OptionalDependencyError',
    'LayerResolutionError',
]
