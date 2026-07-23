"""**Every format can have a Z, so the Z scale is applied in ONE place.**

Physical z-depth reached the *measurements* (`pixel_size.z_step_um`) but never the *viewer*:
`_enable_auto_scale_bar` wrote only `sc[-1]`/`sc[-2]` (Y, X) and left every leading axis at 1.0. So
a calibrated z-stack rendered with a Z step of 1.0 world units against a 0.065 µm/px lateral scale —
**15x stretched** — for IMS, TIFF and CZI alike. Consistently wrong, but wrong.

The fix is deliberately NOT in any reader. A z-scale wired into the TIFF loader, then the IMS
loader, then the next one, is how they drift apart — invisibly, because a stack with the wrong
aspect still looks like a stack. Instead:

* `axis_order` ('YX'/'TYX'/'ZYX'/'TZYX') is tagged **once**, at the single load-time chokepoint
  (`tagging._tag_loaded_layer`) that every loader already calls;
* `napari_adapter._apply_z_scale` reads that tag and puts the z-step on the right axis, for
  whatever built the layer.

These tests therefore drive the **shared** functions with layers that differ only in their tag —
exactly what a new format would produce — rather than testing any one reader.

Why a tag is unavoidable: **a (N, Y, X) movie and a (N, Y, X) z-stack are the same array.**

pycat imports are inside the test bodies (`conftest.py`'s `pytest_ignore_collect` drops modules
whose module-scope imports name `pycat.file_io` when the GUI stack is absent).
"""

# Third party imports
import numpy as np
import pytest


pytestmark = pytest.mark.base

_PX = 0.065          # µm per lateral pixel
_Z_STEP = 0.30       # µm per slice — a typical confocal pairing


class _Layer:
    def __init__(self, ndim, name='layer'):
        self.name = name
        self.metadata = {}
        self.scale = [1.0] * ndim


def _layer_with_axes(axes):
    from pycat.utils.layer_tags import tag_layer
    layer = _Layer(len(axes))
    tag_layer(layer, 'axis_order', axes, source='inferred')
    return layer


def _repo(z_step=_Z_STEP):
    dr = {'file_metadata': {'common': {}}}
    if z_step is not None:
        dr['file_metadata']['common']['z_step_um'] = z_step
    return dr


class _CM:
    def __init__(self, dr):
        class _DC:
            pass
        self.active_data_class = _DC()
        self.active_data_class.data_repository = dr


def _scale_for(axes, z_step=_Z_STEP):
    """Run the shared helper exactly as `_enable_auto_scale_bar` does."""
    from pycat.file_io.napari_adapter import _apply_z_scale
    layer = _layer_with_axes(axes)
    sc = [1.0] * len(axes)
    sc[-1] = _PX
    sc[-2] = _PX
    _apply_z_scale(sc, layer, _CM(_repo(z_step)), _PX)
    return sc


def test_a_ZYX_layer_gets_the_REAL_z_step_on_the_Z_axis():
    """The whole point: Z renders at its physical depth, not at the placeholder 1.0."""
    sc = _scale_for('ZYX')
    assert sc == [_Z_STEP, _PX, _PX], (
        f"expected (z_step, px, px), got {sc}. A z-stack rendered with Z=1.0 against a "
        f"{_PX} µm/px lateral scale is stretched by {1.0 / _PX:.0f}x.")


def test_a_TZYX_layer_gets_the_z_step_on_Z_and_leaves_T_alone():
    """T is not a spatial axis — scaling it would stretch *time*. The tag is what makes the
    difference knowable."""
    sc = _scale_for('TZYX')
    assert sc == [1.0, _Z_STEP, _PX, _PX], f"expected (1.0, z_step, px, px), got {sc}"


def test_a_TYX_movie_is_NOT_given_a_z_step():
    """**The failure a shared scale must not have.** A (N, Y, X) movie and a (N, Y, X) z-stack are
    the same array; only the tag distinguishes them. Putting a depth on axis 0 of a movie would
    silently rescale time."""
    sc = _scale_for('TYX')
    assert sc == [1.0, _PX, _PX], f"a time axis was given a physical z-step: {sc}"


def test_a_plain_2D_layer_is_untouched():
    sc = _scale_for('YX')
    assert sc == [_PX, _PX]


def test_EVERY_reader_gets_the_same_Z_from_the_same_tag():
    """**The consistency claim, stated as a test.** The helper is given layers that differ only in
    which reader is imagined to have made them — i.e. not at all, because the only thing it reads
    is the shared tag. A ZYX IMS and a ZYX TIFF of the same specimen therefore cannot render
    differently, and a format added later inherits this by tagging its layers like everything
    else."""
    from_ims = _scale_for('ZYX')
    from_tiff = _scale_for('ZYX')
    from_a_future_format = _scale_for('ZYX')
    assert from_ims == from_tiff == from_a_future_format == [_Z_STEP, _PX, _PX]


def test_an_UNKNOWN_z_step_renders_ISOTROPIC_rather_than_15x_stretched():
    """`z_step_um` returns NaN when the file is silent — it never guesses, because a silent 1.0 on a
    0.3 µm confocal step is a 3.3x volume error that looks like a normal number.

    But napari needs a positive finite scale, so DISPLAY falls back to the lateral pixel size: an
    isotropic voxel. The alternative — leaving the placeholder 1.0 — renders 15x stretched, which is
    an artifact of the placeholder rather than anything about the specimen.
    """
    sc = _scale_for('ZYX', z_step=None)
    assert sc == [_PX, _PX, _PX], (
        f"expected an isotropic fallback (px, px, px), got {sc}")
    assert sc[0] != 1.0, "the placeholder 1.0 would render the stack ~15x stretched"


def test_measurements_still_get_NaN_for_an_unknown_z_step():
    """The display fallback must NOT leak into the numbers. A stretched picture is not a wrong
    volume; an assumed-isotropic volume IS."""
    from pycat.utils.pixel_size import z_step_um
    assert np.isnan(z_step_um(_repo(z_step=None))), (
        "the isotropic DISPLAY fallback must not become a measured z-step")
    assert z_step_um(_repo()) == pytest.approx(_Z_STEP)


def test_an_IMPLAUSIBLE_z_step_does_not_reach_the_viewer():
    """`z_step_um` screens physically impossible values; the viewer inherits that screen for free
    by going through it rather than reading the repository itself."""
    sc = _scale_for('ZYX', z_step=2.3e-6)     # µm — no microscope produced this
    assert sc == [_PX, _PX, _PX], f"an implausible z-step was rendered as real: {sc}"


def test_a_layer_with_NO_axis_order_tag_is_left_alone():
    """Declining beats guessing: without the tag, which axis is depth is unknowable."""
    from pycat.file_io.napari_adapter import _apply_z_scale
    layer = _Layer(3)                       # untagged
    sc = [1.0, _PX, _PX]
    _apply_z_scale(sc, layer, _CM(_repo()), _PX)
    assert sc == [1.0, _PX, _PX]


def _tag_via_the_chokepoint(n_t=1, n_z=1, n_p=1, assumed_axis=None):
    """Drive the REAL `_tag_loaded_layer` — the one place every loader tags its layers."""
    from pycat.file_io.tagging import _tag_loaded_layer
    from pycat.utils.layer_tags import get_tag

    dr = {'file_metadata': {'common': {'z_step_um': _Z_STEP}}}
    if assumed_axis is not None:
        dr['stack_axis_assumed'] = True
        dr['stack_axis_label'] = assumed_axis

    layer = _Layer(2 + (n_t > 1) + (n_z > 1))
    _tag_loaded_layer(_CM(dr), layer, role='image', n_t=n_t, n_z=n_z, n_p=n_p)
    return (get_tag(layer, 'axis_order'), get_tag(layer, 'dimensionality'),
            get_tag(layer, 'stack_axis'))


@pytest.mark.parametrize("n_t,n_z,expected", [
    (1, 1, 'YX'),
    (20, 1, 'TYX'),
    (1, 12, 'ZYX'),
    (4, 6, 'TZYX'),
])
def test_the_load_chokepoint_tags_axis_order_for_EVERY_layer(n_t, n_z, expected):
    """Written once, for every loader — that is what makes the scale format-agnostic."""
    axis_order, _dim, _ax = _tag_via_the_chokepoint(n_t=n_t, n_z=n_z)
    assert axis_order == expected


def test_the_users_Z_ANSWER_beats_the_readers_guess():
    """**Two tags on one layer used to contradict each other, on exactly the file where the user
    was asked.**

    An undeclared multipage TIFF has no axis metadata, so BioIO puts the pages on **T** — it has
    nowhere else to put them. PyCAT asks the user "time-series or z-stack?" and then threw the
    answer away: it was recorded, but `n_t`/`n_z` were never touched. So answering "Z-stack" gave
    `stack_axis='Z'` **and** `dimensionality='2d+t'`. Anything reading `dimensionality` believed
    the reader; anything reading `stack_axis` believed the user.

    The answer is now resolved once, at the chokepoint, before either tag is written.
    """
    axis_order, dim, stack_axis = _tag_via_the_chokepoint(n_t=20, n_z=1, assumed_axis='Z')

    assert stack_axis == 'Z', "the user's answer was not recorded"
    assert axis_order == 'ZYX', (
        f"the user said Z-stack and the layer is still tagged {axis_order!r} — so the shared "
        f"z-scale would put a depth on a time axis, or none at all")
    assert dim == 'z-stack', (
        f"`dimensionality` still says {dim!r} while `stack_axis` says 'Z' — the two tags on the "
        f"same layer contradict each other")


def test_the_users_T_ANSWER_is_honoured_too():
    """The mirror case: a file whose pages landed on Z but the user says they are timepoints."""
    axis_order, dim, stack_axis = _tag_via_the_chokepoint(n_t=1, n_z=20, assumed_axis='T')
    assert (stack_axis, axis_order, dim) == ('T', 'TYX', '2d+t')


def test_a_DECLARED_file_is_not_second_guessed():
    """No answer was assumed, so the reader's dims stand and no `stack_axis` tag is written."""
    axis_order, dim, stack_axis = _tag_via_the_chokepoint(n_t=1, n_z=12)
    assert (axis_order, dim) == ('ZYX', 'z-stack')
    assert stack_axis is None, "a declared file must not be tagged as an assumed axis"


def test_the_answered_z_stack_then_renders_with_its_real_depth():
    """End to end, through both shared points: the user's answer reaches the *viewer*.

    This is the case the whole chain exists for — an undeclared multipage TIFF the user labelled
    'Z-stack'. The reader called it TYX; without the chokepoint resolving the answer, the z-scale
    would see 'TYX' and correctly refuse to touch it, and the stack would render flat forever.
    """
    from pycat.file_io.napari_adapter import _apply_z_scale
    from pycat.file_io.tagging import _tag_loaded_layer

    dr = {'file_metadata': {'common': {'z_step_um': _Z_STEP}},
          'stack_axis_assumed': True, 'stack_axis_label': 'Z'}
    cm = _CM(dr)

    layer = _Layer(3)                                   # (N, Y, X): pages, Y, X
    _tag_loaded_layer(cm, layer, role='image', n_t=20, n_z=1)

    sc = [1.0, _PX, _PX]
    _apply_z_scale(sc, layer, cm, _PX)
    assert sc == [_Z_STEP, _PX, _PX], (
        f"the user's 'Z-stack' answer did not reach the viewer: {sc}")


def test_a_tag_that_DISAGREES_with_the_array_is_refused():
    """If the tag says 4 axes and the layer has 3, which one is depth is a guess — and a guess here
    silently rescales the wrong axis."""
    from pycat.file_io.napari_adapter import _apply_z_scale
    from pycat.utils.layer_tags import tag_layer

    layer = _Layer(3)
    tag_layer(layer, 'axis_order', 'TZYX', source='inferred')   # 4 axes, 3-D layer
    sc = [1.0, _PX, _PX]
    _apply_z_scale(sc, layer, _CM(_repo()), _PX)
    assert sc == [1.0, _PX, _PX], f"a contradictory tag was trusted: {sc}"
