"""**A UI control that does not reach the code it names is worse than no control.**

`segment_subcellular_objects` accepts five refinement thresholds — kurtosis,
local SNR, global SNR, intensity HWHM scale, max area fraction — and
`run_segment_subcellular_objects` threads them down from the UI. The call to
`puncta_refinement_func` then passed `min_spot_radius` and `fast` and **dropped the
other five**, so the refiner silently used its own defaults.

Those defaults are IDENTICAL to the caller's (-3.0, 1.0, 1.0, 1.17, 0.25). That is
why this survived: at defaults the bug is invisible. It only bites when a user
CHANGES a threshold, at which point the control does nothing and the refinement
carries on at -3.0 — the same class as the dead SNR gate of 1.5.416, where the
pipeline made a consequential decision and told nobody.

Identical defaults are also why the test has to pass NON-default values and watch
what arrives. Asserting on the output at defaults would pass against the bug.
"""

# Third party imports
import numpy as np
import pytest

pytestmark = pytest.mark.base

_NON_DEFAULT = dict(
    kurtosis_threshold=-1.5,        # default -3.0
    local_snr_threshold=2.5,        # default 1.0
    global_snr_threshold=3.5,       # default 1.0
    intensity_hwhm_scale=2.25,      # default 1.17
    max_area_fraction=0.6,          # default 0.25
)


@pytest.fixture
def spy(monkeypatch):
    """Record what `puncta_refinement_func` is actually handed."""
    # segment_subcellular_objects moved to segmentation/subcellular.py (1.6.243) and calls
    # puncta_refinement_func as bound in THAT module's namespace, so patch it there.
    from pycat.toolbox.segmentation import subcellular

    seen = {}

    def _fake(original_image, processed_image, puncta_mask, cell_mask, **kwargs):
        seen.update(kwargs)
        return np.zeros_like(np.asarray(cell_mask), dtype=int)

    monkeypatch.setattr(subcellular, 'puncta_refinement_func', _fake)
    return seen


def _run(seg, **overrides):
    """Drive the real `segment_subcellular_objects` over a small synthetic cell."""
    rng = np.random.default_rng(0)
    img = rng.normal(120, 5, (64, 64)).astype(np.float32)
    img[28:36, 28:36] += 300.0                      # one bright punctum
    cell_mask = np.zeros((64, 64), dtype=int)
    cell_mask[8:56, 8:56] = 1

    kw = dict(_NON_DEFAULT)
    kw.update(overrides)
    return seg.segment_subcellular_objects(
        img, img.copy(), cell_mask, cell_label=1, ball_radius=5, **kw)


def test_the_thresholds_REACH_the_refiner(spy):
    """The bug: five of seven arguments stopped at the call site."""
    from pycat.toolbox import segmentation_tools as seg

    _run(seg)

    missing = [k for k in _NON_DEFAULT if k not in spy]
    assert not missing, (
        f"these thresholds never reached puncta_refinement_func: {missing}. "
        f"They are accepted by segment_subcellular_objects and dropped on the floor, "
        f"so the user's control silently does nothing.")


def test_the_VALUES_arrive_intact_not_just_the_names(spy):
    """Passing the wrong value would be the same bug wearing the right shape."""
    from pycat.toolbox import segmentation_tools as seg

    _run(seg)

    wrong = {k: (v, spy.get(k)) for k, v in _NON_DEFAULT.items() if spy.get(k) != v}
    assert not wrong, f"threshold(s) arrived changed (expected, got): {wrong}"


def test_min_spot_radius_and_fast_still_arrive(spy):
    """The two that always worked must keep working — this is a fix, not a rewrite."""
    from pycat.toolbox import segmentation_tools as seg

    _run(seg, min_spot_radius=4)

    assert spy.get('min_spot_radius') == 4
    assert 'fast' in spy


def test_the_defaults_are_IDENTICAL_which_is_why_this_hid():
    """Pinned as the reason the bug was invisible, not as an aspiration.

    If these two signatures ever drift apart, the old dropped-argument bug would
    start changing results at DEFAULTS too — and the failure would look like a
    science regression rather than a plumbing one. Better to fail here.

    Deliberately takes no `spy`: that fixture replaces `puncta_refinement_func`,
    and inspecting the stand-in's signature would tell us nothing about the real
    one.
    """
    import inspect
    from pycat.toolbox import segmentation_tools as seg

    outer = inspect.signature(seg.segment_subcellular_objects).parameters
    inner = inspect.signature(seg.puncta_refinement_func).parameters

    for name in _NON_DEFAULT:
        assert outer[name].default == inner[name].default, (
            f"'{name}' defaults differ between segment_subcellular_objects "
            f"({outer[name].default}) and puncta_refinement_func ({inner[name].default})")
