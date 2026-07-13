"""
**One seam between PyCAT and whichever library reads the file.**

``aicsimageio`` is in maintenance mode; its maintainers name ``bioio`` as the *compatible
successor*. The compatibility is real — ``BioImage`` exposes the same names, the same semantics and
the same **TCZYX** ordering — so the substitution itself is small.

**What is not small is the risk of doing it in one irreversible step.** This project has already
been bitten twice by a change that looked safe and could not be A/B-ed:

* the **rolling-ball normalisation** that made batch disagree with the recording
* the **frame-zero collapse** that told users their movie was a still image

**Both were invisible until someone compared two runs.** So the swap ships behind a switch, both
libraries stay installable, and ``compare_readers()`` is the acceptance test — *run on real files,
not synthetic ones.*
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


@pytest.mark.core
def test_NOTHING_constructs_a_reader_directly():
    """**The seam is only a seam if everything goes through it.**

    A single ``AICSImage(path)`` left behind means one code path that cannot be switched, cannot be
    compared, and will still be there when the library is finally removed.
    """
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        if path.name == 'image_reader.py':
            continue        # the seam itself is where the construction belongs

        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
            if name in ('AICSImage', 'BioImage'):
                offenders.append(f"{path.name}:{node.lineno} constructs {name}() directly")

    assert not offenders, (
        "these sites construct an image reader directly instead of going through "
        "`pycat.file_io.image_reader.open_image`:\n  " + "\n  ".join(offenders)
        + "\n\nA site that bypasses the seam cannot be switched to bioio, cannot be A/B compared, "
          "and will still be here when aicsimageio is removed."
    )


@pytest.mark.core
def test_the_DEFAULT_backend_is_still_the_incumbent():
    """**Flipping a default is a decision to make with evidence, not with a passing import.**

    The BioIO path stays opt-in until ``compare_readers()`` has been run on real CZI, OME-TIFF and
    Micro-Manager data — *the formats that have actually exposed loader bugs in this project.*
    """
    reader = pytest.importorskip("pycat.file_io.image_reader")

    assert reader._DEFAULT_BACKEND == 'aicsimageio', (
        "the default reader has been flipped to bioio. That is the right destination — but only "
        "after `compare_readers()` shows identical pixels, dimension order and pixel size on "
        "Gable's real files."
    )


@pytest.mark.core
def test_a_MISSING_BIOIO_PLUGIN_says_so_rather_than_blaming_the_file():
    """**BioIO's readers are separate packages** — that is the improvement, and the trap.

    A user who opens a CZI without ``bioio-czi`` installed must be told *"install bioio-czi"*, not
    *"cannot read file"* — which would send them looking at their microscope.
    """
    reader_module = (_SOURCE / "file_io" / "image_reader.py").read_text(
        encoding='utf-8', errors='ignore')

    assert 'bioio-czi' in reader_module, "the missing-plugin error must name the package to install"
    assert 'MISSING PLUGIN, not a corrupt file' in reader_module, (
        "a missing reader plugin must not be reported as a broken file"
    )


@pytest.mark.core
def test_compare_readers_checks_the_PIXELS_and_not_just_the_metadata():
    """**Shape, dtype and dimension order can all match while the data differs.**

    A byte-order bug, an off-by-one at a chunk boundary, a scene selected differently — none of
    those show up in the metadata. **The only claim worth making is that the pixels are identical.**
    """
    reader_module = (_SOURCE / "file_io" / "image_reader.py").read_text(
        encoding='utf-8', errors='ignore')

    assert 'array_equal' in reader_module, (
        "compare_readers must compare the actual pixel data, not only the metadata"
    )

    # And the two differences that would corrupt science silently rather than crash.
    assert 'DIMENSION ORDER' in reader_module, (
        "a reader returning CTZYX instead of TCZYX would not crash — it would return the WRONG "
        "CHANNEL. The comparison must call that out explicitly."
    )
    assert 'PIXEL SIZE' in reader_module, (
        "every length, area and diffusion coefficient PyCAT reports depends on the physical pixel "
        "size. A disagreement between readers must be flagged loudly."
    )
