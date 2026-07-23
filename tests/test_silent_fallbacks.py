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
import numpy as np

import pytest

_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

# Modules whose job is to produce a NUMBER a scientist will report.
_SCIENCE = [
    # Still-monolithic science modules:
    "frap_tools", "fusion_tools", "nb_tools", "spida_tools", "molecular_counting_tools",
    "partition_enrichment_tools", "pixel_wise_corr_analysis_tools", "spatial_metrology_tools",
    "gaussian_localization_tools", "brightfield_tools", "partial_volume_tools",
    # DECOMPOSED (2026-07-22): condensate_physics/invitro/vpt moved their science into packages, so the
    # old *_tools.py are now empty shims — this guard must follow the science to where it lives.
    "condensate_physics/coarsening", "condensate_physics/frame_quality", "condensate_physics/intensity",
    "condensate_physics/moduli", "condensate_physics/msd", "condensate_physics/photobleaching",
    "condensate_physics/relaxation", "condensate_physics/survival",
    "vpt/analysis", "vpt/detection", "vpt/drift", "vpt/host", "vpt/populations", "vpt/viscosity",
    "invitro/analysis", "invitro/field_summary", "invitro/partition", "invitro/size_distribution",
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
    import ast
    import re

    source_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"

    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        relative = str(path.relative_to(source_root)).replace('\\', '/')
        if relative in _LAZY_STACK_ALLOWED:
            continue

        source = path.read_text(encoding='utf-8', errors='ignore')

        # ── AST, not a regex over the raw text ───────────────────────────────────
        #
        # The regex that stood here matched **the prose as well as the code.** A docstring
        # explaining *why* ``np.asarray(layer.data)`` is dangerous was itself flagged as an
        # instance of it — the same failure `test_ci_dependencies` documents: *"the guard was
        # checking a comment."*
        #
        # **A guard that cannot tell code from prose will eventually flag its own explanation**,
        # and the fix for that is not to stop explaining.
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (isinstance(function, ast.Attribute)
                    and function.attr in ('asarray', 'array')
                    and getattr(function.value, 'id', None) in ('np', 'numpy', '_np')):
                continue
            if not node.args:
                continue
            argument = node.args[0]
            if not (isinstance(argument, ast.Attribute) and argument.attr == 'data'):
                continue
            # A napari layer is not always in a variable literally named `layer` — the frame-0 bug bit on
            # `active.data` (brightfield best-slice, CLEAN, the flatfield corrector) and `lmask.data`, none
            # of which the old `'layer' in name` heuristic caught. Match the image-layer variable names that
            # actually hold layers, so the guard covers the bug class regardless of what the var is called.
            _var = str(getattr(argument.value, 'id', '')).lower()
            if any(_t in _var for _t in ('layer', 'active', 'image', 'mask')):
                offenders.append(f"{relative}:{node.lineno}")

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


# ── The pixel size: a default of 1 is a CLAIM, not an absence ─────────────────────────────

_NO_PIXEL_GATE_NEEDED = {
    # UIs that report NO physical length or area. Each is a deliberate exclusion, and the list is
    # short on purpose: **if it grows, the guard is being eroded by exception.**
    'contrast_cascade_ui.py',      # preprocessing only — emits images, not measurements
    'data_qc_ui.py',               # QC verdicts, no lengths
    'fd_curve_ui.py',              # force/extension come from the instrument, not the pixel size
    'fusion_ui.py',                # aspect ratios — dimensionless
    'coloc_ui.py',                 # overlap coefficients — dimensionless
    'nb_ui.py',                    # brightness/number — no lengths
    'spida_ui.py',                 # brightness — no lengths
    'molecular_counting_ui.py',    # counts — no lengths
    'topology_ui.py',              # normalised envelope metrics
    'fibril_ui.py',                # takes px_size_um explicitly as a parameter
}


@pytest.mark.core
def test_every_UI_that_reports_a_LENGTH_has_the_pixel_size_gate():
    """**A pixel size of 1 is a CLAIM about the microscope, not an absence of one.**

    ``microns_per_pixel_sq`` defaults to **1** when the metadata does not carry it — and **1 µm/px
    is a plausible value**, not an obviously-wrong one. So a length silently comes out in
    **pixels, labelled as microns**, and nothing says so.

    ``utils/pixel_size.py`` puts it exactly:

        *"A NaN area is visibly wrong; a 1435× overestimate is not."*

    That module exists to guard this, and it has **2 call sites** — while **48 sites read the
    pixel size raw**, defaulting to 1. The gate in ``field_status`` is the UI-level backstop, and
    **two panels that report nothing BUT lengths did not have it**:

    * ``spatial_metrology_ui`` — nearest-neighbour distances, Ripley's L, the pair-correlation
      function. **Every single output is a length.**
    * ``advanced_analysis_ui`` — the main cellular puncta/condensate workflow, reporting areas.

    Both now carry it.
    """
    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    missing = []
    for path in sorted(toolbox.glob("*_ui.py")):
        if path.name in _NO_PIXEL_GATE_NEEDED:
            continue

        source = path.read_text(encoding='utf-8', errors='ignore')

        # Does this panel report a physical length or area?
        import re
        reports_length = bool(re.search(r"['\"][a-z_0-9]+_um2?3?['\"]", source))
        if not reports_length:
            continue

        if 'add_pixel_size_gate' not in source:
            missing.append(path.name)

    assert not missing, (
        f"these UIs report a physical length or area and have NO pixel-size gate: {missing}\n\n"
        f"The pixel size defaults to 1 um/px when the metadata does not carry it — a PLAUSIBLE "
        f"value, not an obviously-wrong one — so every length silently comes out in pixels "
        f"labelled as microns. Add `add_pixel_size_gate`, or add the file to "
        f"_NO_PIXEL_GATE_NEEDED **with a reason** if it genuinely reports no lengths."
    )


@pytest.mark.core
def test_a_pixel_size_of_exactly_1_is_a_SENTINEL_not_a_measurement():
    """**``file_io`` writes ``microns_per_pixel_sq = 1`` when it does not know.**

    It even says so — *"Resolution data incomplete, using default value of 1 (um/px)^2"*.

    So **a value of exactly 1 is the loader saying "I have no idea", not the microscope saying
    "one micron".** ``pixel_size_um`` was returning it as a legitimate measurement, **with no
    warning** — which is precisely the failure that module exists to prevent:

        *1 µm/px is a plausible value, not an obviously-wrong one.*

    ``field_status``'s gate already knew this — ``abs(val - 1.0) > 1e-9`` is its test for a REAL
    scale. **The accessor did not.**

    A microscope whose pixel really IS 1.000 µm is possible, and such a user confirms it through
    the gate, which sets ``pixel_size_confirmed``. **That flag is the one thing that distinguishes
    "the user told us it is 1" from "nobody told us anything".**
    """
    pixel_size = pytest.importorskip("pycat.utils.pixel_size")

    real = pixel_size.pixel_size_um({'microns_per_pixel_sq': 0.0625})
    assert real == pytest.approx(0.25), f"a real pixel size must pass through; got {real}"

    sentinel = pixel_size.pixel_size_um({'microns_per_pixel_sq': 1})
    assert not np.isfinite(sentinel), (
        "a pixel size of EXACTLY 1 is the loader's fallback for 'no resolution in the metadata'. "
        "Returning it as a measurement means every length silently comes out in PIXELS, labelled "
        "as microns."
    )

    confirmed = pixel_size.pixel_size_um(
        {'microns_per_pixel_sq': 1, 'pixel_size_confirmed': True})
    assert confirmed == pytest.approx(1.0), (
        "a user who CONFIRMS a 1 um pixel must be believed — otherwise a legitimate microscope "
        "becomes unusable"
    )


@pytest.mark.core
def test_the_UIs_read_the_pixel_size_through_the_ACCESSOR():
    """**``float(dr.get('microns_per_pixel_sq', 1.0)) ** 0.5`` was copy-pasted into 15 places.**

    It is a verbatim reimplementation of ``pixel_size_um_or_default`` — **minus the warning.** The
    accessor exists, it does exactly this, and it says so when it is defaulting. It had **2 call
    sites** while the copy-paste had 15.

    *That is how a guard stops guarding: not by being removed, but by being bypassed.*
    """
    import re

    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    idiom = re.compile(
        r"float\([^)]*\.get\('microns_per_pixel_sq',\s*1(?:\.0)?\)\)\s*\*\*\s*0\.5")

    offenders = []
    for path in sorted(toolbox.glob("*_ui.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        if idiom.search(source):
            offenders.append(path.name)

    assert not offenders, (
        f"these UIs reimplement the pixel-size accessor by hand: {offenders}\n\n"
        f"`float(dr.get('microns_per_pixel_sq', 1.0)) ** 0.5` is exactly what "
        f"`pixel_size_um_or_default` does — **minus the warning that says the result is in PIXEL "
        f"units, not microns.** Call the accessor."
    )


@pytest.mark.core
def test_require_stack_RAISES_instead_of_returning_frame_zero():
    """**A test guard is an allow-list that erodes. A type is a wall.**

    ``layer_is_stack`` reads ``.shape``, which a lazy wrapper reports **honestly** — it is only
    ``__array__`` that truncates. So the question *"is this a movie?"* has a correct answer, and
    **27 toolbox modules re-derive it by hand from ``.ndim``** on an array that may already have
    been collapsed.

    **The shape is the thing that lies.** ``require_stack`` asks the wrapper instead, and **raises**
    rather than handing back one frame and letting the caller conclude the data is 2-D.
    """
    stack_access = pytest.importorskip("pycat.file_io.stack_access")

    class _LazyStack:
        """Exactly PyCAT's contract: `.shape` is honest, `__array__` is truncated."""

        def __init__(self, frames, height, width):
            self.shape = (frames, height, width)
            self._data = np.random.rand(frames, height, width).astype(np.float32)

        def __array__(self, dtype=None):
            return self._data[0]          # <-- the bug: frame 0 only

        def __getitem__(self, key):
            return self._data[key]

        def __len__(self):
            return self.shape[0]

    lazy = _LazyStack(40, 32, 32)

    # The trap, demonstrated.
    assert np.asarray(lazy).ndim == 2, (
        "the fixture must reproduce the real failure: np.asarray on a lazy wrapper returns 2-D"
    )
    assert stack_access.layer_is_stack(lazy), (
        "...while `.shape` reports the truth. That asymmetry IS the bug."
    )

    # The accessor gets the whole movie.
    data = stack_access.require_stack(lazy, context='a test')
    assert data.shape == (40, 32, 32), f"require_stack returned {data.shape}, not the full movie"

    # And REFUSES a genuinely 2-D layer, instead of proceeding.
    with pytest.raises(stack_access.NotAStack):
        stack_access.require_stack(np.random.rand(32, 32), context='a test')


# ── The frame interval: the pixel-size problem, one axis over ─────────────────────────────

@pytest.mark.core
def test_the_frame_interval_is_NaN_when_the_file_does_not_carry_one():
    """**``frame_interval_s = 1.0`` is a claim that the microscope ran at 1 fps.**

    It is not an absence of information. **51 functions default it**, and it is silently wrong on
    almost every real acquisition.

    This has already cost real time: **VPT's viscosity read ~0.094 Pa·s against an expected ~7**,
    and one of the two root causes was exactly this — *the frame interval defaulted while the real
    MicroManager metadata said 0.5 s/frame.* **A 5× error in the time axis is a 5× error in every
    diffusion coefficient**, and nothing about the output looks wrong.

    *A NaN diffusion coefficient is visibly wrong; a 5× overestimate is not.*
    """
    frame_interval = pytest.importorskip("pycat.utils.frame_interval")

    real = frame_interval.frame_interval_s(
        {'file_metadata': {'common': {'frame_interval_s': 0.5}}})
    assert real == pytest.approx(0.5), f"a real interval must pass through; got {real}"

    for repository in ({}, {'file_metadata': {'common': {}}},
                       {'file_metadata': {'common': {'frame_interval_s': 'x'}}}):
        value = frame_interval.frame_interval_s(repository)
        assert not np.isfinite(value), (
            f"a missing or unreadable frame interval must be NaN, not a plausible 1.0 — "
            f"got {value} from {repository}"
        )


@pytest.mark.core
def test_the_metadata_sync_NEVER_overrides_the_users_own_value():
    """**A sync that stomps a deliberate choice is worse than no sync at all.**

    The user changed it *because they knew something the file did not.* VPT's implementation gets
    this right, and it is the rule this helper preserves.
    """
    frame_interval = pytest.importorskip("pycat.utils.frame_interval")

    class _Spin:
        def __init__(self, value=1.0):
            self.value = value

        def blockSignals(self, _):
            pass

        def setValue(self, v):
            self.value = v

    class _Owner:
        touched = False

    repository = {'file_metadata': {'common': {'frame_interval_s': 0.5}}}

    # Untouched: the file wins.
    owner, spin = _Owner(), _Spin()
    assert frame_interval.sync_spinbox_from_metadata(
        spin, repository, touched_flag='touched', owner=owner)
    assert spin.value == pytest.approx(0.5)

    # The user chose 0.25: THEIR value wins.
    owner.touched = True
    spin = _Spin(0.25)
    assert not frame_interval.sync_spinbox_from_metadata(
        spin, repository, touched_flag='touched', owner=owner)
    assert spin.value == pytest.approx(0.25), (
        "the metadata sync overrode a value the user deliberately set. They changed it BECAUSE "
        "they knew something the file did not."
    )


@pytest.mark.core
def test_every_UI_with_a_frame_interval_SYNCS_it_from_the_file():
    """**Three UIs read the file. Seven took a spinbox default and reported it as physics.**

    ``metadata_extract`` captures the true interval at load. VPT reads it. The rest did not.
    """
    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    import re

    missing = []
    for path in sorted(toolbox.glob("*_ui.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')

        # Does this panel HAVE a frame-interval spinbox?
        has_interval = bool(re.search(r'\b\w*(?:dt|interval)\w*\s*=\s*QDoubleSpinBox\(\)',
                                      source, re.I))
        if not has_interval:
            continue

        syncs = ('sync_spinbox_from_metadata' in source
                 or '_sync_frame_interval_from_metadata' in source
                 or 'frame_interval_s' in source)

        if not syncs:
            missing.append(path.name)

    assert not missing, (
        f"these UIs have a frame-interval field and never read it from the file: {missing}\n\n"
        f"A 1.0 s default is a CLAIM that the microscope ran at 1 fps. Every time-dependent "
        f"result — a diffusion coefficient, an MSD exponent, a recovery half-time — scales with "
        f"it directly."
    )


@pytest.mark.core
def test_every_UI_that_treats_frames_as_TIME_warns_about_an_assumed_axis():
    """**A wrong axis label makes every RATE meaningless, and nothing about the number looks wrong.**

    An undeclared multipage TIFF carries no axis metadata, so the user labels it **T or Z at
    load**. **T and Z load identically** — a wrong label is completely harmless for viewing, and
    there is **nothing on screen to tell you it happened.**

    But a step that treats frames as **time** — an MSD, a diffusion coefficient, a coarsening rate,
    a recovery half-time — is computing a rate **per frame**. If those frames are actually
    **Z-slices**, *the rate is a fiction.*

    ``warn_if_assumed_axis`` exists for exactly this, and it is a **safe no-op when the axis was
    declared in the metadata** — it only speaks when the label really was a guess.

    **Ten UIs run a time-dependent analysis. Four warned.** Five of the other six compute an
    **MSD**.
    """
    import re

    toolbox = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"

    # The calls that treat a frame index as a TIME index.
    time_dependent = ('compute_msd', 'msd_per_track', 'coarsening_statistics',
                      'growth_shrinkage_kinetics', 'aspect_ratio_signal',
                      'field_trajectories', 'fit_photobleaching')

    missing = []
    for path in sorted(toolbox.glob("*_ui.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')

        # Does this panel actually RUN one? (An import alone does not count.)
        runs_one = any(re.search(rf'\b{call}\s*\(\s*[^)\s]', source)
                       for call in time_dependent)
        if not runs_one:
            continue

        if 'warn_if_assumed_axis' not in source:
            missing.append(path.name)

    assert not missing, (
        f"these UIs compute a RATE from frame indices and never warn that the axis may have been "
        f"assumed: {missing}\n\n"
        f"T and Z load identically. If the user labelled a z-stack as a time-series, every "
        f"diffusion coefficient, coarsening rate and recovery half-time from this panel is a "
        f"fiction — **and nothing about the number looks wrong.**"
    )
