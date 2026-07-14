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
def test_NO_lazy_wrapper_ANYWHERE_materialises_itself():
    """**An implicit full-stack read is never what the caller meant.**

    ── 1.6.3 fixed THREE of NINE, and the guard said it was done ────────────────

    There are **nine** lazy wrappers. Three live in ``multidim_io``; **six live in ``file_io``** —
    *including all three IMS wrappers.* 1.6.3 fixed the three, and **this guard only looked at
    ``multidim_io``**, so it passed while six identical landmines sat untouched.

    **And the IMS ones are the ones that lag.** PyCAT's own source has said so for months::

        "napari auto-estimates contrast (and builds the thumbnail) by calling np.asarray() on
         the layer — which for a lazy (T,Y,X) wrapper triggers __array__ and loads EVERY frame
         from disk. On a USB-HDD IMS stack that is the real cause of the multi-second stalls."

    ***A guard whose scope is narrower than the bug will certify the half that was fixed.*** It now
    walks **every** module in ``file_io``.
    """
    offenders = []
    refusing = 0

    for path in sorted((_SOURCE / "file_io").glob("*.py")):
        source = path.read_text(encoding='utf-8', errors='ignore')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != '__array__':
                continue

            body = ast.get_source_segment(source, node) or ''

            # It must refuse — either by raising directly, or through the shared guard.
            refuses = ('refuse_implicit_full_read' in body
                       or any(isinstance(inner, ast.Raise) for inner in ast.walk(node)))
            materialises = 'np.stack' in body or 'np.asarray(self._z)' in body

            if materialises or not refuses:
                offenders.append(f"{path.name}:{node.lineno}")
            else:
                refusing += 1

    assert not offenders, (
        "these `__array__` methods still materialise the full stack:\n  " + "\n  ".join(offenders)
        + "\n\n**Every frame comes off disk** the moment anything calls `np.asarray()` on the "
          "layer — a thumbnail, a contrast estimate, a plugin, a layer-list refresh. "
          "Use `pycat.file_io.lazy_guard.refuse_implicit_full_read(self)`."
    )
    assert refusing >= 9, (
        f"only {refusing} `__array__` methods refuse — there are 9 lazy wrappers "
        f"(6 in file_io, 3 in multidim_io). Has one been added without a refusal?"
    )


@pytest.mark.core
def test_EVERY_lazy_layer_pins_its_CONTRAST_LIMITS():
    """**Pinning the limits is what stops napari calling ``__array__`` at all.**

    Without explicit ``contrast_limits``, napari auto-estimates contrast **and builds the
    thumbnail** by calling ``np.asarray()`` on the layer. On a lazy wrapper that reads the entire
    acquisition off disk, one frame at a time.

    The IMS branches pinned them. **Three TIFF/CZI branches did not.**

    *(``__array__`` now raises rather than materialising — so an unpinned layer fails loudly
    instead of hanging. That is the right trade, but pinning is what stops it happening.)*
    """
    source = (_SOURCE / "file_io" / "file_io.py").read_text(encoding='utf-8', errors='ignore')
    lines = source.split('\n')

    unpinned = []
    for number, line in enumerate(lines, 1):
        if 'add_image(' not in line:
            continue
        if not any(name in line for name in ('wrapper', 'lazy_')):
            continue

        # The limits are computed just above the call, and may be passed via **kwargs.
        window = '\n'.join(lines[max(0, number - 16):number])
        if 'contrast_limits' not in window and '_lazy_contrast_limits' not in window:
            unpinned.append(f"file_io.py:{number}")

    assert not unpinned, (
        "these lazy layers are added WITHOUT explicit contrast limits:\n  "
        + "\n  ".join(unpinned)
        + "\n\nnapari will call `np.asarray()` on them to estimate contrast — which reads every "
          "frame off disk. Compute the limits from ONE frame with `_lazy_contrast_limits(wrapper)`."
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
