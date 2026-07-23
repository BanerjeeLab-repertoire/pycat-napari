"""**Clicking an off-page bead pages the VPT plots to its track's bucket (results_figure_reflow Part 3).**

VPT pages thousands of tracks in buckets; every bead is clickable, including beads whose track is not in the
displayed bucket. Before this, clicking one promoted its curve alone onto the current page while the pager
label still named a range that excluded it — the curve floated without its cohort, against a contradicting
label. Now an off-page selection moves the pager to the bucket that contains the track; an on-page (already
visible) selection does not move the view. The logic is exercised headlessly with a stubbed render.
"""
import pytest

from pycat.toolbox.vpt.results_dock import _VptResultsDockMixin

pytestmark = pytest.mark.core


class _Dock(_VptResultsDockMixin):
    """A minimal stand-in: real paging logic, a stubbed render that just counts, and a page-0 ensemble whose
    drawn ids we set explicitly (the panels' representative sample, normally recorded in the registries)."""

    def __init__(self, all_tids, page, bucket_size, drawn_page0=()):
        self._vpt_results = {"all_tids": list(all_tids), "page": page, "bucket_size": bucket_size}
        self._centered_registry = {"coords": {int(t): None for t in drawn_page0}}
        self._msd_line_registry = {"coords": {}}
        self.renders = 0

    def _vpt_render_page(self):
        self.renders += 1


def _ids(n=100):
    return list(range(1, n + 1))


def test_selecting_an_off_page_track_moves_to_its_bucket_and_renders():
    d = _Dock(_ids(), page=1, bucket_size=10)          # page 1 shows tids 1–10
    d._vpt_page_to_selected_track(35)                  # tid 35 → position 34 → bucket 34//10 + 1 = 4
    assert d._vpt_results["page"] == 4 and d.renders == 1


def test_selecting_an_on_page_track_does_not_move_the_view():
    d = _Dock(_ids(), page=1, bucket_size=10)
    d._vpt_page_to_selected_track(5)                   # 5 is on page 1 already
    assert d._vpt_results["page"] == 1 and d.renders == 0


def test_a_track_in_the_page0_ensemble_keeps_page0():
    d = _Dock(_ids(), page=0, bucket_size=10, drawn_page0=[5, 35, 77])
    d._vpt_page_to_selected_track(35)                  # drawn in the ensemble → visible → stay
    assert d._vpt_results["page"] == 0 and d.renders == 0


def test_a_track_absent_from_the_page0_ensemble_pages_to_its_bucket():
    d = _Dock(_ids(), page=0, bucket_size=10, drawn_page0=[5, 77])
    d._vpt_page_to_selected_track(35)                  # NOT in the ensemble sample → off-page → navigate
    assert d._vpt_results["page"] == 4 and d.renders == 1


def test_the_bucket_index_is_correct_at_boundaries():
    # last id of bucket 1 (tid 10 → pos 9 → bucket 1) vs first of bucket 2 (tid 11 → pos 10 → bucket 2)
    d1 = _Dock(_ids(), page=0, bucket_size=10, drawn_page0=[])
    d1._vpt_page_to_selected_track(10)
    assert d1._vpt_results["page"] == 1
    d2 = _Dock(_ids(), page=0, bucket_size=10, drawn_page0=[])
    d2._vpt_page_to_selected_track(11)
    assert d2._vpt_results["page"] == 2
    # the final partial bucket: 95 ids, size 10 → tid 95 is in bucket 10
    d3 = _Dock(_ids(95), page=0, bucket_size=10, drawn_page0=[])
    d3._vpt_page_to_selected_track(95)
    assert d3._vpt_results["page"] == 10


def test_bucket_size_change_then_select_recomputes_not_caches():
    d = _Dock(_ids(), page=1, bucket_size=25)          # bigger buckets now
    d._vpt_page_to_selected_track(60)                  # pos 59 → bucket 59//25 + 1 = 3
    assert d._vpt_results["page"] == 3


def test_an_unknown_track_is_a_clean_noop():
    d = _Dock(_ids(), page=1, bucket_size=10)
    d._vpt_page_to_selected_track(999)                 # not a paged track
    assert d._vpt_results["page"] == 1 and d.renders == 0


def test_no_results_dock_is_a_clean_noop():
    d = _Dock(_ids(), page=1, bucket_size=10)
    d._vpt_results = None
    d._vpt_page_to_selected_track(35)                  # existing contract: no dock → nothing happens
    assert d.renders == 0
