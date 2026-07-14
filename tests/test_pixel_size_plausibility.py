"""
**A file can carry a scale that is a lie, and PyCAT was believing it.**

Gable exported a substack from ImageJ. The resulting TIFF carries::

    XResolution    = 2147054150 / 4999   ->  429,496.7 pixels per unit
    ResolutionUnit = 1                   ->  "no absolute unit"
    ImageJ unit    = micron

That decodes to **2.3 picometres per pixel** — *four hundred times smaller than a hydrogen atom.*

And ``2147054150`` is a hair under **2³¹ = 2147483648**: a **signed-integer overflow** in ImageJ's
Substack export. **This is a known artefact, and Gable will not be the last person to hit it.**

The gate did not fire — and it was RIGHT not to
------------------------------------------------
The old check asked *"is there a number?"* **There was one.** It was not ``None``, and it was not
the ``1.0`` sentinel, so PyCAT concluded the file carried a real scale, set
``pixel_size_from_metadata = True``, and **hid the prompt.**

***It was doing exactly what it was told. The file was lying.***

And every length, area and diffusion coefficient downstream was then computed from a fabricated
number — **silently**, because nothing looked wrong.

The bounds come from OPTICS, not from taste
--------------------------------------------
**Lower — 0.001 µm/px (1 nm).** Abbe puts the best real resolution at ``λ/(2·NA) = 400/(2×1.49) ≈
134 nm``, and Nyquist wants 2–3 samples across it, so the smallest *sensible* pixel is ~40–65 nm.
A super-resolution reconstruction goes finer — an aggressive SMLM render might be 5 nm/px.
**1 nm is a 1000× margin below even that.**

**Upper — 1000 µm/px (1 mm).** A 4× objective is ~1.6 µm/px; a slide scanner ~20 µm/px; a
photograph of a gel might be 100 µm/px. **1 mm per pixel is not a micrograph.**

***Both bounds are deliberately loose.*** A bound this wide can only catch **garbage**, never real
data — which is the point. *A plausibility check that rejects a real acquisition is worse than none,
because it would train the user to click through it.*
"""

import pytest


@pytest.mark.core
def test_the_IMAGEJ_SUBSTACK_OVERFLOW_is_rejected():
    """**The file that prompted this.** 2.3 picometres per pixel."""
    sizes = pytest.importorskip("pycat.utils.pixel_size")

    # The tag, decoded exactly as tifffile reads it.
    pixel_size = 1.0 / (2147054150 / 4999)

    assert pixel_size == pytest.approx(2.328e-06, rel=1e-3), (
        "the decoding changed — re-derive before trusting this test"
    )

    assert not sizes.is_physically_plausible(pixel_size), (
        f"a pixel size of {pixel_size:.3e} µm (2.3 PICOMETRES) was accepted as physically "
        f"possible. No microscope can resolve that."
    )

    reason = sizes.implausible_reason(pixel_size)
    assert reason and 'nm per pixel' in reason, (
        "the reason must be stated in the units a microscopist thinks in"
    )
    assert 'corrupt' in reason.lower(), (
        "the user must be told the FILE is wrong, not that PyCAT is confused"
    )


@pytest.mark.core
@pytest.mark.parametrize("instrument,pixel_size_um", [
    ("SMLM render (aggressive)", 0.005),
    ("STED", 0.02),
    ("Zeiss confocal 63x/1.4", 0.0264),
    ("the bead data, 100x", 0.067),
    ("spinning disk 60x", 0.108),
    ("widefield 20x", 0.325),
    ("10x air", 0.65),
    ("4x", 1.6),
    ("slide scanner", 20.0),
    ("a photograph of a gel", 100.0),
])
def test_EVERY_REAL_INSTRUMENT_passes(instrument, pixel_size_um):
    """***A plausibility check that rejects real data is worse than none*** — it would train the
    user to click through it, and then it would not be believed on the day it matters.

    Every instrument in the lab, and then some.
    """
    sizes = pytest.importorskip("pycat.utils.pixel_size")

    assert sizes.is_physically_plausible(pixel_size_um), (
        f"{instrument} ({pixel_size_um} µm/px) was rejected as implausible. **The bounds are meant "
        f"to catch a corrupt metadata tag, not to second-guess a real acquisition.**"
    )
    assert sizes.implausible_reason(pixel_size_um) is None


@pytest.mark.core
@pytest.mark.parametrize("value", [0.0, -1.0, float('nan'), None, 'not a number', 1e9])
def test_a_NONSENSE_value_is_refused(value):
    """Zero, negative, NaN, absent, non-numeric, and a kilometre per pixel."""
    sizes = pytest.importorskip("pycat.utils.pixel_size")

    assert not sizes.is_physically_plausible(value)
    assert sizes.implausible_reason(value) is not None


@pytest.mark.core
def test_the_LOADER_rejects_an_implausible_scale_so_the_GATE_FIRES():
    """**A check nothing calls is a check that does nothing.**

    The whole failure was that the gate stayed hidden. So the loader must set
    ``pixel_size_from_metadata = False`` on a corrupt tag — which is what makes the gate appear.
    """
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "data"
              / "data_modules.py").read_text(encoding='utf-8', errors='ignore')

    assert 'is_physically_plausible' in source, (
        "the loader does not check whether the file's pixel size is physically possible"
    )
    assert 'implausible_reason' in source, (
        "the loader must tell the user WHY — 'resolution data incomplete' would be a lie of its "
        "own, because the data is not incomplete, it is WRONG"
    )


def _implausible_branch_body():
    """**The body of the `elif _implausible is not None:` branch — and nothing else.**

    ── A character window CANNOT do this, and a first version tried ─────────────

    The first version of these tests read ``source[marker:marker + 900]`` and asserted that
    ``pixel_size_from_metadata = False`` appeared in it.

    **It passed on deliberately regressed code.** The 900-character window **spilled past the
    ``elif`` into the NEXT branch** — the missing-tag one — which *also* sets the flag ``False``. So
    the assertion was satisfied by the wrong branch, and the guard was **blind to the exact bug it
    was written for.**

    ***A guard with no power is worse than no guard: it certifies the damage.***

    The AST knows where a branch ends. A regex does not.
    """
    import ast
    import pathlib as _pathlib

    source = (_pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat" / "data"
              / "data_modules.py").read_text(encoding='utf-8', errors='ignore')
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        test = ast.get_source_segment(source, node.test) or ''
        if '_implausible is not None' not in test:
            continue

        # ONLY this branch's body. Not its neighbours.
        return '\n'.join(ast.get_source_segment(source, statement) or ''
                         for statement in node.body)

    return None


@pytest.mark.core
def test_a_CORRUPT_TAG_prompts_for_a_scale_and_does_NOT_block_the_load():
    """**Gable's question, and it is the right one:** *"if the gate fires and an invalid pixel size
    is present, is it just a warning as part of the pixel-size gate widget, and does it still prompt
    you to enter a pixel scale?"*

    **Yes — and it must stay that way.** A corrupt resolution tag is not a reason to refuse the
    image. The user still gets:

    1. **the image**, loaded normally
    2. **a warning** saying the file's scale is not physically possible, *and why*
    3. **the gate**, prompting for the correct scale — exactly as for a file with **no** tag at all

    ***A file with a corrupt tag and a file with no tag end up in the same place.*** The only
    difference is that the warning is honest about which one it was.
    """
    branch = _implausible_branch_body()

    assert branch is not None, (
        "there is no `elif _implausible is not None:` branch — the plausibility check is computed "
        "and then thrown away"
    )

    # 1. It must NOT raise. A corrupt tag is not a reason to refuse the image.
    assert 'raise' not in branch, (
        "the loader RAISES on a corrupt pixel size. **That would refuse to open the image.** A "
        "corrupt resolution tag is a metadata problem, not a broken file."
    )

    # 2. It must WARN, and say why.
    assert 'napari_show_warning' in branch, (
        "the user is never told that the file's scale is impossible"
    )

    # 3. **It must set the flag that makes the gate APPEAR — and must NOT set it True.**
    #
    # This is the whole fix, and it is where the first version of this test was blind: it checked a
    # character window that bled into the neighbouring branch.
    assert "'pixel_size_from_metadata'] = False" in branch, (
        "the corrupt-tag branch does not set `pixel_size_from_metadata = False`. **The gate hides "
        "itself when that flag is True** — so without this, the gate stays hidden and PyCAT "
        "silently computes every length, area and diffusion coefficient from a fabricated pixel "
        "size."
    )
    assert "'pixel_size_from_metadata'] = True" not in branch, (
        "the corrupt-tag branch sets `pixel_size_from_metadata = True`. **That hides the gate**, "
        "and PyCAT then trusts a scale the file could not possibly have measured."
    )


@pytest.mark.core
def test_the_gate_treats_a_CORRUPT_tag_exactly_like_a_MISSING_one():
    """**The two paths must converge**, or a corrupt tag becomes a special case that drifts.

    A file with no resolution tag and a file with a *garbage* one are, for PyCAT's purposes, the
    same situation: **the file cannot tell you the scale, so the user must.**
    """
    branch = _implausible_branch_body()
    assert branch is not None

    for required in ("'microns_per_pixel_sq'] = 1", "'pixel_size_from_metadata'] = False"):
        assert required in branch, (
            f"the corrupt-tag branch does not set `{required}` — it must end up in exactly the "
            f"same state as a file with no tag at all, or the two paths will drift apart"
        )
