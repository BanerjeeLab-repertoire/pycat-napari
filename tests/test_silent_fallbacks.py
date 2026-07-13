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


# ── The lazy-stack collapse: it has bitten FOUR times ─────────────────────────────────────

_LAZY_STACK_ALLOWED = {
    # Sites where the layer is genuinely 2D and `np.asarray` is correct. Each is a DELIBERATE
    # exclusion, and the list is short on purpose: **if it grows, the guard is being eroded by
    # exception rather than the bug being fixed.**
    'file_io/stack_access.py',        # the module that FIXES this; its docstring quotes the bug
    'file_io/file_io.py',             # the loader itself — it is what BUILDS the lazy wrappers
    'toolbox/fibril_tools.py',        # 2D masks/skeletons
    'toolbox/pipeline_snr_tools.py',  # a single frame, by construction
    'toolbox/topology_tools.py',      # 2D envelopes and cell masks
    'toolbox/label_and_mask_tools.py',# 2D masks
    'utils/brushing.py',              # crops one frame, and indexes it explicitly first
    'ui/coordinate_readout.py',       # reads the pixel under the cursor
    'ui/ui_diagnostics_mixin.py',     # a diagnostic dump
    'ui/ui_utils.py',                 # 2D display helpers
    'toolbox/data_qc_ui.py',          # already materializes; the asarray is on the result
    'toolbox/ts_cellpose_tools.py',   # annotation layers, indexed [0] explicitly
}


@pytest.mark.core
def test_time_series_analyses_do_not_collapse_a_lazy_stack_to_frame_zero():
    """**``np.asarray(layer.data)`` on a lazy wrapper returns FRAME 0 ONLY.**

    PyCAT's lazy wrappers deliberately truncate ``__array__`` so napari's thumbnail request does
    not materialise a multi-gigabyte movie. **Nothing errors.** The array simply comes back 2D,
    and the analysis runs on one frame while reporting it as the whole movie.

    **This bug has now been found four times** — VPT (1.5.273), the temperature UI (1.5.253), and
    then ``nb_tools`` and ``spida_tools`` in this audit. It is not a coding mistake that keeps
    recurring; **it is a missing guard.**

    N&B was the worst case. It needs ``(T, H, W)``, and the check immediately after the
    ``np.asarray`` is::

        if data.ndim < 3:  "N&B needs a time-series ... but this layer is 2D"

    So a user who loaded a **correct time-series** was told their data was **2D**. The message was
    not merely unhelpful — **it was wrong**, and it sent them off to fix a problem they did not
    have. *(And N&B's whole measurement is a variance across time. On one frame, that is zero.)*

    SpIDA was quieter and no better: the user scrolls to frame 40, runs it, and **silently
    analyses frame 0.**

    Any module that consumes a stack must call ``stack_access.materialize_stack`` (or
    ``iter_frames``). The modules listed in ``_LAZY_STACK_ALLOWED`` are genuinely 2D.
    """
    import re

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        relative = str(path.relative_to(source_root)).replace('\\', '/')
        if relative in _LAZY_STACK_ALLOWED:
            continue

        source = path.read_text(encoding='utf-8', errors='ignore')

        for match in re.finditer(r'np\.(?:asarray|array)\s*\(\s*(\w*layer\w*)\.data', source):
            line_number = source[:match.start()].count('\n') + 1
            offenders.append(f"{relative}:{line_number}")

    assert not offenders, (
        f"these sites call np.asarray on a layer's data: {offenders}\n\n"
        f"On one of PyCAT's LAZY wrappers that returns **frame 0 only** — silently. Use "
        f"`stack_access.materialize_stack(layer.data)` if the analysis needs every frame, or add "
        f"the module to _LAZY_STACK_ALLOWED **with a reason** if the layer is genuinely 2D."
    )


@pytest.mark.core
def test_the_stack_helpers_have_ONE_implementation():
    """**They were defined twice, byte-identically — and that is the dangerous state.**

    ``materialize_stack``, ``iter_frames``, ``layer_is_stack``, ``extract_2d_plane`` and
    ``warn_if_assumed_axis`` existed in **both** ``file_io.py`` and ``stack_access.py``, as exact
    copies.

    **They agreed, so nothing would catch the day they stopped.** And these are not any five
    functions — **they are the functions that fix the lazy-stack bug**, the one that has silently
    collapsed a movie to frame 0 **four separate times**. *Fixing one copy and missing the other is
    exactly how that bug survives.*

    ``stack_access.py`` owns them now (it is the purpose-built module — its docstring names the
    bug), and ``file_io.py`` re-exports, so all 25 existing call sites keep working.
    """
    import ast

    file_io = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "file_io"

    owned = {'materialize_stack', 'iter_frames', 'layer_is_stack',
             'extract_2d_plane', 'warn_if_assumed_axis'}

    def _defined_in(path):
        tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}

    in_stack_access = _defined_in(file_io / "stack_access.py")
    in_file_io = _defined_in(file_io / "file_io.py")

    assert owned <= in_stack_access, (
        f"stack_access.py must OWN the stack helpers; it is missing "
        f"{sorted(owned - in_stack_access)}"
    )

    duplicated = owned & in_file_io
    assert not duplicated, (
        f"file_io.py re-DEFINES {sorted(duplicated)} instead of re-exporting them. **Two "
        f"implementations of the function that fixes the lazy-stack bug** is how that bug "
        f"survives a fix: patch one copy, miss the other."
    )
