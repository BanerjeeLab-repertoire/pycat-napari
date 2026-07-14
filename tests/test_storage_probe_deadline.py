"""
**A diagnostic that reproduces the symptom it diagnoses is not free.**

The slow-storage probe read a flat **8 MB** from the file *before the reader opened it* — and 8 MB
is not a bounded cost. It is 8 MB **at whatever speed the medium runs**, and the medium is the
unknown being measured. On the 2 MB/s network share the probe exists to detect, the probe alone
spent **four seconds** before anything reached the screen, to establish a fact the first fraction
of a second had already proved.

The read is now bounded by **time**, not bytes: it stops at whichever comes first, the byte budget
or ``PROBE_DEADLINE_S``. Stopping early does not weaken the verdict — the question is *"fast or
slow?"*, and a source that has not delivered the budget within the deadline has answered it.

*The first attempt at this bounded a 3500 ms probe to 1500 ms against a 750 ms deadline — because
the chunk was 1 MB, which at 2 MB/s takes 500 ms **inside a single ``read()``**, so the deadline
could not be honoured to better than half a second. **A bound that a coarse chunk can overshoot by
2x is a coincidence, not a bound.** The chunk is now 128 KB.*
"""

import io
import os
import time

import pytest

from pycat.file_io import storage_probe


class _ThrottledFile:
    """A file that delivers bytes at a fixed MB/s — the network share, in a test."""

    def __init__(self, handle, mbps):
        self._handle = handle
        self._mbps = mbps

    def read(self, n=-1):
        chunk = self._handle.read(n)
        time.sleep(len(chunk) / (self._mbps * 1024 * 1024))
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._handle.close()


@pytest.fixture
def slow_storage(monkeypatch, tmp_path):
    """A 16 MB file that reads at 2 MB/s — well under `SLOW_MBPS_THRESHOLD`."""
    path = tmp_path / "acquisition.tif"
    path.write_bytes(b'\0' * (16 * 1024 * 1024))

    real_open = io.open

    def throttled_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        if str(file) == str(path):
            return _ThrottledFile(handle, mbps=2.0)
        return handle

    monkeypatch.setattr('builtins.open', throttled_open)
    storage_probe.clear_probe_cache()
    return path


@pytest.mark.core
def test_the_probe_does_not_BECOME_the_stall_it_warns_about(slow_storage):
    """**Bounded by the deadline, not by the medium.**"""
    started = time.perf_counter()
    throughput = storage_probe.measure_throughput(str(slow_storage))
    elapsed = time.perf_counter() - started

    # It still reaches the right VERDICT — that is non-negotiable.
    assert throughput is not None
    assert throughput < storage_probe.SLOW_MBPS_THRESHOLD, (
        "the probe no longer detects slow storage. Bounding it must not cost the finding."
    )

    # An 8 MB read at 2 MB/s is ~3.5 s. The deadline plus one warm-up chunk is the budget.
    budget = storage_probe.PROBE_DEADLINE_S + 0.35
    assert elapsed < budget, (
        f"the probe took {elapsed * 1000:.0f} ms against a {storage_probe.PROBE_DEADLINE_S * 1000:.0f} ms "
        f"deadline. **It is reproducing the stall it exists to warn about.** If the chunk size grew, "
        f"the deadline cannot bite: at 2 MB/s a 1 MB chunk spends 500 ms inside a single read()."
    )


@pytest.mark.core
def test_the_probe_does_not_RERUN_for_every_file_in_a_folder(slow_storage):
    """Storage speed is a property of the **medium**. A 200-image folder paid for it 200 times."""
    sibling = slow_storage.parent / "second.tif"
    sibling.write_bytes(b'\0' * (16 * 1024 * 1024))

    storage_probe.clear_probe_cache()
    storage_probe.probe_path(str(slow_storage))          # pays the probe

    started = time.perf_counter()
    verdict = storage_probe.probe_path(str(sibling))     # must not pay it again
    elapsed = time.perf_counter() - started

    assert elapsed < 0.15, (
        f"the second file in the same directory re-probed ({elapsed * 1000:.0f} ms). "
        f"Throughput does not vary between two files in one folder."
    )
    assert verdict.slow, "the cached verdict lost the finding"

    # **But the SIZE is per-file, not cached.** Caching the whole verdict would report whichever
    # file happened to be probed first — exactly the class of quietly-wrong output being dug out.
    assert verdict.size_bytes == os.path.getsize(sibling)
