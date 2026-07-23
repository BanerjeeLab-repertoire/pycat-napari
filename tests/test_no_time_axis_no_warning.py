"""
**PyCAT shouted about a missing frame interval at a user who had opened a still image.**

Reported against 1.6.17. Meet Raval opened a plain 2-D DAPI/GFP pair and got, twice::

    WARNING: Frame interval unknown (advanced_analysis_ui) — this file's metadata does not
    carry one.

    **Every time-dependent result depends on it.** A diffusion coefficient, an MSD exponent, a
    recovery half-time and a coarsening rate all scale with it directly: if the true interval is
    0.5 s and the assumed one is 1.0 s, **every one of them is out by a factor of two.**

*Every word of that is true — **of a movie**.*

**His file was a single 2-D image.** No time axis. No diffusion coefficient, no recovery half-time,
no coarsening rate. ***There is nothing for a frame interval to be wrong about.*** The panels seed
their frame-interval spinbox at **build** time, so the warning fired simply because the panel
existed.

***A warning that fires where it cannot apply is how real warnings get trained away.*** The next
one — on an actual time series, where a wrong interval **is** a factor-of-two error in every
dynamics result — is the one that gets scrolled past.

── The two halves of the fix ────────────────────────────────────────────────────────────

**``record_time_axis`` is called OUTSIDE the metadata ``try``.** Every loader wraps its
``extract_metadata`` call in ``try/except``. Recording ``n_t`` inside it would mean a metadata
failure leaves the value unset — and ***the previous file's frame count still sitting in the
repository.*** **A stale time axis is worse than an absent one: it is confidently wrong.**

**``has_time_axis`` treats unknown as YES.** An older session, or a loader not yet taught to set
``n_t``, must **warn**. A missing interval on a movie is a factor-of-two error in every dynamics
result; a spurious warning on a still image is merely noise. *Fail toward the loud side.*
"""

import contextlib
import io

import pytest

from pycat.utils.frame_interval import (
    _WARNED,
    has_time_axis,
    record_time_axis,
    sync_spinbox_from_metadata,
)


class _Spinbox:
    """The three methods `sync_spinbox_from_metadata` touches. No Qt needed."""

    def __init__(self):
        self.value = None

    def blockSignals(self, _):
        pass

    def setValue(self, value):
        self.value = value


def _warned_while_syncing(data_repository):
    # Detect the warning ROUTING-INDEPENDENTLY. `_warn_once` records the warning in the
    # module-level `_WARNED` set BEFORE it tries to surface it — and it surfaces via
    # napari's `show_warning` when napari is importable (a GUI notification, no stdout),
    # falling back to `print` only when that raises. Asserting on captured stdout therefore
    # silently depends on napari being ABSENT; with napari installed the warning fires but
    # never reaches stdout. `_WARNED` is populated on either path, so it is the honest signal
    # for "did PyCAT warn?". (stdout is still redirected to keep the print-fallback quiet.)
    _WARNED.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        sync_spinbox_from_metadata(_Spinbox(), data_repository,
                                   context='advanced_analysis_ui')
    return bool(_WARNED)


@pytest.mark.base
def test_a_STILL_IMAGE_is_not_warned_about_a_frame_interval():
    """**The bug Meet hit.** A 2-D image has no time axis to be wrong about."""
    repository = {}
    record_time_axis(repository, 1)

    assert not _warned_while_syncing(repository), (
        "PyCAT warned that the frame interval was unknown on an image with **one frame**.\n\n"
        "There is no diffusion coefficient, no recovery half-time and no coarsening rate for it to "
        "be wrong about. ***A warning that fires where it cannot apply is how real warnings get "
        "trained away.***"
    )


@pytest.mark.base
def test_a_MOVIE_with_no_frame_interval_is_STILL_warned_about():
    """**The warning must not be softened — only aimed.**

    On a real time series a wrong interval is a **factor-of-two error in every dynamics result.**
    That is exactly what the warning is for.
    """
    repository = {}
    record_time_axis(repository, 214)

    assert _warned_while_syncing(repository), (
        "a 214-frame movie with no frame interval did NOT warn. Suppressing the warning on still "
        "images must not suppress it where it matters — a wrong interval scales D, alpha, t-half "
        "and the coarsening rate directly."
    )


@pytest.mark.base
def test_an_UNKNOWN_time_axis_FAILS_TOWARD_THE_LOUD_SIDE():
    """An older session, or a loader not yet taught to record ``n_t``, must **warn** — *once an
    image is actually loaded.*

    The contract was tightened (commit "Fix frame-interval warning firing with no image loaded"):
    an EMPTY session — nothing loaded, no ``file_metadata`` and no ``n_t`` — stays SILENT, because
    the panels build their spinbox before any file is opened and a warning that fires there cannot
    apply (and trains the user to scroll past the one that matters). "Unknown → loud" still holds,
    but only once an image is present. See ``tests/test_frame_interval_no_image.py`` for the full
    contract.

    *A spurious warning on a still image is noise. A missing one on a loaded movie is a wrong number
    in a paper.*
    """
    # Image loaded (file_metadata present), frame count not recorded → fail toward the loud side.
    assert has_time_axis({'file_metadata': {}}), \
        "an unrecorded time axis on a LOADED image must be treated as a movie, not a still"
    assert has_time_axis({'file_metadata': {}, 'n_t': None})
    # An older session that recorded n_t (so an image WAS loaded) but left it unparseable → loud.
    assert has_time_axis({'n_t': 'not a number'})
    # Nothing loaded at all → silent: there is no image for a frame interval to be wrong about.
    assert not has_time_axis({})

    assert _warned_while_syncing({'file_metadata': {}}), (
        "with an image loaded but `n_t` never recorded, the warning was suppressed. It must fire — "
        "the cost of being wrong in that direction is a factor-of-two error in every dynamics result."
    )


@pytest.mark.base
def test_a_STALE_frame_count_from_the_PREVIOUS_file_is_overwritten():
    """**Recorded outside the metadata `try` — because a stale time axis is confidently wrong.**

    Every loader wraps `extract_metadata` in `try/except`. If `n_t` were recorded *inside* it, a
    metadata failure on a 2-D image opened after a movie would leave **214** in the repository, and
    PyCAT would warn about a time axis that no longer exists.
    """
    repository = {'n_t': 214}          # a movie was open
    record_time_axis(repository, 1)    # now a still image

    assert repository['n_t'] == 1, "the previous file's frame count survived the next load"
    assert not has_time_axis(repository)


@pytest.mark.base
@pytest.mark.parametrize('given,expected', [
    (1, 1), (0, 1), (None, 1), (214, 214), (-5, 1), ('abc', 1),
])
def test_the_recorded_frame_count_is_always_a_sane_positive_integer(given, expected):
    """Garbage in must not become a garbage time axis — it must become **one frame**."""
    repository = {}
    record_time_axis(repository, given)
    assert repository['n_t'] == expected
