"""
**Comments that name the wrong library are a trap, not a cosmetic issue.**

After 1.6.0, ``file_io.py`` still had:

* **9 references to ``use_aicsimage``** — a flag that ***never meant "is it aicsimageio?"***. It
  meant *"did the structured reader give us dimensions, scenes and channel metadata, or are we
  falling back to reading raw pages?"* **The name described the implementation rather than the
  question**, and it went stale the moment BioIO replaced aicsimageio.
* **15 comments describing current behaviour in terms of ``AICSImage``** — *"opened via AICSImage"*,
  *"AICSImage's dask reader"*, *"skip the AICSImage path"*.

**None of that is true any more**, and a reader who trusts it will look in the wrong place. The
audit flagged it, and it is the sort of thing that gets waved through as "just comments" — right up
until someone debugs against them.

*(The reader is now named by what it **does** — ``reader_has_structure``, "the structured reader",
"the reader seam" — which stays true whichever library is underneath. That is the point.)*
"""

import pathlib
import re

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


@pytest.mark.core
def test_no_flag_is_named_after_a_LIBRARY_that_is_no_longer_used():
    """``use_aicsimage`` described the **implementation**, not the **question** it answered."""
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')

        for match in re.finditer(r'\buse_aicsimage\b', source):
            line = source[:match.start()].count('\n') + 1
            offenders.append(f"{path.relative_to(_SOURCE)}:{line}")

    assert not offenders, (
        "`use_aicsimage` is back:\n  " + "\n  ".join(offenders)
        + "\n\nIt never meant 'is it aicsimageio?'. It meant 'did the structured reader give us "
          "dimensions and scenes, or are we reading raw pages?'. Name it for the question."
    )


@pytest.mark.core
def test_comments_do_not_describe_CURRENT_behaviour_using_the_OLD_reader():
    """**A reader who trusts a stale comment looks in the wrong place.**

    Historical notes are fine — *"the aicsimageio import was removed"* is true and worth keeping.
    What is not fine is a comment saying the code **does** something it no longer does.
    """
    source_path = _SOURCE / "file_io" / "file_io.py"
    source = source_path.read_text(encoding='utf-8', errors='ignore')

    # Phrases that assert PRESENT behaviour. A historical note reads differently, and the one
    # remaining mention — "AST walk confirms AICSImage is referenced nowhere" — is exactly that.
    present_tense = (
        r'opened via AICSImage',
        r'using AICSImage',
        r'the AICSImage path',
        r"AICSImage's (?:dask|physical_pixel_sizes|\.scenes)",
        r'via AICSImage',
    )

    offenders = []
    for pattern in present_tense:
        for match in re.finditer(pattern, source):
            line = source[:match.start()].count('\n') + 1
            offenders.append(f"file_io.py:{line}  {match.group(0)}")

    assert not offenders, (
        "these comments describe what the code does using the OLD reader's name:\n  "
        + "\n  ".join(offenders)
        + "\n\nBioIO replaced aicsimageio in 1.6.0. Name the reader by what it DOES — 'the "
          "structured reader', 'the reader seam' — which stays true whichever library is under it."
    )


@pytest.mark.core
def test_the_MISNAMED_zarr_wrapper_is_gone():
    """``_ZarrTYX_generic`` **was not zarr-specific.**

    It received **zarr arrays, numpy arrays, and BioIO dask arrays** — and the name told every
    reader it could rely on zarr semantics it does not have.

    *Worse: the TZYX branch transcoded the entire file into a temporary zarr before showing
    anything, **purely so it would have a zarr to wrap.** The dask array was already lazy.*

    ``_LazyArraySource`` wraps whatever it is given, and was verified to behave **identically** on
    every indexing pattern napari uses on a (T, Y, X) layer.
    """
    import ast

    source = (_SOURCE / "file_io" / "file_io.py").read_text(encoding='utf-8', errors='ignore')
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.name != '_ZarrTYX_generic', (
                f"line {node.lineno}: `_ZarrTYX_generic` is back. It is not zarr-specific — it "
                f"receives zarr, numpy AND dask arrays. Use `_LazyArraySource`."
            )
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == '_ZarrTYX_generic':
            assert False, f"line {node.lineno}: still constructing `_ZarrTYX_generic`"
