"""
Guard: the scientific kernels must stay importable WITHOUT a GUI stack.

This test exists because the coupling it guards against is easy to re-introduce with a
single convenient line — ``from napari.utils.notifications import show_warning`` at the
top of a module — and the consequences are invisible until someone tries to run the
tests.

The coupling is **transitive**. One GUI import at the base of the import graph blocks
every module above it: ``feature_analysis_tools`` was un-importable not because of its
own imports but because of ``image_processing_tools``, three levels down. Before this
was fixed, **four of six scientific test modules could not even be COLLECTED** without
napari and PyQt installed — they failed at import time, before a single assertion ran.

That is backwards. The numerical code — a viscosity calculation, an MSD fit, a
colocalization coefficient, a FRAP mobile fraction — is the part that most needs
automated regression testing, and it should be verifiable in a plain ``pytest`` run with
no display, no Qt, and no napari.

If this test fails, the fix is NOT to add napari to the test environment. It is to move
the GUI import out of the scientific module:

* notifications  -> ``from pycat.utils.notify import show_info, show_warning``
  (forwards to napari when a UI is present, prints otherwise)
* ``napari.layers.X`` isinstance checks -> import napari inside the function that needs
  it, which by definition only runs when a viewer exists
* Qt widgets / ``pycat.ui.*`` helpers -> import at call time inside the viewer-facing
  function
"""

import ast
import pathlib

import pytest

# Scientific modules whose numerical content must be importable with no GUI.
# (UI modules — *_ui.py — are exempt: presenting a widget IS their job.)
SCIENTIFIC_MODULES = [
    "condensate_physics_tools",
    "feature_analysis_tools",
    "frap_tools",
    "image_processing_tools",
    "invitro_tools",
    "label_and_mask_tools",
    "nb_tools",
    "partial_volume_tools",
    "partition_enrichment_tools",
    "pixel_wise_corr_analysis_tools",
    "segmentation_scale_advisor",
    "spida_tools",
    "vpt_tools",
    # Newly decoupled (1.5.438). `obj_based_coloc_analysis_tools` holds 12 pure analysis
    # functions — Manders' M1/M2, object overlap, per-object colocalisation — that were
    # locked behind a module-scope Qt import for a single dialog, so CI could never see
    # them. `correlation_func_analysis_tools` and `layer_tools` pulled in `pycat.ui.ui_utils`
    # (which imports napari) for one function each.
    "obj_based_coloc_analysis_tools",
    # 1.5.439: 16 pure analysis functions — the puncta refinement filter, local thresholding,
    # the SNR/contrast gates, watershed splitting — were locked behind a module-scope napari
    # import for a handful of viewer functions. The puncta filter had NEVER been tested (see
    # tests/test_puncta_refinement.py); its SNR gate was found completely dead in 1.5.416.
    "segmentation_tools",
    "correlation_func_analysis_tools",
    "layer_tools",
    "clean_spot_detection_tools",
    "fd_curve_tools",
    "fft_bandpass_tools",
    "intensity_profile_tools",
]

_FORBIDDEN_ROOTS = {"napari", "PyQt5", "PyQt6", "qtpy"}

_TOOLBOX = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "toolbox"


def _module_level_gui_imports(path: pathlib.Path):
    """Return the GUI imports made at MODULE scope (i.e. at import time).

    Imports inside a function body are fine — those run only when a viewer already
    exists. It is the top-level ones that break headless import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    bad = []
    for node in tree.body:                       # module scope only, deliberately
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _FORBIDDEN_ROOTS:
                    bad.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _FORBIDDEN_ROOTS:
                bad.append(node.module)
        # `try: import PyQt5 ... except ImportError:` is an ACCEPTED pattern: the
        # module still imports without Qt, it just disables the dialog. Skip Try
        # bodies rather than flagging them.
    return bad


@pytest.mark.core
@pytest.mark.parametrize("mod", SCIENTIFIC_MODULES)
def test_no_module_level_gui_import(mod):
    """A scientific module must not import napari or Qt at module scope."""
    path = _TOOLBOX / f"{mod}.py"
    if not path.exists():
        pytest.skip(f"{mod} not present")
    bad = _module_level_gui_imports(path)
    assert not bad, (
        f"{mod}.py imports a GUI stack at module scope: {bad}.\n"
        f"This makes the module — and every module that imports it — un-importable "
        f"without a display, so its science cannot be tested headlessly.\n"
        f"Use pycat.utils.notify for notifications, and import napari/Qt inside the "
        f"viewer-facing functions that actually need them."
    )


# ─────────────────── the module must ACTUALLY import, not just look clean ───────────────
#
# The GUI check above is STATIC: it parses the source and asserts that no napari/Qt import
# sits at module scope. That is necessary but not sufficient, and the gap was real.
#
# The `core` workflow originally installed only numpy/scipy/scikit-image/pandas — a
# plausible-looking "the scientific deps" list written from memory. Four of the thirteen
# guarded modules could not import at all in that environment:
#
#     image_processing_tools          -> pywavelets, SimpleITK
#     feature_analysis_tools          -> cv2
#     label_and_mask_tools            -> cv2
#     pixel_wise_corr_analysis_tools  -> matplotlib
#
# The static guard passed (none of those are napari or Qt), the workflow went red, and the
# failure taught nothing — a CI that is red for an uninteresting reason is a CI people
# learn to ignore.
#
# So: actually import each module. This runs in the CI environment, so if the workflow's
# dependency list is missing something a science module genuinely needs, THIS test says so
# — pointing at the real problem instead of failing somewhere downstream.

@pytest.mark.core
@pytest.mark.parametrize("mod", SCIENTIFIC_MODULES)
def test_module_actually_imports(mod):
    """Each guarded module must import in the headless CI environment."""
    import importlib

    try:
        importlib.import_module(f"pycat.toolbox.{mod}")
    except ImportError as exc:
        pytest.fail(
            f"pycat.toolbox.{mod} cannot be imported in the headless environment: "
            f"{exc}\n\n"
            f"If the missing package is a GUI dependency (napari, PyQt), the fix is to "
            f"move the import inside the function that needs it — see the notify shim "
            f"(pycat.utils.notify) and the lazy-accessor pattern already used in "
            f"label_and_mask_tools.\n\n"
            f"If it is a COMPUTE dependency (cv2, pywavelets, SimpleITK, matplotlib, "
            f"scikit-learn), the fix is the opposite: add it to the install step in "
            f".github/workflows/core.yml. The headless job excludes the GUI stack on "
            f"purpose; it is not supposed to exclude the maths."
        )
