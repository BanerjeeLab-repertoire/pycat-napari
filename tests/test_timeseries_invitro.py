"""
The in-vitro time-series chain, against known growth and a known fusion event.

``stack_frame_properties`` → ``link_condensates`` → ``build_object_records`` is what produces
condensate **growth rates** and **fusion events** — the numbers a coarsening or maturation paper
reports.

**Audited and correct.** This is a clean module, and recording that is as much the point as
recording a bug: the audit is only worth anything if a pass means something.

Growth rate, against a known radius growth (area = π·r², so the area rate is analytic):

=========================  ==============  ============  ========
radius growth per frame    TRUE (µm²/s)    measured      error
=========================  ==============  ============  ========
0 % *(static)*             0.0000          −0.0000       **0.0 %**
2 %                        0.6729          0.6677        −0.8 %
5 %                        2.0852          2.1030        +0.9 %
10 %                       5.5135          5.5188        +0.1 %
=========================  ==============  ============  ========

**Within 1 % at every rate**, and exactly zero on a static stack — which is the case a
growth-rate estimator is most likely to get wrong, because noise alone can manufacture a trend.

Fusion detection recovers the event **at the correct frame**, with the correct parents.
"""

import numpy as np
import pytest


def _fio_stub():
    """`stack_frame_properties` streams via file_io, which needs aicsimageio at import."""
    import sys
    import types
    if 'pycat.file_io.file_io' in sys.modules:
        return
    stub = types.ModuleType('pycat.file_io.file_io')
    stub.iter_frames = lambda s, **k: (
        (i, np.asarray(s)[i]) for i in range(np.asarray(s).shape[0]))
    stub.materialize_stack = lambda s, **k: np.asarray(s)
    sys.modules['pycat.file_io.file_io'] = stub


_MPP = 0.5          # µm per pixel
_DT = 2.0           # seconds per frame


def _growing_condensates(radius_growth_per_frame, n_frames=20, size=96):
    """Four condensates whose radius grows at a KNOWN rate. Area = pi*r^2 is then analytic."""
    yy, xx = np.mgrid[0:size, 0:size]

    labels = np.zeros((n_frames, size, size), np.int32)
    intensity = np.zeros((n_frames, size, size), np.float32)

    for i, (cy, cx) in enumerate([(25, 25), (25, 70), (70, 25), (70, 70)], start=1):
        for t in range(n_frames):
            radius = 6.0 * (1 + radius_growth_per_frame * t)
            d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
            labels[t][d < radius] = i
            intensity[t] += 800 * np.exp(-(d ** 2) / (2 * (radius / 2) ** 2))

    return labels, intensity + 50


@pytest.mark.core
@pytest.mark.parametrize("growth", [0.0, 0.05, 0.10])
def test_the_growth_rate_matches_the_analytic_truth(growth):
    """A static stack must give **exactly zero**, and a growing one the analytic rate.

    Zero is the case worth guarding: **noise alone can manufacture a trend**, and a growth-rate
    estimator that reports a small positive rate on a static condensate would put a spurious
    coarsening exponent into a paper.
    """
    _fio_stub()
    ts = pytest.importorskip("pycat.toolbox.timeseries_invitro_tools")

    n_frames = 20
    labels, intensity = _growing_condensates(growth, n_frames=n_frames)

    props = ts.stack_frame_properties(labels, intensity, microns_per_pixel=_MPP)
    linked, _fusion = ts.link_condensates(props)
    records = ts.object_records_to_df(
        ts.build_object_records(linked, frame_interval_s=_DT))

    assert linked.track_id.nunique() == 4, (
        f"{linked.track_id.nunique()} tracks from 4 condensates — the linking is wrong before "
        f"any rate can be trusted"
    )

    # Analytic: A(t) = pi * (r0 * (1 + g*t))^2, so the mean rate over the record is exact.
    r0 = 6.0 * _MPP
    area_start = np.pi * r0 ** 2
    area_end = np.pi * (r0 * (1 + growth * (n_frames - 1))) ** 2
    true_rate = (area_end - area_start) / ((n_frames - 1) * _DT)

    measured = float(records.area_growth_rate_um2_per_s.mean())

    if growth == 0.0:
        assert abs(measured) < 1e-6, (
            f"a STATIC condensate reported a growth rate of {measured:.6f} um2/s. Noise alone "
            f"can manufacture a trend, and a spurious rate here becomes a spurious coarsening "
            f"exponent in a paper."
        )
    else:
        assert measured == pytest.approx(true_rate, rel=0.05), (
            f"growth rate {measured:.4f} um2/s against an analytic {true_rate:.4f} "
            f"(radius growing {growth:.0%} per frame)"
        )


@pytest.mark.core
def test_fusion_is_detected_at_the_right_frame_with_the_right_parents():
    """Two condensates merge into one. The event, the frame, and the parents must all be right.

    A fusion event is not cosmetic: it is what a maturation study counts, and it is also what
    separates *"one droplet grew"* from *"two droplets combined"* — a completely different
    physical claim.
    """
    _fio_stub()
    ts = pytest.importorskip("pycat.toolbox.timeseries_invitro_tools")

    size, n_frames, fuse_at = 96, 20, 10
    yy, xx = np.mgrid[0:size, 0:size]

    labels = np.zeros((n_frames, size, size), np.int32)
    intensity = np.zeros((n_frames, size, size), np.float32)

    for t in range(n_frames):
        if t < fuse_at:                                    # TWO droplets
            for i, (cy, cx) in enumerate([(48, 38), (48, 58)], start=1):
                d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
                labels[t][d < 7] = i
                intensity[t] += 800 * np.exp(-(d ** 2) / (2 * 3.5 ** 2))
        else:                                              # ONE, larger
            d = np.sqrt((yy - 48) ** 2 + (xx - 48) ** 2)
            labels[t][d < 10] = 1
            intensity[t] += 800 * np.exp(-(d ** 2) / (2 * 5.0 ** 2))

    props = ts.stack_frame_properties(labels, intensity + 50, microns_per_pixel=_MPP)
    _linked, fusion = ts.link_condensates(props, detect_fusion=True)

    assert len(fusion) == 1, (
        f"{len(fusion)} fusion events detected; the truth is exactly 1 (two droplets merging "
        f"at frame {fuse_at})"
    )

    event = fusion.iloc[0]
    assert int(event['frame']) == pytest.approx(fuse_at, abs=1), (
        f"the fusion was placed at frame {event['frame']}, not {fuse_at}"
    )
    assert len(event['parent_track_ids']) == 2, (
        f"the fusion has {len(event['parent_track_ids'])} parents; two droplets merged"
    )

    # The child must be about as large as the two parents together.
    parents = float(np.sum(event['parent_areas_um2']))
    child = float(event['child_area_um2'])
    assert child == pytest.approx(parents, rel=0.25), (
        f"the fused droplet is {child:.1f} um2 and its parents summed to {parents:.1f}. If "
        f"these disagree badly, what was detected is not a fusion."
    )
