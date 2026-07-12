"""
A silent exception in a MEASUREMENT path becomes a wrong number, not a missing feature.

The distinction that matters
----------------------------
Most of the ~330 bare ``except Exception: pass`` handlers in this codebase are harmless —
optional imports, best-effort cleanup, GPU probes that fall back to CPU. Swallowing there costs
a feature, and the user notices.

The dangerous ones **return a plausible value**. ``estimate_psf_sigma`` did::

    except Exception:
        return 1.0

The caller cannot then distinguish *"the PSF is 1.0 px"* from *"the estimation crashed"* — and
1.0 is a perfectly plausible PSF width, so nothing looks wrong.

**It is not a harmless default.** The PSF sigma is the *kernel of the partial-volume
correction*. With a true PSF of 2.5 px and a silent fallback of 1.0:

===========  ===========  ==================  ======
radius (px)  true bias    with fallback 1.0   gap
===========  ===========  ==================  ======
1.0          −0.954       −0.635              0.319
2.0          −0.734       −0.358              **0.376**
4.0          −0.437       −0.185              0.252
===========  ===========  ==================  ======

**Roughly a third of a small object's signal, left uncorrected, silently.**

This test does not forbid fallbacks — a caller often needs *something*. It forbids a fallback
that is **invisible**: if a handler in a science module returns a value, it must also warn, log,
or return an explicit failure flag, so the caller can tell.
"""

import ast
import pathlib

import pytest

_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

# Modules whose job is to produce a NUMBER a scientist will report.
_SCIENCE = [
    "condensate_physics_tools", "invitro_tools", "vpt_tools", "frap_tools", "fusion_tools",
    "nb_tools", "spida_tools", "molecular_counting_tools", "partition_enrichment_tools",
    "pixel_wise_corr_analysis_tools", "spatial_metrology_tools", "gaussian_localization_tools",
    "brightfield_tools", "partial_volume_tools",
]

# A handler that returns one of these is ANNOUNCING failure, which is the correct behaviour.
_HONEST_FAILURE = {"nan", "none", "false"}


def _returns_a_bare_value(handler):
    """Does this handler return a plausible-looking VALUE with no failure signal?"""
    returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return) and n.value is not None]
    if not returns:
        return None

    for ret in returns:
        src = ast.dump(ret.value).lower()

        # A dict carrying a failure flag (fit_success=False, success=False, valid=False,
        # assessable=False) is honest — the caller can see it.
        if isinstance(ret.value, ast.Call) and 'dict' in ast.dump(ret.value.func).lower():
            if any(k in src for k in ('success', 'valid', 'assessable', 'nan', 'refused')):
                continue

        # NaN / None / False announce failure.
        if any(tok in src for tok in _HONEST_FAILURE):
            continue

        # A bare numeric constant is the dangerous case: `return 1.0`, `return 0`.
        if isinstance(ret.value, ast.Constant) and isinstance(ret.value.value, (int, float)):
            return ret.lineno

    return None


def _warns_or_logs(handler):
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        name = ''
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if any(tok in name.lower() for tok in ('warn', 'log', 'print', 'error')):
            return True
    return False


@pytest.mark.core
@pytest.mark.parametrize("module", _SCIENCE)
def test_silent_handlers_do_not_return_plausible_values(module):
    """A handler in a science module may not return a bare number without saying it failed."""
    path = _TOOLBOX / f"{module}.py"
    if not path.exists():
        pytest.skip(f"{module} not present")

    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))

    offenders = []
    for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
        broad = handler.type is None or (
            isinstance(handler.type, ast.Name)
            and handler.type.id in ("Exception", "BaseException"))
        if not broad:
            continue
        if _warns_or_logs(handler):
            continue
        if any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
            continue

        lineno = _returns_a_bare_value(handler)
        if lineno is not None:
            offenders.append(f"{module}.py:{lineno}")

    assert not offenders, (
        "These handlers swallow an exception and return a PLAUSIBLE NUMBER, so the caller "
        "cannot tell the measurement failed:\n  " + "\n  ".join(offenders)
        + "\n\nThis is how `estimate_psf_sigma` returned 1.0 on any error — a perfectly "
          "plausible PSF width, which is the kernel of the partial-volume correction. With a "
          "true PSF of 2.5 px it left roughly a THIRD of a small object's signal uncorrected, "
          "silently.\n\nEither warn/log in the handler, or return an explicit failure "
          "(NaN, None, or a dict with success=False)."
    )
