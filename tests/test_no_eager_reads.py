"""
**``get_image_data()`` loads the ENTIRE scene into memory.**

This is not a subtlety. **Both libraries document it in the same words:**

    *"The ``.get_image_data`` function will **load the whole scene into memory** and then retrieve
    the specified chunk."*
    — BioIO's docs, and aicsimageio's before them

``get_image_dask_data()`` is the lazy one: *"will not load any piece of the imaging data into
memory until you specifically call ``.compute()``."*

**PyCAT was calling the eager one in EIGHT places in the loading path** — including to read a
*single plane* in order to *classify* a file. On a large 4-D acquisition that pulls the entire scene
into memory **to look at one frame**, and it can happen **more than once per file**, because the
reader is constructed several times before anything is displayed.

***That is the freeze.***

This is NOT a BioIO regression, and the distinction matters
-----------------------------------------------------------
**aicsimageio documented the same eager semantics.** The calls were wrong in 1.5.x too.

What the migration did was **expose** them: ``bioio-czi`` uses a different backend
(``pylibczirw`` rather than ``aicspylibczi``), and a different TIFF reader — **the same mistake can
cost very differently.**

*Chasing "what did BioIO break?" would have been chasing a phantom. The loader was always eager
here.*

And ``__array__`` was the same landmine, wearing a different hat
----------------------------------------------------------------
``np.asarray(layer.data)`` on a lazy stack has already cost this project **two bugs** — N&B told
users their movie was 2-D, and SpIDA silently analysed frame 0 while they looked at frame 40. The
fix there was ``materialize_stack()``: **an explicit, named, deliberate full read.**

**But ``_ZarrTZYX.__array__`` did the opposite.** It quietly stacked *every frame* — so any
thumbnail, plugin, layer refresh, contrast estimate, or stray numpy operation could pull an entire
acquisition into memory **without anyone asking, and without anything saying so.**

*A comment claimed pinned contrast limits stop napari calling it. **That is a hope, not a
guarantee.***
"""

import ast
import pathlib

import pytest


_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "pycat"


@pytest.mark.core
def test_the_LOADER_never_calls_the_EAGER_api():
    """**One reader means the ban can be enforced.**

    Eight call sites, each free to reach for the eager API again — that is not a thing a code
    review catches reliably. ``read_plane()`` is the only way in, and this is the fence.
    """
    offenders = []

    for path in sorted(_SOURCE.rglob("*.py")):
        # `image_reader.py` is where the reader lives. `compare_readers` legitimately calls the
        # eager API — it is comparing two libraries on ONE plane, deliberately.
        if path.name == 'image_reader.py':
            continue

        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, 'attr', None) == 'get_image_data':
                offenders.append(f"{path.relative_to(_SOURCE)}:{node.lineno}")

    assert not offenders, (
        "these sites call the EAGER `get_image_data()`:\n  " + "\n  ".join(offenders)
        + "\n\n**It loads the WHOLE SCENE into memory** — both libraries document this in the same "
          "words — and then retrieves the chunk you asked for.\n\n"
          "Use `pycat.file_io.image_reader.read_plane(image, t=, c=, z=)`, which goes through "
          "`get_image_dask_data()` and computes exactly one plane."
    )


@pytest.mark.core
def test_no_LAZY_WRAPPER_materialises_itself_on_np_asarray():
    """**An implicit full-stack read is never what the caller meant.**

    ``__array__`` is called by *anything* that treats the layer as an array — a thumbnail, a
    plugin, a contrast estimate. **If it stacks every frame, an entire acquisition comes into
    memory with nothing to show for it and nothing saying so.**
    """
    multidim = _SOURCE / "file_io" / "multidim_io.py"
    source = multidim.read_text(encoding='utf-8', errors='ignore')
    tree = ast.parse(source)

    eager = []
    refusing = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != '__array__':
            continue

        body = ast.get_source_segment(source, node) or ''

        # It must RAISE, and it must not stack.
        raises = any(isinstance(inner, ast.Raise) for inner in ast.walk(node))
        stacks = 'np.stack' in body

        if stacks or not raises:
            eager.append(f"multidim_io.py:{node.lineno}")
        else:
            refusing += 1

    assert not eager, (
        "these `__array__` methods still materialise the full stack:\n  " + "\n  ".join(eager)
        + "\n\nThey must RAISE. `materialize_stack()` remains available for a full read that "
          "someone actually meant."
    )
    assert refusing >= 3, (
        f"only {refusing} `__array__` methods refuse — there were 3. Has one been removed?"
    )


@pytest.mark.core
def test_read_plane_exists_and_uses_the_LAZY_api():
    """The single way in. If it reaches for the eager API, the fence is decorative."""
    reader = (_SOURCE / "file_io" / "image_reader.py").read_text(encoding='utf-8', errors='ignore')

    assert 'def read_plane' in reader, "there is no canonical plane reader"

    tree = ast.parse(reader)
    plane_reader = next((n for n in ast.walk(tree)
                         if isinstance(n, ast.FunctionDef) and n.name == 'read_plane'), None)
    assert plane_reader is not None

    # ── Check the CODE, not the prose ────────────────────────────────────────────
    #
    # A first version searched `read_plane`'s source text for `get_image_data(` — and **flagged its
    # own docstring**, which quotes the eager API in order to explain why it must not be used.
    #
    # *That is the third time this session a guard has checked a comment.* The lesson keeps
    # arriving: **a guard that cannot tell code from prose will eventually flag its own
    # explanation, and the fix is not to stop explaining.**
    calls = [getattr(node.func, 'attr', None)
             for node in ast.walk(plane_reader) if isinstance(node, ast.Call)]

    assert 'get_image_dask_data' in calls, (
        "`read_plane` must call the LAZY api — that is its entire purpose"
    )
    assert 'get_image_data' not in calls, (
        "`read_plane` CALLS the eager api. It is the one function that exists to avoid it."
    )
